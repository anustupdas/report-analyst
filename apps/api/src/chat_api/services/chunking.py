"""Structure-aware chunking for long reports.

Works on per-page text from either extractor:
- plain (PyMuPDF): hard-wrapped lines, no headings; running page headers/footers carry the section.
- markdown (Mistral OCR): ``#`` headings and ``|`` tables carry the structure.

Boundaries are preferred in this order: section, paragraph, sentence, word. Chunks never
exceed ``max_chars`` and consecutive chunks in the same section share whole sentences.
"""

from __future__ import annotations

import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable, Literal

TextFormat = Literal["plain", "markdown"]

SECTION_SEPARATOR = " › "
MAX_SECTION_CHARS = 200
_ZONE_LINES = 8
_GLOBAL_RUNNING_SHARE = 0.3
_PATH_EXCLUDE_SHARE = 0.8
# "#" and "##" start a new chunk; deeper headings are packed together with their siblings.
_MAJOR_HEADING_LEVEL = 2
_MAX_OVERLAP_SHARE = 0.2

_TERMINAL = re.compile(r"[.!?:;][\"”’')\]]*$")
_BULLET = re.compile(r"^\s*(?:[-*•▪◦–]|\(?\d{1,2}[.)]|\(?[a-z][.)])\s+")
_MD_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_MD_IMAGE = re.compile(r"^!\[[^\]]*\]\([^)]*\)$")
_MD_TABLE_SEPARATOR = re.compile(r"^\|?\s*:?-{3,}")
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])[\"”’')\]]*\s+(?=[\"“‘'(\[]?[A-Z0-9€$£])")
_ABBREVIATIONS = frozenset(
    "e.g i.e etc vs approx incl excl no nr mr mrs ms dr prof inc ltd co corp st fig figs p pp art cf ca resp bijv o.a".split()
)


@dataclass(frozen=True)
class PageText:
    page_number: int | None
    text: str
    # Running header already separated by the extractor (Mistral); used as the section hint.
    header: str | None = None


@dataclass(frozen=True)
class TextChunk:
    chunk_index: int
    content: str
    section: str | None
    page_start: int | None
    page_end: int | None
    token_count: int

    @property
    def embed_text(self) -> str:
        """Text sent to the embedding model: section path gives the chunk its context."""
        return f"{self.section}\n\n{self.content}" if self.section else self.content


@dataclass
class _Unit:
    kind: Literal["paragraph", "heading", "table"]
    text: str
    page: int | None
    section: str | None
    # Section path without minor (### and deeper) headings; a change forces a new chunk.
    major: str | None
    level: int = 0
    # (char offset, page) where a paragraph merged across a page break continues.
    page_breaks: list[tuple[int, int | None]] = field(default_factory=list)

    def page_at(self, offset: int) -> int | None:
        page = self.page
        for start, next_page in self.page_breaks:
            if offset >= start:
                page = next_page
        return page


@dataclass
class _Piece:
    text: str
    page: int | None
    joiner: str
    overlappable: bool


@dataclass
class _Builder:
    max_chars: int
    pieces: list[_Piece] = field(default_factory=list)
    fresh: int = 0
    major: str | None = None
    sections: list[str | None] = field(default_factory=list)

    @property
    def section(self) -> str | None:
        """Deepest section path shared by every fresh piece of the chunk."""
        paths = [section.split(SECTION_SEPARATOR) if section else [] for section in self.sections]
        if not paths:
            return self.major
        common: list[str] = []
        for parts in zip(*paths):
            if any(part != parts[0] for part in parts):
                break
            common.append(parts[0])
        return SECTION_SEPARATOR.join(common) or self.major

    @property
    def length(self) -> int:
        return len(self.render())

    def render(self) -> str:
        out = ""
        for index, piece in enumerate(self.pieces):
            out += piece.text if index == 0 else piece.joiner + piece.text
        return out

    def fits(self, piece: _Piece) -> bool:
        if not self.pieces:
            return len(piece.text) <= self.max_chars
        return self.length + len(piece.joiner) + len(piece.text) <= self.max_chars

    def add(self, piece: _Piece, section: str | None) -> None:
        self.pieces.append(piece)
        self.sections.append(section)
        self.fresh += 1


