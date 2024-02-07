aoi_type_func_map = {
    'alt_text': 'get_by_alt_text',
    'label': 'get_by_label',
    'placeholder': 'get_by_placeholder',
    'role': 'get_by_role',
    'test_id': 'get_by_test_id',
    'text': 'get_by_text',
    'title': 'get_by_title',
}

def get_aoi_locators_for_page(page, aoi_definitions):
    locators = {}
    for aoi_name,aoi in aoi_definitions.items():
        if aoi['type'] not in aoi_type_func_map:
            print('Unrecognized aoi type', aoi['type'])
            continue

        locater_method = getattr(page, aoi_type_func_map[aoi['type']])
        locators[aoi_name] = locater_method(**aoi['args'])

    return locators
