#!/usr/bin/env python3
"""
Clean up annotation files by removing redundant entries.

For each bolded-quote entry, identifies the primary topic(s) it explains.
If that topic was already explained in a previous chapter, removes the entry.
Keeps the first explanation of each topic and removes later re-explanations.

Usage:
    # Preview what would be removed:
    python cleanup_annotations.py --book 1 --dry-run

    # Actually remove redundant entries:
    python cleanup_annotations.py --book 1
"""

import argparse
import re
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
ANNOTATIONS_DIR = PROJECT_ROOT / "src" / "content" / "annotations"
CHAPTERS_JSON = PROJECT_ROOT / "src" / "data" / "chapters.json"

# Topics that are so fundamental they should only be explained once
# (after first explanation, later mentions are noise)
EXPLAIN_ONCE_TOPICS = {
    "natural-philosophy", "alchemy", "royal-society", "humorism",
    "english-civil-war", "plague", "apocalypticism", "puritans",
    "charles-ii", "oliver-cromwell", "charles-i", "the-interregnum",
    "quicksilver-mercury", "piece-of-eight", "calculus-priority-dispute",
    "isaac-newton", "gottfried-wilhelm-von-leibniz", "christiaan-huygens",
    "robert-hooke", "robert-boyle", "john-wilkins", "rene-descartes",
    "trinity-college-cambridge", "gresham-college", "william-laud",
    "glorious-revolution", "jack-ketch", "james-ii", "monmouth",
    "judge-jeffreys", "action-at-a-distance", "vortex-theory",
    "aether", "euclid", "thomas-hobbes", "pirates",
    "english-dissenters", "quakers", "stourbridge-fair",
    "coffeehouses", "the-royal-mint", "banqueting-house",
    "christopher-wren", "john-locke", "baruch-spinoza",
    "samuel-pepys", "caroline-of-ansbach", "sophia-of-the-palatinate",
}


def parse_entries(body_text):
    """Parse annotation body into individual entries.

    Each entry starts with **" (bolded quote).
    Returns list of (entry_text, primary_slugs) tuples.
    The opening context line (before first **") is preserved separately.
    """
    lines = body_text.split('\n')

    # Find the opening context line(s) before first entry
    opening_lines = []
    entry_lines = []
    found_first_entry = False

    for line in lines:
        if not found_first_entry and not line.startswith('**"') and not line.startswith('**\u201c'):
            opening_lines.append(line)
        else:
            found_first_entry = True
            entry_lines.append(line)

    # Split entry_lines into individual entries
    entries = []
    current_entry = []

    for line in entry_lines:
        if (line.startswith('**"') or line.startswith('**\u201c')) and current_entry:
            entries.append('\n'.join(current_entry))
            current_entry = [line]
        else:
            current_entry.append(line)

    if current_entry:
        entries.append('\n'.join(current_entry))

    # Extract primary topic slugs from each entry
    parsed = []
    for entry in entries:
        slugs = re.findall(r'\(/topic/([^)]+)\)', entry)
        parsed.append((entry, slugs))

    return '\n'.join(opening_lines), parsed


def cleanup_book(book_num, dry_run=True):
    """Process all chapters in a book, removing redundant entries."""
    chapters = json.load(open(CHAPTERS_JSON))
    book_chapters = sorted(
        [ch for ch in chapters if ch["book"] == book_num],
        key=lambda x: x["page"]
    )

    # Track which topics have been explained
    explained_topics = {}  # slug -> first chapter page

    total_removed = 0
    total_kept = 0

    for ch in book_chapters:
        page = ch["page"]
        filepath = ANNOTATIONS_DIR / f"chapter-{page:04d}.md"
        if not filepath.exists():
            continue

        content = filepath.read_text()

        # Split frontmatter from body
        parts = content.split('---', 2)
        if len(parts) < 3:
            continue
        frontmatter = parts[1]
        body = parts[2].lstrip('\n')

        opening, entries = parse_entries(body)

        kept_entries = []
        removed_entries = []

        for entry_text, slugs in entries:
            # Check if this entry's primary topic was already explained
            dominated_by_old = False
            if slugs:
                primary_slug = slugs[0]
                if primary_slug in explained_topics and primary_slug in EXPLAIN_ONCE_TOPICS:
                    dominated_by_old = True

            if dominated_by_old:
                removed_entries.append((entry_text, slugs))
                total_removed += 1
            else:
                kept_entries.append(entry_text)
                total_kept += 1
                # Mark all slugs in this entry as explained
                for slug in slugs:
                    if slug not in explained_topics:
                        explained_topics[slug] = page

        if removed_entries:
            print(f"\n  Chapter p.{page} '{ch['location']}':")
            print(f"    Kept: {len(kept_entries)}, Removed: {len(removed_entries)}")
            for entry_text, slugs in removed_entries:
                # Show first 80 chars of the entry
                preview = entry_text[:80].replace('\n', ' ')
                print(f"    - [{slugs[0] if slugs else '?'}] {preview}...")

            if not dry_run:
                # Rebuild the file
                new_body = opening.rstrip('\n') + '\n\n' + '\n\n'.join(kept_entries) + '\n'
                new_content = f"---{frontmatter}---\n\n{new_body}"
                filepath.write_text(new_content)
                print(f"    -> Written")

    print(f"\n{'DRY RUN - ' if dry_run else ''}Summary:")
    print(f"  Total entries kept: {total_kept}")
    print(f"  Total entries removed: {total_removed}")
    print(f"  Topics tracked: {len(explained_topics)}")


def main():
    parser = argparse.ArgumentParser(description="Clean up redundant annotation entries")
    parser.add_argument("--book", type=int, required=True, help="Book number to clean up")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing")
    args = parser.parse_args()

    cleanup_book(args.book, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
