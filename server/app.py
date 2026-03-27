from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import pymysql
from bs4 import BeautifulSoup
from flask import Flask, g, jsonify, request
from pymysql.cursors import DictCursor
from werkzeug.exceptions import HTTPException

try:
    from janome.tokenizer import Tokenizer as JanomeTokenizer
except Exception:
    JanomeTokenizer = None

APP_VERSION = "0.1.0"
APP_SLUG = "mine-city-reiki"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
MINE_CITY_INDEX_URL = "https://www2.city.mine.lg.jp/section/reiki/reiki_taikei/r_taikei_05.html"
EGOV_LAWDATA_URL = "https://laws.e-gov.go.jp/api/1/lawdata/322AC0000000067"
TOKYO_OFFSET = "+09:00"


@dataclass
class AppConfig:
    host: str
    port: int
    user: str
    password: str
    db_name: str
    charset: str
    auto_init: bool
    api_port: int
    auth_verify_url: str
    auth_app_slug: str
    auth_bypass: bool


def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except Exception:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    return _env(name, "1" if default else "0").lower() in {"1", "true", "yes", "on"}


def load_config() -> AppConfig:
    return AppConfig(
        host=_env("DB_HOST", "127.0.0.1"),
        port=_env_int("DB_PORT", 3306),
        user=_env("DB_USER", "mine_city_reiki"),
        password=_env("DB_PASSWORD", ""),
        db_name=_env("DB_NAME", "mine_city_reiki"),
        charset=_env("DB_CHARSET", "utf8mb4"),
        auto_init=_env_bool("DB_AUTO_INIT", True),
        api_port=_env_int("MINE_CITY_REIKI_API_PORT", 8795),
        auth_verify_url=_env("AUTH_VERIFY_URL", "http://127.0.0.1:8787/api/auth/verify"),
        auth_app_slug=_env("AUTH_APP_SLUG", APP_SLUG),
        auth_bypass=_env_bool("MINE_CITY_REIKI_AUTH_BYPASS", False),
    )


CFG = load_config()
app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False
JANOME_TOKENIZER = None
STOP_TERMS = {
    "する",
    "こと",
    "もの",
    "ため",
    "について",
    "より",
    "また",
    "その",
    "この",
    "及び",
    "ならびに",
    "その他",
    "各",
    "第",
}


def db_connect(with_database: bool = True):
    params: Dict[str, Any] = {
        "host": CFG.host,
        "port": CFG.port,
        "user": CFG.user,
        "password": CFG.password,
        "charset": CFG.charset,
        "cursorclass": DictCursor,
        "autocommit": False,
    }
    if with_database:
        params["database"] = CFG.db_name
    return pymysql.connect(**params)


def execute_sql_script(cur, sql_text: str) -> None:
    buffer: list[str] = []
    for raw_line in sql_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("--"):
            continue
        buffer.append(raw_line)
        if line.endswith(";"):
            statement = "\n".join(buffer).strip().rstrip(";").strip()
            buffer = []
            if statement:
                cur.execute(statement)


def ensure_schema() -> None:
    if not CFG.auto_init:
        return
    schema_file = Path(__file__).resolve().parent / "schema.mariadb.sql"
    sql_text = schema_file.read_text(encoding="utf-8")
    try:
        with db_connect(with_database=False) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"CREATE DATABASE IF NOT EXISTS `{CFG.db_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
            conn.commit()
    except Exception:
        pass
    with db_connect(with_database=True) as conn:
        with conn.cursor() as cur:
            execute_sql_script(cur, sql_text)
            ensure_column(cur, "law_documents", "search_tokens", "search_tokens LONGTEXT NOT NULL DEFAULT '' AFTER normalized_title")
            ensure_table(
                cur,
                "law_search_terms",
                """
                CREATE TABLE law_search_terms (
                  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                  target_type ENUM('document','article') NOT NULL,
                  target_id BIGINT UNSIGNED NOT NULL,
                  document_id BIGINT UNSIGNED NOT NULL,
                  article_id BIGINT UNSIGNED NULL,
                  term VARCHAR(191) NOT NULL,
                  weight TINYINT UNSIGNED NOT NULL DEFAULT 1,
                  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  UNIQUE KEY uq_law_search_terms_target_term (target_type, target_id, term),
                  KEY idx_law_search_terms_term_target (term, target_type),
                  KEY idx_law_search_terms_document (document_id),
                  KEY idx_law_search_terms_article (article_id),
                  CONSTRAINT fk_law_search_terms_document FOREIGN KEY (document_id) REFERENCES law_documents(id) ON DELETE CASCADE,
                  CONSTRAINT fk_law_search_terms_article FOREIGN KEY (article_id) REFERENCES law_articles(id) ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """,
            )
        conn.commit()


@contextmanager
def db_cursor(commit: bool = False):
    conn = db_connect(with_database=True)
    cur = conn.cursor()
    try:
        yield conn, cur
        if commit:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def auth_verify() -> tuple[int, dict[str, Any]]:
    if CFG.auth_bypass or not CFG.auth_verify_url:
        return 200, {"enabled": False}
    try:
        req = urllib.request.Request(
            CFG.auth_verify_url,
            method="GET",
            headers={
                "X-Auth-App": CFG.auth_app_slug,
                "Cookie": request.headers.get("Cookie", ""),
            },
        )
        with urllib.request.urlopen(req, timeout=5) as res:
            payload = json.loads(res.read().decode("utf-8") or "{}")
            return res.status, payload
    except urllib.error.HTTPError as err:
        try:
            payload = json.loads(err.read().decode("utf-8") or "{}")
        except Exception:
            payload = {"error": "login required"}
        return err.code, payload
    except Exception:
        return 503, {"error": "auth verify failed"}


