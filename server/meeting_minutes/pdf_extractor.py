from __future__ import annotations

import hashlib
import io
import re
import urllib.request
from dataclasses import dataclass
from typing import Any

import pdfplumber


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
            text = str(word.get("text") or "")
            if not text:
                continue
            x0 = float(word.get("x0") or 0)
            if prev_x1 is not None and x0 - prev_x1 > 10:
                parts.append(" ")
            parts.append(text)
            prev_x1 = float(word.get("x1") or x0)
        line_text = re.sub(r"\s+", " ", "".join(parts)).strip()
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
            text = page.extract_text(x_tolerance=1, y_tolerance=3) or ""
            words = page.extract_words(x_tolerance=1, y_tolerance=3, keep_blank_chars=False, use_text_flow=False) or []
            normalized_words = [
                {
                    "text": str(word.get("text") or ""),
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

