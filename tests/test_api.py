import csv

import pupil_labs.web_aois as this_project
from pupil_labs.web_aois.process import RecordingProcessor
from pupil_labs.web_aois.record import _resolve_start_url


def test_package_metadata() -> None:
    assert hasattr(this_project, "__version__")


def test_resolve_start_url_prefers_explicit_url() -> None:
    definitions = {
        "https://first.example": {},
        "https://second.example": {},
    }
    assert (
        _resolve_start_url(definitions, start_url="https://override.example")
        == "https://override.example"
    )


def test_resolve_start_url_adds_https_scheme() -> None:
    definitions = {
        "https://first.example": {},
    }
    assert (
        _resolve_start_url(definitions, start_url="example.com")
        == "https://example.com"
    )


def test_resolve_start_url_falls_back_to_first_definition() -> None:
    definitions = {
        "https://first.example": {},
        "https://second.example": {},
    }
    assert _resolve_start_url(definitions) == "https://first.example"


def test_resolve_start_url_rejects_invalid_url() -> None:
    definitions = {
        "https://first.example": {},
    }

    try:
        _resolve_start_url(definitions, start_url="http://")
    except ValueError as exc:
        assert "Invalid start URL" in str(exc)
    else:
        raise AssertionError("Expected ValueError for invalid start URL")


def test_recording_processor_accepts_browser_event_sidecar(tmp_path) -> None:
    sidecar = tmp_path / "browser-events.csv"
    with sidecar.open("wt", newline="") as handle:
        writer = csv.DictWriter(handle, ["timestamp [ns]", "event"])
        writer.writeheader()
        writer.writerow({"timestamp [ns]": 100, "event": "browser_url[0]=https://example.com"})
        writer.writerow({"timestamp [ns]": 200, "event": "browser_size=1280,720"})

    processor = RecordingProcessor(tmp_path, tmp_path / "output", event_log_path=sidecar)

    event_data, event_timestamps = processor._load_events()
    assert event_data == ["browser_url[0]=https://example.com", "browser_size=1280,720"]
    assert event_timestamps.tolist() == [100, 200]
