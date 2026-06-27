from __future__ import annotations

import gzip
import itertools
import re
import shutil
import sqlite3
import tempfile
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable


WORDNET_VERSION = "1.1"
WORDNET_SQLITE_URL = "https://github.com/bond-lab/wnja/releases/download/v1.1/wnjpn.db.gz"
ENGINE_VERSION = "dictionary-engine-2026-06-28"

MIN_TERM_LEN = 2
MAX_TERM_LEN = 40
DOMAIN_MIN_COUNT = 2

DOMAIN_STOPWORDS = {
    "こと",
    "もの",
    "ため",
    "これ",
    "それ",
    "ここ",
    "ところ",
    "よう",
    "これら",
    "本市",
    "当該",
    "以下",
    "以上",
    "ただし",
}

LAW_SUFFIXES = ("条例", "規則", "要綱", "規程", "法律", "法", "附則", "別表")
TITLE_ALIASES = {
    "市長": ["市長部局", "執行部"],
    "副市長": ["執行部"],
    "教育長": ["教育委員会", "執行部"],
    "教育委員会事務局長": ["教育委員会", "事務局長", "執行部"],
    "農業委員会事務局長": ["農業委員会", "事務局長", "執行部"],
}


def normalize_term(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", "", value.strip().lower())


def is_good_term(value: str | None) -> bool:
    term = normalize_term(value)
    if len(term) < MIN_TERM_LEN or len(term) > MAX_TERM_LEN:
        return False
    if term in DOMAIN_STOPWORDS:
        return False
    if re.fullmatch(r"[\d０-９一二三四五六七八九十百千]+", term):
        return False
    if re.fullmatch(r"[a-z0-9_./:-]+", term):
        return False
    if not re.search(r"[\u3040-\u30ff\u3400-\u9fff]", term):
        return False
    return True


def add_pair(pairs: set[tuple[str, str]], left: str | None, right: str | None) -> None:
    a = normalize_term(left)
    b = normalize_term(right)
    if not a or not b or a == b:
        return
    if not is_good_term(a) or not is_good_term(b):
        return
    pairs.add((a, b))


def download_wordnet_sqlite(work_dir: Path, url: str = WORDNET_SQLITE_URL) -> Path:
    gz_path = work_dir / "wnjpn.db.gz"
    db_path = work_dir / "wnjpn.db"
    urllib.request.urlretrieve(url, gz_path)
    with gzip.open(gz_path, "rb") as src, db_path.open("wb") as dst:
        shutil.copyfileobj(src, dst)
    return db_path


def build_wordnet_pairs(db_path: Path, max_pairs: int = 30000) -> tuple[set[tuple[str, str]], dict[str, Any]]:
    pairs: set[tuple[str, str]] = set()
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            """
            SELECT s.synset, w.lemma
            FROM sense s
            JOIN word w ON w.wordid=s.wordid
            WHERE w.lang='jpn'
            ORDER BY s.synset, w.lemma
            """
        ).fetchall()
    finally:
        conn.close()

    synsets: dict[str, set[str]] = defaultdict(set)
    for synset, lemma in rows:
        term = normalize_term(str(lemma or ""))
        if is_good_term(term):
            synsets[str(synset)].add(term)

    used_synsets = 0
    for terms in synsets.values():
        if len(terms) < 2 or len(terms) > 6:
            continue
        used_synsets += 1
        for left, right in itertools.combinations(sorted(terms), 2):
            add_pair(pairs, left, right)
            if len(pairs) >= max_pairs:
                return pairs, {"wordnetVersion": WORDNET_VERSION, "synsetsUsed": used_synsets, "pairs": len(pairs), "truncated": True}
    return pairs, {"wordnetVersion": WORDNET_VERSION, "synsetsUsed": used_synsets, "pairs": len(pairs), "truncated": False}


def split_title_terms(value: str | None) -> list[str]:
    text = normalize_term(value)
    if not text:
        return []
    parts = re.split(r"(?:の|に関する|について|及び|並びに|又は|または|及|、|・|／|/|（|）|\(|\))", text)
    terms = [part for part in parts if is_good_term(part)]
    for suffix in LAW_SUFFIXES:
        if text.endswith(suffix) and len(text) > len(suffix) + 1:
            terms.append(text[: -len(suffix)])
    return list(dict.fromkeys(terms))