def actor_name() -> str:
    user = getattr(g, "auth_user", None) or {}
    return (
        (user.get("displayName") or "").strip()
        or (user.get("name") or "").strip()
        or (user.get("username") or "").strip()
        or "system"
    )


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def now_tokyo() -> datetime:
    return datetime.now().astimezone()


def normalize_text(text: str) -> str:
    value = unicodedata.normalize("NFKC", text or "")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def contains_japanese(text: str) -> bool:
    return any(
        ("\u3040" <= ch <= "\u30ff")
        or ("\u3400" <= ch <= "\u9fff")
        or ch == "々"
        for ch in text
    )


def get_janome_tokenizer():
    global JANOME_TOKENIZER
    if JANOME_TOKENIZER is None and JanomeTokenizer is not None:
        JANOME_TOKENIZER = JanomeTokenizer()
    return JANOME_TOKENIZER


def janome_terms(text: str) -> list[str]:
    tokenizer = get_janome_tokenizer()
    if tokenizer is None:
        return []
    terms: list[str] = []
    try:
        for token in tokenizer.tokenize(text):
            pos = (token.part_of_speech or "").split(",")[0]
            if pos not in {"名詞", "動詞", "形容詞"}:
                continue
            raw = normalize_text(token.surface).lower()
            base = normalize_text(token.base_form if token.base_form != "*" else raw).lower()
            for candidate in (base, raw):
                if not candidate or candidate in STOP_TERMS:
                    continue
                if len(candidate) <= 1 and not candidate.isdigit():
                    continue
                terms.append(candidate)
    except Exception:
        return []
    return terms


def chunk_terms(text: str) -> list[str]:
    normalized = normalize_text(text).lower()
    if not normalized:
        return []
    terms: list[str] = []
    for chunk in re.findall(r"[0-9a-zA-Z一-龯ぁ-んァ-ヶー々]+", normalized):
        if not chunk or chunk in STOP_TERMS:
            continue
        if len(chunk) > 1 or chunk.isdigit():
            terms.append(chunk)
        if contains_japanese(chunk):
            compact = chunk.replace(" ", "")
            if 2 <= len(compact) <= 20:
                for size in (2, 3):
                    if len(compact) < size:
                        continue
                    for idx in range(len(compact) - size + 1):
                        terms.append(compact[idx : idx + size])
    return terms


def limited_weighted_terms(*groups: tuple[str, int, bool], max_terms: int = 160) -> dict[str, int]:
    weights: dict[str, int] = {}
    for text, weight, include_phrase in groups:
        normalized = normalize_text(text).lower()
        if not normalized:
            continue
        if include_phrase and 1 < len(normalized) <= 96:
            weights[normalized] = max(weights.get(normalized, 0), weight)
        for term in janome_terms(normalized):
            weights[term] = max(weights.get(term, 0), weight)
        for term in chunk_terms(normalized):
            weights[term] = max(weights.get(term, 0), weight)
    ranked = sorted(weights.items(), key=lambda item: (-item[1], len(item[0]), item[0]))
    return dict(ranked[:max_terms])


def query_terms(query: str) -> list[str]:
    terms = list(
        limited_weighted_terms(
            (query, 10, True),
            max_terms=24,
        ).keys()
    )
    return terms


def ensure_column(cur, table: str, column: str, definition: str) -> None:
    cur.execute(
        """
        SELECT COUNT(*) AS cnt
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND COLUMN_NAME=%s
        """,
        (CFG.db_name, table, column),
    )
    exists = int((cur.fetchone() or {}).get("cnt") or 0) > 0
    if not exists:
        cur.execute(f"ALTER TABLE `{table}` ADD COLUMN {definition}")


def ensure_table(cur, table: str, ddl: str) -> None:
    cur.execute(
        """
        SELECT COUNT(*) AS cnt
        FROM information_schema.TABLES
        WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s
        """,
        (CFG.db_name, table),
    )
    exists = int((cur.fetchone() or {}).get("cnt") or 0) > 0
    if not exists:
        cur.execute(ddl)


def make_content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def split_keywords(query: str) -> list[str]:
    normalized = normalize_text(query)
    parts = [p for p in re.split(r"[\s、,，。]+", normalized) if p]
    seen: set[str] = set()
    result: list[str] = []
    for part in parts:
        token = part.strip()
        if token and token not in seen:
            seen.add(token)
            result.append(token)
    if not result and normalized:
        result = [normalized]
    return result[:8]


def source_label(source: str) -> str:
    return "美祢市例規" if source == "mine-city" else "地方自治法"


def text_snippet(text: str, keywords: list[str]) -> str:
    compact = re.sub(r"\s+", " ", text or "").strip()
    if not compact:
        return ""
    for keyword in keywords:
        idx = compact.find(keyword)
        if idx >= 0:
            start = max(0, idx - 50)
            end = min(len(compact), idx + 130)
            snippet = compact[start:end]
            if start > 0:
                snippet = f"…{snippet}"
            if end < len(compact):
                snippet = f"{snippet}…"
            return snippet
    return compact[:180] + ("…" if len(compact) > 180 else "")


def clean_html_text(text: str) -> str:
    lines = [normalize_text(line) for line in text.splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines)


def parse_japanese_date(text: str) -> str | None:
    m = re.search(r"(昭和|平成|令和)(\d+)年(\d+)月(\d+)日", text)
    if not m:
        return None
    era, y, mo, d = m.groups()
    year = int(y)
    base = {"昭和": 1925, "平成": 1988, "令和": 2018}.get(era)
    if not base:
        return None
    return f"{base + year:04d}-{int(mo):02d}-{int(d):02d}"