def chunk_text(
    text: str,
    *,
    target_chars: int = 2000,
    max_chars: int = 2500,
    overlap_sentences: int = 2,
    text_format: TextFormat = "plain",
) -> list[TextChunk]:
    """Chunk a single text without page information."""
    return chunk_pages(
        [PageText(page_number=None, text=text or "")],
        target_chars=target_chars,
        max_chars=max_chars,
        overlap_sentences=overlap_sentences,
        text_format=text_format,
    )


def chunk_pages(
    pages: Iterable[PageText],
    *,
    target_chars: int = 2000,
    max_chars: int = 2500,
    overlap_sentences: int = 2,
    text_format: TextFormat = "plain",
) -> list[TextChunk]:
    max_chars = max(200, max_chars)
    target_chars = max(100, min(target_chars, max_chars))
    overlap_sentences = max(0, overlap_sentences)

    page_list = [page for page in pages if page.text and page.text.strip()]
    if not page_list:
        return []

    units = _build_units(page_list, text_format)
    return _pack(units, target_chars=target_chars, max_chars=max_chars, overlap_sentences=overlap_sentences)


# ---------------------------------------------------------------------------
# Page cleanup: running headers / footers
# ---------------------------------------------------------------------------


def _exact(line: str) -> str:
    line = _MD_HEADING.sub(r"\2", line.strip())
    return re.sub(r"\s+", " ", line.lower()).strip(" *_")


def _normalize(line: str) -> str:
    """Digit-insensitive form, so "Page 12 of 80" matches across pages."""
    return re.sub(r"\d+", "#", _exact(line))


def _is_marker(norm: str) -> bool:
    """Page numbers and decorative glyphs ("63", "≡", "# | #")."""
    return len(norm) <= 12 and not re.search(r"[a-z]", norm)


def _zone_indices(lines: list[str]) -> tuple[list[int], list[int]]:
    filled = [index for index, line in enumerate(lines) if line.strip()]
    return filled[:_ZONE_LINES], filled[-_ZONE_LINES:][::-1]


def _strip_running_lines(pages: list[PageText]) -> list[tuple[list[str], str | None]]:
    """Remove repeated header/footer lines; return (body lines, section path) per page."""
    split = [page.text.splitlines() for page in pages]
    zone_exact: list[set[str]] = []
    zone_norms: list[set[str]] = []
    for lines in split:
        top, bottom = _zone_indices(lines)
        zone_exact.append({_exact(lines[i]) for i in top + bottom})
        zone_norms.append({_normalize(lines[i]) for i in top + bottom})

    exact_counts = Counter(text for texts in zone_exact for text in texts)
    counts = Counter(norm for norms in zone_norms for norm in norms)
    total = len(pages)
    global_min = max(3, int(total * _GLOBAL_RUNNING_SHARE))
    path_exclude_min = max(3, int(total * _PATH_EXCLUDE_SHARE))

    def is_global(line: str, *, in_zone: bool = True) -> bool:
        if exact_counts[_exact(line)] >= global_min:
            return True
        return in_zone and len(line.strip()) <= 60 and counts[_normalize(line)] >= global_min

    result: list[tuple[list[str], str | None]] = []
    for position, lines in enumerate(split):
        neighbours: set[str] = set()
        if position > 0:
            neighbours |= zone_exact[position - 1]
        if position + 1 < total:
            neighbours |= zone_exact[position + 1]

        def running(line: str) -> bool:
            norm = _normalize(line)
            if not norm or _is_marker(norm):
                return True
            return is_global(line) or (_exact(line) in neighbours and len(norm) <= 120)

        removed: set[int] = set()
        path: list[str] = []
        top, bottom = _zone_indices(lines)
        for zone in (top, bottom):
            zone_labels: list[tuple[int, str]] = []
            for index in zone:
                if not running(lines[index]):
                    break
                removed.add(index)
                norm = _normalize(lines[index])
                label = _MD_HEADING.sub(r"\2", lines[index].strip()).strip(" *_")
                exact_count = exact_counts[_exact(lines[index])]
                # A repeated line whose number changes per page is a page-numbered footer.
                page_numbered = counts[norm] >= global_min and counts[norm] > exact_count
                document_wide = exact_count >= path_exclude_min or page_numbered
                if not _is_marker(norm) and not document_wide:
                    zone_labels.append((index, label))
            for _index, label in sorted(zone_labels):
                if label not in path:
                    path.append(label)

        # Extractors sometimes place footer text between table cells, so global lines go everywhere.
        body = [
            line
            for index, line in enumerate(lines)
            if index not in removed
            and not (line.strip() and len(line.strip()) <= 60 and is_global(line, in_zone=False))
        ]
        section = SECTION_SEPARATOR.join(path)[:MAX_SECTION_CHARS] or None
        result.append((body, section))
    return result


