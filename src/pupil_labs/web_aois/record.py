import json
import asyncio
import time
import os
import argparse
import csv
from urllib.parse import urlparse
from pathlib import Path
from importlib.resources import files

from playwright.async_api import async_playwright
from pupil_labs.realtime_api import Device, Network

from .aoi_locator_helper import get_aoi_locators_for_page


def _is_target_closed_error(exc):
    if exc is None:
        return False

    text = str(exc)
    return (
        'Target page, context or browser has been closed' in text
        or 'Execution context was destroyed' in text
    )


def _install_target_closed_exception_filter():
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()

    def _exception_handler(current_loop, context):
        exc = context.get('exception')
        message = context.get('message', '')

        if _is_target_closed_error(exc):
            return

        if 'Target page, context or browser has been closed' in message:
            return

        if previous_handler is not None:
            previous_handler(current_loop, context)
        else:
            current_loop.default_exception_handler(context)

    loop.set_exception_handler(_exception_handler)


class NeonEventSink:
    def __init__(self, device):
        self.device = device

    async def send_event(self, event, event_timestamp_unix_ns):
        await self.device.send_event(event, event_timestamp_unix_ns=event_timestamp_unix_ns)

    async def close(self):
        return


class CsvFileEventSink:
    def __init__(self, output_path):
        self.output_path = Path(output_path)
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_handle = self.output_path.open('wt', newline='')
        self.writer = csv.DictWriter(self.file_handle, ['timestamp [ns]', 'event'])
        self.writer.writeheader()

    async def send_event(self, event, event_timestamp_unix_ns):
        self.writer.writerow({
            'timestamp [ns]': int(event_timestamp_unix_ns),
            'event': event,
        })
        self.file_handle.flush()

    async def close(self):
        self.file_handle.close()


class CompositeEventSink:
    def __init__(self, sinks):
        self.sinks = sinks

    async def send_event(self, event, event_timestamp_unix_ns):
        for sink in self.sinks:
            await sink.send_event(event, event_timestamp_unix_ns)

    async def close(self):
        for sink in self.sinks:
            await sink.close()


def _optional_context_auth_kwargs():
    username = os.getenv('WEB_AOIS_AUTH_USERNAME')
    password = os.getenv('WEB_AOIS_AUTH_PASSWORD')

    if bool(username) != bool(password):
        raise ValueError(
            'Set both WEB_AOIS_AUTH_USERNAME and WEB_AOIS_AUTH_PASSWORD, or neither.'
        )

    if username and password:
        return {
            'http_credentials': {
                'username': username,
                'password': password,
            }
        }

    return {}


def _optional_device_connection_kwargs():
    device_ip = os.getenv('WEB_AOIS_DEVICE_IP')
    device_port = os.getenv('WEB_AOIS_DEVICE_PORT', '8080')

    if not device_ip:
        return {}

    try:
        port = int(device_port)
    except ValueError as exc:
        raise ValueError('WEB_AOIS_DEVICE_PORT must be an integer.') from exc

    if port <= 0 or port > 65535:
        raise ValueError('WEB_AOIS_DEVICE_PORT must be in range 1-65535.')

    return {
        'address': device_ip,
        'port': port,
    }


async def discover_device(timeout_seconds=30):
    async with Network() as network:
        dev_info = await network.wait_for_new_device(timeout_seconds=timeout_seconds)
        if dev_info is not None:
            return dev_info
        if network.devices:
            return network.devices[0]

    raise RuntimeError(
        "No Neon device discovered. Ensure Neon Companion is running, "
        "your device is on the same network, and try again."
    )


def _optional_event_sink_config():
    mode = os.getenv('WEB_AOIS_EVENT_SINK', 'both').strip().lower()
    if mode not in {'neon', 'file', 'both'}:
        raise ValueError("WEB_AOIS_EVENT_SINK must be one of: neon, file, both")

    output_path = os.getenv('WEB_AOIS_EVENT_LOG_PATH')
    return {
        'mode': mode,
        'output_path': output_path,
    }


def _default_event_log_path():
    return Path('data') / f"web-events-{int(time.time())}.csv"


def _build_event_sink(device, mode, output_path=None):
    sinks = []
    if mode in {'neon', 'both'}:
        sinks.append(NeonEventSink(device))

    if mode in {'file', 'both'}:
        log_path = Path(output_path) if output_path else _default_event_log_path()
        sinks.append(CsvFileEventSink(log_path))
        print(f"Writing browser events to: {log_path}")

    if len(sinks) == 1:
        return sinks[0]

    return CompositeEventSink(sinks)


