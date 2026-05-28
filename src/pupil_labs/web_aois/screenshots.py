import sys
import json
import asyncio
import os
import re
from pathlib import Path

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

from .aoi_locator_helper import get_aoi_locators_for_page


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


def _slugify(value):
    value = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip())
    return value.strip("-") or "page"


async def async_main():
    async with async_playwright() as playwright:
        with open(sys.argv[1], "rt") as aoi_definitions_file:
            aoi_definitions = json.load(aoi_definitions_file)

        output_path = Path(sys.argv[2])
        output_path.mkdir(parents=True, exist_ok=True)

        browser = await playwright.chromium.launch(headless=False, args=['--start-maximized'])
        context_kwargs = {'no_viewport': True}
        context_kwargs.update(_optional_context_auth_kwargs())
        context = await browser.new_context(**context_kwargs)

        page = await context.new_page()
        failed_aois = []
        for url, page_aoi_definitions in aoi_definitions.items():
            page_dir = output_path / _slugify(url)
            page_dir.mkdir(parents=True, exist_ok=True)

            await page.goto(url, wait_until="domcontentloaded")
            try:
                await page.wait_for_load_state("networkidle", timeout=10000)
            except PlaywrightTimeoutError:
                pass

            await page.screenshot(path=page_dir / "full-page.png", full_page=True)

            aoi_locators = get_aoi_locators_for_page(page, page_aoi_definitions)
            for aoi_name, locator in aoi_locators.items():
                try:
                    await locator.first.screenshot(
                        path=page_dir / f"aoi-{_slugify(aoi_name)}.png",
                        timeout=10000,
                    )
                except PlaywrightTimeoutError as error:
                    failed_aois.append((url, aoi_name, str(error).splitlines()[0]))
                    print(f"[warn] AOI screenshot timed out for '{aoi_name}' on {url}")

        if failed_aois:
            print("\nScreenshot completed with AOI warnings:")
            for url, aoi_name, reason in failed_aois:
                print(f"- {url} :: {aoi_name} :: {reason}")

        await context.close()
        await browser.close()


def main():
    asyncio.run(async_main())


if __name__ == '__main__':
    main()
