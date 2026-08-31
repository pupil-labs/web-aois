import asyncio
import json
import os
import argparse
from typing import Callable, Optional

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


def _qt_pick_save_path(initialfile="web-aois.json"):
    from PyQt6.QtWidgets import QApplication, QFileDialog

    app = QApplication.instance()
    owns_app = False
    if app is None:
        app = QApplication([])
        owns_app = True

    file_path, _ = QFileDialog.getSaveFileName(
        None,
        "Save AOI Definitions as...",
        initialfile,
        "JSON files (*.json);;All Files (*.*)",
    )

    if owns_app:
        app.quit()

    return file_path or None


def _save_definitions_to_path(path, data):
    with open(path, "wt") as output_file:
        json.dump(data, output_file, indent=4)


async def on_new_page(page):
    page.on("dialog", lambda _: None)


async def define_aois(
    start_url=None,
    save_path_provider: Optional[Callable[[dict], Optional[str]]] = None,
    on_saved: Optional[Callable[[str], None]] = None,
):
    if save_path_provider is None:
        save_path_provider = lambda _: _qt_pick_save_path()

    state = {"last_saved_path": None}

    async def save_definitions(source, data):
        del source
        save_path = save_path_provider(data)
        if not save_path:
            return

        _save_definitions_to_path(save_path, data)
        state["last_saved_path"] = save_path
        print("Saved", save_path)

        if on_saved is not None:
            on_saved(save_path)

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

    return state["last_saved_path"]


async def async_main(start_url=None):
    return await define_aois(start_url=start_url)


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
