from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import threading
import time
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
from flask import Flask, Response, g, jsonify, request
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
SEARCH_CACHE_TTL_SECONDS = 60 * 30
ASK_CACHE_TTL_SECONDS = 60 * 60 * 6
LOCAL_CACHE_TTL_SECONDS = 120
LOCAL_SEARCH_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
LOCAL_ASK_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
LOCAL_SYNONYM_CACHE: tuple[float, dict[str, list[str]]] | None = None
SYNC_THREAD_LOCK = threading.Lock()
BUILTIN_SYNONYM_GROUPS = [
    # ── 法令の種類・総称 ──────────────────────────────
    ("地方自治法", ["自治法", "自治体法"]),
    ("美祢市", ["本市", "市"]),
    ("条例", ["例規", "市条例"]),
    ("規則", ["例規", "市規則"]),
    ("要綱", ["例規", "実施要綱", "運用要綱"]),
    ("規程", ["例規", "内規", "規定"]),
    ("告示", ["例規", "公示"]),
    ("訓令", ["例規", "通達", "通知"]),
    ("協定", ["協約", "取決め"]),
    # ── 職員・人事 ───────────────────────────────────
    ("職員", ["職員等", "従業員", "公務員", "市職員"]),
    ("会計年度任用職員", ["会計年度職員", "任用職員", "非常勤職員", "パートタイム職員", "フルタイム職員"]),
    ("正規職員", ["常勤職員", "一般職員", "正職員"]),
    ("非常勤", ["パートタイム", "短時間勤務", "臨時職員"]),
    ("任用", ["採用", "雇用", "登用"]),
    ("分限", ["降格", "免職", "休職"]),
    ("懲戒", ["戒告", "減給", "停職", "免職"]),
    ("人事異動", ["異動", "転任", "配置換え", "転勤"]),
    ("研修", ["講習", "教育訓練", "人材育成"]),
    ("定年", ["定年退職", "停年", "退職年齢"]),
    ("退職", ["離職", "辞職", "免職"]),
    # ── 休暇・勤務時間 ───────────────────────────────
    ("休暇", ["休業", "休み", "欠勤", "休日"]),
    ("年次有給休暇", ["年休", "有給休暇", "有給", "年次休暇"]),
    ("特別休暇", ["特休", "慶弔休暇"]),
    ("育児休業", ["育休", "育児休暇", "子育て休業"]),
    ("介護休業", ["介護休暇", "介護のための休業"]),
    ("病気休暇", ["病休", "傷病休暇", "療養休暇"]),
    ("産前産後休業", ["産休", "出産休暇", "産前休業", "産後休業"]),
    ("勤務時間", ["就業時間", "労働時間", "業務時間", "勤務時間数"]),
    ("時間外勤務", ["残業", "超過勤務", "時間外労働"]),
    ("深夜勤務", ["夜間勤務", "深夜労働", "夜勤"]),
    ("休日勤務", ["休日出勤", "休日労働"]),
    ("代休", ["振替休日", "振休"]),
    # ── 給与・手当 ───────────────────────────────────
    ("給与", ["給料", "報酬", "賃金", "俸給", "手当"]),
    ("給料", ["基本給", "本俸", "月給"]),
    ("手当", ["給付", "支給", "補助"]),
    ("扶養手当", ["家族手当", "扶養給付"]),
    ("住居手当", ["住宅手当", "家賃補助"]),
    ("通勤手当", ["交通費", "通勤費"]),
    ("時間外手当", ["残業手当", "超過勤務手当", "割増賃金"]),
    ("期末手当", ["賞与", "ボーナス", "一時金"]),
    ("勤勉手当", ["成績手当", "業績手当"]),
    ("管理職手当", ["管理職員手当", "管理監督者手当"]),
    ("退職手当", ["退職金", "退職給付"]),
    # ── 財政・会計 ───────────────────────────────────
    ("予算", ["当初予算", "補正予算", "財政計画"]),
    ("決算", ["歳入歳出決算", "年度決算"]),
    ("歳入", ["収入", "税収", "財源"]),
    ("歳出", ["支出", "経費", "財政支出"]),
    ("一般会計", ["普通会計"]),
    ("特別会計", ["企業会計", "事業会計"]),
    ("補助金", ["交付金", "助成金", "補助", "給付金"]),
    ("負担金", ["分担金", "拠出金"]),
    ("使用料", ["利用料", "料金", "手数料"]),
    ("財産", ["市有財産", "公有財産", "行政財産", "普通財産"]),
    ("契約", ["請負契約", "委託契約", "売買契約", "協定"]),
    ("入札", ["競争入札", "一般競争入札", "指名競争入札", "随意契約"]),
    ("監査", ["会計検査", "内部監査", "外部監査"]),
    # ── 行政・組織 ───────────────────────────────────
    ("市長", ["首長", "長", "行政の長"]),
    ("副市長", ["助役", "副長"]),
    ("教育委員会", ["教委", "教育行政機関"]),
    ("行政委員会", ["委員会", "附属機関"]),
    ("審議会", ["諮問機関", "委員会", "審査会", "協議会"]),
    ("議会", ["市議会", "議員", "議員会"]),
    ("委員会", ["特別委員会", "常任委員会"]),
    ("許可", ["認可", "承認", "許諾", "認定"]),
    ("申請", ["届出", "申込", "請求", "申立"]),
    ("届出", ["申請", "報告", "届"]),
    ("処分", ["行政処分", "決定", "措置"]),
    ("不服申立", ["異議申立", "審査請求", "行政不服申立"]),
    # ── 福祉・社会保障 ───────────────────────────────
    ("福祉", ["社会福祉", "厚生", "生活支援"]),
    ("介護", ["介護保険", "介護サービス", "要介護"]),
    ("障害", ["障がい", "障碍", "ハンディキャップ"]),
    ("障害者", ["障がい者", "障碍者", "身体障害者", "知的障害者"]),
    ("高齢者", ["老人", "シニア", "高齢市民"]),
    ("児童", ["子ども", "子供", "未成年者", "少年"]),
    ("生活保護", ["保護", "生活扶助", "公的扶助"]),
    ("保育", ["保育所", "保育園", "子育て支援"]),
    ("医療", ["診療", "医療機関", "病院", "医療サービス"]),
    ("国民健康保険", ["国保", "健保", "医療保険"]),
    # ── 土地・都市計画 ───────────────────────────────
    ("土地", ["宅地", "農地", "山林", "用地"]),
    ("道路", ["市道", "公道", "道路法"]),
    ("河川", ["水路", "用水路", "準用河川"]),
    ("公園", ["都市公園", "緑地", "広場"]),
    ("開発", ["開発行為", "宅地開発", "土地開発"]),
    ("建築", ["建物", "建設", "工事"]),
    ("地区計画", ["都市計画", "まちづくり", "区域"]),
    # ── 個人情報・情報管理 ──────────────────────────
    ("個人情報", ["個人情報保護", "プライバシー", "個人データ"]),
    ("情報公開", ["開示", "公開請求", "情報開示"]),
    ("マイナンバー", ["個人番号", "社会保障番号", "番号制度"]),
    # ── 環境・廃棄物 ─────────────────────────────────
    ("廃棄物", ["ごみ", "廃棄", "産業廃棄物", "一般廃棄物"]),
    ("環境", ["環境保全", "環境保護", "自然環境"]),
    ("騒音", ["振動", "公害", "生活環境"]),
    # ── 手続き・総則 ─────────────────────────────────
    ("施行", ["実施", "施行日", "適用"]),
    ("改正", ["改定", "修正", "改訂"]),
    ("廃止", ["失効", "撤廃", "廃案"]),
    ("附則", ["経過措置", "経過規定"]),
    ("委任", ["専決", "権限委任", "委任規定"]),
    ("罰則", ["罰金", "過料", "行政罰"]),
    ("遵守", ["順守", "履行", "義務"]),
]


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
            ensure_column(cur, "sync_settings", "cache_generation", "cache_generation BIGINT UNSIGNED NOT NULL DEFAULT 1 AFTER source_scope")
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
            ensure_table(
                cur,
                "law_synonyms",
                """
                CREATE TABLE law_synonyms (
                  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                  canonical_term VARCHAR(191) NOT NULL,
                  synonym_term VARCHAR(191) NOT NULL,
                  priority TINYINT UNSIGNED NOT NULL DEFAULT 10,
                  is_active TINYINT(1) NOT NULL DEFAULT 1,
                  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                  UNIQUE KEY uq_law_synonyms_pair (canonical_term, synonym_term),
                  KEY idx_law_synonyms_canonical (canonical_term, is_active),
                  KEY idx_law_synonyms_synonym (synonym_term, is_active)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """,
            )
            ensure_table(
                cur,
                "law_document_history",
                """
                CREATE TABLE law_document_history (
                  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                  document_id BIGINT UNSIGNED NOT NULL,
                  content_hash CHAR(64) NOT NULL,
                  title VARCHAR(255) NOT NULL,
                  law_number VARCHAR(128) NOT NULL DEFAULT '',
                  promulgated_at DATE NULL,
                  updated_at_source VARCHAR(64) NOT NULL DEFAULT '',
                  full_text LONGTEXT NULL,
                  changed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  KEY idx_law_document_history_document (document_id, changed_at),
                  CONSTRAINT fk_law_document_history_document FOREIGN KEY (document_id) REFERENCES law_documents(id) ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """,
            )
            ensure_column(
                cur,
                "law_document_history",
                "full_text",
                "full_text LONGTEXT NULL AFTER updated_at_source",
            )
            ensure_table(
                cur,
                "search_query_cache",
                """
                CREATE TABLE search_query_cache (
                  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                  cache_key CHAR(64) NOT NULL,
                  normalized_query VARCHAR(255) NOT NULL,
                  source_scope ENUM('all','mine-city','egov') NOT NULL DEFAULT 'all',
                  limit_n SMALLINT UNSIGNED NOT NULL DEFAULT 20,
                  cache_generation BIGINT UNSIGNED NOT NULL DEFAULT 1,
                  result_json LONGTEXT NOT NULL,
                  hit_count INT UNSIGNED NOT NULL DEFAULT 0,
                  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  last_hit_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  expires_at TIMESTAMP NULL DEFAULT NULL,
                  UNIQUE KEY uq_search_query_cache_key (cache_key),
                  KEY idx_search_query_cache_lookup (cache_generation, source_scope, limit_n),
                  KEY idx_search_query_cache_expires (expires_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """,
            )
            ensure_table(
                cur,
                "ask_query_cache",
                """
                CREATE TABLE ask_query_cache (
                  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                  cache_key CHAR(64) NOT NULL,
                  normalized_query VARCHAR(255) NOT NULL,
                  cache_generation BIGINT UNSIGNED NOT NULL DEFAULT 1,
                  response_json LONGTEXT NOT NULL,
                  hit_count INT UNSIGNED NOT NULL DEFAULT 0,
                  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  last_hit_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  expires_at TIMESTAMP NULL DEFAULT NULL,
                  UNIQUE KEY uq_ask_query_cache_key (cache_key),
                  KEY idx_ask_query_cache_generation (cache_generation),
                  KEY idx_ask_query_cache_expires (expires_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """,
            )
            seed_law_synonyms(cur)
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


