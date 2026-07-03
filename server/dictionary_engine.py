from __future__ import annotations

import gzip
import itertools
import json
import re
import shutil
import sqlite3
import tempfile
import csv
import io
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable


WORDNET_FALLBACK_VERSION = "1.1"
WORDNET_RELEASE_API = "https://api.github.com/repos/bond-lab/wnja/releases/latest"
WORDNET_SQLITE_ASSET = "wnjpn.db.gz"
WORDNET_SQLITE_FALLBACK_URL = f"https://github.com/bond-lab/wnja/releases/download/v{WORDNET_FALLBACK_VERSION}/{WORDNET_SQLITE_ASSET}"
ENGINE_VERSION = "dictionary-engine-2026-06-28"
MINUTES_DICTIONARY_ENGINE_VERSION = "minutes-dictionary-2026-06-30"
INTERNET_DICTIONARY_ENGINE_VERSION = "internet-dictionary-2026-07-03"
WIKIDATA_API_URL = "https://www.wikidata.org/w/api.php"

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

MINUTES_ROLE_ALIASES = {
    "questioner": ["議員", "質問者"],
    "answerer": ["答弁者", "執行部"],
    "chair": ["議長", "議事進行"],
    "secretariat": ["議会事務局", "事務局"],
    "report": ["報告"],
}

CURATED_SYNONYM_GROUPS: list[tuple[int, list[str]]] = [
    (10, ["老人", "お年寄り", "高齢者", "年寄り", "高齢の方", "シニア", "老年者"]),
    (9, ["高齢者", "65歳以上", "高齢世帯", "高齢の世帯"]),
    (9, ["後期高齢者", "75歳以上", "後期高齢"]),
    (10, ["マイナンバー", "個人番号"]),
    (9, ["障害者", "障がい者", "障害のある人", "障がいのある人"]),
    (9, ["子ども", "こども", "児童", "子供"]),
    (9, ["保育園", "保育所", "保育施設"]),
    (9, ["認定こども園", "こども園", "認定子ども園"]),
    (8, ["ごみ", "ゴミ", "廃棄物"]),
    (8, ["空き家", "空家", "空き家等"]),
    (8, ["観光客", "来訪者", "旅行者"]),
    (8, ["公共交通", "地域交通", "生活交通"]),
    (8, ["上下水道", "水道", "下水道"]),
    (8, ["養鱒場", "養ます場", "養魚場", "鱒養殖"]),
]

MINUTES_PROPER_NOUN_PATTERN = re.compile(
    r"[一-龯ぁ-んァ-ヴー]{2,30}"
    r"(?:市|町|村|地区|地域|川|山|台|湖|公園|学校|小学校|中学校|高等学校|高校|"
    r"保育園|こども園|センター|館|場|施設|事業|計画|委員会|協議会|組合|"
    r"会|部|課|室|局|署|駅|線|道路|橋|ダム|温泉|観光|農業|林業|水産|養鱒場)"
)


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


def build_curated_synonym_pairs() -> tuple[dict[int, set[tuple[str, str]]], dict[str, Any]]:
    grouped: dict[int, set[tuple[str, str]]] = defaultdict(set)
    for priority, terms in CURATED_SYNONYM_GROUPS:
        normalized_terms = [normalize_term(term) for term in terms if is_good_term(term)]
        for left, right in itertools.combinations(dict.fromkeys(normalized_terms), 2):
            add_pair(grouped[priority], left, right)
    return grouped, {
        "groups": len(CURATED_SYNONYM_GROUPS),
        "pairs": sum(len(pairs) for pairs in grouped.values()),
    }


