from __future__ import annotations

import gzip
import itertools
import json
import os
import re
import shutil
import sqlite3
import tempfile
import csv
import io
import time
import threading
import urllib.parse
import urllib.request
import urllib.error
from collections import Counter, OrderedDict, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable


WORDNET_FALLBACK_VERSION = "1.1"
WORDNET_RELEASE_API = "https://api.github.com/repos/bond-lab/wnja/releases/latest"
WORDNET_SQLITE_ASSET = "wnjpn.db.gz"
WORDNET_SQLITE_FALLBACK_URL = f"https://github.com/bond-lab/wnja/releases/download/v{WORDNET_FALLBACK_VERSION}/{WORDNET_SQLITE_ASSET}"
ENGINE_VERSION = "dictionary-engine-2026-06-28"
MINUTES_DICTIONARY_ENGINE_VERSION = "minutes-dictionary-2026-06-30"
INTERNET_DICTIONARY_ENGINE_VERSION = "internet-dictionary-2026-07-23"
COMPILED_DICTIONARY_VERSION = "compiled-synonyms-sqlite-2026-07-17"
WIKIDATA_API_URL = "https://www.wikidata.org/w/api.php"
WIKIDATA_REQUEST_INTERVAL_SECONDS = 0.8
MEDIAWIKI_REQUEST_INTERVAL_SECONDS = 0.25
THESAURUS_TARGET_TERM_COUNT = 500_000
THESAURUS_ULTIMATE_TERM_COUNT = 1_000_000
STEADY_DICTIONARY_BUDGET = {
    "wikipediaLimit": 5_000,
    "wiktionaryLimit": 2_000,
    "wikidataTermLimit": 25,
}
ACCELERATED_DICTIONARY_BUDGET = {
    "wikipediaLimit": 100_000,
    "wiktionaryLimit": 50_000,
    "wikidataTermLimit": 100,
}
WIKIMEDIA_USER_AGENT = os.getenv(
    "REIKI_DICTIONARY_USER_AGENT",
    "mine-city-reiki-thesaurus-bot/0.1 (https://github.com/shiburingo/mine-city-reiki)",
)
DEFAULT_COMPILED_DICTIONARY_PATH = Path(__file__).resolve().parent / "data" / "compiled_synonyms.json"
COMPILED_DICTIONARY_BATCH_SIZE = 5_000
COMPILED_DICTIONARY_CACHE_SIZE = 4_096
COMPILED_JSON_COMPAT_MAX_TERMS = max(0, int(os.getenv("REIKI_SYNONYM_JSON_COMPAT_MAX_TERMS", "100000")))

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

_LAST_REQUEST_AT_BY_HOST: dict[str, float] = {}


@dataclass(frozen=True)
class DictionaryObservation:
    canonical: str
    synonym: str
    source_item_id: str = ""
    source_url: str = ""
    metadata: dict[str, Any] | None = None


MEDIAWIKI_REDIRECT_SOURCES: tuple[dict[str, Any], ...] = (
    {
        "sourceKey": "jawikipedia-redirects",
        "displayName": "日本語Wikipediaリダイレクト",
        "sourceType": "wikipedia",
        "endpoint": "https://ja.wikipedia.org/w/api.php",
        "pageBaseUrl": "https://ja.wikipedia.org/wiki/",
        "licenseName": "CC BY-SA 4.0",
        "licenseUrl": "https://creativecommons.org/licenses/by-sa/4.0/",
        "priority": 9,
    },
    {
        "sourceKey": "jawiktionary-redirects",
        "displayName": "日本語Wiktionaryリダイレクト",
        "sourceType": "wiktionary",
        "endpoint": "https://ja.wiktionary.org/w/api.php",
        "pageBaseUrl": "https://ja.wiktionary.org/wiki/",
        "licenseName": "CC BY-SA 4.0",
        "licenseUrl": "https://creativecommons.org/licenses/by-sa/4.0/",
        "priority": 12,
    },
)

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


def normalize_pair(left: str | None, right: str | None) -> tuple[str, str]:
    a = normalize_term(left)
    b = normalize_term(right)
    if not a or not b or a == b:
        return "", ""
    return (a, b) if a < b else (b, a)


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
    a, b = normalize_pair(left, right)
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


def dictionary_collection_budget(
    current_term_count: int,
    *,
    accelerated: bool = True,
    target_term_count: int = THESAURUS_TARGET_TERM_COUNT,
) -> dict[str, Any]:
    use_accelerated = accelerated and max(0, int(current_term_count)) < max(1, int(target_term_count))
    selected = ACCELERATED_DICTIONARY_BUDGET if use_accelerated else STEADY_DICTIONARY_BUDGET
    return {
        **selected,
        "mode": "accelerated" if use_accelerated else "steady",
        "currentTermCount": max(0, int(current_term_count)),
        "targetTermCount": max(1, int(target_term_count)),
    }


