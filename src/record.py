import asyncio
import time
from pathlib import Path

from playwright.async_api import async_playwright
from pupil_labs.realtime_api import Device, Network

aoi_type_func_map = {
    'alt_text': 'get_by_alt_text',
    'label': 'get_by_label',
    'placeholder': 'get_by_placeholder',
    'role': 'get_by_role',
    'test_id': 'get_by_test_id',
    'text': 'get_by_text',
    'title': 'get_by_title',
}

class BrowserRelay:
    def __init__(self, pw, device, aois):
        self.pw = pw
        self.device = device
        self.aois = aois

        self.browser = None
        self.context = None

        self.last_size = (-1, -1)
        self.tab_info = {}

        self.marker_size = 250
        self.marker_brightness = 0.5

        self.recording_id = ''
        self.screenshot_aois_every_load = False
        self.taking_screen_shots = False


    async def playwright_init(self):
        self.browser = await self.pw.chromium.launch(headless=False)#, args=['--start-maximized'])
        self.context = await self.browser.new_context(
            no_viewport=True,
            record_video_dir=f"data/{self.recording_id}/"
        )

        await self.context.add_init_script(path="src/aoi-client.js")
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

        await page.evaluate("hideTags()")
        tab_load_id = f"{tab_info['id']}-{tab_info['load_count']}"
        screenshot_path = Path(f"data/{self.recording_id}/screenshots/{tab_load_id}/")
        await page.screenshot(path=screenshot_path/"full.png", full_page=True)

        if self.screenshot_aois_every_load:
            await self.screenshot_aois(page, screenshot_path/'aois')

        await page.evaluate("showTags()")

    async def send_elements(self, page):
        tab_info = self.tab_info[page]
        tab_load_id = f"{tab_info['id']},{tab_info['load_count']}"

        aoi_locators = self.get_aoi_locators_for_page(page)
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
        if self.taking_screen_shots:
            return

        print('SEND', event)
        await self.device.send_event(event, event_timestamp_unix_ns=time.time_ns())

    async def record_page(self, url):
        self.recording_id = await self.device.recording_start()
        #self.recording_id = "FAKE"

        if self.browser is None:
            await self.playwright_init()

        page = await self.context.new_page()

        await page.goto(url)

        while len(self.context.pages) > 0:
            async with self.context.pages[0].expect_event("close", timeout=0) as _:
                pass

        await self.device.recording_stop_and_save()
        await self.context.close()

    async def screenshot_aois(self, page, path=None):
        self.taking_screen_shots = True

        aoi_locators = self.get_aoi_locators_for_page(page)
        for aoi_name,locator in aoi_locators.items():
            await locator.screenshot(path=path/f"{aoi_name}.png")

        await page.evaluate('window.scrollTo(0, 0)')
        self.taking_screen_shots = False

    def get_aoi_locators_for_page(self, page):
        locators = {}
        for url,aois in self.aois.items():
            if page.url == url:
                for aoi_name,aoi in aois.items():
                    if aoi['type'] not in aoi_type_func_map:
                        print('Unrecognized aoi type', aoi['type'])
                        continue

                    locater_method = getattr(page, aoi_type_func_map[aoi['type']])
                    locators[aoi_name] = locater_method(**aoi['args'])

        return locators

async def main():

    async with Network() as network:
        dev_info = await network.wait_for_new_device(timeout_seconds=5)

    async with Device.from_discovered_device(dev_info) as device:
        print('Starting recording!')

        async with async_playwright() as playwright:
            relay = BrowserRelay(playwright, device, aois={
                "https://docs.pupil-labs.com/neon/data-collection/data-streams/": {
                    "gaze": {'type':"role",'args':{'role': "img", 'name':"Gaze"}},
                    "fixations": {'type':"role",'args':{'role': "img", 'name':"Fixations"}},
                    "eye_states": {'type':"role",'args':{'role': "img", 'name':"Coordinate systems of 3D eye"}},
                    "imu_coordinates": {'type':"role",'args':{'role': "img", 'name':"IMU Coordinate System"}},
                    "imu_camera": {'type':"role",'args':{'role': "img", 'name':"IMU Scene Camera"}},
                    "imu_orientation": {'type':"role",'args':{'role': "img", 'name':"IMU Pitch, Yaw, Roll"}},
                },
            })

            await relay.record_page(
                url="https://docs.pupil-labs.com/neon/data-collection/data-streams/",
            )


asyncio.run(main())
