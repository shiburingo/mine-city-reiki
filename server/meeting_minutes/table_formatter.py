from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Any

from .pdf_extractor import ExtractedPage


ENGINE_VERSION = "coordinate-table-v1"


@dataclass
class FormattedTable:
    table_key: str
    page: int
    caption: str
    rows: list[list[str]]
    html: str
    search_text: str
    confidence: float


def _row_key(top: float, tolerance: float = 5.0) -> int:
    return round(top / tolerance)


def _cluster_x(words: list[dict[str, Any]], tolerance: float = 34.0) -> list[float]:
    clusters: list[float] = []
    for word in sorted(words, key=lambda w: float(w.get("x0") or 0)):
        x0 = float(word.get("x0") or 0)
        if not clusters or x0 - clusters[-1] > tolerance:
            clusters.append(x0)
    return clusters


def _assign_column(x0: float, clusters: list[float]) -> int:
    if not clusters:
        return 0
    return min(range(len(clusters)), key=lambda idx: abs(float(x0) - clusters[idx]))


def _render_html(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    parts = ["<table>"]
    for row_index, row in enumerate(rows):
        tag = "th" if row_index == 0 else "td"
        parts.append("<tr>")
        for cell in row:
            parts.append(f"<{tag}>{html.escape(cell)}</{tag}>")
        parts.append("</tr>")
    parts.append("</table>")
    return "".join(parts)


def _flush_table(table_key: str, page_no: int, rows: list[list[str]], caption_prefix: str) -> FormattedTable | None:
    clean_rows = [[re.sub(r"\s+", " ", cell).strip() for cell in row] for row in rows]
    clean_rows = [row for row in clean_rows if any(row)]
    if len(clean_rows) < 3 or max(len(row) for row in clean_rows) < 3:
        return None
    width = max(len(row) for row in clean_rows)
    normalized = [row + [""] * (width - len(row)) for row in clean_rows]
    caption = f"{caption_prefix} {table_key}".strip()
    search_parts = [caption]
    for row in normalized:
        search_parts.append(" ".join(cell for cell in row if cell))
    return FormattedTable(
        table_key=table_key,
        page=page_no,
        caption=caption,
        rows=normalized,
        html=_render_html(normalized),
        search_text="\n".join(search_parts),
        confidence=0.62,
    )


def extract_coordinate_tables(pages: list[ExtractedPage], document_key: str) -> list[FormattedTable]:
    """Detect simple text-coordinate tables from PDF words.

    The Mine City PDFs often do not expose vector table objects. This engine
    keeps conservative coordinate tables so searchable tabular data is not lost.
    """
    tables: list[FormattedTable] = []
    for page in pages:
        grouped: dict[int, list[dict[str, Any]]] = {}
        for word in page.words:
            text = str(word.get("text") or "").strip()
            if not text:
                continue
            grouped.setdefault(_row_key(float(word.get("top") or 0)), []).append(word)
        candidate_rows: list[list[str]] = []
        seq = 1
        for _key, words in sorted(grouped.items(), key=lambda item: min(float(w.get("top") or 0) for w in item[1])):
            ordered = sorted(words, key=lambda w: float(w.get("x0") or 0))
            if len(ordered) < 4:
                table = _flush_table(f"{document_key}-p{page.page}-t{seq}", page.page, candidate_rows, "PDF座標表")
                if table:
                    tables.append(table)
                    seq += 1
                candidate_rows = []
                continue
            clusters = _cluster_x(ordered)
            if len(clusters) < 3:
                table = _flush_table(f"{document_key}-p{page.page}-t{seq}", page.page, candidate_rows, "PDF座標表")
                if table:
                    tables.append(table)
                    seq += 1
                candidate_rows = []
                continue
            cells = [""] * len(clusters)
            for word in ordered:
                col = _assign_column(float(word.get("x0") or 0), clusters)
                text = str(word.get("text") or "")
                cells[col] = f"{cells[col]} {text}".strip() if cells[col] else text
            candidate_rows.append(cells)
        table = _flush_table(f"{document_key}-p{page.page}-t{seq}", page.page, candidate_rows, "PDF座標表")
        if table:
            tables.append(table)
    return tables

