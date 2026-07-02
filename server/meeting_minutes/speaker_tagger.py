from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from .pdf_extractor import ExtractedLine, is_separator_line, normalize_extracted_text_layout


ENGINE_VERSION = "speaker-rules-v9"
SPEAKER_RE = re.compile(r"^○\s*(?P<title>[^（(]{1,40})[（(](?P<name>[^）)]{1,40})(?:君|さん|氏)?[）)]\s*(?P<body>.*)$")
SPEAKER_NUMBER_TITLE_RE = re.compile(r"([0-9０-９]+|[一二三四五六七八九十]+)(番)?")
PRINTED_PAGE_NUMBER_RE = re.compile(r"^[－ー―−\-–—]\s*[0-9０-９]{1,4}\s*[－ー―−\-–—]$")
SENTENCE_END_RE = re.compile(r"[。！？）」』]$")
PROCEDURAL_LINE_END_RE = re.compile(r"(休憩|再開|散会|閉会)$")
STRUCTURAL_LINE_RE = re.compile(
    r"^(日程第|〔|【|（|第[0-9０-９一二三四五六七八九十]+[、 　]|[0-9０-９]+[、.．)]|[（(][0-9０-９一二三四五六七八九十]+[）)])"
)
ANSWERER_TITLES = (
    "市長",
    "副市長",
    "教育長",
    "上下水道事業管理者",
    "病院事業管理者",
    "事業管理者",
    "代表監査委員",
    "部長",
    "次長",
    "課長",
    "所長",
    "事務長",
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
EXTERNAL_ANSWERER_TITLES = (
    "参考人",
    "証人",
)
ANSWER_OPENING_RE = re.compile(
    r"^\s*(それでは、?|では、?|まず、?|ただいまの[^。]{0,40})?[^。！？\n]{0,40}"
    r"(お答え|御答え|ご答弁|答弁|回答|説明)(いたします|します|させていただきます|申し上げます)"
)
ANSWER_CONTEXT_RE = re.compile(r"(御質問|ご質問|質問|お尋ね|御指摘|ご指摘|お答え|御答え|答弁|回答|説明|申し上げ)")
QUESTION_CLOSING_RE = re.compile(
    r"(お答え|御答え|ご答弁|答弁|回答|説明)[^。！？\n]{0,40}"
    r"(いただいておきたい|いただきたい|願いたい|お願いしたい|求めたい)"
    r"|"
    r"(お伺い|伺い|お尋ね|質問|確認)[^。！？\n]{0,40}(したい|します|させていただきたい)"
)
REPORT_REQUEST_RE = re.compile(
    r"(報告を求め|報告.*お願いいたします|報告.*お願いをいたします|報告.*お願い申し上げます|進捗.*お願いいたします|説明を求め|説明.*お願いいたします|説明.*お願いをいたします|分科会長、お願いいたします|部会長、お願いいたします)"
)
REPORT_BODY_RE = re.compile(r"(報告させて|報告いた|御報告|ご報告|説明させて|説明いた|御説明|ご説明|進捗|分科会|部会|前回|協議|取組|取り組)")
REPORT_CLOSING_RE = re.compile(r"(私からの報告|報告は|報告を).{0,20}(以上|終わり)|以上.{0,20}(報告|御報告|ご報告)")


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
    if "議長" in title or "委員長" in title or "座長" in title:
        return "chair", "議事進行", 0.98, "title includes chair alias"
    if "議会事務局" in title or "書記" in title:
        return "secretariat", "事務局", 0.95, "title includes secretariat alias"
    if "事務局" in title:
        return "answerer", "執行部", 0.9, "non-council secretariat title is executive staff"
    if any(token in title for token in EXTERNAL_ANSWERER_TITLES):
        return "answerer", "参考人・証人", 0.9, "title indicates external testimony answerer"
    if SPEAKER_NUMBER_TITLE_RE.fullmatch(title) or "議員" in title or title.endswith("委員") or title in {"部会長", "副委員長"}:
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
    if role == "report":
        return "report"
    return "statement"


def looks_report_context(previous: TaggedUtterance | None, current: TaggedUtterance, next_item: TaggedUtterance | None) -> bool:
    if current.speaker_role in {"chair", "secretariat"}:
        return False
    title = current.speaker_title
    previous_text = previous.text if previous else ""
    next_text = next_item.text if next_item else ""
    if previous and previous.speaker_role == "chair" and REPORT_REQUEST_RE.search(previous_text):
        return True
    if current.speaker_role != "unknown":
        return False
    if REPORT_CLOSING_RE.search(current.text):
        return True
    if "分科会長" not in title and "部会長" not in title:
        return False
    if REPORT_BODY_RE.search(current.text) and "報告" in next_text:
        return True
    if REPORT_BODY_RE.search(current.text) and previous and previous.speaker_role == "chair":
        return True
    return False


def looks_answer_context(previous: TaggedUtterance | None, current: TaggedUtterance, next_item: TaggedUtterance | None) -> bool:
    if current.speaker_role != "unknown":
        return False
    if previous and previous.speaker_role == "questioner" and ANSWER_CONTEXT_RE.search(current.text):
        return True
    if next_item and next_item.speaker_role == "questioner" and ANSWER_CONTEXT_RE.search(current.text):
        return True
    return False


def looks_question_context(current: TaggedUtterance) -> bool:
    if current.speaker_role != "unknown":
        return False
    return bool(QUESTION_CLOSING_RE.search(current.text))


def looks_followup_question_context(utterances: list[TaggedUtterance], index: int) -> bool:
    current = utterances[index]
    if current.speaker_role != "unknown" or not current.speaker_name:
        return False
    start = max(0, index - 8)
    for previous in reversed(utterances[start:index]):
        if previous.speaker_name != current.speaker_name:
            continue
        if previous.speaker_role == "questioner":
            return True
        if previous.speaker_role not in {"answerer", "chair", "secretariat", "unknown"}:
            return False
    return False


def reclassify_contextual_utterances(utterances: list[TaggedUtterance]) -> list[TaggedUtterance]:
    for index, utterance in enumerate(utterances):
        previous = utterances[index - 1] if index > 0 else None
        next_item = utterances[index + 1] if index + 1 < len(utterances) else None
        if utterance.speaker_role not in {"chair", "secretariat"} and ANSWER_OPENING_RE.search(utterance.text):
            previous_role = utterance.speaker_role
            utterance.speaker_role = "answerer"
            if previous_role != "answerer":
                utterance.speaker_group = "執行部"
            utterance.speech_type = "answer"
            utterance.confidence = max(utterance.confidence, 0.88)
            utterance.reason = "opening phrase indicates answer"
        if looks_answer_context(previous, utterance, next_item):
            utterance.speaker_role = "answerer"
            utterance.speaker_group = "執行部"
            utterance.speech_type = "answer"
            utterance.confidence = max(utterance.confidence, 0.82)
            utterance.reason = "surrounding questioner utterance and body indicate answer"
        if looks_question_context(utterance):
            utterance.speaker_role = "questioner"
            utterance.speaker_group = "議員・委員"
            utterance.speech_type = "question"
            utterance.confidence = max(utterance.confidence, 0.82)
            utterance.reason = "closing phrase asks for answer"
        if looks_followup_question_context(utterances, index):
            utterance.speaker_role = "questioner"
            utterance.speaker_group = "議員・委員"
            utterance.speech_type = "question"
            utterance.confidence = max(utterance.confidence, 0.84)
            utterance.reason = "same speaker follows prior question after answer"
        if looks_report_context(previous, utterance, next_item):
            utterance.speaker_role = "report"
            utterance.speaker_group = "報告"
            utterance.speech_type = "report"
            utterance.confidence = max(utterance.confidence, 0.86)
            utterance.reason = "surrounding chair utterance and body indicate report"
    return utterances


def is_printed_page_number(text: str) -> bool:
    return bool(PRINTED_PAGE_NUMBER_RE.fullmatch(re.sub(r"\s+", "", text or "")))


def should_keep_line_break(previous: str, current: str) -> bool:
    previous = previous.strip()
    current = current.strip()
    if not previous or not current:
        return True
    previous_line = previous.splitlines()[-1].strip()
    if is_separator_line(previous_line) or is_separator_line(current):
        return True
    if PROCEDURAL_LINE_END_RE.search(previous_line):
        return True
    if STRUCTURAL_LINE_RE.match(current):
        return True
    if SENTENCE_END_RE.search(previous):
        return True
    return False


def normalize_body_lines(parts: list[object]) -> str:
    lines = [normalize_extracted_text_layout(str(part)).strip() for part in parts if str(part).strip()]
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
    return reclassify_contextual_utterances(utterances)
