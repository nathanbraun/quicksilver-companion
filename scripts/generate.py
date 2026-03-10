"""Generate enhanced wiki content using Claude API.

Usage:
    python3 scripts/generate.py [--annotations] [--characters] [--dry-run] [--page PAGE] [--limit N]

Examples:
    python3 scripts/generate.py --annotations --limit 5     # First 5 annotated pages
    python3 scripts/generate.py --annotations --page 3       # Just page 3
    python3 scripts/generate.py --characters --limit 5       # First 5 characters
    python3 scripts/generate.py --dry-run --annotations      # Show what would be generated
"""

import argparse
import hashlib
import json
import os
import sys
import time
import yaml
from pathlib import Path

import anthropic

from parse_source import catalog_all_files, get_annotations, group_annotations_by_page, SourceFile
from chapter_data import get_chapter_for_page, BOOK_NAMES

SCRIPTS_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPTS_DIR.parent
CONTENT_DIR = PROJECT_DIR / "src" / "content"
CACHE_DIR = SCRIPTS_DIR / ".cache"
PROMPTS_DIR = SCRIPTS_DIR / "prompts"

# Major characters to generate profiles for
MAJOR_CHARACTERS = [
    "stephenson-neal-quicksilver-daniel-waterhouse",
    "stephenson-neal-quicksilver-jack-shaftoe",
    "stephenson-neal-quicksilver-eliza",
    "stephenson-neal-quicksilver-enoch-root",
    "isaac-newton",
    "gottfried-wilhelm-von-leibniz",
    "robert-hooke",
    "john-wilkins",
    "christiaan-huygens",
    "nicolas-fatio-de-duillier",
    "samuel-pepys",
    "charles-ii",
    "james-ii-of-england",
    "william-of-orange",
    "monmouth",
    "louis-anglesey-earl-of-upnor",
    "judge-jeffreys",
    "bonaventure-rossignol",
    "stephenson-neal-quicksilver-roger-comstock",
    "stephenson-neal-quicksilver-drake-waterhouse",
    "stephenson-neal-quicksilver-bob-shaftoe",
    "stephenson-neal-quicksilver-captain-van-hoek",
    "stephenson-neal-quicksilver-d-arcachon",
    "stephenson-neal-quicksilver-d-avaux",
    "stephenson-neal-quicksilver-knott-bolstrood",
    "stephenson-neal-quicksilver-gomer-bolstrood",
    "caroline-of-ansbach",
    "sophia-of-hanover",
    "dappa",
    "john-locke",
    "christopher-wren",
    "robert-boyle",
    "benjamin-franklin",
]


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.txt").read_text()


def get_cache_path(key: str) -> Path:
    CACHE_DIR.mkdir(exist_ok=True)
    h = hashlib.md5(key.encode()).hexdigest()
    return CACHE_DIR / f"{h}.json"


def get_cached(key: str) -> str | None:
    path = get_cache_path(key)
    if path.exists():
        data = json.loads(path.read_text())
        return data.get("content")
    return None


def set_cache(key: str, content: str):
    path = get_cache_path(key)
    path.write_text(json.dumps({"key": key, "content": content}))


def call_claude(prompt: str, cache_key: str, dry_run: bool = False) -> str:
    """Call Claude API with caching."""
    cached = get_cached(cache_key)
    if cached:
        return cached

    if dry_run:
        return "[DRY RUN — would call Claude API here]"

    client = anthropic.Anthropic()
    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    content = message.content[0].text
    set_cache(cache_key, content)

    # Rate limiting
    time.sleep(0.5)
    return content


def get_related_context(source_file: SourceFile, catalog: dict[str, SourceFile], max_chars: int = 3000) -> str:
    """Gather related content from linked pages."""
    context_parts = []
    seen = set()
    total_chars = 0

    for link_target in source_file.outbound_links:
        if link_target in seen or link_target == source_file.slug:
            continue
        seen.add(link_target)

        linked = catalog.get(link_target)
        if not linked:
            continue

        # Truncate long entries
        excerpt = linked.content[:800]
        if len(linked.content) > 800:
            excerpt += "..."

        part = f"### {linked.title}\n{excerpt}\n"
        if total_chars + len(part) > max_chars:
            break
        context_parts.append(part)
        total_chars += len(part)

    return "\n".join(context_parts) if context_parts else "(No related context available)"


