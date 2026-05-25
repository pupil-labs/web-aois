from pathlib import Path
import re
import csv
import argparse
from collections import OrderedDict
from types import SimpleNamespace

import numpy as np

import cv2
from tqdm import tqdm

from pupil_labs.marker_mapper import Surface
from pupil_labs.camera import perspective_transform
import pupil_apriltags


# Default marker size (px in browser space) from BrowserRelay in record.py.
# Markers are always at the four viewport corners (IDs 0–3), so we can
# reconstruct the surface coordinate system even without browser marker events.
DEFAULT_MARKER_SIZE_PX = 250

# pupil_apriltags returns corners in order: BL, BR, TR, TL (in image/screen
# space, y-down). Surface.localize pairs stored corners with detected corners
# by index, so our surface marker dicts must use the same ordering.
_MARKER_BROWSER_VERTS = {
    # marker_id: [(BL), (BR), (TR), (TL)] in browser-pixel offsets from (0,0).
    # Positions follow record.js embedTags layout:
    #   0 → top=0, left=0   (top-left corner)
    #   1 → top=0, right=0  (top-right corner)
    #   2 → bottom=0, left=0 (bottom-left corner)
    #   3 → bottom=0, right=0 (bottom-right corner)
    # Each entry is a lambda(W, H, s) → array of shape (4, 2)
}


def _marker_verts_normalized(marker_id: int, W: float, H: float, s: float) -> np.ndarray:
    """Return browser-normalized marker corners in BL, BR, TR, TL order."""
    sw, sh = s / W, s / H
    verts = {
        0: [(0,    sh), (sw,   sh), (sw,   0),    (0,    0)],
        1: [(1-sw, sh), (1,    sh), (1,    0),    (1-sw, 0)],
        2: [(0,    1),  (sw,   1),  (sw,   1-sh), (0,    1-sh)],
        3: [(1-sw, 1),  (1,    1),  (1,    1-sh), (1-sw, 1-sh)],
    }
    return np.array(verts[marker_id], dtype=np.float32)


FIXATION_DTYPE = np.dtype(
    [
        ("event_type", "<i4"),
        ("start_timestamp_ns", "<i8"),
        ("end_timestamp_ns", "<i8"),
        ("start_gaze_x", "<f4"),
        ("start_gaze_y", "<f4"),
        ("end_gaze_x", "<f4"),
        ("end_gaze_y", "<f4"),
        ("mean_gaze_x", "<f4"),
        ("mean_gaze_y", "<f4"),
        ("amplitude_pixels", "<f4"),
        ("amplitude_angle_deg", "<f4"),
        ("mean_velocity", "<f4"),
        ("max_velocity", "<f4"),
    ]
)


def load_calibration(path):
    return np.fromfile(
        str(path),
        np.dtype(
            [
                ("version", "u1"),
                ("serial", "6a"),
                ("scene_camera_matrix", "(3,3)d"),
                ("scene_distortion_coefficients", "8d"),
                ("scene_extrinsics_affine_matrix", "(4,4)d"),
                ("right_camera_matrix", "(3,3)d"),
                ("right_distortion_coefficients", "8d"),
                ("right_extrinsics_affine_matrix", "(4,4)d"),
                ("left_camera_matrix", "(3,3)d"),
                ("left_distortion_coefficients", "8d"),
                ("left_extrinsics_affine_matrix", "(4,4)d"),
                ("crc", "u4"),
            ]
        ),
    )


