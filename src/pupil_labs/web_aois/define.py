import asyncio
import json
import os
import argparse

from tkinter import filedialog
from importlib.resources import files


from playwright.async_api import async_playwright


def _optional_context_auth_kwargs():
    username = os.getenv("WEB_AOIS_AUTH_USERNAME")
    password = os.getenv("WEB_AOIS_AUTH_PASSWORD")

    if bool(username) != bool(password):
        raise ValueError(
            "Set both WEB_AOIS_AUTH_USERNAME and WEB_AOIS_AUTH_PASSWORD, or neither."
        )

    if username and password:
        return {
            "http_credentials": {
                "username": username,
                "password": password,
            }
        }

    return {}


async def save_definitions(source, data):
    output_file = filedialog.asksaveasfile(
        mode='w',
        title="Save AOI Definitions as...",
        initialfile="web-aois.json",
        defaultextension=".json",
        filetypes=[
            ('JSON files', '*.json'),
            ('All Files', '*.*'),
        ]
    )
    if output_file is None:
        return

    json.dump(data, output_file, indent=4)
    print("Saved", output_file.name)
    output_file.close()


async def on_new_page(page):
    page.on("dialog", lambda _: None)


async def async_main(start_url=None):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=False, args=['--start-maximized'])
        context_kwargs = {"no_viewport": True}
        context_kwargs.update(_optional_context_auth_kwargs())

        context = await browser.new_context(**context_kwargs)
        context.on("page", on_new_page)
        await context.add_init_script(path=files('pupil_labs.web_aois.client').joinpath('define.js'))
        await context.expose_binding('web_aois_save_definitions', save_definitions)

        page = await context.new_page()
        if start_url:
            await page.goto(start_url, wait_until="domcontentloaded")

        while len(context.pages) > 0:
            async with context.pages[0].expect_event("close", timeout=0) as _:
                pass

        await context.close()


def main():
    parser = argparse.ArgumentParser(
        description="Open Web AOI definition UI with optional start URL."
    )
    parser.add_argument(
        "url",
        nargs="?",
        default=None,
        help="Optional website URL to open immediately.",
    )
    args = parser.parse_args()

    asyncio.run(async_main(start_url=args.url))


if __name__ == '__main__':
    main()
