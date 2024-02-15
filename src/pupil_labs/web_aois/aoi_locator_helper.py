import re

aoi_type_func_map = {
    "alt_text": "get_by_alt_text",
    "label": "get_by_label",
    "placeholder": "get_by_placeholder",
    "role": "get_by_role",
    "test_id": "get_by_test_id",
    "text": "get_by_text",
    "title": "get_by_title",
    "locator": "locator",
    "filter": "filter",
    "nth": "nth",
    "first": lambda t,**args: t.first,
    "last": lambda t,**args: t.last,
}

def get_aoi_locators_for_page(page, aoi_definitions):
    locators = {}
    for aoi_name,locator_definitions in aoi_definitions.items():
        locators[aoi_name] = page
        for locator_definition in locator_definitions:
            locators[aoi_name] = get_sub_locator(locators[aoi_name], locator_definition)

    return locators

def get_sub_locator(target, locator_definition):
    if locator_definition["type"] not in aoi_type_func_map:
        print("Unrecognized aoi type", locator_definition["type"])
        return

    args = locator_definition.get("args", {})
    new_args = {}
    for param,arg in args.items():
        if param.endswith("(re)"):
            new_args[param[:-4]] = re.compile(arg)
        else:
            new_args[param] = arg

    args = new_args

    mapped_func = aoi_type_func_map[locator_definition["type"]]
    if isinstance(mapped_func, str):
        locater_method = getattr(target, mapped_func)
        locator = locater_method(**args)
    else:
        locator = mapped_func(target, **args)

    if "next" in locator_definition:
        locator = get_sub_locator(locator, locator_definition["next"])


    return locator