# ---------------------------------------------------------------------------
# Units: paragraphs, headings, tables
# ---------------------------------------------------------------------------


_HEADER_SEPARATOR = re.compile(r"\s*(?:[}|›>»/]|\s-\s|\s–\s)\s*")


_EDGE_GLYPHS = re.compile(r"^[^\w(\"'“‘€$£]+|[^\w)\"'”’%.]+$")


def _header_parts(header: str) -> list[str]:
    parts = [_EDGE_GLYPHS.sub("", part) for part in _HEADER_SEPARATOR.split(header.replace("\n", " | "))]
    return [part for part in parts if part and not _is_marker(_normalize(part))]


def _header_sections(pages: list[PageText]) -> list[str | None]:
    """Section path per page from extractor headers, minus parts repeated on almost every page."""
    parts_per_page = [_header_parts(page.header) if page.header else [] for page in pages]
    with_header = sum(1 for parts in parts_per_page if parts)
    counts = Counter(_normalize(part) for parts in parts_per_page for part in set(parts))
    document_wide = {
        norm for norm, count in counts.items() if with_header >= 5 and count >= with_header * _PATH_EXCLUDE_SHARE
    }
    sections: list[str | None] = []
    for parts in parts_per_page:
        kept = [part for part in parts if _normalize(part) not in document_wide]
        sections.append(SECTION_SEPARATOR.join(kept)[:MAX_SECTION_CHARS] or None)
    return sections


def _build_units(pages: list[PageText], text_format: TextFormat) -> list[_Unit]:
    cleaned = _strip_running_lines(pages) if len(pages) > 1 else [(pages[0].text.splitlines(), None)]
    if any(page.header for page in pages):
        hints = _header_sections(pages)
        cleaned = [(lines, hint or detected) for (lines, detected), hint in zip(cleaned, hints)]
    units: list[_Unit] = []
    heading_stack: list[tuple[int, str]] = []
    previous_running: str | None = None

    for page, (lines, running_section) in zip(pages, cleaned):
        # A new running header means a new chapter: headings from the previous one no longer apply.
        if running_section and running_section != previous_running:
            heading_stack.clear()
        previous_running = running_section or previous_running
        if text_format == "markdown":
            page_units = _markdown_units(lines, page.page_number, running_section, heading_stack)
        else:
            page_units = _plain_units(lines, page.page_number, running_section)
        if not page_units:
            continue
        first = page_units[0]
        if units and _continues(units[-1], first):
            previous = units[-1]
            joiner = "" if previous.text.endswith("-") else " "
            previous.page_breaks.append((len(previous.text) + len(joiner), first.page))
            previous.text += joiner + first.text
            page_units = page_units[1:]
        units.extend(page_units)
    return units


def _continues(previous: _Unit, current: _Unit) -> bool:
    """A paragraph cut by a page break: no terminal punctuation, next starts lowercase."""
    return (
        previous.kind == "paragraph"
        and current.kind == "paragraph"
        and not _TERMINAL.search(previous.text)
        and current.text[:1].islower()
    )


def _line_width(lines: list[str]) -> int:
    lengths = sorted(len(line.strip()) for line in lines if line.strip())
    if not lengths:
        return 0
    return lengths[int(len(lengths) * 0.9) - 1 if len(lengths) > 1 else 0]