def _fetch_json(
    url: str,
    params: dict[str, str] | None = None,
    timeout: int = 20,
    *,
    min_interval: float = WIKIDATA_REQUEST_INTERVAL_SECONDS,
) -> Any:
    full_url = url
    if params:
        full_url = f"{url}?{urllib.parse.urlencode(params)}"
    host = urllib.parse.urlparse(url).netloc
    request = urllib.request.Request(
        full_url,
        headers={
            "Accept": "application/json",
            "User-Agent": WIKIMEDIA_USER_AGENT,
        },
    )
    for attempt in range(3):
        last_request_at = _LAST_REQUEST_AT_BY_HOST.get(host, 0.0)
        wait = min_interval - (time.monotonic() - last_request_at)
        if wait > 0:
            time.sleep(wait)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                _LAST_REQUEST_AT_BY_HOST[host] = time.monotonic()
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            _LAST_REQUEST_AT_BY_HOST[host] = time.monotonic()
            if exc.code not in {429, 503} or attempt >= 2:
                raise
            retry_after = exc.headers.get("Retry-After")
            delay = float(retry_after) if retry_after and retry_after.isdigit() else 2.0 * (attempt + 1)
            time.sleep(min(delay, 15.0))


def cjk_chars(value: str) -> set[str]:
    return {ch for ch in normalize_term(value) if "\u3400" <= ch <= "\u9fff"}


def curated_peer_terms() -> dict[str, set[str]]:
    peers: dict[str, set[str]] = {}
    for _, terms in CURATED_SYNONYM_GROUPS:
        normalized_terms = {normalize_term(term) for term in terms if is_good_term(term)}
        for term in normalized_terms:
            peers.setdefault(term, set()).update(normalized_terms)
    return peers


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


def wikidata_entity_ids_for_term(
    term: str,
    *,
    limit: int = 5,
    accepted_labels: set[str] | None = None,
) -> list[str]:
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
    allowed_labels = {normalized_term, *(accepted_labels or set())}
    for item in search_payload.get("search") or []:
        label = normalize_term(item.get("label") or "")
        if item.get("id") and label in allowed_labels:
            ids.append(str(item.get("id")))
        if len(ids) >= limit:
            break
    return ids


def _chunked(values: list[Any], size: int) -> Iterable[list[Any]]:
    for index in range(0, len(values), size):
        yield values[index:index + size]


def fetch_wikidata_entities(ids: list[str]) -> dict[str, dict[str, Any]]:
    entities: dict[str, dict[str, Any]] = {}
    for id_chunk in _chunked(list(dict.fromkeys(ids)), 50):
        entity_payload = _fetch_json(
            WIKIDATA_API_URL,
            {
                "action": "wbgetentities",
                "format": "json",
                "languages": "ja",
                "props": "labels|aliases",
                "ids": "|".join(id_chunk),
            },
        )
        entities.update(entity_payload.get("entities") or {})
    return entities


def wikidata_aliases_from_entities(
    term: str,
    ids: Iterable[str],
    entities: dict[str, dict[str, Any]],
) -> set[str]:
    aliases: set[str] = set()
    for entity_id in ids:
        entity = entities.get(entity_id) or {}
        label = ((entity.get("labels") or {}).get("ja") or {}).get("value")
        if label:
            aliases.add(str(label))
        for alias in ((entity.get("aliases") or {}).get("ja") or []):
            value = alias.get("value")
            if value and is_safe_wikidata_alias(term, label or term, str(value)):
                aliases.add(str(value))
    return {alias for alias in aliases if is_good_term(alias)}


def wikidata_aliases_for_term(term: str, *, limit: int = 5, accepted_labels: set[str] | None = None) -> set[str]:
    ids = wikidata_entity_ids_for_term(term, limit=limit, accepted_labels=accepted_labels)
    if not ids:
        return set()
    return wikidata_aliases_from_entities(term, ids, fetch_wikidata_entities(ids))


def build_wikidata_pairs(seed_terms: Iterable[str] | None = None, *, max_terms: int = 30) -> tuple[set[tuple[str, str]], dict[str, Any]]:
    peer_lookup = curated_peer_terms()
    default_seeds = [terms[0] for _, terms in CURATED_SYNONYM_GROUPS if terms]
    candidate_terms = list(seed_terms) if seed_terms is not None else (
        default_seeds + ["介護", "福祉", "年金", "住民票", "戸籍", "税金", "観光", "防災", "農業", "水道", "下水道"]
    )
    seeds = list(dict.fromkeys(
        normalize_term(term)
        for term in candidate_terms
        if is_good_term(term)
    ))[:max_terms]
    pairs: set[tuple[str, str]] = set()
    fetched = 0
    failed = 0
    seed_entity_ids: dict[str, list[str]] = {}
    for seed in seeds:
        try:
            seed_entity_ids[seed] = wikidata_entity_ids_for_term(
                seed,
                accepted_labels=peer_lookup.get(seed, set()),
            )
        except Exception:
            failed += 1
            continue
        fetched += 1
    all_entity_ids = [
        entity_id
        for entity_ids in seed_entity_ids.values()
        for entity_id in entity_ids
    ]
    try:
        entities = fetch_wikidata_entities(all_entity_ids) if all_entity_ids else {}
    except Exception:
        entities = {}
        failed += sum(1 for entity_ids in seed_entity_ids.values() if entity_ids)
    for seed, entity_ids in seed_entity_ids.items():
        aliases = wikidata_aliases_from_entities(seed, entity_ids, entities)
        for alias in aliases:
            add_pair(pairs, seed, alias)
        for left, right in itertools.combinations(sorted(aliases), 2):
            add_pair(pairs, left, right)
    return pairs, {"seedTerms": len(seeds), "fetchedTerms": fetched, "failedTerms": failed, "pairs": len(pairs)}


