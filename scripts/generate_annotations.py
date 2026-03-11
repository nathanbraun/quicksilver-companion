#!/usr/bin/env python3
"""
Generate narrative, quote-anchored annotation pages for each chapter.

Pipeline:
1. Load chapters.json for chapter metadata
2. Load epub text for each chapter
3. Load scan findings (with quotes) for each chapter
4. Load original wiki annotations for pages in each chapter's range
5. Sort findings by position in epub text
6. Send to LLM to write narrative annotation
7. Output markdown files to src/content/annotations/

Usage:
    # List chapters and what data we have for each:
    python generate_annotations.py --list

    # Generate a single chapter:
    python generate_annotations.py --chapter 3

    # Generate all chapters:
    python generate_annotations.py --all

    # Dry run (show what would be sent to LLM):
    python generate_annotations.py --chapter 3 --dry-run

    # Use a specific model:
    python generate_annotations.py --chapter 3 --model google/gemini-3-flash-preview
"""

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

import requests
import yaml
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
import ebooklib
from ebooklib import epub
import warnings
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

PROJECT_ROOT = Path(__file__).parent.parent
CHAPTERS_JSON = PROJECT_ROOT / "src" / "data" / "chapters.json"
ANNOTATIONS_DIR = PROJECT_ROOT / "src" / "content" / "annotations"
TOPICS_DIR = PROJECT_ROOT / "src" / "content" / "topics"
WIKI_ANNOTATIONS_DIR = PROJECT_ROOT.parent / "quicksilver-wiki-original" / "docs" / "quicksilver" / "annotations"
REPORTS_DIR = PROJECT_ROOT / "scripts" / "link_reports"
EPUB_PATH = Path.home() / "Calibre Library" / "Neal Stephenson" / \
    "Quicksilver_ The Baroque Cycle #1 (88)" / \
    "Quicksilver_ The Baroque Cycle #1 - Neal Stephenson.epub"

# Same aliases as scan_chapter.py
SLUG_ALIASES = {
    "christian-huygens": "christiaan-huygens",
    "pieces-of-eight": "piece-of-eight",
    "leviathan-hobbes": "leviathan",
    "leviathan-thomas-hobbes": "leviathan",
    "archbishop-laud": "william-laud",
    "sophie-of-hanover": "sophia-of-the-palatinate",
    "electress-sophie-of-hanover": "sophia-of-the-palatinate",
    "electress-sophia-of-hanover": "sophia-of-the-palatinate",
    "electress-sophia": "sophia-of-the-palatinate",
    "sophia-of-hanover": "sophia-of-the-palatinate",
    "harvard-university": "harvard-college",
    "harvard-university-history": "harvard-college",
    "carlos-ii-of-spain": "charles-ii-of-spain",
    "act-of-uniformity": "act-of-uniformity-1662",
    "declaration-of-uniformity": "act-of-uniformity-1662",
    "plato-and-aristotle": "aristotle",
    "cartesian-geometry": "rene-descartes",
    "principia-philosophiae": "rene-descartes",
    "cryptonomicon-wilkins": "john-wilkins",
    "duke-of-york": "james-ii",
    "english-interregnum": "the-interregnum",
    "aristotelian-physics": "aristotle",
    "aristotelian-curriculum": "aristotle",
    "john-churchill-duke-of-marlborough": "john-churchill",
    "duke-of-marlborough": "john-churchill",
    "john-churchill-marlborough": "john-churchill",
    "elizabeth-stuart-winter-queen": "the-winter-queen",
    "elizabeth-stuart": "the-winter-queen",
    "liselotte-palatine": "elizabeth-charlotte-madame-palatine",
    "liselotte-duchess-of-orleans": "elizabeth-charlotte-madame-palatine",
    "comte-d-avaux": "comte-davaux",
    "nicaise-le-febvre": "nicaise-le-febure",
    "philippe-duke-of-orleans": "philippe-i-duke-of-orleans",
    "sir-winston-churchill-17th-century": "sir-winston-churchill-cavalier",
    "sir-winston-churchill-elder": "sir-winston-churchill-cavalier",
    "sir-winston-churchill-ancestor": "sir-winston-churchill-cavalier",
    "barbara-palmer": "lady-castlemaine",
    "william-of-orange": "william-iii-of-orange",
    "william-and-mary": "william-iii-of-orange",
    "duchess-of-portsmouth": "louise-de-keroualle",
    "nicolas-fatio-de-duilliers": "nicolas-fatio-de-duillier",
    "antoine-rossignol": "bonaventure-rossignol",
    "hugh-peter": "hugh-peters",
    "comenius": "john-amos-comenius",
    "copernican-heliocentrism": "copernicus",
    "sophia-charlotte-of-hanover": "sophia-charlotte",
    "eleanor-erdmuthe-of-saxe-eisenach": "eleanor-erdmuthe",
    "charles-i-louis-elector-palatine": "charles-louis-elector-palatine",
    "henrietta-anne-minette": "henrietta-anne-stuart",
    "the-glorious-revolution": "glorious-revolution",
    "the-english-civil-war": "english-civil-war",
    "execution-of-charles-i": "english-civil-war",
    "the-royal-society": "royal-society",
    "the-plague": "plague",
    "the-restoration": "charles-ii",
    "duke-of-monmouth": "monmouth",
    "the-calculus-priority-dispute": "calculus-priority-dispute",
}