def generate_annotation_page(
    page: int,
    page_annotations: list[SourceFile],
    catalog: dict[str, SourceFile],
    dry_run: bool = False,
) -> None:
    """Generate an enhanced annotation for a single page."""
    chapter = get_chapter_for_page(page)
    if not chapter:
        print(f"  WARNING: No chapter found for page {page}, skipping")
        return

    # Combine all original annotations for this page
    original_parts = []
    all_authors = []
    for ann in page_annotations:
        author_label = ", ".join(ann.original_authors) if ann.original_authors else "community"
        original_parts.append(f"--- Annotation by {author_label} ---\n{ann.content}")
        all_authors.extend(ann.original_authors)

    original_content = "\n\n".join(original_parts)

    # Gather related context from linked pages
    all_links = []
    for ann in page_annotations:
        all_links.extend(ann.outbound_links)
    # Create a temporary SourceFile to use get_related_context
    combined = SourceFile(
        path=page_annotations[0].path,
        filename="",
        file_type="annotation",
        content="",
        outbound_links=list(dict.fromkeys(all_links)),  # dedupe preserving order
    )
    related_context = get_related_context(combined, catalog)

    # Build prompt
    prompt_template = load_prompt("annotation")
    prompt = prompt_template.format(
        page=page,
        chapter_start_page=chapter["page"],
        chapter_location=chapter["location"],
        chapter_date=chapter["date"],
        book_title=chapter["book_title"],
        book=chapter["book"],
        original_content=original_content,
        related_context=related_context,
    )

    cache_key = f"annotation-{page}-{hashlib.md5(original_content.encode()).hexdigest()[:8]}"
    content = call_claude(prompt, cache_key, dry_run=dry_run)

    # Build slug from first annotation's description
    first_desc = page_annotations[0].title.lower().replace(" ", "-")
    slug = f"{page}-{first_desc}" if first_desc else str(page)

    # Write output
    frontmatter = {
        "title": f"Page {page}",
        "page": page,
        "book": chapter["book"],
        "book_title": chapter["book_title"],
        "chapter_start_page": chapter["page"],
        "chapter_location": chapter["location"],
        "chapter_date": chapter["date"],
        "characters": [],  # TODO: extract from chapter data
        "topics": [],
        "original_authors": list(dict.fromkeys(a for a in all_authors if a)),
    }

    output_path = CONTENT_DIR / "annotations" / f"page-{page:04d}.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w") as f:
        f.write("---\n")
        yaml.dump(frontmatter, f, default_flow_style=False, allow_unicode=True)
        f.write("---\n\n")
        f.write(content)
        f.write("\n")

    print(f"  -> {output_path.relative_to(PROJECT_DIR)}")


def generate_character_page(
    slug: str,
    catalog: dict[str, SourceFile],
    dry_run: bool = False,
) -> None:
    """Generate an enhanced character profile."""
    source = catalog.get(slug)
    if not source:
        print(f"  WARNING: Source not found for {slug}")
        return

    # Determine character type
    is_historical = slug in [
        "isaac-newton", "gottfried-wilhelm-von-leibniz", "robert-hooke",
        "john-wilkins", "christiaan-huygens", "nicolas-fatio-de-duillier",
        "samuel-pepys", "charles-ii", "james-ii-of-england", "william-of-orange",
        "monmouth", "judge-jeffreys", "bonaventure-rossignol", "caroline-of-ansbach",
        "sophia-of-hanover", "john-locke", "christopher-wren", "robert-boyle",
        "benjamin-franklin",
    ]
    character_type = "historical" if is_historical else "fictional"

    # Gather related content from linked pages
    related_context = get_related_context(source, catalog, max_chars=4000)

    # Also look for authored sub-entries (e.g., daniel-waterhouse-alan-sinder)
    base_name = slug.replace("stephenson-neal-quicksilver-", "")
    sub_entries = []
    for cat_slug, sf in catalog.items():
        if cat_slug.startswith(base_name + "-") and cat_slug != slug:
            sub_entries.append(f"### {sf.title}\n{sf.content[:1000]}")
    if sub_entries:
        related_context += "\n\nAUTHORED SUB-ENTRIES:\n" + "\n\n".join(sub_entries[:3])

    # Build prompt
    prompt_template = load_prompt("character")
    character_name = source.title
    prompt = prompt_template.format(
        character_name=character_name,
        character_type=f"{'Real historical figure' if is_historical else 'Fictional character'}",
        original_content=source.content[:6000],
        related_content=related_context,
    )

    cache_key = f"character-{slug}-{hashlib.md5(source.content.encode()).hexdigest()[:8]}"
    content = call_claude(prompt, cache_key, dry_run=dry_run)

    # Build frontmatter
    char_slug = slug.replace("stephenson-neal-quicksilver-", "")
    frontmatter = {
        "title": character_name,
        "type": character_type,
        "category": "major",
        "books": [1],
        "related_characters": [],
    }

    output_path = CONTENT_DIR / "characters" / f"{char_slug}.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w") as f:
        f.write("---\n")
        yaml.dump(frontmatter, f, default_flow_style=False, allow_unicode=True)
        f.write("---\n\n")
        f.write(content)
        f.write("\n")

    print(f"  -> {output_path.relative_to(PROJECT_DIR)}")


def main():
    parser = argparse.ArgumentParser(description="Generate enhanced Quicksilver wiki content")
    parser.add_argument("--annotations", action="store_true", help="Generate annotation pages")
    parser.add_argument("--characters", action="store_true", help="Generate character pages")
    parser.add_argument("--dry-run", action="store_true", help="Don't call API, show what would be generated")
    parser.add_argument("--page", type=int, help="Generate only this page number")
    parser.add_argument("--limit", type=int, help="Limit number of pages to generate")
    args = parser.parse_args()

    if not args.annotations and not args.characters:
        parser.print_help()
        sys.exit(1)

    print("Parsing source files...")
    catalog = catalog_all_files()
    print(f"Cataloged {len(catalog)} source files")

    if args.annotations:
        annotations = get_annotations(catalog)
        by_page = group_annotations_by_page(annotations)
        pages = sorted(by_page.keys())

        if args.page:
            pages = [p for p in pages if p == args.page]
        if args.limit:
            pages = pages[:args.limit]

        print(f"\nGenerating {len(pages)} annotation pages...")
        for i, page in enumerate(pages):
            print(f"[{i+1}/{len(pages)}] Page {page} ({len(by_page[page])} annotations)")
            generate_annotation_page(page, by_page[page], catalog, dry_run=args.dry_run)

    if args.characters:
        chars = MAJOR_CHARACTERS
        if args.limit:
            chars = chars[:args.limit]

        print(f"\nGenerating {len(chars)} character pages...")
        for i, slug in enumerate(chars):
            print(f"[{i+1}/{len(chars)}] {slug}")
            generate_character_page(slug, catalog, dry_run=args.dry_run)

    print("\nDone!")


if __name__ == "__main__":
    main()
