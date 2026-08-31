# /// script
# dependencies = [
#   "pupil-labs-web-aois @ git+https://github.com/pupil-labs/web-aois.git@@website_app",
#   "playwright",
#   "scipy",
#   "matplotlib",
#   "opencv-python",
# ]
# ///

"""
Web AOIs Plugin for Neon Player.

A single Run action performs all three pipeline steps in sequence:
  1. Take Screenshots  – Playwright screenshots of web pages and their AOIs
  2. Process Recording – Map Neon gaze/fixation data to web page coordinates
  3. Generate Heatmaps – Render both gaze and fixation heatmap images

Outputs are cached under <recording>/.neon_player/web_aois/.
AOI timeline tracks display gaze segments.
"""

import asyncio
import csv
import json
import logging
import sys
import typing as T
from pathlib import Path

from qt_property_widgets.utilities import FilePath, property_params
from pupil_labs.neon_player import Plugin, ProgressUpdate, action


log = logging.getLogger(__name__)

# Progress band allocations for the three pipeline steps (0.0-1.0 scale).
_SCREENSHOTS_BAND = (0.0, 0.20)
_PROCESS_BAND = (0.20, 0.85)
_HEATMAPS_BAND = (0.85, 1.0)


class WebAoisPlugin(Plugin):
    label = "Web AOIs"
    TIMELINE_PREFIX = "Web AOIs"

    def __init__(self):
        super().__init__()
        self._aoi_definitions_file = FilePath("")
        self._event_log_file = FilePath("")
        self._timeline_rows: list[str] = []
        self._pipeline_job = None

    # ------------------------------------------------------------------ #
    # Properties                                                           #
    # ------------------------------------------------------------------ #

    @property
    @property_params(dialog_title="Select AOI definitions JSON")
    def aoi_definitions_file(self) -> FilePath:
        """Path to the AOI definitions JSON produced by pl-web-aois-define."""
        return self._aoi_definitions_file

    @aoi_definitions_file.setter
    def aoi_definitions_file(self, value: FilePath) -> None:
        self._aoi_definitions_file = value

    @property
    @property_params(dialog_title="Select browser events sidecar (optional)")
    def event_log_file(self) -> FilePath:
        """Optional browser-events sidecar (.csv/.jsonl/.json) for mapping."""
        return self._event_log_file

    @event_log_file.setter
    def event_log_file(self, value: FilePath) -> None:
        self._event_log_file = value

    # ------------------------------------------------------------------ #
    # Cache path helpers                                                   #
    # ------------------------------------------------------------------ #

    def _recording_path_from_argv(self) -> Path | None:
        """Resolve recording path from Neon background-worker argv shape.

        Worker jobs are launched as:
        `python -m pupil_labs.neon_player <recording_path> --progress-ipc-name ... --job ...`
        so the first non-option argv token after module args is the recording path.
        """
        for token in sys.argv[1:]:
            if token.startswith("-"):
                continue
            candidate = Path(token)
            if candidate.exists() and candidate.is_dir():
                return candidate
        return None

    def _recording_path(self) -> Path:
        """Return the path to the currently loaded recording.

        The Plugin base class is expected to expose this as ``self.recording_path``.
        If the attribute is absent the plugin raises a clear error so the user
        knows what to fix rather than seeing a cryptic AttributeError.
        """
        path = getattr(self, "recording_path", None)
        if path is None:
            g_pool = getattr(self, "g_pool", None)
            if g_pool is not None:
                path = getattr(g_pool, "rec_dir", None)
        if path is None:
            recording = getattr(self, "recording", None)
            if recording is not None:
                path = getattr(recording, "path", None)
        if path is None:
            argv_path = self._recording_path_from_argv()
            if argv_path is not None:
                path = argv_path
        if path is None:
            raise RuntimeError(
                "No recording is loaded. "
                "Open a Neon recording in Neon Player before running this action."
            )
        return Path(path)

    def _cache_dir(self) -> Path:
        d = self._recording_path() / ".neon_player" / "web_aois"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _screenshots_dir(self) -> Path:
        d = self._cache_dir() / "screenshots"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _output_dir(self) -> Path:
        d = self._cache_dir() / "output"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _default_event_sidecar_path(self) -> Path:
        return self._cache_dir() / "browser-events.csv"

    def _resolve_event_sidecar_path(self) -> Path | None:
        configured = Path(str(self._event_log_file)) if str(self._event_log_file) else None
        if configured is not None and configured.is_file():
            return configured

        default_path = self._default_event_sidecar_path()
        if default_path.is_file():
            return default_path

        return None

    def _clear_timeline_rows(self) -> None:
        """Remove all AOI timeline rows created by this plugin."""
        timeline = self.get_timeline()
        for row_name in list(self._timeline_rows):
            timeline.remove_timeline_plot(row_name)
        self._timeline_rows.clear()

    def _parse_gaze_segments(self, csv_path: Path) -> list[tuple[int, int]]:
        """Parse gaze AOI CSV samples into merged timeline segments."""
        timestamps: list[int] = []
        with csv_path.open("rt", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                try:
                    timestamps.append(int(float(row["timestamp [ns]"])))
                except (KeyError, TypeError, ValueError):
                    continue

        if not timestamps:
            return []

        timestamps.sort()
        if len(timestamps) >= 2:
            deltas = [b - a for a, b in zip(timestamps[:-1], timestamps[1:]) if b > a]
            sample_dt = int(sum(deltas) / len(deltas)) if deltas else 16_000_000
        else:
            sample_dt = 16_000_000
        sample_dt = max(sample_dt, 1_000_000)
        gap_threshold = sample_dt * 3

        segments: list[tuple[int, int]] = []
        start = timestamps[0]
        prev = timestamps[0]
        for ts in timestamps[1:]:
            if ts - prev > gap_threshold:
                segments.append((start, prev + sample_dt))
                start = ts
            prev = ts
        segments.append((start, prev + sample_dt))
        return segments

    def _aoi_row_name(self, csv_path: Path) -> str:
        """Build a stable timeline row name from tab folder and AOI CSV stem."""
        tab_name = csv_path.parent.name
        stem = csv_path.stem
        aoi_name = stem[len("aoi-"):] if stem.startswith("aoi-") else stem
        return f"{self.TIMELINE_PREFIX} / {tab_name} / {aoi_name}"

    def refresh_aoi_timeline(self) -> None:
        """Rebuild AOI timeline gaze tracks from cached gaze AOI CSV files."""
        output_dir = self._output_dir()
        aoi_files = sorted(
            path
            for path in output_dir.glob("tab-*/aoi-*.csv")
            if not path.name.startswith("aoi-fixations-")
        )
        self._clear_timeline_rows()

        if not aoi_files:
            log.info("Web AOIs: no gaze AOI files found for timeline update.")
            return

        timeline = self.get_timeline()
        for csv_path in aoi_files:
            segments = self._parse_gaze_segments(csv_path)
            if not segments:
                continue
            row_name = self._aoi_row_name(csv_path)
            timeline.add_timeline_broken_bar(row_name, segments, color="cyan")
            self._timeline_rows.append(row_name)

        log.info(f"Web AOIs: timeline updated with {len(self._timeline_rows)} gaze track(s).")

    # ------------------------------------------------------------------ #
    # Pipeline step helpers (used inside the single background job)       #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _scale(value: float, band: tuple[float, float]) -> float:
        """Map a 0-1 sub-step value into an outer progress band."""
        lo, hi = band
        return lo + value * (hi - lo)

    def _run_screenshots(self, aoi_json: Path) -> T.Generator[float, None, None]:
        """Capture screenshots; yields 0-1 sub-progress."""
        output_path = self._screenshots_dir()
        try:
            from pupil_labs.web_aois.screenshots import (
                capture_screenshots_async,
                load_aoi_definitions,
            )
            aoi_definitions = load_aoi_definitions(aoi_json)
            total = len(aoi_definitions) or 1
            log.info(f"Web AOIs: capturing screenshots for {total} URL(s).")
            progress_state = {"done": 0}

            def _on_progress(done: int, _count: int) -> None:
                progress_state["done"] = done

            result = asyncio.run(
                capture_screenshots_async(
                    aoi_definitions,
                    output_path,
                    headless=False,
                    progress_callback=_on_progress,
                )
            )
            yield progress_state["done"] / total
            for w in result.warnings:
                log.warning(f"Screenshot failed - {w.url} :: {w.aoi_name} :: {w.reason}")
            return
        except ImportError:
            pass

        # Legacy fallback for older site-packages build.
        from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
        from pupil_labs.web_aois.aoi_locator_helper import get_aoi_locators_for_page
        from pupil_labs.web_aois.screenshots import _optional_context_auth_kwargs, _slugify

        with aoi_json.open() as fh:
            aoi_definitions = json.load(fh)
        total = len(aoi_definitions) or 1
        log.info(f"Web AOIs: capturing screenshots (legacy) for {total} URL(s).")
        failed: list[tuple[str, str, str]] = []

        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=False,
                args=["--start-maximized"],
            )
            ctx = browser.new_context(no_viewport=True, **_optional_context_auth_kwargs())
            page = ctx.new_page()

            for index, (url, page_aoi_defs) in enumerate(aoi_definitions.items()):
                page_dir = output_path / _slugify(url)
                page_dir.mkdir(parents=True, exist_ok=True)
                page.goto(url, wait_until="domcontentloaded")
                try:
                    page.wait_for_load_state("networkidle", timeout=10_000)
                except PlaywrightTimeoutError:
                    pass
                page.screenshot(path=page_dir / "full-page.png", full_page=True)
                log.info(f"Saved full-page screenshot for {url}")
                for aoi_name, locator in get_aoi_locators_for_page(page, page_aoi_defs).items():
                    try:
                        locator.first.screenshot(
                            path=page_dir / f"aoi-{_slugify(aoi_name)}.png", timeout=10_000
                        )
                    except PlaywrightTimeoutError as exc:
                        failed.append((url, aoi_name, str(exc).splitlines()[0]))
                        log.warning(f"AOI screenshot timed out: '{aoi_name}' on {url}")
                yield (index + 1) / total

            ctx.close()
            browser.close()

        for url, aoi_name, reason in failed:
            log.warning(f"Screenshot failed - {url} :: {aoi_name} :: {reason}")

    def _run_process(self) -> T.Generator[float, None, None]:
        """Process recording gaze/fixation mapping; yields 0-1 sub-progress."""
        from pupil_labs.web_aois.process import RecordingProcessor

        recording_path = self._recording_path()
        output_dir = self._output_dir()
        event_sidecar = self._resolve_event_sidecar_path()
        log.info(f"Web AOIs: processing {recording_path} -> {output_dir}")
        if event_sidecar is not None:
            log.info(f"Web AOIs: using browser events sidecar {event_sidecar}")

        progress_state = {"value": 0.0}

        def _on_progress(value: float) -> None:
            progress_state["value"] = max(0.0, min(1.0, value))

        try:
            processor = RecordingProcessor(
                str(recording_path),
                str(output_dir),
                progress_callback=_on_progress,
                event_log_path=str(event_sidecar) if event_sidecar is not None else None,
            )
        except TypeError:
            if event_sidecar is not None:
                raise RuntimeError(
                    "The installed pupil-labs-web-aois package does not support "
                    "browser event sidecars. Update the package and restart Neon Player."
                )
            log.info("Web AOIs: using RecordingProcessor.")
            processor = RecordingProcessor(str(recording_path), str(output_dir))

        yield 0.02
        processor.process()
        yield max(0.02, min(0.98, progress_state["value"]))

        if hasattr(processor, "export_tab_manifest"):
            manifest_path = processor.export_tab_manifest(output_dir / "tab_manifest.json")
        else:
            manifest_path = output_dir / "tab_manifest.json"
            tabs = {f"tab-{t.id}": t.history for t in processor.tab_states}
            manifest_path.write_text(json.dumps({"schema_version": 1, "tabs": tabs}, indent=2))
        log.info(f"Web AOIs: tab manifest written to {manifest_path}")
        yield 1.0

    def _run_heatmaps(self) -> T.Generator[float, None, None]:
        """Generate gaze and fixation heatmaps for all tabs; yields 0-1 sub-progress."""
        try:
            from pupil_labs.web_aois.visualize import generate_heatmaps_for_path
        except ImportError:
            generate_heatmaps_for_path = None

        try:
            from pupil_labs.web_aois.screenshots import slugify
        except ImportError:
            from pupil_labs.web_aois.screenshots import _slugify as slugify

        output_dir = self._output_dir()
        screenshots_dir = self._screenshots_dir()

        manifest_data: dict[str, T.Any] = {}
        manifest_path = output_dir / "tab_manifest.json"
        if manifest_path.exists():
            manifest_data = json.loads(manifest_path.read_text())
        manifest: dict[str, list[str]] = (
            manifest_data.get("tabs", manifest_data)
            if isinstance(manifest_data, dict)
            else {}
        )

        tab_dirs = sorted(output_dir.glob("tab-*"))
        if not tab_dirs:
            log.warning("Web AOIs: no tab-* directories found; skipping heatmap step.")
            yield 1.0
            return

        total_steps = len(tab_dirs) * 2  # gaze + fixation per tab
        done = 0

        for tab_dir in tab_dirs:
            tab_name = tab_dir.name
            first_url = (manifest.get(tab_name) or [None])[0]

            if first_url:
                screenshot_dir = screenshots_dir / slugify(first_url)
            else:
                slug_dirs = [d for d in screenshots_dir.iterdir() if d.is_dir()]
                if len(slug_dirs) == 1:
                    screenshot_dir = slug_dirs[0]
                    log.warning(f"Web AOIs: falling back to {screenshot_dir.name} for {tab_name}")
                else:
                    log.warning(f"Web AOIs: cannot resolve screenshot folder for {tab_name}; skipping.")
                    done += 2
                    yield done / total_steps
                    continue

            if not screenshot_dir.is_dir():
                log.warning(f"Web AOIs: screenshot folder {screenshot_dir} missing; skipping {tab_name}.")
                done += 2
                yield done / total_steps
                continue

            for source in ("gaze", "fixation"):
                if generate_heatmaps_for_path is not None:
                    generate_heatmaps_for_path(tab_dir, screenshot_dir, source=source, scale=1.0)
                else:
                    from pupil_labs.web_aois.visualize import HeatmapVisualizer
                    viz = HeatmapVisualizer(tab_dir, screenshot_dir)
                    viz.save_full_heatmap(scale=1.0, source=source)
                    viz.save_aoi_heatmaps(scale=1.0, source=source)
                log.info(f"Web AOIs: {source} heatmaps saved for {tab_name}")
                done += 1
                yield done / total_steps

    # ------------------------------------------------------------------ #
    # Single Run action                                                    #
    # ------------------------------------------------------------------ #

    def _run_job(self) -> T.Generator[ProgressUpdate, None, None]:
        """Run screenshots -> process -> heatmaps with scaled progress reporting."""
        aoi_json = Path(str(self._aoi_definitions_file))
        if not aoi_json.is_file():
            raise FileNotFoundError(
                f"AOI definitions file not found: {aoi_json}\n"
                "Set the aoi_definitions_file property to a valid JSON file."
            )

        yield ProgressUpdate(_SCREENSHOTS_BAND[0])
        for p in self._run_screenshots(aoi_json):
            yield ProgressUpdate(self._scale(p, _SCREENSHOTS_BAND))

        yield ProgressUpdate(_PROCESS_BAND[0])
        for p in self._run_process():
            yield ProgressUpdate(self._scale(p, _PROCESS_BAND))

        yield ProgressUpdate(_HEATMAPS_BAND[0])
        for p in self._run_heatmaps():
            yield ProgressUpdate(self._scale(p, _HEATMAPS_BAND))

        yield ProgressUpdate(1.0)
        log.info("Web AOIs: pipeline complete.")

    @action
    def run(self) -> None:
        """Take screenshots, process recording, and generate heatmaps."""
        aoi_json = Path(str(self._aoi_definitions_file))
        if not aoi_json.is_file():
            log.error(
                f"Web AOIs: AOI definitions file not found: {aoi_json}\n"
                "Set the aoi_definitions_file property to a valid JSON file before running."
            )
            return

        if self._pipeline_job is not None:
            log.warning("Web AOIs: pipeline is already running; ignoring duplicate run request.")
            return

        job = self.job_manager.run_background_action(
            "Web AOIs: Run Pipeline",
            "WebAoisPlugin._run_job",
        )

        if job is None:
            return

        self._pipeline_job = job

        def _on_finished() -> None:
            self._pipeline_job = None
            log.info("Web AOIs: pipeline finished.")
            try:
                self.refresh_aoi_timeline()
            except Exception as exc:
                log.warning(f"Web AOIs: timeline refresh failed: {exc}")

        job.finished.connect(_on_finished)

    # ------------------------------------------------------------------ #
    # Lifecycle hooks                                                      #
    # ------------------------------------------------------------------ #

    def on_recording_loaded(self, recording: T.Any) -> None:
        """Refresh AOI gaze timeline tracks when cached outputs exist."""
        try:
            self.refresh_aoi_timeline()
        except Exception as exc:
            log.warning(f"Web AOIs: timeline refresh on load failed: {exc}")

    def on_disabled(self) -> None:
        """Remove AOI timeline rows when plugin is disabled."""
        self._clear_timeline_rows()