def _reflow(lines: list[str]) -> list[str]:
    """Join hard-wrapped lines into paragraphs; keep real breaks, bullets and short lines."""
    width = _line_width(lines)
    paragraphs: list[str] = []
    current = ""
    previous = ""

    for raw in lines:
        line = raw.strip()
        if not line:
            if current:
                paragraphs.append(current)
            current, previous = "", ""
            continue
        if not current:
            current, previous = line, line
            continue

        ends_sentence = bool(_TERMINAL.search(previous))
        short = width and len(previous) < width * 0.75
        if _BULLET.match(line) or (ends_sentence and short):
            paragraphs.append(current)
            current = line
        elif short and not ends_sentence and len(previous) < width * 0.6:
            current += "\n" + line
        elif previous.endswith("-") and line[:1].islower():
            current += line
        else:
            current += " " + line
        previous = line

    if current:
        paragraphs.append(current)
    return [paragraph for paragraph in paragraphs if paragraph.strip()]


def _plain_units(lines: list[str], page: int | None, section: str | None) -> list[_Unit]:
    return [_Unit(kind="paragraph", text=text, page=page, section=section, major=section) for text in _reflow(lines)]


def _markdown_units(
    lines: list[str],
    page: int | None,
    running_section: str | None,
    heading_stack: list[tuple[int, str]],
) -> list[_Unit]:
    units: list[_Unit] = []
    buffer: list[str] = []
    table: list[str] = []

    def path(max_level: int = 6) -> str | None:
        parts = running_section.split(SECTION_SEPARATOR) if running_section else []
        parts += [title for level, title in heading_stack if level <= max_level and title not in parts]
        return SECTION_SEPARATOR.join(parts)[:MAX_SECTION_CHARS] or None

    def unit(kind: Literal["paragraph", "heading", "table"], text: str, level: int = 0) -> _Unit:
        return _Unit(kind=kind, text=text, page=page, section=path(), major=path(_MAJOR_HEADING_LEVEL), level=level)

    def flush_text() -> None:
        if buffer:
            units.extend(unit("paragraph", text) for text in _reflow(buffer))
            buffer.clear()

    def flush_table() -> None:
        if table:
            units.append(unit("table", "\n".join(table)))
            table.clear()

    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()
        if _MD_IMAGE.match(stripped):
            continue
        if stripped.startswith("|"):
            flush_text()
            table.append(stripped)
            continue
        flush_table()
        heading = _MD_HEADING.match(stripped)
        if heading:
            flush_text()
            level = len(heading.group(1))
            title = heading.group(2).strip(" *_")
            if not title:
                continue
            heading_stack[:] = [entry for entry in heading_stack if entry[0] < level]
            heading_stack.append((level, title))
            units.append(unit("heading", stripped, level))
            continue
        buffer.append(line)

    flush_text()
    flush_table()
    return units


# ---------------------------------------------------------------------------
# Sentences and packing
# ---------------------------------------------------------------------------


def split_sentences(text: str) -> list[str]:
    parts = _SENTENCE_BOUNDARY.split(text.strip())
    sentences: list[str] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if sentences and _ends_with_abbreviation(sentences[-1]):
            sentences[-1] = f"{sentences[-1]} {part}"
        else:
            sentences.append(part)
    return sentences


def _ends_with_abbreviation(sentence: str) -> bool:
    last = sentence.rsplit(None, 1)[-1].rstrip(".").lower()
    return last in _ABBREVIATIONS or (len(last) == 1 and last.isalpha())


def _split_words(text: str, max_chars: int) -> list[str]:
    pieces: list[str] = []
    current = ""
    for word in text.split():
        while len(word) > max_chars:
            if current:
                pieces.append(current)
                current = ""
            pieces.append(word[:max_chars])
            word = word[max_chars:]
        candidate = f"{current} {word}" if current else word
        if len(candidate) <= max_chars:
            current = candidate
        else:
            pieces.append(current)
            current = word
    if current:
        pieces.append(current)
    return pieces


def _unit_pieces(unit: _Unit, max_chars: int) -> list[_Piece]:
    """Break a unit into the smallest pieces the packer may split between."""
    first_joiner = "\n\n"
    if unit.kind == "heading":
        return [_Piece(text=unit.text[:max_chars], page=unit.page, joiner=first_joiner, overlappable=False)]
    if unit.kind == "table":
        rows = unit.text.split("\n")
        return [
            _Piece(
                text=row[:max_chars], page=unit.page, joiner=first_joiner if index == 0 else "\n", overlappable=False
            )
            for index, row in enumerate(rows)
        ]

    pieces: list[_Piece] = []
    cursor = 0
    for sentence in split_sentences(unit.text) or [unit.text]:
        found = unit.text.find(sentence, cursor)
        cursor = found if found >= 0 else cursor
        page = unit.page_at(cursor)
        cursor += len(sentence)
        for part in _split_words(sentence, max_chars) if len(sentence) > max_chars else [sentence]:
            pieces.append(_Piece(text=part, page=page, joiner=" ", overlappable=True))
    if pieces:
        pieces[0].joiner = first_joiner
    return pieces