def katakana_to_hiragana(text: str) -> str:
    """カタカナをひらがなに変換する。"""
    return "".join(chr(ord(c) - 0x60) if "\u30a1" <= c <= "\u30f3" else c for c in text)


def janome_reading_terms(text: str) -> list[str]:
    """テキスト中の名詞・動詞・形容詞の読み（ひらがな）を返す。"""
    tokenizer = get_janome_tokenizer()
    if tokenizer is None:
        return []
    terms: list[str] = []
    seen: set[str] = set()
    try:
        for token in tokenizer.tokenize(text):
            pos = (token.part_of_speech or "").split(",")[0]
            if pos not in {"名詞", "動詞", "形容詞"}:
                continue
            reading = getattr(token, "reading", "") or ""
            if reading == "*":
                reading = ""
            hira = katakana_to_hiragana(normalize_text(reading)).lower()
            if not hira or len(hira) < 2 or hira in STOP_TERMS or not contains_japanese(hira):
                continue
            if hira not in seen:
                seen.add(hira)
                terms.append(hira)
    except Exception:
        return []
    return terms


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


def trim_text_for_indexing(text: str, max_chars: int = 4000) -> str:
    normalized = normalize_text(text)
    if len(normalized) <= max_chars:
        return normalized
    head = max_chars // 2
    tail = max_chars - head
    return f"{normalized[:head]}\n{normalized[-tail:]}"


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
        # 読み（ひらがな）を低ウェイトで追加（表記が漢字でも読みで検索できるようにする）
        reading_weight = max(1, int(weight * 0.6))
        for term in janome_reading_terms(normalized):
            weights[term] = max(weights.get(term, 0), reading_weight)
    ranked = sorted(weights.items(), key=lambda item: (-item[1], len(item[0]), item[0]))
    return dict(ranked[:max_terms])


def query_terms(query: str, cur=None) -> list[str]:
    base_keywords = split_keywords(query)
    expanded_keywords = expand_keywords_with_synonyms(base_keywords, cur=cur, max_keywords=20)
    weighted_groups: list[tuple[str, int, bool]] = [(query, 12, True)]
    for keyword in expanded_keywords:
        weighted_groups.append((keyword, 10 if keyword in {normalize_text(query).lower()} else 7, True))
    terms = list(limited_weighted_terms(*weighted_groups, max_terms=40).keys())
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


def seed_law_synonyms(cur) -> None:
    for canonical_term, synonyms in BUILTIN_SYNONYM_GROUPS:
        for synonym_term in synonyms:
            cur.execute(
                """
                INSERT IGNORE INTO law_synonyms (canonical_term, synonym_term, priority, is_active)
                VALUES (%s,%s,%s,1)
                """,
                (canonical_term, synonym_term, 10),
            )


def prune_expired_caches(cur) -> None:
    cur.execute("DELETE FROM search_query_cache WHERE expires_at IS NOT NULL AND expires_at < CURRENT_TIMESTAMP")
    cur.execute("DELETE FROM ask_query_cache WHERE expires_at IS NOT NULL AND expires_at < CURRENT_TIMESTAMP")


