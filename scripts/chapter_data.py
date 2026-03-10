"""Chapter structure data for Quicksilver."""

import json
from pathlib import Path

CHAPTERS_FILE = Path(__file__).parent.parent / "src" / "data" / "chapters.json"


def load_chapters():
    with open(CHAPTERS_FILE) as f:
        return json.load(f)


CHAPTERS = load_chapters()
CHAPTER_START_PAGES = sorted(ch["page"] for ch in CHAPTERS)


def get_chapter_for_page(page: int) -> dict | None:
    """Given a page number, return the chapter it belongs to.
    Pages before the first chapter (e.g. front matter) get assigned to chapter 1.
    """
    result = None
    for ch in CHAPTERS:
        if ch["page"] <= page:
            result = ch
        else:
            break
    # Front matter pages before the first chapter
    if result is None and CHAPTERS:
        result = CHAPTERS[0]
    return result


BOOK_NAMES = {1: "Quicksilver", 2: "King of the Vagabonds", 3: "Odalisque"}
