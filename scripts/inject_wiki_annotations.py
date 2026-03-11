#!/usr/bin/env python3
"""
Inject missing wiki annotations (Stephenson + good community ones) into chapter files.

Usage:
    python inject_wiki_annotations.py --book 1 --dry-run
    python inject_wiki_annotations.py --book 1
"""

import argparse
import os
import re
import json
import sys
import time
from pathlib import Path
import requests

PROJECT_ROOT = Path(__file__).parent.parent
ANNOTATIONS_DIR = PROJECT_ROOT / "src" / "content" / "annotations"
CHAPTERS_JSON = PROJECT_ROOT / "src" / "data" / "chapters.json"
WIKI_DIR = PROJECT_ROOT.parent / "quicksilver-wiki-original" / "docs" / "quicksilver" / "annotations"

# Phrases that indicate a stub/placeholder rather than real content
STUB_PHRASES = [
    'this is a page for',
    'this is a placeholder',
    'this is an intermediate page',
    'this is the quicksilver page',
    'this page will discuss',
    'this page will talk',
    'this discusses the',
    'placeholder for',
    'a page for',
    'a page on',
    'a page about',
    'page for **',
    'page about **',
    'help!',
    'tba',
]

# Phrases that indicate wiki cruft rather than useful content
CRUFT_PHRASES = [
    '### authored entries',
    '### community entry',
    '### stephensonia',
    'seems worthy of an entry',
    'for chris\' sake',
    'monty python',
    'all i can think of is',
]


def clean_wiki_content(content):
    """Strip metaweb header and clean up wiki markdown."""
    # Remove title line(s) - handles various formats
    content = re.sub(r'^#[^\n]*\n+', '', content)
    # Remove "From the Quicksilver Metaweb" line
    content = re.sub(r'From the Quicksilver Metaweb\.?\s*\n*', '', content)
    # Remove "(Redirected from ...)" lines
    content = re.sub(r'\(Redirected from[^\)]+\)\s*\n*', '', content)
    # Remove ### section headers like "### Stephensonia", "### Authored entries"
    content = re.sub(r'^#{1,4}\s+.*$', '', content, flags=re.MULTILINE)
    # Remove table markup
    content = re.sub(r'\|[^\n]*\|', '', content)
    # Remove image references
    content = re.sub(r'!\[[^\]]*\]\([^)]*\)', '', content)
    # Remove metaweb-style links, keep text
    content = re.sub(r'\[([^\]]+)\]\(/[^)]+\)', r'\1', content)
    # Remove external links, keep text
    content = re.sub(r'\[([^\]]+)\]\(http[^)]+\)', r'\1', content)
    # Remove bold/italic markers
    content = re.sub(r'\*{1,3}([^*]+)\*{1,3}', r'\1', content)
    # Remove bullet list markers referencing other pages/entries
    content = re.sub(r'^\s*\*\s*Stephenson:.*$', '', content, flags=re.MULTILINE)
    content = re.sub(r'^\s*\*\s*\[.*$', '', content, flags=re.MULTILINE)
    # Remove "See:" lines with broken links
    content = re.sub(r'^See:?\s*$', '', content, flags=re.MULTILINE)
    # Collapse multiple newlines
    content = re.sub(r'\n{3,}', '\n\n', content)
    return content.strip()


def is_low_quality(content):
    """Check if wiki content is a stub, placeholder, or low-quality."""
    cleaned = clean_wiki_content(content).lower()
    if len(cleaned) < 80:
        return True

    # Check for stub language anywhere in first 300 chars
    start = cleaned[:300]
    if any(phrase in start for phrase in STUB_PHRASES):
        return True

    # Check for wiki cruft
    if any(phrase in cleaned[:500] for phrase in CRUFT_PHRASES):
        # Only reject if the actual content after cruft is thin
        # Strip out the cruft sections and check what's left
        useful = cleaned
        for phrase in CRUFT_PHRASES:
            useful = useful.replace(phrase, '')
        useful = useful.strip()
        if len(useful) < 100:
            return True

    # If mostly questions with no answers
    sentences = [s.strip() for s in cleaned.split('.') if len(s.strip()) > 10]
    if sentences:
        questions = sum(1 for s in sentences if '?' in s)
        if questions >= len(sentences) * 0.6:
            return True

    return False