def fetch_mediawiki_redirect_observations(
    source: dict[str, Any],
    *,
    cursor: str = "",
    max_items: int = 1000,
) -> tuple[list[DictionaryObservation], dict[str, Any]]:
    endpoint = str(source["endpoint"])
    page_base_url = str(source["pageBaseUrl"])
    next_cursor = cursor
    scanned = 0
    requests = 0
    observations: dict[tuple[str, str], DictionaryObservation] = {}
    cycle_complete = False

    while scanned < max_items:
        limit = min(500, max_items - scanned)
        params = {
            "action": "query",
            "format": "json",
            "formatversion": "2",
            "generator": "allredirects",
            "garnamespace": "0",
            "garlimit": str(limit),
            "garprop": "ids|title|fragment",
            "redirects": "1",
            "prop": "info",
            "maxlag": "5",
        }
        if next_cursor:
            params["garcontinue"] = next_cursor
        payload = _fetch_json(
            endpoint,
            params,
            timeout=30,
            min_interval=MEDIAWIKI_REQUEST_INTERVAL_SECONDS,
        )
        requests += 1
        rows = (payload.get("query") or {}).get("redirects") or []
        if not rows:
            cycle_complete = not bool((payload.get("continue") or {}).get("garcontinue"))
            next_cursor = str((payload.get("continue") or {}).get("garcontinue") or "")
            break

        scanned += len(rows)

        for row in rows:
            alias = str(row.get("from") or "")
            target = str(row.get("to") or "")
            canonical = normalize_term(target)
            synonym = normalize_term(alias)
            if not is_good_term(canonical) or not is_good_term(synonym) or canonical == synonym:
                continue
            source_url = f"{page_base_url}{urllib.parse.quote(alias.replace(' ', '_'))}"
            observations[(canonical, synonym)] = DictionaryObservation(
                canonical=canonical,
                synonym=synonym,
                source_item_id=alias,
                source_url=source_url,
                metadata={
                    "target": target,
                    "fragment": str(row.get("tofragment") or row.get("fragment") or ""),
                },
            )

        continuation = str((payload.get("continue") or {}).get("garcontinue") or "")
        if not continuation:
            next_cursor = ""
            cycle_complete = True
            break
        next_cursor = continuation

    return list(observations.values()), {
        "scanned": scanned,
        "accepted": len(observations),
        "requests": requests,
        "cursor": next_cursor,
        "cycleComplete": cycle_complete,
    }


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
    normalized_pairs = {normalize_pair(canonical, synonym) for canonical, synonym in pairs}
    for canonical, synonym in sorted(normalized_pairs):
        if not canonical or not synonym:
            continue
        cur.execute(
            """
            INSERT INTO law_synonyms
              (canonical_term, synonym_term, priority, is_active, source_type, source_version)
            VALUES (%s,%s,%s,1,%s,%s)
            ON DUPLICATE KEY UPDATE
              priority=GREATEST(priority, VALUES(priority)),
              is_active=1,
              source_version=IF(source_type='manual', source_version, VALUES(source_version)),
              source_type=IF(source_type='manual', source_type, VALUES(source_type))
            """,
            (canonical, synonym, priority, source_type, source_version),
        )
        count += 1
    return count


def ensure_dictionary_source_rows(cur) -> None:
    sources = [
        *MEDIAWIKI_REDIRECT_SOURCES,
        {
            "sourceKey": "wikidata-ja-aliases",
            "displayName": "Wikidata日本語別名",
            "sourceType": "wikidata",
            "endpoint": WIKIDATA_API_URL,
            "licenseName": "CC0 1.0",
            "licenseUrl": "https://creativecommons.org/publicdomain/zero/1.0/",
            "priority": 8,
        },
        {
            "sourceKey": "curated-ja-seeds",
            "displayName": "管理済み日本語シード",
            "sourceType": "curated",
            "endpoint": "internal://curated-ja-seeds",
            "licenseName": "Project data",
            "licenseUrl": "",
            "priority": 10,
        },
    ]
    for source in sources:
        cur.execute(
            """
            INSERT INTO dictionary_sources
              (source_key, display_name, source_type, endpoint, license_name, license_url, priority, is_enabled)
            VALUES (%s,%s,%s,%s,%s,%s,%s,1)
            ON DUPLICATE KEY UPDATE
              display_name=VALUES(display_name),
              source_type=VALUES(source_type),
              endpoint=VALUES(endpoint),
              license_name=VALUES(license_name),
              license_url=VALUES(license_url),
              priority=VALUES(priority)
            """,
            (
                source["sourceKey"],
                source["displayName"],
                source["sourceType"],
                source["endpoint"],
                source["licenseName"],
                source["licenseUrl"],
                source["priority"],
            ),
        )


def dictionary_source_state(cur, source_key: str) -> dict[str, Any]:
    cur.execute(
        "SELECT source_key, cursor_json, cycle_count, processed_items, discovered_pairs, last_error"
        " FROM dictionary_sources WHERE source_key=%s",
        (source_key,),
    )
    row = cur.fetchone() or {}
    try:
        cursor = json.loads(row.get("cursor_json") or "{}")
    except (TypeError, ValueError):
        cursor = {}
    return {**row, "cursor": cursor}


