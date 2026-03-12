# Quicksilver Reading Companion

Reading companion wiki for Neal Stephenson's *Quicksilver* (Baroque Cycle vol 1). Provides page-by-page historical/scientific annotations and topic pages.

## Build & Dev

```bash
npm run dev          # local dev server
npm run build        # production build
rm -rf .astro && npx astro build  # clean build (needed when collections change)
```

Requires Node >= 22.12.0. Astro 6 with MDX.

Python scripts use a venv at `scripts/.venv/` (activate before running generation scripts).

## Project Structure

- `src/content/annotations/` — page-by-page annotation files (`chapter-NNNN.md`, NNNN = chapter start page)
- `src/content/topics/` — unified topic pages (characters, historical figures, events, science, institutions)
- `src/data/chapters.json` — chapter metadata
- `src/pages/` — Astro page routes (annotation, topic, book, chapter views + before-you-read primers)
- `src/plugins/rehype-topic-links.mjs` — converts dead `/topic/*` links to plain `<span>` at build time
- `src/content.config.ts` — collection schemas
- `scripts/` — Python generation pipeline (see below)

## Content Collections

**annotations**: `title`, `page`, `book` (1-3), `book_title`, `chapter_start_page`, `chapter_location`, `chapter_date`, `characters[]`, `topics[]`, `original_authors[]`

**topics**: `title`, `category`, `fictional` (optional boolean for characters), `first_mention_page`, `related_characters[]`

## Annotation Style

- Bold-term-dash format: `**Term** — explanation`
- Lead with surprising/interesting facts, no filler
- Inline topic links: `[text](/topic/{slug})`
- Tone: earnest and informational. No "fascinating," "pivotal," "arguably," or other AI filler. No glib asides.
- One plain opening context line per annotation page
- Stephenson's own annotations get quoted directly

## Generation Pipeline (scripts/)

Two-pass process using Gemini Flash via OpenRouter (`~/.config/open-router.token`):

1. `generate_annotations.py` — scan epub chapters, match findings, generate annotations
2. `cleanup_annotations.py` — remove redundant entries (topics already explained earlier)
3. `inject_wiki_annotations.py` — merge quality-filtered wiki annotations from `../quicksilver-wiki-original/`

Supporting: `scan_chapter.py`, `find_links.py`, `chapter_data.py`, `parse_source.py`

## Content Status

- 68 chapters, 1537 annotations across all 3 books
- 110 topic pages
- 3 before-you-read primers (no plot spoilers, historical context only)
- `scripts/missing-topics.txt` tracks 833 missing topic slugs for future generation
