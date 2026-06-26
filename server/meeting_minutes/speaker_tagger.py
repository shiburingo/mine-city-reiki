from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from .pdf_extractor import ExtractedLine


ENGINE_VERSION = "speaker-rules-v1"
SPEAKER_RE = re.compile(r"^○\s*(?P<title>[^（(]{1,40})[（(](?P<name>[^）)]{1,40})(?:君|さん|氏)?[）)]\s*(?P<body>.*)$")
PRINTED_PAGE_NUMBER_RE = re.compile(r"^[－ー―−\-–—]\s*[0-9０-９]{1,4}\s*[－ー―−\-–—]$")
SENTENCE_END_RE = re.compile(r"[。！？）」』]$")
STRUCTURAL_LINE_RE = re.compile(
    r"^(日程第|〔|【|（|第[0-9０-９一二三四五六七八九十]+[、 　]|[0-9０-９]+[、.．)]|[（(][0-9０-９一二三四五六七八九十]+[）)])"
)
ANSWERER_TITLES = (
    "市長",
    "副市長",
    "教育長",
    "部長",
    "次長",
    "課長",
    "所長",
    "局長",
    "消防長",
    "会計管理者",
    "監",
    "参事",
    "主幹",
    "室長",
    "支所長",
    "センター長",
)


@dataclass
class TaggedUtterance:
    order: int
    speaker_name: str
    speaker_title: str
    speaker_role: str
    speaker_group: str
    speech_type: str
    text: str
    page_start: int
    page_end: int
    position_top_start: float
    position_top_end: float
    confidence: float
    reason: str


def normalize_name(name: str) -> str:
    value = re.sub(r"(君|さん|氏)$", "", name or "")
    return re.sub(r"\s+", "", value)


def classify_speaker(title: str, name: str) -> tuple[str, str, float, str]:
    title = re.sub(r"\s+", "", title or "")
    if "議長" in title or "委員長" in title:
        return "chair", "議事進行", 0.98, "title includes chair alias"
    if "議会事務局" in title or "事務局" in title or "書記" in title:
        return "secretariat", "事務局", 0.95, "title includes secretariat alias"
    if re.search(r"\d+番|[一二三四五六七八九十]+番", title) or "議員" in title or title in {"委員", "部会長", "副委員長"}:
        return "questioner", "議員・委員", 0.9, "title indicates elected member or committee member"
    if any(token in title for token in ANSWERER_TITLES):
        return "answerer", "執行部", 0.9, "title indicates executive staff"
    return "unknown", "未分類", 0.45, "no rule matched"


def speech_type_from_role(role: str) -> str:
    if role == "questioner":
        return "question"
    if role == "answerer":
        return "answer"
    if role == "chair":
        return "proceeding"
    return "statement"


def is_printed_page_number(text: str) -> bool:
    return bool(PRINTED_PAGE_NUMBER_RE.fullmatch(re.sub(r"\s+", "", text or "")))


def should_keep_line_break(previous: str, current: str) -> bool:
    previous = previous.strip()
    current = current.strip()
    if not previous or not current:
        return True
    if STRUCTURAL_LINE_RE.match(current):
        return True
    if SENTENCE_END_RE.search(previous):
        return True
    return False


def normalize_body_lines(parts: list[object]) -> str:
    lines = [str(part).strip() for part in parts if str(part).strip()]
    if not lines:
        return ""
    normalized = lines[0]
    for line in lines[1:]:
        if should_keep_line_break(normalized, line):
            normalized = f"{normalized}\n{line}"
        else:
            normalized = f"{normalized}{line}"
    return normalized.strip()


def append_body_line(current: dict[str, object], text: str) -> None:
    parts = current["parts"]
    if not isinstance(parts, list):
        return
    if current.get("join_next") and parts and not SENTENCE_END_RE.search(str(parts[-1])):
        parts[-1] = f"{parts[-1]}{text}"
    else:
        parts.append(text)
    current["join_next"] = False


def tag_utterances(lines: Iterable[ExtractedLine]) -> list[TaggedUtterance]:
    utterances: list[TaggedUtterance] = []
    current: dict[str, object] | None = None

    def flush() -> None:
        nonlocal current
        if not current:
            return
        parts = current["parts"]
        body = normalize_body_lines(parts) if isinstance(parts, list) else ""
        if body:
            utterances.append(
                TaggedUtterance(
                    order=len(utterances) + 1,
                    speaker_name=str(current["speaker_name"]),
                    speaker_title=str(current["speaker_title"]),
                    speaker_role=str(current["speaker_role"]),
                    speaker_group=str(current["speaker_group"]),
                    speech_type=str(current["speech_type"]),
                    text=body,
                    page_start=int(current["page_start"]),
                    page_end=int(current["page_end"]),
                    position_top_start=float(current["position_top_start"]),
                    position_top_end=float(current["position_top_end"]),
                    confidence=float(current["confidence"]),
                    reason=str(current["reason"]),
                )
            )
        current = None

    for line in lines:
        text = line.text.strip()
        if is_printed_page_number(text):
            if current is not None:
                current["join_next"] = True
                current["page_end"] = line.page
                current["position_top_end"] = line.top
            continue
        match = SPEAKER_RE.match(text)
        if match:
            flush()
            title = re.sub(r"\s+", "", match.group("title"))
            name = normalize_name(match.group("name"))
            role, group, confidence, reason = classify_speaker(title, name)
            body = match.group("body").strip()
            current = {
                "speaker_name": name,
                "speaker_title": title,
                "speaker_role": role,
                "speaker_group": group,
                "speech_type": speech_type_from_role(role),
                "parts": [body] if body else [],
                "page_start": line.page,
                "page_end": line.page,
                "position_top_start": line.top,
                "position_top_end": line.top,
                "join_next": False,
                "confidence": confidence,
                "reason": reason,
            }
            continue
        if current is not None:
            append_body_line(current, text)
            current["page_end"] = line.page
            current["position_top_end"] = line.top
    flush()
    return utterances