def _resolve_start_url(aoi_definitions, start_url=None):
    if start_url:
        return _normalize_start_url(start_url)

    first_defined_url = next(iter(aoi_definitions))
    return _normalize_start_url(first_defined_url)


def _normalize_start_url(url):
    normalized = (url or '').strip()
    if not normalized:
        raise ValueError(
            'Start URL is empty. Provide a URL in the app or save AOIs for a concrete webpage URL.'
        )

    parsed = urlparse(normalized)
    if not parsed.scheme:
        normalized = f'https://{normalized}'
        parsed = urlparse(normalized)

    if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
        raise ValueError(
            f"Invalid start URL: '{url}'. Use a full URL like https://example.com"
        )

    return normalized

class BrowserRelay:
    def __init__(self, pw, device, aoi_definitions_by_url, event_sink):
        self.pw = pw
        self.device = device
        self.aoi_definitions_by_url = aoi_definitions_by_url
        self.event_sink = event_sink

        self.browser = None
        self.context = None

        self.last_size = (-1, -1)
        self.tab_info = {}

        self.marker_size = 250
        self.marker_brightness = 1
        self.marker_contrast = 3

        self.recording_id = ''

    async def playwright_init(self):
        self.browser = await self.pw.chromium.launch(headless=False, args=['--start-maximized'])
        context_kwargs = {
            'no_viewport': True,
            'record_video_dir': f"data/{self.recording_id}/",
        }
        context_kwargs.update(_optional_context_auth_kwargs())
        self.context = await self.browser.new_context(**context_kwargs)

        await self.context.add_init_script(path=files('pupil_labs.web_aois.client').joinpath('record.js'))
        await self.context.expose_binding('propagateScrollEvent', self.on_scroll)
        await self.context.expose_binding('propagateResizeEvent', self.on_resized)
        await self.context.expose_binding('propagateFocusEvent', self.on_tab_switched)
        await self.context.expose_binding('propagatePageVisible', self.on_tab_switched)
        await self.context.expose_binding('propagateLocationChangeEvent', self.on_tab_location_changed)
        await self.context.expose_binding('propagatePageElements', self.on_elements_changed)

        self.context.on("page", self.on_new_page)

    async def on_scroll(self, source, x, y):
        try:
            t_ns = time.time_ns()
            await self.send_scroll(source['page'], x, y, t_ns)
        except Exception as exc:
            if _is_target_closed_error(exc):
                return
            raise

    async def on_resized(self, source, width, height):
        try:
            if self.last_size == (width, height):
                return

            self.last_size = (width, height)

            # @todo - this scheme assumes all tabs are in the same window
            await self.send_event(
                f"browser_size={width},{height}",
                event_timestamp_unix_ns=time.time_ns()
            )
        except Exception as exc:
            if _is_target_closed_error(exc):
                return
            raise

    async def on_new_page(self, page):
        self.tab_info[page] = {
            'id': len(self.tab_info),
            'load_count': -1,
        }
        page.on('domcontentloaded', self.on_page_loaded)

    async def on_page_loaded(self, page):
        try:
            if page.url in ['about:blank', 'chrome://newtab/']:
                return

            await asyncio.sleep(1.0)
            await page.evaluate(
                f'embedTags({self.marker_size}, {self.marker_brightness}, null, {self.marker_contrast})'
            )
            await page.evaluate('installEventListeners()')
        except Exception as exc:
            if _is_target_closed_error(exc):
                return
            raise

    async def on_tab_switched(self, source, scroll_x, scroll_y):
        try:
            t_ns = time.time_ns()
            tab_id = self.tab_info[source['page']]['id']

            await self.send_event(
                f"browser_tab={tab_id}", event_timestamp_unix_ns=t_ns
            )
            await self.send_scroll(source['page'], scroll_x, scroll_y, t_ns)
        except Exception as exc:
            if _is_target_closed_error(exc):
                return
            raise

    async def on_tab_location_changed(self, source):
        try:
            await self.on_new_url(source['page'])
        except Exception as exc:
            if _is_target_closed_error(exc):
                return
            raise

    async def on_elements_changed(self, source):
        try:
            await self.send_elements(source['page'])
        except Exception as exc:
            if _is_target_closed_error(exc):
                return
            raise

    async def send_scroll(self, page, x, y, t_ns):
        tab_info = self.tab_info[page]
        await self.send_event(
            f"browser_scroll[{tab_info['id']},{tab_info['load_count']}]={x},{y}", event_timestamp_unix_ns=t_ns
        )

    async def on_new_url(self, page):
        tab_info = self.tab_info[page]
        tab_info['load_count'] += 1

        await self.send_event(
            f"browser_url[{tab_info['id']},{tab_info['load_count']}]={page.url}",
            event_timestamp_unix_ns=time.time_ns()
        )

    async def send_elements(self, page):
        tab_info = self.tab_info[page]
        tab_load_id = f"{tab_info['id']},{tab_info['load_count']}"

        if page.url in self.aoi_definitions_by_url:
            aoi_locators = get_aoi_locators_for_page(page, self.aoi_definitions_by_url[page.url])
            for aoi_name, locator in aoi_locators.items():
                bounds = await locator.bounding_box()
                if bounds is None:
                    continue
                bounds = [bounds['x'], bounds['y'], bounds['width'], bounds['height']]
                bounds_str = ','.join([str(v) for v in bounds])

                await self.send_event(f"aoi[{tab_load_id},{aoi_name}]={bounds_str}")

        # @TODO: make marker ids configurable
        for marker_id in range(4):
            locator = page.locator(f"#pupil-apriltag-marker-{marker_id}")
            bounds = await locator.bounding_box()
            if bounds is None:
                continue

            margin = bounds['width'] / 10, bounds['height'] / 10
            real_bounds = [
                bounds['x'] + margin[0],
                bounds['y'] + margin[1],
                bounds['width'] - margin[0]*2,
                bounds['height'] - margin[1]*2
            ]

            bounds_str = ','.join([str(v) for v in real_bounds])

            await self.send_event(f"marker[{tab_load_id},{marker_id}]={bounds_str}")

    async def send_event(self, event, event_timestamp_unix_ns=None):
        if event_timestamp_unix_ns is None:
            event_timestamp_unix_ns = time.time_ns()

        await self.event_sink.send_event(
            event,
            event_timestamp_unix_ns=event_timestamp_unix_ns,
        )

    async def record_page(self, url):
        self.recording_id = await self.device.recording_start()

        if self.browser is None:
            await self.playwright_init()

        page = await self.context.new_page()

        await page.goto(url)

        while len(self.context.pages) > 0:
            async with self.context.pages[0].expect_event("close", timeout=0) as _:
                pass

        await self.device.recording_stop_and_save()
        await self.context.close()