def clear_local_caches() -> None:
    LOCAL_SEARCH_CACHE.clear()
    LOCAL_ASK_CACHE.clear()
    global LOCAL_SYNONYM_CACHE
    LOCAL_SYNONYM_CACHE = None


def get_cache_generation(cur) -> int:
    settings = get_sync_settings(cur)
    return int(settings.get("cache_generation") or 1)


def bump_cache_generation(cur) -> int:
    cur.execute("UPDATE sync_settings SET cache_generation = cache_generation + 1 WHERE id=1")
    cur.execute("SELECT cache_generation FROM sync_settings WHERE id=1")
    row = cur.fetchone() or {}
    clear_local_caches()
    return int(row.get("cache_generation") or 1)


def synonyms_map(cur=None) -> dict[str, list[str]]:
    global LOCAL_SYNONYM_CACHE
    now = time.time()
    if LOCAL_SYNONYM_CACHE and now - LOCAL_SYNONYM_CACHE[0] < LOCAL_CACHE_TTL_SECONDS:
        return LOCAL_SYNONYM_CACHE[1]
    synonym_groups: list[tuple[str, str]] = []
    if cur is not None:
        cur.execute(
            """
            SELECT canonical_term, synonym_term
            FROM law_synonyms
            WHERE is_active=1
            ORDER BY priority DESC, id ASC
            """
        )
        synonym_groups = [
            (normalize_text(row["canonical_term"]).lower(), normalize_text(row["synonym_term"]).lower())
            for row in (cur.fetchall() or [])
            if normalize_text(row.get("canonical_term") or "") and normalize_text(row.get("synonym_term") or "")
        ]
    else:
        with db_cursor() as (_, inner_cur):
            return synonyms_map(inner_cur)
    graph: dict[str, set[str]] = {}
    for canonical_term, synonym_term in synonym_groups:
        graph.setdefault(canonical_term, set()).add(synonym_term)
        graph.setdefault(synonym_term, set()).add(canonical_term)
    result = {key: sorted(values) for key, values in graph.items()}
    LOCAL_SYNONYM_CACHE = (now, result)
    return result


def make_content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


QUESTION_TYPE_PATTERNS: list[tuple[str, list[str]]] = [
    ("eligibility", ["できますか", "できるか", "できるでしょうか", "可能ですか", "権利があります", "資格があります", "受けられます", "対象になります"]),
    ("procedure",   ["手続き", "申請", "どうすれば", "どのようにすれば", "どのように手続き", "方法を", "方法は", "どうしたら", "どこに申請"]),
    ("definition",  ["とは何ですか", "とはなんですか", "とはどういう", "とは何か", "の定義", "の意味", "について教えて", "とはどのよう"]),
    ("period",      ["いつから", "いつまで", "期間は", "何日間", "何ヶ月", "何年間", "いつ", "期限は"]),
    ("amount",      ["いくら", "何円", "金額は", "額は", "いくつ", "何日", "何時間", "何割", "何パーセント"]),
    ("location",    ["どこで", "どこに", "窓口は", "場所は", "どの部署", "どの課"]),
]

QUESTION_TYPE_LABELS: dict[str, str] = {
    "eligibility": "権利・対象資格に関する質問",
    "procedure":   "手続き・申請方法に関する質問",
    "definition":  "定義・内容に関する質問",
    "period":      "期間・時期に関する質問",
    "amount":      "金額・日数・数量に関する質問",
    "location":    "場所・窓口に関する質問",
    "general":     "一般的な質問",
}

QUESTION_SUFFIXES = [
    "ですか", "ますか", "でしょうか", "だろうか", "でしょう",
    "を教えてください", "を教えて", "について教えてください", "について教えて",
    "はどうすればいいですか", "はどうすれば", "はどのようにすれば", "はどのように",
    "はどこに", "はいつ", "とはなんですか", "とは何ですか", "はどういうこと",
]


def detect_question_type(query: str) -> str:
    normalized = normalize_text(query).lower()
    for qtype, patterns in QUESTION_TYPE_PATTERNS:
        for pattern in patterns:
            if pattern in normalized:
                return qtype
    return "general"


def extract_question_keywords(query: str) -> list[str]:
    """質問語尾・助詞を除去して内容語キーワードを抽出する。"""
    normalized = normalize_text(query)
    cleaned = normalized
    for suffix in QUESTION_SUFFIXES:
        cleaned = re.sub(re.escape(suffix) + r"[？?。]*$", "", cleaned).strip()
    keywords = split_keywords(cleaned) if cleaned else split_keywords(query)
    # 元の質問からも分割して補完（短くなりすぎた場合の保険）
    if len(keywords) < 2:
        keywords = split_keywords(query)
    return keywords


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


def expand_keywords_with_synonyms(keywords: list[str], cur=None, max_keywords: int = 16) -> list[str]:
    expanded: list[str] = []
    seen: set[str] = set()
    synonym_lookup = synonyms_map(cur)
    for keyword in keywords:
        token = normalize_text(keyword).lower()
        if not token or token in seen:
            continue
        seen.add(token)
        expanded.append(token)
        for synonym in synonym_lookup.get(token, []):
            if synonym and synonym not in seen:
                seen.add(synonym)
                expanded.append(synonym)
        for canonical, linked in synonym_lookup.items():
            if token in linked and canonical not in seen:
                seen.add(canonical)
                expanded.append(canonical)
        if len(expanded) >= max_keywords:
            break
    return expanded[:max_keywords]


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


