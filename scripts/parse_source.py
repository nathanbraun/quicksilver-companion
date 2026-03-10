"""Parse the original wiki source files and catalog them."""

import re
from pathlib import Path
from dataclasses import dataclass, field

RAW_DIR = Path(__file__).parent.parent.parent / "quicksilver-wiki-original" / "raw"

# Matches annotation files: stephenson-neal-quicksilver-{page}-{desc}-{author}.md
# Page can be a number or roman numeral (v, vii, ix, xiii, xvi)
ANNOTATION_RE = re.compile(
    r"^stephenson-neal-quicksilver-(\d+|[ivxlc]+)-(.+?)(?:-([a-z]+-[a-z]+(?:-[a-z]+)*))?\.md$"
)

# More precise: match known author patterns at the end
ANNOTATION_WITH_AUTHOR_RE = re.compile(
    r"^stephenson-neal-quicksilver-(\d+)-(.+)-([a-z]+-(?:stephenson|sinder|bornstein|kuehner|stephenson|tufts|whalen|horst|lotzmana|armaced|jonnay|morgan|smith|doe|einstein|descartes|professorbikeybike|neal-stephenson))\.md$"
)

# Simpler: just grab page number annotations
ANNOTATION_SIMPLE_RE = re.compile(
    r"^stephenson-neal-quicksilver-(\d+)-(.+)\.md$"
)

# Character pages within the QS namespace
CHARACTER_QS_RE = re.compile(
    r"^stephenson-neal-quicksilver-([a-z][\w-]+)\.md$"
)

# Internal link pattern in markdown
LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")


@dataclass
class SourceFile:
    path: Path
    filename: str
    file_type: str  # 'annotation', 'character', 'topic'
    page: int | None = None
    title: str = ""
    slug: str = ""
    content: str = ""
    outbound_links: list[str] = field(default_factory=list)
    original_authors: list[str] = field(default_factory=list)


def parse_annotation_filename(filename: str) -> tuple[int | None, str, str]:
    """Extract page number, description, author from annotation filename."""
    # Try to match with known author suffixes
    m = ANNOTATION_SIMPLE_RE.match(filename)
    if not m:
        return None, "", ""

    page_str = m.group(1)
    rest = m.group(2)

    try:
        page = int(page_str)
    except ValueError:
        return None, rest, ""

    # Try to split author from description
    # Known authors appear as the last hyphen-separated segment
    known_authors = [
        "neal-stephenson", "alan-sinder", "jeremy-bornstein", "brett-kuehner",
        "patrick-tufts", "t-whalen", "steven-horst", "lotzmana", "armaced",
        "jonnay", "cheryl-morgan", "professorbikeybike", "albert-einstein",
        "jane-smith", "john-doe", "rene-descartes", "agquarx", "scott-elkin",
        "mike-lorrey", "gary-thompson", "enrique", "sparky", "timberbee",
        "talith", "chris-swingle", "bram-dingelstad", "dennis-watson",
        "pat-mchugh"
    ]

    author = ""
    desc = rest
    for a in known_authors:
        if rest.endswith("-" + a):
            author = a
            desc = rest[: -(len(a) + 1)]
            break
        elif rest == a:
            author = a
            desc = ""
            break

    return page, desc, author


def extract_links(content: str) -> list[str]:
    """Extract internal wiki link targets from markdown content."""
    links = []
    for match in LINK_RE.finditer(content):
        target = match.group(2)
        # Skip external links
        if target.startswith("http") or target.startswith("#"):
            continue
        links.append(target)
    return links


def strip_frontmatter(content: str) -> str:
    """Remove YAML frontmatter from markdown."""
    if content.startswith("---"):
        end = content.find("---", 3)
        if end != -1:
            return content[end + 3:].strip()
    return content


def catalog_all_files() -> dict[str, SourceFile]:
    """Parse all raw source files and return a catalog keyed by slug."""
    catalog = {}

    for path in sorted(RAW_DIR.glob("*.md")):
        filename = path.name
        slug = filename.replace(".md", "")
        content = path.read_text(encoding="utf-8", errors="replace")
        body = strip_frontmatter(content)
        links = extract_links(body)

        # Classify the file
        if filename.startswith("stephenson-neal-quicksilver-"):
            page, desc, author = parse_annotation_filename(filename)
            if page is not None:
                sf = SourceFile(
                    path=path,
                    filename=filename,
                    file_type="annotation",
                    page=page,
                    title=desc.replace("-", " ").title(),
                    slug=slug,
                    content=body,
                    outbound_links=links,
                    original_authors=[author] if author else [],
                )
            else:
                # QS-namespaced but not a page annotation (character or meta page)
                sf = SourceFile(
                    path=path,
                    filename=filename,
                    file_type="character",
                    title=slug.replace("stephenson-neal-quicksilver-", "").replace("-", " ").title(),
                    slug=slug,
                    content=body,
                    outbound_links=links,
                )
        else:
            # General topic/character page
            sf = SourceFile(
                path=path,
                filename=filename,
                file_type="topic",
                title=slug.replace("-", " ").title(),
                slug=slug,
                content=body,
                outbound_links=links,
            )

        catalog[slug] = sf

    return catalog


def get_annotations(catalog: dict[str, SourceFile]) -> list[SourceFile]:
    """Get all annotation files sorted by page number."""
    annotations = [sf for sf in catalog.values() if sf.file_type == "annotation"]
    annotations.sort(key=lambda sf: (sf.page or 0, sf.slug))
    return annotations


def group_annotations_by_page(annotations: list[SourceFile]) -> dict[int, list[SourceFile]]:
    """Group annotations by page number."""
    groups: dict[int, list[SourceFile]] = {}
    for sf in annotations:
        if sf.page is not None:
            groups.setdefault(sf.page, []).append(sf)
    return groups


if __name__ == "__main__":
    catalog = catalog_all_files()
    annotations = get_annotations(catalog)
    by_page = group_annotations_by_page(annotations)

    print(f"Total source files: {len(catalog)}")
    print(f"Annotations: {len(annotations)}")
    print(f"Unique annotated pages: {len(by_page)}")
    print(f"Characters: {sum(1 for sf in catalog.values() if sf.file_type == 'character')}")
    print(f"Topics: {sum(1 for sf in catalog.values() if sf.file_type == 'topic')}")
    print()
    print("Sample annotations:")
    for ann in annotations[:5]:
        print(f"  p.{ann.page}: {ann.title} (by {', '.join(ann.original_authors) or 'unknown'})")
        print(f"    {len(ann.content)} chars, {len(ann.outbound_links)} links")