async def record_session(aoi_definitions_path, start_url=None):
    _install_target_closed_exception_filter()

    with open(aoi_definitions_path, "rt") as aoi_definitions_file:
        aoi_definitions = json.load(aoi_definitions_file)

    device_connection = _optional_device_connection_kwargs()
    if device_connection:
        print(
            f"Connecting directly to Neon at {device_connection['address']}:{device_connection['port']}"
        )
        device_context = Device(device_connection['address'], device_connection['port'])
    else:
        dev_info = await discover_device(timeout_seconds=30)
        device_context = Device.from_discovered_device(dev_info)

    async with device_context as device:
        print('Starting recording!')
        event_sink_config = _optional_event_sink_config()
        event_sink = _build_event_sink(
            device,
            mode=event_sink_config['mode'],
            output_path=event_sink_config['output_path'],
        )

        try:
            async with async_playwright() as playwright:
                relay = BrowserRelay(
                    playwright,
                    device,
                    aoi_definitions_by_url=aoi_definitions,
                    event_sink=event_sink,
                )

                url = _resolve_start_url(aoi_definitions, start_url=start_url)

                await relay.record_page(url=url)
        finally:
            await event_sink.close()


async def async_main(aoi_definitions_path, start_url=None):
    await record_session(aoi_definitions_path=aoi_definitions_path, start_url=start_url)


def main():
    parser = argparse.ArgumentParser(
        description="Record browsing data with Neon using AOI definitions."
    )
    parser.add_argument(
        "aoi_definitions_path",
        help="Path to AOI definitions JSON file.",
    )
    parser.add_argument(
        "url",
        nargs="?",
        default=None,
        help="Optional website URL to open immediately.",
    )
    args = parser.parse_args()

    asyncio.run(async_main(args.aoi_definitions_path, start_url=args.url))


if __name__ == '__main__':
    main()