def get_api_key():
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        token_path = Path.home() / ".config" / "open-router.token"
        if token_path.exists():
            key = token_path.read_text().strip()
    return key


def filter_with_llm(annotations, api_key, model="google/gemini-3-flash-preview"):
    """Send annotations to Gemini to filter for quality.
    Returns set of indices to keep."""
    # Build the prompt with all annotations numbered
    items = []
    for i, ann in enumerate(annotations):
        cleaned = clean_wiki_content(ann['content'])[:400]
        author = ann['author']
        marker = 'STEPHENSON' if author == 'stephenson' else f'COMMUNITY ({author})'
        items.append(f"{i}. [{marker}] p.{ann['page']} \"{ann['desc'].replace('-', ' ')}\"\n   {cleaned}")

    prompt = f"""You are filtering wiki annotations for a reading companion to Neal Stephenson's novel "Quicksilver."

For each annotation below, reply with KEEP or SKIP and a brief reason.

KEEP if the annotation:
- Reveals something genuinely interesting (historical fact, Stephenson explaining what's real vs fictional, surprising context)
- Adds real value for a reader following along with the novel
- Contains a specific, concrete insight (not vague hand-waving)

SKIP if the annotation:
- Is a stub/placeholder ("This is a page for...", "TBA", etc.)
- Is just a question with no answer
- Repeats obvious information already covered by standard annotations
- Is mostly wiki cruft (links to other pages, section headers, no real content)
- Is speculation without substance
- Is a joke or off-topic tangent

NOTE: Stephenson's own annotations should almost always be KEPT — he's the author explaining his own work. Only SKIP if truly empty.

Reply in this exact format, one per line:
0: KEEP - reason
1: SKIP - reason
...

ANNOTATIONS:
{chr(10).join(items)}"""

    resp = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "max_tokens": 4000,
        },
        timeout=120,
    )
    resp.raise_for_status()
    result = resp.json()["choices"][0]["message"]["content"]

    # Parse response
    keep_indices = set()
    for line in result.strip().split('\n'):
        m = re.match(r'(\d+):\s*(KEEP|SKIP)\s*-?\s*(.*)', line)
        if m:
            idx = int(m.group(1))
            verdict = m.group(2)
            reason = m.group(3)
            if verdict == 'KEEP':
                keep_indices.add(idx)
            if verdict == 'SKIP':
                print(f"    SKIP [{idx}] {reason[:60]}")

    return keep_indices


def format_entry(desc, content, author):
    """Format a wiki annotation as a reading companion entry."""
    desc_display = desc.replace('-', ' ').strip('. ')
    if desc_display.startswith('...'):
        desc_display = desc_display[3:].strip()

    cleaned = clean_wiki_content(content)

    # Trim to reasonable length
    if len(cleaned) > 600:
        cut = cleaned[:600].rfind('.')
        if cut > 300:
            cleaned = cleaned[:cut + 1]
        else:
            cleaned = cleaned[:600] + '...'

    if author == 'stephenson':
        return f'**"{desc_display}"** — Stephenson\'s annotation: "{cleaned}"'
    else:
        return f'**"{desc_display}"** — From the original wiki ({author}): "{cleaned}"'


def get_chapter_page(page, chapter_pages):
    """Find which chapter a page belongs to."""
    candidates = [p for p in chapter_pages if p <= page]
    return max(candidates) if candidates else None


