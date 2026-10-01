"""Turn Mistral OCR markdown + HTML tables into search-friendly markdown."""

from __future__ import annotations

import re
from html.parser import HTMLParser

__all__ = ("html_table_to_markdown", "inline_tables", "strip_image_refs")

_TABLE_REF = re.compile(r"\[(tbl-[\w-]+\.(?:html|md))\]\(\1\)")
_IMAGE_REF = re.compile(r"!\[[^\]]*\]\([^)]*\)")


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._cell: list[str] | None = None
        self._colspan = 1

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self.rows.append([])
        elif tag in {"td", "th"}:
            if not self.rows:
                self.rows.append([])
            self._cell = []
            span = dict(attrs).get("colspan") or "1"
            self._colspan = int(span) if span.isdigit() else 1
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._cell is not None:
            text = re.sub(r"\s+", " ", "".join(self._cell)).strip().replace("|", "\\|")
            self.rows[-1].append(text)
            self.rows[-1].extend([""] * (self._colspan - 1))
            self._cell = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def html_table_to_markdown(html: str) -> str:
    """Flatten an HTML table to a markdown table (merged cells become empty cells)."""
    parser = _TableParser()
    parser.feed(html)
    rows = [row for row in parser.rows if any(cell for cell in row)]
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    lines = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
    lines += ["| " + " | ".join(row) + " |" for row in rows[1:]]
    return "\n".join(lines)


def inline_tables(markdown: str, tables: dict[str, tuple[str, str]]) -> str:
    """Replace ``[tbl-1.html](tbl-1.html)`` placeholders with markdown tables.

    ``tables`` maps table id to (format, content).
    """

    def replace(match: re.Match[str]) -> str:
        table = tables.get(match.group(1))
        if table is None:
            return ""
        table_format, content = table
        return html_table_to_markdown(content) if table_format == "html" else content.strip()

    return _TABLE_REF.sub(replace, markdown)


def strip_image_refs(markdown: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", _IMAGE_REF.sub("", markdown)).strip()
