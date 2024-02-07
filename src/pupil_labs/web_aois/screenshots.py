import sys
import json
import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

from .aoi_locator_helper import get_aoi_locators_for_page

async def async_main():
    async with async_playwright() as playwright:
        with open(sys.argv[1], "rt") as aoi_definitions_file:
            aoi_definitions = json.load(aoi_definitions_file)

        output_path = Path(sys.argv[2])

        browser = await playwright.chromium.launch(headless=False, args=['--start-maximized'])
        context = await browser.new_context(no_viewport=True)

        page = await context.new_page()
        for url,aoi_definitions in aoi_definitions.items():
            await page.goto(url)
            await page.screenshot(path=output_path / "full-page.png", full_page=True)

            aoi_locators = get_aoi_locators_for_page(page, aoi_definitions)
            for aoi_name,locator in aoi_locators.items():
                await locator.screenshot(path=output_path / f"aoi-{aoi_name}.png")

def main():
    asyncio.run(async_main())

if __name__ == '__main__':
    main()
