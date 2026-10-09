CATALOG_LABEL = "晨光书店"


def find_title(items, title):
    return next((item for item in items if item["title"] == title), None)
