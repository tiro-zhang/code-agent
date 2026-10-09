"""Slugify utility.

Provides slugify(text), which normalizes text into a lowercase,
hyphen-separated slug using only the standard library.
"""


def slugify(text):
    """Convert text into a slug.

    Leading and trailing whitespace is removed, every run of whitespace
    is collapsed into a single hyphen, and the result is lowercased.
    Empty (or whitespace-only) input yields an empty string.
    """
    return "-".join(text.split()).lower()