def deduce_law_type(title: str) -> str:
    for token in ["条例", "規則", "告示", "訓令", "要綱", "規程", "法律"]:
        if token in title:
            return token
    return "例規"


def node_text(node: Any, separator: str = "") -> str:
    if node is None:
        return ""
    return normalize_text(node.get_text(separator, strip=True))


def fetch_url_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": f"{APP_SLUG}/{APP_VERSION}"})
    with urllib.request.urlopen(req, timeout=60) as res:
        raw = res.read()
    for encoding in ["utf-8", "cp932", "shift_jis"]:
        try:
            return raw.decode(encoding)
        except Exception:
            continue
    return raw.decode("utf-8", "ignore")


def crawl_mine_city_index(start_url: str = MINE_CITY_INDEX_URL) -> list[dict[str, str]]:
    queue: list[tuple[str, list[str]]] = [(start_url, ["美祢市例規"])]
    seen_pages: set[str] = set()
    documents: dict[str, dict[str, str]] = {}
    while queue:
        url, trail = queue.pop(0)
        if url in seen_pages:
            continue
        seen_pages.add(url)
        html = fetch_url_text(url)
        soup = BeautifulSoup(html, "html.parser")
        for link in soup.select('a[href]'):
            href = (link.get('href') or '').strip()
            if not href:
                continue
            abs_url = urllib.parse.urljoin(url, href)
            parsed = urllib.parse.urlparse(abs_url)
            if parsed.netloc != 'www2.city.mine.lg.jp':
                continue
            label = normalize_text(link.get_text(" ", strip=True))
            if '/reiki_honbun/' in parsed.path and parsed.path.endswith('.html'):
                documents.setdefault(abs_url, {"url": abs_url, "category_path": " / ".join(trail), "title_hint": label})
            elif '/reiki_taikei/' in parsed.path and parsed.path.endswith('.html'):
                next_trail = trail + ([label] if label and label not in trail else [])
                if abs_url not in seen_pages:
                    queue.append((abs_url, next_trail))
        if len(seen_pages) > 500:
            break
    return list(documents.values())


def parse_mine_city_articles(root: BeautifulSoup) -> list[dict[str, str]]:
    articles: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for eline in root.select("div.eline"):
        article_block = eline.find("div", class_="article")
        if article_block is not None:
            num_p = article_block.find("p", class_="num")
            if not num_p:
                continue
            article_num_node = num_p.find("span", class_="num")
            article_number = node_text(article_num_node) or node_text(num_p)
            article_title = node_text(article_block.find("p", class_="title"))
            paragraph_text = node_text(num_p)
            if article_number and paragraph_text.startswith(article_number):
                paragraph_text = paragraph_text[len(article_number) :].lstrip(" 　")
            current = {
                "article_key": article_number,
                "article_number": article_number,
                "article_title": article_title,
                "parent_path": "",
                "parts": [paragraph_text] if paragraph_text else [],
            }
            articles.append(current)
            continue

        if current is None:
            continue

        for block_class in ("clause", "item", "subitem1", "subitem2", "subitem3", "subitem4", "subitem5", "subitem6", "subitem7", "subitem8", "subitem9", "table"):
            block = eline.find("div", class_=block_class)
            if block is None:
                continue
            text = node_text(block)
            if text:
                current["parts"].append(text)
            break

    return [
        {
            "article_key": str(article["article_key"]),
            "article_number": str(article["article_number"]),
            "article_title": str(article["article_title"]),
            "parent_path": str(article["parent_path"]),
            "text": "\n".join(part for part in article["parts"] if part).strip(),
        }
        for article in articles
    ]


def parse_mine_city_document(item: dict[str, str]) -> dict[str, Any]:
    html = fetch_url_text(item['url'])
    soup = BeautifulSoup(html, 'html.parser')
    title = normalize_text((soup.title.get_text(strip=True) if soup.title else item.get('title_hint') or ''))
    content_root = soup.select_one('#primaryInner2') or soup.body
    full_text = clean_html_text(content_root.get_text('\n', strip=True) if content_root else '')
    law_number = node_text(soup.select_one('p.number'))
    promulgated_at = parse_japanese_date(node_text(soup.select_one('p.date')))
    parsed = urllib.parse.urlparse(item['url'])
    external_id = Path(parsed.path).stem
    articles = parse_mine_city_articles(content_root) if content_root is not None else []
    return {
        'source': 'mine-city',
        'external_id': external_id,
        'title': title,
        'normalized_title': normalize_text(title).lower(),
        'law_type': deduce_law_type(title),
        'law_number': law_number,
        'category_path': item.get('category_path', ''),
        'source_url': item['url'],
        'promulgated_at': promulgated_at,
        'effective_at': None,
        'updated_at_source': now_iso(),
        'content_hash': make_content_hash(full_text),
        'full_text': full_text,
        'metadata_json': json.dumps({'title_hint': item.get('title_hint', '')}, ensure_ascii=False),
        'articles': articles,
    }


def iter_egov_articles(root: ET.Element) -> list[dict[str, str]]:
    articles: list[dict[str, str]] = []
    for article in root.findall('.//Article'):
        article_title = normalize_text(''.join(article.findtext('ArticleTitle', default='')))
        article_number = article_title or f"第{article.attrib.get('Num', '')}条"
        paragraphs: list[str] = []
        for paragraph in article.findall('Paragraph'):
            text = normalize_text(' '.join(''.join(paragraph.itertext()).split()))
            if text:
                paragraphs.append(text)
        articles.append({
            'article_key': article_number,
            'article_number': article_number,
            'article_title': '',
            'parent_path': '',
            'text': '\n'.join(paragraphs).strip() or normalize_text(' '.join(''.join(article.itertext()).split())),
        })
    return articles


