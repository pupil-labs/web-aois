import asyncio
import json
import os
import traceback
from concurrent.futures import Future

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from pupil_labs.web_aois.define import define_aois
from pupil_labs.web_aois.record import record_session


class AsyncWorker(QThread):
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, coroutine_factory):
        super().__init__()
        self.coroutine_factory = coroutine_factory

    def run(self):
        try:
            result = asyncio.run(self.coroutine_factory())
            self.completed.emit(result)
        except Exception:
            self.failed.emit(traceback.format_exc())


class WebAoisApp(QMainWindow):
    save_path_requested = pyqtSignal(object)
    aoi_path_saved = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Web AOIs Recorder")
        self.resize(800, 520)

        self.aoi_json_path = None
        self.active_worker = None

        self.define_button = QPushButton("Define AOIs")
        self.load_button = QPushButton("Load AOI JSON")
        self.record_button = QPushButton("Connect Neon and record")
        self.record_button.setEnabled(False)

        self.path_value = QLineEdit()
        self.path_value.setReadOnly(True)

        self.start_url_value = QLineEdit()
        self.start_url_value.setPlaceholderText("Optional start URL, e.g. https://example.com")

        self.device_ip_value = QLineEdit()
        self.device_ip_value.setPlaceholderText("Optional Neon IP, e.g. 192.168.1.25")

        self.device_port_value = QLineEdit()
        self.device_port_value.setPlaceholderText("Optional Neon port (default: 8080)")
        self.device_port_value.setText("8080")

        self.status_log = QTextEdit()
        self.status_log.setReadOnly(True)

        self.define_button.clicked.connect(self.on_define_clicked)
        self.load_button.clicked.connect(self.on_load_clicked)
        self.record_button.clicked.connect(self.on_record_clicked)
        self.save_path_requested.connect(self._handle_save_path_request)
        self.aoi_path_saved.connect(self._set_saved_path)

        self._build_layout()

    def _build_layout(self):
        main_widget = QWidget()
        main_layout = QVBoxLayout()

        path_grid = QGridLayout()
        path_grid.addWidget(QLabel("AOI definitions JSON"), 0, 0)
        path_grid.addWidget(self.path_value, 0, 1)
        path_grid.addWidget(QLabel("Start URL"), 1, 0)
        path_grid.addWidget(self.start_url_value, 1, 1)
        path_grid.addWidget(QLabel("Neon IP (optional)"), 2, 0)
        path_grid.addWidget(self.device_ip_value, 2, 1)
        path_grid.addWidget(QLabel("Neon Port"), 3, 0)
        path_grid.addWidget(self.device_port_value, 3, 1)

        button_layout = QHBoxLayout()
        button_layout.addWidget(self.define_button)
        button_layout.addWidget(self.load_button)
        button_layout.addWidget(self.record_button)

        main_layout.addLayout(path_grid)
        main_layout.addLayout(button_layout)
        main_layout.addWidget(QLabel("Status"))
        main_layout.addWidget(self.status_log)

        main_widget.setLayout(main_layout)
        self.setCentralWidget(main_widget)

    def _append_status(self, text):
        self.status_log.append(text)

    def _set_running(self, running):
        self.define_button.setEnabled(not running)
        self.load_button.setEnabled(not running)
        self.record_button.setEnabled((not running) and bool(self.aoi_json_path))

    def _handle_worker_failure(self, tb):
        self.active_worker = None
        self._set_running(False)
        self._append_status("Operation failed. See details below.")
        if "No Neon device discovered" in tb:
            self._append_status(
                "Tip: enter Neon IP and port in the app to bypass discovery, "
                "then click Connect Neon & record again."
            )
        self._append_status(tb)
        QMessageBox.critical(self, "Operation failed", tb)

    def _set_saved_path(self, path):
        self.aoi_json_path = path
        self.path_value.setText(path)
        self.record_button.setEnabled(self.active_worker is None)
        self._append_status(f"Saved AOI definitions to: {path}")

    def _validate_aoi_json(self, file_path):
        with open(file_path, "rt") as input_file:
            data = json.load(input_file)

        if not isinstance(data, dict) or len(data) == 0:
            raise ValueError("AOI JSON must be a non-empty object keyed by URL.")

        return data

    def on_load_clicked(self):
        start_dir = os.path.dirname(self.aoi_json_path) if self.aoi_json_path else ""
        suggested_path = os.path.join(start_dir, "web-aois.json") if start_dir else "web-aois.json"
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Load AOI Definitions",
            suggested_path,
            "JSON files (*.json);;All Files (*.*)",
        )

        if not file_path:
            return

        try:
            self._validate_aoi_json(file_path)
        except Exception as exc:
            QMessageBox.warning(self, "Invalid AOI JSON", str(exc))
            self._append_status(f"Failed to load AOI JSON: {exc}")
            return

        self.aoi_json_path = file_path
        self.path_value.setText(file_path)
        self.record_button.setEnabled(self.active_worker is None)
        self._append_status(f"Loaded AOI definitions from: {file_path}")

    def _build_save_path_provider(self):
        def provider(_data):
            result_future = Future()
            self.save_path_requested.emit(result_future)
            return result_future.result()

        return provider

    def _handle_save_path_request(self, result_future):
        start_dir = os.path.dirname(self.aoi_json_path) if self.aoi_json_path else ""
        default_name = "web-aois.json"
        suggested_path = os.path.join(start_dir, default_name) if start_dir else default_name

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save AOI Definitions as...",
            suggested_path,
            "JSON files (*.json);;All Files (*.*)",
        )

        result_future.set_result(file_path or None)

    def _start_worker(self, coroutine_factory):
        self._set_running(True)
        self.active_worker = AsyncWorker(coroutine_factory)
        self.active_worker.completed.connect(self._on_worker_completed)
        self.active_worker.failed.connect(self._handle_worker_failure)
        self.active_worker.start()

    def _on_worker_completed(self, _result):
        self.active_worker = None
        self._set_running(False)
        self._append_status("Operation finished.")

    def on_define_clicked(self):
        self._append_status("Starting AOI definition browser...")
        save_path_provider = self._build_save_path_provider()
        start_url = self.start_url_value.text().strip() or None

        def coroutine_factory():
            return define_aois(
                start_url=start_url,
                save_path_provider=save_path_provider,
                on_saved=lambda path: self.aoi_path_saved.emit(path),
            )

        self._start_worker(coroutine_factory)

    def on_record_clicked(self):
        if not self.aoi_json_path:
            QMessageBox.warning(self, "No AOI file", "Define and save AOIs first.")
            return

        self._append_status("Connecting to Neon and starting recording...")
        self._append_status("Event sink mode: file (browser/AOI events will be written to CSV only)")
        start_url = self.start_url_value.text().strip() or None
        device_ip = self.device_ip_value.text().strip()
        device_port = self.device_port_value.text().strip() or "8080"

        if device_ip and not device_port.isdigit():
            QMessageBox.warning(self, "Invalid port", "Neon port must be an integer.")
            return

        old_device_ip = os.environ.get("WEB_AOIS_DEVICE_IP")
        old_device_port = os.environ.get("WEB_AOIS_DEVICE_PORT")
        old_event_sink = os.environ.get("WEB_AOIS_EVENT_SINK")
        old_event_log_path = os.environ.get("WEB_AOIS_EVENT_LOG_PATH")
        event_log_path = os.path.join(os.path.dirname(self.aoi_json_path), "web-events.csv")

        async def record_with_optional_direct_connect():
            try:
                os.environ["WEB_AOIS_EVENT_SINK"] = "file"
                os.environ["WEB_AOIS_EVENT_LOG_PATH"] = event_log_path
                self._append_status(f"Event log path: {event_log_path}")

                if device_ip:
                    os.environ["WEB_AOIS_DEVICE_IP"] = device_ip
                    os.environ["WEB_AOIS_DEVICE_PORT"] = device_port
                else:
                    os.environ.pop("WEB_AOIS_DEVICE_IP", None)
                    os.environ.pop("WEB_AOIS_DEVICE_PORT", None)

                return await record_session(
                    aoi_definitions_path=self.aoi_json_path,
                    start_url=start_url,
                )
            finally:
                if old_device_ip is None:
                    os.environ.pop("WEB_AOIS_DEVICE_IP", None)
                else:
                    os.environ["WEB_AOIS_DEVICE_IP"] = old_device_ip

                if old_device_port is None:
                    os.environ.pop("WEB_AOIS_DEVICE_PORT", None)
                else:
                    os.environ["WEB_AOIS_DEVICE_PORT"] = old_device_port

                if old_event_sink is None:
                    os.environ.pop("WEB_AOIS_EVENT_SINK", None)
                else:
                    os.environ["WEB_AOIS_EVENT_SINK"] = old_event_sink

                if old_event_log_path is None:
                    os.environ.pop("WEB_AOIS_EVENT_LOG_PATH", None)
                else:
                    os.environ["WEB_AOIS_EVENT_LOG_PATH"] = old_event_log_path

        def coroutine_factory():
            return record_with_optional_direct_connect()

        self._start_worker(coroutine_factory)


def main():
    app = QApplication.instance()
    owns_app = False
    if app is None:
        app = QApplication([])
        owns_app = True

    window = WebAoisApp()
    window.show()

    if owns_app:
        app.exec()


if __name__ == "__main__":
    main()
