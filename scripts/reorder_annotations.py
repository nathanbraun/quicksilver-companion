#!/usr/bin/env python3
"""
Reorder annotations within chapter files based on where their quotes
appear in the epub text. Fixes wiki annotations that were inserted
at wrong positions by the proportional-placement heuristic.

Usage:
    # Reorder a single chapter (dry run):
    python reorder_annotations.py --chapter 3 --dry-run

    # Reorder a single chapter:
    python reorder_annotations.py --chapter 3

    # Reorder all chapters in a book:
    python reorder_annotations.py --book 1

    # Reorder everything:
    python reorder_annotations.py --all
"""

import argparse
import json
import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
import ebooklib
from ebooklib import epub
import warnings
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

PROJECT_ROOT = Path(__file__).parent.parent
CHAPTERS_JSON = PROJECT_ROOT / "src" / "data" / "chapters.json"
ANNOTATIONS_DIR = PROJECT_ROOT / "src" / "content" / "annotations"
EPUB_PATH = Path.home() / "Calibre Library" / "Neal Stephenson" / \
    "Quicksilver_ The Baroque Cycle #1 (88)" / \
    "Quicksilver_ The Baroque Cycle #1 - Neal Stephenson.epub"


def load_epub_chapters():
    """Load all chapters from the epub as a list (in reading order)."""
    book = epub.read_epub(str(EPUB_PATH))
    chapters = []
    skip_titles = {'Contents', 'Invocation', 'Dramatis Personae', 'Acknowledgments', 'Copyright'}
    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        soup = BeautifulSoup(item.get_content(), "html.parser")
        text = soup.get_text(separator="\n", strip=True)
        title_tag = soup.find(["h1", "h2", "h3"])
        title = title_tag.get_text(strip=True) if title_tag else item.get_name()
        if len(text) > 500 and title not in skip_titles and not title.startswith('text/'):
            chapters.append({"title": title, "text": text})
    return chapters


def build_epub_page_map(epub_chapters, chapters_json):
    """Map chapters.json page -> epub text by sequential alignment."""
    page_to_text = {}
    epub_idx = 0
    for cj in chapters_json:
        page = cj['page']
        if page == 829:
            page_to_text[page] = page_to_text.get(821, '')
            continue
        if epub_idx < len(epub_chapters):
            page_to_text[page] = epub_chapters[epub_idx]['text']
            epub_idx += 1
    return page_to_text


