"""Conservative collection and ranking policy shared by dictionary and search."""
from __future__ import annotations

import re
import unicodedata
from itertools import combinations

MAX_SEARCH_ALTERNATIVES = 5
POLICY_VERSION = "quality-first-2026-09-26"

CURATED_SYNONYM_GROUPS: list[tuple[int, list[str]]] = [
    (18, ["老人", "お年寄り", "高齢者", "年寄り", "年寄", "高齢の方", "老年者"]),
    (18, ["マイナンバー", "個人番号"]),
    (18, ["障害者", "障がい者", "障害のある人", "障がいのある人"]),
    (18, ["子ども", "こども", "子供"]),
    (18, ["保育園", "保育所"]),
    (18, ["認定こども園", "認定子ども園"]),
    (18, ["ごみ", "ゴミ"]),
    (18, ["空き家", "空家"]),
    (18, ["罹災証明書", "り災証明書"]),
    (18, ["養鱒場", "養ます場"]),
    (18, ["空き家対策", "空家対策"]),
    (18, ["障害福祉", "障がい福祉"]),
    (18, ["子育て支援", "子育支援"]),
    (18, ["申請手続き", "申請手続"]),
]
CURATED_PAIRS = {
    frozenset((left, right))
    for _, terms in CURATED_SYNONYM_GROUPS
    for left, right in combinations(terms, 2)
}

# These are spelling variants, not broader/narrower concepts or shared topics.
SPELLING_VARIANTS = (
    ("お年寄り", "お年寄", "年寄り", "年寄"),
    ("子ども", "子供", "こども"),
    ("障害", "障がい"),
    ("空き家", "空家"),
    ("ごみ", "ゴミ"),
    ("養鱒", "養ます"),
    ("手続き", "手続"),
    ("申し込み", "申込み", "申込"),
)
SPELLING_PATTERNS = tuple(
    (re.compile("|".join(re.escape(variant) for variant in sorted(variants, key=len, reverse=True))), variants[0])
    for variants in SPELLING_VARIANTS
)
MUNICIPAL_TERMS = (
    "美祢", "秋吉台", "秋芳洞", "弁天池", "養鱒", "養ます", "高齢", "介護",
    "福祉", "障害", "障がい", "子ども", "子供", "こども", "保育", "学校",
    "住民", "戸籍", "個人番号", "マイナンバー", "税", "公共交通", "地域交通",
    "防災", "避難", "消防", "空き家", "空家", "ごみ", "ゴミ", "廃棄物",
    "水道", "観光", "農業", "林業", "水産", "条例", "地方自治", "地方公務員",
)


def spelling_key(term: str) -> str:
    value = re.sub(r"\s+", "", unicodedata.normalize("NFKC", term)).lower()
    for pattern, replacement in SPELLING_PATTERNS:
        value = pattern.sub(replacement, value)
    return value


def is_spelling_variant(left: str, right: str) -> bool:
    return left != right and spelling_key(left) == spelling_key(right)


def is_municipal_term(term: str) -> bool:
    return any(keyword in term for keyword in MUNICIPAL_TERMS)


def search_priority(left: str, right: str, priority: int, source: str) -> int:
    """Never promote topic extraction or low-confidence external evidence."""
    if source == "manual":
        return max(priority, 20)
    if source == "curated":
        # Keep old broad seed pairs stored, but do not expand them as synonyms.
        return max(priority, 18) if frozenset((left, right)) in CURATED_PAIRS else min(priority, 7)
    if source not in {"wiktionary", "wikidata", "wikipedia", "internet", "wordnet"} or priority < 8:
        return priority
    if is_spelling_variant(left, right):
        return max(priority, 16)
    if is_municipal_term(left) or is_municipal_term(right):
        return max(priority, 14)
    if source == "wiktionary":
        return max(priority, 12)
    return priority