def get_api_key():
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        token_path = Path.home() / ".config" / "open-router.token"
        if token_path.exists():
            key = token_path.read_text().strip()
    return key


def normalize_slug(slug):
    if not slug or slug == "None":
        return None
    return SLUG_ALIASES.get(slug, slug)


def slugify(text):
    text = text.lower().strip()
    text = re.sub(r'[^a-z0-9\s-]', '', text)
    text = re.sub(r'[\s]+', '-', text)
    text = re.sub(r'-+', '-', text)
    return text.strip('-')


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
    """Map chapters.json page -> epub text by sequential alignment.

    The epub chapters and chapters.json entries are in the same reading order,
    so we can zip them. One exception: p.829 'Journal Entry' is embedded inside
    the preceding Rossignol epub chapter, so it doesn't get its own epub chapter.
    """
    page_to_text = {}
    epub_idx = 0
    for cj in chapters_json:
        page = cj['page']
        if page == 829:
            # Journal Entry is embedded in the previous Rossignol epub chapter
            page_to_text[page] = page_to_text.get(821, '')
            continue
        if epub_idx < len(epub_chapters):
            page_to_text[page] = epub_chapters[epub_idx]['text']
            epub_idx += 1
    return page_to_text


def load_scan_findings():
    """Load all scan findings from all three books, normalize slugs."""
    all_findings = []
    for book_num in [1, 2, 3]:
        path = REPORTS_DIR / f"scan-book{book_num}-clean-clean.json"
        if not path.exists():
            continue
        data = json.load(open(path))
        for f in data:
            slug = f.get("suggested_slug") or f.get("existing_topic")
            if not slug or slug == "None":
                slug = slugify(f.get("subject", ""))
            slug = normalize_slug(slug) or slug
            f["normalized_slug"] = slug
            f["book"] = book_num
        all_findings.extend(data)
    return all_findings


def load_wiki_annotations(page_start, page_end):
    """Load original wiki annotations for pages in range [page_start, page_end)."""
    if not WIKI_ANNOTATIONS_DIR.exists():
        return []

    annotations = []
    for f in WIKI_ANNOTATIONS_DIR.glob("stephenson-neal-quicksilver-*"):
        m = re.match(r'stephenson-neal-quicksilver-(\d+)-(.+)\.md', f.name)
        if not m:
            continue
        page = int(m.group(1))
        if page_start <= page < page_end:
            content = f.read_text()
            # Strip the metaweb header
            content = re.sub(r'^#.*\n\nFrom the Quicksilver Metaweb\.?\n*', '', content)
            content = content.strip()
            # Extract author from filename
            parts = m.group(2).rsplit('-', 1)
            author = parts[-1] if len(parts) > 1 else "unknown"
            # Extract description/quote from filename
            desc = parts[0] if len(parts) > 1 else m.group(2)
            desc = desc.replace('-', ' ')

            annotations.append({
                "page": page,
                "description": desc,
                "author": author,
                "content": content,
                "filename": f.name,
            })

    annotations.sort(key=lambda x: (x["page"], x["description"]))
    return annotations


