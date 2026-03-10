#!/usr/bin/env python3
"""
Scan annotations and epub to find missing cross-links in the Quicksilver wiki.

Pass 1: Local scan — find topic mentions in annotations that aren't linked.
Pass 2: LLM scan — use cheap model via OpenRouter to find topic connections
         in the epub text that our annotations don't cover.

Usage:
    # Just the local scan (no API needed):
    python find_links.py --local-only

    # Full scan with LLM:
    OPENROUTER_API_KEY=... python find_links.py

    # Specify model:
    python find_links.py --model google/gemini-2.0-flash-001
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
from bs4 import BeautifulSoup
import ebooklib
from ebooklib import epub

# Paths
PROJECT_ROOT = Path(__file__).parent.parent
ANNOTATIONS_DIR = PROJECT_ROOT / "src" / "content" / "annotations"
TOPICS_DIR = PROJECT_ROOT / "src" / "content" / "topics"
EPUB_PATH = Path.home() / "Calibre Library" / "Neal Stephenson" / \
    "Quicksilver_ The Baroque Cycle #1 (88)" / \
    "Quicksilver_ The Baroque Cycle #1 - Neal Stephenson.epub"
OUTPUT_DIR = PROJECT_ROOT / "scripts" / "link_reports"


def load_topics():
    """Load all topic slugs and build search terms for each."""
    topics = {}
    for f in sorted(TOPICS_DIR.glob("*.md")):
        slug = f.stem
        text = f.read_text()
        # Parse frontmatter
        if text.startswith("---"):
            fm_end = text.index("---", 3)
            fm = yaml.safe_load(text[3:fm_end])
        else:
            fm = {}

        title = fm.get("title", slug.replace("-", " ").title())

        # Build search terms: title + slug variations
        search_terms = set()
        search_terms.add(title.lower())
        # slug words (e.g. "daniel-waterhouse" -> "daniel waterhouse")
        slug_words = slug.replace("-", " ")
        search_terms.add(slug_words)

        # Last name for characters (e.g. "Waterhouse", "Newton")
        parts = title.split()
        if len(parts) >= 2 and fm.get("category") == "characters":
            search_terms.add(parts[-1].lower())

        # Some special cases for common references
        aliases = {
            "gottfried-wilhelm-von-leibniz": ["leibniz"],
            "isaac-newton": ["newton"],
            "robert-hooke": ["hooke"],
            "daniel-waterhouse": ["waterhouse", "daniel"],
            "jack-shaftoe": ["jack shaftoe", "shaftoe"],
            "bob-shaftoe": ["bob shaftoe"],
            "enoch-root": ["enoch"],
            "charles-ii": ["charles ii", "king charles"],
            "james-ii": ["james ii", "king james"],
            "louis-xiv": ["louis xiv", "le roi"],
            "john-wilkins": ["wilkins"],
            "john-locke": ["locke"],
            "samuel-pepys": ["pepys"],
            "oliver-cromwell": ["cromwell"],
            "caroline-of-ansbach": ["caroline", "princess caroline"],
            "roger-comstock": ["roger comstock"],
            "john-comstock": ["john comstock"],
            "henry-oldenburg": ["oldenburg"],
            "natural-philosophy": ["natural philosophy", "natural philosopher"],
            "royal-society": ["royal society"],
            "great-fire-of-london": ["great fire"],
            "english-civil-war": ["civil war"],
            "calculus-priority-dispute": ["calculus dispute", "priority dispute", "calculus"],
            "glorious-revolution": ["glorious revolution"],
            "hanoverian-succession": ["hanoverian succession", "hanover"],
            "restoration-london": ["restoration london"],
            "quicksilver-mercury": ["quicksilver", "mercury"],
            "computing-machines": ["computing machine", "computing"],
            "universal-characteristic": ["universal characteristic", "characteristica"],
        }
        if slug in aliases:
            search_terms.update(aliases[slug])

        topics[slug] = {
            "title": title,
            "search_terms": search_terms,
            "category": fm.get("category", "topic"),
            "fictional": fm.get("fictional", False),
        }
    return topics


def parse_annotation(path):
    """Parse an annotation .md file, return frontmatter + body."""
    text = path.read_text()
    if not text.startswith("---"):
        return None, text
    fm_end = text.index("---", 3)
    fm = yaml.safe_load(text[3:fm_end])
    body = text[fm_end + 3:].strip()
    return fm, body


def find_existing_links(body):
    """Find all topic slugs already linked in the body."""
    # Match [text](/topic/slug) patterns
    linked = set()
    for m in re.finditer(r'\[.*?\]\(/topic/([\w-]+)\)', body):
        linked.add(m.group(1))
    return linked


def pass1_local_scan(topics):
    """Scan annotations for unlinked topic mentions."""
    results = []

    for ann_path in sorted(ANNOTATIONS_DIR.glob("*.md")):
        fm, body = parse_annotation(ann_path)
        if fm is None:
            continue

        page = fm.get("page", "?")
        existing_links = find_existing_links(body)
        # Also count topics/characters listed in frontmatter
        fm_topics = set(fm.get("topics", []) + fm.get("characters", []))

        body_lower = body.lower()

        for slug, info in topics.items():
            if slug in existing_links:
                continue  # Already linked

            for term in info["search_terms"]:
                # Word boundary check - use regex for accuracy
                # Skip very short terms (< 4 chars) to avoid false positives
                if len(term) < 4:
                    continue
                pattern = r'\b' + re.escape(term) + r'\b'
                matches = list(re.finditer(pattern, body_lower))
                if matches:
                    # Find the actual text context around first match
                    m = matches[0]
                    start = max(0, m.start() - 40)
                    end = min(len(body), m.end() + 40)
                    context = body[start:end].replace("\n", " ").strip()

                    in_frontmatter = slug in fm_topics
                    results.append({
                        "page": page,
                        "file": ann_path.name,
                        "topic_slug": slug,
                        "topic_title": info["title"],
                        "matched_term": term,
                        "context": f"...{context}...",
                        "in_frontmatter": in_frontmatter,
                        "match_count": len(matches),
                    })
                    break  # One match per topic per page is enough

    return results


def extract_epub_chapters(epub_path):
    """Extract text from epub, organized by chapter/section."""
    book = epub.read_epub(str(epub_path))
    chapters = []

    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        soup = BeautifulSoup(item.get_content(), "lxml")
        text = soup.get_text(separator="\n", strip=True)
        if len(text.strip()) < 100:
            continue  # Skip trivial sections

        # Try to extract chapter title
        title_tag = soup.find(["h1", "h2", "h3"])
        title = title_tag.get_text(strip=True) if title_tag else item.get_name()

        chapters.append({
            "id": item.get_name(),
            "title": title,
            "text": text,
        })

    return chapters


def chunk_text(text, max_chars=6000, overlap=200):
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
            "temperature": 0.2,
            "max_tokens": 2000,
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def pass2_llm_scan(topics, chapters, model, api_key, annotation_pages):
    """Use LLM to find topic connections in epub text."""
    topic_list = "\n".join(
        f"- {slug}: {info['title']}"
        for slug, info in sorted(topics.items())
    )

    # Which pages do we already have annotations for?
    covered_pages = sorted(annotation_pages)

    results = []
    total_chunks = 0

    for ch in chapters:
        chunks = chunk_text(ch["text"])
        total_chunks += len(chunks)

    print(f"  Total chunks to process: {total_chunks}")

    chunk_idx = 0
    for ch in chapters:
        chunks = chunk_text(ch["text"])
        for i, chunk in enumerate(chunks):
            chunk_idx += 1
            if chunk_idx % 10 == 0:
                print(f"  Processing chunk {chunk_idx}/{total_chunks}...")

            prompt = f"""You are analyzing a passage from Neal Stephenson's novel "Quicksilver" to help build a reading companion wiki.