def _table_header(unit: _Unit) -> list[str]:
    rows = unit.text.split("\n")
    if len(rows) >= 2 and _MD_TABLE_SEPARATOR.match(rows[1]):
        return rows[:2]
    return []


def _pack(units: list[_Unit], *, target_chars: int, max_chars: int, overlap_sentences: int) -> list[TextChunk]:
    chunks: list[TextChunk] = []
    builder = _Builder(max_chars=max_chars)
    min_section_chunk = min(300, target_chars // 4)
    overlap_budget = int(max_chars * _MAX_OVERLAP_SHARE)

    def emit() -> list[_Piece]:
        if builder.fresh == 0:
            builder.pieces.clear()
            return []
        content = builder.render().strip()
        pages = [piece.page for piece in builder.pieces if piece.page is not None]
        chunks.append(
            TextChunk(
                chunk_index=len(chunks),
                content=content,
                section=builder.section,
                page_start=min(pages) if pages else None,
                page_end=max(pages) if pages else None,
                token_count=max(1, len(content.split())),
            )
        )
        tail = builder.pieces
        builder.pieces = []
        builder.sections = []
        builder.fresh = 0
        return tail

    def start_with_overlap(tail: list[_Piece], next_piece: _Piece) -> None:
        if overlap_sentences <= 0:
            return
        carried: list[_Piece] = []
        for piece in reversed(tail):
            if not piece.overlappable or len(carried) >= overlap_sentences:
                break
            if sum(len(p.text) + 1 for p in carried) + len(piece.text) > overlap_budget:
                break
            carried.insert(0, piece)
        if not carried:
            return
        overlap = [_Piece(text=p.text, page=p.page, joiner=" ", overlappable=True) for p in carried]
        size = len(" ".join(p.text for p in overlap)) + len(next_piece.joiner) + len(next_piece.text)
        if size <= max_chars:
            builder.pieces = overlap
            builder.fresh = 0

    for unit in units:
        major_changed = builder.pieces and unit.major != builder.major
        if major_changed or (unit.kind == "heading" and unit.level <= _MAJOR_HEADING_LEVEL):
            if builder.fresh and builder.length >= min_section_chunk:
                emit()
            elif not builder.fresh:
                builder.pieces.clear()
            else:
                # Too short to stand alone (e.g. just a chapter title): label by what follows.
                builder.sections = [unit.major] * len(builder.sections)
        builder.major = unit.major

        pieces = _unit_pieces(unit, max_chars)
        header = _table_header(unit) if unit.kind == "table" else []
        unit_length = sum(len(p.text) + len(p.joiner) for p in pieces)
        fits_whole = builder.length + unit_length <= max_chars
        if builder.fresh and not fits_whole and builder.length >= target_chars * 0.6 and unit_length <= max_chars:
            start_with_overlap(emit(), pieces[0])

        for index, piece in enumerate(pieces):
            if builder.fresh and (builder.length >= target_chars or not builder.fits(piece)):
                tail = emit()
                if unit.kind == "table" and header and index >= len(header):
                    builder.pieces = [
                        _Piece(text=row, page=unit.page, joiner="\n", overlappable=False) for row in header
                    ]
                    if not builder.fits(piece):
                        builder.pieces = []
                else:
                    start_with_overlap(tail, piece)
            if not builder.fits(piece):
                builder.pieces = []
                builder.fresh = 0
            builder.add(piece, unit.section)

    emit()
    return chunks


def chunk_stats(chunks: list[TextChunk]) -> dict[str, float]:
    lengths = [len(chunk.content) for chunk in chunks] or [0]
    return {
        "chunks": len(chunks),
        "median_chars": statistics.median(lengths),
        "max_chars": max(lengths),
    }
