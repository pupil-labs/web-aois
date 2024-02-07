import sys
import json
import asyncio
import time
from importlib.resources import files

from playwright.async_api import async_playwright
from pupil_labs.realtime_api import Device, Network

from .aoi_locator_helper import get_aoi_locators_for_page

class BrowserRelay:
    def __init__(self, pw, device, aoi_definitions_by_url):
        self.pw = pw
        self.device = device
        self.aoi_definitions_by_url = aoi_definitions_by_url

        self.browser = None
        self.context = None

        self.last_size = (-1, -1)
        self.tab_info = {}

        self.marker_size = 250
        self.marker_brightness = 0.5

        self.recording_id = ''

    async def playwright_init(self):
        self.browser = await self.pw.chromium.launch(headless=False, args=['--start-maximized'])
        self.context = await self.browser.new_context(
            no_viewport=True,
            record_video_dir=f"data/{self.recording_id}/"
        )

        await self.context.add_init_script(path=files('pupil_labs.web_aois').joinpath('aoi-client.js'))
        await self.context.expose_binding('propagateScrollEvent', self.on_scroll)
        await self.context.expose_binding('propagateResizeEvent', self.on_resized)
        await self.context.expose_binding('propagateFocusEvent', self.on_tab_switched)
        await self.context.expose_binding('propagatePageVisible', self.on_tab_switched)
        await self.context.expose_binding('propagateLocationChangeEvent', self.on_tab_location_changed)
        await self.context.expose_binding('propagatePageElements', self.on_elements_changed)

        self.context.on("page", self.on_new_page)

    async def on_scroll(self, source, x, y):
        t_ns = time.time_ns()
        await self.send_scroll(source['page'], x, y, t_ns)

    async def on_resized(self, source, width, height):
        if self.last_size == (width, height):
            return

        self.last_size = (width, height)

        # @todo - this scheme assumes all tabs are in the same window
        await self.send_event(
            f"browser_size={width},{height}",
             event_timestamp_unix_ns=time.time_ns()
        )

    async def on_new_page(self, page):
        self.tab_info[page] = {
            'id': len(self.tab_info),
            'load_count': -1,
        }
        page.on('domcontentloaded', self.on_page_loaded)

    async def on_page_loaded(self, page):
        if page.url in ['about:blank', 'chrome://newtab/']:
            return

        await asyncio.sleep(1.0)
        await page.evaluate(f'embedTags({self.marker_size}, {self.marker_brightness})')
        await page.evaluate('installEventListeners()')

    async def on_tab_switched(self, source, scroll_x, scroll_y):
        t_ns = time.time_ns()
        tab_id = self.tab_info[source['page']]['id']

        await self.send_event(
            f"browser_tab={tab_id}", event_timestamp_unix_ns=t_ns
        )
        await self.send_scroll(source['page'], scroll_x, scroll_y, t_ns)

    async def on_tab_location_changed(self, source):
        await self.on_new_url(source['page'])

    async def on_elements_changed(self, source):
        await self.send_elements(source['page'])

    async def send_scroll(self, page, x, y, t_ns):
        tab_info = self.tab_info[page]
        await self.send_event(
            f"browser_scroll[{tab_info['id']},{tab_info['load_count']}]={x},{y}", event_timestamp_unix_ns=time.time_ns()
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
            for aoi_name,locator in aoi_locators.items():
                bounds = await locator.bounding_box()
                bounds = [bounds['x'], bounds['y'], bounds['width'], bounds['height']]
                bounds_str = ','.join([str(v) for v in bounds])

                await self.send_event(f"aoi[{tab_load_id},{aoi_name}]={bounds_str}")

        # @TODO: make marker ids configurable
        for marker_id in range(4):
            locator = page.locator(f"#pupil-apriltag-marker-{marker_id}")
            bounds = await locator.bounding_box()

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
        await self.device.send_event(event, event_timestamp_unix_ns=time.time_ns())

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

async def async_main():
    async with Network() as network:
        dev_info = await network.wait_for_new_device(timeout_seconds=5)

    async with Device.from_discovered_device(dev_info) as device:
        print('Starting recording!')

        async with async_playwright() as playwright:
            with open(sys.argv[1], "rt") as aoi_definitions_file:
                aoi_definitions = json.load(aoi_definitions_file)

            relay = BrowserRelay(playwright, device, aoi_definitions_by_url=aoi_definitions)

            if len(sys.argv) > 2:
                url = sys.argv[2]
            else:
                url = next(iter(aoi_definitions))

            await relay.record_page(url=url)

def main():
    asyncio.run(async_main())

if __name__ == '__main__':
    main()
