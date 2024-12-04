# Web AOIs
This project can be used with a [Pupil Labs Neon eye tracker](https://pupil-labs.com/products/neon) to record and visualize data as a user browses a webpage. Gaze data is mapped to coordinates for the webpage and for individual AOIs within a page.

## Installation
Install the python package and initialize Playwright
```
pip install git+https://github.com/pupil-labs/web-aois.git
playwright install
```

## Usage
1. Create an [AOI Definitions](#aoi-defintions) file
    ```bash
    pl-web-aois-define
    ```
    1. This will open a web browser. Navigate to the page you intend to study.
    2. Move the mouse over an AOI element. A red box will appear indicating the extents of the element.
        * Note: To select the enclosing element of the currently highlighted element, press the `p` key.
    3. Right-click to create the AOI. You will be prompted for a name.
    4. Once all of the AOIs for this page are defined, click on the `Save` button under the list of AOIs.

    NOTE: At this time on most sites (not on SPAs, for example), navigating to a new page will reset the list of AOIs, so you must define and save AOIs one page at a time. You can then manually combine the definitions.

    NOTE: The definition tool uses xpaths to identify web elements, which is [not recommended by Playwright developers](https://playwright.dev/docs/locators#locate-by-css-or-xpath) because it relies on the underlying structure of the web page which may not be consistent. If that structure changes (which you may not be able to tell just by looking at the page), you will need to re-define the AOIs. For more reliable definitions, you can use locators by [manually specifying your AOI definitions](#aoi-defintions).

2. Collect data

    a. Record a browsing session. This will connect to the companion app and start a recording. The recording will be stopped when the browser window is closed.

    You can optionally specify a URL to start with. If you do not, the first URL in the AOI definitions file will be used.
    ```bash
    pl-web-aois-record path-to-aoi-defs.json [https://example.com/]
    ```

    b. Download and extract the recording to your PC. Recordings can be [transferred from the device over USB](https://docs.pupil-labs.com/neon/data-collection/transfer-recordings-via-usb/#transfer-recordings-via-usb) or downloaded from Pupil Cloud (use "Native Recording Data").

3. Process your recording to generate new CSV files that have gaze mapped to web page coordinates and individual AOI coordinates
    ```bash
    pl-web-aois-process path-to-recording process-output-path
    ```

4. Visualize your data

    a. Collect screenshots
    ```bash
    pl-web-aois-screenshots path-to-aoi-defs.json screenshots-output-path
    ```

    b. Create visualizations
    ```bash
    pl-web-aois-visualize process-output-path screenshots-output-path
    ```


## AOI Definitions
The AOI definitions file is a JSON-formatted structure that describes which elements on which webpages should be considered AOI's. The file follows this format:
```json
{
    "[url]":{
        "[aoi name]": [
            {
                "type": "[selector type]",
                "args": {
                    "[arg name]": "[arg value]",
                    ...
                },
            },{
                "type": "[selector type]",
                "args": {
                    "[arg name]": "[arg value]",
                    ...
                }
            },{
                ...
            }
        ]
        ...
    }
}
```
* `url` indicates on which page the AOIs defined within it should be considered
* `aoi name` is up to you, but avoid characters that aren't friendly for file names
* The value for each AOI is an array of sequentially applied selector definitions.
* The first selector definition in each AOI array must have a `type` of one of the following:
    * [`alt_text`](https://playwright.dev/python/docs/api/class-page#page-get-by-alt-text)
    * [`label`](https://playwright.dev/python/docs/api/class-page#page-get-by-label)
    * [`placeholder`](https://playwright.dev/python/docs/api/class-page#page-get-by-placeholder)
    * [`role`](https://playwright.dev/python/docs/api/class-page#page-get-by-role)
    * [`test_id`](https://playwright.dev/python/docs/api/class-page#page-get-by-test-id)
    * [`text`](https://playwright.dev/python/docs/api/class-page#page-get-by-text)
    * [`title`](https://playwright.dev/python/docs/api/class-page#page-get-by-title)
    * [`locator`](https://playwright.dev/python/docs/api/class-page#page-locator)
* Subsequent selectors are applied to the previous selector result to identify child elements therein. Except for the first selector definition in each AOI array, the following `type` values are allowed in addition to the ones above:
    * [`filter`](https://playwright.dev/python/docs/api/class-locator#locator-filter)
    * [`nth`](https://playwright.dev/python/docs/api/class-locator#locator-nth)
    * [`first`](https://playwright.dev/python/docs/api/class-locator#locator-first)
    * [`last`](https://playwright.dev/python/docs/api/class-locator#locator-last)

The corresponding links for each of the selector types documents arguments for each selector type. Some arguments accept regular expressions (e.g., `filter(has_text=re.compile(...))`), but JSON does not support regular expression literals like JavaScript or Python do. To specify that an argument should be interpreted has a regular expression, append `(re)` to the argument name like so:
```json
    ...
    {
        "type": "filter",
        "args": {
            "has_text(re)": "^Lorem ipsum dolor.$"
        }
    }
    ...
```

Under the hood, AOIs on a webpage are identified using [Playwright's locators](https://playwright.dev/python/docs/locators). You can use Playwright's `codegen` tool to determine the appropriate values for `type` and its `args` when setting up your AOIs.
* Launch the codegen tool
    ```bash
    playwright codegen "https://example.com/"
    ```
* Click the `Pick Locator` tool on the toolbar at the top of the browser window
* Hover the mouse over the element you wish to use as an AOI
* Note the tooltip that appears below the AOI. It will be something like `get_by_text("The Neon Companion app can")`, `get_by_role("img", name="Fixations")`, `locator("video")`, etc.
    * If it starts with `get_by_`, then the following word indicates the value for type
    * If it starts with `locator`, then the type will be `locator`.
    * Values inside the parentheses are the arguments. You may probably need to look up the name of the first argument.
* The dot `.` operator separates values for the selector definition array. In the example below, the `our_tools_section` AOI had a tooltip that read: `locator("div").filter(has_text="Our tools make the hidden").nth(3)`.

Here's an example that defines two simple AOIs for one webpage and one complex AOI for another webpage:
```json
{
    "https://docs.pupil-labs.com/neon/data-collection/data-streams/": {
        "gaze_paragraph": [{
            "type": "text",
            "args": {
                "text": "The Neon Companion app can"
            }
        }],
        "fixations_image": [{
            "type": "role",
            "args": {
                "role": "img",
                "name": "Fixations"
            }
        }]
    },
    "https://pupil-labs.com/": {
        "our_tools_section": [
            {
                "type": "locator",
                "args": {
                    "selector": "div"
                }
            },{
                "type": "filter",
                "args": {
                    "has_text": "Our tools make the hidden"
                }
            },{
                "type": "nth",
                "args": {
                    "index": 3
                }
            }
        ]
    }
}
```

## Output Files
### Data files
A file named `gazes.csv` file will be generated which represents mapped gaze data to the webpage in full. It includes the following columns:

| Column         | Description
|----------------|--------------
| timestamp [ns] | UTC timestamp in nanoseconds of the sample
| x [norm]       | X-coordinate of the mapped gaze point to the browser window content in normalized space (`0-1`)
| y [norm]       | Y-coordinate of the mapped gaze point to the browser window content in normalized space (`0-1`)
| window x [px]  | X-coordinate of the mapped gaze point to the browser window content in pixels (ignores page scroll)
| window y [px]  | Y-coordinate of the mapped gaze point to the browser window content in pixels (ignores page scroll)
| page x [px]    | X-coordinate of the mapped gaze point to the web page in pixels
| page y [px]    | Y-coordinate of the mapped gaze point to the web page in pixels

For each AOI, a `aoi-[AOI_NAME].csv` file will be generated which represents mapped gaze data that lands on that particular AOI. It includes the following columns:

| Column         | Description
|----------------|--------------
| timestamp [ns] | UTC timestamp in nanoseconds of the sample
| x [norm]       | X-coordinate of the mapped gaze point to the AOI in normalized space (`0-1`)
| y [norm]       | Y-coordinate of the mapped gaze point to the AOI in normalized space (`0-1`)
| x [px]         | X-coordinate of the mapped gaze point to the AOI in pixels
| y [px]         | Y-coordinate of the mapped gaze point to the AOI in pixels

### Image files
If heatmaps are generated per-recording, you will find two `.png` files for the whole page (`heatmap-gazes-overlaid.png` and `heatmap-gazes-transparent.png`) and two `.png` files for each AOI (`heatmap-aoi-[AOI_NAME]-overlaid.png` and `heatmap-aoi-[AOI_NAME]-transparent.png`). The transparent images include only the heatmap data, while the overlaid versions show the heatmap superimposed on captures of the webpage and AOIs.


## Known issues
* Although Playwright supports several browsers, this has only been configured and tested with Chromium.
* At this time browsing must be limited to a single window and tab. Gaze mapping in other windows or tabs is currently unsupported and results are undefined.
* Firefox can't track new tabs created with `Ctrl+t`.
* Chromium doesn't trigger focus/visibility changes (switching between tabs) consistently.