def dictionary_source_statuses(cur) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT s.source_key, s.display_name, s.source_type, s.endpoint,
               s.license_name, s.license_url, s.priority, s.is_enabled,
               s.cycle_count, s.processed_items, s.discovered_pairs,
               s.last_started_at, s.last_success_at, s.last_error,
               COUNT(e.id) AS evidence_count,
               COALESCE(SUM(e.observation_count),0) AS observation_count
        FROM dictionary_sources s
        LEFT JOIN dictionary_pair_evidence e ON e.source_key=s.source_key
        GROUP BY s.source_key, s.display_name, s.source_type, s.endpoint,
                 s.license_name, s.license_url, s.priority, s.is_enabled,
                 s.cycle_count, s.processed_items, s.discovered_pairs,
                 s.last_started_at, s.last_success_at, s.last_error
        ORDER BY s.priority DESC, s.source_key
        """
    )
    return cur.fetchall() or []


def update_dictionary_source_state(
    cur,
    source_key: str,
    *,
    cursor: dict[str, Any] | None = None,
    processed: int = 0,
    discovered: int = 0,
    cycle_complete: bool = False,
    error: str | None = None,
) -> None:
    cur.execute(
        """
        UPDATE dictionary_sources
        SET cursor_json=%s,
            cycle_count=cycle_count+%s,
            processed_items=processed_items+%s,
            discovered_pairs=discovered_pairs+%s,
            last_started_at=CURRENT_TIMESTAMP,
            last_success_at=IF(%s IS NULL, CURRENT_TIMESTAMP, last_success_at),
            last_error=%s
        WHERE source_key=%s
        """,
        (
            json.dumps(cursor or {}, ensure_ascii=False, separators=(",", ":")),
            1 if cycle_complete else 0,
            max(0, int(processed)),
            max(0, int(discovered)),
            error,
            error,
            source_key,
        ),
    )


def upsert_dictionary_observations(
    cur,
    observations: Iterable[DictionaryObservation],
    *,
    source_key: str,
    source_type: str,
    source_version: str,
    priority: int,
    confidence: float,
) -> dict[str, int]:
    unique: dict[tuple[str, str], DictionaryObservation] = {}
    for observation in observations:
        canonical, synonym = normalize_pair(observation.canonical, observation.synonym)
        if canonical == synonym or not is_good_term(canonical) or not is_good_term(synonym):
            continue
        unique[(canonical, synonym)] = DictionaryObservation(
            canonical=canonical,
            synonym=synonym,
            source_item_id=observation.source_item_id,
            source_url=observation.source_url,
            metadata=observation.metadata,
        )
    rows = list(unique.values())
    if not rows:
        return {"observed": 0, "added": 0, "confirmed": 0}

    evidence_values = [
        (
            row.canonical,
            row.synonym,
            source_key,
            row.source_item_id[:191],
            row.source_url[:512],
            max(1, min(20, int(priority))),
            max(0.0, min(1.0, float(confidence))),
            json.dumps(row.metadata or {}, ensure_ascii=False, separators=(",", ":")),
        )
        for row in rows
    ]
    cur.executemany(
        """
        INSERT IGNORE INTO dictionary_pair_evidence
          (canonical_term, synonym_term, source_key, source_item_id, source_url,
           priority, confidence, observation_count, metadata_json)
        VALUES (%s,%s,%s,%s,%s,%s,%s,0,%s)
        """,
        evidence_values,
    )
    evidence_added = int(cur.rowcount or 0)
    cur.executemany(
        """
        INSERT INTO dictionary_pair_evidence
          (canonical_term, synonym_term, source_key, source_item_id, source_url,
           priority, confidence, observation_count, metadata_json)
        VALUES (%s,%s,%s,%s,%s,%s,%s,1,%s)
        ON DUPLICATE KEY UPDATE
          source_item_id=VALUES(source_item_id),
          source_url=VALUES(source_url),
          priority=GREATEST(priority,VALUES(priority)),
          confidence=GREATEST(confidence,VALUES(confidence)),
          observation_count=observation_count+1,
          last_seen_at=CURRENT_TIMESTAMP,
          metadata_json=VALUES(metadata_json)
        """,
        evidence_values,
    )

    synonym_values = [
        (row.canonical, row.synonym, max(1, min(20, int(priority))), source_type, source_version)
        for row in rows
    ]
    cur.executemany(
        """
        INSERT IGNORE INTO law_synonyms
          (canonical_term, synonym_term, priority, is_active, source_type, source_version)
        VALUES (%s,%s,%s,1,%s,%s)
        """,
        synonym_values,
    )
    synonym_added = int(cur.rowcount or 0)
    cur.executemany(
        """
        INSERT INTO law_synonyms
          (canonical_term, synonym_term, priority, is_active, source_type, source_version)
        VALUES (%s,%s,%s,1,%s,%s)
        ON DUPLICATE KEY UPDATE
          priority=GREATEST(priority,VALUES(priority)),
          is_active=1,
          source_version=IF(source_type='manual',source_version,VALUES(source_version)),
          source_type=IF(source_type='manual',source_type,VALUES(source_type))
        """,
        synonym_values,
    )
    return {
        "observed": len(rows),
        "added": synonym_added,
        "confirmed": max(0, len(rows) - evidence_added),
    }


def select_wikidata_seed_terms(cur, *, last_synonym_id: int = 0, limit: int = 25) -> tuple[list[str], int, bool]:
    cur.execute(
        """
        SELECT id, canonical_term, synonym_term
        FROM law_synonyms
        WHERE is_active=1 AND id>%s
        ORDER BY id ASC
        LIMIT %s
        """,
        (max(0, int(last_synonym_id)), max(1, int(limit)) * 3),
    )
    rows = cur.fetchall() or []
    terms: list[str] = []
    seen: set[str] = set()
    next_id = max(0, int(last_synonym_id))
    for row in rows:
        next_id = max(next_id, int(row.get("id") or 0))
        for value in (row.get("canonical_term"), row.get("synonym_term")):
            term = normalize_term(value)
            if term and term not in seen and is_good_term(term):
                seen.add(term)
                terms.append(term)
                if len(terms) >= limit:
                    return terms, next_id, False
    return terms, next_id, len(rows) == 0


def compiled_dictionary_path(path: str | Path | None = None) -> Path:
    return Path(path).expanduser().resolve() if path else DEFAULT_COMPILED_DICTIONARY_PATH


def compiled_dictionary_index_path(path: str | Path | None = None) -> Path:
    source_path = compiled_dictionary_path(path)
    if source_path.suffix.lower() in {".sqlite", ".sqlite3", ".db"}:
        return source_path
    return source_path.with_suffix(".sqlite3")


def compiled_dictionary_runtime_path(path: str | Path | None = None) -> Path:
    index_path = compiled_dictionary_index_path(path)
    if index_path.exists():
        return index_path
    return compiled_dictionary_path(path)


class IndexedSynonymDictionary:
    """Read-only SQLite lookup with a bounded per-process hot-term cache."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        uri = f"{self.path.as_uri()}?mode=ro&immutable=1"
        self._connection = sqlite3.connect(uri, uri=True, check_same_thread=False)
        self._lock = threading.RLock()
        self._cache: OrderedDict[str, tuple[tuple[str, int], ...]] = OrderedDict()

    def _lookup(self, term: str) -> tuple[tuple[str, int], ...]:
        with self._lock:
            cached = self._cache.pop(term, None)
            if cached is not None:
                self._cache[term] = cached
                return cached
            rows = self._connection.execute(
                "SELECT related_term, priority FROM edges WHERE term=? ORDER BY rank",
                (term,),
            ).fetchall()
            result = tuple((str(row[0]), int(row[1])) for row in rows)
            self._cache[term] = result
            if len(self._cache) > COMPILED_DICTIONARY_CACHE_SIZE:
                self._cache.popitem(last=False)
            return result

    def get(self, term: str, default=None):
        normalized = normalize_term(term)
        if not normalized:
            return default
        result = self._lookup(normalized)
        return list(result) if result else default

    def existing_terms(self, candidates: Iterable[str]) -> set[str]:
        normalized_terms = {normalize_term(term) for term in candidates}
        normalized = sorted(term for term in normalized_terms if term)
        found: set[str] = set()
        with self._lock:
            for offset in range(0, len(normalized), 500):
                batch = normalized[offset : offset + 500]
                placeholders = ",".join("?" for _ in batch)
                rows = self._connection.execute(
                    f"SELECT DISTINCT term FROM edges WHERE term IN ({placeholders})",
                    batch,
                ).fetchall()
                found.update(str(row[0]) for row in rows)
        return found

    def close(self) -> None:
        with self._lock:
            self._cache.clear()
            self._connection.close()


