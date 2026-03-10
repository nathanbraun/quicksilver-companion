#!/usr/bin/env python3
"""
Scan a single epub chapter for historical/scientific references a reader would
want context on. Designed to be run iteratively so you can inspect and tweak.

Usage:
    # List available chapters:
    python scan_chapter.py --list

    # Scan a specific chapter by number:
    python scan_chapter.py --chapter 4

    # Scan by name (partial match):
    python scan_chapter.py --chapter "Boston Common"

    # Use a different model:
    python scan_chapter.py --chapter 4 --model google/gemini-2.0-flash-001
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import yaml
import requests
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
import ebooklib
from ebooklib import epub
import warnings
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

PROJECT_ROOT = Path(__file__).parent.parent
TOPICS_DIR = PROJECT_ROOT / "src" / "content" / "topics"
ANNOTATIONS_DIR = PROJECT_ROOT / "src" / "content" / "annotations"
OUTPUT_DIR = PROJECT_ROOT / "scripts" / "link_reports"
EPUB_PATH = Path.home() / "Calibre Library" / "Neal Stephenson" / \
    "Quicksilver_ The Baroque Cycle #1 (88)" / \
    "Quicksilver_ The Baroque Cycle #1 - Neal Stephenson.epub"


def get_api_key():
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        token_path = Path.home() / ".config" / "open-router.token"
        if token_path.exists():
            key = token_path.read_text().strip()
    return key


def load_existing_topics():
    """Load topic slugs and titles for context."""
    topics = {}
    for f in sorted(TOPICS_DIR.glob("*.md")):
        text = f.read_text()
        if text.startswith("---"):
            fm_end = text.index("---", 3)
            fm = yaml.safe_load(text[3:fm_end])
        else:
            fm = {}
        topics[f.stem] = {
            "title": fm.get("title", f.stem.replace("-", " ").title()),
            "category": fm.get("category", "topic"),
            "fictional": fm.get("fictional", False),
        }
    return topics


def load_existing_annotations():
    """Load annotation page numbers and their topics."""
    annotations = {}
    for f in sorted(ANNOTATIONS_DIR.glob("*.md")):
        text = f.read_text()
        if text.startswith("---"):
            fm_end = text.index("---", 3)
            fm = yaml.safe_load(text[3:fm_end])
            page = fm.get("page")
            if page:
                annotations[page] = {
                    "characters": fm.get("characters", []),
                    "topics": fm.get("topics", []),
                }
    return annotations


def extract_chapters():
    """Extract all chapters from epub."""
    book = epub.read_epub(str(EPUB_PATH))
    chapters = []
    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        soup = BeautifulSoup(item.get_content(), "lxml")
        text = soup.get_text(separator="\n", strip=True)
        if len(text.strip()) < 100:
            continue
        title_tag = soup.find(["h1", "h2", "h3"])
        title = title_tag.get_text(strip=True) if title_tag else item.get_name()
        chapters.append({"title": title, "text": text})
    return chapters


def chunk_text(text, max_chars=8000, overlap=300):
    """Split text into overlapping chunks."""
    chunks = []
    start = 0
    while start < len(text):
        end = start + max_chars
        chunks.append(text[start:end])
        start = end - overlap
    return chunks


def call_openrouter(messages, model, api_key):
    """Call OpenRouter chat completion API."""
    resp = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": 4000,
        },
        timeout=90,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def build_system_prompt(topics):
    """Build the system prompt with novel context."""

    existing_real = []
    existing_fictional = []
    for slug, info in sorted(topics.items()):
        line = f"  - {slug}: {info['title']}"
        if info.get("fictional"):
            existing_fictional.append(line)
        else:
            existing_real.append(line)

    return f"""You are helping build a reading companion wiki for Neal Stephenson's novel "Quicksilver" (2003), the first volume of the Baroque Cycle. The novel is set mostly in the 1660s-1710s and blends fictional characters with real historical figures and events.

YOUR TASK: Read a passage from the novel and identify references that a first-time reader would genuinely benefit from having historical, scientific, or cultural context on. Focus on things where background knowledge meaningfully enriches the reading.

WHAT TO FLAG — things where a paragraph of context would help a reader:
- Real historical people the reader may not know (scientists, monarchs, politicians, philosophers)
- Real historical events that are referenced or alluded to (wars, treaties, revolutions, scientific breakthroughs)
- Scientific concepts and discoveries being discussed or demonstrated (optics, calculus, alchemy)
- Institutions with specific historical roles (Royal Society, East India Company, etc.)
- Period-specific practices, technologies, or social customs that are genuinely unfamiliar (e.g. the Asiento, trepanning, how London Bridge worked as a street of shops)
- Books, papers, or inventions specifically referenced
- Religious/political movements with specific historical context (not just "Puritans exist")