def fetch_egov_document() -> dict[str, Any]:
    xml_text = fetch_url_text(EGOV_LAWDATA_URL)
    root = ET.fromstring(xml_text)
    law = root.find('.//Law')
    law_body = root.find('.//LawBody')
    law_title = normalize_text(''.join(law_body.findtext('LawTitle', default='')) if law_body is not None else '地方自治法')
    law_num = normalize_text(root.findtext('.//Law/LawNum', default=''))
    full_text = normalize_text(' '.join(''.join(root.find('.//LawFullText').itertext()).split()))
    promulgated_at = None
    if law is not None:
        try:
            promulgated_at = f"{int(law.attrib.get('Year', '0')) + 1925:04d}-{int(law.attrib.get('PromulgateMonth', '1')):02d}-{int(law.attrib.get('PromulgateDay', '1')):02d}"
        except Exception:
            promulgated_at = None
    return {
        'source': 'egov',
        'external_id': '322AC0000000067',
        'title': law_title,
        'normalized_title': normalize_text(law_title).lower(),
        'law_type': '法律',
        'law_number': law_num,
        'category_path': 'e-Gov法令検索',
        'source_url': 'https://laws.e-gov.go.jp/law/322AC0000000067',
        'promulgated_at': promulgated_at,
        'effective_at': None,
        'updated_at_source': now_iso(),
        'content_hash': make_content_hash(full_text),
        'full_text': full_text,
        'metadata_json': json.dumps({'lawId': '322AC0000000067'}, ensure_ascii=False),
        'articles': iter_egov_articles(root),
    }


def build_document_search_terms(document: dict[str, Any]) -> dict[str, int]:
    return limited_weighted_terms(
        (document.get("title", ""), 12, True),
        (document.get("law_number", ""), 8, True),
        (document.get("category_path", ""), 4, False),
        (document.get("law_type", ""), 4, True),
    )


def build_article_search_terms(document: dict[str, Any], article: dict[str, Any]) -> dict[str, int]:
    return limited_weighted_terms(
        (document.get("title", ""), 6, True),
        (article.get("article_number", ""), 12, True),
        (article.get("article_title", ""), 8, True),
        (article.get("text", ""), 2, False),
    )


def insert_search_terms(
    cur,
    target_type: str,
    target_id: int,
    document_id: int,
    article_id: int | None,
    terms: dict[str, int],
) -> None:
    if not terms:
        return
    for term, weight in terms.items():
        cur.execute(
            """
            INSERT INTO law_search_terms (target_type, target_id, document_id, article_id, term, weight)
            VALUES (%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE weight=VALUES(weight)
            """,
            (target_type, target_id, document_id, article_id, term, weight),
        )


def rebuild_search_terms_for_document(cur, document_id: int) -> None:
    cur.execute(
        """
        SELECT id, source, title, normalized_title, law_type, law_number, category_path, source_url, full_text
        FROM law_documents
        WHERE id=%s
        """,
        (document_id,),
    )
    doc = cur.fetchone()
    if not doc:
        return
    cur.execute(
        """
        SELECT id, article_number, article_title, text
        FROM law_articles
        WHERE document_id=%s
        ORDER BY sort_key ASC, id ASC
        """,
        (document_id,),
    )
    articles = cur.fetchall() or []
    document = {
        "title": doc.get("title") or "",
        "law_type": doc.get("law_type") or "",
        "law_number": doc.get("law_number") or "",
        "category_path": doc.get("category_path") or "",
    }
    doc_terms = build_document_search_terms(document)
    cur.execute(
        "UPDATE law_documents SET search_tokens=%s WHERE id=%s",
        (" ".join(doc_terms.keys()), document_id),
    )
    cur.execute("DELETE FROM law_search_terms WHERE document_id=%s", (document_id,))
    insert_search_terms(cur, "document", document_id, document_id, None, doc_terms)
    for article in articles:
        article_terms = build_article_search_terms(document, article)
        insert_search_terms(cur, "article", int(article["id"]), document_id, int(article["id"]), article_terms)


def maybe_backfill_search_terms() -> None:
    with db_cursor(commit=True) as (_, cur):
        cur.execute("SELECT COUNT(*) AS cnt FROM law_search_terms")
        term_count = int((cur.fetchone() or {}).get("cnt") or 0)
        if term_count > 0:
            return
        cur.execute("SELECT GET_LOCK('mine_city_reiki_backfill_search_terms', 0) AS locked")
        locked = int((cur.fetchone() or {}).get("locked") or 0)
        if locked != 1:
            return
        try:
            cur.execute("SELECT COUNT(*) AS cnt FROM law_search_terms")
            term_count = int((cur.fetchone() or {}).get("cnt") or 0)
            if term_count > 0:
                return
            cur.execute("SELECT id FROM law_documents ORDER BY id ASC")
            doc_ids = [int(row["id"]) for row in (cur.fetchall() or [])]
            for document_id in doc_ids:
                rebuild_search_terms_for_document(cur, document_id)
        finally:
            cur.execute("DO RELEASE_LOCK('mine_city_reiki_backfill_search_terms')")


