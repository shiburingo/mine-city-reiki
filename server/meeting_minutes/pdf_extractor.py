from __future__ import annotations

import hashlib
import io
import re
import urllib.request
from dataclasses import dataclass
from typing import Any

import pdfplumber


GAIJI_SEPARATOR_CHARS = "疑癡癘"
GAIJI_SEPARATOR_RE = re.compile(f"[{re.escape(GAIJI_SEPARATOR_CHARS)}]{{3,}}")
PDF_SEPARATOR_CHARS = "-‐‑‒–—―ー−─━"
PDF_SEPARATOR_PATTERN = f"[{re.escape(PDF_SEPARATOR_CHARS)}]{{6,}}"
SEPARATOR_LINE_RE = re.compile(PDF_SEPARATOR_PATTERN)
EMBEDDED_SEPARATOR_RE = re.compile(rf"(?<=[休憩散会閉会])({PDF_SEPARATOR_PATTERN})(?=(?:午前|午後|上会議))")


@dataclass
class ExtractedLine:
    page: int
    line_no: int
    text: str
    x0: float
    top: float


@dataclass
class ExtractedPage:
    page: int
    text: str
    lines: list[ExtractedLine]
    words: list[dict[str, Any]]


@dataclass
class ExtractedPdf:
    sha256: str
    page_count: int
    text: str
    pages: list[ExtractedPage]


def normalize_pdf_gaiji_text(text: str) -> str:
    """Replace custom-font gaiji runs used as divider lines with safe symbols."""
    if not text:
        return ""
    return GAIJI_SEPARATOR_RE.sub(lambda match: "-" * len(match.group(0)), str(text))


def normalize_extracted_text_layout(text: str) -> str:
    value = normalize_pdf_gaiji_text(text)
    return EMBEDDED_SEPARATOR_RE.sub(r"\n\1\n", value)


def is_separator_line(text: str) -> bool:
    value = re.sub(r"\s+", "", normalize_pdf_gaiji_text(text or ""))
    return bool(SEPARATOR_LINE_RE.fullmatch(value))


def download_pdf(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "mine-city-reiki-minutes/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return res.read()


def _line_key(top: float, tolerance: float = 4.0) -> int:
    return round(top / tolerance)


def _extract_lines(words: list[dict[str, Any]], page_no: int) -> list[ExtractedLine]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for word in words:
        grouped.setdefault(_line_key(float(word.get("top") or 0)), []).append(word)
    lines: list[ExtractedLine] = []
    for line_no, (_key, row_words) in enumerate(sorted(grouped.items(), key=lambda item: min(float(w.get("top") or 0) for w in item[1])), start=1):
        ordered = sorted(row_words, key=lambda w: float(w.get("x0") or 0))
        parts: list[str] = []
        prev_x1: float | None = None
        for word in ordered:
            text = normalize_pdf_gaiji_text(str(word.get("text") or ""))
            if not text:
                continue
            x0 = float(word.get("x0") or 0)
            if prev_x1 is not None and x0 - prev_x1 > 10:
                parts.append(" ")
            parts.append(text)
            prev_x1 = float(word.get("x1") or x0)
        line_text = normalize_extracted_text_layout(re.sub(r"\s+", " ", "".join(parts)).strip())
        if line_text:
            lines.append(
                ExtractedLine(
                    page=page_no,
                    line_no=line_no,
                    text=line_text,
                    x0=min(float(w.get("x0") or 0) for w in ordered),
                    top=min(float(w.get("top") or 0) for w in ordered),
                )
            )
    return lines


def extract_pdf_from_bytes(pdf_bytes: bytes) -> ExtractedPdf:
    sha = hashlib.sha256(pdf_bytes).hexdigest()
    pages: list[ExtractedPage] = []
    text_parts: list[str] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for idx, page in enumerate(pdf.pages, start=1):
            text = normalize_extracted_text_layout(page.extract_text(x_tolerance=1, y_tolerance=3) or "")
            words = page.extract_words(x_tolerance=1, y_tolerance=3, keep_blank_chars=False, use_text_flow=False) or []
            normalized_words = [
                {
                    "text": normalize_pdf_gaiji_text(str(word.get("text") or "")),
                    "x0": float(word.get("x0") or 0),
                    "x1": float(word.get("x1") or 0),
                    "top": float(word.get("top") or 0),
                    "bottom": float(word.get("bottom") or 0),
                }
                for word in words
            ]
            lines = _extract_lines(normalized_words, idx)
            pages.append(ExtractedPage(page=idx, text=text, lines=lines, words=normalized_words))
            if text:
                text_parts.append(text)
    return ExtractedPdf(sha256=sha, page_count=len(pages), text="\n".join(text_parts), pages=pages)


def extract_pdf_from_url(url: str) -> ExtractedPdf:
    return extract_pdf_from_bytes(download_pdf(url))