Here is the passage from chapter/section "{ch['title']}":

---
{chunk}
---

Here are the topics and characters that have wiki pages:

{topic_list}

Our reading guide already has annotation pages for these page numbers: {covered_pages}

TASK: Identify which wiki topics are meaningfully discussed, referenced, or relevant in this passage. Only include topics where the passage provides real context that would help a reader understand the topic better — not just passing mentions of a name.

For each topic found, note:
1. The topic slug
2. A brief quote or description of what's discussed
3. Whether this seems like it could be on a page we already cover (based on nearby context clues about page location in the book)

Return JSON array like:
[{{"slug": "topic-slug", "relevance": "brief description", "likely_covered": true/false}}]

If no meaningful topic connections, return: []
Return ONLY the JSON, no other text."""

            try:
                response = call_openrouter(
                    [{"role": "user", "content": prompt}],
                    model, api_key,
                )
                # Parse JSON from response
                # Strip markdown code fences if present
                clean = response.strip()
                if clean.startswith("```"):
                    clean = re.sub(r'^```\w*\n?', '', clean)
                    clean = re.sub(r'\n?```$', '', clean)
                    clean = clean.strip()

                findings = json.loads(clean)
                for f in findings:
                    f["chapter"] = ch["title"]
                    f["chunk_index"] = i
                results.extend(findings)

            except Exception as e:
                print(f"  Error on chunk {chunk_idx}: {e}")

            # Rate limiting
            time.sleep(0.5)

    return results


def main():
    parser = argparse.ArgumentParser(description="Find missing links in Quicksilver wiki")
    parser.add_argument("--local-only", action="store_true",
                       help="Only run local scan, skip LLM pass")
    parser.add_argument("--model", default="google/gemini-2.0-flash-001",
                       help="OpenRouter model to use")
    parser.add_argument("--api-key", default=os.environ.get("OPENROUTER_API_KEY"),
                       help="OpenRouter API key")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(exist_ok=True)
    topics = load_topics()
    print(f"Loaded {len(topics)} topics")

    # --- Pass 1: Local scan ---
    print("\n=== Pass 1: Local scan for unlinked topic mentions ===")
    local_results = pass1_local_scan(topics)

    # Filter to most interesting: not already in frontmatter
    new_links = [r for r in local_results if not r["in_frontmatter"]]
    existing_mentions = [r for r in local_results if r["in_frontmatter"]]

    print(f"\nFound {len(new_links)} unlinked topic mentions (topic not in frontmatter)")
    print(f"Found {len(existing_mentions)} unlinked mentions where topic IS in frontmatter")

    # Save results
    report_path = OUTPUT_DIR / "pass1_unlinked_mentions.json"
    with open(report_path, "w") as f:
        json.dump(local_results, f, indent=2)
    print(f"Full results saved to {report_path}")

    # Print summary
    print("\n--- Top unlinked mentions (not in frontmatter) ---")
    for r in new_links[:30]:
        print(f"  p.{r['page']:>4}: [{r['topic_title']}] matched '{r['matched_term']}' — {r['context'][:80]}")

    print(f"\n--- Mentions where topic is in frontmatter but not linked in body ---")
    # Group by topic for readability
    by_topic = {}
    for r in existing_mentions:
        by_topic.setdefault(r["topic_slug"], []).append(r["page"])
    for slug, pages in sorted(by_topic.items()):
        print(f"  {topics[slug]['title']}: pages {pages}")

    if args.local_only:
        print("\n(Skipping LLM scan — use without --local-only to run)")
        return

    # --- Pass 2: LLM scan ---
    if not args.api_key:
        print("\nNo OPENROUTER_API_KEY set. Skipping LLM scan.")
        return

    print(f"\n=== Pass 2: LLM scan with {args.model} ===")
    print("Extracting epub...")
    chapters = extract_epub_chapters(EPUB_PATH)
    print(f"Extracted {len(chapters)} sections from epub")

    annotation_pages = set()
    for ann_path in ANNOTATIONS_DIR.glob("*.md"):
        fm, _ = parse_annotation(ann_path)
        if fm and "page" in fm:
            annotation_pages.add(fm["page"])

    llm_results = pass2_llm_scan(topics, chapters, args.model, args.api_key, annotation_pages)

    report_path2 = OUTPUT_DIR / "pass2_llm_findings.json"
    with open(report_path2, "w") as f:
        json.dump(llm_results, f, indent=2)
    print(f"\nLLM findings saved to {report_path2}")

    # Summarize
    topic_counts = {}
    for r in llm_results:
        slug = r.get("slug", "unknown")
        topic_counts[slug] = topic_counts.get(slug, 0) + 1

    print(f"\nTotal topic references found: {len(llm_results)}")
    print("Most referenced topics:")
    for slug, count in sorted(topic_counts.items(), key=lambda x: -x[1])[:20]:
        title = topics.get(slug, {}).get("title", slug)
        print(f"  {title}: {count} references")


if __name__ == "__main__":
    main()
