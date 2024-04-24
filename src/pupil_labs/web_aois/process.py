from pathlib import Path
import re
import csv

import numpy as np

import decord

from pupil_labs.real_time_screen_gaze.gaze_mapper import GazeMapper


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
        self.scroll_position = (0, 0)
        self.aoi_definitions = {}

        self.output_path = output_path
        self.output_path.mkdir(parents=True, exist_ok=True)
        self.gaze_writer = csv.DictWriter(
            (output_path/"gazes.csv").open('wt'),
            [
                'timestamp',
                'norm x',
                'norm y',
                'window x [px]',
                'window y [px]',
                'page x [px]',
                'page y [px]',
            ]
        )
        self.aoi_writers = {}

        self.gaze_writer.writeheader()

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
                    'timestamp',
                    'x norm',
                    'y norm',
                    'x [px]',
                    'y [px]',
                ]
            )
            self.aoi_writers[name].writeheader()


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
            "timestamp": timestamp,
            "norm x": surface_gaze.x,
            "norm y": surface_gaze.y,
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
                    'timestamp': timestamp,
                    'x [px]': aoi_gaze[0],
                    'y [px]': aoi_gaze[1],
                    'x norm': aoi_gaze[0] / aoi_bounds['width'],
                    'y norm': aoi_gaze[1] / aoi_bounds['height'],
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
    def __init__(self, recording_path, output_path):
        self.recording_path = Path(recording_path)
        self.output_path = Path(output_path)

        self.event_regex = re.compile(r'(?P<event>[^\[=]*)(\[(?P<args>[^\]]*)\])?(=(?P<value>.*))?')

        calibration = load_calibration(self.recording_path / 'calibration.bin')
        self.gaze_mapper = GazeMapper(calibration)
        self.browser_client_size = (1, 1)

        self.tab_states = []
        self.active_tab = None
        self.last_frame = None

    def process(self):
        video_file = self.recording_path / "Neon Scene Camera v1 ps1.mp4"
        video_reader = decord.VideoReader(str(video_file), ctx=decord.cpu(0))
        video_timestamps = np.fromfile(video_file.with_suffix(".time"), dtype="<u8")

        frames_with_timestamps = TimedDataCollection(video_timestamps, video_reader)

        event_file = self.recording_path / "event.txt"
        event_data = event_file.read_text().split("\n")
        event_timestamps = np.fromfile(event_file.with_suffix(".time"), dtype="<u8")

        events_with_timestamps = TimedDataCollection(event_timestamps, event_data)

        gaze_file = self.recording_path / "gaze ps1.raw"
        gaze_data = np.fromfile(gaze_file, dtype="<f4").reshape((-1, 2))
        gaze_timestamps = np.fromfile(gaze_file.with_suffix(".time"), dtype="<u8")

        gazes_with_timestamps = TimedDataCollection(gaze_timestamps, gaze_data)

        self.event_generator = ExpirationGenerator(events_with_timestamps)
        self.gaze_generator = ExpirationGenerator(gazes_with_timestamps)

        for frame_timestamp, frame in frames_with_timestamps:
            self.iterate_until(frame_timestamp)
            self.process_frame(frame_timestamp, frame)

        self.iterate_until(None)

    def iterate_until(self, timestamp):
        for gaze_timestamp, gaze in self.gaze_generator.until(timestamp):
            for event_timestamp, event in self.event_generator.until(gaze_timestamp):
                self.process_event(event_timestamp, event)

            self.process_gaze(gaze_timestamp, gaze)

    def process_frame(self, timestamp, frame):
        if self.active_tab is None:
            return

        if self.active_tab.markers_dirty:
            if self.active_tab.surface is None:
                self.active_tab.surface = self.gaze_mapper.add_surface(
                    self.active_tab.marker_verts,
                    self.browser_client_size
                )
            else:
                self.active_tab.surface = self.gaze_mapper.replace_surface(
                    self.active_tab.surface,
                    self.active_tab.marker_verts,
                    self.browser_client_size
                )

            self.active_tab.markers_dirty = False

        self.last_frame = frame.asnumpy()

    def process_gaze(self, timestamp, gaze):
        if self.last_frame is None:
            return

        # @TODO: each call to process_frame has to find surface tags again
        #        this is only necessary when the frame has changed
        #        if the frame hasn't changed, gaze can be mapped to the previously
        #        calculated surface position
        result = self.gaze_mapper.process_frame(self.last_frame, gaze)
        for surface_uid, surface_gazes in result.mapped_gaze.items():
            if surface_uid == self.active_tab.surface.uid:
                for surface_gaze in surface_gazes:
                    self.active_tab.process_gaze(
                        timestamp,
                        surface_gaze,
                        self.browser_client_size
                    )

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
                self.browser_client_size = [int(v) for v in event_match.group('value').split(',')]


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
    import sys
    processor = RecordingProcessor(sys.argv[1], sys.argv[2])
    processor.process()

if __name__ == '__main__':
    main()