def find_findings_in_text(findings, epub_text):
    """Find which findings have quotes that appear in this chapter's epub text.
    Returns findings sorted by position, excluding ones not found."""
    if not epub_text:
        return []

    text_lower = epub_text.lower()
    positioned = []
    seen_slugs = set()

    for f in findings:
        quote = f.get("quote", "")
        slug = f.get("normalized_slug", "")
        if not quote:
            continue

        # Deduplicate: keep first occurrence of each slug per chapter
        if slug in seen_slugs:
            continue

        # Try to find the quote in this chapter's text
        search = quote.lower()[:40]
        pos = text_lower.find(search)
        if pos == -1 and len(search) > 20:
            pos = text_lower.find(search[:20])

        # Only include findings whose quotes actually appear in this chapter
        if pos == -1:
            continue

        positioned.append({
            "pos": pos,
            "quote": quote,
            "subject": f.get("subject", ""),
            "note": f.get("note", ""),
            "slug": slug,
            "type": f.get("type", ""),
            "existing_topic": f.get("existing_topic"),
            "chunk": f.get("chunk", 0),
        })
        seen_slugs.add(slug)

    positioned.sort(key=lambda x: (x["pos"], x["chunk"]))
    return positioned


def build_llm_prompt(chapter_meta, positioned_findings, wiki_annotations, epub_text_excerpt):
    """Build the prompt for the LLM to write the narrative annotation."""

    # Format positioned findings
    findings_text = ""
    for i, f in enumerate(positioned_findings):
        findings_text += f'\n{i+1}. Quote: "{f["quote"]}"\n'
        findings_text += f'   Subject: {f["subject"]} (slug: {f["slug"]})\n'
        findings_text += f'   Type: {f["type"]}\n'
        if f["note"]:
            findings_text += f'   Context: {f["note"]}\n'

    # Format wiki annotations
    wiki_text = ""
    if wiki_annotations:
        wiki_text = "\n\nORIGINAL WIKI ANNOTATIONS (from the Metaweb):\n"
        for wa in wiki_annotations:
            author_label = "Stephenson" if wa["author"] in ("neal-stephenson", "neal stephenson") else wa["author"]
            wiki_text += f'\n--- Page {wa["page"]}: "{wa["description"]}" (by {author_label}) ---\n'
            # Truncate very long annotations
            content = wa["content"]
            if len(content) > 800:
                content = content[:800] + "..."
            wiki_text += content + "\n"

    system_prompt = """You are writing a reading companion for Neal Stephenson's novel "Quicksilver." Your job is to write an annotation page for one chapter that a reader can follow alongside the book.

FORMAT:
- Start with one plain sentence setting the scene (location, date, what's happening)
- Then a series of entries, each anchored to a BOLDED QUOTE from the novel
- Each entry format: **"quote from novel"** — your annotation (1-3 sentences)
- Use inline links: [Topic Name](/topic/slug-name) for the FIRST mention of each topic
- Order entries by where they appear in the chapter (the findings are pre-sorted for you)

CONTENT GUIDELINES:
- Focus on historical/scientific context a reader needs, not plot summary
- Lead with the most interesting or surprising fact
- When a Stephenson annotation exists from the original wiki, quote or paraphrase it and attribute: 'Stephenson's annotation: ...'
- When a community wiki annotation adds genuine insight, incorporate it naturally
- Be selective: skip findings that are trivial, redundant, or don't add real value for the reader
- Don't annotate every single finding — aim for the 15-30 most useful ones per chapter
- For very long chapters, you can group related nearby references into a single entry
- No AI filler phrases ("It's worth noting...", "Interestingly...", "This is significant because...")
- Keep it concise and direct

DO NOT include any frontmatter or YAML header — just the markdown body content starting with the opening sentence."""

    user_prompt = f"""Write the annotation for this chapter:

CHAPTER: {chapter_meta["location"]}
BOOK: {chapter_meta["book"]} ({chapter_meta["book_title"]})
DATE: {chapter_meta["date"]}
PAGES: starts at p.{chapter_meta["page"]}

FINDINGS (sorted by position in text):
{findings_text}
{wiki_text}

FIRST ~2000 CHARS OF CHAPTER TEXT (for context on the scene):
{epub_text_excerpt[:2000]}

Write the annotation now. Remember: bold quotes, inline topic links, historical context, no frontmatter."""

    return system_prompt, user_prompt