def _create_compiled_index_temp_path(target_path: Path) -> Path:
    handle, raw_path = tempfile.mkstemp(prefix=f".{target_path.name}.", suffix=".tmp", dir=target_path.parent)
    os.close(handle)
    temp_path = Path(raw_path)
    temp_path.unlink()
    return temp_path


def _write_json_compatibility_artifact(index_path: Path, target_path: Path, metadata: dict[str, Any]) -> None:
    handle, raw_path = tempfile.mkstemp(prefix=f".{target_path.name}.", suffix=".tmp", dir=target_path.parent)
    os.close(handle)
    temp_path = Path(raw_path)
    connection = sqlite3.connect(f"{index_path.as_uri()}?mode=ro&immutable=1", uri=True)
    try:
        with temp_path.open("w", encoding="utf-8") as output:
            encoded_metadata = json.dumps(metadata, ensure_ascii=False, separators=(",", ":"))
            output.write(encoded_metadata[:-1])
            output.write(',"terms":{')
            current_term = ""
            current_edges: list[dict[str, Any]] = []
            first_term = True

            def flush_term() -> None:
                nonlocal first_term
                if not current_term:
                    return
                if not first_term:
                    output.write(",")
                first_term = False
                output.write(json.dumps(current_term, ensure_ascii=False))
                output.write(":")
                output.write(json.dumps(current_edges, ensure_ascii=False, separators=(",", ":")))

            rows = connection.execute(
                "SELECT term, related_term, priority, source_type, source_version FROM edges ORDER BY term, rank"
            )
            for term, related_term, priority, source_type, source_version in rows:
                if current_term and term != current_term:
                    flush_term()
                    current_edges = []
                current_term = str(term)
                current_edges.append({
                    "term": str(related_term),
                    "priority": int(priority),
                    "sourceType": str(source_type),
                    "sourceVersion": str(source_version),
                })
            flush_term()
            output.write("}}")
        temp_path.replace(target_path)
    finally:
        connection.close()
        temp_path.unlink(missing_ok=True)