class BrowserTabState:
    def __init__(self, id, output_path):
        self.id = id
        self.history = []
        self.marker_verts = {}
        self.markers_dirty = False
        self.surface = None
        self.img2surface = None
        self.scroll_position = (0, 0)
        self.aoi_definitions = {}

        self.output_path = output_path
        self.output_path.mkdir(parents=True, exist_ok=True)
        self.gaze_writer = csv.DictWriter(
            (output_path/"gazes.csv").open('wt'),
            [
                'timestamp [ns]',
                'x [norm]',
                'y [norm]',
                'window x [px]',
                'window y [px]',
                'page x [px]',
                'page y [px]',
            ]
        )
        self.aoi_writers = {}
        self.fixation_writer = csv.DictWriter(
            (output_path/"fixations.csv").open('wt'),
            [
                'timestamp [ns]',
                'start timestamp [ns]',
                'end timestamp [ns]',
                'duration [ms]',
                'x [norm]',
                'y [norm]',
                'window x [px]',
                'window y [px]',
                'page x [px]',
                'page y [px]',
            ]
        )
        self.aoi_fixation_writers = {}

        self.gaze_writer.writeheader()
        self.fixation_writer.writeheader()

    def add_history(self, url):
        self.history.append(url)

    def set_marker_bounds(self, marker_id, x, y, width, height):
        self.markers_dirty = True
        self.marker_verts[marker_id] = [
            (x, y),
            (x + width, y),
            (x + width, y + height),
            (x, y + height),
        ]

    def set_aoi(self, name, x, y, width, height):
        self.aoi_definitions[name] = {
            'top_left': (x, y),
            'width': width,
            'height': height
        }
        if name not in self.aoi_writers:
            self.aoi_writers[name] = csv.DictWriter(
                (self.output_path/f"aoi-{name}.csv").open('wt'),
                [
                    'timestamp [ns]',
                    'x [norm]',
                    'y [norm]',
                    'x [px]',
                    'y [px]',
                ]
            )
            self.aoi_writers[name].writeheader()

        if name not in self.aoi_fixation_writers:
            self.aoi_fixation_writers[name] = csv.DictWriter(
                (self.output_path/f"aoi-fixations-{name}.csv").open('wt'),
                [
                    'timestamp [ns]',
                    'start timestamp [ns]',
                    'end timestamp [ns]',
                    'duration [ms]',
                    'x [norm]',
                    'y [norm]',
                    'x [px]',
                    'y [px]',
                ]
            )
            self.aoi_fixation_writers[name].writeheader()


    def set_scroll_position(self, x, y):
        self.scroll_position = (x, y)

    def process_gaze(self, timestamp, surface_gaze, browser_size):
        if not surface_gaze.is_on_aoi:
            return

        window_gaze = [
            surface_gaze.x * browser_size[0],
            browser_size[1] - surface_gaze.y * browser_size[1],
        ]
        page_gaze = [window_gaze[i] + self.scroll_position[i] for i in range(2)]

        self.gaze_writer.writerow({
            "timestamp [ns]": timestamp,
            "x [norm]": surface_gaze.x,
            "y [norm]": surface_gaze.y,
            "window x [px]": window_gaze[0],
            "window y [px]": window_gaze[1],
            "page x [px]": page_gaze[0],
            "page y [px]": page_gaze[1],
        })

        for aoi_name, aoi_bounds in self.aoi_definitions.items():
            aoi_gaze = [page_gaze[i] - aoi_bounds['top_left'][i] for i in range(2)]

            x_ok = 0 < aoi_gaze[0] < aoi_bounds['width']
            y_ok = 0 < aoi_gaze[1] < aoi_bounds['height']
            if x_ok and y_ok:
                self.aoi_writers[aoi_name].writerow({
                    'timestamp [ns]': timestamp,
                    'x [px]': aoi_gaze[0],
                    'y [px]': aoi_gaze[1],
                    'x [norm]': aoi_gaze[0] / aoi_bounds['width'],
                    'y [norm]': aoi_gaze[1] / aoi_bounds['height'],
                })

    def process_fixation(self, timestamp, surface_point, browser_size, fixation_record):
        window_point = [
            surface_point[0] * browser_size[0],
            browser_size[1] - surface_point[1] * browser_size[1],
        ]
        page_point = [window_point[i] + self.scroll_position[i] for i in range(2)]

        duration_ms = (fixation_record['end_timestamp_ns'] - fixation_record['start_timestamp_ns']) / 1e6
        self.fixation_writer.writerow({
            'timestamp [ns]': int(timestamp),
            'start timestamp [ns]': int(fixation_record['start_timestamp_ns']),
            'end timestamp [ns]': int(fixation_record['end_timestamp_ns']),
            'duration [ms]': float(duration_ms),
            'x [norm]': float(surface_point[0]),
            'y [norm]': float(surface_point[1]),
            'window x [px]': float(window_point[0]),
            'window y [px]': float(window_point[1]),
            'page x [px]': float(page_point[0]),
            'page y [px]': float(page_point[1]),
        })

        for aoi_name, aoi_bounds in self.aoi_definitions.items():
            aoi_point = [page_point[i] - aoi_bounds['top_left'][i] for i in range(2)]
            x_ok = 0 < aoi_point[0] < aoi_bounds['width']
            y_ok = 0 < aoi_point[1] < aoi_bounds['height']
            if x_ok and y_ok:
                self.aoi_fixation_writers[aoi_name].writerow({
                    'timestamp [ns]': int(timestamp),
                    'start timestamp [ns]': int(fixation_record['start_timestamp_ns']),
                    'end timestamp [ns]': int(fixation_record['end_timestamp_ns']),
                    'duration [ms]': float(duration_ms),
                    'x [px]': float(aoi_point[0]),
                    'y [px]': float(aoi_point[1]),
                    'x [norm]': float(aoi_point[0] / aoi_bounds['width']),
                    'y [norm]': float(aoi_point[1] / aoi_bounds['height']),
                })