def load_wiki_annotations(book_num):
    """Load all wiki annotations for a book, both Stephenson and community."""
    chapters = json.load(open(CHAPTERS_JSON))
    book_chapters = [ch for ch in chapters if ch["book"] == book_num]
    page_min = min(ch["page"] for ch in book_chapters)
    page_max = max(ch["page"] for ch in book_chapters) + 50

    annotations = []
    for f in WIKI_DIR.glob('stephenson-neal-quicksilver-*'):
        if f.name == 'index.md':
            continue
        m = re.match(r'stephenson-neal-quicksilver-(\d+)-(.+)\.md', f.name)
        if not m:
            continue
        page = int(m.group(1))
        if not (page_min <= page <= page_max):
            continue

        rest = m.group(2)
        content = f.read_text()

        # Determine author
        is_stephenson = f.name.endswith('neal-stephenson.md')
        if is_stephenson:
            author = 'stephenson'
            desc = re.sub(r'-neal-stephenson$', '', rest)
        else:
            # Extract author from end of filename
            # Pattern: description-author-name  (but author is usually last 1-2 parts)
            # Most filenames end with known author patterns
            author_match = re.search(r'-(?:alan-sinder|gary-thompson|jeremy-bornstein|'
                                     r'chris-swingley|edward-vielmetti|dennis-traub|'
                                     r'scott-elkin|bill-seitz|brett-kuehner|'
                                     r't-whalen|andux|ghash|jonnay|armaced|lotzmana|'
                                     r'sorenson|simon|professorbikeybike|trismegis2|'
                                     r'mike-lorrey|john-b|patrick-tufts|'
                                     r'jere7my-tho-rpe|eric-s-raymond|professor-bikey-bike|'
                                     r'richard-comstock|rpe)$', rest)
            if author_match:
                author = author_match.group(0).lstrip('-').replace('-', ' ').title()
                desc = rest[:author_match.start()]
            else:
                # Fallback: last hyphenated segment
                parts = rest.rsplit('-', 1)
                author = parts[-1] if len(parts) > 1 else 'unknown'
                desc = parts[0] if len(parts) > 1 else rest

        # Quality filter for community annotations
        if author != 'stephenson':
            if is_low_quality(content):
                continue

        annotations.append({
            'page': page,
            'desc': desc,
            'content': content,
            'author': author,
            'filename': f.name,
        })

    annotations.sort(key=lambda x: x['page'])
    return annotations


def is_already_incorporated(annotation, chapter_content):
    """Check if a wiki annotation's content is already in the chapter file."""
    ch_lower = chapter_content.lower()

    # Check for key phrases from the description
    desc_words = [w for w in annotation['desc'].split('-') if len(w) > 4]

    # For Stephenson: check if "stephenson" appears near key words
    if annotation['author'] == 'stephenson':
        for word in desc_words[:3]:
            if word.lower() in ch_lower:
                idx = ch_lower.find(word.lower())
                nearby = ch_lower[max(0, idx - 200):idx + 200]
                if 'stephenson' in nearby:
                    return True

    # Check for distinctive phrases from the content
    cleaned = clean_wiki_content(annotation['content'])
    for phrase_len in [50, 30]:
        phrase = cleaned[:phrase_len].lower()
        if phrase and phrase in ch_lower:
            return True

    # For community: check if "original wiki" appears near key words
    if annotation['author'] != 'stephenson':
        for word in desc_words[:3]:
            if word.lower() in ch_lower:
                idx = ch_lower.find(word.lower())
                nearby = ch_lower[max(0, idx - 300):idx + 300]
                if 'original wiki' in nearby or 'community' in nearby:
                    return True

    return False