def build_domain_pairs(cur) -> tuple[set[tuple[str, str]], dict[str, Any]]:
    pairs: set[tuple[str, str]] = set()
    term_counts: Counter[str] = Counter()

    cur.execute("SELECT title, law_type, category_path FROM law_documents")
    documents = cur.fetchall() or []
    for row in documents:
        title = row.get("title") or ""
        law_type = row.get("law_type") or ""
        category_path = row.get("category_path") or ""
        title_terms = split_title_terms(title)
        for term in title_terms:
            term_counts[term] += 1
            add_pair(pairs, title, term)
        if law_type:
            add_pair(pairs, law_type, "例規")
            add_pair(pairs, title, law_type)
        for segment in re.split(r"[/>／｜|]+", category_path):
            add_pair(pairs, title, segment)

    cur.execute("SELECT article_title, text FROM law_articles")
    articles = cur.fetchall() or []
    for row in articles:
        for term in split_title_terms(row.get("article_title") or ""):
            term_counts[term] += 1
        body = row.get("text") or ""
        for term in re.findall(r"[一-龯ぁ-んァ-ヴー]{2,20}(?:制度|計画|委員会|職員|手当|給与|費用|管理|事業|施設|会計|議会|条例|規則)", body):
            normalized = normalize_term(term)
            if is_good_term(normalized):
                term_counts[normalized] += 1

    cur.execute("SELECT DISTINCT speaker_name, speaker_title, speaker_role FROM meeting_utterances")
    speakers = cur.fetchall() or []
    for row in speakers:
        name = row.get("speaker_name") or ""
        title = row.get("speaker_title") or ""
        role = row.get("speaker_role") or ""
        add_pair(pairs, name, title)
        if role == "questioner":
            add_pair(pairs, name, "議員")
            add_pair(pairs, name, "質問者")
        elif role == "answerer":
            add_pair(pairs, title, "答弁者")
            add_pair(pairs, title, "執行部")
        for alias in TITLE_ALIASES.get(title, []):
            add_pair(pairs, title, alias)

    repeated_terms = [term for term, count in term_counts.items() if count >= DOMAIN_MIN_COUNT and is_good_term(term)]
    for left, right in itertools.combinations(sorted(repeated_terms[:300]), 2):
        if left in right or right in left:
            add_pair(pairs, left, right)

    return pairs, {
        "documents": len(documents),
        "articles": len(articles),
        "speakers": len(speakers),
        "domainTerms": len(repeated_terms),
        "pairs": len(pairs),
    }


def insert_pairs(cur, pairs: Iterable[tuple[str, str]], source_type: str, source_version: str, priority: int) -> int:
    count = 0
    for canonical, synonym in sorted(set(pairs)):
        if canonical == synonym:
            continue
        cur.execute(
            """
            INSERT INTO law_synonyms
              (canonical_term, synonym_term, priority, is_active, source_type, source_version)
            VALUES (%s,%s,%s,1,%s,%s)
            ON DUPLICATE KEY UPDATE
              priority=GREATEST(priority, VALUES(priority)),
              is_active=1,
              source_type=IF(source_type='manual', source_type, VALUES(source_type)),
              source_version=IF(source_type='manual', source_version, VALUES(source_version))
            """,
            (canonical, synonym, priority, source_type, source_version),
        )
        count += 1
    return count


def build_hybrid_dictionary(
    cur,
    *,
    include_wordnet: bool = True,
    include_domain: bool = True,
    max_wordnet_pairs: int = 30000,
    progress: Callable[[str, int, int], None] | None = None,
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "operation": "dictionary-update",
        "engineVersion": ENGINE_VERSION,
        "includeWordnet": include_wordnet,
        "includeDomain": include_domain,
        "wordnetPairs": 0,
        "domainPairs": 0,
        "inserted": 0,
        "progressCurrent": 0,
        "progressTotal": int(include_wordnet) + int(include_domain) + 1,
        "progressLabel": "関連語辞書を準備しています",
    }

    cur.execute("DELETE FROM law_synonyms WHERE source_type IN ('wordnet','domain')")
    if progress:
        progress("既存の自動生成辞書を削除しました", 0, summary["progressTotal"])

    inserted = 0
    current = 0
    if include_wordnet:
        if progress:
            progress("日本語 WordNet を取得しています", current, summary["progressTotal"])
        with tempfile.TemporaryDirectory(prefix="mine-city-reiki-wordnet-") as tmp:
            db_path = download_wordnet_sqlite(Path(tmp))
            wordnet_pairs, wordnet_stats = build_wordnet_pairs(db_path, max_pairs=max_wordnet_pairs)
        current += 1
        summary.update({f"wordnet{key[0].upper()}{key[1:]}": value for key, value in wordnet_stats.items()})
        summary["wordnetPairs"] = len(wordnet_pairs)
        if progress:
            progress(f"日本語 WordNet 関連語 {len(wordnet_pairs):,}件を登録しています", current, summary["progressTotal"])
        inserted += insert_pairs(cur, wordnet_pairs, "wordnet", f"wnja-{WORDNET_VERSION}", 5)

    if include_domain:
        if progress:
            progress("既存DBから関連語候補を生成しています", current, summary["progressTotal"])
        domain_pairs, domain_stats = build_domain_pairs(cur)
        current += 1
        summary["domainStats"] = domain_stats
        summary["domainPairs"] = len(domain_pairs)
        if progress:
            progress(f"既存DB関連語 {len(domain_pairs):,}件を登録しています", current, summary["progressTotal"])
        inserted += insert_pairs(cur, domain_pairs, "domain", ENGINE_VERSION, 12)

    current = summary["progressTotal"]
    summary["inserted"] = inserted
    summary["progressCurrent"] = current
    summary["progressLabel"] = "関連語辞書更新が完了しました"
    return summary