WHAT NOT TO FLAG:
- Fictional characters and events (see list below)
- Generic places (London, Boston, Paris) unless the passage describes something historically specific about them
- Common terms a modern reader can understand from context (gallows, tavern, grammar school, Latin)
- Things merely mentioned in passing — only flag if there's enough in the passage for an annotation to be useful
- Duplicate references: if someone is mentioned multiple times in the passage, flag only the first substantive mention
- Broad categories (e.g. don't flag "religious conflict in Europe" — flag the specific conflict)

IMPORTANT CONTEXT — FICTIONAL vs REAL:

The following are FICTIONAL characters/things invented by Stephenson. Do NOT flag these:
- Daniel Waterhouse, Drake Waterhouse, Raleigh Waterhouse, Sterling Waterhouse
- Jack Shaftoe, Bob Shaftoe, Jimmy and Danny Shaftoe
- Eliza (Duchess of Qwghlm/Arcachon)
- Knott Bolstrood, Gomer Bolstrood, Gregory Bolstrood
- Roger Comstock, John Comstock, Charles Comstock
- Louis Anglesey (Earl of Upnor), Thomas More Anglesey
- The "Barkers" (fictional Puritan sect)
- Godfrey William Waterhouse, Wait-Still Waterhouse, Mayflower Waterhouse, Hortense Waterhouse
- Tess, Faith, Praise-God (Waterhouse family)
- Dappa, van Hoek, the Minerva (ship)
- "Massachusetts Bay Colony Institute of Technologickal Arts" (fictional — joke about MIT)
- Any character you cannot identify as a real historical figure — assume fictional

The following are REAL people/topics. We already have wiki pages for these — use the slug in "existing_topic":
{chr(10).join(existing_real)}

We also have wiki pages for these fictional characters:
{chr(10).join(existing_fictional)}

For each reference you identify, provide:
1. "quote" — the specific phrase from the text (enough to locate it, ~5-15 words)
2. "subject" — what the reference is about
3. "type" — one of: "person", "event", "science", "institution", "place", "term", "work"
4. "existing_topic" — slug of our existing wiki page if we have one, or null
5. "suggested_slug" — if no existing topic, suggest a kebab-case slug for a new page
6. "note" — 1-2 sentences: what specific context would help the reader here?

Return a JSON array. If nothing substantive in the passage, return [].
Return ONLY valid JSON, no markdown fences or other text."""


def scan_chapter(chapter, topics, model, api_key):
    """Scan a single chapter and return findings."""
    system_prompt = build_system_prompt(topics)
    chunks = chunk_text(chapter["text"])
    all_findings = []

    print(f"  Scanning '{chapter['title']}' ({len(chapter['text'])} chars, {len(chunks)} chunks)")

    for i, chunk in enumerate(chunks):
        print(f"    Chunk {i+1}/{len(chunks)}...", end=" ", flush=True)

        user_prompt = f"""Here is a passage from the chapter "{chapter['title']}" in Quicksilver:

---
{chunk}
---

Identify every historical, scientific, or cultural reference a first-time reader would want context on. Return JSON array."""

        try:
            response = call_openrouter(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                model, api_key,
            )

            clean = response.strip()
            if clean.startswith("```"):
                clean = re.sub(r'^```\w*\n?', '', clean)
                clean = re.sub(r'\n?```$', '', clean)
                clean = clean.strip()

            findings = json.loads(clean)
            for f in findings:
                f["chapter"] = chapter["title"]
                f["chunk"] = i
            all_findings.extend(findings)
            print(f"{len(findings)} references found")

        except json.JSONDecodeError as e:
            print(f"JSON parse error: {e}")
            print(f"  Raw response: {response[:200]}")
        except Exception as e:
            print(f"Error: {e}")

        time.sleep(0.5)

    return all_findings


def print_chapter_summary(findings, chapter_title):
    """Print a summary of findings for one chapter."""
    has_topic = [f for f in findings if f.get("existing_topic")]
    needs_topic = [f for f in findings if not f.get("existing_topic")]

    print(f"\n  Already have topic page: {len(has_topic)}")
    print(f"  Would need new topic:    {len(needs_topic)}")

    if has_topic:
        print(f"\n--- References matching existing topics ---")
        for f in has_topic:
            print(f"  [{f['existing_topic']}] {f['subject']}")
            print(f"    \"{f['quote'][:80]}\"")

    if needs_topic:
        print(f"\n--- References that could use new topic pages ---")
        seen = set()
        for f in needs_topic:
            slug = f.get("suggested_slug", f["subject"])
            if slug in seen:
                continue
            seen.add(slug)
            print(f"  {f['type']:<12} {f['subject']}")
            print(f"    slug: {f.get('suggested_slug', '?')}")
            print(f"    {f.get('note', '')[:100]}")


# --- Noise words / low-value slugs to auto-filter ---
NOISE_SLUGS = {
    # Too generic / obvious from context
    "gallows", "grammar-school", "grammar-school-education", "anno-domini",
    "latin", "latin-language", "london", "boston", "paris",
    "lobsterbacks", "men-of-war", "man-of-war", "galleons", "spanish-galleon",
    "anti-monarchism", "christendom", "metaphysics", "metaphysics-17th-century",
    "display-of-corpses", "public-executions-in-london",
    "london-in-the-17th-century", "cambridge-university",
    "oxford-university", "cambridge-england",
    # Fictional things that slipped through
    "the-barkers", "barkers", "massachusetts-bay-colony-institute-of-technologickal-arts",
    "earl-of-epsom",
    # Too vague / broad
    "european-wars-of-religion", "english-religious-tensions",
    "religious-diversity-amsterdam", "spanish-colonial-architecture",
    "historical-trade-cities", "early-modern-trade-routes",
    "colonial-trade-goods", "tallow-chandlery",
    "gaols-in-17th-century-england", "horse-powered-wheel",
    "socratic-method", "euclidean-geometry",
    "cartesian-coordinate-system", "cartesian-coordinates", "cartesian-analysis",
    "religious-conflict-london-1600s", "religious-fanaticism",
    "social-hierarchy-17th-century", "london-fashion-17th-century",
    "historical-role-of-clergy",
    # Terms a reader can figure out from context
    "sizar", "cavaliers", "coroner", "guinea-coin", "lascars",
    "pieta", "hair-shirt", "usquebaugh", "periwig", "whitsunday",
    "pensioner-cambridge", "baroque-minuet", "homunculus",
    "admiralty-law", "justice-of-the-peace", "prince-elector",
    "predestination", "calvinism", "empiricism", "scholasticism",
    "arminianism", "cartesianism",
    "established-church", "religious-independents",
    "carolus-ii-dei-gratia", "spanish-coinage",
    # Overly specific place refs
    "luanda", "angola", "angola-slave-trade", "angola-and-the-slave-trade",
    "nantucket", "charlestown-massachusetts", "cambridge-massachusetts",
    "charles-river", "st-james-palace", "boston-common",
    "spanish-main", "pool-of-london", "pillars-of-hercules",
    "ely", "the-backs-cambridge", "volcanoes-italy",
    "kings-college-cambridge", "plymouth-rock",
    "gibraltar-newfoundland-st-kitts", "alexandria",
    # Already covered by broader topic or too minor
    "jacobs-ladder", "primum-mobile", "ursa-major",
    "precession-of-the-zodiac",
    "irish-indentured-servitude",
    "history-of-tea", "cryptography",
}

NOISE_SUBJECTS_LOWER = {
    "gallows", "grammar school", "latin", "london", "anno domini",
    "lobsterbacks", "men-of-war", "galleons", "periwig", "periwigs",
    "gaol", "gaols", "socratically", "cavaliers", "sizar",
    "coroner", "lascars", "hair shirt", "whitsunday",
}

# Slug normalization: map variant slugs to canonical forms
SLUG_ALIASES = {
    "christian-huygens": "christiaan-huygens",
    "pieces-of-eight": "piece-of-eight",
    "leviathan-hobbes": "leviathan",
    "leviathan-thomas-hobbes": "leviathan",
    "archbishop-laud": "william-laud",
    "sophie-of-hanover": "electress-sophie-of-hanover",
    "harvard-university": "harvard-college",
    "harvard-university-history": "harvard-college",
    "carlos-ii-of-spain": "charles-ii-of-spain",
    "act-of-uniformity": "act-of-uniformity-1662",
    "declaration-of-uniformity": "act-of-uniformity-1662",
    "plato-and-aristotle": "aristotle",
    "cartesian-geometry": "rene-descartes",
    "principia-philosophiae": "rene-descartes",
    "cryptonomicon-wilkins": "john-wilkins",  # covered by existing topic
    "duke-of-york": "james-ii",  # already have james-ii topic
    "english-interregnum": "english-civil-war",  # covered by existing topic
    "aristotelian-physics": "aristotle",
    "john-churchill-duke-of-marlborough": "john-churchill",
    "duke-of-marlborough": "john-churchill",
}


def normalize_slug(slug):
    """Normalize a slug using aliases."""
    return SLUG_ALIASES.get(slug, slug)


def filter_noise(findings):
    """Remove low-value findings and normalize slugs."""
    filtered = []
    for f in findings:
        slug = f.get("suggested_slug", "")
        if slug in NOISE_SLUGS:
            continue
        if f.get("subject", "").lower() in NOISE_SUBJECTS_LOWER:
            continue
        # Normalize slug aliases
        if slug and slug in SLUG_ALIASES:
            f["suggested_slug"] = SLUG_ALIASES[slug]
        # Skip if it's just a generic place name with no specific historical content
        if f.get("type") == "place" and not f.get("existing_topic"):
            note = f.get("note", "").lower()
            if not any(w in note for w in ["famous", "known for", "significant", "important",
                                            "notable", "served as", "was a", "built", "destroyed",
                                            "battle", "siege", "massacre", "treaty"]):
                continue
        filtered.append(f)
    return filtered


def consolidate_findings(all_findings):
    """Deduplicate and consolidate findings across chapters into an actionable report."""

    # Group by topic (existing_topic or suggested_slug)
    by_topic = {}
    for f in all_findings:
        key = f.get("existing_topic") or f.get("suggested_slug") or f.get("subject", "unknown")
        key = key.lower().strip()
        if key not in by_topic:
            by_topic[key] = {
                "subject": f.get("subject", ""),
                "type": f.get("type", ""),
                "existing_topic": f.get("existing_topic"),
                "suggested_slug": f.get("suggested_slug"),
                "note": f.get("note", ""),
                "chapters": [],
                "quotes": [],
            }
        entry = by_topic[key]
        ch = f.get("chapter", "?")
        if ch not in entry["chapters"]:
            entry["chapters"].append(ch)
        quote = f.get("quote", "")
        if quote and len(entry["quotes"]) < 3:  # Keep up to 3 sample quotes
            entry["quotes"].append({"chapter": ch, "quote": quote})

    return by_topic


def write_report(consolidated, output_path):
    """Write a clean markdown report from consolidated findings."""

    existing = {k: v for k, v in consolidated.items() if v.get("existing_topic")}
    new = {k: v for k, v in consolidated.items() if not v.get("existing_topic")}

    lines = []
    lines.append("# Quicksilver Reading Guide — Reference Scan Report\n")
    lines.append(f"Total unique references: {len(consolidated)}")
    lines.append(f"Already have topic pages: {len(existing)}")
    lines.append(f"Need new topic pages: {len(new)}\n")

    # --- Existing topics: where they appear ---
    lines.append("## Existing Topics — Chapter Appearances\n")
    lines.append("These topics already have wiki pages. Use this to add links in annotations.\n")
    for key in sorted(existing, key=lambda k: -len(existing[k]["chapters"])):
        entry = existing[key]
        ch_list = ", ".join(entry["chapters"])
        lines.append(f"- **{entry['subject']}** (`{entry['existing_topic']}`) — {len(entry['chapters'])} chapters")
        lines.append(f"  - Chapters: {ch_list}")

    # --- New topics needed ---
    lines.append("\n## New Topics Needed\n")
    lines.append("Grouped by type, sorted by number of chapter appearances.\n")

    by_type = {}
    for key, entry in new.items():
        t = entry.get("type", "other")
        by_type.setdefault(t, []).append((key, entry))

    type_order = ["person", "event", "science", "institution", "work", "term", "place"]
    for t in type_order:
        items = by_type.get(t, [])
        if not items:
            continue
        items.sort(key=lambda x: -len(x[1]["chapters"]))
        lines.append(f"### {t.title()}s\n")
        for key, entry in items:
            n_ch = len(entry["chapters"])
            slug = entry.get("suggested_slug", key)
            lines.append(f"- **{entry['subject']}** (`{slug}`) — {n_ch} chapter{'s' if n_ch > 1 else ''}")
            lines.append(f"  - {entry['note'][:150]}")
            if entry["quotes"]:
                q = entry["quotes"][0]
                lines.append(f"  - Example: \"{q['quote'][:80]}\" ({q['chapter']})")

    with open(output_path, "w") as f:
        f.write("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser(description="Scan epub chapters for historical references")
    parser.add_argument("--list", action="store_true", help="List available chapters")
    parser.add_argument("--chapter", help="Chapter number or name (partial match)")
    parser.add_argument("--book", type=int, help="Scan all chapters in a book (1, 2, or 3)")
    parser.add_argument("--all", action="store_true", help="Scan all novel chapters")
    parser.add_argument("--model", default="google/gemini-2.0-flash-001")
    parser.add_argument("--output", help="Output file (default: auto-named in link_reports/)")
    parser.add_argument("--resume", help="Resume batch scan from a partial results JSON file")
    args = parser.parse_args()

    chapters = extract_chapters()

    if args.list:
        for i, ch in enumerate(chapters):
            print(f"  {i+1:>3}. {ch['title']:<65} ({len(ch['text']):>6} chars)")
        return

    # Determine which chapters to scan
    targets = []

    if args.chapter:
        try:
            idx = int(args.chapter) - 1
            targets = [chapters[idx]]
        except (ValueError, IndexError):
            for ch in chapters:
                if args.chapter.lower() in ch["title"].lower():
                    targets = [ch]
                    break

    elif args.book or args.all:
        # Book boundaries by chapter title
        book_starts = {
            1: "BOOK ONE",
            2: "BOOK TWO",
            3: "BOOK THREE",
        }
        # Skip non-novel content
        skip_prefixes = ["Contents", "Invocation", "BOOK ONE", "BOOK TWO", "BOOK THREE",
                         "Dramatis Personae", "Acknowledgments", "About the Author",
                         "Copyright", "text/"]

        if args.all:
            books_to_scan = [1, 2, 3]
        else:
            books_to_scan = [args.book]

        current_book = 0
        for ch in chapters:
            if ch["title"] == "BOOK ONE":
                current_book = 1
            elif ch["title"] == "BOOK TWO":
                current_book = 2
            elif ch["title"] == "BOOK THREE":
                current_book = 3

            if current_book not in books_to_scan:
                continue
            if any(ch["title"].startswith(p) for p in skip_prefixes):
                continue
            if len(ch["text"]) < 500:
                continue
            targets.append(ch)

    if not targets:
        if not args.list:
            parser.print_help()
        return

    api_key = get_api_key()
    if not api_key:
        print("No API key found. Set OPENROUTER_API_KEY or put key in ~/.config/open-router.token")
        return

    topics = load_existing_topics()
    print(f"Loaded {len(topics)} existing topic pages")
    print(f"Chapters to scan: {len(targets)}\n")

    OUTPUT_DIR.mkdir(exist_ok=True)

    # Load partial results if resuming
    all_findings = []
    scanned_titles = set()
    if args.resume:
        with open(args.resume) as f:
            all_findings = json.load(f)
        scanned_titles = set(f["chapter"] for f in all_findings)
        print(f"Resuming: loaded {len(all_findings)} findings from {len(scanned_titles)} chapters\n")

    # Determine output paths
    if len(targets) == 1:
        slug = re.sub(r'[^a-z0-9]+', '-', targets[0]["title"].lower()).strip('-')
        raw_path = args.output or str(OUTPUT_DIR / f"scan-{slug}.json")
        report_path = str(OUTPUT_DIR / f"report-{slug}.md")
    else:
        label = f"book{args.book}" if args.book else "all"
        raw_path = args.output or str(OUTPUT_DIR / f"scan-{label}-raw.json")
        report_path = str(OUTPUT_DIR / f"report-{label}.md")

    # Scan
    for i, target in enumerate(targets):
        if target["title"] in scanned_titles:
            print(f"  [{i+1}/{len(targets)}] Skipping '{target['title']}' (already scanned)")
            continue

        print(f"  [{i+1}/{len(targets)}]", end=" ")
        findings = scan_chapter(target, topics, args.model, api_key)
        all_findings.extend(findings)

        # Save incrementally so we can resume
        with open(raw_path, "w") as f:
            json.dump(all_findings, f, indent=2)

    # Filter noise
    clean_findings = filter_noise(all_findings)
    print(f"\nFiltered: {len(all_findings)} raw → {len(clean_findings)} after noise removal")

    # Save cleaned raw
    clean_path = raw_path.replace("-raw.json", "-clean.json").replace(".json", "-clean.json")
    with open(clean_path, "w") as f:
        json.dump(clean_findings, f, indent=2)

    # Consolidate and write report
    consolidated = consolidate_findings(clean_findings)
    write_report(consolidated, report_path)

    print(f"\nRaw findings: {raw_path}")
    print(f"Clean findings: {clean_path}")
    print(f"Report: {report_path}")

    # Print summary
    existing = sum(1 for v in consolidated.values() if v.get("existing_topic"))
    new = sum(1 for v in consolidated.values() if not v.get("existing_topic"))
    print(f"\nUnique references: {len(consolidated)} ({existing} existing, {new} new)")

    if len(targets) == 1:
        print_chapter_summary(clean_findings, targets[0]["title"])


if __name__ == "__main__":
    main()