def call_llm(system_prompt, user_prompt, model, api_key, retries=3):
    """Call the LLM via OpenRouter with retry on transient errors."""
    import time
    for attempt in range(retries):
        try:
            resp = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.3,
                    "max_tokens": 4000,
                },
                timeout=120,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]
        except (requests.exceptions.ChunkedEncodingError,
                requests.exceptions.ConnectionError,
                requests.exceptions.Timeout) as e:
            if attempt < retries - 1:
                wait = 5 * (attempt + 1)
                print(f"    Retry {attempt+1}/{retries} after error: {e.__class__.__name__} (waiting {wait}s)")
                time.sleep(wait)
            else:
                raise


def build_frontmatter(chapter_meta, positioned_findings, wiki_annotations, existing_topics):
    """Build the YAML frontmatter for the annotation file."""
    # Collect characters and topics from findings
    fm_characters = []
    fm_topics = []
    for f in positioned_findings:
        slug = f["slug"]
        if slug in existing_topics:
            fm_topics.append(slug)

    # Deduplicate and limit
    fm_topics = list(dict.fromkeys(fm_topics))[:10]

    # Collect original authors from wiki annotations
    authors = []
    for wa in wiki_annotations:
        author = wa["author"]
        if author not in authors:
            authors.append(author)

    fm = {
        "title": chapter_meta["location"],
        "page": chapter_meta["page"],
        "book": chapter_meta["book"],
        "book_title": chapter_meta["book_title"],
        "chapter_start_page": chapter_meta["page"],
        "chapter_location": chapter_meta["location"],
        "chapter_date": chapter_meta["date"],
        "characters": [],
        "topics": fm_topics,
        "original_authors": authors,
    }
    return fm


def generate_chapter(chapter_meta, all_chapters_json, scan_findings, epub_page_map,
                     existing_topics, model, api_key, dry_run=False):
    """Generate an annotation page for one chapter."""
    book_num = chapter_meta["book"]
    page = chapter_meta["page"]
    location = chapter_meta["location"]

    # Determine page range for this chapter
    book_chapters = sorted(
        [ch for ch in all_chapters_json if ch["book"] == book_num],
        key=lambda x: x["page"]
    )
    ch_idx = next(i for i, ch in enumerate(book_chapters) if ch["page"] == page)
    page_start = page
    page_end = book_chapters[ch_idx + 1]["page"] if ch_idx + 1 < len(book_chapters) else page + 50

    # Get epub text for this chapter (from sequential alignment)
    epub_text = epub_page_map.get(page, "")

    # Find findings by quote matching: try ALL findings from this book
    # against this chapter's epub text. Only findings whose quotes appear
    # in the text will be included.
    book_findings = [f for f in scan_findings if f["book"] == book_num]
    positioned = find_findings_in_text(book_findings, epub_text)

    # Load wiki annotations for this page range
    wiki_annotations = load_wiki_annotations(page_start, page_end)

    print(f"  Chapter p.{page} '{location}':")
    print(f"    Findings matched by quote: {len(positioned)}")
    print(f"    Wiki annotations: {len(wiki_annotations)}")
    print(f"    Epub text: {len(epub_text)} chars")

    if not positioned and not wiki_annotations:
        print(f"    SKIP: no data")
        return None

    # Build LLM prompt
    system_prompt, user_prompt = build_llm_prompt(
        chapter_meta, positioned, wiki_annotations,
        epub_text[:2000] if epub_text else "(no epub text available)"
    )

    if dry_run:
        print(f"\n--- DRY RUN: System prompt ({len(system_prompt)} chars) ---")
        print(system_prompt[:500])
        print(f"\n--- DRY RUN: User prompt ({len(user_prompt)} chars) ---")
        print(user_prompt[:2000])
        print("...")
        return None

    # Call LLM
    print(f"    Calling {model}...")
    body = call_llm(system_prompt, user_prompt, model, api_key)

    # Strip any markdown fences the model might add
    body = re.sub(r'^```(?:markdown|md)?\s*\n', '', body)
    body = re.sub(r'\n```\s*$', '', body)

    # Build frontmatter
    fm = build_frontmatter(chapter_meta, positioned, wiki_annotations, existing_topics)

    # Combine
    fm_yaml = yaml.dump(fm, default_flow_style=None, allow_unicode=True, sort_keys=False)
    content = f"---\n{fm_yaml}---\n\n{body}\n"

    # Write file
    filename = f"chapter-{page:04d}.md"
    outpath = ANNOTATIONS_DIR / filename
    outpath.write_text(content)
    print(f"    Wrote {filename}")

    return outpath


