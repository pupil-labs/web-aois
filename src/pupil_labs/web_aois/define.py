import asyncio
import json

from tkinter import filedialog
from importlib.resources import files


from playwright.async_api import async_playwright

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

async def async_main():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=False, args=['--start-maximized'])
        context = await browser.new_context(no_viewport=True)
        context.on("page", on_new_page)
        await context.add_init_script(path=files('pupil_labs.web_aois.client').joinpath('define.js'))
        await context.expose_binding('web_aois_save_definitions', save_definitions)

        page = await context.new_page()

        while len(context.pages) > 0:
            async with context.pages[0].expect_event("close", timeout=0) as _:
                pass

        await context.close()


def main():
    asyncio.run(async_main())

if __name__ == '__main__':
    main()