def upsert_document(cur, document: dict[str, Any]) -> dict[str, int | bool]:
    document_terms = build_document_search_terms(document)
    document["search_tokens"] = " ".join(document_terms.keys())
    cur.execute(
        """
        SELECT id, content_hash
        FROM law_documents
        WHERE source=%s AND external_id=%s
        """,
        (document['source'], document['external_id']),
    )
    existing = cur.fetchone()
    if existing:
        document_id = int(existing['id'])
        changed = existing.get('content_hash') != document['content_hash']
        cur.execute(
            """
            UPDATE law_documents
            SET title=%s, normalized_title=%s, law_type=%s, law_number=%s, category_path=%s,
                source_url=%s, promulgated_at=%s, effective_at=%s, updated_at_source=%s, search_tokens=%s,
                content_hash=%s, full_text=%s, metadata_json=%s, updated_at=CURRENT_TIMESTAMP
            WHERE id=%s
            """,
            (
                document['title'], document['normalized_title'], document['law_type'], document['law_number'],
                document['category_path'], document['source_url'], document['promulgated_at'], document['effective_at'],
                document['updated_at_source'], document['search_tokens'], document['content_hash'], document['full_text'], document['metadata_json'], document_id,
            ),
        )
    else:
        changed = True
        cur.execute(
            """
            INSERT INTO law_documents (
              source, external_id, title, normalized_title, law_type, law_number, category_path,
              source_url, promulgated_at, effective_at, updated_at_source, search_tokens, content_hash, full_text, metadata_json
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                document['source'], document['external_id'], document['title'], document['normalized_title'], document['law_type'],
                document['law_number'], document['category_path'], document['source_url'], document['promulgated_at'], document['effective_at'],
                document['updated_at_source'], document['search_tokens'], document['content_hash'], document['full_text'], document['metadata_json'],
            ),
        )
        document_id = int(cur.lastrowid)
    if changed:
        cur.execute("DELETE FROM law_articles WHERE document_id=%s", (document_id,))
        for idx, article in enumerate(document.get('articles', []), start=1):
            cur.execute(
                """
                INSERT INTO law_articles (
                  document_id, article_key, article_number, article_title, parent_path, sort_key, text, search_text
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    document_id,
                    article['article_key'],
                    article['article_number'],
                    article.get('article_title', ''),
                    article.get('parent_path', ''),
                    idx,
                    article['text'],
                    normalize_text(f"{article['article_number']} {article.get('article_title', '')} {article['text']}").lower(),
                ),
            )
        rebuild_search_terms_for_document(cur, document_id)
    return {'document_id': document_id, 'changed': changed}


def set_sync_run_status(cur, run_id: int, status: str, summary: dict[str, Any] | None = None, error_text: str | None = None):
    cur.execute(
        """
        UPDATE sync_runs
        SET status=%s, finished_at=%s, summary_json=%s, error_text=%s
        WHERE id=%s
        """,
        (status, now_iso(), json.dumps(summary or {}, ensure_ascii=False), error_text, run_id),
    )


def get_sync_settings(cur) -> dict[str, Any]:
    cur.execute("SELECT * FROM sync_settings WHERE id=1")
    row = cur.fetchone()
    if row:
        return row
    cur.execute(
        "INSERT INTO sync_settings (id, enabled, day_of_month, hour, minute, timezone, source_scope) VALUES (1,0,1,3,0,%s,'all')",
        (TOKYO_OFFSET,),
    )
    cur.execute("SELECT * FROM sync_settings WHERE id=1")
    return cur.fetchone() or {}


def sync_status_payload(cur) -> dict[str, Any]:
    settings = get_sync_settings(cur)
    cur.execute("SELECT COUNT(*) AS cnt FROM law_documents")
    doc_count = int((cur.fetchone() or {}).get('cnt') or 0)
    cur.execute("SELECT COUNT(*) AS cnt FROM law_articles")
    article_count = int((cur.fetchone() or {}).get('cnt') or 0)
    cur.execute("SELECT COUNT(*) AS cnt FROM sync_runs")
    run_count = int((cur.fetchone() or {}).get('cnt') or 0)
    return {
        'enabled': bool(settings.get('enabled')),
        'dayOfMonth': int(settings.get('day_of_month') or 1),
        'hour': int(settings.get('hour') or 0),
        'minute': int(settings.get('minute') or 0),
        'timezone': settings.get('timezone') or TOKYO_OFFSET,
        'sourceScope': settings.get('source_scope') or 'all',
        'lastStartedAt': settings.get('last_started_at'),
        'lastFinishedAt': settings.get('last_finished_at'),
        'lastSuccessAt': settings.get('last_success_at'),
        'lastError': settings.get('last_error'),
        'documentCount': doc_count,
        'articleCount': article_count,
        'runCount': run_count,
    }


def should_run_monthly(settings: dict[str, Any], now_dt: datetime | None = None) -> bool:
    if not settings or not bool(settings.get('enabled')):
        return False
    now_dt = now_dt or now_tokyo()
    day = int(settings.get('day_of_month') or 1)
    hour = int(settings.get('hour') or 0)
    minute = int(settings.get('minute') or 0)
    year = now_dt.year
    month = now_dt.month
    from calendar import monthrange
    target_day = min(day, monthrange(year, month)[1])
    scheduled = now_dt.replace(day=target_day, hour=hour, minute=minute, second=0, microsecond=0)
    if now_dt < scheduled:
        return False
    last_success_raw = settings.get('last_success_at')
    if not last_success_raw:
        return True
    try:
        last_success = datetime.fromisoformat(str(last_success_raw).replace('Z', '+00:00')).astimezone(now_dt.tzinfo)
    except Exception:
        return True
    return last_success < scheduled