def parse_annotations(content):
    """Parse a chapter annotation file into frontmatter, opening lines, and entries."""
    parts = content.split('---', 2)
    if len(parts) < 3:
        return None, None, None
    frontmatter = parts[1]
    body = parts[2].strip('\n')

    lines = body.split('\n')
    opening_lines = []
    entry_text = []
    found_first = False
    for line in lines:
        if not found_first and not line.startswith('**"') and not line.startswith('**\u201c'):
            opening_lines.append(line)
        else:
            found_first = True
            entry_text.append(line)

    # Split entries on lines starting with **"
    entries = []
    current = []
    for line in entry_text:
        if (line.startswith('**"') or line.startswith('**\u201c')) and current:
            entries.append('\n'.join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        entries.append('\n'.join(current))

    return frontmatter, opening_lines, entries


def extract_quote(entry):
    """Extract the bold-quoted text from an annotation entry."""
    # Match **"quote"** or **\u201cquote\u201d**
    m = re.match(r'\*\*["\u201c](.+?)["\u201d]\*\*', entry)
    if m:
        return m.group(1)
    return None


def find_quote_position(quote, epub_text_lower):
    """Find the position of a quote in the epub text. Returns position or -1."""
    if not quote:
        return -1

    # Try progressively shorter prefixes of the quote
    search = quote.lower()
    for length in [60, 40, 25, 15]:
        prefix = search[:length]
        if len(prefix) < length and length > 15:
            continue
        pos = epub_text_lower.find(prefix)
        if pos != -1:
            return pos

    # Try individual distinctive words (4+ chars) as fallback
    words = [w for w in search.split() if len(w) >= 4]
    if len(words) >= 2:
        # Try pairs of adjacent words
        for i in range(len(words) - 1):
            pair = f"{words[i]} {words[i+1]}"
            pos = epub_text_lower.find(pair)
            if pos != -1:
                return pos

    return -1


def reorder_chapter(chapter_page, epub_page_map, dry_run=True):
    """Reorder annotations in a single chapter file based on epub text position."""
    ch_file = ANNOTATIONS_DIR / f"chapter-{chapter_page:04d}.md"
    if not ch_file.exists():
        print(f"  SKIP p.{chapter_page}: no annotation file")
        return False

    epub_text = epub_page_map.get(chapter_page, "")
    if not epub_text:
        print(f"  SKIP p.{chapter_page}: no epub text")
        return False

    content = ch_file.read_text()
    frontmatter, opening_lines, entries = parse_annotations(content)
    if entries is None:
        print(f"  SKIP p.{chapter_page}: could not parse")
        return False

    epub_text_lower = epub_text.lower()

    # Find position for each entry
    positioned = []
    unpositioned = []
    for i, entry in enumerate(entries):
        quote = extract_quote(entry)
        pos = find_quote_position(quote, epub_text_lower)
        if pos >= 0:
            positioned.append((pos, i, entry))
        else:
            unpositioned.append((i, entry, quote))

    if not positioned:
        print(f"  SKIP p.{chapter_page}: no quotes found in epub text")
        return False

    # Sort by epub position
    positioned.sort(key=lambda x: x[0])
    new_order = [entry for pos, orig_idx, entry in positioned]

    # Check if order actually changed
    old_order = [entry for entry in entries if entry in new_order]
    if new_order == old_order and not unpositioned:
        print(f"  OK   p.{chapter_page}: already in order ({len(entries)} entries)")
        return False

    # Report what moved
    orig_indices = [orig_idx for pos, orig_idx, entry in positioned]
    moves = 0
    for new_pos, orig_idx in enumerate(orig_indices):
        if new_pos != orig_idx:
            moves += 1

    # For unpositioned entries, insert them where they were relative to positioned ones
    # (best effort - keep their original relative position)
    if unpositioned:
        print(f"  WARN p.{chapter_page}: {len(unpositioned)} entries couldn't be found in epub:")
        for orig_idx, entry, quote in unpositioned:
            print(f"         [{orig_idx}] \"{(quote or '(no quote)')[:50]}...\"")
        # Insert unpositioned entries at their original relative positions
        # Map original index -> new position
        for orig_idx, entry, quote in unpositioned:
            # Find the best insertion point: after the last positioned entry
            # that was originally before this one
            insert_at = 0
            for new_pos, pi in enumerate(orig_indices):
                if pi < orig_idx:
                    insert_at = new_pos + 1
            new_order.insert(insert_at, entry)
            # Update orig_indices to account for insertion
            orig_indices.insert(insert_at, orig_idx)

    print(f"  FIX  p.{chapter_page}: {moves} entries moved, {len(unpositioned)} unmatched ({len(entries)} total)")

    if moves == 0 and not unpositioned:
        return False

    if dry_run:
        # Show the reordering
        for new_pos, (pos, orig_idx, entry) in enumerate(positioned):
            quote = extract_quote(entry)
            moved = " <-- MOVED" if new_pos != orig_idx else ""
            print(f"         [{orig_idx}->{new_pos}] pos={pos:>6} \"{quote[:50]}\"{moved}")
        return True

    # Rebuild file
    opening = '\n'.join(opening_lines).rstrip('\n')
    entries_text = '\n\n'.join(new_order)
    new_body = f"{opening}\n\n{entries_text}\n"
    new_content = f'---{frontmatter}---\n\n{new_body}'
    ch_file.write_text(new_content)
    return True


def main():
    parser = argparse.ArgumentParser(description="Reorder annotations by epub text position")
    parser.add_argument("--chapter", type=int, help="Chapter start page")
    parser.add_argument("--book", type=int, help="All chapters in a book")
    parser.add_argument("--all", action="store_true", help="All chapters")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    chapters = json.load(open(CHAPTERS_JSON))

    print("Loading epub...")
    epub_chapters = load_epub_chapters()
    epub_page_map = build_epub_page_map(epub_chapters, chapters)

    if args.chapter:
        targets = [ch for ch in chapters if ch["page"] == args.chapter]
    elif args.book:
        targets = [ch for ch in chapters if ch["book"] == args.book]
    elif args.all:
        targets = chapters
    else:
        parser.print_help()
        sys.exit(1)

    if not targets:
        print("No matching chapters found")
        sys.exit(1)

    print(f"{'DRY RUN: ' if args.dry_run else ''}Reordering {len(targets)} chapter(s)...\n")

    fixed = 0
    for ch in targets:
        if reorder_chapter(ch["page"], epub_page_map, dry_run=args.dry_run):
            fixed += 1

    print(f"\n{'Would fix' if args.dry_run else 'Fixed'}: {fixed}/{len(targets)} chapters")


if __name__ == "__main__":
    main()