def compile_synonym_dictionary(
    cur,
    *,
    output_path: str | Path | None = None,
    min_priority: int = 1,
    max_edges_per_term: int = 64,
) -> dict[str, Any]:
    compatibility_path = compiled_dictionary_path(output_path)
    index_path = compiled_dictionary_index_path(output_path)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = _create_compiled_index_temp_path(index_path)
    source_counts: Counter[str] = Counter()
    db_rows = 0
    connection = sqlite3.connect(temp_path)
    try:
        connection.execute("PRAGMA journal_mode=OFF")
        connection.execute("PRAGMA synchronous=OFF")
        connection.execute("PRAGMA temp_store=FILE")
        connection.execute("PRAGMA cache_size=-65536")
        connection.execute(
            """
            CREATE TABLE candidate_edges (
              term TEXT NOT NULL,
              related_term TEXT NOT NULL,
              priority INTEGER NOT NULL,
              source_type TEXT NOT NULL,
              source_version TEXT NOT NULL,
              PRIMARY KEY (term, related_term)
            ) WITHOUT ROWID
            """
        )
        upsert_sql = """
            INSERT INTO candidate_edges (term, related_term, priority, source_type, source_version)
            VALUES (?,?,?,?,?)
            ON CONFLICT(term, related_term) DO UPDATE SET
              priority=MAX(candidate_edges.priority, excluded.priority),
              source_type=CASE
                WHEN excluded.priority > candidate_edges.priority THEN excluded.source_type
                ELSE candidate_edges.source_type
              END,
              source_version=CASE
                WHEN excluded.priority > candidate_edges.priority THEN excluded.source_version
                ELSE candidate_edges.source_version
              END
        """
        last_id = 0
        while True:
            cur.execute(
                """
                SELECT id, canonical_term, synonym_term, priority, source_type, source_version
                FROM law_synonyms
                WHERE is_active=1 AND priority >= %s AND id > %s
                ORDER BY id ASC
                LIMIT %s
                """,
                (min_priority, last_id, COMPILED_DICTIONARY_BATCH_SIZE),
            )
            rows = cur.fetchall() or []
            if not rows:
                break
            edge_rows: list[tuple[str, str, int, str, str]] = []
            for row in rows:
                last_id = max(last_id, int(row.get("id") or 0))
                canonical, synonym = normalize_pair(row.get("canonical_term"), row.get("synonym_term"))
                priority = int(row.get("priority") or 0)
                if not canonical or priority < min_priority:
                    continue
                source_type = str(row.get("source_type") or "manual")
                source_version = str(row.get("source_version") or "")
                db_rows += 1
                source_counts[source_type] += 1
                edge_rows.extend((
                    (canonical, synonym, priority, source_type, source_version),
                    (synonym, canonical, priority, source_type, source_version),
                ))
            connection.executemany(upsert_sql, edge_rows)
            connection.commit()
            if len(rows) < COMPILED_DICTIONARY_BATCH_SIZE:
                break

        connection.execute(
            """
            CREATE TABLE edges (
              term TEXT NOT NULL,
              rank INTEGER NOT NULL,
              related_term TEXT NOT NULL,
              priority INTEGER NOT NULL,
              source_type TEXT NOT NULL,
              source_version TEXT NOT NULL,
              PRIMARY KEY (term, rank),
              UNIQUE (term, related_term)
            ) WITHOUT ROWID
            """
        )
        connection.execute(
            """
            INSERT INTO edges (term, rank, related_term, priority, source_type, source_version)
            SELECT term, edge_rank, related_term, priority, source_type, source_version
            FROM (
              SELECT
                term,
                related_term,
                priority,
                source_type,
                source_version,
                ROW_NUMBER() OVER (
                  PARTITION BY term
                  ORDER BY priority DESC, LENGTH(related_term), related_term
                ) AS edge_rank
              FROM candidate_edges
            ) ranked
            WHERE edge_rank <= ?
            """,
            (max(1, int(max_edges_per_term)),),
        )
        term_count = int(connection.execute("SELECT COUNT(DISTINCT term) FROM edges").fetchone()[0])
        edge_count = int(connection.execute("SELECT COUNT(*) FROM edges").fetchone()[0])
        compiled_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        metadata = {
            "version": COMPILED_DICTIONARY_VERSION,
            "compiledAt": compiled_at,
            "minPriority": int(min_priority),
            "maxEdgesPerTerm": int(max_edges_per_term),
            "dbRows": db_rows,
            "termCount": term_count,
            "edgeCount": edge_count,
            "sourceCounts": dict(sorted(source_counts.items())),
        }
        connection.execute(
            "CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID"
        )
        connection.executemany(
            "INSERT INTO metadata (key, value) VALUES (?,?)",
            [(key, json.dumps(value, ensure_ascii=False, separators=(",", ":"))) for key, value in metadata.items()],
        )
        connection.execute("DROP TABLE candidate_edges")
        connection.execute("PRAGMA user_version=1")
        connection.commit()
        connection.execute("VACUUM")
    except Exception:
        connection.close()
        temp_path.unlink(missing_ok=True)
        raise
    else:
        connection.close()
    temp_path.replace(index_path)

    json_compat_written = False
    if compatibility_path != index_path:
        if term_count <= COMPILED_JSON_COMPAT_MAX_TERMS:
            _write_json_compatibility_artifact(index_path, compatibility_path, metadata)
            json_compat_written = True
        else:
            compatibility_path.unlink(missing_ok=True)
    return {
        "operation": "dictionary-compile",
        "engineVersion": COMPILED_DICTIONARY_VERSION,
        "format": "sqlite-index",
        "path": str(index_path),
        "bytes": index_path.stat().st_size,
        "compiledAt": compiled_at,
        "dbRows": db_rows,
        "termCount": term_count,
        "edgeCount": edge_count,
        "minPriority": min_priority,
        "maxEdgesPerTerm": max_edges_per_term,
        "sourceCounts": metadata["sourceCounts"],
        "jsonCompatWritten": json_compat_written,
    }