def execute_sync(run_type: str = 'manual', source_scope: str = 'all') -> dict[str, Any]:
    with db_cursor(commit=True) as (_, cur):
        settings = get_sync_settings(cur)
        cur.execute(
            "INSERT INTO sync_runs (run_type, status, started_at, summary_json) VALUES (%s,'running',%s,%s)",
            (run_type, now_iso(), json.dumps({}, ensure_ascii=False)),
        )
        run_id = int(cur.lastrowid)
        cur.execute(
            "UPDATE sync_settings SET last_started_at=%s, last_error=NULL WHERE id=1",
            (now_iso(),),
        )

    summary: dict[str, Any] = {'sourceScope': source_scope, 'documents': 0, 'updated': 0, 'articles': 0}
    try:
        with db_cursor(commit=True) as (_, cur):
            if source_scope in {'all', 'mine-city'}:
                items = crawl_mine_city_index()
                summary['mineCityCandidates'] = len(items)
                for item in items:
                    parsed = parse_mine_city_document(item)
                    result = upsert_document(cur, parsed)
                    summary['documents'] += 1
                    summary['updated'] += 1 if result['changed'] else 0
                    summary['articles'] += len(parsed.get('articles', []))
            if source_scope in {'all', 'egov'}:
                parsed = fetch_egov_document()
                result = upsert_document(cur, parsed)
                summary['documents'] += 1
                summary['updated'] += 1 if result['changed'] else 0
                summary['articles'] += len(parsed.get('articles', []))
            set_sync_run_status(cur, run_id, 'success', summary, None)
            cur.execute(
                "UPDATE sync_settings SET last_finished_at=%s, last_success_at=%s, last_error=NULL WHERE id=1",
                (now_iso(), now_iso()),
            )
        return summary
    except Exception as exc:
        with db_cursor(commit=True) as (_, cur):
            set_sync_run_status(cur, run_id, 'failed', summary, str(exc))
            cur.execute(
                "UPDATE sync_settings SET last_finished_at=%s, last_error=%s WHERE id=1",
                (now_iso(), str(exc)),
            )
        raise


def serialize_search_row(row: dict[str, Any], keywords: list[str]) -> dict[str, Any]:
    law_type = row.get('law_type') or ''
    snippet_text = row.get('article_text') or row.get('full_text') or ''
    return {
        'score': int(row.get('score') or 0),
        'documentId': int(row['document_id']),
        'articleId': int(row['article_id']) if row.get('article_id') else None,
        'source': row.get('source'),
        'title': row.get('title') or '',
        'lawType': law_type,
        'lawNumber': row.get('law_number') or '',
        'sourceUrl': row.get('source_url') or '',
        'articleNumber': row.get('article_number') or None,
        'articleTitle': row.get('article_title') or None,
        'snippet': text_snippet(snippet_text, keywords),
        'categoryPath': row.get('category_path') or '',
    }


def search_documents_slow(query: str, source: str = 'all', limit: int = 20) -> list[dict[str, Any]]:
    keywords = split_keywords(query)
    if not keywords:
        return []
    where_parts = []
    params: list[Any] = []
    if source != 'all':
        where_parts.append('d.source=%s')
        params.append(source)
    keyword_parts = []
    for keyword in keywords:
        like = f"%{keyword.lower()}%"
        keyword_parts.append("(d.normalized_title LIKE %s OR COALESCE(a.search_text,'') LIKE %s OR LOWER(COALESCE(d.full_text,'')) LIKE %s)")
        params.extend([like, like, like])
    if keyword_parts:
        where_parts.append('(' + ' OR '.join(keyword_parts) + ')')
    where_sql = ('WHERE ' + ' AND '.join(where_parts)) if where_parts else ''
    sql = f"""
        SELECT
          d.id AS document_id,
          d.source,
          d.title,
          d.law_type,
          d.law_number,
          d.source_url,
          d.category_path,
          d.full_text,
          a.id AS article_id,
          a.article_number,
          a.article_title,
          a.text AS article_text
        FROM law_documents d
        LEFT JOIN law_articles a ON a.document_id=d.id
        {where_sql}
        ORDER BY d.updated_at DESC, a.sort_key ASC
        LIMIT 500
    """
    with db_cursor() as (_, cur):
        cur.execute(sql, tuple(params))
        rows = cur.fetchall() or []
    scored: list[dict[str, Any]] = []
    normalized_query = normalize_text(query).lower()
    for row in rows:
        title = (row.get('title') or '').lower()
        article_no = (row.get('article_number') or '').lower()
        article_text = (row.get('article_text') or '').lower()
        doc_text = (row.get('full_text') or '').lower()
        score = 0
        if normalized_query and normalized_query in title:
            score += 120
        if normalized_query and normalized_query in article_text:
            score += 80
        for keyword in keywords:
            kw = keyword.lower()
            if kw in title:
                score += 50
            if kw in article_no:
                score += 35
            if kw in article_text:
                score += 20
            elif kw in doc_text:
                score += 8
        row['score'] = score
        if score > 0:
            scored.append(row)
    scored.sort(key=lambda x: (-int(x['score']), x.get('document_id') or 0, x.get('article_id') or 0))
    return [serialize_search_row(row, keywords) for row in scored[:limit]]