def inject_into_chapter(chapter_file, entries_to_add, chapter_start, chapter_end, dry_run=True):
    """Add new entries to a chapter annotation file at the right positions.

    Inserts each wiki annotation proportionally based on its page number
    within the chapter's page range, so entries appear in reading order.
    """
    content = chapter_file.read_text()

    parts = content.split('---', 2)
    if len(parts) < 3:
        return 0
    frontmatter = parts[1]
    body = parts[2].strip('\n')

    # Split body into opening line(s) and bolded-quote entries
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
    existing_entries = []
    current = []
    for line in entry_text:
        if (line.startswith('**"') or line.startswith('**\u201c')) and current:
            existing_entries.append('\n'.join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        existing_entries.append('\n'.join(current))

    n_existing = len(existing_entries)
    page_span = max(chapter_end - chapter_start, 1)

    # Build list of (position, entry_text) for new entries
    new_entries = []
    for ann in entries_to_add:
        entry = format_entry(ann['desc'], ann['content'], ann['author'])
        # Calculate insertion index based on page position within chapter
        fraction = (ann['page'] - chapter_start) / page_span
        insert_idx = int(fraction * n_existing)
        insert_idx = max(0, min(insert_idx, n_existing))
        new_entries.append((insert_idx, entry))

    # Sort by insertion index (stable — preserves page order for same index)
    new_entries.sort(key=lambda x: x[0])

    # Insert in reverse order so indices stay valid
    for insert_idx, entry in reversed(new_entries):
        existing_entries.insert(insert_idx, entry)

    # Rebuild body
    opening = '\n'.join(opening_lines).rstrip('\n')
    entries_text = '\n\n'.join(existing_entries)
    new_body = f"{opening}\n\n{entries_text}\n"
    new_content = f'---{frontmatter}---\n\n{new_body}'

    if not dry_run:
        chapter_file.write_text(new_content)

    return len(new_entries)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--book", type=int, required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--stephenson-only", action="store_true",
                        help="Only inject Stephenson annotations")
    parser.add_argument("--no-filter", action="store_true",
                        help="Skip LLM quality filter")
    parser.add_argument("--model", default="google/gemini-3-flash-preview")
    args = parser.parse_args()

    chapters = json.load(open(CHAPTERS_JSON))
    chapter_pages = sorted([ch["page"] for ch in chapters if ch["book"] == args.book])

    wiki_annotations = load_wiki_annotations(args.book)

    if args.stephenson_only:
        wiki_annotations = [a for a in wiki_annotations if a['author'] == 'stephenson']

    n_stephenson = sum(1 for a in wiki_annotations if a['author'] == 'stephenson')
    n_community = len(wiki_annotations) - n_stephenson
    print(f"Found {len(wiki_annotations)} wiki annotations for Book {args.book} "
          f"({n_stephenson} Stephenson, {n_community} community)")

    # Run LLM quality filter
    if not args.no_filter and not args.stephenson_only:
        api_key = get_api_key()
        if not api_key:
            print("Error: No OpenRouter API key for LLM filter", file=sys.stderr)
            sys.exit(1)

        # Filter in batches (to stay within context limits)
        print(f"\nFiltering with {args.model}...")
        keep_set = set()
        batch_size = 40
        for i in range(0, len(wiki_annotations), batch_size):
            batch = wiki_annotations[i:i + batch_size]
            print(f"  Batch {i // batch_size + 1} ({len(batch)} annotations)...")
            batch_keep = filter_with_llm(batch, api_key, args.model)
            # Map batch indices back to global indices
            for idx in batch_keep:
                keep_set.add(i + idx)
            if i + batch_size < len(wiki_annotations):
                time.sleep(1)

        before = len(wiki_annotations)
        wiki_annotations = [a for i, a in enumerate(wiki_annotations) if i in keep_set]
        print(f"\nLLM filter: {before} -> {len(wiki_annotations)} "
              f"({before - len(wiki_annotations)} removed)")

    total_added = 0
    total_skipped = 0

    # Group by chapter
    by_chapter = {}
    for ann in wiki_annotations:
        ch_page = get_chapter_page(ann['page'], chapter_pages)
        if ch_page is None:
            continue
        by_chapter.setdefault(ch_page, []).append(ann)

    for ch_page in sorted(by_chapter.keys()):
        ch_file = ANNOTATIONS_DIR / f"chapter-{ch_page:04d}.md"
        if not ch_file.exists():
            print(f"  SKIP p.{ch_page}: no annotation file")
            continue

        # Compute chapter end page (start of next chapter, or +50 for last chapter)
        ch_idx = chapter_pages.index(ch_page)
        chapter_end = chapter_pages[ch_idx + 1] if ch_idx + 1 < len(chapter_pages) else ch_page + 50

        ch_content = ch_file.read_text()
        to_add = []
        skipped = []

        for ann in by_chapter[ch_page]:
            if is_already_incorporated(ann, ch_content):
                skipped.append(ann)
            else:
                to_add.append(ann)

        if to_add:
            n = inject_into_chapter(ch_file, to_add, ch_page, chapter_end, dry_run=args.dry_run)
            total_added += n
            print(f"  p.{ch_page}: +{n} entries ({len(skipped)} already present)")
            for ann in to_add:
                desc = ann['desc'].replace('-', ' ')[:50]
                marker = 'S' if ann['author'] == 'stephenson' else 'C'
                print(f"    + [{marker}] p.{ann['page']} {desc}")
        elif skipped:
            total_skipped += len(skipped)

    print(f"\n{'DRY RUN - ' if args.dry_run else ''}Summary:")
    print(f"  Added: {total_added}")
    print(f"  Already present: {total_skipped}")


if __name__ == "__main__":
    main()