def _fetch_json(url: str, params: dict[str, str] | None = None, timeout: int = 20) -> Any:
    full_url = url
    if params:
        full_url = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        full_url,
        headers={
            "Accept": "application/json",
            "User-Agent": "mine-city-reiki-dictionary-engine/0.1",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def cjk_chars(value: str) -> set[str]:
    return {ch for ch in normalize_term(value) if "\u3400" <= ch <= "\u9fff"}


def is_safe_wikidata_alias(seed: str, label: str, alias: str) -> bool:
    normalized_seed = normalize_term(seed)
    normalized_label = normalize_term(label)
    normalized_alias = normalize_term(alias)
    if not is_good_term(normalized_alias) or normalized_alias in {normalized_seed, normalized_label}:
        return False
    if len(normalized_alias) > 16:
        return False
    seed_cjk = cjk_chars(normalized_seed)
    alias_cjk = cjk_chars(normalized_alias)
    if seed_cjk and alias_cjk:
        return bool(seed_cjk & alias_cjk)
    if not seed_cjk and not alias_cjk:
        return normalized_seed[:3] in normalized_alias or normalized_alias[:3] in normalized_seed
    return False


def wikidata_aliases_for_term(term: str, *, limit: int = 3) -> set[str]:
    aliases: set[str] = set()
    search_payload = _fetch_json(
        WIKIDATA_API_URL,
        {
            "action": "wbsearchentities",
            "format": "json",
            "language": "ja",
            "uselang": "ja",
            "type": "item",
            "limit": str(limit),
            "search": term,
        },
    )
    ids = []
    normalized_term = normalize_term(term)
    for item in search_payload.get("search") or []:
        label = normalize_term(item.get("label") or "")
        if item.get("id") and label == normalized_term:
            ids.append(str(item.get("id")))
        if len(ids) >= limit:
            break
    if not ids:
        return aliases
    entity_payload = _fetch_json(
        WIKIDATA_API_URL,
        {
            "action": "wbgetentities",
            "format": "json",
            "languages": "ja",
            "props": "labels|aliases",
            "ids": "|".join(ids),
        },
    )
    for entity in (entity_payload.get("entities") or {}).values():
        label = ((entity.get("labels") or {}).get("ja") or {}).get("value")
        if label:
            aliases.add(str(label))
        for alias in ((entity.get("aliases") or {}).get("ja") or []):
            value = alias.get("value")
            if value and is_safe_wikidata_alias(term, label or term, str(value)):
                aliases.add(str(value))
    return {alias for alias in aliases if is_good_term(alias)}


def build_wikidata_pairs(seed_terms: Iterable[str] | None = None, *, max_terms: int = 80) -> tuple[set[tuple[str, str]], dict[str, Any]]:
    seeds = list(dict.fromkeys(
        normalize_term(term)
        for term in (
            list(seed_terms or [])
            + [term for _, terms in CURATED_SYNONYM_GROUPS for term in terms]
            + ["介護", "福祉", "年金", "住民票", "戸籍", "税金", "観光", "防災", "農業", "水道", "下水道"]
        )
        if is_good_term(term)
    ))[:max_terms]
    pairs: set[tuple[str, str]] = set()
    fetched = 0
    for seed in seeds:
        try:
            aliases = wikidata_aliases_for_term(seed)
        except Exception:
            continue
        fetched += 1
        for alias in aliases:
            add_pair(pairs, seed, alias)
        for left, right in itertools.combinations(sorted(aliases), 2):
            add_pair(pairs, left, right)
    return pairs, {"seedTerms": len(seeds), "fetchedTerms": fetched, "pairs": len(pairs)}


def _pairs_from_json_payload(payload: Any) -> tuple[dict[int, set[tuple[str, str]]], int]:
    grouped: dict[int, set[tuple[str, str]]] = defaultdict(set)
    rows: Iterable[Any]
    if isinstance(payload, dict):
        rows = payload.get("items") or payload.get("pairs") or payload.get("synonyms") or []
    elif isinstance(payload, list):
        rows = payload
    else:
        rows = []
    count = 0
    for row in rows:
        priority = 8
        if isinstance(row, dict):
            left = row.get("canonical") or row.get("canonicalTerm") or row.get("term") or row.get("left")
            right = row.get("synonym") or row.get("synonymTerm") or row.get("related") or row.get("right")
            priority = int(row.get("priority") or row.get("score") or priority)
        elif isinstance(row, (list, tuple)) and len(row) >= 2:
            left, right = row[0], row[1]
            if len(row) >= 3:
                priority = int(row[2] or priority)
        else:
            continue
        before = len(grouped[priority])
        add_pair(grouped[priority], left, right)
        if len(grouped[priority]) > before:
            count += 1
    return grouped, count


def build_url_dictionary_pairs(source_url: str) -> tuple[dict[int, set[tuple[str, str]]], dict[str, Any]]:
    parsed = urllib.parse.urlparse(source_url)
    if parsed.scheme not in {"https", "http"}:
        raise ValueError("辞書URLは http または https のみ対応しています。")
    request = urllib.request.Request(
        source_url,
        headers={
            "Accept": "application/json,text/csv,text/plain;q=0.8",
            "User-Agent": "mine-city-reiki-dictionary-engine/0.1",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        content_type = response.headers.get("Content-Type", "")
        body = response.read(5 * 1024 * 1024)
    text = body.decode("utf-8-sig")
    if "json" in content_type or source_url.lower().endswith(".json"):
        grouped, count = _pairs_from_json_payload(json.loads(text))
    else:
        grouped: dict[int, set[tuple[str, str]]] = defaultdict(set)
        count = 0
        reader = csv.DictReader(io.StringIO(text))
        if reader.fieldnames and {"canonical", "synonym"} <= set(reader.fieldnames):
            for row in reader:
                priority = int(row.get("priority") or row.get("score") or 8)
                before = len(grouped[priority])
                add_pair(grouped[priority], row.get("canonical"), row.get("synonym"))
                if len(grouped[priority]) > before:
                    count += 1
        else:
            for row in csv.reader(io.StringIO(text)):
                if len(row) < 2:
                    continue
                priority = int(row[2]) if len(row) >= 3 and str(row[2]).isdigit() else 8
                before = len(grouped[priority])
                add_pair(grouped[priority], row[0], row[1])
                if len(grouped[priority]) > before:
                    count += 1
    return grouped, {"url": source_url, "pairs": count}


def resolve_wordnet_release() -> tuple[str, str]:
    request = urllib.request.Request(
        WORDNET_RELEASE_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "mine-city-reiki-dictionary-engine",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
        version = str(payload.get("tag_name") or f"v{WORDNET_FALLBACK_VERSION}").removeprefix("v")
        for asset in payload.get("assets") or []:
            if asset.get("name") == WORDNET_SQLITE_ASSET and asset.get("browser_download_url"):
                return version, str(asset["browser_download_url"])
    except Exception:
        pass
    return WORDNET_FALLBACK_VERSION, WORDNET_SQLITE_FALLBACK_URL


def download_wordnet_sqlite(work_dir: Path) -> tuple[Path, str]:
    version, url = resolve_wordnet_release()
    gz_path = work_dir / "wnjpn.db.gz"
    db_path = work_dir / "wnjpn.db"
    urllib.request.urlretrieve(url, gz_path)
    with gzip.open(gz_path, "rb") as src, db_path.open("wb") as dst:
        shutil.copyfileobj(src, dst)
    return db_path, version


def build_wordnet_pairs(db_path: Path, max_pairs: int = 30000, wordnet_version: str = WORDNET_FALLBACK_VERSION) -> tuple[set[tuple[str, str]], dict[str, Any]]:
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
                return pairs, {"wordnetVersion": wordnet_version, "synsetsUsed": used_synsets, "pairs": len(pairs), "truncated": True}
    return pairs, {"wordnetVersion": wordnet_version, "synsetsUsed": used_synsets, "pairs": len(pairs), "truncated": False}


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


def count_unprocessed_minutes_dictionary_rows(cur, engine_version: str = MINUTES_DICTIONARY_ENGINE_VERSION) -> int:
    cur.execute(
        """
        SELECT COUNT(*) AS cnt
        FROM meeting_utterances u
        LEFT JOIN meeting_dictionary_sources src
          ON src.utterance_id=u.id AND src.engine_version=%s
        WHERE src.utterance_id IS NULL
        """,
        (engine_version,),
    )
    return int((cur.fetchone() or {}).get("cnt") or 0)


def fetch_unprocessed_minutes_dictionary_rows(
    cur,
    batch_size: int = 1000,
    engine_version: str = MINUTES_DICTIONARY_ENGINE_VERSION,
) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT
          u.id, u.speaker_name, u.speaker_title, u.speaker_role, u.speaker_group, u.text,
          d.title AS day_title, d.date_label, d.meeting_date,
          s.section, s.meeting_name, s.title AS session_title
        FROM meeting_utterances u
        JOIN meeting_days d ON d.id=u.day_id
        JOIN meeting_sessions s ON s.id=d.session_id
        LEFT JOIN meeting_dictionary_sources src
          ON src.utterance_id=u.id AND src.engine_version=%s
        WHERE src.utterance_id IS NULL
        ORDER BY u.id ASC
        LIMIT %s
        """,
        (engine_version, batch_size),
    )
    return cur.fetchall() or []


def build_minutes_pairs_from_rows(rows: Iterable[dict[str, Any]]) -> tuple[set[tuple[str, str]], dict[str, Any]]:
    row_list = list(rows)
    pairs: set[tuple[str, str]] = set()
    term_counts: Counter[str] = Counter()
    speaker_count = 0
    title_count = 0
    text_term_count = 0

    for row in row_list:
        speaker_name = row.get("speaker_name") or ""
        speaker_title = row.get("speaker_title") or ""
        speaker_role = row.get("speaker_role") or ""
        speaker_group = row.get("speaker_group") or ""
        meeting_name = row.get("meeting_name") or ""
        day_title = row.get("day_title") or ""
        section = row.get("section") or ""

        if speaker_name:
            speaker_count += 1
            add_pair(pairs, speaker_name, normalize_term(speaker_name))
            if speaker_title:
                add_pair(pairs, speaker_name, speaker_title)
                add_pair(pairs, speaker_title, speaker_name)
                title_count += 1
            if speaker_group:
                add_pair(pairs, speaker_name, speaker_group)
            for alias in MINUTES_ROLE_ALIASES.get(speaker_role, []):
                add_pair(pairs, speaker_name, alias)
                if speaker_title:
                    add_pair(pairs, speaker_title, alias)

        if speaker_title:
            for alias in TITLE_ALIASES.get(speaker_title, []):
                add_pair(pairs, speaker_title, alias)
            for term in split_title_terms(speaker_title):
                add_pair(pairs, speaker_title, term)

        for title_source in [meeting_name, day_title]:
            for term in split_title_terms(title_source):
                add_pair(pairs, title_source, term)
                term_counts[term] += 1
        if section:
            add_pair(pairs, meeting_name, section)

        text = row.get("text") or ""
        for raw_term in MINUTES_PROPER_NOUN_PATTERN.findall(text):
            term = normalize_term(raw_term)
            if is_good_term(term):
                term_counts[term] += 1
                text_term_count += 1
                if "美祢市" in raw_term and len(raw_term) > len("美祢市"):
                    add_pair(pairs, raw_term, raw_term.replace("美祢市", "", 1))
                if "養鱒場" in raw_term:
                    add_pair(pairs, raw_term, "養鱒場")

    repeated_terms = [term for term, count in term_counts.items() if count >= 2 and is_good_term(term)]
    for left, right in itertools.combinations(sorted(repeated_terms[:150]), 2):
        if left in right or right in left:
            add_pair(pairs, left, right)

    return pairs, {
        "rows": len(row_list),
        "speakers": speaker_count,
        "titles": title_count,
        "textTerms": text_term_count,
        "domainTerms": len(repeated_terms),
        "pairs": len(pairs),
    }


def mark_minutes_dictionary_rows_processed(
    cur,
    utterance_ids: Iterable[int],
    *,
    engine_version: str = MINUTES_DICTIONARY_ENGINE_VERSION,
    term_count: int = 0,
) -> int:
    ids = [int(value) for value in utterance_ids if int(value or 0) > 0]
    if not ids:
        return 0
    cur.executemany(
        """
        INSERT INTO meeting_dictionary_sources (utterance_id, engine_version, term_count, processed_at)
        VALUES (%s,%s,%s,CURRENT_TIMESTAMP)
        ON DUPLICATE KEY UPDATE
          engine_version=VALUES(engine_version),
          term_count=VALUES(term_count),
          processed_at=VALUES(processed_at)
        """,
        [(utterance_id, engine_version, term_count) for utterance_id in ids],
    )
    return len(ids)


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
            db_path, wordnet_version = download_wordnet_sqlite(Path(tmp))
            wordnet_pairs, wordnet_stats = build_wordnet_pairs(db_path, max_pairs=max_wordnet_pairs, wordnet_version=wordnet_version)
        current += 1
        summary.update({f"wordnet{key[0].upper()}{key[1:]}": value for key, value in wordnet_stats.items()})
        summary["wordnetPairs"] = len(wordnet_pairs)
        if progress:
            progress(f"日本語 WordNet 関連語 {len(wordnet_pairs):,}件を登録しています", current, summary["progressTotal"])
        inserted += insert_pairs(cur, wordnet_pairs, "wordnet", f"wnja-{wordnet_version}", 5)

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


def build_internet_dictionary(
    cur,
    *,
    include_wikidata: bool = True,
    include_curated: bool = True,
    source_url: str = "",
    progress: Callable[[str, int, int], None] | None = None,
) -> dict[str, Any]:
    enabled_steps = int(include_curated) + int(include_wikidata) + int(bool(source_url))
    summary: dict[str, Any] = {
        "operation": "internet-dictionary-update",
        "engineVersion": INTERNET_DICTIONARY_ENGINE_VERSION,
        "includeCurated": include_curated,
        "includeWikidata": include_wikidata,
        "sourceUrl": source_url,
        "curatedPairs": 0,
        "wikidataPairs": 0,
        "urlPairs": 0,
        "inserted": 0,
        "progressCurrent": 0,
        "progressTotal": max(1, enabled_steps + 1),
        "progressLabel": "インターネット辞書取り込みを準備しています",
    }
    if enabled_steps == 0:
        raise ValueError("取り込み対象を1つ以上選択してください。")

    cur.execute("DELETE FROM law_synonyms WHERE source_type IN ('wikidata','internet','curated')")
    if progress:
        progress("既存のインターネット由来辞書を削除しました", 0, summary["progressTotal"])

    inserted = 0
    current = 0
    if include_curated:
        grouped, stats = build_curated_synonym_pairs()
        current += 1
        summary["curatedStats"] = stats
        summary["curatedPairs"] = stats["pairs"]
        if progress:
            progress(f"同義語・言い換えシード {stats['pairs']:,}件を登録しています", current, summary["progressTotal"])
        for priority, pairs in grouped.items():
            inserted += insert_pairs(cur, pairs, "curated", INTERNET_DICTIONARY_ENGINE_VERSION, priority)

    if include_wikidata:
        if progress:
            progress("Wikidata から日本語別名を取得しています", current, summary["progressTotal"])
        pairs, stats = build_wikidata_pairs()
        current += 1
        summary["wikidataStats"] = stats
        summary["wikidataPairs"] = len(pairs)
        if progress:
            progress(f"Wikidata 別名 {len(pairs):,}件を登録しています", current, summary["progressTotal"])
        inserted += insert_pairs(cur, pairs, "wikidata", INTERNET_DICTIONARY_ENGINE_VERSION, 8)

    if source_url:
        if progress:
            progress("指定URLから辞書を取得しています", current, summary["progressTotal"])
        grouped, stats = build_url_dictionary_pairs(source_url)
        current += 1
        summary["urlStats"] = stats
        summary["urlPairs"] = stats["pairs"]
        if progress:
            progress(f"指定URL辞書 {stats['pairs']:,}件を登録しています", current, summary["progressTotal"])
        for priority, pairs in grouped.items():
            inserted += insert_pairs(cur, pairs, "internet", INTERNET_DICTIONARY_ENGINE_VERSION, max(1, min(20, priority)))

    summary["inserted"] = inserted
    summary["progressCurrent"] = summary["progressTotal"]
    summary["progressLabel"] = "インターネット辞書取り込みが完了しました"
    return summary