def fetch_search_detail_rows(
    article_candidates: list[dict[str, Any]],
    document_candidates: list[dict[str, Any]],
    keywords: list[str],
    normalized_query: str,
    limit: int,
) -> list[dict[str, Any]]:
    article_map = {
        int(item["article_id"]): {"term_score": int(item["term_score"]), "matched_terms": int(item["matched_terms"])}
        for item in article_candidates
    }
    document_map = {
        int(item["document_id"]): {"term_score": int(item["term_score"]), "matched_terms": int(item["matched_terms"])}
        for item in document_candidates
    }
    rows: list[dict[str, Any]] = []
    with db_cursor() as (_, cur):
        if article_map:
            article_ids = list(article_map.keys())
            placeholders = ",".join(["%s"] * len(article_ids))
            cur.execute(
                f"""
                SELECT
                  d.id AS document_id,
                  d.source,
                  d.title,
                  d.law_type,
                  d.law_number,
                  d.source_url,
                  d.category_path,
                  d.full_text,
                  a.id AS article_id,
                  a.article_number,
                  a.article_title,
                  a.text AS article_text
                FROM law_articles a
                JOIN law_documents d ON d.id=a.document_id
                WHERE a.id IN ({placeholders})
                """,
                tuple(article_ids),
            )
            rows.extend(cur.fetchall() or [])
        if document_map:
            document_ids = [doc_id for doc_id in document_map.keys() if doc_id not in {int(r["document_id"]) for r in rows}]
            if document_ids:
                placeholders = ",".join(["%s"] * len(document_ids))
                cur.execute(
                    f"""
                    SELECT
                      d.id AS document_id,
                      d.source,
                      d.title,
                      d.law_type,
                      d.law_number,
                      d.source_url,
                      d.category_path,
                      d.full_text,
                      NULL AS article_id,
                      NULL AS article_number,
                      NULL AS article_title,
                      NULL AS article_text
                    FROM law_documents d
                    WHERE d.id IN ({placeholders})
                    """,
                    tuple(document_ids),
                )
                rows.extend(cur.fetchall() or [])
    for row in rows:
        article_id = row.get("article_id")
        base = article_map.get(int(article_id)) if article_id else document_map.get(int(row["document_id"]))
        term_score = int((base or {}).get("term_score") or 0)
        matched_terms = int((base or {}).get("matched_terms") or 0)
        title = (row.get("title") or "").lower()
        article_no = (row.get("article_number") or "").lower()
        article_title = (row.get("article_title") or "").lower()
        article_text = (row.get("article_text") or "").lower()
        doc_text = (row.get("full_text") or "").lower()
        score = term_score * 10 + matched_terms * 12
        if normalized_query and normalized_query in title:
            score += 120
        if normalized_query and normalized_query in article_no:
            score += 90
        if normalized_query and normalized_query in article_title:
            score += 80
        if normalized_query and normalized_query in article_text:
            score += 50
        for keyword in keywords:
            kw = keyword.lower()
            if kw in title:
                score += 35
            if kw in article_no:
                score += 30
            if kw in article_title:
                score += 24
            if kw in article_text:
                score += 14
            elif kw in doc_text:
                score += 4
        row["score"] = score
    rows.sort(key=lambda item: (-int(item["score"]), -(item.get("article_id") is not None), int(item["document_id"]), int(item.get("article_id") or 0)))
    return [serialize_search_row(row, keywords) for row in rows[:limit]]


def search_documents(query: str, source: str = 'all', limit: int = 20) -> list[dict[str, Any]]:
    normalized_query = normalize_text(query).lower()
    keywords = split_keywords(query)
    terms = query_terms(query)
    if not terms:
        return []
    article_candidates: list[dict[str, Any]] = []
    document_candidates: list[dict[str, Any]] = []
    with db_cursor() as (_, cur):
        placeholders = ",".join(["%s"] * len(terms))
        params: list[Any] = list(terms)
        source_sql = ""
        if source != "all":
            source_sql = " AND d.source=%s"
            params.append(source)
        cur.execute(
            f"""
            SELECT
              st.document_id,
              st.article_id,
              SUM(st.weight) AS term_score,
              COUNT(*) AS matched_terms
            FROM law_search_terms st
            JOIN law_documents d ON d.id=st.document_id
            WHERE st.target_type='article' AND st.term IN ({placeholders}){source_sql}
            GROUP BY st.document_id, st.article_id
            ORDER BY term_score DESC, matched_terms DESC, st.document_id ASC, st.article_id ASC
            LIMIT 240
            """,
            tuple(params),
        )
        article_candidates = cur.fetchall() or []

        params = list(terms)
        if source != "all":
            params.append(source)
        cur.execute(
            f"""
            SELECT
              st.document_id,
              SUM(st.weight) AS term_score,
              COUNT(*) AS matched_terms
            FROM law_search_terms st
            JOIN law_documents d ON d.id=st.document_id
            WHERE st.target_type='document' AND st.term IN ({placeholders}){source_sql}
            GROUP BY st.document_id
            ORDER BY term_score DESC, matched_terms DESC, st.document_id ASC
            LIMIT 120
            """,
            tuple(params),
        )
        document_candidates = cur.fetchall() or []

    if not article_candidates and not document_candidates:
        return search_documents_slow(query, source, limit)
    results = fetch_search_detail_rows(article_candidates, document_candidates, keywords, normalized_query, limit)
    if not results:
        return search_documents_slow(query, source, limit)
    return results


@app.before_request
def enforce_auth():
    if not request.path.startswith('/api/'):
        return None
    if request.path == '/api/health':
        return None
    if request.method == 'OPTIONS':
        return ('', 204)
    status, payload = auth_verify()
    if status >= 400:
        return jsonify({'ok': False, 'error': 'login required'}), status
    if payload.get('enabled') is False:
        return None
    g.auth_user = payload.get('user') or {}
    if bool(g.auth_user.get('isGuest')) and request.method not in SAFE_METHODS:
        return jsonify({'ok': False, 'error': 'guest write blocked'}), 403
    return None


@app.errorhandler(ValueError)
def handle_value_error(err: ValueError):
    return jsonify({'ok': False, 'error': str(err)}), 400


@app.errorhandler(Exception)
def handle_exception(err: Exception):
    if isinstance(err, HTTPException):
        return jsonify({'ok': False, 'error': err.description}), err.code or 500
    app.logger.exception('Unhandled error')
    return jsonify({'ok': False, 'error': 'internal_error'}), 500


