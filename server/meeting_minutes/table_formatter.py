from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Any

from .pdf_extractor import ExtractedPage


ENGINE_VERSION = "coordinate-table-v1"
PERSON_TABLE_ENGINE_VERSION = "coordinate-person-table-v2"


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


def _render_html(rows: list[list[str]], has_header: bool = True) -> str:
    if not rows:
        return ""
    parts = ["<table>"]
    for row_index, row in enumerate(rows):
        tag = "th" if has_header and row_index == 0 else "td"
        parts.append("<tr>")
        for cell in row:
            parts.append(f"<{tag}>{html.escape(cell)}</{tag}>")
        parts.append("</tr>")
    parts.append("</table>")
    return "".join(parts)


def _norm(value: str) -> str:
    return re.sub(r"\s+", "", value or "")


def _non_empty(row: list[str]) -> list[str]:
    return [cell.strip() for cell in row if cell.strip()]


def _is_number_cell(value: str) -> bool:
    return bool(re.fullmatch(r"\d+\s*番", _norm(value)))


def _name_candidates(speakers: list[Any]) -> set[str]:
    names: set[str] = set()
    for speaker in speakers:
        value = getattr(speaker, "speaker_name", "") or ""
        normalized = _norm(str(value))
        if normalized:
            names.add(normalized)
    return names


ROLE_TOKENS = (
    "市長",
    "副市長",
    "教育長",
    "部長",
    "次長",
    "課長",
    "局長",
    "消防長",
    "会計管理者",
    "事務局長",
    "病院事業管理者",
    "代表監査委員",
    "委員長",
    "副委員長",
    "理事",
    "監",
    "管理者",
)


def _split_role_name_scored(cells: list[str], names: set[str]) -> tuple[int, str, str] | None:
    values = _non_empty(cells)
    if len(values) < 2:
        return None
    best: tuple[int, str, str] | None = None
    for index in range(1, len(values)):
        role = _norm("".join(values[:index]))
        name = _norm("".join(values[index:]))
        if not role or not name:
            continue
        score = 0
        if role in ROLE_TOKENS:
            score += 7
        elif any(role.endswith(token) for token in ROLE_TOKENS):
            score += 6
        elif any(token in role for token in ROLE_TOKENS):
            score += 4
        if name in names:
            score += 8
        if 4 <= len(name) <= 5:
            score += 6
        elif len(name) == 3:
            score += 4
        elif len(name) == 2:
            score += 1
        elif len(name) == 6:
            score += 2
        if len(role) <= 16:
            score += 1
        candidate = (score, role, name)
        if best is None or candidate[0] > best[0]:
            best = candidate
    if not best or best[0] < 7:
        return None
    return best


def _split_role_name(cells: list[str], names: set[str]) -> tuple[str, str] | None:
    parsed = _split_role_name_scored(cells, names)
    if not parsed:
        return None
    return parsed[1], parsed[2]


def _compact_number_roster(rows: list[list[str]]) -> list[list[str]] | None:
    compacted: list[list[str]] = [["番号", "氏名", "番号", "氏名"]]
    matched = 0
    for row in rows:
        values = _non_empty(row)
        if len(values) < 3:
            continue
        entries: list[tuple[str, str]] = []
        index = 0
        while index + 2 < len(values):
            if _is_number_cell(values[index]):
                name = _norm(values[index + 1] + values[index + 2])
                if name:
                    entries.append((_norm(values[index]), name))
                    index += 3
                    continue
            index += 1
        if entries:
            matched += len(entries)
            for pair_index in range(0, len(entries), 2):
                left = entries[pair_index]
                right = entries[pair_index + 1] if pair_index + 1 < len(entries) else ("", "")
                compacted.append([left[0], left[1], right[0], right[1]])
    return compacted if matched >= 4 else None


def _compact_role_roster(rows: list[list[str]], names: set[str]) -> list[list[str]] | None:
    joined_table = "".join("".join(_non_empty(row)) for row in rows)
    if "出席委員" in joined_table or "出席議員" in joined_table:
        return None
    compacted: list[list[str]] = [["役職", "氏名", "役職", "氏名"]]
    matched = 0
    for row in rows:
        values = _non_empty(row)
        if len(values) < 4 or any(_is_number_cell(value) for value in values):
            continue
        candidates: list[tuple[int, list[tuple[str, str]]]] = []

        def walk(start: int, score: int, entries: list[tuple[str, str]]) -> None:
            if start >= len(values):
                candidates.append((score, entries))
                return
            for end in range(start + 2, min(len(values), start + 4) + 1):
                parsed = _split_role_name_scored(values[start:end], names)
                if parsed:
                    walk(end, score + parsed[0], entries + [(parsed[1], parsed[2])])

        walk(0, 0, [])
        if not candidates:
            continue
        entries = max(candidates, key=lambda item: (len(item[1]), item[0]))[1]
        if entries:
            matched += len(entries)
            left = entries[0]
            right = entries[1] if len(entries) > 1 else ("", "")
            compacted.append([left[0], left[1], right[0], right[1]])
    return compacted if matched >= 4 else None


def _replace_table(table: FormattedTable, rows: list[list[str]], caption: str, confidence: float) -> FormattedTable:
    search_parts = [caption]
    for row in rows:
        search_parts.append(" ".join(cell for cell in row if cell))
    return FormattedTable(
        table_key=table.table_key,
        page=table.page,
        caption=caption,
        rows=rows,
        html=_render_html(rows, has_header=True),
        search_text="\n".join(search_parts),
        confidence=confidence,
    )


def refine_person_roster_tables(tables: list[FormattedTable], speakers: list[Any]) -> list[FormattedTable]:
    """Normalize attendance/executive roster tables using speaker names.

    Coordinate extraction splits Japanese names and titles easily. This pass is
    intentionally narrow: it only rewrites tables that clearly look like member
    number rosters or role/name rosters.
    """
    names = _name_candidates(speakers)
    refined: list[FormattedTable] = []
    for table in tables:
        number_rows = _compact_number_roster(table.rows)
        if number_rows:
            refined.append(_replace_table(table, number_rows, f"出席者番号名簿 {table.table_key}", 0.86))
            continue
        role_rows = _compact_role_roster(table.rows, names)
        if role_rows:
            refined.append(_replace_table(table, role_rows, f"役職者名簿 {table.table_key}", 0.82))
            continue
        refined.append(table)
    return refined


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
