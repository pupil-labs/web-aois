# Web AOIs
This project can be used with a [Pupil Labs Neon eyetracker](https://pupil-labs.com/products/neon) to record and visualize data as a user browses a webpage. Gaze data is mapped to coordinates for the webpage and for individual AOIs within a page.

## Installation
Install the python package and initialize Playwright
```
pip install git+https://github.com/pupil-labs/pupil-web-aois.git
playwright install
```

## Usage
1. Create an [AOI Definitions](#aoi-defintions) file

2. Collect data

    a. Record a browsing session. This will connect to the companion app and start a recording. The recording will be stopped when the browser window is closed.

    You can optionally specify a URL to start with. If you do not, the first URL in the AOI definitions file will be used.
    ```bash
    pl-web-aois-record path-to-aoi-defs.json [https://example.com/]
    ```

    b. Download and extract the recording to your PC. Recordings can be [transfered from the device over USB](https://docs.pupil-labs.com/neon/data-collection/transfer-recordings-via-usb/#transfer-recordings-via-usb) or downloaded from Pupil Cloud.

3. Process your recording to generate new CSV files that have gaze mapped to web page coordinates and individual AOI coordinates
    ```bash
    pl-web-aois-process path-to-recording output-path
    ```

4. Visualize your data

    a. Collect screenshots
    ```bash
    pl-web-aois-screenshots path-to-aoi-defs.json output-path
    ```

    b. Create visualizations
    ```bash
    pl-web-aois-visualize process-output-path screenshots-output-path
    ```


## AOI Defintions
The AOI definitions file is a JSON-formatted structure that defines which elements on which webpages should be considered AOI's. The file follows this format:
```json
{
    "[url]":{
        "[aoi name]": {
            "type": "[selector type]",
            "args": {
                "[arg name]": "[arg value]",
                ...
            },
        },
        ...
    }
}
```

* `aoi name` is up to you, but avoid characters that aren't friendly for file names
* `selector type` must be one of:
    * [`alt_text`](https://playwright.dev/python/docs/api/class-page#page-get-by-alt-text)
    * [`label`](https://playwright.dev/python/docs/api/class-page#page-get-by-label)
    * [`placeholder`](https://playwright.dev/python/docs/api/class-page#page-get-by-placeholder)
    * [`role`](https://playwright.dev/python/docs/api/class-page#page-get-by-role)
    * [`test_id`](https://playwright.dev/python/docs/api/class-page#page-get-by-test-id)
    * [`text`](https://playwright.dev/python/docs/api/class-page#page-get-by-text)
    * [`title`](https://playwright.dev/python/docs/api/class-page#page-get-by-title)
    * (see corresponding links for documentation on arguments)

Under the hood, AOIs on a webpage are identified using [Playwright's locators](https://playwright.dev/python/docs/locators). You can use Playwright's `codegen` tool to determine the appropriate values for `type` and its `args` when setting up your AOIs.
* Launch the codegen tool
    ```bash
    playwright codegen "https://example.com/"
    ```
* Click the `Pick Locator` tool on the toolbar at the top of the browser window
* Hover the mouse over the element you wish to use as an AOI
* Note the tooltip that appears below the AOI. It will be something like `get_by_text("The Neon Companion app can")` or `get_by_role("img", name="Fixations")`
* The word after `get_by_` is the type, and the values inside the parentheses are the arguments.

Here's an example that defines two AOIs for one webpage:
```json
{
    "https://docs.pupil-labs.com/neon/data-collection/data-streams/": {
        "gaze": {
            "type": "text",
            "args": {
                "text": "The Neon Companion app can"
            }
        },
        "fixations": {
            "type": "role",
            "args": {
                "role": "img",
                "name": "Fixations"
            }
        }
    }
}
```

## Known issues
* Although Playwright supports several browsers, this has only been configured and tested with Chromium
* At this time browsing must be limited to a single window and tab. Gaze mapping in other windows or tabs is currently unsupported and results are undefined.
* Firefox can't track new tabs created with `Ctrl+t`
* Chromium doesn't trigger focus/visibility changes (switching between tabs) consistently