@app.get('/api/health')
def api_health():
    return jsonify({'ok': True, 'service': APP_SLUG, 'version': APP_VERSION})


@app.get('/api/sync/status')
def api_sync_status():
    with db_cursor() as (_, cur):
        return jsonify(sync_status_payload(cur))


@app.get('/api/sync/runs')
def api_sync_runs():
    with db_cursor() as (_, cur):
        cur.execute(
            "SELECT id, run_type, status, started_at, finished_at, summary_json, error_text FROM sync_runs ORDER BY id DESC LIMIT 30"
        )
        rows = cur.fetchall() or []
    items = []
    for row in rows:
        items.append(
            {
                'id': int(row['id']),
                'runType': row['run_type'],
                'status': row['status'],
                'startedAt': row['started_at'],
                'finishedAt': row['finished_at'],
                'summary': json.loads(row.get('summary_json') or '{}'),
                'errorText': row.get('error_text'),
            }
        )
    return jsonify({'items': items})


@app.put('/api/sync/settings')
def api_sync_settings_update():
    payload = request.get_json(silent=True) or {}
    enabled = 1 if bool(payload.get('enabled')) else 0
    day = max(1, min(31, int(payload.get('dayOfMonth') or 1)))
    hour = max(0, min(23, int(payload.get('hour') or 0)))
    minute = max(0, min(59, int(payload.get('minute') or 0)))
    source_scope = payload.get('sourceScope') or 'all'
    if source_scope not in {'all', 'mine-city', 'egov'}:
        raise ValueError('sourceScope が不正です。')
    with db_cursor(commit=True) as (_, cur):
        get_sync_settings(cur)
        cur.execute(
            """
            UPDATE sync_settings
            SET enabled=%s, day_of_month=%s, hour=%s, minute=%s, source_scope=%s, timezone=%s, updated_by=%s, updated_at=CURRENT_TIMESTAMP
            WHERE id=1
            """,
            (enabled, day, hour, minute, source_scope, TOKYO_OFFSET, actor_name()),
        )
        return jsonify(sync_status_payload(cur))


@app.post('/api/sync/run')
def api_sync_run():
    payload = request.get_json(silent=True) or {}
    scope = payload.get('sourceScope') or 'all'
    if scope not in {'all', 'mine-city', 'egov'}:
        raise ValueError('sourceScope が不正です。')
    summary = execute_sync('manual', scope)
    return jsonify({'ok': True, 'summary': summary})


@app.get('/api/search')
def api_search():
    query = (request.args.get('q') or '').strip()
    source = (request.args.get('source') or 'all').strip()
    limit = max(1, min(100, int(request.args.get('limit') or '20')))
    items = search_documents(query, source, limit) if query else []
    return jsonify({'items': items})


@app.get('/api/reference/search')
def api_reference_search():
    return api_search()


@app.get('/api/documents/<int:document_id>')
def api_document_detail(document_id: int):
    with db_cursor() as (_, cur):
        cur.execute(
            "SELECT id, source, external_id, title, law_type, law_number, category_path, source_url, promulgated_at, effective_at, updated_at_source, full_text FROM law_documents WHERE id=%s",
            (document_id,),
        )
        doc = cur.fetchone()
        if not doc:
            raise ValueError('該当する例規が見つかりません。')
        cur.execute(
            "SELECT id, article_key, article_number, article_title, parent_path, text FROM law_articles WHERE document_id=%s ORDER BY sort_key ASC, id ASC",
            (document_id,),
        )
        articles = cur.fetchall() or []
    return jsonify(
        {
            'id': int(doc['id']),
            'source': doc['source'],
            'externalId': doc['external_id'],
            'title': doc['title'],
            'lawType': doc['law_type'] or '',
            'lawNumber': doc['law_number'] or '',
            'categoryPath': doc['category_path'] or '',
            'sourceUrl': doc['source_url'],
            'promulgatedAt': doc['promulgated_at'],
            'effectiveAt': doc['effective_at'],
            'updatedAtSource': doc['updated_at_source'],
            'fullText': doc['full_text'],
            'articles': [
                {
                    'id': int(article['id']),
                    'articleKey': article['article_key'],
                    'articleNumber': article['article_number'],
                    'articleTitle': article['article_title'] or '',
                    'parentPath': article['parent_path'] or '',
                    'text': article['text'],
                }
                for article in articles
            ],
        }
    )


@app.get('/api/reference/document/<int:document_id>')
def api_reference_document(document_id: int):
    return api_document_detail(document_id)


@app.post('/api/ask')
def api_ask():
    payload = request.get_json(silent=True) or {}
    query = (payload.get('query') or '').strip()
    if not query:
        raise ValueError('query が必要です。')
    keywords = split_keywords(query)
    candidates = search_documents(query, 'all', 10)
    lead = '関連性の高い条文候補を表示します。運用判断は必ず原文を確認してください。'
    if candidates:
        top = candidates[0]
        lead = f"{source_label(top['source'])}の「{top['title']}」が最も関連すると推定されます。候補条文を上から確認してください。"
    return jsonify(
        {
            'query': query,
            'normalizedQuery': normalize_text(query),
            'keywords': keywords,
            'answerLead': lead,
            'candidates': candidates,
        }
    )


@app.get('/api/reference/ask')
def api_reference_ask_get():
    query = (request.args.get('q') or '').strip()
    if not query:
        raise ValueError('q が必要です。')
    with app.test_request_context('/api/ask', method='POST', json={'query': query}):
        return api_ask()


ensure_schema()
maybe_backfill_search_terms()


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=CFG.api_port, debug=True)