def main():
    parser = argparse.ArgumentParser(description="Generate narrative annotation pages")
    parser.add_argument("--list", action="store_true", help="List chapters and data availability")
    parser.add_argument("--chapter", type=int, help="Generate for chapter starting at this page number")
    parser.add_argument("--book", type=int, help="Generate for all chapters in a book")
    parser.add_argument("--all", action="store_true", help="Generate all chapters")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be sent to LLM")
    parser.add_argument("--skip-existing", action="store_true", help="Skip chapters that already have annotation files")
    parser.add_argument("--model", default="google/gemini-3-flash-preview")
    args = parser.parse_args()

    chapters = json.load(open(CHAPTERS_JSON))
    existing_topics = set(f.stem for f in TOPICS_DIR.glob("*.md"))

    # Load epub and build page map (needed for both --list and generation)
    print("Loading epub...")
    epub_chapters = load_epub_chapters()
    epub_page_map = build_epub_page_map(epub_chapters, chapters)
    print("Loading scan findings...")
    scan_findings = load_scan_findings()

    if args.list:
        for ch in chapters:
            book_chapters = [c for c in chapters if c["book"] == ch["book"]]
            ch_idx = next(i for i, c in enumerate(book_chapters) if c["page"] == ch["page"])
            page_end = book_chapters[ch_idx + 1]["page"] if ch_idx + 1 < len(book_chapters) else ch["page"] + 50

            epub_text = epub_page_map.get(ch["page"], "")
            book_findings = [f for f in scan_findings if f["book"] == ch["book"]]
            n_findings = len(find_findings_in_text(book_findings, epub_text))
            n_wiki = len(load_wiki_annotations(ch["page"], page_end))

            status = "OK" if n_findings > 0 or n_wiki > 0 else "EMPTY"
            print(f"  [{status:5}] Book {ch['book']} p.{ch['page']:>4}: {ch['location']:<50} "
                  f"({n_findings} scan, {n_wiki} wiki)")
        return

    api_key = get_api_key()
    if not api_key and not args.dry_run:
        print("Error: No OpenRouter API key found", file=sys.stderr)
        sys.exit(1)

    print(f"Existing topics: {len(existing_topics)}")

    # Select chapters to generate
    if args.chapter:
        targets = [ch for ch in chapters if ch["page"] == args.chapter]
        if not targets:
            print(f"No chapter found starting at page {args.chapter}")
            sys.exit(1)
    elif args.book:
        targets = [ch for ch in chapters if ch["book"] == args.book]
    elif args.all:
        targets = chapters
    else:
        parser.print_help()
        sys.exit(1)

    if args.skip_existing:
        targets = [ch for ch in targets
                   if not (ANNOTATIONS_DIR / f"chapter-{ch['page']:04d}.md").exists()]

    print(f"\nGenerating {len(targets)} chapter(s)...\n")

    for ch in targets:
        generate_chapter(ch, chapters, scan_findings, epub_page_map,
                        existing_topics, args.model, api_key, args.dry_run)
        print()


if __name__ == "__main__":
    main()