class MatchedIterator:
    def __init__(self, *iterables):
        self.iterators = [iter(itr) for itr in iterables]

    def __next__(self):
        return [next(itr) for itr in self.iterators]

class ExpirationGenerator:
    def __init__(self, timed_data_collection):
        self.itr = iter(timed_data_collection)
        self.buffer = None
        self.reached_end_of_iterator = False

    def until(self, timestamp):
        if self.reached_end_of_iterator:
            return

        if self.buffer is not None:
            if timestamp is not None and self.buffer[0] > timestamp:
                return

            yield self.buffer

        try:
            self.buffer = next(self.itr)
            while timestamp is None or self.buffer[0] <= timestamp:
                yield self.buffer
                self.buffer = next(self.itr)
        except StopIteration:
            self.reached_end_of_iterator = True
            return


class TimedDataCollection:
    def __init__(self, timestamps, data):
        paired = zip(timestamps, data)

        if not np.all(timestamps[:-1] <= timestamps[1:]):
            paired = list(sorted(paired, key=lambda p: p[0]))

        self.timestamps, self.data = list(zip(*paired))

    def __iter__(self):
        return MatchedIterator(self.timestamps, self.data)


class RecordingProcessor:
    def __init__(self, recording_path, output_path, debug_mapping=False):
        self.recording_path = Path(recording_path)
        self.output_path = Path(output_path)
        self.debug_mapping = debug_mapping

        self.event_regex = re.compile(r'(?P<event>[^\[=]*)(\[(?P<args>[^\]]*)\])?(=(?P<value>.*))?')

        calibration = load_calibration(self.recording_path / 'calibration.bin')
        from pupil_labs.camera import Camera
        self.scene_size = (1600, 1200)
        self.camera = Camera(
            self.scene_size[0],
            self.scene_size[1],
            calibration['scene_camera_matrix'][0],
            calibration['scene_distortion_coefficients'][0],
        )
        self.marker_detector = pupil_apriltags.Detector(families="tag36h11")
        self.browser_client_size = (1, 1)

        self.tab_states = []
        self.active_tab = None
        self.last_frame = None

        self.debug_stats = {
            "frames_total": 0,
            "frames_with_markers": 0,
            "frames_localized": 0,
            "gaze_total": 0,
            "gaze_on_surface": 0,
            "gaze_written": 0,
            "fixation_total": 0,
            "fixation_on_surface": 0,
            "fixation_written": 0,
        }

    def _print_debug_summary(self):
        if not self.debug_mapping:
            return

        frames_total = self.debug_stats["frames_total"]
        frames_with_markers = self.debug_stats["frames_with_markers"]
        frames_localized = self.debug_stats["frames_localized"]
        gaze_total = self.debug_stats["gaze_total"]
        gaze_on_surface = self.debug_stats["gaze_on_surface"]
        gaze_written = self.debug_stats["gaze_written"]
        fixation_total = self.debug_stats["fixation_total"]
        fixation_on_surface = self.debug_stats["fixation_on_surface"]
        fixation_written = self.debug_stats["fixation_written"]

        marker_rate = (frames_with_markers / frames_total * 100.0) if frames_total else 0.0
        localization_rate = (frames_localized / frames_total * 100.0) if frames_total else 0.0
        on_surface_rate = (gaze_on_surface / gaze_total * 100.0) if gaze_total else 0.0
        written_rate = (gaze_written / gaze_total * 100.0) if gaze_total else 0.0
        fixation_on_surface_rate = (fixation_on_surface / fixation_total * 100.0) if fixation_total else 0.0
        fixation_written_rate = (fixation_written / fixation_total * 100.0) if fixation_total else 0.0

        print("\nMapping Debug Summary")
        print(f"  Frames total: {frames_total}")
        print(f"  Frames with markers: {frames_with_markers} ({marker_rate:.1f}%)")
        print(f"  Frames localized: {frames_localized} ({localization_rate:.1f}%)")
        print(f"  Gaze samples total: {gaze_total}")
        print(f"  Gaze on surface: {gaze_on_surface} ({on_surface_rate:.1f}%)")
        print(f"  Gaze written: {gaze_written} ({written_rate:.1f}%)")
        print(f"  Fixations total: {fixation_total}")
        print(f"  Fixations on surface: {fixation_on_surface} ({fixation_on_surface_rate:.1f}%)")
        print(f"  Fixations written: {fixation_written} ({fixation_written_rate:.1f}%)")

    def _build_surface_for_tab(self, tab_state):
        if not tab_state.marker_verts:
            return None

        width, height = self.browser_client_size
        if width <= 0 or height <= 0:
            return None

        markers = OrderedDict()
        for marker_id, verts in sorted(tab_state.marker_verts.items()):
            verts_np = np.array(verts, dtype=np.float32)
            verts_norm = verts_np / np.array([width, height], dtype=np.float32)
            markers[int(marker_id)] = verts_norm

        return Surface(f"tab-{tab_state.id}", markers)

    def _build_fallback_surface(self, tab_id):
        """Build a surface from the known marker browser layout.

        Used when no ``marker[...]`` browser events were recorded.  The four
        AprilTag markers are always embedded at the corners of the viewport by
        *record.js*, so we can reconstruct their browser-normalised positions
        from the default marker size and the ``browser_size`` event value.

        Corner ordering matches what ``pupil_apriltags.Detector`` returns:
        BL → BR → TR → TL.  ``Surface.localize`` pairs stored corners with
        detected corners by index, so the ordering must be consistent.
        """
        W, H = self.browser_client_size
        if W <= 1 or H <= 1:
            return None
        s = DEFAULT_MARKER_SIZE_PX
        markers = OrderedDict()
        for marker_id in range(4):
            markers[marker_id] = _marker_verts_normalized(marker_id, W, H, s)
        return Surface(f"tab-{tab_id}", markers)

    def process(self):
        video_file = self.recording_path / "Neon Scene Camera v1 ps1.mp4"
        video_timestamps = np.fromfile(video_file.with_suffix(".time"), dtype="<u8")
        video_reader = cv2.VideoCapture(str(video_file))
        if not video_reader.isOpened():
            raise RuntimeError(f"Unable to open video file: {video_file}")

        frames = []
        while True:
            ok, frame = video_reader.read()
            if not ok:
                break
            frames.append(frame)
        video_reader.release()

        frame_count = min(len(video_timestamps), len(frames))
        if frame_count == 0:
            raise RuntimeError("No frames available in scene video")

        if frame_count < len(video_timestamps):
            video_timestamps = video_timestamps[:frame_count]
        if frame_count < len(frames):
            frames = frames[:frame_count]

        frames_with_timestamps = TimedDataCollection(video_timestamps, frames)

        event_file = self.recording_path / "event.txt"
        event_data = event_file.read_text().split("\n")
        event_timestamps = np.fromfile(event_file.with_suffix(".time"), dtype="<u8")

        events_with_timestamps = TimedDataCollection(event_timestamps, event_data)

        gaze_file = self.recording_path / "gaze ps1.raw"
        gaze_data = np.fromfile(gaze_file, dtype="<f4").reshape((-1, 2))
        gaze_timestamps = np.fromfile(gaze_file.with_suffix(".time"), dtype="<u8")

        fixation_file = self.recording_path / "fixations ps1.raw"
        fixation_timestamps_file = fixation_file.with_suffix(".time")
        fixation_data = np.empty(0, dtype=FIXATION_DTYPE)
        fixation_timestamps = np.empty(0, dtype="<u8")
        if fixation_file.exists() and fixation_timestamps_file.exists():
            fixation_data = np.fromfile(fixation_file, dtype=FIXATION_DTYPE)
            # Keep only fixation events to match pl-neon-recording semantics.
            fixation_data = fixation_data[fixation_data['event_type'] == 1]
            # The native fixation time stream aligns with fixation starts. For mapping,
            # midpoint timing is usually more representative of the stable fixation.
            fixation_timestamps = (
                (fixation_data['start_timestamp_ns'] + fixation_data['end_timestamp_ns']) // 2
            ).astype("<u8")

        sample_timestamps = np.concatenate([gaze_timestamps, fixation_timestamps])
        sample_data = ([('gaze', sample) for sample in gaze_data] +
                       [('fixation', sample) for sample in fixation_data])
        samples_with_timestamps = TimedDataCollection(sample_timestamps, sample_data)

        self.event_generator = ExpirationGenerator(events_with_timestamps)
        self.sample_generator = ExpirationGenerator(samples_with_timestamps)

        with tqdm(total=len(video_timestamps)) as pbar:
            for frame_timestamp, frame in frames_with_timestamps:
                self.iterate_until(frame_timestamp)
                self.process_frame(frame_timestamp, frame)

                pbar.update(1)

            self.iterate_until(None)

            self._print_debug_summary()

    def iterate_until(self, timestamp):
        for sample_timestamp, sample in self.sample_generator.until(timestamp):
            for event_timestamp, event in self.event_generator.until(sample_timestamp):
                self.process_event(event_timestamp, event)

            sample_type, sample_data = sample
            if sample_type == 'gaze':
                self.process_gaze(sample_timestamp, sample_data)
            elif sample_type == 'fixation':
                self.process_fixation(sample_timestamp, sample_data)

    def process_frame(self, timestamp, frame):
        if self.debug_mapping:
            self.debug_stats["frames_total"] += 1

        img = frame.asnumpy() if hasattr(frame, 'asnumpy') else frame
        self.last_frame = img

        if self.active_tab is None:
            return

        if self.active_tab.markers_dirty:
            built_surface = self._build_surface_for_tab(self.active_tab)
            if built_surface is not None:
                self.active_tab.surface = built_surface
            self.active_tab.markers_dirty = False

        # pupil_apriltags expects a single-channel image.
        if img.ndim == 3:
            gray = np.round(0.299 * img[..., 0] + 0.587 * img[..., 1] + 0.114 * img[..., 2]).astype(np.uint8)
        else:
            gray = img

        markers = self.marker_detector.detect(gray)
        if not markers:
            # Keep the last valid img2surface so gaze between marker detections
            # can still be mapped (the screen/camera relationship changes slowly).
            return

        if self.debug_mapping:
            self.debug_stats["frames_with_markers"] += 1

        # Fallback for recordings that don't emit marker[...] browser events.
        # Use the known viewport-corner layout rather than from_apriltag_detections,
        # which would create a surface spanning only the tiny marker area.
        if self.active_tab.surface is None:
            fallback = self._build_fallback_surface(self.active_tab.id)
            if fallback is not None:
                self.active_tab.surface = fallback
            else:
                # browser_size not yet known – use detection-based fallback as
                # last resort (will have poor yield but won't crash).
                self.active_tab.surface = Surface.from_apriltag_detections(
                    f"tab-{self.active_tab.id}", markers, self.camera
                )

        localization = self.active_tab.surface.localize(markers, self.camera)
        if localization is not None:
            self.active_tab.img2surface, _ = localization
            if self.debug_mapping:
                self.debug_stats["frames_localized"] += 1
        # If localization fails, retain the previous img2surface.

    def process_gaze(self, timestamp, gaze):
        if self.debug_mapping:
            self.debug_stats["gaze_total"] += 1

        if self.last_frame is None or self.active_tab is None or self.active_tab.img2surface is None:
            return

        gaze_point_dist = np.array(gaze, dtype=np.float32).reshape(1, 2)
        # Some recordings provide normalized gaze [0..1], others pixel gaze.
        if np.max(gaze_point_dist) <= 2.0:
            gaze_point_dist[:, 0] *= self.scene_size[0]
            gaze_point_dist[:, 1] *= self.scene_size[1]
        gaze_point_undist = self.camera.undistort_points(gaze_point_dist)[:, :2]
        gaze_on_surface = perspective_transform(gaze_point_undist, self.active_tab.img2surface)[0]

        is_on_surface = bool(np.all((0.0 <= gaze_on_surface) & (gaze_on_surface <= 1.0)))
        if not is_on_surface:
            return

        if self.debug_mapping:
            self.debug_stats["gaze_on_surface"] += 1

        # BrowserTabState expects y to increase bottom-to-top due to legacy conversion,
        # so we flip here to keep output CSVs aligned with previous behavior.
        surface_gaze = SimpleNamespace(
            x=float(gaze_on_surface[0]),
            y=float(1.0 - gaze_on_surface[1]),
            is_on_aoi=True,
        )
        self.active_tab.process_gaze(timestamp, surface_gaze, self.browser_client_size)

        if self.debug_mapping:
            self.debug_stats["gaze_written"] += 1

    def process_fixation(self, timestamp, fixation):
        if self.debug_mapping:
            self.debug_stats["fixation_total"] += 1

        if self.last_frame is None or self.active_tab is None or self.active_tab.img2surface is None:
            return

        candidate_points = [
            (fixation['mean_gaze_x'], fixation['mean_gaze_y']),
            (fixation['start_gaze_x'], fixation['start_gaze_y']),
            (fixation['end_gaze_x'], fixation['end_gaze_y']),
        ]

        fixation_on_surface = None
        for point_x, point_y in candidate_points:
            fixation_point_dist = np.array([point_x, point_y], dtype=np.float32).reshape(1, 2)
            if np.max(fixation_point_dist) <= 2.0:
                fixation_point_dist[:, 0] *= self.scene_size[0]
                fixation_point_dist[:, 1] *= self.scene_size[1]

            fixation_point_undist = self.camera.undistort_points(fixation_point_dist)[:, :2]
            mapped = perspective_transform(
                fixation_point_undist,
                self.active_tab.img2surface,
            )[0]
            if bool(np.all((0.0 <= mapped) & (mapped <= 1.0))):
                fixation_on_surface = mapped
                break

        if fixation_on_surface is None:
            return

        if self.debug_mapping:
            self.debug_stats["fixation_on_surface"] += 1

        surface_fixation_point = [
            float(fixation_on_surface[0]),
            float(1.0 - fixation_on_surface[1]),
        ]
        self.active_tab.process_fixation(
            timestamp,
            surface_fixation_point,
            self.browser_client_size,
            fixation,
        )

        if self.debug_mapping:
            self.debug_stats["fixation_written"] += 1

    def process_event(self, timestamp, event):
        event_match = self.event_regex.match(event)
        if event_match is None:
            return

        args = event_match.group('args')
        if args is not None:
            args = args.split(',')

        match event_match.group('event'):
            case 'browser_url':
                tab_id = int(args[0])
                self.get_tab_state(tab_id).add_history(event_match.group('value'))

            case 'aoi':
                tab_id, _, aoi_name = args
                bounds = [float(v) for v in event_match.group('value').split(',')]
                self.get_tab_state(tab_id).set_aoi(aoi_name, *bounds)

            case 'marker':
                tab_id, _, marker_id = [int(v) for v in args]
                bounds = [float(v) for v in event_match.group('value').split(',')]
                self.set_marker_bounds(tab_id, marker_id, *bounds)

            case 'browser_scroll':
                tag_id = int(args[0])
                self.active_tab = self.get_tab_state(tag_id)

                scroll_value = [float(v) for v in event_match.group('value').split(',')]
                self.active_tab.set_scroll_position(*scroll_value)

            case 'browser_size':
                new_size = [int(v) for v in event_match.group('value').split(',')]
                if new_size != list(self.browser_client_size):
                    self.browser_client_size = new_size
                    # Invalidate any fallback surface built from the old size.
                    for tab in self.tab_states:
                        if tab.surface is not None and not tab.marker_verts:
                            tab.surface = None

    def set_marker_bounds(self, tab_id, marker_id, x, y, width, height):
        tab_state = self.get_tab_state(tab_id)
        tab_state.set_marker_bounds(marker_id, x, y, width, height)

    def get_tab_state(self, tab_id):
        tab_id = int(tab_id)
        while tab_id >= len(self.tab_states):
            self.tab_states.append(BrowserTabState(tab_id, self.output_path / f"tab-{tab_id}"))

        tab = self.tab_states[tab_id]
        if self.active_tab is None:
            self.active_tab = tab

        return tab


def main():
    parser = argparse.ArgumentParser(
        description="Map Neon gaze data to web page coordinates and AOIs."
    )
    parser.add_argument("recording_path", help="Path to the Neon recording directory")
    parser.add_argument("output_path", help="Path where output CSV files will be written")
    parser.add_argument(
        "--debug-mapping",
        action="store_true",
        help="Print mapping diagnostics (marker/localization/gaze rates)",
    )
    args = parser.parse_args()

    processor = RecordingProcessor(
        args.recording_path,
        args.output_path,
        debug_mapping=args.debug_mapping,
    )
    processor.process()


if __name__ == '__main__':
    main()