def serialize_table_block(block: Any) -> str:
    """table div ブロックをタブ区切り行形式のマーカーとしてシリアライズする。"""
    table_elem = block.find("table")
    if table_elem is None:
        return node_text(block, "\n")
    rows: list[str] = []
    for tr in table_elem.find_all("tr"):
        cells = [normalize_text(td.get_text(" ", strip=True)) for td in tr.find_all(["td", "th"])]
        rows.append("\t".join(cells))
    if not rows:
        return node_text(block, "\n")
    return "__TABLE_START__\n" + "\n".join(rows) + "\n__TABLE_END__"


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
            if block_class == "table":
                text = serialize_table_block(block)
            else:
                text = node_text(block, "")
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
    # 文書全文は非常に長くなり得るため、索引生成時は先頭と末尾の抜粋に絞る。
    full_text_excerpt = trim_text_for_indexing(document.get("full_text", ""), max_chars=4000)
    return limited_weighted_terms(
        (document.get("title", ""), 12, True),
        (document.get("law_number", ""), 8, True),
        (document.get("category_path", ""), 4, False),
        (document.get("law_type", ""), 4, True),
        (full_text_excerpt, 1, False),
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
        "full_text": doc.get("full_text") or "",
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
    is_new = existing is None
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
        cur.execute(
            """
            INSERT INTO law_document_history (document_id, content_hash, title, law_number, promulgated_at, updated_at_source, full_text)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                document_id,
                document.get('content_hash', ''),
                document.get('title', ''),
                document.get('law_number', ''),
                document.get('promulgated_at'),
                document.get('updated_at_source', ''),
                document.get('full_text', ''),
            ),
        )
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
    return {'document_id': document_id, 'changed': changed, 'is_new': is_new}


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
    # ソース別カウント
    cur.execute("SELECT COUNT(*) AS cnt FROM law_documents WHERE source='mine-city'")
    mc_doc_count = int((cur.fetchone() or {}).get('cnt') or 0)
    cur.execute(
        "SELECT COUNT(*) AS cnt FROM law_articles a JOIN law_documents d ON d.id=a.document_id WHERE d.source='mine-city'"
    )
    mc_article_count = int((cur.fetchone() or {}).get('cnt') or 0)
    cur.execute("SELECT COUNT(*) AS cnt FROM law_documents WHERE source='egov'")
    egov_doc_count = int((cur.fetchone() or {}).get('cnt') or 0)
    cur.execute(
        "SELECT COUNT(*) AS cnt FROM law_articles a JOIN law_documents d ON d.id=a.document_id WHERE d.source='egov'"
    )
    egov_article_count = int((cur.fetchone() or {}).get('cnt') or 0)
    # ソース別 最新改定ドキュメント (updated_at 降順 top5)
    def _latest_revisions(source: str) -> list[dict[str, Any]]:
        cur.execute(
            """
            SELECT id, title, law_type, law_number, promulgated_at, updated_at, source_url
            FROM law_documents
            WHERE source=%s
            ORDER BY updated_at DESC
            LIMIT 5
            """,
            (source,),
        )
        rows = cur.fetchall() or []
        return [
            {
                'id': int(r['id']),
                'title': r['title'],
                'lawType': r['law_type'] or '',
                'lawNumber': r['law_number'] or '',
                'promulgatedAt': str(r['promulgated_at']) if r['promulgated_at'] else None,
                'updatedAt': str(r['updated_at']) if r['updated_at'] else None,
                'sourceUrl': r['source_url'],
            }
            for r in rows
        ]
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
        'mineCityDocumentCount': mc_doc_count,
        'mineCityArticleCount': mc_article_count,
        'egovDocumentCount': egov_doc_count,
        'egovArticleCount': egov_article_count,
        'mineCityLatestRevisions': _latest_revisions('mine-city'),
        'egovLatestRevisions': _latest_revisions('egov'),
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

    summary: dict[str, Any] = {
        'sourceScope': source_scope,
        'documents': 0, 'added': 0, 'updated': 0, 'unchanged': 0, 'articles': 0,
    }
    try:
        with db_cursor(commit=True) as (_, cur):
            if source_scope in {'all', 'mine-city'}:
                items = crawl_mine_city_index()
                summary['mineCityCandidates'] = len(items)
                for item in items:
                    parsed = parse_mine_city_document(item)
                    result = upsert_document(cur, parsed)
                    summary['documents'] += 1
                    if result['changed']:
                        if result.get('is_new'):
                            summary['added'] += 1
                        else:
                            summary['updated'] += 1
                    else:
                        summary['unchanged'] += 1
                    summary['articles'] += len(parsed.get('articles', []))
            if source_scope in {'all', 'egov'}:
                parsed = fetch_egov_document()
                result = upsert_document(cur, parsed)
                summary['documents'] += 1
                if result['changed']:
                    if result.get('is_new'):
                        summary['added'] += 1
                    else:
                        summary['updated'] += 1
                else:
                    summary['unchanged'] += 1
                summary['articles'] += len(parsed.get('articles', []))
            if int(summary.get("updated") or 0) > 0 or int(summary.get("added") or 0) > 0:
                bump_cache_generation(cur)
                prune_expired_caches(cur)
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


def launch_sync_in_background(source_scope: str) -> None:
    def _runner() -> None:
        try:
            execute_sync('manual', source_scope)
        except Exception:
            app.logger.exception('Background sync failed')

    thread = threading.Thread(
        target=_runner,
        name=f"mine-city-reiki-sync-{source_scope}",
        daemon=True,
    )
    with SYNC_THREAD_LOCK:
        thread.start()


def make_cache_key(parts: list[str]) -> str:
    payload = "\u241f".join(parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def get_local_cache(cache: dict[str, tuple[float, Any]], key: str):
    entry = cache.get(key)
    if not entry:
        return None
    cached_at, payload = entry
    if time.time() - cached_at > LOCAL_CACHE_TTL_SECONDS:
        cache.pop(key, None)
        return None
    return payload


def put_local_cache(cache: dict[str, tuple[float, Any]], key: str, payload: Any) -> None:
    cache[key] = (time.time(), payload)


def get_search_cache(cur, cache_key: str):
    payload = get_local_cache(LOCAL_SEARCH_CACHE, cache_key)
    if payload is not None:
        return payload
    cur.execute(
        """
        SELECT result_json
        FROM search_query_cache
        WHERE cache_key=%s AND (expires_at IS NULL OR expires_at >= CURRENT_TIMESTAMP)
        """,
        (cache_key,),
    )
    row = cur.fetchone()
    if not row:
        return None
    cur.execute(
        "UPDATE search_query_cache SET hit_count=hit_count+1, last_hit_at=CURRENT_TIMESTAMP WHERE cache_key=%s",
        (cache_key,),
    )
    payload = json.loads(row.get("result_json") or "[]")
    put_local_cache(LOCAL_SEARCH_CACHE, cache_key, payload)
    return payload


def put_search_cache(cur, cache_key: str, normalized_query: str, source: str, limit: int, generation: int, payload: list[dict[str, Any]]) -> None:
    result_json = json.dumps(payload, ensure_ascii=False)
    cur.execute(
        """
        INSERT INTO search_query_cache
          (cache_key, normalized_query, source_scope, limit_n, cache_generation, result_json, hit_count, last_hit_at, expires_at)
        VALUES (%s,%s,%s,%s,%s,%s,0,CURRENT_TIMESTAMP,DATE_ADD(CURRENT_TIMESTAMP, INTERVAL %s SECOND))
        ON DUPLICATE KEY UPDATE
          normalized_query=VALUES(normalized_query),
          source_scope=VALUES(source_scope),
          limit_n=VALUES(limit_n),
          cache_generation=VALUES(cache_generation),
          result_json=VALUES(result_json),
          last_hit_at=CURRENT_TIMESTAMP,
          expires_at=VALUES(expires_at)
        """,
        (cache_key, normalized_query, source, limit, generation, result_json, SEARCH_CACHE_TTL_SECONDS),
    )
    put_local_cache(LOCAL_SEARCH_CACHE, cache_key, payload)


def get_ask_cache(cur, cache_key: str):
    payload = get_local_cache(LOCAL_ASK_CACHE, cache_key)
    if payload is not None:
        return payload
    cur.execute(
        """
        SELECT response_json
        FROM ask_query_cache
        WHERE cache_key=%s AND (expires_at IS NULL OR expires_at >= CURRENT_TIMESTAMP)
        """,
        (cache_key,),
    )
    row = cur.fetchone()
    if not row:
        return None
    cur.execute(
        "UPDATE ask_query_cache SET hit_count=hit_count+1, last_hit_at=CURRENT_TIMESTAMP WHERE cache_key=%s",
        (cache_key,),
    )
    payload = json.loads(row.get("response_json") or "{}")
    put_local_cache(LOCAL_ASK_CACHE, cache_key, payload)
    return payload


def put_ask_cache(cur, cache_key: str, normalized_query: str, generation: int, payload: dict[str, Any]) -> None:
    response_json = json.dumps(payload, ensure_ascii=False)
    cur.execute(
        """
        INSERT INTO ask_query_cache
          (cache_key, normalized_query, cache_generation, response_json, hit_count, last_hit_at, expires_at)
        VALUES (%s,%s,%s,%s,0,CURRENT_TIMESTAMP,DATE_ADD(CURRENT_TIMESTAMP, INTERVAL %s SECOND))
        ON DUPLICATE KEY UPDATE
          normalized_query=VALUES(normalized_query),
          cache_generation=VALUES(cache_generation),
          response_json=VALUES(response_json),
          last_hit_at=CURRENT_TIMESTAMP,
          expires_at=VALUES(expires_at)
        """,
        (cache_key, normalized_query, generation, response_json, ASK_CACHE_TTL_SECONDS),
    )
    put_local_cache(LOCAL_ASK_CACHE, cache_key, payload)


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
        'matchReasons': row.get('match_reasons') or [],
        'promulgatedAt': str(row['promulgated_at']) if row.get('promulgated_at') else None,
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
    offset: int = 0,
) -> tuple[int, list[dict[str, Any]]]:
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
                  d.promulgated_at,
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
                      d.promulgated_at,
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
        match_reasons: list[str] = []
        if normalized_query and normalized_query in title:
            score += 120
            match_reasons.append("タイトル")
        if normalized_query and normalized_query in article_no:
            score += 90
            match_reasons.append("条番号")
        if normalized_query and normalized_query in article_title:
            score += 80
            if "条名" not in match_reasons:
                match_reasons.append("条名")
        if normalized_query and normalized_query in article_text:
            score += 50
            if "条文" not in match_reasons:
                match_reasons.append("条文")
        for keyword in keywords:
            kw = keyword.lower()
            if kw in title and "タイトル" not in match_reasons:
                match_reasons.append("タイトル")
            if kw in article_no and "条番号" not in match_reasons:
                match_reasons.append("条番号")
            if kw in article_title and "条名" not in match_reasons:
                match_reasons.append("条名")
            if kw in article_text:
                score += 14
                if "条文" not in match_reasons:
                    match_reasons.append("条文")
            elif kw in doc_text:
                score += 4
                if "本文" not in match_reasons:
                    match_reasons.append("本文")
            if kw in title:
                score += 35
            if kw in article_no:
                score += 30
            if kw in article_title:
                score += 24
        row["score"] = score
        row["match_reasons"] = match_reasons
    rows.sort(key=lambda item: (-int(item["score"]), -(item.get("article_id") is not None), int(item["document_id"]), int(item.get("article_id") or 0)))
    total = len(rows)
    sliced = rows[offset:offset + limit]
    return total, [serialize_search_row(row, keywords) for row in sliced]


def _filter_doc_ids_by_meta(
    doc_ids: set[int],
    cur,
    law_type: str = '',
    from_date: str = '',
    to_date: str = '',
) -> set[int]:
    """doc_ids を法令種別・公布日でさらに絞り込む。"""
    if not doc_ids or (not law_type and not from_date and not to_date):
        return doc_ids
    placeholders = ",".join(["%s"] * len(doc_ids))
    conditions: list[str] = [f"id IN ({placeholders})"]
    params: list[Any] = list(doc_ids)
    if law_type:
        conditions.append("law_type=%s")
        params.append(law_type)
    if from_date:
        conditions.append("(promulgated_at IS NULL OR promulgated_at >= %s)")
        params.append(from_date)
    if to_date:
        conditions.append("(promulgated_at IS NULL OR promulgated_at <= %s)")
        params.append(to_date)
    cur.execute(f"SELECT id FROM law_documents WHERE {' AND '.join(conditions)}", params)
    return {int(r["id"]) for r in (cur.fetchall() or [])}


def _doc_ids_for_keyword(keyword: str, source: str, cur) -> set[int]:
    """転置インデックスから1キーワードにマッチする document_id の集合を返す。"""
    norm = normalize_text(keyword).lower()
    if not norm:
        return set()
    terms = list(limited_weighted_terms((norm, 10, True), max_terms=30).keys())
    for syn in expand_keywords_with_synonyms([norm], cur=cur, max_keywords=6):
        if syn != norm:
            terms.extend(limited_weighted_terms((syn, 8, True), max_terms=10).keys())
    terms = list(set(terms))
    if not terms:
        return set()
    placeholders = ",".join(["%s"] * len(terms))
    source_filter = " AND d.source=%s" if source != "all" else ""
    params: list[Any] = terms + ([source] if source != "all" else [])
    cur.execute(
        f"SELECT DISTINCT st.document_id"
        f" FROM law_search_terms st JOIN law_documents d ON d.id=st.document_id"
        f" WHERE st.term IN ({placeholders}){source_filter}",
        params,
    )
    return {int(r["document_id"]) for r in cur.fetchall()}


def _doc_ids_for_field(field_q: str, source: str, cur) -> set[int] | None:
    """フィールド内の全キーワード（スペース区切り）をAND結合した document_id 集合を返す。
    フィールドが空なら None を返す（制約なし扱い）。"""
    keywords = [k for k in normalize_text(field_q).lower().split() if k]
    if not keywords:
        return None
    sets = [_doc_ids_for_keyword(k, source, cur) for k in keywords]
    result = sets[0]
    for s in sets[1:]:
        result &= s
    return result


def search_documents_structured(
    fields: list[dict[str, str]],
    source: str = "all",
    limit: int = 20,
    offset: int = 0,
    law_type: str = '',
    from_date: str = '',
    to_date: str = '',
) -> tuple[int, list[dict[str, Any]]]:
    """複数フィールドによる構造化検索。
    fields = [{"q": "...", "op": "AND"}, ...] の形式。
    フィールド内スペース区切り＝AND、フィールド間は op で AND/OR を切り替える。
    戻り値: (total, items)
    """
    active = [f for f in fields if f.get("q", "").strip()]
    if not active:
        return 0, []

    all_keywords: list[str] = []
    for f in active:
        all_keywords.extend(normalize_text(f["q"]).lower().split())

    normalized_query = " ".join(all_keywords)
    cache_key_parts = (
        ["structured"]
        + [f"{f['op']}:{normalize_text(f['q']).lower()}" for f in active]
        + [source, str(limit), law_type, from_date, to_date]
    )

    with db_cursor() as (_, cur):
        generation = get_cache_generation(cur)
        cache_key = make_cache_key(cache_key_parts + [str(generation)])
        if offset == 0 and not law_type and not from_date and not to_date:
            cached = get_search_cache(cur, cache_key)
            if cached is not None:
                return len(cached), cached

        # フィールドごとに document_id 集合を求めて AND/OR で合成
        valid_ids: set[int] | None = None
        for f in active:
            ids = _doc_ids_for_field(f["q"], source, cur)
            if ids is None:
                continue
            if valid_ids is None:
                valid_ids = ids
            elif f.get("op", "AND") == "OR":
                valid_ids |= ids
            else:
                valid_ids &= ids

        # 法令種別・公布日でさらに絞り込む
        if valid_ids and (law_type or from_date or to_date):
            valid_ids = _filter_doc_ids_by_meta(valid_ids, cur, law_type, from_date, to_date)

        if not valid_ids:
            if offset == 0 and not law_type and not from_date and not to_date:
                with db_cursor(commit=True) as (_, cur2):
                    put_search_cache(cur2, cache_key, normalized_query, source, limit, generation, [])
            return 0, []

        # 有効 ID に絞って term_score でスコアリング
        all_terms = list(set(
            t
            for f in active
            for keyword in normalize_text(f["q"]).lower().split()
            for t in limited_weighted_terms((keyword, 10, True), max_terms=20).keys()
        ))
        if not all_terms:
            return []
        placeholders = ",".join(["%s"] * len(all_terms))
        id_placeholders = ",".join(["%s"] * len(valid_ids))
        params_a: list[Any] = all_terms + list(valid_ids)
        cur.execute(
            f"""
            SELECT st.document_id, st.article_id,
                   SUM(st.weight) AS term_score, COUNT(*) AS matched_terms
            FROM law_search_terms st
            WHERE st.target_type='article'
              AND st.term IN ({placeholders})
              AND st.document_id IN ({id_placeholders})
            GROUP BY st.document_id, st.article_id
            ORDER BY term_score DESC, matched_terms DESC
            LIMIT 240
            """,
            tuple(params_a),
        )
        article_candidates = cur.fetchall() or []

        params_d: list[Any] = all_terms + list(valid_ids)
        cur.execute(
            f"""
            SELECT st.document_id,
                   SUM(st.weight) AS term_score, COUNT(*) AS matched_terms
            FROM law_search_terms st
            WHERE st.target_type='document'
              AND st.term IN ({placeholders})
              AND st.document_id IN ({id_placeholders})
            GROUP BY st.document_id
            ORDER BY term_score DESC, matched_terms DESC
            LIMIT 120
            """,
            tuple(params_d),
        )
        document_candidates = cur.fetchall() or []

    # article / document 候補がなければ valid_ids の文書だけでスコア 0 で返す
    if not article_candidates and not document_candidates:
        # valid_ids はあるが term_score なし → タイトル一致等で最低限返す
        document_candidates = [{"document_id": doc_id, "term_score": 1, "matched_terms": 1} for doc_id in list(valid_ids)[:120]]

    keywords = expand_keywords_with_synonyms(all_keywords, max_keywords=20)
    total, results = fetch_search_detail_rows(article_candidates, document_candidates, keywords, normalized_query, limit, offset)

    if offset == 0:
        with db_cursor(commit=True) as (_, cur):
            put_search_cache(cur, cache_key, normalized_query, source, limit, generation, results)
    return total, results


def search_documents(query: str, source: str = 'all', limit: int = 20) -> tuple[int, list[dict[str, Any]]]:
    normalized_query = normalize_text(query).lower()
    if not normalized_query:
        return 0, []
    article_candidates: list[dict[str, Any]] = []
    document_candidates: list[dict[str, Any]] = []
    with db_cursor() as (_, cur):
        generation = get_cache_generation(cur)
        keywords = expand_keywords_with_synonyms(split_keywords(query), cur=cur, max_keywords=20)
        terms = query_terms(query, cur=cur)
        cache_key = make_cache_key(["search", normalized_query, source, str(limit), str(generation)])
        cached = get_search_cache(cur, cache_key)
        if cached is not None:
            return len(cached), cached
        if not terms:
            return 0, []
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
        results = search_documents_slow(query, source, limit)
        with db_cursor(commit=True) as (_, cur):
            put_search_cache(cur, cache_key, normalized_query, source, limit, generation, results)
        return len(results), results
    _total, results = fetch_search_detail_rows(article_candidates, document_candidates, keywords, normalized_query, limit)
    if not results:
        results = search_documents_slow(query, source, limit)
    with db_cursor(commit=True) as (_, cur):
        put_search_cache(cur, cache_key, normalized_query, source, limit, generation, results)
    return len(results), results


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
    launch_sync_in_background(scope)
    return jsonify({'ok': True, 'started': True, 'sourceScope': scope, 'summary': {}}), 202


@app.get('/api/search')
def api_search():
    source = (request.args.get('source') or 'all').strip()
    limit = max(1, min(100, int(request.args.get('limit') or '20')))
    offset = max(0, int(request.args.get('offset') or '0'))
    law_type = (request.args.get('lawType') or '').strip()
    from_date = (request.args.get('fromDate') or '').strip()
    to_date = (request.args.get('toDate') or '').strip()
    # 構造化検索: q1..q4 + op2..op4 が渡された場合
    fields: list[dict[str, str]] = []
    for i in range(1, 5):
        q = (request.args.get(f'q{i}') or '').strip()
        op = (request.args.get(f'op{i}') or 'AND').strip().upper()
        if op not in ('AND', 'OR'):
            op = 'AND'
        fields.append({'q': q, 'op': op})
    if any(f['q'] for f in fields):
        total, items = search_documents_structured(fields, source, limit, offset, law_type, from_date, to_date)
        return jsonify({'items': items, 'total': total})
    # 後方互換: q パラメータによる従来検索
    query = (request.args.get('q') or '').strip()
    if query:
        total, items = search_documents(query, source, limit)
    else:
        total, items = 0, []
    return jsonify({'items': items, 'total': total})


@app.get('/api/reference/search')
def api_reference_search():
    return api_search()


@app.get('/api/documents')
def api_document_list():
    source = (request.args.get('source') or 'all').strip()
    fmt = (request.args.get('format') or '').strip().lower()
    with db_cursor() as (_, cur):
        if source in ('mine-city', 'egov'):
            cur.execute(
                "SELECT id, source, title, law_type, law_number, category_path, promulgated_at"
                " FROM law_documents WHERE source=%s ORDER BY law_type, title",
                (source,),
            )
        else:
            cur.execute(
                "SELECT id, source, title, law_type, law_number, category_path, promulgated_at"
                " FROM law_documents ORDER BY source, law_type, title"
            )
        docs = cur.fetchall() or []
    if fmt == 'csv':
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(['ID', 'ソース', 'タイトル', '法令種別', '法令番号', '分類', '公布日'])
        for doc in docs:
            w.writerow([
                int(doc['id']), doc['source'], doc['title'],
                doc['law_type'] or '', doc['law_number'] or '',
                doc['category_path'] or '',
                str(doc['promulgated_at']) if doc['promulgated_at'] else '',
            ])
        return Response(
            '\ufeff' + buf.getvalue(),
            mimetype='text/csv; charset=utf-8-sig',
            headers={'Content-Disposition': 'attachment; filename=documents.csv'},
        )
    return jsonify({
        'items': [
            {
                'id': int(doc['id']),
                'source': doc['source'],
                'title': doc['title'],
                'lawType': doc['law_type'] or '',
                'lawNumber': doc['law_number'] or '',
                'categoryPath': doc['category_path'] or '',
                'promulgatedAt': str(doc['promulgated_at']) if doc['promulgated_at'] else None,
            }
            for doc in docs
        ]
    })


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


def enrich_candidates_with_text(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """候補リストに条文テキストを付加する。"""
    article_ids = [c["articleId"] for c in candidates if c.get("articleId")]
    doc_ids_no_article = [c["documentId"] for c in candidates if not c.get("articleId")]
    article_texts: dict[int, str] = {}
    doc_texts: dict[int, str] = {}
    with db_cursor() as (_, cur):
        if article_ids:
            ph = ",".join(["%s"] * len(article_ids))
            cur.execute(f"SELECT id, text FROM law_articles WHERE id IN ({ph})", article_ids)
            for row in cur.fetchall():
                article_texts[int(row["id"])] = row["text"] or ""
        if doc_ids_no_article:
            ph = ",".join(["%s"] * len(doc_ids_no_article))
            cur.execute(f"SELECT id, full_text FROM law_documents WHERE id IN ({ph})", doc_ids_no_article)
            for row in cur.fetchall():
                full = row["full_text"] or ""
                doc_texts[int(row["id"])] = full[:600]
    result = []
    for c in candidates:
        ec = dict(c)
        if c.get("articleId"):
            ec["articleText"] = article_texts.get(int(c["articleId"]), "")
        else:
            ec["articleText"] = doc_texts.get(int(c["documentId"]), "")
        result.append(ec)
    return result


def group_ask_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """候補を文書単位でグルーピングしてスコア降順で返す。"""
    groups: dict[int, dict[str, Any]] = {}
    for c in candidates:
        doc_id = int(c["documentId"])
        if doc_id not in groups:
            groups[doc_id] = {
                "documentId": doc_id,
                "source": c.get("source"),
                "title": c.get("title", ""),
                "lawType": c.get("lawType", ""),
                "lawNumber": c.get("lawNumber", ""),
                "sourceUrl": c.get("sourceUrl", ""),
                "categoryPath": c.get("categoryPath", ""),
                "topScore": int(c.get("score") or 0),
                "articles": [],
            }
        groups[doc_id]["articles"].append({
            "articleId": c.get("articleId"),
            "articleNumber": c.get("articleNumber"),
            "articleTitle": c.get("articleTitle"),
            "articleText": c.get("articleText", ""),
            "score": int(c.get("score") or 0),
        })
    return sorted(groups.values(), key=lambda g: -g["topScore"])


@app.post('/api/ask')
def api_ask():
    payload = request.get_json(silent=True) or {}
    query = (payload.get('query') or '').strip()
    if not query:
        raise ValueError('query が必要です。')
    normalized_query = normalize_text(query).lower()
    question_type = detect_question_type(query)
    with db_cursor(commit=True) as (_, cur):
        generation = get_cache_generation(cur)
        cache_key = make_cache_key(["ask2", normalized_query, str(generation)])
        cached = get_ask_cache(cur, cache_key)
        if cached is not None:
            return jsonify(cached)
        base_keywords = extract_question_keywords(query)
        keywords = expand_keywords_with_synonyms(base_keywords, cur=cur, max_keywords=20)
    _total, candidates = search_documents(query, 'all', 10)
    enriched = enrich_candidates_with_text(candidates)
    candidate_groups = group_ask_candidates(enriched)
    # answerLead をタイプに応じて生成
    type_label = QUESTION_TYPE_LABELS.get(question_type, "一般的な質問")
    if candidate_groups:
        top = candidate_groups[0]
        top_article = top["articles"][0] if top["articles"] else {}
        article_part = f"（{top_article['articleNumber']}）" if top_article.get("articleNumber") else ""
        lead = (
            f"{source_label(top['source'])}の「{top['title']}」{article_part}が最も関連すると推定されます。"
            f"以下の条文を確認のうえ、必ず原文で内容を確認してください。"
        )
    else:
        lead = "関連する条文が見つかりませんでした。キーワードを変えて再度お試しください。"
    response_payload = {
        'query': query,
        'normalizedQuery': normalize_text(query),
        'keywords': keywords,
        'questionType': question_type,
        'questionTypeLabel': type_label,
        'answerLead': lead,
        'candidateGroups': candidate_groups,
        'candidates': candidates,  # 後方互換
    }
    with db_cursor(commit=True) as (_, cur):
        put_ask_cache(cur, cache_key, normalized_query, generation, response_payload)
    return jsonify(response_payload)


@app.get('/api/reference/ask')
def api_reference_ask_get():
    query = (request.args.get('q') or '').strip()
    if not query:
        raise ValueError('q が必要です。')
    with app.test_request_context('/api/ask', method='POST', json={'query': query}):
        return api_ask()


@app.get('/api/documents/<int:document_id>/history')
def api_document_history(document_id: int):
    with db_cursor() as (_, cur):
        cur.execute("SELECT id FROM law_documents WHERE id=%s", (document_id,))
        if not cur.fetchone():
            raise ValueError('該当する例規が見つかりません。')
        cur.execute(
            "SELECT id, content_hash, title, law_number, promulgated_at, updated_at_source, changed_at"
            " FROM law_document_history WHERE document_id=%s ORDER BY changed_at DESC LIMIT 30",
            (document_id,),
        )
        rows = cur.fetchall() or []
    return jsonify({
        'items': [
            {
                'id': int(r['id']),
                'contentHash': r['content_hash'],
                'title': r['title'],
                'lawNumber': r['law_number'] or '',
                'promulgatedAt': str(r['promulgated_at']) if r['promulgated_at'] else None,
                'updatedAtSource': r['updated_at_source'] or '',
                'changedAt': str(r['changed_at']) if r['changed_at'] else None,
            }
            for r in rows
        ]
    })


@app.get('/api/documents/<int:document_id>/history/<int:history_id>')
def api_document_history_detail(document_id: int, history_id: int):
    with db_cursor() as (_, cur):
        cur.execute(
            "SELECT id, content_hash, title, law_number, promulgated_at, updated_at_source, full_text, changed_at"
            " FROM law_document_history WHERE id=%s AND document_id=%s",
            (history_id, document_id),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError('履歴が見つかりません。')
    return jsonify({
        'id': int(row['id']),
        'contentHash': row['content_hash'],
        'title': row['title'],
        'lawNumber': row['law_number'] or '',
        'promulgatedAt': str(row['promulgated_at']) if row['promulgated_at'] else None,
        'updatedAtSource': row['updated_at_source'] or '',
        'fullText': row['full_text'] or '',
        'changedAt': str(row['changed_at']) if row['changed_at'] else None,
    })


@app.get('/api/synonyms')
def api_synonyms_list():
    with db_cursor() as (_, cur):
        cur.execute(
            "SELECT id, canonical_term, synonym_term, priority, is_active"
            " FROM law_synonyms ORDER BY canonical_term, synonym_term LIMIT 500"
        )
        rows = cur.fetchall() or []
    return jsonify({
        'items': [
            {
                'id': int(r['id']),
                'canonicalTerm': r['canonical_term'],
                'synonymTerm': r['synonym_term'],
                'priority': int(r['priority']),
                'isActive': bool(r['is_active']),
            }
            for r in rows
        ]
    })


@app.post('/api/synonyms')
def api_synonyms_create():
    payload = request.get_json(silent=True) or {}
    canonical = normalize_text(payload.get('canonicalTerm') or '').lower()
    synonym = normalize_text(payload.get('synonymTerm') or '').lower()
    priority = max(1, min(20, int(payload.get('priority') or 10)))
    if not canonical or not synonym:
        raise ValueError('canonicalTerm と synonymTerm は必須です。')
    if canonical == synonym:
        raise ValueError('canonicalTerm と synonymTerm は異なる必要があります。')
    with db_cursor(commit=True) as (_, cur):
        cur.execute(
            "INSERT INTO law_synonyms (canonical_term, synonym_term, priority, is_active)"
            " VALUES (%s,%s,%s,1)"
            " ON DUPLICATE KEY UPDATE priority=%s, is_active=1, updated_at=CURRENT_TIMESTAMP",
            (canonical, synonym, priority, priority),
        )
        new_id = int(cur.lastrowid) if cur.lastrowid else 0
        bump_cache_generation(cur)
    global LOCAL_SYNONYM_CACHE
    LOCAL_SYNONYM_CACHE = None
    return jsonify({'id': new_id, 'canonicalTerm': canonical, 'synonymTerm': synonym, 'priority': priority, 'isActive': True})


@app.delete('/api/synonyms/<int:synonym_id>')
def api_synonyms_delete(synonym_id: int):
    with db_cursor(commit=True) as (_, cur):
        cur.execute("DELETE FROM law_synonyms WHERE id=%s", (synonym_id,))
        if cur.rowcount == 0:
            raise ValueError('同義語が見つかりません。')
        bump_cache_generation(cur)
    global LOCAL_SYNONYM_CACHE
    LOCAL_SYNONYM_CACHE = None
    return jsonify({'ok': True})


@app.get('/api/analytics')
def api_analytics():
    with db_cursor() as (_, cur):
        cur.execute("SELECT COALESCE(SUM(hit_count),0) AS total_hits, COUNT(*) AS entries FROM search_query_cache")
        sc = cur.fetchone() or {}
        cur.execute("SELECT COALESCE(SUM(hit_count),0) AS total_hits, COUNT(*) AS entries FROM ask_query_cache")
        ac = cur.fetchone() or {}
        cur.execute(
            "SELECT normalized_query, hit_count FROM search_query_cache"
            " ORDER BY hit_count DESC LIMIT 10"
        )
        top_search = cur.fetchall() or []
        cur.execute(
            "SELECT normalized_query, hit_count FROM ask_query_cache"
            " ORDER BY hit_count DESC LIMIT 10"
        )
        top_ask = cur.fetchall() or []
    return jsonify({
        'searchCacheHits': int(sc.get('total_hits') or 0),
        'searchCacheEntries': int(sc.get('entries') or 0),
        'askCacheHits': int(ac.get('total_hits') or 0),
        'askCacheEntries': int(ac.get('entries') or 0),
        'topSearchQueries': [{'query': r['normalized_query'], 'hits': int(r['hit_count'])} for r in top_search],
        'topAskQueries': [{'query': r['normalized_query'], 'hits': int(r['hit_count'])} for r in top_ask],
    })


@app.post('/api/cache/clear')
def api_cache_clear():
    import csv as csv_mod  # noqa: F401 – reuse import below
    payload = request.get_json(silent=True) or {}
    scope = payload.get('scope', 'all')
    if scope not in ('search', 'ask', 'all'):
        raise ValueError('scope は search / ask / all のいずれかです。')
    with db_cursor(commit=True) as (_, cur):
        if scope in ('search', 'all'):
            cur.execute("DELETE FROM search_query_cache")
        if scope in ('ask', 'all'):
            cur.execute("DELETE FROM ask_query_cache")
        bump_cache_generation(cur)
    LOCAL_SEARCH_CACHE.clear()
    LOCAL_ASK_CACHE.clear()
    return jsonify({'ok': True, 'scope': scope})


@app.get('/api/law-types')
def api_law_types():
    """DBに存在する law_type の一覧を返す。"""
    with db_cursor() as (_, cur):
        cur.execute("SELECT DISTINCT law_type FROM law_documents WHERE law_type != '' ORDER BY law_type")
        rows = cur.fetchall() or []
    return jsonify({'items': [r['law_type'] for r in rows]})


@app.get('/api/openapi')
def api_openapi():
    spec: dict[str, Any] = {
        'openapi': '3.0.3',
        'info': {
            'title': 'mine-city-reiki API',
            'version': APP_VERSION,
            'description': '美祢市例規・地方自治法データベース API',
        },
        'servers': [{'url': '/mine-city-reiki-api/api', 'description': 'Production'}],
        'paths': {
            '/health': {'get': {'summary': 'ヘルスチェック', 'responses': {'200': {'description': 'OK'}}}},
            '/search': {
                'get': {
                    'summary': '例規検索',
                    'parameters': [
                        {'name': 'q1', 'in': 'query', 'schema': {'type': 'string'}},
                        {'name': 'op2', 'in': 'query', 'schema': {'type': 'string', 'enum': ['AND', 'OR']}},
                        {'name': 'source', 'in': 'query', 'schema': {'type': 'string', 'enum': ['all', 'mine-city', 'egov']}},
                        {'name': 'limit', 'in': 'query', 'schema': {'type': 'integer', 'default': 20}},
                        {'name': 'offset', 'in': 'query', 'schema': {'type': 'integer', 'default': 0}},
                    ],
                    'responses': {'200': {'description': '検索結果リスト'}},
                }
            },
            '/documents': {'get': {'summary': '例規一覧', 'responses': {'200': {'description': '例規一覧'}}}},
            '/documents/{id}': {'get': {'summary': '例規詳細', 'parameters': [{'name': 'id', 'in': 'path', 'required': True, 'schema': {'type': 'integer'}}], 'responses': {'200': {'description': '例規詳細'}}}},
            '/documents/{id}/history': {'get': {'summary': '例規変更履歴', 'parameters': [{'name': 'id', 'in': 'path', 'required': True, 'schema': {'type': 'integer'}}], 'responses': {'200': {'description': '変更履歴'}}}},
            '/ask': {'post': {'summary': '条文照会', 'requestBody': {'content': {'application/json': {'schema': {'type': 'object', 'properties': {'query': {'type': 'string'}}}}}}, 'responses': {'200': {'description': '照会結果'}}}},
            '/synonyms': {
                'get': {'summary': '同義語一覧', 'responses': {'200': {'description': '同義語リスト'}}},
                'post': {'summary': '同義語追加', 'responses': {'200': {'description': '追加結果'}}},
            },
            '/analytics': {'get': {'summary': '利用統計', 'responses': {'200': {'description': '統計データ'}}}},
        },
    }
    return jsonify(spec)


ensure_schema()
maybe_backfill_search_terms()


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=CFG.api_port, debug=True)