def load_compiled_synonym_dictionary(path: str | Path | None = None) -> Any:
    index_path = compiled_dictionary_index_path(path)
    if index_path.exists():
        return IndexedSynonymDictionary(index_path)
    source_path = compiled_dictionary_path(path)
    payload = json.loads(source_path.read_text(encoding="utf-8"))
    terms = payload.get("terms") or {}
    result: dict[str, list[tuple[str, int]]] = {}
    for term, edges in terms.items():
        normalized_term = normalize_term(term)
        if not normalized_term:
            continue
        result[normalized_term] = [
            (normalize_term(edge.get("term") or ""), int(edge.get("priority") or 0))
            for edge in (edges or [])
            if normalize_term(edge.get("term") or "")
        ]
    return result


def compiled_synonym_dictionary_status(path: str | Path | None = None) -> dict[str, Any]:
    source_path = compiled_dictionary_runtime_path(path)
    if not source_path.exists():
        return {"exists": False, "path": str(source_path)}
    stat = source_path.stat()
    status: dict[str, Any] = {
        "exists": True,
        "path": str(source_path),
        "bytes": stat.st_size,
        "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(stat.st_mtime)),
    }
    try:
        if source_path == compiled_dictionary_index_path(path):
            connection = sqlite3.connect(f"{source_path.as_uri()}?mode=ro&immutable=1", uri=True)
            try:
                payload = {
                    str(key): json.loads(str(value))
                    for key, value in connection.execute("SELECT key, value FROM metadata")
                }
            finally:
                connection.close()
            status["format"] = "sqlite-index"
        else:
            payload = json.loads(source_path.read_text(encoding="utf-8"))
            status["format"] = "json"
        status.update({
            "version": payload.get("version") or "",
            "compiledAt": payload.get("compiledAt") or "",
            "termCount": int(payload.get("termCount") or 0),
            "edgeCount": int(payload.get("edgeCount") or 0),
            "dbRows": int(payload.get("dbRows") or 0),
            "maxEdgesPerTerm": int(payload.get("maxEdgesPerTerm") or 0),
        })
    except Exception as exc:
        status["error"] = str(exc)
    return status


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
    include_mediawiki: bool = True,
    source_url: str = "",
    wikipedia_limit: int = 5000,
    wiktionary_limit: int = 2000,
    wikidata_term_limit: int = 25,
    progress: Callable[[str, int, int], None] | None = None,
) -> dict[str, Any]:
    mediawiki_sources = list(MEDIAWIKI_REDIRECT_SOURCES) if include_mediawiki else []
    enabled_steps = int(include_curated) + int(include_wikidata) + len(mediawiki_sources) + int(bool(source_url))
    summary: dict[str, Any] = {
        "operation": "internet-dictionary-update",
        "engineVersion": INTERNET_DICTIONARY_ENGINE_VERSION,
        "includeCurated": include_curated,
        "includeWikidata": include_wikidata,
        "includeMediawiki": include_mediawiki,
        "sourceUrl": source_url,
        "collectionBudget": {
            "wikipediaLimit": max(0, int(wikipedia_limit)),
            "wiktionaryLimit": max(0, int(wiktionary_limit)),
            "wikidataTermLimit": max(0, int(wikidata_term_limit)),
        },
        "curatedPairs": 0,
        "wikidataPairs": 0,
        "mediawikiPairs": 0,
        "urlPairs": 0,
        "observed": 0,
        "inserted": 0,
        "confirmed": 0,
        "sourceStats": [],
        "errors": [],
        "targetTermCount": THESAURUS_TARGET_TERM_COUNT,
        "ultimateTermCount": THESAURUS_ULTIMATE_TERM_COUNT,
        "progressCurrent": 0,
        "progressTotal": max(1, enabled_steps + 1),
        "progressLabel": "インターネット辞書取り込みを準備しています",
    }
    if enabled_steps == 0:
        raise ValueError("取り込み対象を1つ以上選択してください。")

    ensure_dictionary_source_rows(cur)
    current = 0

    def record_result(result: dict[str, int]) -> None:
        summary["observed"] += int(result.get("observed") or 0)
        summary["inserted"] += int(result.get("added") or 0)
        summary["confirmed"] += int(result.get("confirmed") or 0)

    if include_curated:
        grouped, stats = build_curated_synonym_pairs()
        current += 1
        summary["curatedStats"] = stats
        summary["curatedPairs"] = stats["pairs"]
        if progress:
            progress(f"管理済みシード {stats['pairs']:,}件を確認しています", current, summary["progressTotal"])
        for priority, pairs in grouped.items():
            result = upsert_dictionary_observations(
                cur,
                [DictionaryObservation(canonical=left, synonym=right) for left, right in pairs],
                source_key="curated-ja-seeds",
                source_type="curated",
                source_version=INTERNET_DICTIONARY_ENGINE_VERSION,
                priority=priority,
                confidence=1.0,
            )
            record_result(result)
        update_dictionary_source_state(
            cur,
            "curated-ja-seeds",
            processed=stats["pairs"],
            discovered=stats["pairs"],
        )

    mediawiki_limits = {
        "jawikipedia-redirects": max(0, int(wikipedia_limit)),
        "jawiktionary-redirects": max(0, int(wiktionary_limit)),
    }
    for source in mediawiki_sources:
        current += 1
        source_key = str(source["sourceKey"])
        limit = mediawiki_limits[source_key]
        state = dictionary_source_state(cur, source_key)
        cursor = str((state.get("cursor") or {}).get("continue") or "")
        if progress:
            progress(f"{source['displayName']}を巡回しています", current - 1, summary["progressTotal"])
        try:
            observations, source_stats = fetch_mediawiki_redirect_observations(
                source,
                cursor=cursor,
                max_items=limit,
            )
            result = upsert_dictionary_observations(
                cur,
                observations,
                source_key=source_key,
                source_type=str(source["sourceType"]),
                source_version=INTERNET_DICTIONARY_ENGINE_VERSION,
                priority=int(source["priority"]),
                confidence=0.9 if source["sourceType"] == "wiktionary" else 0.78,
            )
            record_result(result)
            summary["mediawikiPairs"] += len(observations)
            source_stats.update(result)
            source_stats["sourceKey"] = source_key
            source_stats["displayName"] = source["displayName"]
            summary["sourceStats"].append(source_stats)
            update_dictionary_source_state(
                cur,
                source_key,
                cursor={"continue": source_stats["cursor"]},
                processed=int(source_stats["scanned"]),
                discovered=int(result["added"]),
                cycle_complete=bool(source_stats["cycleComplete"]),
            )
            if progress:
                progress(
                    f"{source['displayName']} {source_stats['scanned']:,}件確認 / 新規 {result['added']:,}件",
                    current,
                    summary["progressTotal"],
                )
        except Exception as exc:
            error = f"{source_key}: {exc}"
            summary["errors"].append(error)
            update_dictionary_source_state(cur, source_key, cursor={"continue": cursor}, error=str(exc))

    if include_wikidata:
        current += 1
        source_key = "wikidata-ja-aliases"
        state = dictionary_source_state(cur, source_key)
        last_synonym_id = int((state.get("cursor") or {}).get("lastSynonymId") or 0)
        if progress:
            progress("未調査語をWikidataで補強しています", current - 1, summary["progressTotal"])
        try:
            seeds, next_synonym_id, cycle_complete = select_wikidata_seed_terms(
                cur,
                last_synonym_id=last_synonym_id,
                limit=max(1, int(wikidata_term_limit)),
            )
            if cycle_complete:
                next_synonym_id = 0
            pairs, stats = build_wikidata_pairs(seeds, max_terms=max(1, int(wikidata_term_limit))) if seeds else (set(), {
                "seedTerms": 0,
                "fetchedTerms": 0,
                "failedTerms": 0,
                "pairs": 0,
            })
            result = upsert_dictionary_observations(
                cur,
                [DictionaryObservation(canonical=left, synonym=right) for left, right in pairs],
                source_key=source_key,
                source_type="wikidata",
                source_version=INTERNET_DICTIONARY_ENGINE_VERSION,
                priority=8,
                confidence=0.82,
            )
            record_result(result)
            summary["wikidataStats"] = {**stats, **result, "lastSynonymId": next_synonym_id}
            summary["wikidataPairs"] = len(pairs)
            update_dictionary_source_state(
                cur,
                source_key,
                cursor={"lastSynonymId": next_synonym_id},
                processed=len(seeds),
                discovered=int(result["added"]),
                cycle_complete=cycle_complete,
            )
            if progress:
                progress(
                    f"Wikidata {len(seeds):,}語調査 / 新規 {result['added']:,}件",
                    current,
                    summary["progressTotal"],
                )
        except Exception as exc:
            error = f"{source_key}: {exc}"
            summary["errors"].append(error)
            update_dictionary_source_state(
                cur,
                source_key,
                cursor={"lastSynonymId": last_synonym_id},
                error=str(exc),
            )

    if source_url:
        current += 1
        if progress:
            progress("指定URLから辞書を取得しています", current - 1, summary["progressTotal"])
        grouped, stats = build_url_dictionary_pairs(source_url)
        summary["urlStats"] = stats
        summary["urlPairs"] = stats["pairs"]
        if progress:
            progress(f"指定URL辞書 {stats['pairs']:,}件を登録しています", current, summary["progressTotal"])
        for priority, pairs in grouped.items():
            before = len(pairs)
            insert_pairs(cur, pairs, "internet", INTERNET_DICTIONARY_ENGINE_VERSION, max(1, min(20, priority)))
            summary["observed"] += before

    summary["progressCurrent"] = summary["progressTotal"]
    summary["progressLabel"] = (
        f"累積辞書更新が完了しました（新規 {summary['inserted']:,}件 / 確認 {summary['confirmed']:,}件）"
    )
    return summary
