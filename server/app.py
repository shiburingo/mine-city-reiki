from __future__ import annotations

import csv
import base64
import html as html_lib
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
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import pymysql
from bs4 import BeautifulSoup
from flask import Flask, Response, g, jsonify, request
from pymysql.cursors import DictCursor
from werkzeug.exceptions import HTTPException

from meeting_minutes.crawler import crawl_minutes_pdfs
from meeting_minutes.pdf_extractor import download_pdf, extract_pdf_from_bytes
from meeting_minutes.speaker_tagger import ENGINE_VERSION as SPEAKER_ENGINE_VERSION
from meeting_minutes.speaker_tagger import TaggedUtterance, classify_speaker, reclassify_contextual_utterances, speech_type_from_role, tag_utterances
from meeting_minutes.table_formatter import ENGINE_VERSION as TABLE_ENGINE_VERSION
from meeting_minutes.table_formatter import PERSON_TABLE_ENGINE_VERSION, extract_coordinate_tables, refine_person_roster_tables
from dictionary_engine import (
    MINUTES_DICTIONARY_ENGINE_VERSION,
    build_hybrid_dictionary,
    build_internet_dictionary,
    build_minutes_pairs_from_rows,
    compile_synonym_dictionary,
    compiled_synonym_dictionary_status,
    compiled_dictionary_path,
    count_unprocessed_minutes_dictionary_rows,
    fetch_unprocessed_minutes_dictionary_rows,
    insert_pairs,
    load_compiled_synonym_dictionary,
    mark_minutes_dictionary_rows_processed,
)

try:
    from janome.tokenizer import Tokenizer as JanomeTokenizer
except Exception:
    JanomeTokenizer = None

APP_VERSION = "0.1.0"
APP_SLUG = "mine-city-reiki"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
MINE_CITY_INDEX_URL = "https://www2.city.mine.lg.jp/section/reiki/reiki_taikei/r_taikei_05.html"
TOKYO_OFFSET = "+09:00"
SOURCE_SCOPES = {"all", "mine-city", "egov", "local-public-service"}
EGOV_LAWS: list[dict[str, str]] = [
    {
        "source": "egov",
        "law_id": "322AC0000000067",
        "fallback_title": "地方自治法",
        "source_url": "https://laws.e-gov.go.jp/law/322AC0000000067",
    },
    {
        "source": "local-public-service",
        "law_id": "325AC0000000261",
        "fallback_title": "地方公務員法",
        "source_url": "https://laws.e-gov.go.jp/law/325AC0000000261",
    },
]


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
    meili_url: str
    meili_key: str
    meili_index: str
    meili_minutes_index: str
    meili_enabled: bool


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
        meili_url=_env("MEILI_URL", "http://127.0.0.1:7700").rstrip("/"),
        meili_key=_env("MEILI_MASTER_KEY", ""),
        meili_index=_env("MEILI_INDEX", "mine_city_reiki_articles"),
        meili_minutes_index=_env("MEILI_MINUTES_INDEX", "mine_city_meeting_minutes"),
        meili_enabled=_env_bool("MEILI_ENABLED", False),
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
LOCAL_CACHE_TTL_SECONDS = 600
LOCAL_SEARCH_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
LOCAL_ASK_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
LOCAL_MINUTES_SEARCH_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
LOCAL_SYNONYM_CACHE: tuple[float, dict[str, list[str]]] | None = None
LOCAL_SCORED_SYNONYM_CACHE: tuple[float, dict[str, list[tuple[str, int]]]] | None = None
LOCAL_COMPILED_SYNONYM_CACHE: tuple[float, float, dict[str, list[tuple[str, int]]]] | None = None
SYNC_THREAD_LOCK = threading.Lock()
MINUTES_CURSOR_PAGE_SIZE = 60
MINUTES_CURSOR_MAX_PAGE_SIZE = 200
MINUTES_SHORT_TERM_INDEX_VERSION = "short-term-v1"
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


def ensure_index(cur, table: str, index_name: str, definition: str) -> None:
    cur.execute(
        """
        SELECT COUNT(*) AS cnt
        FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND INDEX_NAME=%s
        """,
        (CFG.db_name, table, index_name),
    )
    exists = int((cur.fetchone() or {}).get("cnt") or 0) > 0
    if not exists:
        try:
            cur.execute(f"ALTER TABLE `{table}` ADD INDEX `{index_name}` {definition}")
        except pymysql.err.OperationalError as exc:
            if exc.args and exc.args[0] == 1061:
                return
            raise


def ensure_fulltext_index(cur, table: str, index_name: str, columns: str) -> None:
    cur.execute(
        """
        SELECT COUNT(*) AS cnt
        FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND INDEX_NAME=%s
        """,
        (CFG.db_name, table, index_name),
    )
    exists = int((cur.fetchone() or {}).get("cnt") or 0) > 0
    if not exists:
        try:
            cur.execute(f"ALTER TABLE `{table}` ADD FULLTEXT KEY `{index_name}` {columns}")
        except pymysql.err.OperationalError as exc:
            if exc.args and exc.args[0] == 1061:
                return
            raise


def ensure_enum_values(cur, table: str, column: str, values: list[str]) -> None:
    enum_values = ",".join([f"'{v}'" for v in values])
    cur.execute(f"ALTER TABLE `{table}` MODIFY COLUMN `{column}` ENUM({enum_values}) NOT NULL")


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
            ensure_column(cur, "law_documents", "browse_category_key", "browse_category_key VARCHAR(128) NOT NULL DEFAULT '' AFTER category_path")
            ensure_column(cur, "law_documents", "browse_document_order", "browse_document_order INT NOT NULL DEFAULT 0 AFTER browse_category_key")
            ensure_column(cur, "sync_settings", "cache_generation", "cache_generation BIGINT UNSIGNED NOT NULL DEFAULT 1 AFTER source_scope")
            ensure_enum_values(cur, "law_documents", "source", ["mine-city", "egov", "local-public-service"])
            ensure_enum_values(cur, "sync_settings", "source_scope", ["all", "mine-city", "egov", "local-public-service"])
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
            ensure_index(
                cur,
                "law_search_terms",
                "idx_law_search_terms_target_term_doc_article",
                "(target_type, term, document_id, article_id)",
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
            ensure_column(cur, "law_synonyms", "source_type", "source_type ENUM('builtin','manual','wordnet','domain','minutes-domain') NOT NULL DEFAULT 'manual' AFTER is_active")
            ensure_column(cur, "law_synonyms", "source_version", "source_version VARCHAR(64) NOT NULL DEFAULT '' AFTER source_type")
            ensure_enum_values(cur, "law_synonyms", "source_type", ["builtin", "manual", "wordnet", "domain", "minutes-domain", "curated", "wikidata", "internet"])
            ensure_index(cur, "law_synonyms", "idx_law_synonyms_source", "(source_type, is_active)")
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
                  source_scope ENUM('all','mine-city','egov','local-public-service') NOT NULL DEFAULT 'all',
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
            ensure_enum_values(cur, "search_query_cache", "source_scope", ["all", "mine-city", "egov", "local-public-service"])
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
            ensure_table(
                cur,
                "usage_events",
                """
                CREATE TABLE usage_events (
                  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                  event_type VARCHAR(32) NOT NULL,
                  normalized_query VARCHAR(255) NOT NULL DEFAULT '',
                  source_scope VARCHAR(64) NOT NULL DEFAULT '',
                  result_count INT UNSIGNED NOT NULL DEFAULT 0,
                  metadata_json LONGTEXT NULL,
                  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  KEY idx_usage_events_type_created (event_type, created_at),
                  KEY idx_usage_events_type_query (event_type, normalized_query),
                  KEY idx_usage_events_created (created_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """,
            )
            ensure_table(
                cur,
                "meeting_sessions",
                """
                CREATE TABLE meeting_sessions (
                  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                  source_url VARCHAR(512) NOT NULL,
                  section VARCHAR(64) NOT NULL,
                  meeting_name VARCHAR(255) NOT NULL,
                  title VARCHAR(255) NOT NULL,
                  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                  UNIQUE KEY uq_meeting_sessions_source (source_url),
                  KEY idx_meeting_sessions_section_name (section, meeting_name)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """,
            )
            ensure_table(
                cur,
                "meeting_days",
                """
                CREATE TABLE meeting_days (
                  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                  session_id BIGINT UNSIGNED NOT NULL,
                  source_url VARCHAR(512) NOT NULL,
                  page_url VARCHAR(512) NOT NULL,
                  pdf_url VARCHAR(512) NOT NULL,
                  pdf_hash CHAR(64) NOT NULL DEFAULT '',
                  meeting_date DATE NULL,
                  date_label VARCHAR(255) NOT NULL DEFAULT '',
                  title VARCHAR(255) NOT NULL DEFAULT '',
                  extraction_status VARCHAR(32) NOT NULL DEFAULT 'pending',
                  page_count INT UNSIGNED NOT NULL DEFAULT 0,
                  text_char_count INT UNSIGNED NOT NULL DEFAULT 0,
                  error_text TEXT NULL,
                  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                  UNIQUE KEY uq_meeting_days_pdf (pdf_url),
                  KEY idx_meeting_days_session (session_id),
                  KEY idx_meeting_days_date (meeting_date),
                  KEY idx_meeting_days_status (extraction_status),
                  CONSTRAINT fk_meeting_days_session FOREIGN KEY (session_id) REFERENCES meeting_sessions(id) ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """,
            )
            ensure_table(
                cur,
                "meeting_speakers",
                """
                CREATE TABLE meeting_speakers (
                  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                  normalized_name VARCHAR(191) NOT NULL,
                  display_name VARCHAR(191) NOT NULL,
                  title VARCHAR(191) NOT NULL DEFAULT '',
                  speaker_group VARCHAR(64) NOT NULL DEFAULT '',
                  role VARCHAR(32) NOT NULL DEFAULT 'unknown',
                  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                  UNIQUE KEY uq_meeting_speakers_identity (normalized_name, title, role),
                  KEY idx_meeting_speakers_role_name (role, display_name)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """,
            )
            ensure_table(
                cur,
                "meeting_speaker_dictionary",
                """
                CREATE TABLE meeting_speaker_dictionary (
                  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                  fiscal_year INT NOT NULL,
                  valid_from DATE NULL,
                  valid_to DATE NULL,
                  normalized_name VARCHAR(191) NOT NULL,
                  display_name VARCHAR(191) NOT NULL,
                  title VARCHAR(191) NOT NULL DEFAULT '',
                  speaker_group VARCHAR(64) NOT NULL DEFAULT '',
                  role VARCHAR(32) NOT NULL DEFAULT 'unknown',
                  source_type VARCHAR(32) NOT NULL DEFAULT 'utterance',
                  confidence DECIMAL(5,4) NOT NULL DEFAULT 0,
                  first_day_id BIGINT UNSIGNED NULL,
                  last_day_id BIGINT UNSIGNED NULL,
                  occurrences INT UNSIGNED NOT NULL DEFAULT 0,
                  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                  UNIQUE KEY uq_meeting_speaker_dict_identity (fiscal_year, normalized_name, title, role),
                  KEY idx_meeting_speaker_dict_year_role (fiscal_year, role, display_name),
                  KEY idx_meeting_speaker_dict_name (normalized_name),
                  CONSTRAINT fk_meeting_speaker_dict_first_day FOREIGN KEY (first_day_id) REFERENCES meeting_days(id) ON DELETE SET NULL,
                  CONSTRAINT fk_meeting_speaker_dict_last_day FOREIGN KEY (last_day_id) REFERENCES meeting_days(id) ON DELETE SET NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """,
            )
            ensure_table(
                cur,
                "meeting_day_speaker_roster",
                """
                CREATE TABLE meeting_day_speaker_roster (
                  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                  day_id BIGINT UNSIGNED NOT NULL,
                  normalized_name VARCHAR(191) NOT NULL,
                  display_name VARCHAR(191) NOT NULL,
                  title VARCHAR(191) NOT NULL DEFAULT '',
                  speaker_group VARCHAR(64) NOT NULL DEFAULT '',
                  role VARCHAR(32) NOT NULL DEFAULT 'unknown',
                  source_table_key VARCHAR(191) NOT NULL DEFAULT '',
                  confidence DECIMAL(5,4) NOT NULL DEFAULT 0,
                  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                  UNIQUE KEY uq_meeting_day_roster_identity (day_id, normalized_name, title, role),
                  KEY idx_meeting_day_roster_day_name (day_id, normalized_name),
                  KEY idx_meeting_day_roster_day_role (day_id, role),
                  CONSTRAINT fk_meeting_day_roster_day FOREIGN KEY (day_id) REFERENCES meeting_days(id) ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """,
            )
            ensure_table(
                cur,
                "meeting_utterances",
                """
                CREATE TABLE meeting_utterances (
                  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                  day_id BIGINT UNSIGNED NOT NULL,
                  speaker_id BIGINT UNSIGNED NULL,
                  speaker_name VARCHAR(191) NOT NULL DEFAULT '',
                  speaker_title VARCHAR(191) NOT NULL DEFAULT '',
                  speaker_role VARCHAR(32) NOT NULL DEFAULT 'unknown',
                  speaker_group VARCHAR(64) NOT NULL DEFAULT '',
                  speech_type VARCHAR(32) NOT NULL DEFAULT 'statement',
                  utterance_order INT UNSIGNED NOT NULL,
                  page_start INT UNSIGNED NOT NULL DEFAULT 0,
                  page_end INT UNSIGNED NOT NULL DEFAULT 0,
                  position_top_start FLOAT NOT NULL DEFAULT 0,
                  position_top_end FLOAT NOT NULL DEFAULT 0,
                  text LONGTEXT NOT NULL,
                  search_text LONGTEXT NOT NULL,
                  confidence DECIMAL(5,4) NOT NULL DEFAULT 0,
                  reason VARCHAR(255) NOT NULL DEFAULT '',
                  engine_version VARCHAR(64) NOT NULL DEFAULT '',
                  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  UNIQUE KEY uq_meeting_utterances_day_order (day_id, utterance_order),
                  KEY idx_meeting_utterances_day_role (day_id, speaker_role),
                  KEY idx_meeting_utterances_speaker (speaker_id),
                  KEY idx_meeting_utterances_role (speaker_role),
                  FULLTEXT KEY ft_meeting_utterances_search (search_text),
                  CONSTRAINT fk_meeting_utterances_day FOREIGN KEY (day_id) REFERENCES meeting_days(id) ON DELETE CASCADE,
                  CONSTRAINT fk_meeting_utterances_speaker FOREIGN KEY (speaker_id) REFERENCES meeting_speakers(id) ON DELETE SET NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """,
            )
            ensure_table(
                cur,
                "meeting_utterance_search_index",
                """
                CREATE TABLE meeting_utterance_search_index (
                  utterance_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
                  day_id BIGINT UNSIGNED NOT NULL,
                  session_id BIGINT UNSIGNED NOT NULL,
                  meeting_date DATE NULL,
                  section VARCHAR(64) NOT NULL DEFAULT '',
                  meeting_name VARCHAR(255) NOT NULL DEFAULT '',
                  day_title VARCHAR(255) NOT NULL DEFAULT '',
                  pdf_url VARCHAR(512) NOT NULL DEFAULT '',
                  page_url VARCHAR(512) NOT NULL DEFAULT '',
                  utterance_order INT UNSIGNED NOT NULL,
                  speaker_name VARCHAR(191) NOT NULL DEFAULT '',
                  speaker_title VARCHAR(191) NOT NULL DEFAULT '',
                  speaker_role VARCHAR(32) NOT NULL DEFAULT 'unknown',
                  speaker_group VARCHAR(64) NOT NULL DEFAULT '',
                  speech_type VARCHAR(32) NOT NULL DEFAULT 'statement',
                  page_start INT UNSIGNED NOT NULL DEFAULT 0,
                  page_end INT UNSIGNED NOT NULL DEFAULT 0,
                  position_top_start FLOAT NOT NULL DEFAULT 0,
                  position_top_end FLOAT NOT NULL DEFAULT 0,
                  text_preview TEXT NOT NULL,
                  body_search_text LONGTEXT NULL,
                  search_text LONGTEXT NOT NULL,
                  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                  KEY idx_minutes_search_day_order (day_id, utterance_order),
                  KEY idx_minutes_search_date_section_order (meeting_date, section, utterance_order),
                  KEY idx_minutes_search_speaker (speaker_name, day_id, utterance_order),
                  KEY idx_minutes_search_role_title (speaker_role, speaker_title, day_id),
                  FULLTEXT KEY ft_minutes_search_body_text (body_search_text),
                  FULLTEXT KEY ft_minutes_search_index_text (search_text),
                  CONSTRAINT fk_minutes_search_index_utterance FOREIGN KEY (utterance_id) REFERENCES meeting_utterances(id) ON DELETE CASCADE,
                  CONSTRAINT fk_minutes_search_index_day FOREIGN KEY (day_id) REFERENCES meeting_days(id) ON DELETE CASCADE,
                  CONSTRAINT fk_minutes_search_index_session FOREIGN KEY (session_id) REFERENCES meeting_sessions(id) ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """,
            )
            ensure_table(
                cur,
                "meeting_utterance_short_terms",
                """
                CREATE TABLE meeting_utterance_short_terms (
                  term VARCHAR(16) NOT NULL,
                  utterance_id BIGINT UNSIGNED NOT NULL,
                  day_id BIGINT UNSIGNED NOT NULL,
                  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  PRIMARY KEY (term, utterance_id),
                  KEY idx_meeting_utterance_short_terms_day_term (day_id, term),
                  KEY idx_meeting_utterance_short_terms_utterance (utterance_id),
                  CONSTRAINT fk_meeting_utterance_short_terms_utterance FOREIGN KEY (utterance_id) REFERENCES meeting_utterances(id) ON DELETE CASCADE,
                  CONSTRAINT fk_meeting_utterance_short_terms_day FOREIGN KEY (day_id) REFERENCES meeting_days(id) ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """,
            )
            ensure_table(
                cur,
                "meeting_short_term_index_status",
                """
                CREATE TABLE meeting_short_term_index_status (
                  term VARCHAR(16) NOT NULL PRIMARY KEY,
                  index_version VARCHAR(64) NOT NULL DEFAULT '',
                  utterance_count INT UNSIGNED NOT NULL DEFAULT 0,
                  rebuilt_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """,
            )
            ensure_table(
                cur,
                "meeting_dictionary_sources",
                """
                CREATE TABLE meeting_dictionary_sources (
                  utterance_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
                  engine_version VARCHAR(64) NOT NULL DEFAULT '',
                  term_count INT UNSIGNED NOT NULL DEFAULT 0,
                  processed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  KEY idx_meeting_dictionary_sources_version (engine_version, processed_at),
                  CONSTRAINT fk_meeting_dictionary_sources_utterance FOREIGN KEY (utterance_id) REFERENCES meeting_utterances(id) ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """,
            )
            ensure_table(
                cur,
                "meeting_tables",
                """
                CREATE TABLE meeting_tables (
                  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                  day_id BIGINT UNSIGNED NOT NULL,
                  table_key VARCHAR(191) NOT NULL,
                  page INT UNSIGNED NOT NULL DEFAULT 0,
                  position_top FLOAT NOT NULL DEFAULT 0,
                  position_bottom FLOAT NOT NULL DEFAULT 0,
                  caption VARCHAR(255) NOT NULL DEFAULT '',
                  rows_json LONGTEXT NOT NULL,
                  html LONGTEXT NOT NULL,
                  search_text LONGTEXT NOT NULL,
                  confidence DECIMAL(5,4) NOT NULL DEFAULT 0,
                  engine_version VARCHAR(64) NOT NULL DEFAULT '',
                  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  UNIQUE KEY uq_meeting_tables_day_key (day_id, table_key),
                  KEY idx_meeting_tables_day (day_id),
                  FULLTEXT KEY ft_meeting_tables_search (search_text),
                  CONSTRAINT fk_meeting_tables_day FOREIGN KEY (day_id) REFERENCES meeting_days(id) ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """,
            )
            ensure_column(cur, "meeting_utterances", "position_top_start", "position_top_start FLOAT NOT NULL DEFAULT 0 AFTER page_end")
            ensure_column(cur, "meeting_utterances", "position_top_end", "position_top_end FLOAT NOT NULL DEFAULT 0 AFTER position_top_start")
            ensure_column(cur, "meeting_tables", "position_top", "position_top FLOAT NOT NULL DEFAULT 0 AFTER page")
            ensure_column(cur, "meeting_tables", "position_bottom", "position_bottom FLOAT NOT NULL DEFAULT 0 AFTER position_top")
            ensure_index(cur, "meeting_days", "idx_meeting_days_session_date", "(session_id, meeting_date)")
            ensure_index(cur, "meeting_days", "idx_meeting_days_date_session", "(meeting_date, session_id, id)")
            ensure_index(cur, "meeting_sessions", "idx_meeting_sessions_section_id", "(section, id)")
            ensure_index(cur, "meeting_utterances", "idx_meeting_utterances_speaker_name_title", "(speaker_name, speaker_title, day_id)")
            ensure_index(cur, "meeting_utterances", "idx_meeting_utterances_speaker_name_day_order", "(speaker_name, day_id, utterance_order)")
            ensure_index(cur, "meeting_utterances", "idx_meeting_utterances_speaker_title_day_order", "(speaker_title, day_id, utterance_order)")
            ensure_index(cur, "meeting_utterances", "idx_meeting_utterances_role_title", "(speaker_role, speaker_title, day_id)")
            ensure_index(cur, "meeting_utterances", "idx_meeting_utterances_day_page_order", "(day_id, page_start, utterance_order)")
            ensure_index(cur, "meeting_tables", "idx_meeting_tables_day_page", "(day_id, page, position_top, id)")
            ensure_table(
                cur,
                "meeting_extract_runs",
                """
                CREATE TABLE meeting_extract_runs (
                  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                  started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  finished_at TIMESTAMP NULL DEFAULT NULL,
                  status VARCHAR(32) NOT NULL DEFAULT 'running',
                  recent_days INT UNSIGNED NOT NULL DEFAULT 365,
                  summary_json LONGTEXT NOT NULL,
                  error_text TEXT NULL,
                  engine_versions VARCHAR(255) NOT NULL DEFAULT ''
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """,
            )
            ensure_table(
                cur,
                "meeting_compile_versions",
                """
                CREATE TABLE meeting_compile_versions (
                  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                  version_key VARCHAR(64) NOT NULL,
                  status VARCHAR(32) NOT NULL DEFAULT 'running',
                  is_active TINYINT(1) NOT NULL DEFAULT 0,
                  started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  finished_at TIMESTAMP NULL DEFAULT NULL,
                  activated_at TIMESTAMP NULL DEFAULT NULL,
                  summary_json LONGTEXT NOT NULL,
                  error_text TEXT NULL,
                  engine_versions VARCHAR(255) NOT NULL DEFAULT '',
                  UNIQUE KEY uq_meeting_compile_versions_key (version_key),
                  KEY idx_meeting_compile_versions_active (is_active, status, id),
                  KEY idx_meeting_compile_versions_status (status, id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """,
            )
            ensure_table(
                cur,
                "meeting_compiled_days",
                """
                CREATE TABLE meeting_compiled_days (
                  version_id BIGINT UNSIGNED NOT NULL,
                  day_id BIGINT UNSIGNED NOT NULL,
                  session_id BIGINT UNSIGNED NOT NULL,
                  meeting_date DATE NULL,
                  section VARCHAR(64) NOT NULL DEFAULT '',
                  meeting_name VARCHAR(255) NOT NULL DEFAULT '',
                  title VARCHAR(255) NOT NULL DEFAULT '',
                  utterance_count INT UNSIGNED NOT NULL DEFAULT 0,
                  table_count INT UNSIGNED NOT NULL DEFAULT 0,
                  detail_json LONGTEXT NOT NULL,
                  content_hash CHAR(64) NOT NULL DEFAULT '',
                  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  PRIMARY KEY (version_id, day_id),
                  KEY idx_meeting_compiled_days_day (day_id, version_id),
                  KEY idx_meeting_compiled_days_session (version_id, session_id, meeting_date),
                  CONSTRAINT fk_meeting_compiled_days_version FOREIGN KEY (version_id) REFERENCES meeting_compile_versions(id) ON DELETE CASCADE,
                  CONSTRAINT fk_meeting_compiled_days_day FOREIGN KEY (day_id) REFERENCES meeting_days(id) ON DELETE CASCADE,
                  CONSTRAINT fk_meeting_compiled_days_session FOREIGN KEY (session_id) REFERENCES meeting_sessions(id) ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """,
            )
            ensure_table(
                cur,
                "meeting_compiled_utterances",
                """
                CREATE TABLE meeting_compiled_utterances (
                  version_id BIGINT UNSIGNED NOT NULL,
                  utterance_id BIGINT UNSIGNED NOT NULL,
                  day_id BIGINT UNSIGNED NOT NULL,
                  session_id BIGINT UNSIGNED NOT NULL,
                  meeting_date DATE NULL,
                  section VARCHAR(64) NOT NULL DEFAULT '',
                  meeting_name VARCHAR(255) NOT NULL DEFAULT '',
                  day_title VARCHAR(255) NOT NULL DEFAULT '',
                  pdf_url VARCHAR(512) NOT NULL DEFAULT '',
                  page_url VARCHAR(512) NOT NULL DEFAULT '',
                  utterance_order INT UNSIGNED NOT NULL,
                  speaker_name VARCHAR(191) NOT NULL DEFAULT '',
                  speaker_title VARCHAR(191) NOT NULL DEFAULT '',
                  speaker_role VARCHAR(32) NOT NULL DEFAULT 'unknown',
                  speaker_group VARCHAR(64) NOT NULL DEFAULT '',
                  speech_type VARCHAR(32) NOT NULL DEFAULT 'statement',
                  page_start INT UNSIGNED NOT NULL DEFAULT 0,
                  page_end INT UNSIGNED NOT NULL DEFAULT 0,
                  position_top_start FLOAT NOT NULL DEFAULT 0,
                  position_top_end FLOAT NOT NULL DEFAULT 0,
                  text_preview TEXT NOT NULL,
                  display_text LONGTEXT NULL,
                  body_search_text LONGTEXT NULL,
                  search_text LONGTEXT NOT NULL,
                  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  PRIMARY KEY (version_id, utterance_id),
                  KEY idx_minutes_compiled_day_order (version_id, day_id, utterance_order),
                  KEY idx_minutes_compiled_date_section_order (version_id, meeting_date, section, utterance_order),
                  KEY idx_minutes_compiled_speaker (version_id, speaker_name, day_id, utterance_order),
                  KEY idx_minutes_compiled_role_title (version_id, speaker_role, speaker_title, day_id),
                  FULLTEXT KEY ft_minutes_compiled_body_text (body_search_text),
                  FULLTEXT KEY ft_minutes_compiled_search_text (search_text),
                  CONSTRAINT fk_minutes_compiled_version FOREIGN KEY (version_id) REFERENCES meeting_compile_versions(id) ON DELETE CASCADE,
                  CONSTRAINT fk_minutes_compiled_utterance FOREIGN KEY (utterance_id) REFERENCES meeting_utterances(id) ON DELETE CASCADE,
                  CONSTRAINT fk_minutes_compiled_day FOREIGN KEY (day_id) REFERENCES meeting_days(id) ON DELETE CASCADE,
                  CONSTRAINT fk_minutes_compiled_session FOREIGN KEY (session_id) REFERENCES meeting_sessions(id) ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """,
            )
            ensure_column(cur, "meeting_utterance_search_index", "body_search_text", "body_search_text LONGTEXT NULL AFTER text_preview")
            ensure_fulltext_index(cur, "meeting_utterance_search_index", "ft_minutes_search_body_text", "(body_search_text)")
            ensure_column(cur, "meeting_compiled_utterances", "display_text", "display_text LONGTEXT NULL AFTER text_preview")
            ensure_column(cur, "meeting_compiled_utterances", "body_search_text", "body_search_text LONGTEXT NULL AFTER display_text")
            ensure_fulltext_index(cur, "meeting_compiled_utterances", "ft_minutes_compiled_body_text", "(body_search_text)")
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


def compact_usage_query(value: str, *, fallback: str = "") -> str:
    query = normalize_text(value)
    if not query:
        query = fallback
    return query[:255]


def record_usage_event_cur(
    cur,
    event_type: str,
    normalized_query: str = "",
    source_scope: str = "",
    result_count: int = 0,
    metadata: dict[str, Any] | None = None,
) -> None:
    cur.execute(
        """
        INSERT INTO usage_events
          (event_type, normalized_query, source_scope, result_count, metadata_json)
        VALUES (%s,%s,%s,%s,%s)
        """,
        (
            event_type[:32],
            compact_usage_query(normalized_query),
            (source_scope or "")[:64],
            max(0, int(result_count or 0)),
            json.dumps(metadata or {}, ensure_ascii=False) if metadata else None,
        ),
    )


def record_usage_event(
    event_type: str,
    normalized_query: str = "",
    source_scope: str = "",
    result_count: int = 0,
    metadata: dict[str, Any] | None = None,
) -> None:
    with db_cursor(commit=True) as (_, cur):
        record_usage_event_cur(cur, event_type, normalized_query, source_scope, result_count, metadata)


def contains_japanese(text: str) -> bool:
    return any(
        ("\u3040" <= ch <= "\u30ff")
        or ("\u3400" <= ch <= "\u9fff")
        or ch == "々"
        for ch in text
    )


def is_japanese_single_search_char(ch: str) -> bool:
    return (
        len(ch) == 1
        and (
            ("\u3040" <= ch <= "\u30ff")
            or ("\u3400" <= ch <= "\u9fff")
            or ch in {"々", "〆", "〇"}
        )
    )


def normalize_minutes_short_term(term: str) -> str:
    value = normalize_text(term)
    return value if is_japanese_single_search_char(value) else ""


def extract_minutes_short_terms(text: str) -> list[str]:
    normalized = normalize_text(text)
    return sorted({ch for ch in normalized if is_japanese_single_search_char(ch)})


def insert_minutes_short_terms_for_utterance(cur, utterance_id: int, day_id: int, search_text: str) -> None:
    terms = extract_minutes_short_terms(search_text)
    if not terms:
        return
    cur.executemany(
        """
        INSERT IGNORE INTO meeting_utterance_short_terms (term, utterance_id, day_id)
        VALUES (%s,%s,%s)
        """,
        [(term, utterance_id, day_id) for term in terms],
    )


def is_minutes_short_term_index_ready(cur, term: str) -> bool:
    short_term = normalize_minutes_short_term(term)
    if not short_term:
        return False
    cur.execute(
        """
        SELECT 1
        FROM meeting_short_term_index_status
        WHERE term=%s AND index_version=%s
        LIMIT 1
        """,
        (short_term, MINUTES_SHORT_TERM_INDEX_VERSION),
    )
    return cur.fetchone() is not None


def rebuild_minutes_short_term_index_for_term(cur, term: str) -> int:
    short_term = normalize_minutes_short_term(term)
    if not short_term:
        return 0
    cur.execute("DELETE FROM meeting_utterance_short_terms WHERE term=%s", (short_term,))
    cur.execute(
        """
        INSERT IGNORE INTO meeting_utterance_short_terms (term, utterance_id, day_id)
        SELECT %s, id, day_id
        FROM meeting_utterances
        WHERE search_text LIKE %s
        """,
        (short_term, f"%{short_term}%"),
    )
    utterance_count = int(cur.rowcount or 0)
    cur.execute(
        """
        INSERT INTO meeting_short_term_index_status (term, index_version, utterance_count, rebuilt_at)
        VALUES (%s,%s,%s,CURRENT_TIMESTAMP)
        ON DUPLICATE KEY UPDATE
          index_version=VALUES(index_version),
          utterance_count=VALUES(utterance_count),
          rebuilt_at=VALUES(rebuilt_at),
          updated_at=CURRENT_TIMESTAMP
        """,
        (short_term, MINUTES_SHORT_TERM_INDEX_VERSION, utterance_count),
    )
    return utterance_count


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


def is_hiragana_only(text: str) -> bool:
    return bool(text) and all("\u3040" <= ch <= "\u309f" for ch in text)


def chunk_terms(
    text: str,
    max_compact_len: int = 20,
    ngram_sizes: tuple[int, ...] = (2, 3),
    prefix_lengths: tuple[int, ...] = (),
) -> list[str]:
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
            for prefix_len in prefix_lengths:
                if prefix_len <= len(compact):
                    terms.append(compact[:prefix_len])
            if 2 <= len(compact) <= max_compact_len:
                for size in ngram_sizes:
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


def title_weighted_terms(text: str, weight: int, include_phrase: bool = True) -> dict[str, int]:
    normalized = normalize_text(text).lower()
    if not normalized:
        return {}
    weights: dict[str, int] = {}
    if include_phrase and 1 < len(normalized) <= 191:
        weights[normalized] = weight
    for term in janome_terms(normalized):
        weights[term] = max(weights.get(term, 0), weight)
    for term in chunk_terms(
        normalized,
        max_compact_len=80,
        ngram_sizes=(2, 3),
        prefix_lengths=(4, 5, 6, 7, 8, 9, 10, 11, 12),
    ):
        weights[term] = max(weights.get(term, 0), weight)
    reading_weight = max(1, int(weight * 0.6))
    for term in janome_reading_terms(normalized):
        weights[term] = max(weights.get(term, 0), reading_weight)
    ranked = sorted(weights.items(), key=lambda item: (-item[1], len(item[0]), item[0]))
    return dict(ranked[:96])


def exact_query_terms(query: str) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    normalized_query = normalize_text(query).lower()
    for candidate in [normalized_query, *[normalize_text(k).lower() for k in split_keywords(query)]]:
        if candidate and candidate not in seen:
            seen.add(candidate)
            terms.append(candidate)
    return terms[:12]


def query_terms(query: str, cur=None, fuzzy: bool = False) -> list[str]:
    if not fuzzy:
        return exact_query_terms(query)
    base_keywords = split_keywords(query)
    expanded_keywords = expand_keywords_with_synonyms(base_keywords, cur=cur, max_keywords=20)
    weighted_groups: list[tuple[str, int, bool]] = [(query, 12, True)]
    for keyword in expanded_keywords:
        weighted_groups.append((keyword, 10 if keyword in {normalize_text(query).lower()} else 7, True))
    terms = list(limited_weighted_terms(*weighted_groups, max_terms=40).keys())
    normalized_query = normalize_text(query).lower()
    if contains_japanese(normalized_query) and re.search(r"[一-龯]", normalized_query):
        terms = [term for term in terms if not is_hiragana_only(term)]
    strong_terms = [term for term in terms if contains_japanese(term) and len(term) >= 3]
    if strong_terms:
        strong_set = set(strong_terms)
        terms = strong_terms + [term for term in terms if term not in strong_set and (term.isdigit() or len(term) >= 4)]
    deduped: list[str] = []
    seen: set[str] = set()
    for term in terms:
        if term and term not in seen:
            seen.add(term)
            deduped.append(term)
    return deduped[:24]


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
                INSERT IGNORE INTO law_synonyms (canonical_term, synonym_term, priority, is_active, source_type, source_version)
                VALUES (%s,%s,%s,1,'builtin',%s)
                """,
                (canonical_term, synonym_term, 10, APP_VERSION),
            )


def prune_expired_caches(cur) -> None:
    cur.execute("DELETE FROM search_query_cache WHERE expires_at IS NOT NULL AND expires_at < CURRENT_TIMESTAMP")
    cur.execute("DELETE FROM ask_query_cache WHERE expires_at IS NOT NULL AND expires_at < CURRENT_TIMESTAMP")


def clear_local_caches() -> None:
    LOCAL_SEARCH_CACHE.clear()
    LOCAL_ASK_CACHE.clear()
    LOCAL_MINUTES_SEARCH_CACHE.clear()
    global LOCAL_SYNONYM_CACHE
    global LOCAL_SCORED_SYNONYM_CACHE
    global LOCAL_COMPILED_SYNONYM_CACHE
    LOCAL_SYNONYM_CACHE = None
    LOCAL_SCORED_SYNONYM_CACHE = None
    LOCAL_COMPILED_SYNONYM_CACHE = None


def get_compiled_dictionary_path() -> Path:
    return compiled_dictionary_path(os.getenv("REIKI_SYNONYM_COMPILED_PATH") or None)


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


def scored_synonyms_map(cur=None) -> dict[str, list[tuple[str, int]]]:
    global LOCAL_SCORED_SYNONYM_CACHE
    global LOCAL_COMPILED_SYNONYM_CACHE
    now = time.time()
    if LOCAL_SCORED_SYNONYM_CACHE and now - LOCAL_SCORED_SYNONYM_CACHE[0] < LOCAL_CACHE_TTL_SECONDS:
        return LOCAL_SCORED_SYNONYM_CACHE[1]
    compiled_path = get_compiled_dictionary_path()
    if compiled_path.exists():
        try:
            compiled_mtime = compiled_path.stat().st_mtime
            if (
                LOCAL_COMPILED_SYNONYM_CACHE
                and LOCAL_COMPILED_SYNONYM_CACHE[1] == compiled_mtime
                and now - LOCAL_COMPILED_SYNONYM_CACHE[0] < LOCAL_CACHE_TTL_SECONDS
            ):
                result = LOCAL_COMPILED_SYNONYM_CACHE[2]
            else:
                result = load_compiled_synonym_dictionary(compiled_path)
                LOCAL_COMPILED_SYNONYM_CACHE = (now, compiled_mtime, result)
            LOCAL_SCORED_SYNONYM_CACHE = (now, result)
            return result
        except Exception:
            app.logger.exception("Compiled synonym dictionary load failed; falling back to database")
    if cur is None:
        with db_cursor() as (_, inner_cur):
            return scored_synonyms_map(inner_cur)
    cur.execute(
        """
        SELECT canonical_term, synonym_term, priority
        FROM law_synonyms
        WHERE is_active=1
        ORDER BY priority DESC, id ASC
        """
    )
    graph: dict[str, dict[str, int]] = {}
    for row in cur.fetchall() or []:
        canonical = normalize_text(row.get("canonical_term") or "").lower()
        synonym = normalize_text(row.get("synonym_term") or "").lower()
        priority = int(row.get("priority") or 0)
        if not canonical or not synonym or canonical == synonym:
            continue
        graph.setdefault(canonical, {})[synonym] = max(priority, graph.setdefault(canonical, {}).get(synonym, 0))
        graph.setdefault(synonym, {})[canonical] = max(priority, graph.setdefault(synonym, {}).get(canonical, 0))
    result = {
        term: sorted(items.items(), key=lambda item: (-item[1], len(item[0]), item[0]))
        for term, items in graph.items()
    }
    LOCAL_SCORED_SYNONYM_CACHE = (now, result)
    return result


def make_content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def make_document_content_hash(full_text: str, articles: list[dict[str, Any]] | None = None) -> str:
    payload = {
        "full_text": full_text or "",
        "articles": [
            {
                "article_key": article.get("article_key", ""),
                "article_number": article.get("article_number", ""),
                "article_title": article.get("article_title", ""),
                "parent_path": article.get("parent_path", ""),
                "text": article.get("text", ""),
            }
            for article in (articles or [])
        ],
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


QUESTION_TYPE_PATTERNS: list[tuple[str, list[str]]] = [
    ("eligibility", ["できますか", "できるか", "できるでしょうか", "可能ですか", "権利があります", "資格があります", "受けられます", "対象になります", "要件", "条件", "対象"]),
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

QUESTION_INTENT_TERMS = {
    "できる",
    "できます",
    "可能",
    "可否",
    "対象",
    "資格",
    "要件",
    "条件",
    "手続",
    "手続き",
    "申請",
    "取得",
    "方法",
    "内容",
    "必要な",
    "教える",
    "教えて",
    "必要",
    "書類",
    "いつ",
    "期限",
    "期間",
    "いくら",
    "金額",
    "額",
    "どこ",
    "開催",
    "窓口",
    "場所",
    "部署",
    "何",
    "いつ開催され",
    "場合",
    "該当",
    "関係",
    "規定",
    "定める",
}

QUESTION_TYPE_BOOST_TERMS: dict[str, tuple[str, ...]] = {
    "eligibility": ("対象", "資格", "要件", "条件", "できる", "することができる", "認める", "承認"),
    "procedure": ("手続", "申請", "届出", "請求", "提出", "様式", "許可", "承認"),
    "definition": ("定義", "意義", "趣旨", "目的", "いう", "範囲"),
    "period": ("期間", "期限", "日", "月", "年", "まで", "から", "以内"),
    "amount": ("額", "金額", "円", "費用", "料金", "報酬", "給与", "手当", "割合"),
    "location": ("窓口", "場所", "課", "部署", "所管", "提出先"),
    "general": (),
}


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


def clean_question_text(query: str) -> str:
    cleaned = normalize_text(query)
    cleaned = re.sub(r"[？?。]+$", "", cleaned).strip()
    for suffix in QUESTION_SUFFIXES:
        cleaned = re.sub(re.escape(suffix) + r"$", "", cleaned).strip()
    cleaned = re.sub(r"(について|に関して|に関する|を|は)$", "", cleaned).strip()
    return cleaned or normalize_text(query)


def _dedupe_terms(terms: Iterable[str], limit: int = 20) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for term in terms:
        normalized = normalize_text(str(term)).lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
        if len(result) >= limit:
            break
    return result


def _known_question_terms(normalized_query: str, cur=None) -> list[str]:
    known: list[str] = []
    lookup = synonyms_map(cur)
    for canonical, synonyms in lookup.items():
        for term in [canonical, *synonyms]:
            candidate = normalize_text(term).lower()
            if len(candidate) >= 3 and contains_japanese(candidate) and candidate in normalized_query:
                known.append(candidate)
        canonical_norm = normalize_text(canonical).lower()
        if len(canonical_norm) >= 3 and contains_japanese(canonical_norm) and canonical_norm in normalized_query:
            known.append(canonical_norm)
    return sorted(
        _dedupe_terms(known, limit=24),
        key=lambda value: (normalized_query.find(value) if value in normalized_query else 10**6, -len(value), value),
    )


def _strip_question_intent_suffix(term: str) -> str:
    cleaned = normalize_text(term).lower()
    cleaned = re.sub(r"(ください|です|ます)$", "", cleaned)
    cleaned = re.sub(r"(されてい|される|され|したい|した|な)$", "", cleaned)
    changed = True
    while changed and cleaned:
        changed = False
        for suffix in sorted(QUESTION_INTENT_TERMS, key=len, reverse=True):
            if suffix and cleaned.endswith(suffix) and len(cleaned) - len(suffix) >= 2:
                cleaned = cleaned[: -len(suffix)]
                changed = True
                break
    cleaned = re.sub(r"(する|したい|される|なる|について)$", "", cleaned)
    return cleaned.strip()


def _question_particle_phrases(cleaned: str) -> list[str]:
    normalized = normalize_text(cleaned).lower()
    phrases: list[str] = []
    for chunk in re.split(r"(?:について|に関して|に関する|の|には|では|には|に|を|は|が|で|と|、|,|，|。)", normalized):
        chunk = re.sub(r"[^0-9a-z一-龯ぁ-んァ-ヶー々]+", "", chunk)
        chunk = _strip_question_intent_suffix(chunk)
        if chunk in QUESTION_INTENT_TERMS:
            continue
        if len(chunk) >= 2 and contains_japanese(chunk) and not is_hiragana_only(chunk):
            phrases.append(chunk)
    return _dedupe_terms(phrases, limit=12)


def _remove_subsumed_terms(terms: list[str], normalized_query: str, limit: int = 16) -> list[str]:
    sorted_terms = sorted(
        _dedupe_terms(terms, limit=64),
        key=lambda value: (normalized_query.find(value) if value in normalized_query else 10**6, -len(value), value),
    )
    result: list[str] = []
    for term in sorted_terms:
        if any(term != selected and term in selected for selected in result):
            continue
        result.append(term)
        if len(result) >= limit:
            break
    return result


def question_search_profile(query: str, cur=None) -> dict[str, list[str]]:
    """質問文から、検索に使う内容語と表示用キーワードを作る。"""
    cleaned = clean_question_text(query)
    normalized = normalize_text(cleaned).lower()
    known_terms = _known_question_terms(normalized, cur=cur)
    particle_phrases = _question_particle_phrases(cleaned)
    token_terms = [
        term
        for term in janome_terms(cleaned)
        if len(term) >= 2
        and term not in STOP_TERMS
        and term not in QUESTION_INTENT_TERMS
        and not is_hiragana_only(term)
    ]
    compounds: list[str] = []
    for start in range(len(token_terms)):
        current = ""
        for end in range(start, min(len(token_terms), start + 4)):
            current += token_terms[end]
            if 4 <= len(current) <= 20 and current in normalized:
                compounds.append(current)
    phrase_terms = _remove_subsumed_terms([*known_terms, *particle_phrases, *compounds], normalized, limit=16)
    core_terms = _dedupe_terms(
        [
            *phrase_terms,
            *[
                term
                for term in token_terms
                if not any(term in phrase for phrase in phrase_terms)
                and (len(term) >= 3 or re.search(r"[一-龯]", term))
            ],
        ],
        limit=12,
    )
    fallback_terms = _dedupe_terms(
        [
            term
            for term in [*compounds, *token_terms]
            if term not in core_terms
            and term not in QUESTION_INTENT_TERMS
            and not is_hiragana_only(term)
            and len(term) >= 2
        ],
        limit=12,
    )
    if not core_terms:
        core_terms = _dedupe_terms(split_keywords(cleaned), limit=8)
    intent_terms = _dedupe_terms(
        [
            term
            for term in janome_terms(cleaned)
            if term in QUESTION_INTENT_TERMS or any(boost in term for boost in QUESTION_INTENT_TERMS)
        ],
        limit=8,
    )
    display_terms = _dedupe_terms([*core_terms[:8], *intent_terms[:4]], limit=12)
    return {
        "cleaned": [cleaned],
        "phrases": phrase_terms,
        "core": core_terms,
        "fallback": fallback_terms,
        "intent": intent_terms,
        "display": display_terms,
    }


def expand_keywords_with_scores(
    keywords: list[str],
    cur=None,
    max_keywords: int = 16,
    min_priority: int = 0,
) -> list[tuple[str, int]]:
    expanded: list[tuple[str, int]] = []
    seen: set[str] = set()
    synonym_lookup = scored_synonyms_map(cur)
    for keyword in keywords:
        token = normalize_text(keyword).lower()
        if not token or token in seen:
            continue
        seen.add(token)
        expanded.append((token, 1000))
        for synonym, priority in synonym_lookup.get(token, []):
            if priority < min_priority:
                continue
            if synonym and synonym not in seen:
                seen.add(synonym)
                # The source term must always outrank a related term. This keeps
                # broad dictionary edges from displacing a direct text match.
                expanded.append((synonym, min(900, max(1, int(priority)) * 10)))
        if len(expanded) >= max_keywords:
            break
    return expanded[:max_keywords]


def expand_keywords_with_synonyms(
    keywords: list[str],
    cur=None,
    max_keywords: int = 16,
    min_priority: int = 0,
) -> list[str]:
    return [
        term
        for term, _score in expand_keywords_with_scores(
            keywords,
            cur=cur,
            max_keywords=max_keywords,
            min_priority=min_priority,
        )
    ]


def related_keywords_for_highlight(keywords: list[str], expanded_keywords: list[str]) -> list[str]:
    base = {normalize_text(keyword).lower() for keyword in keywords if normalize_text(keyword)}
    related: list[str] = []
    seen: set[str] = set()
    for keyword in expanded_keywords:
        normalized = normalize_text(keyword).lower()
        if not normalized or normalized in base or normalized in seen:
            continue
        seen.add(normalized)
        related.append(normalized)
    return related


def source_label(source: str) -> str:
    if source == "mine-city":
        return "美祢市例規"
    if source == "egov":
        return "地方自治法"
    if source == "local-public-service":
        return "地方公務員法"
    return "全ソース"


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


LINK_START = "__REIKI_LINK_START__"
LINK_TEXT = "__REIKI_LINK_TEXT__"
LINK_END = "__REIKI_LINK_END__"
LINK_MARKER_RE = re.compile(
    re.escape(LINK_START) + r".*?" + re.escape(LINK_TEXT) + r"(.*?)" + re.escape(LINK_END)
)


def encode_link_marker(label: str, href: str) -> str:
    return (
        f"{LINK_START}{urllib.parse.quote(href, safe='')}"
        f"{LINK_TEXT}{urllib.parse.quote(label, safe='')}{LINK_END}"
    )


def strip_link_markers(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        return urllib.parse.unquote(match.group(1))

    return LINK_MARKER_RE.sub(repl, text or "")


LINK_MARKER_FRAGMENT_CHARS = r"A-Za-z0-9%._~:/?#\[\]@!$&'()*+,;=\-"
LINK_MARKER_TRAILING_END_RE = re.compile(rf"[{LINK_MARKER_FRAGMENT_CHARS}]*{re.escape(LINK_END)}")
LINK_MARKER_START_FRAGMENT_RE = re.compile(rf"{re.escape(LINK_START)}[{LINK_MARKER_FRAGMENT_CHARS}]*")
LINK_MARKER_TEXT_FRAGMENT_RE = re.compile(rf"{re.escape(LINK_TEXT)}[{LINK_MARKER_FRAGMENT_CHARS}]*")
LINK_MARKER_PREFIX_FRAGMENT_RE = re.compile(r"__REIKI_LINK_[A-Z_]*")
LINK_MARKER_SUFFIX_FRAGMENT_RE = re.compile(r"(?:START|TART|ART|RT|TEXT|EXT|XT|END|ND|D)__")


def clean_link_marker_fragments(text: str) -> str:
    cleaned = strip_link_markers(text)
    cleaned = LINK_MARKER_TRAILING_END_RE.sub("", cleaned)
    cleaned = LINK_MARKER_START_FRAGMENT_RE.sub("", cleaned)
    cleaned = LINK_MARKER_TEXT_FRAGMENT_RE.sub("", cleaned)
    cleaned = LINK_MARKER_PREFIX_FRAGMENT_RE.sub("", cleaned)
    cleaned = LINK_MARKER_SUFFIX_FRAGMENT_RE.sub("", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def meili_is_enabled() -> bool:
    return bool(CFG.meili_enabled and CFG.meili_url and CFG.meili_key)


def meili_request(
    method: str,
    path: str,
    payload: Any | None = None,
    timeout: int = 10,
    expected: tuple[int, ...] = (200, 201, 202, 204),
) -> Any:
    if not meili_is_enabled():
        raise RuntimeError("Meilisearch is not enabled")
    body = None
    headers = {
        "Authorization": f"Bearer {CFG.meili_key}",
        "Content-Type": "application/json",
    }
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{CFG.meili_url}{path}",
        data=body,
        headers=headers,
        method=method.upper(),
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            if resp.status not in expected:
                raise RuntimeError(f"Meilisearch returned HTTP {resp.status}: {raw[:200]}")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Meilisearch returned HTTP {exc.code}: {raw[:300]}") from exc


def meili_health() -> bool:
    if not meili_is_enabled():
        return False
    try:
        payload = meili_request("GET", "/health", timeout=2)
        return payload.get("status") == "available"
    except Exception:
        return False


def wait_meili_task(task_uid: int | None, timeout_seconds: int = 30) -> None:
    if task_uid is None:
        return
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        task = meili_request("GET", f"/tasks/{task_uid}", timeout=5)
        status = str(task.get("status") or "")
        if status in {"succeeded", "failed", "canceled"}:
            if status != "succeeded":
                raise RuntimeError(f"Meilisearch task {task_uid} {status}: {task.get('error')}")
            return
        time.sleep(0.2)
    raise RuntimeError(f"Meilisearch task {task_uid} timed out")


def meili_task_uid(payload: Any) -> int | None:
    try:
        return int(payload.get("taskUid"))
    except Exception:
        return None


def configure_meili_index() -> None:
    if not meili_is_enabled():
        return
    try:
        meili_request("GET", f"/indexes/{urllib.parse.quote(CFG.meili_index)}", timeout=5)
    except Exception:
        task = meili_request(
            "POST",
            "/indexes",
            {"uid": CFG.meili_index, "primaryKey": "id"},
            timeout=5,
        )
        wait_meili_task(meili_task_uid(task), timeout_seconds=30)
    settings = {
        "searchableAttributes": [
            "titleKeyText",
            "articleKeyText",
            "bodyKeyText",
            "titleSearchText",
            "lawNumberSearchText",
            "articleSearchText",
            "categorySearchText",
            "bodyPlain",
        ],
        "displayedAttributes": [
            "id",
            "recordType",
            "documentId",
            "articleId",
            "articleSort",
            "source",
            "title",
            "lawType",
            "lawNumber",
            "sourceUrl",
            "categoryPath",
            "promulgatedAt",
            "articleNumber",
            "articleTitle",
            "parentPath",
            "bodyPlain",
        ],
        "filterableAttributes": ["recordType", "source", "lawType", "promulgatedAt"],
        "sortableAttributes": [],
        "rankingRules": [
            "words",
            "attributeRank",
            "wordPosition",
            "exactness",
        ],
        "typoTolerance": {"enabled": False},
        "proximityPrecision": "byAttribute",
        "localizedAttributes": [],
        "dictionary": [],
        "pagination": {"maxTotalHits": 500},
        "faceting": {"maxValuesPerFacet": 20},
        "prefixSearch": "disabled",
        "searchCutoffMs": 150,
    }
    task = meili_request("PATCH", f"/indexes/{urllib.parse.quote(CFG.meili_index)}/settings", settings, timeout=10)
    wait_meili_task(meili_task_uid(task), timeout_seconds=60)


def configure_meili_minutes_index() -> None:
    """Create the separate, compiled meeting-minutes search index."""
    if not meili_is_enabled():
        return
    index = urllib.parse.quote(CFG.meili_minutes_index)
    try:
        meili_request("GET", f"/indexes/{index}", timeout=5)
    except Exception:
        task = meili_request(
            "POST",
            "/indexes",
            {"uid": CFG.meili_minutes_index, "primaryKey": "id"},
            timeout=5,
        )
        wait_meili_task(meili_task_uid(task), timeout_seconds=30)
    settings = {
        "searchableAttributes": [
            "bodyKeyText",
            "speakerKeyText",
            "bodyPlain",
            "speakerSearchText",
            "meetingSearchText",
        ],
        "displayedAttributes": [
            "id",
            "compileVersionId",
            "utteranceId",
            "dayId",
            "sessionId",
            "meetingDate",
            "calendarYear",
            "section",
            "meetingName",
            "dayTitle",
            "pdfUrl",
            "pageUrl",
            "utteranceOrder",
            "speakerName",
            "speakerTitle",
            "speakerRole",
            "speechType",
            "pageStart",
            "pageEnd",
            "positionTopStart",
            "positionTopEnd",
            "textPreview",
            "bodyPlain",
        ],
        "filterableAttributes": [
            "compileVersionId",
            "calendarYear",
            "meetingDate",
            "section",
            "sessionId",
            "dayId",
            "speakerName",
            "speakerRole",
            "speechType",
        ],
        "sortableAttributes": ["meetingDate", "utteranceOrder"],
        "rankingRules": ["words", "attributeRank", "wordPosition", "exactness"],
        "typoTolerance": {"enabled": False},
        "proximityPrecision": "byAttribute",
        "pagination": {"maxTotalHits": 200000},
        "faceting": {"maxValuesPerFacet": 1000},
        "prefixSearch": "disabled",
        "searchCutoffMs": 180,
    }
    task = meili_request("PATCH", f"/indexes/{index}/settings", settings, timeout=10)
    wait_meili_task(meili_task_uid(task), timeout_seconds=60)


def meili_filter_expr(source: str = "all", law_type: str = "", from_date: str = "", to_date: str = "") -> list[str]:
    filters: list[str] = []
    if source != "all":
        filters.append(f"source = {json.dumps(source, ensure_ascii=False)}")
    if law_type:
        filters.append(f"lawType = {json.dumps(law_type, ensure_ascii=False)}")
    if from_date:
        filters.append(f"(promulgatedAt IS NULL OR promulgatedAt >= {json.dumps(from_date)})")
    if to_date:
        filters.append(f"(promulgatedAt IS NULL OR promulgatedAt <= {json.dumps(to_date)})")
    return filters


def can_use_meili_structured(
    active: list[dict[str, str]],
    source: str,
    law_type: str = "",
    from_date: str = "",
    to_date: str = "",
) -> bool:
    if not meili_is_enabled() or not active:
        return False
    if from_date or to_date:
        return False
    if source not in SOURCE_SCOPES:
        return False
    return all((field.get("op") or "AND").upper() == "AND" for field in active)


def infer_match_reasons_from_hit(hit: dict[str, Any], keywords: list[str], normalized_query: str) -> list[str]:
    reasons: list[str] = []
    title = str(hit.get("title") or "").lower()
    article_number = str(hit.get("articleNumber") or "").lower()
    article_title = str(hit.get("articleTitle") or "").lower()
    body = str(hit.get("bodyPlain") or "").lower()
    if normalized_query and normalized_query in title:
        reasons.append("タイトル")
    if normalized_query and normalized_query in article_number:
        reasons.append("条番号")
    if normalized_query and normalized_query in article_title:
        reasons.append("条名")
    if normalized_query and normalized_query in body:
        reasons.append("条文")
    for keyword in keywords:
        kw = keyword.lower()
        if kw in title and "タイトル" not in reasons:
            reasons.append("タイトル")
        if kw in article_number and "条番号" not in reasons:
            reasons.append("条番号")
        if kw in article_title and "条名" not in reasons:
            reasons.append("条名")
        if kw in body and "条文" not in reasons:
            reasons.append("条文")
    return reasons


MEILI_RETRIEVE_ATTRIBUTES = [
    "documentId",
    "articleId",
    "source",
    "title",
    "lawType",
    "lawNumber",
    "sourceUrl",
    "categoryPath",
    "promulgatedAt",
    "articleNumber",
    "articleTitle",
    "bodyPlain",
]


def serialize_meili_hit(
    hit: dict[str, Any],
    keywords: list[str],
    normalized_query: str,
    score_boost: int = 0,
    document_level: bool = False,
    force_match_reason: str | None = None,
    highlight_terms: list[str] | None = None,
    related_highlight_terms: list[str] | None = None,
) -> dict[str, Any]:
    ranking_score = hit.get("_rankingScore")
    try:
        score = int(float(ranking_score) * 1000) + score_boost
    except Exception:
        score = 500 + score_boost
    body = clean_link_marker_fragments(hit.get("bodyPlain") or "")
    article_id = int(hit["articleId"]) if hit.get("articleId") is not None else None
    if document_level:
        article_id = None
    return {
        "score": score,
        "documentId": int(hit.get("documentId") or 0),
        "articleId": article_id,
        "source": hit.get("source") or "",
        "title": hit.get("title") or "",
        "lawType": hit.get("lawType") or "",
        "lawNumber": hit.get("lawNumber") or "",
        "sourceUrl": hit.get("sourceUrl") or "",
        "articleNumber": None if document_level else hit.get("articleNumber") or None,
        "articleTitle": None if document_level else hit.get("articleTitle") or None,
        "snippet": text_snippet(body, keywords),
        "categoryPath": hit.get("categoryPath") or "",
        "matchReasons": [force_match_reason] if force_match_reason else infer_match_reasons_from_hit(hit, keywords, normalized_query),
        "promulgatedAt": hit.get("promulgatedAt"),
        "highlightTerms": highlight_terms or [],
        "relatedHighlightTerms": related_highlight_terms or [],
    }


def meili_hit_matches_exact(hit: dict[str, Any], keywords: list[str]) -> bool:
    if not keywords:
        return True
    haystack = normalize_text(
        " ".join(
            str(hit.get(field) or "")
            for field in [
                "title",
                "lawNumber",
                "categoryPath",
                "articleNumber",
                "articleTitle",
                "parentPath",
                "bodyPlain",
            ]
        )
    ).lower()
    return all(keyword in haystack for keyword in keywords)


def merge_search_items(primary: list[dict[str, Any]], secondary: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[int, int | None]] = set()
    for item in [*primary, *secondary]:
        key = (int(item.get("documentId") or 0), int(item["articleId"]) if item.get("articleId") is not None else None)
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
        if len(merged) >= limit:
            break
    return merged


def search_title_exact_matches(
    keywords: list[str],
    normalized_query: str,
    source: str,
    law_type: str = "",
    limit: int = 20,
) -> list[dict[str, Any]]:
    if not keywords:
        return []
    where_parts = []
    params: list[Any] = []
    if source != "all":
        where_parts.append("source=%s")
        params.append(source)
    if law_type:
        where_parts.append("law_type=%s")
        params.append(law_type)
    for keyword in keywords:
        like = f"%{keyword}%"
        where_parts.append("(normalized_title LIKE %s OR LOWER(law_number) LIKE %s)")
        params.extend([like, like])
    where_sql = " AND ".join(where_parts) if where_parts else "1=1"
    phrase = normalized_query or keywords[0]
    with db_cursor() as (_, cur):
        cur.execute(
            f"""
            SELECT id, source, title, law_type, law_number, source_url, category_path, promulgated_at,
                   CASE
                     WHEN normalized_title=%s THEN 0
                     WHEN normalized_title LIKE %s THEN 1
                     ELSE 2
                   END AS title_rank,
                   LOCATE(%s, normalized_title) AS phrase_pos,
                   CHAR_LENGTH(normalized_title) AS title_len
            FROM law_documents
            WHERE {where_sql}
            ORDER BY title_rank ASC, phrase_pos ASC, title_len ASC, id ASC
            LIMIT %s
            """,
            tuple([phrase, f"%{phrase}%", phrase] + params + [limit]),
        )
        rows = cur.fetchall() or []
    return [
        {
            "score": 1200 - idx,
            "documentId": int(row["id"]),
            "articleId": None,
            "source": row.get("source") or "",
            "title": row.get("title") or "",
            "lawType": row.get("law_type") or "",
            "lawNumber": row.get("law_number") or "",
            "sourceUrl": row.get("source_url") or "",
            "articleNumber": None,
            "articleTitle": None,
            "snippet": "",
            "categoryPath": row.get("category_path") or "",
            "matchReasons": ["タイトル"],
            "promulgatedAt": str(row["promulgated_at"]) if row.get("promulgated_at") else None,
        }
        for idx, row in enumerate(rows)
    ]


def meili_search_index(payload: dict[str, Any]) -> dict[str, Any]:
    return meili_request(
        "POST",
        f"/indexes/{urllib.parse.quote(CFG.meili_index)}/search",
        payload,
        timeout=5,
    )


def search_documents_meili_structured(
    fields: list[dict[str, str]],
    source: str,
    limit: int,
    offset: int,
    law_type: str = "",
    fuzzy: bool = False,
) -> tuple[int, list[dict[str, Any]]]:
    active = [f for f in fields if f.get("q", "").strip()]
    all_keywords: list[str] = []
    for field in active:
        all_keywords.extend(normalize_text(field["q"]).lower().split())
    if not all_keywords:
        return 0, []
    keywords = expand_keywords_with_synonyms(all_keywords, max_keywords=20) if fuzzy else all_keywords
    related_keywords = related_keywords_for_highlight(all_keywords, keywords) if fuzzy else []
    query_text = " ".join(keywords)
    normalized_query = " ".join(all_keywords)
    filters = meili_filter_expr(source, law_type)
    base_payload: dict[str, Any] = {
        "limit": limit,
        "matchingStrategy": "all",
        "showRankingScore": True,
        "attributesToRetrieve": MEILI_RETRIEVE_ATTRIBUTES,
    }
    if filters:
        base_payload["filter"] = filters

    pre_items: list[dict[str, Any]] = []
    if not fuzzy and offset == 0:
        key_query = build_meili_query_key_text(all_keywords)
        if key_query:
            key_limit = max(limit, min(60, limit * 3))
            for attr, boost, reason, document_level, record_type in [
                ("titleKeyText", 2500, "タイトル", True, "document"),
                ("articleKeyText", 1800, "条名", False, "article"),
            ]:
                key_filters = list(filters)
                key_filters.append(f"recordType = {json.dumps(record_type)}")
                key_payload = {
                    **base_payload,
                    "q": key_query,
                    "limit": key_limit,
                    "offset": 0,
                    "attributesToSearchOn": [attr],
                    "filter": key_filters,
                }
                key_result = meili_search_index(key_payload)
                pre_items.extend(
                    serialize_meili_hit(
                        hit,
                        keywords,
                        normalized_query,
                        score_boost=boost,
                        document_level=document_level,
                        force_match_reason=reason,
                        highlight_terms=all_keywords,
                        related_highlight_terms=related_keywords,
                    )
                    for hit in (key_result.get("hits") or [])
                )

    payload: dict[str, Any] = {
        **base_payload,
        "q": query_text,
        "limit": limit,
        "offset": offset,
        "attributesToSearchOn": [
            "titleSearchText",
            "lawNumberSearchText",
            "articleSearchText",
            "categorySearchText",
            "bodyPlain",
        ],
    }
    result = meili_search_index(payload)
    hits = result.get("hits") or []
    total = int(result.get("estimatedTotalHits") or result.get("totalHits") or len(hits))
    if not fuzzy:
        hits = [hit for hit in hits if meili_hit_matches_exact(hit, all_keywords)]
    items = [serialize_meili_hit(hit, keywords, normalized_query, highlight_terms=all_keywords, related_highlight_terms=related_keywords) for hit in hits]
    if not fuzzy and offset == 0 and not pre_items and not items:
        key_query = build_meili_query_key_text(all_keywords)
        if key_query:
            body_filters = list(filters)
            body_filters.append('recordType = "article"')
            body_result = meili_search_index(
                {
                    **base_payload,
                    "q": key_query,
                    "limit": limit,
                    "offset": 0,
                    "attributesToSearchOn": ["bodyKeyText"],
                    "filter": body_filters,
                }
            )
            pre_items.extend(
                serialize_meili_hit(
                    hit,
                    keywords,
                    normalized_query,
                    score_boost=900,
                    document_level=False,
                    force_match_reason="条文",
                    highlight_terms=all_keywords,
                    related_highlight_terms=related_keywords,
                )
                for hit in (body_result.get("hits") or [])
            )
    if pre_items:
        items = merge_search_items(pre_items, items, limit)
        total = max(total, len(items))
    return total, items


def extract_mine_city_link_href(node: Any) -> str:
    href = normalize_text(str(node.get("href") or ""))
    if href and not href.lower().startswith("javascript:"):
        return href
    onclick = str(node.get("onclick") or node.get("onClick") or "")
    m = re.search(r"fileDownloadAction2\(['\"]([^'\"]+)['\"]", onclick)
    if m:
        return normalize_text(m.group(1))
    return ""


def linked_node_text(node: Any) -> str:
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if getattr(node, "name", None) == "a":
        label = normalize_text(node.get_text("", strip=True))
        href = extract_mine_city_link_href(node)
        return encode_link_marker(label, href) if label and href else label
    parts: list[str] = []
    for child in getattr(node, "contents", []) or []:
        parts.append(linked_node_text(child))
    return normalize_text("".join(parts))


def element_ids(node: Any) -> list[str]:
    if node is None:
        return []
    ids: list[str] = []
    node_id = getattr(node, "get", lambda *_: None)("id")
    if node_id:
        ids.append(str(node_id))
    for child in getattr(node, "find_all", lambda *_args, **_kwargs: [])(id=True):
        child_id = child.get("id")
        if child_id:
            ids.append(str(child_id))
    return ids


def mine_city_source_anchor_aliases(root: Any) -> dict[str, str]:
    aliases: dict[str, str] = {}
    if root is None:
        return aliases
    for alias_node in root.select("#num-ids > div[id]"):
        alias = str(alias_node.get("id") or "").strip()
        target = normalize_text(alias_node.get_text("", strip=True))
        if alias and target:
            aliases[alias] = target
    return aliases


def build_mine_city_source_anchor_map(root: Any, articles: list[dict[str, Any]]) -> dict[str, str]:
    source_anchor_map: dict[str, str] = {}
    id_to_article_key: dict[str, str] = {}
    for article in articles:
        article_key = str(article.get("article_key") or "")
        if not article_key:
            continue
        for anchor_id in article.get("source_anchor_ids", []) or []:
            anchor = str(anchor_id)
            source_anchor_map[anchor] = article_key
            id_to_article_key[anchor] = article_key

    if root is not None and articles:
        first_article_key = str(articles[0].get("article_key") or "")
        current_key = first_article_key
        for eline in root.select("div.eline"):
            ids = element_ids(eline)
            for anchor_id in ids:
                if anchor_id in id_to_article_key:
                    current_key = id_to_article_key[anchor_id]
                    break
            if current_key:
                for anchor_id in ids:
                    source_anchor_map.setdefault(anchor_id, current_key)

    for alias, target in mine_city_source_anchor_aliases(root).items():
        if target in source_anchor_map:
            source_anchor_map[alias] = source_anchor_map[target]
    return source_anchor_map


def serialize_table_block(block: Any) -> str:
    """table div ブロックをタブ区切り行形式のマーカーとしてシリアライズする。"""
    table_elem = block if getattr(block, "name", "") == "table" else block.find("table")
    if table_elem is None:
        return linked_node_text(block)
    rows: list[str] = []
    for tr in table_elem.find_all("tr"):
        cells = [linked_node_text(td) for td in tr.find_all(["td", "th"])]
        rows.append("\t".join(cells))
    if not rows:
        return linked_node_text(block)
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


def mine_city_category_key_from_url(url: str) -> str:
    name = Path(urllib.parse.urlparse(url).path).stem
    suffix = name.removeprefix("r_taikei_")
    if not suffix or suffix == "r_taikei":
        return ""
    parts = [part for part in suffix.split("_") if part]
    return ".".join(parts)


def parse_mine_city_nav_tree(soup: BeautifulSoup) -> dict[str, str]:
    """ナビゲーションツリーから category_key → ラベル のマッピングを構築する。"""
    nav = soup.select_one("ul#navigation")
    if nav is None:
        return {}
    labels: dict[str, str] = {}
    for link in nav.select("a[href]"):
        href = (link.get("href") or "").strip()
        if "r_taikei_" not in href:
            continue
        key = mine_city_category_key_from_url(href)
        # tk-space span はレイアウト用スペーサーなので中身のテキストだけ残す
        for tk_span in link.select("span.tk-space"):
            tk_span.replace_with(tk_span.get_text())
        raw_label = normalize_text(link.get_text("", strip=True))
        # "第X編名前" → "第X編 名前", "第X章名前" → "第X章 名前", "第X節名前" → "第X節 名前"
        label = re.sub(r"(第\d+(?:編|章|節))\s*(?![\s$])", r"\1 ", raw_label)
        if key and label:
            labels[key] = label
    return labels


def build_category_trail(key: str, nav_labels: dict[str, str]) -> str:
    """category_key からフルパス文字列を組み立てる (例: '05.01' → '第5編 給与 / 第1章 報酬・費用弁償')。"""
    parts = key.split(".")
    trail: list[str] = []
    for i in range(len(parts)):
        prefix = ".".join(parts[: i + 1])
        label = nav_labels.get(prefix)
        if label:
            trail.append(label)
    return " / ".join(trail)


def crawl_mine_city_index(start_url: str = MINE_CITY_INDEX_URL) -> tuple[list[dict[str, str | int]], dict[str, str]]:
    queue: list[tuple[str, str]] = [(start_url, mine_city_category_key_from_url(start_url))]
    seen_pages: set[str] = set()
    documents: dict[str, dict[str, str | int]] = {}
    nav_labels: dict[str, str] = {}
    while queue:
        url, category_key = queue.pop(0)
        if url in seen_pages:
            continue
        seen_pages.add(url)
        html = fetch_url_text(url)
        soup = BeautifulSoup(html, "html.parser")
        if not nav_labels:
            nav_labels = parse_mine_city_nav_tree(soup)
        document_order = 0
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
                document_order += 1
                new_doc = {
                    "url": abs_url,
                    "category_path": "",
                    "title_hint": label,
                    "browse_category_key": category_key,
                    "browse_document_order": document_order,
                }
                existing = documents.get(abs_url)
                if existing is None or len(category_key) > len(str(existing.get("browse_category_key", ""))):
                    documents[abs_url] = new_doc
            elif '/reiki_taikei/' in parsed.path and parsed.path.endswith('.html'):
                next_category_key = mine_city_category_key_from_url(abs_url) or category_key
                if abs_url not in seen_pages:
                    queue.append((abs_url, next_category_key))
        if len(seen_pages) > 500:
            break
    for doc in documents.values():
        doc["category_path"] = build_category_trail(str(doc["browse_category_key"]), nav_labels)
    return list(documents.values()), nav_labels


def _start_article(
    articles: list[dict[str, Any]],
    key: str,
    number: str,
    title: str,
    initial_text: str = "",
    source_anchor_ids: list[str] | None = None,
) -> dict[str, Any]:
    current: dict[str, Any] = {
        "article_key": key,
        "article_number": number,
        "article_title": title,
        "parent_path": "",
        "parts": [initial_text] if initial_text else [],
        "source_anchor_ids": list(dict.fromkeys(source_anchor_ids or [])),
    }
    articles.append(current)
    return current


_FUSOKU_RE = re.compile(r"^附\s*則")
_BEPPYO_RE = re.compile(r"^別表")


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
            paragraph_text = linked_node_text(num_p)
            if article_number and paragraph_text.startswith(article_number):
                paragraph_text = paragraph_text[len(article_number) :].lstrip(" 　")
            anchor_ids = element_ids(article_block)
            article_key = anchor_ids[0] if anchor_ids else article_number
            current = _start_article(articles, article_key, article_number, article_title, paragraph_text, anchor_ids)
            continue

        for section_class in ("form_section", "style", "format"):
            section = eline.find("div", class_=section_class)
            if section is None:
                continue
            label = node_text(section)
            if label:
                anchor_ids = element_ids(section)
                article_key = anchor_ids[0] if anchor_ids else label
                current = _start_article(articles, article_key, label, label, linked_node_text(section), anchor_ids)
            break
        else:
            section = None
        if section is not None:
            continue

        xref_frame = eline.find("div", class_="xref_frame")
        if xref_frame is not None and current is not None:
            current["source_anchor_ids"].extend(element_ids(xref_frame))
            image_labels = [normalize_text(str(img.get("alt") or "")) or "画像" for img in xref_frame.find_all("img")]
            if image_labels:
                current["parts"].append(" / ".join(image_labels))
            continue

        # 別表セクション (div.table_section) → 新しい擬似条文として分離
        table_section = eline.find("div", class_="table_section")
        if table_section is not None:
            label = node_text(table_section)
            if label:
                anchor_ids = element_ids(table_section)
                article_key = anchor_ids[0] if anchor_ids else label
                current = _start_article(articles, article_key, label, label, "", anchor_ids)
                continue

        # 附則ヘッダ (div 無しの eline で "附 則" を含む) → 新しい擬似条文
        first_child_div = eline.find("div", recursive=False)
        if first_child_div is None:
            raw = node_text(eline)
            if raw and _FUSOKU_RE.match(raw):
                anchor_ids = element_ids(eline)
                article_key = anchor_ids[0] if anchor_ids else raw
                current = _start_article(articles, article_key, raw, raw, "", anchor_ids)
                continue
            if current is not None and raw:
                current["source_anchor_ids"].extend(element_ids(eline))
                current["parts"].append(raw)
                continue

        if current is None:
            continue

        for block_class in ("clause", "item", "subitem1", "subitem2", "subitem3", "subitem4", "subitem5", "subitem6", "subitem7", "subitem8", "subitem9", "table", "table_frame", "table-wrapper"):
            block = eline.find("div", class_=block_class)
            if block is None:
                continue
            current["source_anchor_ids"].extend(element_ids(block))
            if block_class in {"table", "table_frame", "table-wrapper"}:
                text = serialize_table_block(block)
            else:
                text = linked_node_text(block)
            if text:
                current["parts"].append(text)
            break
        else:
            table_elem = eline.find("table")
            if table_elem is not None:
                current["source_anchor_ids"].extend(element_ids(table_elem))
                text = serialize_table_block(table_elem)
                if text:
                    current["parts"].append(text)

    return [
        {
            "article_key": str(article["article_key"]),
            "article_number": str(article["article_number"]),
            "article_title": str(article["article_title"]),
            "parent_path": str(article["parent_path"]),
            "text": "\n".join(part for part in article["parts"] if part).strip(),
            "source_anchor_ids": list(dict.fromkeys(str(anchor) for anchor in article.get("source_anchor_ids", []) if anchor)),
        }
        for article in articles
    ]


def parse_mine_city_document(item: dict[str, str | int]) -> dict[str, Any]:
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
    source_anchor_map = build_mine_city_source_anchor_map(content_root, articles)
    return {
        'source': 'mine-city',
        'external_id': external_id,
        'title': title,
        'normalized_title': normalize_text(title).lower(),
        'law_type': deduce_law_type(title),
        'law_number': law_number,
        'category_path': item.get('category_path', ''),
        'browse_category_key': str(item.get('browse_category_key', '') or ''),
        'browse_document_order': int(item.get('browse_document_order', 0) or 0),
        'source_url': item['url'],
        'promulgated_at': promulgated_at,
        'effective_at': None,
        'updated_at_source': now_iso(),
        'content_hash': make_document_content_hash(full_text, articles),
        'full_text': full_text,
        'metadata_json': json.dumps(
            {
                'title_hint': item.get('title_hint', ''),
                'browseCategoryKey': item.get('browse_category_key', ''),
                'browseDocumentOrder': int(item.get('browse_document_order', 0) or 0),
                'sourceAnchorMap': source_anchor_map,
            },
            ensure_ascii=False,
        ),
        'articles': articles,
    }


def xml_local_name(tag: str) -> str:
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def xml_node_text(node: ET.Element | None) -> str:
    if node is None:
        return ""
    return normalize_text(" ".join("".join(node.itertext()).split()))


def xml_child(node: ET.Element, child_name: str) -> ET.Element | None:
    for child in list(node):
        if xml_local_name(child.tag) == child_name:
            return child
    return None


def xml_child_text(node: ET.Element, child_name: str) -> str:
    return xml_node_text(xml_child(node, child_name))


def egov_structure_label(node: ET.Element, title_tag: str, suffix: str) -> str:
    title = xml_child_text(node, title_tag)
    if title:
        return title
    num = normalize_text(str(node.attrib.get("Num", "") or ""))
    if num:
        if num.startswith("第") and suffix in num:
            return num
        return f"第{num}{suffix}"
    return suffix


def compact_parent_path(parts: list[str]) -> str:
    cleaned: list[str] = []
    for part in parts:
        label = normalize_text(part)
        if not label:
            continue
        if cleaned and cleaned[-1] == label:
            continue
        cleaned.append(label)
    return " / ".join(cleaned)


def safe_article_key(raw_key: str, max_len: int = 120) -> str:
    key = normalize_text(raw_key)
    if not key:
        return "article"
    if len(key) <= max_len:
        return key
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
    head = key[: max_len - 13]
    return f"{head}-{digest}"


def collect_egov_toc_lines(toc_node: ET.Element) -> list[str]:
    target_tags = {
        "TOCLabel",
        "TOCPartTitle",
        "TOCChapterTitle",
        "TOCSectionTitle",
        "TOCSubsectionTitle",
        "TOCDivisionTitle",
        "ArticleRange",
        "SupplProvisionLabel",
    }
    lines: list[str] = []
    for node in toc_node.iter():
        if xml_local_name(node.tag) not in target_tags:
            continue
        text = xml_node_text(node)
        if not text:
            continue
        if lines and lines[-1] == text:
            continue
        lines.append(text)
    return lines


def parse_egov_article(article_node: ET.Element, context: list[str]) -> dict[str, str]:
    article_number = xml_child_text(article_node, "ArticleTitle")
    article_title = xml_child_text(article_node, "ArticleCaption")
    if not article_number:
        num = normalize_text(str(article_node.attrib.get("Num", "") or ""))
        article_number = f"第{num}条" if num else "条文"
    paragraphs: list[str] = []
    for paragraph in list(article_node):
        if xml_local_name(paragraph.tag) != "Paragraph":
            continue
        text = xml_node_text(paragraph)
        if text:
            paragraphs.append(text)
    article_text = "\n".join(paragraphs).strip() or xml_node_text(article_node)
    parent_path = compact_parent_path(context)
    return {
        "article_key": safe_article_key(f"{parent_path}:{article_number}"),
        "article_number": article_number,
        "article_title": article_title,
        "parent_path": parent_path,
        "text": article_text,
    }


def walk_egov_articles(node: ET.Element, context: list[str], out: list[dict[str, str]]) -> None:
    structure_tags: dict[str, tuple[str, str]] = {
        "Part": ("PartTitle", "編"),
        "Chapter": ("ChapterTitle", "章"),
        "Section": ("SectionTitle", "節"),
        "Subsection": ("SubsectionTitle", "款"),
        "Division": ("DivisionTitle", "目"),
    }
    for child in list(node):
        name = xml_local_name(child.tag)
        if name in structure_tags:
            title_tag, suffix = structure_tags[name]
            label = egov_structure_label(child, title_tag, suffix)
            walk_egov_articles(child, context + [label], out)
            continue
        if name == "Article":
            out.append(parse_egov_article(child, context))
            continue
        walk_egov_articles(child, context, out)


def iter_egov_articles(root: ET.Element) -> list[dict[str, str]]:
    articles: list[dict[str, str]] = []
    law_body = root.find(".//LawBody")
    if law_body is not None:
        for child in list(law_body):
            name = xml_local_name(child.tag)
            if name == "TOC":
                toc_lines = collect_egov_toc_lines(child)
                toc_text = "\n".join(toc_lines).strip() or xml_node_text(child)
                if toc_text:
                    articles.append(
                        {
                            "article_key": safe_article_key("目次"),
                            "article_number": "目次",
                            "article_title": "",
                            "parent_path": "目次",
                            "text": toc_text,
                        }
                    )
                continue
            if name == "MainProvision":
                walk_egov_articles(child, ["本則"], articles)
                continue
            if name == "SupplProvision":
                suppl_label = xml_child_text(child, "SupplProvisionLabel") or "附則"
                amend_law_num = normalize_text(str(child.attrib.get("AmendLawNum", "") or ""))
                suppl_kind = "改正附則" if amend_law_num else "制定附則"
                context = [suppl_kind]
                if suppl_label:
                    context.append(suppl_label)
                walk_egov_articles(child, context, articles)
                continue
    if articles:
        return articles
    # フォールバック: 構造パースに失敗した場合は全 Article を平坦化
    fallback: list[dict[str, str]] = []
    for article in root.findall(".//Article"):
        fallback.append(parse_egov_article(article, []))
    return fallback


def fetch_egov_document(law_def: dict[str, str]) -> dict[str, Any]:
    law_id = law_def["law_id"]
    xml_text = fetch_url_text(f"https://laws.e-gov.go.jp/api/1/lawdata/{law_id}")
    root = ET.fromstring(xml_text)
    law_node = root.find('.//Law')
    law_body = root.find('.//LawBody')
    law_title = normalize_text(''.join(law_body.findtext('LawTitle', default='')) if law_body is not None else str(law_def.get("fallback_title") or law_id))
    law_num = normalize_text(root.findtext('.//Law/LawNum', default=''))
    full_text = normalize_text(' '.join(''.join(root.find('.//LawFullText').itertext()).split()))
    articles = iter_egov_articles(root)
    promulgated_at = None
    if law_node is not None:
        try:
            promulgated_at = f"{int(law_node.attrib.get('Year', '0')) + 1925:04d}-{int(law_node.attrib.get('PromulgateMonth', '1')):02d}-{int(law_node.attrib.get('PromulgateDay', '1')):02d}"
        except Exception:
            promulgated_at = None
    return {
        'source': law_def.get("source") or 'egov',
        'external_id': law_id,
        'title': law_title,
        'normalized_title': normalize_text(law_title).lower(),
        'law_type': '法律',
        'law_number': law_num,
        'category_path': 'e-Gov法令検索',
        'source_url': law_def.get("source_url") or f"https://laws.e-gov.go.jp/law/{law_id}",
        'promulgated_at': promulgated_at,
        'effective_at': None,
        'updated_at_source': now_iso(),
        'content_hash': make_document_content_hash(full_text, articles),
        'full_text': full_text,
        'metadata_json': json.dumps({'lawId': law_id}, ensure_ascii=False),
        'articles': articles,
    }


def build_document_search_terms(document: dict[str, Any]) -> dict[str, int]:
    # 文書全文は非常に長くなり得るため、索引生成時は先頭と末尾の抜粋に絞る。
    full_text_excerpt = trim_text_for_indexing(document.get("full_text", ""), max_chars=4000)
    weights: dict[str, int] = {}
    for term, weight in title_weighted_terms(document.get("title", ""), 12).items():
        weights[term] = max(weights.get(term, 0), weight)
    for term, weight in title_weighted_terms(document.get("law_number", ""), 8).items():
        weights[term] = max(weights.get(term, 0), weight)
    for term, weight in title_weighted_terms(document.get("category_path", ""), 4, include_phrase=False).items():
        weights[term] = max(weights.get(term, 0), weight)
    for term, weight in limited_weighted_terms(
        (document.get("law_type", ""), 4, True),
        (full_text_excerpt, 1, False),
        max_terms=160,
    ).items():
        weights[term] = max(weights.get(term, 0), weight)
    ranked = sorted(weights.items(), key=lambda item: (-item[1], len(item[0]), item[0]))
    return dict(ranked[:220])


def build_article_search_terms(document: dict[str, Any], article: dict[str, Any]) -> dict[str, int]:
    weights: dict[str, int] = {}
    article_text = strip_link_markers(article.get("text", ""))
    for term, weight in title_weighted_terms(document.get("title", ""), 6).items():
        weights[term] = max(weights.get(term, 0), weight)
    for term, weight in title_weighted_terms(article.get("article_number", ""), 12).items():
        weights[term] = max(weights.get(term, 0), weight)
    for term, weight in title_weighted_terms(article.get("article_title", ""), 8).items():
        weights[term] = max(weights.get(term, 0), weight)
    for term, weight in limited_weighted_terms((article_text, 2, False), max_terms=160).items():
        weights[term] = max(weights.get(term, 0), weight)
    ranked = sorted(weights.items(), key=lambda item: (-item[1], len(item[0]), item[0]))
    return dict(ranked[:220])


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


def meili_ja_key(term: str) -> str:
    normalized = normalize_text(term).lower()
    return "jx" + hashlib.blake2b(normalized.encode("utf-8"), digest_size=10).hexdigest()


def build_meili_ja_key_text(values: list[str], max_terms: int = 400, max_ngram: int = 14) -> str:
    """ASCII search keys for exact Japanese substring matching independent of tokenizer output."""
    terms: list[str] = []
    seen: set[str] = set()

    def add(term: str) -> bool:
        normalized = normalize_text(term).lower()
        if len(normalized) < 2:
            return False
        key = meili_ja_key(normalized)
        if key in seen:
            return False
        seen.add(key)
        terms.append(key)
        return len(terms) >= max_terms

    for value in values:
        normalized = normalize_text(value).lower()
        if add(normalized):
            return " ".join(terms)
        for run in re.findall(r"[0-9a-zぁ-んァ-ヶ一-龯々〆ヵヶー]{2,}", normalized):
            if add(run):
                return " ".join(terms)
            max_size = min(max_ngram, len(run))
            for size in range(2, max_size + 1):
                for start in range(0, len(run) - size + 1):
                    if add(run[start:start + size]):
                        return " ".join(terms)
    return " ".join(terms)


def build_meili_query_key_text(keywords: list[str]) -> str:
    keys: list[str] = []
    seen: set[str] = set()
    for keyword in keywords:
        normalized = normalize_text(keyword).lower()
        if len(normalized) < 2:
            continue
        key = meili_ja_key(normalized)
        if key not in seen:
            seen.add(key)
            keys.append(key)
    return " ".join(keys)


def meili_document_record_from_row(row: dict[str, Any]) -> dict[str, Any]:
    title_key_text = build_meili_ja_key_text(
        [row.get("title") or "", row.get("law_number") or "", row.get("category_path") or ""],
        max_terms=500,
        max_ngram=24,
    )
    return {
        "id": f"d{int(row['document_id'])}",
        "recordType": "document",
        "documentId": int(row["document_id"]),
        "articleId": None,
        "articleSort": 0,
        "source": row.get("source") or "",
        "title": row.get("title") or "",
        "lawType": row.get("law_type") or "",
        "lawNumber": row.get("law_number") or "",
        "sourceUrl": row.get("source_url") or "",
        "categoryPath": row.get("category_path") or "",
        "promulgatedAt": str(row["promulgated_at"]) if row.get("promulgated_at") else None,
        "articleNumber": "",
        "articleTitle": "",
        "parentPath": "",
        "titleKeyText": title_key_text,
        "articleKeyText": "",
        "bodyKeyText": "",
        "titleSearchText": row.get("title") or "",
        "lawNumberSearchText": row.get("law_number") or "",
        "articleSearchText": "",
        "categorySearchText": row.get("category_path") or "",
        "bodyPlain": "",
    }


def meili_article_record_from_row(row: dict[str, Any]) -> dict[str, Any]:
    article_id = row.get("article_id")
    body = clean_link_marker_fragments(row.get("article_text") or row.get("full_text") or "")
    article_key_text = build_meili_ja_key_text(
        [row.get("article_number") or "", row.get("article_title") or "", row.get("parent_path") or ""],
        max_terms=240,
        max_ngram=18,
    )
    body_key_text = build_meili_ja_key_text([body], max_terms=160, max_ngram=14)
    return {
        "id": f"a{int(article_id)}" if article_id else f"d{int(row['document_id'])}",
        "recordType": "article" if article_id else "document",
        "documentId": int(row["document_id"]),
        "articleId": int(article_id) if article_id else None,
        "articleSort": int(row.get("sort_key") or 0),
        "source": row.get("source") or "",
        "title": row.get("title") or "",
        "lawType": row.get("law_type") or "",
        "lawNumber": row.get("law_number") or "",
        "sourceUrl": row.get("source_url") or "",
        "categoryPath": row.get("category_path") or "",
        "promulgatedAt": str(row["promulgated_at"]) if row.get("promulgated_at") else None,
        "articleNumber": row.get("article_number") or "",
        "articleTitle": row.get("article_title") or "",
        "parentPath": row.get("parent_path") or "",
        "titleKeyText": "",
        "articleKeyText": article_key_text,
        "bodyKeyText": body_key_text,
        "titleSearchText": "",
        "lawNumberSearchText": "",
        "articleSearchText": " ".join(
            part
            for part in [
                row.get("article_number") or "",
                row.get("article_title") or "",
                row.get("parent_path") or "",
            ]
            if part
        ),
        "categorySearchText": "",
        "bodyPlain": body,
    }


def fetch_meili_documents_for_ids(document_ids: list[int]) -> list[dict[str, Any]]:
    if not document_ids:
        return []
    placeholders = ",".join(["%s"] * len(document_ids))
    with db_cursor() as (_, cur):
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
              d.promulgated_at,
              d.full_text,
              a.id AS article_id,
              a.article_number,
              a.article_title,
              a.parent_path,
              a.sort_key,
              a.text AS article_text
            FROM law_documents d
            LEFT JOIN law_articles a ON a.document_id=d.id
            WHERE d.id IN ({placeholders})
            ORDER BY d.id ASC, a.sort_key ASC, a.id ASC
            """,
            tuple(document_ids),
        )
        rows = cur.fetchall() or []
    docs: list[dict[str, Any]] = []
    seen_documents: set[int] = set()
    for row in rows:
        document_id = int(row["document_id"])
        if document_id not in seen_documents:
            seen_documents.add(document_id)
            docs.append(meili_document_record_from_row(row))
        if row.get("article_id"):
            docs.append(meili_article_record_from_row(row))
    return docs


def index_meili_documents(document_ids: list[int], batch_size: int = 500, ensure_configured: bool = True) -> int:
    if not meili_is_enabled():
        return 0
    if ensure_configured:
        configure_meili_index()
    indexed = 0
    for offset in range(0, len(document_ids), 100):
        source_batch = document_ids[offset:offset + 100]
        docs = fetch_meili_documents_for_ids(source_batch)
        for start in range(0, len(docs), batch_size):
            batch = docs[start:start + batch_size]
            if not batch:
                continue
            task = meili_request(
                "POST",
                f"/indexes/{urllib.parse.quote(CFG.meili_index)}/documents",
                batch,
                timeout=30,
            )
            wait_meili_task(meili_task_uid(task), timeout_seconds=120)
            indexed += len(batch)
    return indexed


def reset_meili_index() -> None:
    if not meili_is_enabled():
        return
    try:
        task = meili_request("DELETE", f"/indexes/{urllib.parse.quote(CFG.meili_index)}", timeout=10)
        wait_meili_task(meili_task_uid(task), timeout_seconds=60)
    except Exception:
        # Deleting a missing index should not block a rebuild.
        pass
    configure_meili_index()


def meili_minutes_record_from_row(row: dict[str, Any]) -> dict[str, Any]:
    """Project one compiled utterance into the minimal record used at search time."""
    body = clean_link_marker_fragments(row.get("body_search_text") or row.get("display_text") or "")
    speaker_name = row.get("speaker_name") or ""
    speaker_title = row.get("speaker_title") or ""
    meeting_name = row.get("meeting_name") or ""
    day_title = row.get("day_title") or ""
    meeting_date = str(row.get("meeting_date") or "")
    return {
        "id": f"v{int(row['version_id'])}u{int(row['utterance_id'])}",
        "compileVersionId": int(row["version_id"]),
        "utteranceId": int(row["utterance_id"]),
        "dayId": int(row["day_id"]),
        "sessionId": int(row["session_id"]),
        "meetingDate": meeting_date,
        "calendarYear": int(meeting_date[:4]) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", meeting_date) else 0,
        "section": row.get("section") or "",
        "meetingName": meeting_name,
        "dayTitle": day_title,
        "pdfUrl": row.get("pdf_url") or "",
        "pageUrl": row.get("page_url") or "",
        "utteranceOrder": int(row.get("utterance_order") or 0),
        "speakerName": speaker_name,
        "speakerTitle": speaker_title,
        "speakerRole": row.get("speaker_role") or "unknown",
        "speechType": row.get("speech_type") or "statement",
        "pageStart": int(row.get("page_start") or 0),
        "pageEnd": int(row.get("page_end") or 0),
        "positionTopStart": float(row.get("position_top_start") or 0),
        "positionTopEnd": float(row.get("position_top_end") or 0),
        "textPreview": row.get("text_preview") or "",
        "bodyKeyText": build_meili_ja_key_text([body], max_terms=120, max_ngram=14),
        "speakerKeyText": build_meili_ja_key_text([speaker_name, speaker_title], max_terms=80, max_ngram=24),
        "bodyPlain": body,
        "speakerSearchText": " ".join(part for part in [speaker_title, speaker_name] if part),
        "meetingSearchText": " ".join(part for part in [meeting_name, day_title, row.get("section") or ""] if part),
    }


def reset_meili_minutes_index() -> None:
    if not meili_is_enabled():
        return
    index = urllib.parse.quote(CFG.meili_minutes_index)
    try:
        task = meili_request("DELETE", f"/indexes/{index}", timeout=10)
        wait_meili_task(meili_task_uid(task), timeout_seconds=90)
    except Exception:
        # A missing index is a normal first-run condition.
        pass
    configure_meili_minutes_index()


def index_meili_minutes_compile(version_id: int, batch_size: int = 800, reset: bool = True) -> int:
    """Index a complete compiled generation without touching the currently active one."""
    if not meili_is_enabled():
        return 0
    if reset:
        reset_meili_minutes_index()
    else:
        configure_meili_minutes_index()
    indexed = 0
    last_utterance_id = 0
    while True:
        with db_cursor() as (_, cur):
            cur.execute(
                """
                SELECT version_id, utterance_id, day_id, session_id, meeting_date, section, meeting_name, day_title,
                       pdf_url, page_url, utterance_order, speaker_name, speaker_title, speaker_role, speech_type,
                       page_start, page_end, position_top_start, position_top_end, text_preview, display_text, body_search_text
                FROM meeting_compiled_utterances
                WHERE version_id=%s AND utterance_id>%s
                ORDER BY utterance_id ASC
                LIMIT %s
                """,
                (version_id, last_utterance_id, batch_size),
            )
            rows = cur.fetchall() or []
        if not rows:
            break
        records = [meili_minutes_record_from_row(row) for row in rows]
        task = meili_request(
            "POST",
            f"/indexes/{urllib.parse.quote(CFG.meili_minutes_index)}/documents",
            records,
            timeout=60,
        )
        wait_meili_task(meili_task_uid(task), timeout_seconds=180)
        indexed += len(records)
        last_utterance_id = int(rows[-1]["utterance_id"])
    return indexed


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
                browse_category_key=%s, browse_document_order=%s, source_url=%s, promulgated_at=%s, effective_at=%s, updated_at_source=%s, search_tokens=%s,
                content_hash=%s, full_text=%s, metadata_json=%s, updated_at=CURRENT_TIMESTAMP
            WHERE id=%s
            """,
            (
                document['title'], document['normalized_title'], document['law_type'], document['law_number'],
                document['category_path'], document.get('browse_category_key', ''), int(document.get('browse_document_order', 0) or 0),
                document['source_url'], document['promulgated_at'], document['effective_at'],
                document['updated_at_source'], document['search_tokens'], document['content_hash'], document['full_text'], document['metadata_json'], document_id,
            ),
        )
    else:
        changed = True
        cur.execute(
            """
            INSERT INTO law_documents (
              source, external_id, title, normalized_title, law_type, law_number, category_path,
              browse_category_key, browse_document_order, source_url, promulgated_at, effective_at, updated_at_source, search_tokens, content_hash, full_text, metadata_json
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                document['source'], document['external_id'], document['title'], document['normalized_title'], document['law_type'],
                document['law_number'], document['category_path'], document.get('browse_category_key', ''), int(document.get('browse_document_order', 0) or 0),
                document['source_url'], document['promulgated_at'], document['effective_at'],
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
                    normalize_text(f"{article['article_number']} {article.get('article_title', '')} {strip_link_markers(article['text'])}").lower(),
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


def update_sync_run_summary(cur, run_id: int, summary: dict[str, Any]) -> None:
    cur.execute(
        "UPDATE sync_runs SET summary_json=%s WHERE id=%s",
        (json.dumps(summary or {}, ensure_ascii=False), run_id),
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
    cur.execute("SELECT COUNT(*) AS cnt FROM law_documents WHERE source='local-public-service'")
    lps_doc_count = int((cur.fetchone() or {}).get('cnt') or 0)
    cur.execute(
        "SELECT COUNT(*) AS cnt FROM law_articles a JOIN law_documents d ON d.id=a.document_id WHERE d.source='local-public-service'"
    )
    lps_article_count = int((cur.fetchone() or {}).get('cnt') or 0)
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
        'localPublicServiceDocumentCount': lps_doc_count,
        'localPublicServiceArticleCount': lps_article_count,
        'mineCityLatestRevisions': _latest_revisions('mine-city'),
        'egovLatestRevisions': _latest_revisions('egov'),
        'localPublicServiceLatestRevisions': _latest_revisions('local-public-service'),
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
        get_sync_settings(cur)
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
        'operation': 'sync',
        'sourceScope': source_scope,
        'documents': 0, 'added': 0, 'updated': 0, 'unchanged': 0, 'articles': 0,
        'progressCurrent': 0,
        'progressTotal': 0,
        'progressLabel': '準備中',
    }
    try:
        mine_city_items: list[dict[str, Any]] = []
        nav_labels: list[dict[str, Any]] = []
        if source_scope in {'all', 'mine-city'}:
            mine_city_items, nav_labels = crawl_mine_city_index()
            summary['mineCityCandidates'] = len(mine_city_items)
        egov_targets = [law for law in EGOV_LAWS if source_scope in {'all', law["source"]}]
        summary['progressTotal'] = len(mine_city_items) + len(egov_targets)
        with db_cursor(commit=True) as (_, cur):
            update_sync_run_summary(cur, run_id, summary)
            if nav_labels:
                cur.execute(
                    "UPDATE sync_settings SET browse_nav_json=%s WHERE id=1",
                    (json.dumps(nav_labels, ensure_ascii=False),),
                )
        if mine_city_items:
            for idx, item in enumerate(mine_city_items, 1):
                summary['progressLabel'] = f"美祢市例規 {idx}/{len(mine_city_items)}"
                parsed = parse_mine_city_document(item)
                with db_cursor(commit=True) as (_, cur):
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
                    summary['progressCurrent'] += 1
                    if idx == 1 or idx == len(mine_city_items) or idx % 10 == 0:
                        update_sync_run_summary(cur, run_id, summary)
        if egov_targets:
            with db_cursor(commit=True) as (_, cur):
                for law_def in egov_targets:
                    summary['progressLabel'] = f"{source_label(law_def['source'])}を同期中"
                    update_sync_run_summary(cur, run_id, summary)
                    parsed = fetch_egov_document(law_def)
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
                    summary['progressCurrent'] += 1
                summary['progressLabel'] = '完了処理中'
                if int(summary.get("updated") or 0) > 0 or int(summary.get("added") or 0) > 0:
                    bump_cache_generation(cur)
                    prune_expired_caches(cur)
                set_sync_run_status(cur, run_id, 'success', summary, None)
                cur.execute(
                    "UPDATE sync_settings SET last_finished_at=%s, last_success_at=%s, last_error=NULL WHERE id=1",
                    (now_iso(), now_iso()),
                )
        else:
            with db_cursor(commit=True) as (_, cur):
                summary['progressLabel'] = '完了処理中'
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


def execute_reindex(batch_size: int = 10) -> dict[str, Any]:
    with db_cursor(commit=True) as (_, cur):
        cur.execute(
            "INSERT INTO sync_runs (run_type, status, started_at, summary_json) VALUES ('manual','running',%s,%s)",
            (now_iso(), json.dumps({'operation': 'reindex'}, ensure_ascii=False)),
        )
        run_id = int(cur.lastrowid)

    with db_cursor() as (_, cur):
        cur.execute("SELECT id FROM law_documents ORDER BY id ASC")
        doc_ids = [int(row["id"]) for row in (cur.fetchall() or [])]

    summary: dict[str, Any] = {
        'operation': 'reindex',
        'documents': len(doc_ids),
        'reindexed': 0,
        'meiliEnabled': meili_is_enabled(),
        'meiliIndexed': 0,
        'batchSize': batch_size,
        'progressCurrent': 0,
        'progressTotal': len(doc_ids),
        'progressLabel': '再索引を開始します',
    }
    try:
        meili_ready = False
        if meili_is_enabled():
            try:
                summary['progressLabel'] = 'Meilisearchインデックスを初期化しています'
                with db_cursor(commit=True) as (_, cur):
                    update_sync_run_summary(cur, run_id, summary)
                reset_meili_index()
                meili_ready = True
            except Exception as exc:
                summary['meiliError'] = str(exc)
                meili_ready = False
        for start in range(0, len(doc_ids), batch_size):
            batch = doc_ids[start:start + batch_size]
            with db_cursor(commit=True) as (_, cur):
                summary['progressLabel'] = f"再索引 {start + 1}-{start + len(batch)} / {len(doc_ids)}"
                update_sync_run_summary(cur, run_id, summary)
                for document_id in batch:
                    rebuild_search_terms_for_document(cur, document_id)
                summary['reindexed'] += len(batch)
                summary['progressCurrent'] += len(batch)
                update_sync_run_summary(cur, run_id, summary)
            if meili_ready:
                try:
                    summary['progressLabel'] = f"Meilisearch投入 {start + 1}-{start + len(batch)} / {len(doc_ids)}"
                    indexed = index_meili_documents(batch, ensure_configured=False)
                    summary['meiliIndexed'] += indexed
                    with db_cursor(commit=True) as (_, cur):
                        update_sync_run_summary(cur, run_id, summary)
                except Exception as exc:
                    summary['meiliError'] = str(exc)
                    meili_ready = False
                    with db_cursor(commit=True) as (_, cur):
                        update_sync_run_summary(cur, run_id, summary)
        with db_cursor(commit=True) as (_, cur):
            summary['progressLabel'] = '完了処理中'
            bump_cache_generation(cur)
            prune_expired_caches(cur)
            set_sync_run_status(cur, run_id, 'success', summary, None)
        return summary
    except Exception as exc:
        with db_cursor(commit=True) as (_, cur):
            set_sync_run_status(cur, run_id, 'failed', summary, str(exc))
        raise


def launch_reindex_in_background(batch_size: int = 10) -> None:
    def _runner() -> None:
        try:
            execute_reindex(batch_size=batch_size)
        except Exception:
            app.logger.exception('Background reindex failed')

    thread = threading.Thread(
        target=_runner,
        name="mine-city-reiki-reindex",
        daemon=True,
    )
    with SYNC_THREAD_LOCK:
        thread.start()


def execute_dictionary_update(include_wordnet: bool = True, include_domain: bool = True) -> dict[str, Any]:
    with db_cursor(commit=True) as (_, cur):
        cur.execute(
            "INSERT INTO sync_runs (run_type, status, started_at, summary_json) VALUES ('manual','running',%s,%s)",
            (now_iso(), json.dumps({'operation': 'dictionary-update'}, ensure_ascii=False)),
        )
        run_id = int(cur.lastrowid)

    summary: dict[str, Any] = {
        'operation': 'dictionary-update',
        'includeWordnet': include_wordnet,
        'includeDomain': include_domain,
        'progressCurrent': 0,
        'progressTotal': int(include_wordnet) + int(include_domain) + 1,
        'progressLabel': '関連語辞書更新を開始します',
    }

    def _progress(label: str, current: int, total: int) -> None:
        summary['progressLabel'] = label
        summary['progressCurrent'] = current
        summary['progressTotal'] = total
        with db_cursor(commit=True) as (_, progress_cur):
            update_sync_run_summary(progress_cur, run_id, summary)

    try:
        with db_cursor(commit=True) as (_, cur):
            summary = build_hybrid_dictionary(
                cur,
                include_wordnet=include_wordnet,
                include_domain=include_domain,
                progress=_progress,
            )
            summary['progressLabel'] = '検索用関連語辞書をコンパイルしています'
            update_sync_run_summary(cur, run_id, summary)
            summary['compiledDictionary'] = compile_synonym_dictionary(cur, output_path=get_compiled_dictionary_path())
            bump_cache_generation(cur)
            prune_expired_caches(cur)
        with db_cursor(commit=True) as (_, cur):
            set_sync_run_status(cur, run_id, 'success', summary, None)
        return summary
    except Exception as exc:
        with db_cursor(commit=True) as (_, cur):
            set_sync_run_status(cur, run_id, 'failed', summary, str(exc))
        raise


def launch_dictionary_update_in_background(include_wordnet: bool = True, include_domain: bool = True) -> None:
    def _runner() -> None:
        try:
            execute_dictionary_update(include_wordnet=include_wordnet, include_domain=include_domain)
        except Exception:
            app.logger.exception('Background dictionary update failed')

    thread = threading.Thread(
        target=_runner,
        name="mine-city-reiki-dictionary-update",
        daemon=True,
    )
    with SYNC_THREAD_LOCK:
        thread.start()


def execute_internet_dictionary_update(
    include_wikidata: bool = True,
    include_curated: bool = True,
    source_url: str = "",
) -> dict[str, Any]:
    with db_cursor(commit=True) as (_, cur):
        cur.execute(
            "INSERT INTO sync_runs (run_type, status, started_at, summary_json) VALUES ('manual','running',%s,%s)",
            (now_iso(), json.dumps({'operation': 'internet-dictionary-update'}, ensure_ascii=False)),
        )
        run_id = int(cur.lastrowid)

    summary: dict[str, Any] = {
        'operation': 'internet-dictionary-update',
        'includeWikidata': include_wikidata,
        'includeCurated': include_curated,
        'sourceUrl': source_url,
        'progressCurrent': 0,
        'progressTotal': int(include_wikidata) + int(include_curated) + int(bool(source_url)) + 1,
        'progressLabel': 'インターネット辞書取り込みを開始します',
    }

    def _progress(label: str, current: int, total: int) -> None:
        summary['progressLabel'] = label
        summary['progressCurrent'] = current
        summary['progressTotal'] = total
        with db_cursor(commit=True) as (_, progress_cur):
            update_sync_run_summary(progress_cur, run_id, summary)

    try:
        with db_cursor(commit=True) as (_, cur):
            summary = build_internet_dictionary(
                cur,
                include_wikidata=include_wikidata,
                include_curated=include_curated,
                source_url=source_url,
                progress=_progress,
            )
            summary['progressLabel'] = '検索用関連語辞書をコンパイルしています'
            update_sync_run_summary(cur, run_id, summary)
            summary['compiledDictionary'] = compile_synonym_dictionary(cur, output_path=get_compiled_dictionary_path())
            bump_cache_generation(cur)
            prune_expired_caches(cur)
        with db_cursor(commit=True) as (_, cur):
            set_sync_run_status(cur, run_id, 'success', summary, None)
        return summary
    except Exception as exc:
        with db_cursor(commit=True) as (_, cur):
            set_sync_run_status(cur, run_id, 'failed', summary, str(exc))
        raise


def launch_internet_dictionary_update_in_background(
    include_wikidata: bool = True,
    include_curated: bool = True,
    source_url: str = "",
) -> None:
    def _runner() -> None:
        try:
            execute_internet_dictionary_update(
                include_wikidata=include_wikidata,
                include_curated=include_curated,
                source_url=source_url,
            )
        except Exception:
            app.logger.exception('Background internet dictionary update failed')

    thread = threading.Thread(
        target=_runner,
        name="mine-city-reiki-internet-dictionary-update",
        daemon=True,
    )
    with SYNC_THREAD_LOCK:
        thread.start()


def execute_minutes_dictionary_update(batch_size: int = 1000) -> dict[str, Any]:
    with db_cursor(commit=True) as (_, cur):
        cur.execute(
            "INSERT INTO sync_runs (run_type, status, started_at, summary_json) VALUES ('manual','running',%s,%s)",
            (now_iso(), json.dumps({'operation': 'minutes-dictionary-update'}, ensure_ascii=False)),
        )
        run_id = int(cur.lastrowid)

    with db_cursor() as (_, cur):
        total = count_unprocessed_minutes_dictionary_rows(cur)

    summary: dict[str, Any] = {
        'operation': 'minutes-dictionary-update',
        'engineVersion': MINUTES_DICTIONARY_ENGINE_VERSION,
        'batchSize': batch_size,
        'unprocessed': total,
        'processed': 0,
        'minutesPairs': 0,
        'inserted': 0,
        'progressCurrent': 0,
        'progressTotal': total,
        'progressLabel': '会議録から固有名詞辞書を作成しています',
    }

    try:
        if total == 0:
            summary['progressLabel'] = '未抽出の会議録はありません'
            with db_cursor(commit=True) as (_, cur):
                summary['compiledDictionary'] = compile_synonym_dictionary(cur, output_path=get_compiled_dictionary_path())
                bump_cache_generation(cur)
                set_sync_run_status(cur, run_id, 'success', summary, None)
            return summary

        while summary['processed'] < total:
            with db_cursor(commit=True) as (_, cur):
                rows = fetch_unprocessed_minutes_dictionary_rows(cur, batch_size=batch_size)
                if not rows:
                    break
                pairs, stats = build_minutes_pairs_from_rows(rows)
                inserted = insert_pairs(
                    cur,
                    pairs,
                    "minutes-domain",
                    MINUTES_DICTIONARY_ENGINE_VERSION,
                    14,
                )
                marked = mark_minutes_dictionary_rows_processed(
                    cur,
                    [int(row["id"]) for row in rows],
                    term_count=int(stats.get("pairs") or 0),
                )
                summary['processed'] += marked
                summary['minutesPairs'] += len(pairs)
                summary['inserted'] += inserted
                summary['lastBatchStats'] = stats
                summary['progressCurrent'] = min(summary['processed'], total)
                summary['progressLabel'] = f"会議録辞書 {summary['processed']:,}/{total:,} 発言を処理しました"
                update_sync_run_summary(cur, run_id, summary)

        with db_cursor(commit=True) as (_, cur):
            summary['progressCurrent'] = total
            summary['progressLabel'] = '検索用関連語辞書をコンパイルしています'
            update_sync_run_summary(cur, run_id, summary)
            summary['compiledDictionary'] = compile_synonym_dictionary(cur, output_path=get_compiled_dictionary_path())
            summary['progressLabel'] = '会議録固有名詞辞書作成が完了しました'
            bump_cache_generation(cur)
            prune_expired_caches(cur)
            set_sync_run_status(cur, run_id, 'success', summary, None)
        return summary
    except Exception as exc:
        with db_cursor(commit=True) as (_, cur):
            set_sync_run_status(cur, run_id, 'failed', summary, str(exc))
        raise


def launch_minutes_dictionary_update_in_background(batch_size: int = 1000) -> None:
    def _runner() -> None:
        try:
            execute_minutes_dictionary_update(batch_size=batch_size)
        except Exception:
            app.logger.exception('Background minutes dictionary update failed')

    thread = threading.Thread(
        target=_runner,
        name="mine-city-reiki-minutes-dictionary-update",
        daemon=True,
    )
    with SYNC_THREAD_LOCK:
        thread.start()


def execute_dictionary_compile(min_priority: int = 1, max_edges_per_term: int = 64) -> dict[str, Any]:
    with db_cursor(commit=True) as (_, cur):
        cur.execute(
            "INSERT INTO sync_runs (run_type, status, started_at, summary_json) VALUES ('manual','running',%s,%s)",
            (now_iso(), json.dumps({'operation': 'dictionary-compile'}, ensure_ascii=False)),
        )
        run_id = int(cur.lastrowid)

    summary: dict[str, Any] = {
        'operation': 'dictionary-compile',
        'minPriority': min_priority,
        'maxEdgesPerTerm': max_edges_per_term,
        'progressCurrent': 0,
        'progressTotal': 1,
        'progressLabel': '検索用関連語辞書をコンパイルしています',
    }
    try:
        with db_cursor(commit=True) as (_, cur):
            compiled = compile_synonym_dictionary(
                cur,
                output_path=get_compiled_dictionary_path(),
                min_priority=min_priority,
                max_edges_per_term=max_edges_per_term,
            )
            summary.update(compiled)
            summary['progressCurrent'] = 1
            summary['progressLabel'] = '検索用関連語辞書のコンパイルが完了しました'
            bump_cache_generation(cur)
            set_sync_run_status(cur, run_id, 'success', summary, None)
        return summary
    except Exception as exc:
        with db_cursor(commit=True) as (_, cur):
            set_sync_run_status(cur, run_id, 'failed', summary, str(exc))
        raise


def launch_dictionary_compile_in_background(min_priority: int = 1, max_edges_per_term: int = 64) -> None:
    def _runner() -> None:
        try:
            execute_dictionary_compile(min_priority=min_priority, max_edges_per_term=max_edges_per_term)
        except Exception:
            app.logger.exception('Background dictionary compile failed')

    thread = threading.Thread(
        target=_runner,
        name="mine-city-reiki-dictionary-compile",
        daemon=True,
    )
    with SYNC_THREAD_LOCK:
        thread.start()


def normalize_minutes_speaker_name(name: str) -> str:
    return re.sub(r"\s+", "", normalize_text(name))


def fiscal_year_for_date(value: date | datetime | None) -> int:
    if isinstance(value, datetime):
        value = value.date()
    if not isinstance(value, date):
        now = datetime.now().date()
        return now.year if now.month >= 4 else now.year - 1
    return value.year if value.month >= 4 else value.year - 1


def role_group_for_roster_caption(caption: str) -> tuple[str, str, float] | None:
    caption = caption or ""
    if caption.startswith(("出席者番号名簿", "一般質問者名簿", "出席委員名簿")):
        return "questioner", "議員・委員", 0.96
    if caption.startswith(("説明員名簿", "役職者名簿")):
        return "answerer", "執行部", 0.94
    return None


def iter_roster_pairs(caption: str, rows: list[list[str]]) -> Iterable[tuple[str, str]]:
    """Yield (title, name) pairs from normalized roster tables."""
    if not rows:
        return
    header = [normalize_text(cell) for cell in rows[0]]
    for row in rows[1:]:
        values = [normalize_text(cell) for cell in row]
        width = min(len(header), len(values))
        index = 0
        while index + 1 < width:
            left = header[index]
            right = header[index + 1]
            first = values[index].strip()
            second = values[index + 1].strip()
            if not first or not second:
                index += 2
                continue
            if left in {"番号", "役職"} and right == "氏名":
                yield first, second
            elif left == "氏名" and right == "役職":
                yield second, first
            index += 2


def extract_roster_profiles_from_tables(tables: list[Any]) -> dict[str, dict[str, Any]]:
    profiles: dict[str, dict[str, Any]] = {}
    for table in tables:
        caption = getattr(table, "caption", "") if not isinstance(table, dict) else table.get("caption", "")
        role_info = role_group_for_roster_caption(caption)
        if not role_info:
            continue
        role, group, confidence = role_info
        rows = getattr(table, "rows", []) if not isinstance(table, dict) else table.get("rows", [])
        table_key = getattr(table, "table_key", "") if not isinstance(table, dict) else table.get("table_key", "")
        for title, name in iter_roster_pairs(caption, rows):
            normalized = normalize_minutes_speaker_name(name)
            if not normalized:
                continue
            current = profiles.get(normalized)
            profile = {
                "normalizedName": normalized,
                "displayName": name,
                "title": re.sub(r"\s+", "", title or ""),
                "role": role,
                "speakerGroup": group,
                "confidence": confidence,
                "sourceTableKey": table_key,
            }
            if current is None or confidence > float(current.get("confidence") or 0):
                profiles[normalized] = profile
    return profiles


def apply_roster_profiles_to_utterances(
    utterances: list[TaggedUtterance],
    profiles: dict[str, dict[str, Any]],
) -> list[TaggedUtterance]:
    if not profiles:
        return reclassify_contextual_utterances(utterances)
    for utterance in utterances:
        if utterance.speaker_role in {"chair", "secretariat"}:
            continue
        profile = profiles.get(normalize_minutes_speaker_name(utterance.speaker_name))
        if not profile:
            continue
        role = str(profile.get("role") or "")
        group = str(profile.get("speakerGroup") or "")
        if not role or role == utterance.speaker_role:
            continue
        can_override = (
            utterance.speaker_role == "unknown"
            or utterance.confidence < float(profile.get("confidence") or 0)
            or (role == "questioner" and re.search(r"(仮議席|[0-9０-９]+番)", utterance.speaker_title))
        )
        if not can_override:
            continue
        utterance.speaker_role = role
        utterance.speaker_group = group
        utterance.speech_type = speech_type_from_role(role)
        utterance.confidence = max(utterance.confidence, float(profile.get("confidence") or 0))
        utterance.reason = "same-day roster table identifies speaker role"
    return reclassify_contextual_utterances(utterances)


def persist_meeting_day_roster_profiles(cur, day_id: int, profiles: dict[str, dict[str, Any]]) -> None:
    cur.execute("DELETE FROM meeting_day_speaker_roster WHERE day_id=%s", (day_id,))
    if not profiles:
        return
    cur.executemany(
        """
        INSERT INTO meeting_day_speaker_roster
          (day_id, normalized_name, display_name, title, speaker_group, role, source_table_key, confidence)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE
          display_name=VALUES(display_name),
          speaker_group=VALUES(speaker_group),
          source_table_key=VALUES(source_table_key),
          confidence=GREATEST(confidence, VALUES(confidence)),
          updated_at=CURRENT_TIMESTAMP
        """,
        [
            (
                day_id,
                profile["normalizedName"],
                profile["displayName"],
                profile["title"],
                profile["speakerGroup"],
                profile["role"],
                profile["sourceTableKey"],
                profile["confidence"],
            )
            for profile in profiles.values()
        ],
    )


def load_meeting_day_roster_profiles(cur, day_id: int) -> dict[str, dict[str, Any]]:
    cur.execute(
        """
        SELECT normalized_name, display_name, title, speaker_group, role, source_table_key, confidence
        FROM meeting_day_speaker_roster
        WHERE day_id=%s
        """,
        (day_id,),
    )
    profiles: dict[str, dict[str, Any]] = {}
    for row in cur.fetchall() or []:
        profiles[row["normalized_name"]] = {
            "normalizedName": row["normalized_name"],
            "displayName": row.get("display_name") or "",
            "title": row.get("title") or "",
            "speakerGroup": row.get("speaker_group") or "",
            "role": row.get("role") or "unknown",
            "sourceTableKey": row.get("source_table_key") or "",
            "confidence": float(row.get("confidence") or 0),
        }
    return profiles


def load_meeting_year_speaker_profiles(cur, meeting_date: date | datetime | None) -> dict[str, dict[str, Any]]:
    fiscal_year = fiscal_year_for_date(meeting_date)
    cur.execute(
        """
        SELECT normalized_name, display_name, title, speaker_group, role, source_type, confidence, occurrences
        FROM meeting_speaker_dictionary
        WHERE fiscal_year=%s AND role IN ('questioner','answerer','report')
        ORDER BY confidence DESC, occurrences DESC
        """,
        (fiscal_year,),
    )
    profiles: dict[str, dict[str, Any]] = {}
    for row in cur.fetchall() or []:
        normalized = row.get("normalized_name") or ""
        if not normalized or normalized in profiles:
            continue
        profiles[normalized] = {
            "normalizedName": normalized,
            "displayName": row.get("display_name") or "",
            "title": row.get("title") or "",
            "speakerGroup": row.get("speaker_group") or "",
            "role": row.get("role") or "unknown",
            "sourceTableKey": row.get("source_type") or "year-dictionary",
            "confidence": min(0.9, float(row.get("confidence") or 0.75)),
        }
    return profiles


def merge_speaker_profiles(*profile_sets: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for profiles in profile_sets:
        for normalized, profile in profiles.items():
            if normalized not in merged:
                merged[normalized] = profile
    return merged


def rebuild_meeting_day_roster_profiles_from_tables(cur, day_id: int) -> dict[str, dict[str, Any]]:
    cur.execute(
        """
        SELECT table_key, caption, rows_json
        FROM meeting_tables
        WHERE day_id=%s
        ORDER BY page ASC, position_top ASC, id ASC
        """,
        (day_id,),
    )
    tables: list[dict[str, Any]] = []
    for row in cur.fetchall() or []:
        try:
            rows = json.loads(row.get("rows_json") or "[]")
        except Exception:
            rows = []
        caption, rows, _html, _search_text = normalize_minutes_table_for_display(
            row.get("caption") or "",
            rows,
            "",
            "",
        )
        tables.append({"table_key": row.get("table_key") or "", "caption": caption, "rows": rows})
    profiles = extract_roster_profiles_from_tables(tables)
    persist_meeting_day_roster_profiles(cur, day_id, profiles)
    return profiles


def upsert_meeting_session(cur, item: Any) -> int:
    cur.execute(
        """
        INSERT INTO meeting_sessions (source_url, section, meeting_name, title)
        VALUES (%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE
          section=VALUES(section),
          meeting_name=VALUES(meeting_name),
          title=VALUES(title),
          updated_at=CURRENT_TIMESTAMP
        """,
        (item.page_url, item.section, item.meeting_name, item.meeting_name),
    )
    cur.execute("SELECT id FROM meeting_sessions WHERE source_url=%s", (item.page_url,))
    return int((cur.fetchone() or {})["id"])


def upsert_meeting_day(cur, item: Any, session_id: int) -> int:
    cur.execute(
        """
        INSERT INTO meeting_days
          (session_id, source_url, page_url, pdf_url, meeting_date, date_label, title, extraction_status)
        VALUES (%s,%s,%s,%s,%s,%s,%s,'pending')
        ON DUPLICATE KEY UPDATE
          session_id=VALUES(session_id),
          source_url=VALUES(source_url),
          page_url=VALUES(page_url),
          meeting_date=VALUES(meeting_date),
          date_label=VALUES(date_label),
          title=VALUES(title),
          updated_at=CURRENT_TIMESTAMP
        """,
        (session_id, item.pdf_url, item.page_url, item.pdf_url, item.meeting_date, item.date_label, item.title),
    )
    cur.execute("SELECT id FROM meeting_days WHERE pdf_url=%s", (item.pdf_url,))
    return int((cur.fetchone() or {})["id"])


def upsert_meeting_speaker(cur, speaker_name: str, speaker_title: str, speaker_role: str, speaker_group: str) -> int:
    normalized = normalize_minutes_speaker_name(speaker_name)
    cur.execute(
        """
        INSERT INTO meeting_speakers (normalized_name, display_name, title, speaker_group, role)
        VALUES (%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE
          display_name=VALUES(display_name),
          speaker_group=VALUES(speaker_group),
          updated_at=CURRENT_TIMESTAMP
        """,
        (normalized, speaker_name, speaker_title, speaker_group, speaker_role),
    )
    cur.execute(
        "SELECT id FROM meeting_speakers WHERE normalized_name=%s AND title=%s AND role=%s",
        (normalized, speaker_title, speaker_role),
    )
    return int((cur.fetchone() or {})["id"])


def upsert_meeting_speaker_dictionary(cur, day_id: int, meeting_date: date | datetime | None, utterance: Any) -> None:
    normalized = normalize_minutes_speaker_name(utterance.speaker_name)
    if not normalized:
        return
    fiscal_year = fiscal_year_for_date(meeting_date)
    valid_from = meeting_date.date() if isinstance(meeting_date, datetime) else meeting_date
    cur.execute(
        """
        INSERT INTO meeting_speaker_dictionary
          (fiscal_year, valid_from, valid_to, normalized_name, display_name, title, speaker_group, role,
           source_type, confidence, first_day_id, last_day_id, occurrences)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'utterance',%s,%s,%s,1)
        ON DUPLICATE KEY UPDATE
          valid_from=LEAST(COALESCE(valid_from, VALUES(valid_from)), COALESCE(VALUES(valid_from), valid_from)),
          valid_to=GREATEST(COALESCE(valid_to, VALUES(valid_to)), COALESCE(VALUES(valid_to), valid_to)),
          display_name=VALUES(display_name),
          speaker_group=VALUES(speaker_group),
          source_type='utterance',
          confidence=GREATEST(confidence, VALUES(confidence)),
          last_day_id=VALUES(last_day_id),
          occurrences=occurrences + 1,
          updated_at=CURRENT_TIMESTAMP
        """,
        (
            fiscal_year,
            valid_from,
            valid_from,
            normalized,
            utterance.speaker_name,
            utterance.speaker_title,
            utterance.speaker_group,
            utterance.speaker_role,
            utterance.confidence,
            day_id,
            day_id,
        ),
    )


def rebuild_meeting_speaker_dictionary_for_year(cur, fiscal_year: int) -> None:
    start = date(fiscal_year, 4, 1)
    end = date(fiscal_year + 1, 4, 1)
    cur.execute("DELETE FROM meeting_speaker_dictionary WHERE fiscal_year=%s AND source_type IN ('utterance','roster')", (fiscal_year,))
    cur.execute(
        """
        INSERT INTO meeting_speaker_dictionary
          (fiscal_year, valid_from, valid_to, normalized_name, display_name, title, speaker_group, role,
           source_type, confidence, first_day_id, last_day_id, occurrences)
        SELECT
          %s AS fiscal_year,
          MIN(d.meeting_date) AS valid_from,
          MAX(d.meeting_date) AS valid_to,
          REPLACE(REPLACE(u.speaker_name, ' ', ''), '　', '') AS normalized_name,
          MIN(u.speaker_name) AS display_name,
          u.speaker_title AS title,
          MIN(u.speaker_group) AS speaker_group,
          u.speaker_role AS role,
          'utterance' AS source_type,
          MAX(u.confidence) AS confidence,
          MIN(d.id) AS first_day_id,
          MAX(d.id) AS last_day_id,
          COUNT(*) AS occurrences
        FROM meeting_utterances u
        JOIN meeting_days d ON d.id=u.day_id
        WHERE d.meeting_date >= %s AND d.meeting_date < %s AND u.speaker_name <> ''
        GROUP BY REPLACE(REPLACE(u.speaker_name, ' ', ''), '　', ''), u.speaker_title, u.speaker_role
        """,
        (fiscal_year, start, end),
    )
    cur.execute(
        """
        INSERT INTO meeting_speaker_dictionary
          (fiscal_year, valid_from, valid_to, normalized_name, display_name, title, speaker_group, role,
           source_type, confidence, first_day_id, last_day_id, occurrences)
        SELECT
          %s AS fiscal_year,
          MIN(d.meeting_date) AS valid_from,
          MAX(d.meeting_date) AS valid_to,
          r.normalized_name,
          MIN(r.display_name) AS display_name,
          r.title,
          MIN(r.speaker_group) AS speaker_group,
          r.role,
          'roster' AS source_type,
          MAX(r.confidence) AS confidence,
          MIN(d.id) AS first_day_id,
          MAX(d.id) AS last_day_id,
          COUNT(*) AS occurrences
        FROM meeting_day_speaker_roster r
        JOIN meeting_days d ON d.id=r.day_id
        WHERE d.meeting_date >= %s AND d.meeting_date < %s AND r.normalized_name <> ''
        GROUP BY r.normalized_name, r.title, r.role
        ON DUPLICATE KEY UPDATE
          valid_from=LEAST(COALESCE(valid_from, VALUES(valid_from)), COALESCE(VALUES(valid_from), valid_from)),
          valid_to=GREATEST(COALESCE(valid_to, VALUES(valid_to)), COALESCE(VALUES(valid_to), valid_to)),
          speaker_group=VALUES(speaker_group),
          confidence=GREATEST(confidence, VALUES(confidence)),
          first_day_id=LEAST(COALESCE(first_day_id, VALUES(first_day_id)), COALESCE(VALUES(first_day_id), first_day_id)),
          last_day_id=GREATEST(COALESCE(last_day_id, VALUES(last_day_id)), COALESCE(VALUES(last_day_id), last_day_id)),
          occurrences=occurrences + VALUES(occurrences),
          updated_at=CURRENT_TIMESTAMP
        """,
        (fiscal_year, start, end),
    )


def reset_meeting_day_content(cur, day_id: int) -> None:
    cur.execute("DELETE FROM meeting_tables WHERE day_id=%s", (day_id,))
    cur.execute("DELETE FROM meeting_utterances WHERE day_id=%s", (day_id,))


MINUTES_SEARCH_INDEX_PREVIEW_CHARS = 420


def rebuild_meeting_search_index_for_day(cur, day_id: int) -> int:
    cur.execute("DELETE FROM meeting_utterance_search_index WHERE day_id=%s", (day_id,))
    cur.execute(
        """
        INSERT INTO meeting_utterance_search_index
          (utterance_id, day_id, session_id, meeting_date, section, meeting_name, day_title, pdf_url, page_url,
           utterance_order, speaker_name, speaker_title, speaker_role, speaker_group, speech_type,
           page_start, page_end, position_top_start, position_top_end, text_preview, body_search_text, search_text)
        SELECT
          u.id, u.day_id, d.session_id, d.meeting_date, s.section, s.meeting_name, d.title, d.pdf_url, d.page_url,
          u.utterance_order, u.speaker_name, u.speaker_title, u.speaker_role, u.speaker_group, u.speech_type,
          u.page_start, u.page_end, u.position_top_start, u.position_top_end,
          SUBSTRING(u.text, 1, %s), u.text, u.search_text
        FROM meeting_utterances u
        JOIN meeting_days d ON d.id=u.day_id
        JOIN meeting_sessions s ON s.id=d.session_id
        WHERE u.day_id=%s
        ORDER BY u.utterance_order ASC
        """,
        (MINUTES_SEARCH_INDEX_PREVIEW_CHARS, day_id),
    )
    return int(cur.rowcount or 0)


def rebuild_meeting_search_index(cur) -> int:
    cur.execute("DELETE FROM meeting_utterance_search_index")
    cur.execute(
        """
        INSERT INTO meeting_utterance_search_index
          (utterance_id, day_id, session_id, meeting_date, section, meeting_name, day_title, pdf_url, page_url,
           utterance_order, speaker_name, speaker_title, speaker_role, speaker_group, speech_type,
           page_start, page_end, position_top_start, position_top_end, text_preview, body_search_text, search_text)
        SELECT
          u.id, u.day_id, d.session_id, d.meeting_date, s.section, s.meeting_name, d.title, d.pdf_url, d.page_url,
          u.utterance_order, u.speaker_name, u.speaker_title, u.speaker_role, u.speaker_group, u.speech_type,
          u.page_start, u.page_end, u.position_top_start, u.position_top_end,
          SUBSTRING(u.text, 1, %s), u.text, u.search_text
        FROM meeting_utterances u
        JOIN meeting_days d ON d.id=u.day_id
        JOIN meeting_sessions s ON s.id=d.session_id
        ORDER BY d.meeting_date DESC, u.day_id ASC, u.utterance_order ASC
        """,
        (MINUTES_SEARCH_INDEX_PREVIEW_CHARS,),
    )
    return int(cur.rowcount or 0)


def is_meeting_search_index_ready(cur) -> bool:
    cur.execute("SELECT 1 FROM meeting_utterance_search_index LIMIT 1")
    return cur.fetchone() is not None


def persist_meeting_day_content(cur, day_id: int, item: Any, extracted: Any) -> dict[str, int]:
    all_lines = [line for page in extracted.pages for line in page.lines]
    utterances = tag_utterances(all_lines)
    document_key = f"minutes-{day_id}"
    tables = refine_person_roster_tables(extract_coordinate_tables(extracted.pages, document_key), utterances)
    roster_profiles = extract_roster_profiles_from_tables(tables)
    year_profiles = load_meeting_year_speaker_profiles(cur, item.meeting_date)
    speaker_profiles = merge_speaker_profiles(roster_profiles, year_profiles)
    utterances = apply_roster_profiles_to_utterances(utterances, speaker_profiles)
    reset_meeting_day_content(cur, day_id)
    persist_meeting_day_roster_profiles(cur, day_id, roster_profiles)
    speaker_count = 0
    speaker_id_cache: dict[tuple[str, str, str, str], int] = {}
    for utterance in utterances:
        speaker_key = (
            utterance.speaker_name,
            utterance.speaker_title,
            utterance.speaker_role,
            utterance.speaker_group,
        )
        speaker_id = speaker_id_cache.get(speaker_key)
        if speaker_id is None:
            speaker_id = upsert_meeting_speaker(
                cur,
                utterance.speaker_name,
                utterance.speaker_title,
                utterance.speaker_role,
                utterance.speaker_group,
            )
            speaker_id_cache[speaker_key] = speaker_id
        speaker_count += 1
        search_text = "\n".join(
            [
                item.section,
                item.meeting_name,
                item.title,
                utterance.speaker_name,
                utterance.speaker_title,
                utterance.speaker_role,
                utterance.text,
            ]
        )
        cur.execute(
            """
            INSERT INTO meeting_utterances
              (day_id, speaker_id, speaker_name, speaker_title, speaker_role, speaker_group, speech_type,
               utterance_order, page_start, page_end, position_top_start, position_top_end,
               text, search_text, confidence, reason, engine_version)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                day_id,
                speaker_id,
                utterance.speaker_name,
                utterance.speaker_title,
                utterance.speaker_role,
                utterance.speaker_group,
                utterance.speech_type,
                utterance.order,
                utterance.page_start,
                utterance.page_end,
                utterance.position_top_start,
                utterance.position_top_end,
                utterance.text,
                search_text,
                utterance.confidence,
                utterance.reason,
                SPEAKER_ENGINE_VERSION,
            ),
        )
        insert_minutes_short_terms_for_utterance(cur, int(cur.lastrowid), day_id, search_text)
    indexed = rebuild_meeting_search_index_for_day(cur, day_id)
    for table in tables:
        cur.execute(
            """
            INSERT INTO meeting_tables
              (day_id, table_key, page, position_top, position_bottom, caption, rows_json, html, search_text, confidence, engine_version)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                day_id,
                table.table_key,
                table.page,
                table.position_top,
                table.position_bottom,
                table.caption,
                json.dumps(table.rows, ensure_ascii=False),
                table.html,
                table.search_text,
                table.confidence,
                PERSON_TABLE_ENGINE_VERSION
                if table.caption.startswith(("出席者番号名簿", "一般質問者名簿", "出席委員名簿", "説明員名簿", "役職者名簿"))
                else TABLE_ENGINE_VERSION,
            ),
        )
    cur.execute(
        """
        UPDATE meeting_days
        SET pdf_hash=%s, extraction_status='success', page_count=%s, text_char_count=%s,
            error_text=NULL, updated_at=CURRENT_TIMESTAMP
        WHERE id=%s
        """,
        (extracted.sha256, extracted.page_count, len(extracted.text or ""), day_id),
    )
    return {
        "utterances": len(utterances),
        "tables": len(tables),
        "speakers": speaker_count,
        "indexed": indexed,
        "rosterProfiles": len(roster_profiles),
        "speakerProfiles": len(speaker_profiles),
        "fiscalYear": fiscal_year_for_date(item.meeting_date),
    }


def build_minutes_utterance_search_text(
    section: str,
    meeting_name: str,
    day_title: str,
    speaker_name: str,
    speaker_title: str,
    speaker_role: str,
    text: str,
) -> str:
    return "\n".join(
        [
            section,
            meeting_name,
            day_title,
            speaker_name,
            speaker_title,
            speaker_role,
            text,
        ]
    )


def retag_meeting_day_utterances(cur, day_id: int) -> dict[str, int]:
    cur.execute(
        """
        SELECT
          d.id AS day_id, d.meeting_date, d.title AS day_title,
          s.section, s.meeting_name,
          u.id, u.utterance_order, u.speaker_name, u.speaker_title, u.speaker_role,
          u.speaker_group, u.speech_type, u.text, u.page_start, u.page_end,
          u.position_top_start, u.position_top_end, u.confidence, u.reason,
          u.search_text, u.engine_version
        FROM meeting_days d
        JOIN meeting_sessions s ON s.id=d.session_id
        JOIN meeting_utterances u ON u.day_id=d.id
        WHERE d.id=%s
        ORDER BY u.utterance_order ASC
        """,
        (day_id,),
    )
    rows = cur.fetchall() or []
    if not rows:
        return {"processed": 0, "updated": 0, "roleChanged": 0}

    tagged: list[TaggedUtterance] = []
    for row in rows:
        role, group, confidence, reason = classify_speaker(row.get("speaker_title") or "", row.get("speaker_name") or "")
        tagged.append(
            TaggedUtterance(
                order=int(row.get("utterance_order") or 0),
                speaker_name=row.get("speaker_name") or "",
                speaker_title=row.get("speaker_title") or "",
                speaker_role=role,
                speaker_group=group,
                speech_type=speech_type_from_role(role),
                text=row.get("text") or "",
                page_start=int(row.get("page_start") or 0),
                page_end=int(row.get("page_end") or 0),
                position_top_start=float(row.get("position_top_start") or 0),
                position_top_end=float(row.get("position_top_end") or 0),
                confidence=confidence,
                reason=reason,
            )
        )
    meeting_date = rows[0].get("meeting_date")
    section = rows[0].get("section") or ""
    meeting_name = rows[0].get("meeting_name") or ""
    day_title = rows[0].get("day_title") or ""
    roster_profiles = load_meeting_day_roster_profiles(cur, day_id)
    if not roster_profiles:
        roster_profiles = rebuild_meeting_day_roster_profiles_from_tables(cur, day_id)
    year_profiles = load_meeting_year_speaker_profiles(cur, meeting_date)
    speaker_profiles = merge_speaker_profiles(roster_profiles, year_profiles)
    tagged = apply_roster_profiles_to_utterances(tagged, speaker_profiles)

    updated = 0
    role_changed = 0
    dictionary_changed = False
    speaker_id_cache: dict[tuple[str, str, str, str], int] = {}
    for row, utterance in zip(rows, tagged):
        search_text = build_minutes_utterance_search_text(
            section,
            meeting_name,
            day_title,
            utterance.speaker_name,
            utterance.speaker_title,
            utterance.speaker_role,
            utterance.text,
        )
        search_text_changed = row.get("search_text") != search_text
        speaker_changed = (
            row.get("speaker_role") != utterance.speaker_role
            or row.get("speaker_group") != utterance.speaker_group
            or row.get("speech_type") != utterance.speech_type
        )
        changed = (
            speaker_changed
            or row.get("reason") != utterance.reason
            or search_text_changed
            or row.get("engine_version") != SPEAKER_ENGINE_VERSION
        )
        if row.get("speaker_role") != utterance.speaker_role:
            role_changed += 1
        if speaker_changed:
            dictionary_changed = True
        if changed:
            speaker_key = (
                utterance.speaker_name,
                utterance.speaker_title,
                utterance.speaker_role,
                utterance.speaker_group,
            )
            speaker_id = speaker_id_cache.get(speaker_key)
            if speaker_id is None:
                speaker_id = upsert_meeting_speaker(
                    cur,
                    utterance.speaker_name,
                    utterance.speaker_title,
                    utterance.speaker_role,
                    utterance.speaker_group,
                )
                speaker_id_cache[speaker_key] = speaker_id
            cur.execute(
                """
                UPDATE meeting_utterances
                SET speaker_id=%s, speaker_role=%s, speaker_group=%s, speech_type=%s,
                    search_text=%s, confidence=%s, reason=%s, engine_version=%s
                WHERE id=%s
                """,
                (
                    speaker_id,
                    utterance.speaker_role,
                    utterance.speaker_group,
                    utterance.speech_type,
                    search_text,
                    utterance.confidence,
                    utterance.reason,
                    SPEAKER_ENGINE_VERSION,
                    int(row["id"]),
                ),
            )
            if search_text_changed:
                cur.execute("DELETE FROM meeting_utterance_short_terms WHERE utterance_id=%s", (int(row["id"]),))
                insert_minutes_short_terms_for_utterance(cur, int(row["id"]), day_id, search_text)
            updated += 1
    indexed = rebuild_meeting_search_index_for_day(cur, day_id)
    fiscal_year = fiscal_year_for_date(meeting_date) if meeting_date else None
    return {
        "processed": len(rows),
        "updated": updated,
        "roleChanged": role_changed,
        "indexed": indexed,
        "rosterProfiles": len(roster_profiles),
        "speakerProfiles": len(speaker_profiles),
        "fiscalYear": fiscal_year,
        "dictionaryChanged": dictionary_changed,
    }


def update_minutes_run_summary(run_id: int, extract_run_id: int | None, summary: dict[str, Any]) -> None:
    with db_cursor(commit=True) as (_, cur):
        update_sync_run_summary(cur, run_id, summary)
        if extract_run_id:
            cur.execute(
                "UPDATE meeting_extract_runs SET summary_json=%s WHERE id=%s",
                (json.dumps(summary, ensure_ascii=False), extract_run_id),
            )


def execute_minutes_sync(recent_days: int | None = 365) -> dict[str, Any]:
    raw_recent_days = int(recent_days if recent_days is not None else 365)
    recent_days = 0 if raw_recent_days <= 0 else max(1, min(36600, raw_recent_days))
    with db_cursor(commit=True) as (_, cur):
        summary: dict[str, Any] = {
            "operation": "minutes-sync",
            "recentDays": recent_days,
            "discovered": 0,
            "processed": 0,
            "skipped": 0,
            "failed": 0,
            "utterances": 0,
            "tables": 0,
            "indexed": 0,
            "rosterProfiles": 0,
            "speakerProfiles": 0,
            "rebuiltYears": 0,
            "progressCurrent": 0,
            "progressTotal": 1,
            "progressLabel": "会議録PDFリンクを収集しています",
        }
        cur.execute(
            "INSERT INTO sync_runs (run_type, status, started_at, summary_json) VALUES ('manual','running',%s,%s)",
            (now_iso(), json.dumps(summary, ensure_ascii=False)),
        )
        run_id = int(cur.lastrowid)
        cur.execute(
            """
            INSERT INTO meeting_extract_runs (status, recent_days, summary_json, engine_versions)
            VALUES ('running',%s,%s,%s)
            """,
            (
                recent_days,
                json.dumps(summary, ensure_ascii=False),
                f"{SPEAKER_ENGINE_VERSION},{TABLE_ENGINE_VERSION}",
            ),
        )
        extract_run_id = int(cur.lastrowid)
    try:
        items = crawl_minutes_pdfs(recent_days=recent_days)
        summary["discovered"] = len(items)
        summary["progressTotal"] = max(1, len(items))
        summary["progressLabel"] = f"{len(items)}件のPDFを検出しました"
        update_minutes_run_summary(run_id, extract_run_id, summary)
        years_to_rebuild: set[int] = set()
        for index, item in enumerate(items, start=1):
            summary["progressCurrent"] = index - 1
            summary["progressLabel"] = f"{index}/{len(items)} {item.section} {item.title} を抽出しています"
            update_minutes_run_summary(run_id, extract_run_id, summary)
            day_id: int | None = None
            try:
                with db_cursor(commit=True) as (_, cur):
                    session_id = upsert_meeting_session(cur, item)
                    day_id = upsert_meeting_day(cur, item, session_id)
                pdf_bytes = download_pdf(item.pdf_url)
                pdf_hash = hashlib.sha256(pdf_bytes).hexdigest()
                with db_cursor() as (_, cur):
                    cur.execute(
                        "SELECT pdf_hash, extraction_status FROM meeting_days WHERE id=%s",
                        (day_id,),
                    )
                    existing_day = cur.fetchone() or {}
                if existing_day.get("pdf_hash") == pdf_hash and existing_day.get("extraction_status") == "success":
                    summary["skipped"] += 1
                    summary["processed"] += 1
                else:
                    with db_cursor(commit=True) as (_, cur):
                        cur.execute("UPDATE meeting_days SET extraction_status='running', error_text=NULL WHERE id=%s", (day_id,))
                    extracted = extract_pdf_from_bytes(pdf_bytes)
                    with db_cursor(commit=True) as (_, cur):
                        counts = persist_meeting_day_content(cur, day_id, item, extracted)
                    summary["processed"] += 1
                    summary["utterances"] += counts["utterances"]
                    summary["tables"] += counts["tables"]
                    summary["indexed"] += int(counts.get("indexed") or 0)
                    summary["rosterProfiles"] += int(counts.get("rosterProfiles") or 0)
                    summary["speakerProfiles"] += int(counts.get("speakerProfiles") or 0)
                    fiscal_year = counts.get("fiscalYear")
                    if fiscal_year:
                        years_to_rebuild.add(int(fiscal_year))
            except Exception as exc:
                summary["failed"] += 1
                if day_id:
                    with db_cursor(commit=True) as (_, cur):
                        cur.execute(
                            "UPDATE meeting_days SET extraction_status='failed', error_text=%s WHERE id=%s",
                            (str(exc)[:2000], day_id),
                        )
                app.logger.exception("Meeting minutes extraction failed: %s", item.pdf_url)
            summary["progressCurrent"] = index
            update_minutes_run_summary(run_id, extract_run_id, summary)
        sorted_years = sorted(years_to_rebuild)
        for index, fiscal_year in enumerate(sorted_years, start=1):
            with db_cursor(commit=True) as (_, cur):
                summary["progressLabel"] = f"発言者辞書を再構築しています {index}/{len(sorted_years)}"
                update_minutes_run_summary(run_id, extract_run_id, summary)
                rebuild_meeting_speaker_dictionary_for_year(cur, fiscal_year)
                summary["rebuiltYears"] = index
                update_minutes_run_summary(run_id, extract_run_id, summary)
        with db_cursor(commit=True) as (_, cur):
            summary["progressLabel"] = "会議録同期が完了しました"
            bump_cache_generation(cur)
            prune_expired_caches(cur)
            set_sync_run_status(cur, run_id, "success", summary, None)
            cur.execute(
                "UPDATE meeting_extract_runs SET status='success', finished_at=CURRENT_TIMESTAMP, summary_json=%s WHERE id=%s",
                (json.dumps(summary, ensure_ascii=False), extract_run_id),
            )
        try:
            compile_summary = execute_minutes_compile(trigger="minutes-sync")
            summary["compiled"] = True
            summary["compileVersionKey"] = compile_summary.get("versionKey")
        except Exception as compile_exc:
            summary["compiled"] = False
            summary["compileError"] = str(compile_exc)
            app.logger.exception("Meeting minutes compile after sync failed")
        with db_cursor(commit=True) as (_, cur):
            update_sync_run_summary(cur, run_id, summary)
            cur.execute(
                "UPDATE meeting_extract_runs SET summary_json=%s WHERE id=%s",
                (json.dumps(summary, ensure_ascii=False), extract_run_id),
            )
        return summary
    except Exception as exc:
        with db_cursor(commit=True) as (_, cur):
            set_sync_run_status(cur, run_id, "failed", summary, str(exc))
            cur.execute(
                "UPDATE meeting_extract_runs SET status='failed', finished_at=CURRENT_TIMESTAMP, summary_json=%s, error_text=%s WHERE id=%s",
                (json.dumps(summary, ensure_ascii=False), str(exc), extract_run_id),
            )
        raise


def launch_minutes_sync_in_background(recent_days: int | None = 365) -> None:
    def _runner() -> None:
        try:
            execute_minutes_sync(recent_days=recent_days)
        except Exception:
            app.logger.exception("Background meeting minutes sync failed")

    thread = threading.Thread(
        target=_runner,
        name="mine-city-reiki-minutes-sync",
        daemon=True,
    )
    with SYNC_THREAD_LOCK:
        thread.start()


def execute_minutes_retag(batch_size: int = 25) -> dict[str, Any]:
    with db_cursor() as (_, cur):
        cur.execute(
            """
            SELECT id
            FROM meeting_days
            WHERE extraction_status='success'
            ORDER BY meeting_date ASC, id ASC
            """
        )
        day_ids = [int(row["id"]) for row in (cur.fetchall() or [])]

    with db_cursor(commit=True) as (_, cur):
        summary: dict[str, Any] = {
            "operation": "minutes-retag",
            "engineVersion": SPEAKER_ENGINE_VERSION,
            "batchSize": batch_size,
            "days": len(day_ids),
            "processedDays": 0,
            "utterances": 0,
            "updated": 0,
            "roleChanged": 0,
            "indexed": 0,
            "rosterProfiles": 0,
            "speakerProfiles": 0,
            "rebuiltYears": 0,
            "progressCurrent": 0,
            "progressTotal": max(1, len(day_ids)),
            "progressLabel": "会議録の再タグ付けを開始します",
        }
        cur.execute(
            "INSERT INTO sync_runs (run_type, status, started_at, summary_json) VALUES ('manual','running',%s,%s)",
            (now_iso(), json.dumps(summary, ensure_ascii=False)),
        )
        run_id = int(cur.lastrowid)

    years_to_rebuild: set[int] = set()
    try:
        if not day_ids:
            with db_cursor(commit=True) as (_, cur):
                summary["progressLabel"] = "再タグ付け対象の会議録はありません"
                set_sync_run_status(cur, run_id, "success", summary, None)
            return summary

        for start in range(0, len(day_ids), batch_size):
            batch = day_ids[start:start + batch_size]
            with db_cursor(commit=True) as (_, cur):
                summary["progressLabel"] = f"会議録再タグ付け {start + 1}-{start + len(batch)} / {len(day_ids)}"
                update_sync_run_summary(cur, run_id, summary)
                for day_id in batch:
                    counts = retag_meeting_day_utterances(cur, day_id)
                    summary["processedDays"] += 1
                    summary["utterances"] += int(counts.get("processed") or 0)
                    summary["updated"] += int(counts.get("updated") or 0)
                    summary["roleChanged"] += int(counts.get("roleChanged") or 0)
                    summary["indexed"] += int(counts.get("indexed") or 0)
                    summary["rosterProfiles"] += int(counts.get("rosterProfiles") or 0)
                    summary["speakerProfiles"] += int(counts.get("speakerProfiles") or 0)
                    fiscal_year = counts.get("fiscalYear")
                    if fiscal_year and counts.get("dictionaryChanged"):
                        years_to_rebuild.add(int(fiscal_year))
                summary["progressCurrent"] = min(summary["processedDays"], len(day_ids))
                update_sync_run_summary(cur, run_id, summary)

        sorted_years = sorted(years_to_rebuild)
        for index, fiscal_year in enumerate(sorted_years, start=1):
            with db_cursor(commit=True) as (_, cur):
                summary["progressLabel"] = f"発言者辞書を再構築しています {index}/{len(sorted_years)}"
                update_sync_run_summary(cur, run_id, summary)
                rebuild_meeting_speaker_dictionary_for_year(cur, fiscal_year)
                summary["rebuiltYears"] = index
                update_sync_run_summary(cur, run_id, summary)

        with db_cursor(commit=True) as (_, cur):
            summary["progressCurrent"] = len(day_ids)
            summary["progressLabel"] = "会議録の再タグ付けが完了しました"
            bump_cache_generation(cur)
            prune_expired_caches(cur)
            set_sync_run_status(cur, run_id, "success", summary, None)
        return summary
    except Exception as exc:
        with db_cursor(commit=True) as (_, cur):
            set_sync_run_status(cur, run_id, "failed", summary, str(exc))
        raise


def launch_minutes_retag_in_background(batch_size: int = 25) -> None:
    def _runner() -> None:
        try:
            execute_minutes_retag(batch_size=batch_size)
        except Exception:
            app.logger.exception("Background meeting minutes retag failed")

    thread = threading.Thread(
        target=_runner,
        name="mine-city-reiki-minutes-retag",
        daemon=True,
    )
    with SYNC_THREAD_LOCK:
        thread.start()


def serialize_minutes_exchange(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": int(row["id"]),
            "order": int(row["utterance_order"]),
            "speakerName": row.get("speaker_name") or "",
            "speakerTitle": row.get("speaker_title") or "",
            "speakerRole": row.get("speaker_role") or "unknown",
            "speechType": row.get("speech_type") or "statement",
            "text": row.get("text") or "",
            "pageStart": int(row.get("page_start") or 0),
            "pageEnd": int(row.get("page_end") or 0),
            "positionTopStart": float(row.get("position_top_start") or 0),
            "positionTopEnd": float(row.get("position_top_end") or 0),
        }
        for row in rows
    ]


AGENDA_TABLE_TOKENS = (
    "議案",
    "承認",
    "認定",
    "報告",
    "同意",
    "諮問",
    "請願",
    "陳情",
    "補正予算",
    "当初予算",
    "決算",
    "条例",
    "契約",
    "指定管理",
    "専決処分",
)


def render_minutes_table_html(rows: list[list[str]], has_header: bool = True) -> str:
    if not rows:
        return ""
    parts = ["<table>"]
    for row_index, table_row in enumerate(rows):
        tag = "th" if has_header and row_index == 0 else "td"
        parts.append("<tr>")
        for cell in table_row:
            parts.append(f"<{tag}>{html_lib.escape(str(cell or ''))}</{tag}>")
        parts.append("</tr>")
    parts.append("</table>")
    return "".join(parts)


def normalize_minutes_table_for_display(caption: str, rows: list[Any], table_html: str, search_text: str) -> tuple[str, list[Any], str, str]:
    if not rows or not isinstance(rows[0], list):
        return caption, rows, table_html, search_text
    header = [str(cell or "").strip() for cell in rows[0]]
    if not caption.startswith("一般質問者名簿") or header[:2] != ["番号", "氏名"]:
        return caption, rows, table_html, search_text
    body = rows[1:]
    agenda_like = 0
    for row in body:
        if not isinstance(row, list) or len(row) < 2:
            continue
        title = str(row[1] or "")
        if len(re.sub(r"\s+", "", title)) >= 10 or any(token in title for token in AGENDA_TABLE_TOKENS):
            agenda_like += 1
    if agenda_like < max(2, len(body) // 2):
        return caption, rows, table_html, search_text
    fixed_rows = [list(row) for row in rows]
    fixed_rows[0] = list(fixed_rows[0])
    fixed_rows[0][1] = "件名"
    fixed_caption = caption.replace("一般質問者名簿", "付議事件一覧", 1)
    fixed_html = render_minutes_table_html([[str(cell or "") for cell in row] for row in fixed_rows], has_header=True)
    fixed_search = "\n".join([fixed_caption, *[" ".join(str(cell or "") for cell in row if str(cell or "")) for row in fixed_rows]])
    return fixed_caption, fixed_rows, fixed_html, fixed_search


def serialize_minutes_table(row: dict[str, Any]) -> dict[str, Any]:
    rows = json.loads(row.get("rows_json") or "[]")
    caption, rows, table_html, search_text = normalize_minutes_table_for_display(
        row.get("caption") or "",
        rows,
        row.get("html") or "",
        row.get("search_text") or "",
    )
    return {
        "id": int(row["id"]),
        "tableKey": row.get("table_key") or "",
        "page": int(row.get("page") or 0),
        "positionTop": float(row.get("position_top") or 0),
        "positionBottom": float(row.get("position_bottom") or 0),
        "caption": caption,
        "rows": rows,
        "html": table_html,
        "searchText": search_text,
        "confidence": float(row.get("confidence") or 0),
    }


def serialize_minutes_content_items(utterances: list[dict[str, Any]], tables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in serialize_minutes_exchange(utterances):
        items.append(
            {
                "type": "utterance",
                "sortPage": item["pageStart"],
                "sortTop": item["positionTopStart"],
                "utterance": item,
            }
        )
    for row in tables:
        table = serialize_minutes_table(row)
        items.append(
            {
                "type": "table",
                "sortPage": table["page"],
                "sortTop": table["positionTop"],
                "table": table,
            }
        )
    items.sort(key=lambda item: (int(item.get("sortPage") or 0), float(item.get("sortTop") or 0), 0 if item.get("type") == "table" else 1))
    for item in items:
        item.pop("sortPage", None)
        item.pop("sortTop", None)
    return items


MINUTES_COMPILE_ENGINE_VERSION = f"minutes-compile:{APP_VERSION}:{SPEAKER_ENGINE_VERSION}:{TABLE_ENGINE_VERSION}:meili-v1"


def active_minutes_compile_version_id(cur) -> int | None:
    cur.execute(
        """
        SELECT id
        FROM meeting_compile_versions
        WHERE is_active=1 AND status='success'
        ORDER BY activated_at DESC, id DESC
        LIMIT 1
        """
    )
    row = cur.fetchone()
    return int(row["id"]) if row else None


def latest_minutes_compile_status(cur) -> dict[str, Any] | None:
    cur.execute(
        """
        SELECT id, version_key, status, is_active, started_at, finished_at, activated_at, summary_json, error_text
        FROM meeting_compile_versions
        ORDER BY id DESC
        LIMIT 1
        """
    )
    row = cur.fetchone()
    if not row:
        return None
    return {
        "id": int(row["id"]),
        "versionKey": row.get("version_key") or "",
        "status": row.get("status") or "",
        "isActive": bool(row.get("is_active")),
        "startedAt": str(row["started_at"]) if row.get("started_at") else None,
        "finishedAt": str(row["finished_at"]) if row.get("finished_at") else None,
        "activatedAt": str(row["activated_at"]) if row.get("activated_at") else None,
        "summary": json.loads(row.get("summary_json") or "{}"),
        "errorText": row.get("error_text"),
    }


def compiled_minutes_day_detail(cur, day_id: int) -> dict[str, Any] | None:
    version_id = active_minutes_compile_version_id(cur)
    if not version_id:
        return None
    cur.execute(
        """
        SELECT detail_json
        FROM meeting_compiled_days
        WHERE version_id=%s AND day_id=%s
        """,
        (version_id, day_id),
    )
    row = cur.fetchone()
    if not row:
        return None
    return json.loads(row.get("detail_json") or "{}")


def compiled_minutes_days_for_meeting(cur, meeting_id: int) -> dict[int, dict[str, Any]]:
    version_id = active_minutes_compile_version_id(cur)
    if not version_id:
        return {}
    cur.execute(
        """
        SELECT day_id, detail_json
        FROM meeting_compiled_days
        WHERE version_id=%s AND session_id=%s
        """,
        (version_id, meeting_id),
    )
    compiled: dict[int, dict[str, Any]] = {}
    for row in cur.fetchall() or []:
        compiled[int(row["day_id"])] = json.loads(row.get("detail_json") or "{}")
    return compiled


def build_minutes_day_detail_payload(day: dict[str, Any], utterance_rows: list[dict[str, Any]], table_rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": int(day["id"]),
        "meetingDate": str(day["meeting_date"]) if day.get("meeting_date") else None,
        "section": day.get("section") or "",
        "meetingName": day.get("meeting_name") or "",
        "title": day.get("title") or "",
        "pdfUrl": day.get("pdf_url") or "",
        "pageUrl": day.get("page_url") or "",
        "pageCount": int(day.get("page_count") or 0),
        "utterances": serialize_minutes_exchange(utterance_rows),
        "tables": [serialize_minutes_table(row) for row in table_rows],
        "contentItems": serialize_minutes_content_items(utterance_rows, table_rows),
    }


def update_minutes_compile_run_summary(run_id: int, version_id: int, summary: dict[str, Any]) -> None:
    payload = json.dumps(summary, ensure_ascii=False)
    with db_cursor(commit=True) as (_, cur):
        update_sync_run_summary(cur, run_id, summary)
        cur.execute("UPDATE meeting_compile_versions SET summary_json=%s WHERE id=%s", (payload, version_id))


def prune_old_minutes_compile_versions(cur, keep_success: int = 2) -> int:
    cur.execute(
        """
        SELECT id
        FROM meeting_compile_versions
        WHERE status='success'
        ORDER BY is_active DESC, activated_at DESC, id DESC
        """
    )
    success_ids = [int(row["id"]) for row in (cur.fetchall() or [])]
    keep_ids = set(success_ids[:keep_success])
    if not keep_ids:
        return 0
    placeholders = ",".join(["%s"] * len(keep_ids))
    cur.execute(
        f"""
        DELETE FROM meeting_compile_versions
        WHERE status='success' AND id NOT IN ({placeholders})
        """,
        tuple(keep_ids),
    )
    return int(cur.rowcount or 0)


def execute_minutes_compile(trigger: str = "manual") -> dict[str, Any]:
    version_key = datetime.now().strftime("v%Y%m%d%H%M%S%f")
    with db_cursor(commit=True) as (_, cur):
        cur.execute("SELECT COUNT(*) AS cnt FROM meeting_days WHERE extraction_status='success'")
        day_total = int((cur.fetchone() or {}).get("cnt") or 0)
        cur.execute("SELECT COUNT(*) AS cnt FROM meeting_utterances")
        utterance_total = int((cur.fetchone() or {}).get("cnt") or 0)
        cur.execute("SELECT COUNT(*) AS cnt FROM meeting_tables")
        table_total = int((cur.fetchone() or {}).get("cnt") or 0)
        summary: dict[str, Any] = {
            "operation": "minutes-compile",
            "trigger": trigger,
            "versionKey": version_key,
            "engineVersion": MINUTES_COMPILE_ENGINE_VERSION,
            "days": day_total,
            "utterances": utterance_total,
            "tables": table_total,
            "compiledDays": 0,
            "compiledUtterances": 0,
            "compiledTables": 0,
            "prunedVersions": 0,
            "progressCurrent": 0,
            "progressTotal": max(1, day_total + 2),
            "progressLabel": "会議録コンパイルを開始します",
        }
        cur.execute(
            "INSERT INTO sync_runs (run_type, status, started_at, summary_json) VALUES ('manual','running',%s,%s)",
            (now_iso(), json.dumps(summary, ensure_ascii=False)),
        )
        run_id = int(cur.lastrowid)
        cur.execute(
            """
            INSERT INTO meeting_compile_versions (version_key, status, is_active, summary_json, engine_versions)
            VALUES (%s,'running',0,%s,%s)
            """,
            (version_key, json.dumps(summary, ensure_ascii=False), MINUTES_COMPILE_ENGINE_VERSION),
        )
        version_id = int(cur.lastrowid)

    try:
        with db_cursor(commit=True) as (_, cur):
            summary["progressCurrent"] = 1
            summary["progressLabel"] = "発言検索用の軽量テーブルを作成しています"
            update_sync_run_summary(cur, run_id, summary)
            cur.execute("UPDATE meeting_compile_versions SET summary_json=%s WHERE id=%s", (json.dumps(summary, ensure_ascii=False), version_id))
            cur.execute(
                """
                INSERT INTO meeting_compiled_utterances
                  (version_id, utterance_id, day_id, session_id, meeting_date, section, meeting_name, day_title, pdf_url, page_url,
                   utterance_order, speaker_name, speaker_title, speaker_role, speaker_group, speech_type,
                   page_start, page_end, position_top_start, position_top_end, text_preview, display_text, body_search_text, search_text)
                SELECT
                  %s, u.id, u.day_id, d.session_id, d.meeting_date, s.section, s.meeting_name, d.title, d.pdf_url, d.page_url,
                  u.utterance_order, u.speaker_name, u.speaker_title, u.speaker_role, u.speaker_group, u.speech_type,
                  u.page_start, u.page_end, u.position_top_start, u.position_top_end,
                  SUBSTRING(u.text, 1, %s),
                  CONCAT_WS(' ', NULLIF(u.speaker_title, ''), NULLIF(u.speaker_name, ''), u.text),
                  u.text,
                  u.search_text
                FROM meeting_utterances u
                JOIN meeting_days d ON d.id=u.day_id
                JOIN meeting_sessions s ON s.id=d.session_id
                WHERE d.extraction_status='success'
                ORDER BY d.meeting_date DESC, u.day_id ASC, u.utterance_order ASC
                """,
                (version_id, MINUTES_SEARCH_INDEX_PREVIEW_CHARS),
            )
            summary["compiledUtterances"] = int(cur.rowcount or 0)

        with db_cursor() as (_, cur):
            cur.execute(
                """
                SELECT d.id, d.session_id, d.meeting_date, d.title, d.pdf_url, d.page_url, d.page_count,
                       s.section, s.meeting_name
                FROM meeting_days d
                JOIN meeting_sessions s ON s.id=d.session_id
                WHERE d.extraction_status='success'
                ORDER BY d.meeting_date ASC, d.id ASC
                """
            )
            day_rows = cur.fetchall() or []

        for index, day in enumerate(day_rows, start=1):
            day_id = int(day["id"])
            with db_cursor(commit=True) as (_, cur):
                cur.execute(
                    """
                    SELECT id, utterance_order, speaker_name, speaker_title, speaker_role, speech_type, text,
                           page_start, page_end, position_top_start, position_top_end
                    FROM meeting_utterances
                    WHERE day_id=%s
                    ORDER BY utterance_order ASC
                    """,
                    (day_id,),
                )
                utterance_rows = cur.fetchall() or []
                cur.execute(
                    """
                    SELECT id, table_key, page, position_top, position_bottom, caption, rows_json, html, search_text, confidence
                    FROM meeting_tables
                    WHERE day_id=%s
                    ORDER BY page ASC, position_top ASC, id ASC
                    """,
                    (day_id,),
                )
                table_rows = cur.fetchall() or []
                detail_payload = build_minutes_day_detail_payload(day, utterance_rows, table_rows)
                detail_json = json.dumps(detail_payload, ensure_ascii=False, separators=(",", ":"))
                content_hash = hashlib.sha256(detail_json.encode("utf-8")).hexdigest()
                cur.execute(
                    """
                    INSERT INTO meeting_compiled_days
                      (version_id, day_id, session_id, meeting_date, section, meeting_name, title,
                       utterance_count, table_count, detail_json, content_hash)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        version_id,
                        day_id,
                        int(day["session_id"]),
                        day.get("meeting_date"),
                        day.get("section") or "",
                        day.get("meeting_name") or "",
                        day.get("title") or "",
                        len(utterance_rows),
                        len(table_rows),
                        detail_json,
                        content_hash,
                    ),
                )
                summary["compiledDays"] = index
                summary["compiledTables"] += len(table_rows)
                summary["progressCurrent"] = min(summary["progressTotal"], index + 1)
                summary["progressLabel"] = f"閲覧用JSONを作成しています {index}/{len(day_rows)}"
                update_sync_run_summary(cur, run_id, summary)
                cur.execute("UPDATE meeting_compile_versions SET summary_json=%s WHERE id=%s", (json.dumps(summary, ensure_ascii=False), version_id))

        if meili_is_enabled():
            try:
                with db_cursor(commit=True) as (_, cur):
                    summary["progressLabel"] = "会議録検索インデックスを作成しています"
                    update_sync_run_summary(cur, run_id, summary)
                    cur.execute("UPDATE meeting_compile_versions SET summary_json=%s WHERE id=%s", (json.dumps(summary, ensure_ascii=False), version_id))
                indexed = index_meili_minutes_compile(version_id)
                summary["meiliMinutesIndexed"] = indexed
            except Exception as exc:
                # The compiled MySQL generation remains a complete fallback. Do not make
                # a transient search-engine failure take the minutes reader offline.
                summary["meiliMinutesError"] = str(exc)
                app.logger.exception("Meeting minutes Meilisearch indexing failed; MySQL fallback remains active")

        with db_cursor(commit=True) as (_, cur):
            summary["progressCurrent"] = summary["progressTotal"]
            summary["progressLabel"] = "会議録コンパイルを有効化しています"
            cur.execute("UPDATE meeting_compile_versions SET is_active=0 WHERE is_active=1")
            cur.execute(
                """
                UPDATE meeting_compile_versions
                SET status='success', is_active=1, finished_at=CURRENT_TIMESTAMP, activated_at=CURRENT_TIMESTAMP,
                    summary_json=%s
                WHERE id=%s
                """,
                (json.dumps(summary, ensure_ascii=False), version_id),
            )
            summary["prunedVersions"] = prune_old_minutes_compile_versions(cur, keep_success=2)
            summary["progressLabel"] = "会議録コンパイルが完了しました"
            bump_cache_generation(cur)
            prune_expired_caches(cur)
            set_sync_run_status(cur, run_id, "success", summary, None)
            cur.execute("UPDATE meeting_compile_versions SET summary_json=%s WHERE id=%s", (json.dumps(summary, ensure_ascii=False), version_id))
        return summary
    except Exception as exc:
        with db_cursor(commit=True) as (_, cur):
            summary["progressLabel"] = "会議録コンパイルに失敗しました"
            set_sync_run_status(cur, run_id, "failed", summary, str(exc))
            cur.execute(
                """
                UPDATE meeting_compile_versions
                SET status='failed', finished_at=CURRENT_TIMESTAMP, summary_json=%s, error_text=%s
                WHERE id=%s
                """,
                (json.dumps(summary, ensure_ascii=False), str(exc), version_id),
            )
        raise


def launch_minutes_compile_in_background(trigger: str = "manual") -> None:
    def _runner() -> None:
        try:
            execute_minutes_compile(trigger=trigger)
        except Exception:
            app.logger.exception("Background meeting minutes compile failed")

    thread = threading.Thread(
        target=_runner,
        name="mine-city-reiki-minutes-compile",
        daemon=True,
    )
    with SYNC_THREAD_LOCK:
        thread.start()


def minutes_snippet(text: str, keywords: list[str]) -> str:
    snippet = text_snippet(text, keywords)
    return snippet if snippet else normalize_text(text)[:180]


def minutes_preview_anchor(terms: list[str], query: str) -> str:
    for term in terms:
        value = normalize_text(term)
        if value:
            return value[:80]
    return normalize_text(query)[:80]


def minutes_preview_terms(terms: list[str], query: str, limit: int = 8) -> list[str]:
    values = [normalize_text(term) for term in terms if normalize_text(term)]
    if not values:
        values = [normalize_text(query)] if normalize_text(query) else []
    return _dedupe_terms(values, limit=limit)


def minutes_hit_preview_select(text_column: str, terms: list[str], query: str) -> tuple[str, list[Any]]:
    preview_terms = minutes_preview_terms(terms, query)
    if not preview_terms:
        return f"SUBSTRING({text_column}, 1, %s) AS text", [MINUTES_SEARCH_PREVIEW_CHARS]

    cases: list[str] = []
    params: list[Any] = []
    for term in preview_terms:
        cases.append(
            f"WHEN LOCATE(%s, {text_column}) > 0 "
            f"THEN SUBSTRING({text_column}, GREATEST(1, LOCATE(%s, {text_column}) - %s), %s)"
        )
        params.extend([term, term, MINUTES_SEARCH_PREVIEW_BACKTRACK, MINUTES_SEARCH_PREVIEW_CHARS])
    sql = "CASE " + " ".join(cases) + f" ELSE SUBSTRING({text_column}, 1, %s) END AS text"
    params.append(MINUTES_SEARCH_PREVIEW_CHARS)
    return sql, params


def minutes_hit_scope_select(body_column: str, terms: list[str], query: str, include_speaker_meta: bool) -> tuple[str, list[Any]]:
    if not include_speaker_meta:
        return "'body' AS hit_scope", []
    preview_terms = minutes_preview_terms(terms, query, limit=16)
    if not preview_terms:
        return "'body' AS hit_scope", []
    body_conditions: list[str] = []
    params: list[Any] = []
    for term in preview_terms:
        body_conditions.append(f"LOCATE(%s, {body_column}) > 0")
        params.append(term)
    return "CASE WHEN " + " OR ".join(body_conditions) + " THEN 'body' ELSE 'speaker' END AS hit_scope", params


def minutes_match_score_select(search_column: str, weighted_terms: list[tuple[str, int]]) -> tuple[str, list[Any]]:
    """Score related terms after FULLTEXT narrows the candidate set."""
    if not weighted_terms:
        return "0 AS match_score", []
    cases: list[str] = []
    params: list[Any] = []
    for term, score in weighted_terms[:16]:
        normalized = normalize_text(term)
        if not normalized:
            continue
        cases.append(f"CASE WHEN LOCATE(%s, {search_column}) > 0 THEN %s ELSE 0 END")
        params.extend([normalized, int(score)])
    if not cases:
        return "0 AS match_score", []
    return "(" + " + ".join(cases) + ") AS match_score", params


MINUTES_SEARCH_PREVIEW_CHARS = 260
MINUTES_SEARCH_PREVIEW_BACKTRACK = 55
MINUTES_SEARCH_SNIPPET_CHARS = 180


def encode_minutes_cursor(row: dict[str, Any]) -> str:
    payload = {
        "d": str(row.get("meeting_date") or row.get("meetingDate") or ""),
        "day": int(row.get("day_id") or row.get("dayId") or 0),
        "o": int(row.get("utterance_order") or row.get("order") or 0),
        "u": int(row.get("utterance_id") or row.get("id") or 0),
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_minutes_cursor(value: str) -> dict[str, Any] | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        decoded = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8")
        payload = json.loads(decoded)
        meeting_date = str(payload.get("d") or "")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", meeting_date):
            return None
        return {
            "meeting_date": meeting_date,
            "day_id": max(0, int(payload.get("day") or 0)),
            "utterance_order": max(0, int(payload.get("o") or 0)),
            "utterance_id": max(0, int(payload.get("u") or 0)),
        }
    except (TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def minutes_cursor_filter(
    cursor: dict[str, Any] | None,
    use_search_index: bool,
) -> tuple[str, list[Any]]:
    if not cursor:
        return "", []
    date_column = "u.meeting_date" if use_search_index else "d.meeting_date"
    day_column = "u.day_id" if use_search_index else "u.day_id"
    id_column = "u.utterance_id" if use_search_index else "u.id"
    return (
        f"""(
          {date_column} < %s
          OR ({date_column}=%s AND {day_column}>%s)
          OR ({date_column}=%s AND {day_column}=%s AND u.utterance_order>%s)
          OR ({date_column}=%s AND {day_column}=%s AND u.utterance_order=%s AND {id_column}>%s)
        )""",
        [
            cursor["meeting_date"],
            cursor["meeting_date"], cursor["day_id"],
            cursor["meeting_date"], cursor["day_id"], cursor["utterance_order"],
            cursor["meeting_date"], cursor["day_id"], cursor["utterance_order"], cursor["utterance_id"],
        ],
    )


EXACT_EXECUTIVE_TITLE_FILTERS = {
    "市長",
    "副市長",
    "教育長",
    "病院事業管理者",
    "代表監査委員",
    "会計管理者",
    "消防長",
}


def append_minutes_role_filter(
    conditions: list[str],
    params: list[Any],
    role: str,
    role_column: str,
    title_column: str,
) -> None:
    if not role or role == "all":
        return
    if role.startswith("title:"):
        title = role.removeprefix("title:").strip()
        if not title:
            return
        if title in EXACT_EXECUTIVE_TITLE_FILTERS:
            conditions.append(f"({role_column}='answerer' AND {title_column}=%s)")
            params.append(title)
        else:
            conditions.append(f"({role_column}='answerer' AND {title_column} LIKE %s)")
            params.append(f"%{title}")
        return
    conditions.append(f"{role_column}=%s")
    params.append(role)


def minutes_boolean_query(terms: list[str], fallback: str, require_all: bool = True) -> str:
    tokens = terms or ([normalize_text(fallback)] if normalize_text(fallback) else [])
    safe_tokens: list[str] = []
    for token in tokens:
        value = re.sub(r'[+\-<>()~*"@]+', " ", token).strip()
        if len(value) >= 2:
            prefix = "+" if require_all else ""
            safe_tokens.append(f"{prefix}{value}*")
    return " ".join(safe_tokens)


def append_minutes_query_filter(
    conditions: list[str],
    params: list[Any],
    query: str,
    terms: list[str],
    match_mode: str,
    op: str,
    use_fulltext: bool,
    search_column: str = "u.search_text",
) -> None:
    normalized_query = normalize_text(query)
    if not normalized_query:
        return
    if use_fulltext:
        boolean_query = minutes_boolean_query(terms, normalized_query, require_all=match_mode == "exact" and op != "OR")
        if not boolean_query:
            return
        conditions.append(
            f"MATCH({search_column}) AGAINST (%s IN BOOLEAN MODE)"
        )
        params.append(boolean_query)
        # MySQL FULLTEXT/ngram can return broad Japanese candidates for
        # katakana and short terms. Keep FULLTEXT as the fast candidate source,
        # but require at least one expanded term to exist in the actual text.
        presence_terms = _dedupe_terms(terms, limit=16)
        if presence_terms:
            presence_conditions: list[str] = []
            for term in presence_terms:
                presence_conditions.append(f"{search_column} LIKE %s")
                params.append(f"%{term}%")
            presence_joiner = " AND " if match_mode == "exact" and op != "OR" else " OR "
            conditions.append("(" + presence_joiner.join(presence_conditions) + ")")
        return

    if match_mode == "exact":
        exact_terms = _dedupe_terms(terms or [normalized_query], limit=16)
        if not exact_terms:
            return
        term_conditions: list[str] = []
        for term in exact_terms:
            term_conditions.append(f"{search_column} LIKE %s")
            params.append(f"%{term}%")
        joiner = " OR " if op == "OR" else " AND "
        conditions.append("(" + joiner.join(term_conditions) + ")")
        return

    if not terms:
        return
    term_conditions: list[str] = []
    for term in terms:
        like = f"%{term}%"
        term_conditions.append(f"{search_column} LIKE %s")
        params.append(like)
    joiner = " OR " if op == "OR" or match_mode == "related" else " AND "
    conditions.append("(" + joiner.join(term_conditions) + ")")


def build_minutes_where(
    query: str,
    terms: list[str],
    speaker: str,
    role: str,
    section: str,
    from_date: str,
    to_date: str,
    years: list[int] | None,
    meeting_id: int | None,
    day_id: int | None,
    match_mode: str,
    op: str,
    use_fulltext: bool,
    speaker_exact_only: bool = True,
    use_search_index: bool = False,
    search_column: str = "u.search_text",
) -> tuple[str, list[Any]]:
    conditions = ["1=1"]
    params: list[Any] = []
    append_minutes_query_filter(conditions, params, query, terms, match_mode, op, use_fulltext, search_column)
    if speaker:
        if speaker_exact_only:
            conditions.append("u.speaker_name=%s")
            params.append(speaker)
        else:
            conditions.append("(u.speaker_name=%s OR u.speaker_title=%s OR u.speaker_name LIKE %s OR u.speaker_title LIKE %s)")
            params.extend([speaker, speaker, f"%{speaker}%", f"%{speaker}%"])
    append_minutes_role_filter(conditions, params, role, "u.speaker_role", "u.speaker_title")
    if section and section != "all":
        conditions.append(("u.section" if use_search_index else "s.section") + "=%s")
        params.append(section)
    if meeting_id:
        conditions.append(("u.session_id" if use_search_index else "s.id") + "=%s")
        params.append(meeting_id)
    if day_id:
        conditions.append("u.day_id=%s" if use_search_index else "d.id=%s")
        params.append(day_id)
    if years:
        year_ranges = []
        date_column = "u.meeting_date" if use_search_index else "d.meeting_date"
        for year in years:
            year_ranges.append(f"({date_column} >= %s AND {date_column} <= %s)")
            params.extend([f"{year}-01-01", f"{year}-12-31"])
        conditions.append(f"({' OR '.join(year_ranges)})")
    if from_date:
        conditions.append(("u.meeting_date" if use_search_index else "d.meeting_date") + " >= %s")
        params.append(from_date)
    if to_date:
        conditions.append(("u.meeting_date" if use_search_index else "d.meeting_date") + " <= %s")
        params.append(to_date)
    return " AND ".join(conditions), params


def parse_minutes_years(raw: str) -> list[int]:
    years: list[int] = []
    for part in re.split(r"[,\s]+", raw or ""):
        if not part:
            continue
        try:
            year = int(part)
        except ValueError:
            continue
        if 1900 <= year <= 2100 and year not in years:
            years.append(year)
    return years


def fetch_minutes_exchange_windows(cur, rows: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    if not rows:
        return {}
    windows: dict[int, tuple[int, int]] = {}
    for row in rows:
        day_id = int(row["day_id"])
        order = int(row["utterance_order"])
        # 発言集では短い質問が連続した後に答弁が続くことがあるため、
        # 関連発言の取得範囲は閲覧用の数発言より広めに確保する。
        start = max(1, order - 4)
        end = order + 24
        if day_id in windows:
            prev_start, prev_end = windows[day_id]
            windows[day_id] = (min(prev_start, start), max(prev_end, end))
        else:
            windows[day_id] = (start, end)

    predicates: list[str] = []
    params: list[Any] = []
    for day_id, (start, end) in windows.items():
        predicates.append("(day_id=%s AND utterance_order BETWEEN %s AND %s)")
        params.extend([day_id, start, end])
    cur.execute(
        f"""
        SELECT id, day_id, utterance_order, speaker_name, speaker_title, speaker_role, speech_type, text,
               page_start, page_end, position_top_start, position_top_end
        FROM meeting_utterances
        WHERE {" OR ".join(predicates)}
        ORDER BY day_id ASC, utterance_order ASC
        """,
        tuple(params),
    )
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in cur.fetchall() or []:
        grouped.setdefault(int(row["day_id"]), []).append(row)
    return grouped


def meili_minutes_search_index(payload: dict[str, Any]) -> dict[str, Any]:
    return meili_request(
        "POST",
        f"/indexes/{urllib.parse.quote(CFG.meili_minutes_index)}/search",
        payload,
        timeout=5,
    )


def meili_minutes_filters(
    compile_version_id: int,
    speaker: str,
    role: str,
    section: str,
    from_date: str,
    to_date: str,
    years: list[int] | None,
    meeting_id: int | None,
    day_id: int | None,
) -> list[str] | None:
    if role.startswith("title:"):
        # Partial role matching remains on MySQL because Meilisearch filters are
        # exact by design. Returning None keeps behavior identical during rollout.
        return None
    filters = [f"compileVersionId = {int(compile_version_id)}"]
    if speaker:
        filters.append(f"speakerName = {json.dumps(speaker, ensure_ascii=False)}")
    if role and role != "all":
        filters.append(f"speakerRole = {json.dumps(role, ensure_ascii=False)}")
    if section and section != "all":
        filters.append(f"section = {json.dumps(section, ensure_ascii=False)}")
    if meeting_id:
        filters.append(f"sessionId = {int(meeting_id)}")
    if day_id:
        filters.append(f"dayId = {int(day_id)}")
    if years:
        filters.append("calendarYear IN [" + ",".join(str(int(year)) for year in years) + "]")
    if from_date:
        filters.append(f"meetingDate >= {json.dumps(from_date)}")
    if to_date:
        filters.append(f"meetingDate <= {json.dumps(to_date)}")
    return filters


def meili_minutes_hit_text(hit: dict[str, Any], include_speaker_meta: bool) -> str:
    values = [str(hit.get("bodyPlain") or "")]
    if include_speaker_meta:
        values.extend([str(hit.get("speakerTitle") or ""), str(hit.get("speakerName") or "")])
    return normalize_text(" ".join(values)).lower()


def meili_minutes_hit_matches_exact(hit: dict[str, Any], terms: list[str], include_speaker_meta: bool) -> bool:
    haystack = meili_minutes_hit_text(hit, include_speaker_meta)
    return all(normalize_text(term).lower() in haystack for term in terms if normalize_text(term))


def serialize_meili_minutes_hit(
    hit: dict[str, Any],
    base_terms: list[str],
    related_terms: list[str],
    include_speaker_meta: bool,
) -> dict[str, Any]:
    body = clean_link_marker_fragments(hit.get("bodyPlain") or hit.get("textPreview") or "")
    body_lower = normalize_text(body).lower()
    exact_terms = [term for term in base_terms if normalize_text(term).lower() in body_lower]
    related = [term for term in related_terms if normalize_text(term).lower() in body_lower]
    hit_scope = "body" if exact_terms or related or not include_speaker_meta else "speaker"
    return {
        "id": int(hit.get("utteranceId") or 0),
        "dayId": int(hit.get("dayId") or 0),
        "meetingDate": hit.get("meetingDate") or None,
        "section": hit.get("section") or "",
        "meetingName": hit.get("meetingName") or "",
        "dayTitle": hit.get("dayTitle") or "",
        "pdfUrl": hit.get("pdfUrl") or "",
        "pageUrl": hit.get("pageUrl") or "",
        "speakerName": hit.get("speakerName") or "",
        "speakerTitle": hit.get("speakerTitle") or "",
        "speakerRole": hit.get("speakerRole") or "unknown",
        "speechType": hit.get("speechType") or "statement",
        "order": int(hit.get("utteranceOrder") or 0),
        "pageStart": int(hit.get("pageStart") or 0),
        "pageEnd": int(hit.get("pageEnd") or 0),
        "positionTopStart": float(hit.get("positionTopStart") or 0),
        "positionTopEnd": float(hit.get("positionTopEnd") or 0),
        "snippet": minutes_snippet(body, [*base_terms, *related_terms]),
        "text": body,
        "exchange": [],
        "highlightTerms": base_terms,
        "relatedHighlightTerms": related_terms,
        "hitScope": hit_scope,
    }


def search_minutes_meili_items(
    compile_version_id: int,
    query: str,
    base_terms: list[str],
    weighted_terms: list[tuple[str, int]],
    speaker: str,
    role: str,
    section: str,
    from_date: str,
    to_date: str,
    years: list[int] | None,
    meeting_id: int | None,
    day_id: int | None,
    match_mode: str,
    include_speaker_meta: bool,
    limit: int,
) -> list[dict[str, Any]] | None:
    if not meili_is_enabled() or not query:
        return None
    filters = meili_minutes_filters(
        compile_version_id, speaker, role, section, from_date, to_date, years, meeting_id, day_id
    )
    if filters is None:
        return None
    search_terms = [term for term, _score in weighted_terms]
    related_terms = related_keywords_for_highlight(base_terms, search_terms) if match_mode == "related" else []
    attributes = ["bodyKeyText"]
    if include_speaker_meta:
        attributes.append("speakerKeyText")
    candidate_limit = min(800, max(limit * 4, 120))
    key_query = build_meili_query_key_text(search_terms)
    if not key_query:
        return None
    payload: dict[str, Any] = {
        "q": key_query,
        "limit": candidate_limit,
        "matchingStrategy": "all" if match_mode == "exact" else "last",
        "attributesToSearchOn": attributes,
        "filter": filters,
        "attributesToRetrieve": [
            "utteranceId", "dayId", "meetingDate", "section", "meetingName", "dayTitle", "pdfUrl", "pageUrl",
            "utteranceOrder", "speakerName", "speakerTitle", "speakerRole", "speechType", "pageStart", "pageEnd",
            "positionTopStart", "positionTopEnd", "textPreview", "bodyPlain",
        ],
    }
    result = meili_minutes_search_index(payload)
    hits = result.get("hits") or []
    if not hits and match_mode == "exact":
        # The key field is an accelerator. The plain-text fallback preserves
        # exact recall for unusually long terms outside its compact n-gram set.
        payload["q"] = " ".join(base_terms)
        payload["attributesToSearchOn"] = ["bodyPlain", "speakerSearchText"] if include_speaker_meta else ["bodyPlain"]
        result = meili_minutes_search_index(payload)
        hits = result.get("hits") or []
    if match_mode == "exact":
        hits = [hit for hit in hits if meili_minutes_hit_matches_exact(hit, base_terms, include_speaker_meta)]
    else:
        weights = {normalize_text(term).lower(): score for term, score in weighted_terms}
        scored_hits: list[tuple[int, dict[str, Any]]] = []
        for hit in hits:
            haystack = meili_minutes_hit_text(hit, include_speaker_meta)
            score = sum(weight for term, weight in weights.items() if term and term in haystack)
            if score:
                scored_hits.append((score, hit))
        # Stable passes retain chronological order inside the same relevance band.
        scored_hits.sort(key=lambda item: int(item[1].get("utteranceOrder") or 0))
        scored_hits.sort(key=lambda item: str(item[1].get("meetingDate") or ""), reverse=True)
        scored_hits.sort(key=lambda item: -item[0])
        hits = [hit for _score, hit in scored_hits]
    return [
        serialize_meili_minutes_hit(hit, base_terms, related_terms, include_speaker_meta)
        for hit in hits[:limit]
    ]


def search_minutes_items(
    query: str = "",
    speaker: str = "",
    role: str = "",
    section: str = "",
    from_date: str = "",
    to_date: str = "",
    years: list[int] | None = None,
    meeting_id: int | None = None,
    day_id: int | None = None,
    match_mode: str = "exact",
    op: str = "AND",
    limit: int | None = 20,
    context: str = "none",
    include_speaker_meta: bool = False,
    cursor: dict[str, Any] | None = None,
    prefer_meili: bool = True,
    stable_order: bool = False,
) -> list[dict[str, Any]]:
    base_terms = [normalize_text(part) for part in re.split(r"\s+", query or "") if normalize_text(part)]
    with db_cursor() as (_, cur):
        weighted_terms = expand_keywords_with_scores(base_terms, cur=cur, max_keywords=14, min_priority=5) if match_mode == "related" else [(term, 1000) for term in base_terms]
        terms = [term for term, _score in weighted_terms]
        related_terms = related_keywords_for_highlight(base_terms, terms) if match_mode == "related" else []
        generation = get_cache_generation(cur)
        cache_key = make_cache_key(
            [
                "minutes-search",
                normalize_text(query),
                speaker,
                role,
                section,
                str(from_date),
                str(to_date),
                ",".join(str(year) for year in (years or [])),
                str(meeting_id or ""),
                str(day_id or ""),
                match_mode,
                op,
                str(limit),
                context,
                "speaker-meta" if include_speaker_meta else "body-only",
                encode_minutes_cursor(cursor) if cursor else "",
                "stable-order" if stable_order else "relevance-order",
                "meili" if prefer_meili else "mysql",
                str(generation),
            ]
        )
        cached = get_local_cache(LOCAL_MINUTES_SEARCH_CACHE, cache_key)
        if cached is not None:
            return cached

        rows: list[dict[str, Any]] = []
        active_compile_version_id = active_minutes_compile_version_id(cur) if context != "wide" else None
        use_compiled_index = active_compile_version_id is not None
        use_search_index = context != "wide" and (use_compiled_index or is_meeting_search_index_ready(cur))
        search_index_table = "meeting_compiled_utterances" if use_compiled_index else "meeting_utterance_search_index"
        if prefer_meili and cursor is None and use_compiled_index and context != "wide" and limit is not None:
            try:
                meili_items = search_minutes_meili_items(
                    active_compile_version_id,
                    query,
                    base_terms,
                    weighted_terms,
                    speaker,
                    role,
                    section,
                    from_date,
                    to_date,
                    years,
                    meeting_id,
                    day_id,
                    match_mode,
                    include_speaker_meta,
                    limit,
                )
                if meili_items:
                    put_local_cache(LOCAL_MINUTES_SEARCH_CACHE, cache_key, meili_items)
                    return meili_items
            except Exception:
                app.logger.warning("Meeting minutes Meilisearch fallback to MySQL", exc_info=True)
        short_index_term = ""
        if len(base_terms) == 1:
            candidate_short_term = normalize_minutes_short_term(base_terms[0])
            expanded_terms = {normalize_text(term) for term in terms if normalize_text(term)}
            can_satisfy_with_short_index = match_mode == "exact" or expanded_terms <= {candidate_short_term}
            if (
                candidate_short_term
                and can_satisfy_with_short_index
            ):
                short_index_term = candidate_short_term
        use_fulltext_options = (
            [False]
            if short_index_term
            else [True, False]
            if normalize_text(query) and minutes_boolean_query(terms, query, require_all=match_mode != "related")
            else [False]
        )
        speaker_exact_options = [True, False] if speaker else [True]
        attempts = [(use_fulltext, speaker_exact_only) for use_fulltext in use_fulltext_options for speaker_exact_only in speaker_exact_options]
        seen_attempts: set[tuple[bool, bool]] = set()
        compact_results = context != "wide"
        if use_search_index:
            body_search_column = "COALESCE(NULLIF(u.body_search_text, ''), u.text_preview)"
            query_search_column = "u.search_text" if include_speaker_meta else "u.body_search_text"
            hit_scope_body_column = body_search_column
        else:
            body_search_column = "u.text"
            query_search_column = "u.search_text" if include_speaker_meta else "u.text"
            hit_scope_body_column = "u.text"
        preview_anchor = minutes_preview_anchor(terms if match_mode == "related" else base_terms, query)
        index_body_join_sql = ""
        if use_compiled_index and compact_results and preview_anchor:
            text_select, text_select_params = minutes_hit_preview_select(
                "COALESCE(NULLIF(u.body_search_text, ''), u.text_preview)",
                terms if match_mode == "related" else base_terms,
                query,
            )
        elif use_search_index and compact_results and preview_anchor:
            text_select, text_select_params = minutes_hit_preview_select(
                "body.text",
                terms if match_mode == "related" else base_terms,
                query,
            )
            index_body_join_sql = "JOIN meeting_utterances body ON body.id=u.utterance_id"
        elif use_search_index and compact_results:
            text_select = "u.text_preview AS text"
            text_select_params = []
        elif compact_results and preview_anchor:
            text_select, text_select_params = minutes_hit_preview_select(
                "u.text",
                terms if match_mode == "related" else base_terms,
                query,
            )
        elif compact_results:
            text_select = "SUBSTRING(u.text, 1, %s) AS text"
            text_select_params = [MINUTES_SEARCH_PREVIEW_CHARS]
        else:
            text_select = "u.text"
            text_select_params = []
        hit_scope_select, hit_scope_params = minutes_hit_scope_select(
            hit_scope_body_column,
            terms if match_mode == "related" else base_terms,
            query,
            include_speaker_meta,
        )
        match_score_select, match_score_params = minutes_match_score_select(
            query_search_column,
            weighted_terms if match_mode == "related" else [],
        )
        for use_fulltext, speaker_exact_only in attempts:
            if (use_fulltext, speaker_exact_only) in seen_attempts:
                continue
            seen_attempts.add((use_fulltext, speaker_exact_only))
            where, params = build_minutes_where(
                "" if short_index_term else query,
                [] if short_index_term else terms,
                speaker,
                role,
                section,
                from_date,
                to_date,
                years,
                meeting_id,
                day_id,
                match_mode,
                op,
                use_fulltext,
                speaker_exact_only=speaker_exact_only,
                use_search_index=use_search_index,
                search_column=query_search_column,
            )
            if short_index_term and not include_speaker_meta:
                # The short-term index includes metadata as well. Restrict its
                # already-small candidate set to the body for the default mode.
                where = f"{where} AND {body_search_column} LIKE %s"
                params.append(f"%{short_index_term}%")
            cursor_where, cursor_params = minutes_cursor_filter(cursor, use_search_index)
            if cursor_where:
                where = f"{where} AND {cursor_where}"
                params.extend(cursor_params)
            try:
                limit_clause = "LIMIT %s" if limit is not None else ""
                short_join_sql = ""
                short_join_params: list[Any] = []
                if short_index_term:
                    short_join_sql = "JOIN meeting_utterance_short_terms st ON st.utterance_id=u.id AND st.term=%s"
                    short_join_params.append(short_index_term)
                compiled_params = [active_compile_version_id] if use_compiled_index else []
                query_params = match_score_params + text_select_params + hit_scope_params + short_join_params + params + compiled_params + ([limit] if limit is not None else [])
                sort_sql = (
                    "match_score DESC, " if match_mode == "related" and not stable_order else ""
                ) + "u.meeting_date DESC, u.day_id ASC, u.utterance_order ASC, u.utterance_id ASC"
                if use_search_index:
                    if short_index_term:
                        short_join_sql = "JOIN meeting_utterance_short_terms st ON st.utterance_id=u.utterance_id AND st.term=%s"
                    compiled_where = " AND u.version_id=%s" if use_compiled_index else ""
                    cur.execute(
                        f"""
                        SELECT
                          u.utterance_id AS id, u.day_id, u.utterance_order, u.speaker_name, u.speaker_title,
                          u.speaker_role, u.speech_type, {match_score_select}, {text_select}, {hit_scope_select}, u.page_start, u.page_end,
                          u.position_top_start, u.position_top_end, u.meeting_date, u.day_title, u.pdf_url,
                          u.page_url, u.section, u.meeting_name, u.meeting_name AS session_title
                        FROM {search_index_table} u
                        {index_body_join_sql}
                        {short_join_sql}
                        WHERE {where}{compiled_where}
                        ORDER BY {sort_sql}
                        {limit_clause}
                        """,
                        tuple(query_params),
                    )
                else:
                    cur.execute(
                        f"""
                        SELECT
                          u.id, u.day_id, u.utterance_order, u.speaker_name, u.speaker_title, u.speaker_role,
                          u.speech_type, {match_score_select}, {text_select}, {hit_scope_select}, u.page_start, u.page_end, u.position_top_start, u.position_top_end,
                          d.meeting_date, d.title AS day_title, d.pdf_url, d.page_url,
                          s.section, s.meeting_name, s.title AS session_title
                        FROM meeting_utterances u
                        {short_join_sql}
                        JOIN meeting_days d ON d.id=u.day_id
                        JOIN meeting_sessions s ON s.id=d.session_id
                        WHERE {where}
                        ORDER BY {("match_score DESC, " if match_mode == "related" and not stable_order else "")}d.meeting_date DESC, u.day_id ASC, u.utterance_order ASC, u.id ASC
                        {limit_clause}
                        """,
                        tuple(query_params),
                    )
            except pymysql.err.OperationalError:
                if use_fulltext:
                    app.logger.warning("Minutes FULLTEXT search unavailable; falling back to LIKE search", exc_info=True)
                    continue
                raise
            rows = cur.fetchall() or []
            # FULLTEXT is the primary path for minutes search. Falling through to
            # LIKE after FULLTEXT already found rows turns unlimited searches into
            # a table scan and makes common terms such as 観光 several seconds slower.
            if (use_fulltext and rows) or (limit is not None and len(rows) >= limit) or (not use_fulltext and (rows or not speaker_exact_only)):
                break

        include_exchange = context == "wide"
        exchange_windows = fetch_minutes_exchange_windows(cur, rows) if include_exchange else {}
        results: list[dict[str, Any]] = []
        for row in rows:
            day_id = int(row["day_id"])
            order = int(row["utterance_order"])
            row_text = row.get("text") or ""
            snippet = normalize_text(row_text)[:MINUTES_SEARCH_SNIPPET_CHARS] if compact_results else minutes_snippet(row_text, terms)
            exchange = (
                serialize_minutes_exchange(
                    [
                        item for item in exchange_windows.get(day_id, [])
                        if max(1, order - 4) <= int(item["utterance_order"]) <= order + 24
                    ]
                )
                if include_exchange
                else []
            )
            results.append(
                {
                    "id": int(row["id"]),
                    "dayId": day_id,
                    "meetingDate": str(row["meeting_date"]) if row.get("meeting_date") else None,
                    "section": row.get("section") or "",
                    "meetingName": row.get("meeting_name") or "",
                    "dayTitle": row.get("day_title") or "",
                    "pdfUrl": row.get("pdf_url") or "",
                    "pageUrl": row.get("page_url") or "",
                    "speakerName": row.get("speaker_name") or "",
                    "speakerTitle": row.get("speaker_title") or "",
                    "speakerRole": row.get("speaker_role") or "unknown",
                    "speechType": row.get("speech_type") or "statement",
                    "order": order,
                    "pageStart": int(row.get("page_start") or 0),
                    "pageEnd": int(row.get("page_end") or 0),
                    "positionTopStart": float(row.get("position_top_start") or 0),
                    "positionTopEnd": float(row.get("position_top_end") or 0),
                    "snippet": snippet,
                    "text": row_text,
                    "exchange": exchange,
                    "highlightTerms": base_terms,
                    "relatedHighlightTerms": related_terms,
                    "hitScope": row.get("hit_scope") or "body",
                }
            )
        put_local_cache(LOCAL_MINUTES_SEARCH_CACHE, cache_key, results)
        return results


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


def sanitize_search_results(items: Any) -> Any:
    if not isinstance(items, list):
        return items
    sanitized: list[Any] = []
    for item in items:
        if isinstance(item, dict) and isinstance(item.get("snippet"), str):
            next_item = dict(item)
            next_item["snippet"] = clean_link_marker_fragments(next_item["snippet"])
            sanitized.append(next_item)
        else:
            sanitized.append(item)
    return sanitized


def get_search_cache(cur, cache_key: str):
    payload = get_local_cache(LOCAL_SEARCH_CACHE, cache_key)
    if payload is not None:
        return sanitize_search_results(payload)
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
    payload = sanitize_search_results(json.loads(row.get("result_json") or "[]"))
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


def serialize_search_row(
    row: dict[str, Any],
    keywords: list[str],
    highlight_terms: list[str] | None = None,
    related_highlight_terms: list[str] | None = None,
) -> dict[str, Any]:
    law_type = row.get('law_type') or ''
    snippet_text = clean_link_marker_fragments(row.get('article_text') or row.get('full_text') or '')
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
        'highlightTerms': highlight_terms or [],
        'relatedHighlightTerms': related_highlight_terms or [],
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
    highlight_terms: list[str] | None = None,
    related_highlight_terms: list[str] | None = None,
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
                      d.search_tokens AS full_text,
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
    return total, [serialize_search_row(row, keywords, highlight_terms, related_highlight_terms) for row in sliced]


def search_candidate_limit(limit: int, offset: int = 0, multiplier: int = 3, minimum: int = 60, maximum: int = 240) -> int:
    return max(minimum, min(maximum, (limit + offset) * multiplier))


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


def _select_anchor_terms(terms: list[str], source: str, cur, limit: int = 3) -> list[str]:
    candidates = [
        term
        for term in terms
        if term
        and contains_japanese(term)
        and len(term) >= 3
        and not is_hiragana_only(term)
    ]
    if not candidates:
        return []
    deduped: list[str] = []
    seen: set[str] = set()
    for term in candidates:
        if term not in seen:
            seen.add(term)
            deduped.append(term)
    placeholders = ",".join(["%s"] * len(deduped))
    source_sql = " AND d.source=%s" if source != "all" else ""
    params: list[Any] = deduped + ([source] if source != "all" else [])
    cur.execute(
        f"""
        SELECT st.term, COUNT(DISTINCT st.document_id) AS doc_count
        FROM law_search_terms st
        JOIN law_documents d ON d.id=st.document_id
        WHERE st.target_type='document'
          AND st.term IN ({placeholders}){source_sql}
        GROUP BY st.term
        """,
        tuple(params),
    )
    counts = {str(row["term"]): int(row["doc_count"]) for row in (cur.fetchall() or [])}
    ranked = sorted(
        (
            (term, counts.get(term, 10**9))
            for term in deduped
            if counts.get(term, 0) > 0
        ),
        key=lambda item: (item[1], -len(item[0]), item[0]),
    )
    return [term for term, _ in ranked[:limit]]


def _doc_ids_matching_all_terms(terms: list[str], source: str, cur, max_ids: int = 400) -> set[int]:
    if not terms:
        return set()
    placeholders = ",".join(["%s"] * len(terms))
    source_sql = " AND d.source=%s" if source != "all" else ""
    params: list[Any] = terms + ([source] if source != "all" else []) + [len(terms), max_ids]
    cur.execute(
        f"""
        SELECT st.document_id
        FROM law_search_terms st
        JOIN law_documents d ON d.id=st.document_id
        WHERE st.term IN ({placeholders}){source_sql}
        GROUP BY st.document_id
        HAVING COUNT(DISTINCT st.term) >= %s
        ORDER BY MIN(st.document_id) ASC
        LIMIT %s
        """,
        tuple(params),
    )
    return {int(row["document_id"]) for row in (cur.fetchall() or [])}


def _doc_ids_for_keyword(keyword: str, source: str, cur, fuzzy: bool = False) -> set[int]:
    """転置インデックスから1キーワードにマッチする document_id の集合を返す。"""
    norm = normalize_text(keyword).lower()
    if not norm:
        return set()
    if fuzzy:
        terms = list(limited_weighted_terms((norm, 10, True), max_terms=30).keys())
        for syn in expand_keywords_with_synonyms([norm], cur=cur, max_keywords=6):
            if syn != norm:
                terms.extend(limited_weighted_terms((syn, 8, True), max_terms=10).keys())
        terms = list(set(terms))
    else:
        terms = [norm]
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


def _doc_ids_for_field(field_q: str, source: str, cur, fuzzy: bool = False) -> set[int] | None:
    """フィールド内の全キーワード（スペース区切り）をAND結合した document_id 集合を返す。
    フィールドが空なら None を返す（制約なし扱い）。"""
    keywords = [k for k in normalize_text(field_q).lower().split() if k]
    if not keywords:
        return None
    if not fuzzy and len(keywords) > 1:
        return _doc_ids_matching_all_terms(keywords, source, cur, max_ids=5000)
    sets = [_doc_ids_for_keyword(k, source, cur, fuzzy=fuzzy) for k in keywords]
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
    fuzzy: bool = False,
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
        + [source, str(limit), law_type, from_date, to_date, "fuzzy" if fuzzy else "exact"]
    )
    if can_use_meili_structured(active, source, law_type, from_date, to_date):
        try:
            meili_total, meili_items = search_documents_meili_structured(active, source, limit, offset, law_type, fuzzy=fuzzy)
            if meili_total > 0:
                return meili_total, meili_items
            app.logger.info("Meilisearch structured search returned no exact hits; falling back to MySQL")
        except Exception as exc:
            app.logger.warning("Meilisearch structured search fallback: %s", exc)

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
            ids = _doc_ids_for_field(f["q"], source, cur, fuzzy=fuzzy)
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
        if fuzzy:
            all_terms = list(set(
                t
                for f in active
                for keyword in normalize_text(f["q"]).lower().split()
                for t in limited_weighted_terms((keyword, 10, True), max_terms=20).keys()
            ))
        else:
            all_terms = list(set(
                t
                for f in active
                for keyword in normalize_text(f["q"]).lower().split()
                for t in exact_query_terms(keyword)
            ))
        if not all_terms:
            return 0, []
        article_limit = search_candidate_limit(limit, offset)
        document_limit = search_candidate_limit(limit, offset, multiplier=2, minimum=40, maximum=120)
        placeholders = ",".join(["%s"] * len(all_terms))
        id_placeholders = ",".join(["%s"] * len(valid_ids))
        params_a: list[Any] = all_terms + list(valid_ids) + [article_limit]
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
            LIMIT %s
            """,
            tuple(params_a),
        )
        article_candidates = cur.fetchall() or []

        params_d: list[Any] = all_terms + list(valid_ids) + [document_limit]
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
            LIMIT %s
            """,
            tuple(params_d),
        )
        document_candidates = cur.fetchall() or []

    # article / document 候補がなければ valid_ids の文書だけでスコア 0 で返す
    if not article_candidates and not document_candidates:
        # valid_ids はあるが term_score なし → タイトル一致等で最低限返す
        document_candidates = [{"document_id": doc_id, "term_score": 1, "matched_terms": 1} for doc_id in list(valid_ids)[:120]]

    keywords = expand_keywords_with_synonyms(all_keywords, max_keywords=20) if fuzzy else all_keywords
    related_keywords = related_keywords_for_highlight(all_keywords, keywords) if fuzzy else []
    total, results = fetch_search_detail_rows(
        article_candidates,
        document_candidates,
        keywords,
        normalized_query,
        limit,
        offset,
        all_keywords,
        related_keywords,
    )

    if offset == 0:
        with db_cursor(commit=True) as (_, cur):
            put_search_cache(cur, cache_key, normalized_query, source, limit, generation, results)
    return total, results


def search_documents(query: str, source: str = 'all', limit: int = 20, fuzzy: bool = False) -> tuple[int, list[dict[str, Any]]]:
    normalized_query = normalize_text(query).lower()
    if not normalized_query:
        return 0, []
    if meili_is_enabled() and source in SOURCE_SCOPES:
        try:
            meili_total, meili_items = search_documents_meili_structured([{"q": query, "op": "AND"}], source, limit, 0, "", fuzzy=fuzzy)
            if meili_total > 0:
                return meili_total, meili_items
            app.logger.info("Meilisearch search returned no exact hits; falling back to MySQL")
        except Exception as exc:
            app.logger.warning("Meilisearch search fallback: %s", exc)
    article_candidates: list[dict[str, Any]] = []
    document_candidates: list[dict[str, Any]] = []
    anchor_doc_ids: set[int] | None = None
    with db_cursor() as (_, cur):
        generation = get_cache_generation(cur)
        input_keywords = split_keywords(query)
        keywords = expand_keywords_with_synonyms(input_keywords, cur=cur, max_keywords=20) if fuzzy else input_keywords
        related_keywords = related_keywords_for_highlight(input_keywords, keywords) if fuzzy else []
        terms = query_terms(query, cur=cur, fuzzy=fuzzy)
        cache_key = make_cache_key(["search", normalized_query, source, str(limit), str(generation), "fuzzy" if fuzzy else "exact"])
        cached = get_search_cache(cur, cache_key)
        if cached is not None:
            return len(cached), cached
        if not terms:
            return 0, []
        if fuzzy:
            anchor_terms = _select_anchor_terms(terms, source, cur, limit=3)
            if anchor_terms:
                anchor_doc_ids = _doc_ids_matching_all_terms(anchor_terms, source, cur, max_ids=400)
                if not anchor_doc_ids and len(anchor_terms) > 1:
                    anchor_doc_ids = _doc_ids_matching_all_terms(anchor_terms[:2], source, cur, max_ids=400)
        placeholders = ",".join(["%s"] * len(terms))
        params: list[Any] = list(terms)
        source_sql = ""
        if source != "all":
            source_sql = " AND d.source=%s"
            params.append(source)
        doc_filter_sql = ""
        if anchor_doc_ids:
            id_placeholders = ",".join(["%s"] * len(anchor_doc_ids))
            doc_filter_sql = f" AND st.document_id IN ({id_placeholders})"
            params.extend(list(anchor_doc_ids))
        article_limit = search_candidate_limit(limit)
        document_limit = search_candidate_limit(limit, multiplier=2, minimum=40, maximum=180)
        params.append(article_limit)
        cur.execute(
            f"""
            SELECT
              st.document_id,
              st.article_id,
              SUM(st.weight) AS term_score,
              COUNT(*) AS matched_terms
            FROM law_search_terms st
            JOIN law_documents d ON d.id=st.document_id
            WHERE st.target_type='article' AND st.term IN ({placeholders}){source_sql}{doc_filter_sql}
            GROUP BY st.document_id, st.article_id
            ORDER BY term_score DESC, matched_terms DESC, st.document_id ASC, st.article_id ASC
            LIMIT %s
            """,
            tuple(params),
        )
        article_candidates = cur.fetchall() or []

        params = list(terms)
        if source != "all":
            params.append(source)
        if anchor_doc_ids:
            params.extend(list(anchor_doc_ids))
        params.append(document_limit)
        cur.execute(
            f"""
            SELECT
              st.document_id,
              SUM(st.weight) AS term_score,
              COUNT(*) AS matched_terms
            FROM law_search_terms st
            JOIN law_documents d ON d.id=st.document_id
            WHERE st.target_type='document' AND st.term IN ({placeholders}){source_sql}{doc_filter_sql}
            GROUP BY st.document_id
            ORDER BY term_score DESC, matched_terms DESC, st.document_id ASC
            LIMIT %s
            """,
            tuple(params),
        )
        document_candidates = cur.fetchall() or []

    if not article_candidates and not document_candidates:
        results = search_documents_slow(query, source, limit)
        with db_cursor(commit=True) as (_, cur):
            put_search_cache(cur, cache_key, normalized_query, source, limit, generation, results)
        return len(results), results
    _total, results = fetch_search_detail_rows(
        article_candidates,
        document_candidates,
        keywords,
        normalized_query,
        limit,
        0,
        input_keywords,
        related_keywords,
    )
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
        err = payload.get('error') if isinstance(payload, dict) else None
        if not err:
            err = 'access denied' if status == 403 else 'login required'
        return jsonify({'ok': False, 'error': err}), status
    if payload.get('enabled') is False:
        return None
    g.auth_user = payload.get('user') or {}
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
    if source_scope not in SOURCE_SCOPES:
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
    if scope not in SOURCE_SCOPES:
        raise ValueError('sourceScope が不正です。')
    launch_sync_in_background(scope)
    return jsonify({'ok': True, 'started': True, 'sourceScope': scope, 'summary': {}}), 202


@app.post('/api/reindex/run')
def api_reindex_run():
    batch_size = max(1, min(25, int((request.get_json(silent=True) or {}).get('batchSize') or 10)))
    launch_reindex_in_background(batch_size=batch_size)
    return jsonify({'ok': True, 'started': True, 'summary': {'operation': 'reindex', 'batchSize': batch_size}}), 202


@app.post('/api/dictionary/update')
def api_dictionary_update_run():
    payload = request.get_json(silent=True) or {}
    include_wordnet = bool(payload.get('includeWordnet', True))
    include_domain = bool(payload.get('includeDomain', True))
    if not include_wordnet and not include_domain:
        raise ValueError('更新対象を1つ以上選択してください。')
    launch_dictionary_update_in_background(include_wordnet=include_wordnet, include_domain=include_domain)
    return jsonify({
        'ok': True,
        'started': True,
        'summary': {
            'operation': 'dictionary-update',
            'includeWordnet': include_wordnet,
            'includeDomain': include_domain,
        },
    }), 202


@app.post('/api/dictionary/internet/update')
def api_internet_dictionary_update_run():
    payload = request.get_json(silent=True) or {}
    include_wikidata = bool(payload.get('includeWikidata', True))
    include_curated = bool(payload.get('includeCurated', True))
    source_url = normalize_text(payload.get('sourceUrl') or '')
    if source_url and not source_url.startswith(('https://', 'http://')):
        raise ValueError('辞書URLは http または https のみ対応しています。')
    if not include_wikidata and not include_curated and not source_url:
        raise ValueError('取り込み対象を1つ以上選択してください。')
    launch_internet_dictionary_update_in_background(
        include_wikidata=include_wikidata,
        include_curated=include_curated,
        source_url=source_url,
    )
    return jsonify({
        'ok': True,
        'started': True,
        'summary': {
            'operation': 'internet-dictionary-update',
            'includeWikidata': include_wikidata,
            'includeCurated': include_curated,
            'sourceUrl': source_url,
        },
    }), 202


@app.post('/api/dictionary/minutes/update')
def api_minutes_dictionary_update_run():
    payload = request.get_json(silent=True) or {}
    batch_size = max(100, min(5000, int(payload.get('batchSize') or 1000)))
    launch_minutes_dictionary_update_in_background(batch_size=batch_size)
    return jsonify({
        'ok': True,
        'started': True,
        'summary': {
            'operation': 'minutes-dictionary-update',
            'batchSize': batch_size,
        },
    }), 202


@app.post('/api/dictionary/compile')
def api_dictionary_compile_run():
    launch_dictionary_compile_in_background()
    return jsonify({
        'ok': True,
        'started': True,
        'summary': {
            'operation': 'dictionary-compile',
        },
    }), 202


@app.post('/api/minutes/retag')
def api_minutes_retag_run():
    payload = request.get_json(silent=True) or {}
    batch_size = max(1, min(100, int(payload.get('batchSize') or 25)))
    launch_minutes_retag_in_background(batch_size=batch_size)
    return jsonify({
        'ok': True,
        'started': True,
        'summary': {
            'operation': 'minutes-retag',
            'batchSize': batch_size,
        },
    }), 202


@app.get('/api/search')
def api_search():
    source = (request.args.get('source') or 'all').strip()
    if source not in SOURCE_SCOPES:
        raise ValueError('source が不正です。')
    limit = max(1, min(100, int(request.args.get('limit') or '20')))
    offset = max(0, int(request.args.get('offset') or '0'))
    law_type = (request.args.get('lawType') or '').strip()
    from_date = (request.args.get('fromDate') or '').strip()
    to_date = (request.args.get('toDate') or '').strip()
    fuzzy = (request.args.get('fuzzy') or '').strip().lower() in {'1', 'true', 'yes', 'on'}
    # 構造化検索: q1..q4 + op2..op4 が渡された場合
    fields: list[dict[str, str]] = []
    for i in range(1, 5):
        q = (request.args.get(f'q{i}') or '').strip()
        op = (request.args.get(f'op{i}') or 'AND').strip().upper()
        if op not in ('AND', 'OR'):
            op = 'AND'
        fields.append({'q': q, 'op': op})
    if any(f['q'] for f in fields):
        total, items = search_documents_structured(fields, source, limit, offset, law_type, from_date, to_date, fuzzy=fuzzy)
        query_label = " ".join(
            [fields[0]["q"]]
            + [f"{field['op']} {field['q']}" for field in fields[1:] if field["q"]]
        )
        record_usage_event(
            "law-search",
            query_label,
            source,
            total,
            {
                "mode": "structured",
                "lawType": law_type,
                "fromDate": from_date,
                "toDate": to_date,
                "fuzzy": fuzzy,
                "limit": limit,
                "offset": offset,
            },
        )
        return jsonify({'items': items, 'total': total})
    # 後方互換: q パラメータによる従来検索
    query = (request.args.get('q') or '').strip()
    if query:
        total, items = search_documents(query, source, limit, fuzzy=fuzzy)
    else:
        total, items = 0, []
    if query or law_type or from_date or to_date:
        record_usage_event(
            "law-search",
            query,
            source,
            total,
            {
                "mode": "simple",
                "lawType": law_type,
                "fromDate": from_date,
                "toDate": to_date,
                "fuzzy": fuzzy,
                "limit": limit,
                "offset": offset,
            },
        )
    return jsonify({'items': items, 'total': total})


@app.get('/api/reference/search')
def api_reference_search():
    return api_search()


@app.get('/api/minutes/status')
def api_minutes_status():
    with db_cursor() as (_, cur):
        cur.execute("SELECT COUNT(*) AS cnt FROM meeting_days WHERE extraction_status='success'")
        day_count = int((cur.fetchone() or {}).get("cnt") or 0)
        cur.execute("SELECT COUNT(*) AS cnt FROM meeting_utterances")
        utterance_count = int((cur.fetchone() or {}).get("cnt") or 0)
        cur.execute("SELECT COUNT(*) AS cnt FROM meeting_tables")
        table_count = int((cur.fetchone() or {}).get("cnt") or 0)
        cur.execute("SELECT COUNT(*) AS cnt FROM meeting_speakers")
        speaker_count = int((cur.fetchone() or {}).get("cnt") or 0)
        cur.execute(
            """
            SELECT id, status, started_at, finished_at, recent_days, summary_json, error_text
            FROM meeting_extract_runs
            ORDER BY id DESC LIMIT 1
            """
        )
        run = cur.fetchone()
        cur.execute(
            """
            SELECT d.id, d.meeting_date, d.title, d.pdf_url, s.section, s.meeting_name
            FROM meeting_days d
            JOIN meeting_sessions s ON s.id=d.session_id
            WHERE d.extraction_status='success'
            ORDER BY d.meeting_date DESC, d.id DESC
            LIMIT 8
            """
        )
        latest_days = cur.fetchall() or []
        latest_compile = latest_minutes_compile_status(cur)
    return jsonify(
        {
            "dayCount": day_count,
            "utteranceCount": utterance_count,
            "tableCount": table_count,
            "speakerCount": speaker_count,
            "latestCompile": latest_compile,
            "latestRun": {
                "id": int(run["id"]),
                "status": run["status"],
                "startedAt": str(run["started_at"]) if run.get("started_at") else None,
                "finishedAt": str(run["finished_at"]) if run.get("finished_at") else None,
                "recentDays": int(run.get("recent_days") or 0),
                "summary": json.loads(run.get("summary_json") or "{}"),
                "errorText": run.get("error_text"),
            }
            if run
            else None,
            "latestDays": [
                {
                    "id": int(row["id"]),
                    "meetingDate": str(row["meeting_date"]) if row.get("meeting_date") else None,
                    "section": row.get("section") or "",
                    "meetingName": row.get("meeting_name") or "",
                    "title": row.get("title") or "",
                    "pdfUrl": row.get("pdf_url") or "",
                }
                for row in latest_days
            ],
        }
    )


@app.post('/api/minutes/sync')
def api_minutes_sync():
    payload = request.get_json(silent=True) or {}
    requested_recent_days = int(payload.get("recentDays") if payload.get("recentDays") is not None else 365)
    recent_days = 0 if requested_recent_days <= 0 else max(1, min(36600, requested_recent_days))
    launch_minutes_sync_in_background(recent_days=recent_days)
    return jsonify({"ok": True, "started": True, "recentDays": recent_days}), 202


@app.post('/api/minutes/compile')
def api_minutes_compile():
    payload = request.get_json(silent=True) or {}
    trigger = normalize_text(str(payload.get("trigger") or "manual"))[:64] or "manual"
    launch_minutes_compile_in_background(trigger=trigger)
    return jsonify({"ok": True, "started": True, "trigger": trigger}), 202


@app.get('/api/minutes/search')
def api_minutes_search():
    query = (request.args.get("q") or "").strip()
    speaker = (request.args.get("speaker") or "").strip()
    role = (request.args.get("role") or "").strip()
    section = (request.args.get("section") or "").strip()
    from_date = (request.args.get("fromDate") or "").strip()
    to_date = (request.args.get("toDate") or "").strip()
    years = parse_minutes_years(request.args.get("years") or "")
    meeting_id = int(request.args.get("meetingId") or 0) or None
    day_id = int(request.args.get("dayId") or 0) or None
    match_mode = (request.args.get("matchMode") or "exact").strip()
    if match_mode not in {"exact", "related"}:
        match_mode = "exact"
    op = (request.args.get("op") or "AND").strip().upper()
    if op not in {"AND", "OR"}:
        op = "AND"
    context = (request.args.get("context") or "none").strip().lower()
    if context not in {"none", "wide"}:
        context = "none"
    include_speaker_meta = (request.args.get("includeSpeakerMeta") or "").strip().lower() in {"1", "true", "yes", "on"}
    raw_limit = (request.args.get("limit") or "20").strip().lower()
    is_unlimited = raw_limit in {"all", "unlimited", "0"}
    cursor = decode_minutes_cursor(request.args.get("cursor") or "")
    page_size_raw = request.args.get("pageSize") or str(MINUTES_CURSOR_PAGE_SIZE)
    try:
        page_size = max(1, min(MINUTES_CURSOR_MAX_PAGE_SIZE, int(page_size_raw)))
    except ValueError:
        page_size = MINUTES_CURSOR_PAGE_SIZE
    if is_unlimited:
        # Keep the semantics of an unrestricted result set without serializing
        # every matching utterance before the first screen can render.
        limit = page_size + 1
    else:
        try:
            limit = max(1, min(200, int(raw_limit)))
        except ValueError:
            limit = 20
    if not query and not speaker and not role and not section and not meeting_id and not day_id:
        return jsonify({"items": [], "total": 0})
    items = search_minutes_items(
        query=query,
        speaker=speaker,
        role=role,
        section=section,
        from_date=from_date,
        to_date=to_date,
        years=years,
        meeting_id=meeting_id,
        day_id=day_id,
        match_mode=match_mode,
        op=op,
        limit=limit,
        context=context,
        include_speaker_meta=include_speaker_meta,
        cursor=cursor,
        prefer_meili=not is_unlimited,
        stable_order=is_unlimited,
    )
    has_more = is_unlimited and len(items) > page_size
    if has_more:
        items = items[:page_size]
    next_cursor = encode_minutes_cursor(items[-1]) if has_more and items else None
    query_label = query or " / ".join(
        part
        for part in [
            f"発言者:{speaker}" if speaker else "",
            f"区分:{role}" if role else "",
            f"会議種別:{section}" if section else "",
            f"年:{','.join(str(year) for year in years)}" if years else "",
            f"会議ID:{meeting_id}" if meeting_id else "",
            f"日程ID:{day_id}" if day_id else "",
        ]
        if part
    )
    record_usage_event(
        "minutes-search",
        query_label,
        section or "all",
        len(items),
        {
            "speaker": speaker,
            "role": role,
            "fromDate": from_date,
            "toDate": to_date,
            "years": years,
            "meetingId": meeting_id,
            "dayId": day_id,
            "matchMode": match_mode,
            "op": op,
            "limit": raw_limit,
            "context": context,
            "includeSpeakerMeta": include_speaker_meta,
        },
    )
    return jsonify({
        "items": items,
        "total": None if is_unlimited else len(items),
        "hasMore": has_more,
        "nextCursor": next_cursor,
    })


@app.get('/api/minutes/meetings')
def api_minutes_meetings():
    with db_cursor() as (_, cur):
        generation = get_cache_generation(cur)
        cache_key = make_cache_key(["minutes-meetings", str(generation)])
        cached = get_local_cache(LOCAL_MINUTES_SEARCH_CACHE, cache_key)
        if cached is not None:
            return jsonify({"items": cached})
        cur.execute(
            """
            SELECT
              s.id, s.section, s.meeting_name, s.title, s.source_url,
              ds.from_date,
              ds.to_date,
              COALESCE(ds.day_count, 0) AS day_count,
              COALESCE(us.utterance_count, 0) AS utterance_count,
              COALESCE(ts.table_count, 0) AS table_count
            FROM meeting_sessions s
            LEFT JOIN (
              SELECT session_id, MIN(meeting_date) AS from_date, MAX(meeting_date) AS to_date, COUNT(*) AS day_count
              FROM meeting_days
              GROUP BY session_id
            ) ds ON ds.session_id=s.id
            LEFT JOIN (
              SELECT d.session_id, COUNT(*) AS utterance_count
              FROM meeting_days d
              JOIN meeting_utterances u ON u.day_id=d.id
              GROUP BY d.session_id
            ) us ON us.session_id=s.id
            LEFT JOIN (
              SELECT d.session_id, COUNT(*) AS table_count
              FROM meeting_days d
              JOIN meeting_tables t ON t.day_id=d.id
              GROUP BY d.session_id
            ) ts ON ts.session_id=s.id
            ORDER BY ds.to_date DESC, s.section ASC, s.meeting_name ASC
            LIMIT 500
            """
        )
        rows = cur.fetchall() or []
    items = [
        {
            "id": int(row["id"]),
            "section": row.get("section") or "",
            "meetingName": row.get("meeting_name") or "",
            "title": row.get("title") or "",
            "sourceUrl": row.get("source_url") or "",
            "fromDate": str(row["from_date"]) if row.get("from_date") else None,
            "toDate": str(row["to_date"]) if row.get("to_date") else None,
            "dayCount": int(row.get("day_count") or 0),
            "utteranceCount": int(row.get("utterance_count") or 0),
            "tableCount": int(row.get("table_count") or 0),
        }
        for row in rows
    ]
    put_local_cache(LOCAL_MINUTES_SEARCH_CACHE, cache_key, items)
    return jsonify({"items": items})


@app.get('/api/minutes/speakers')
def api_minutes_speakers():
    role = (request.args.get("role") or "").strip()
    section = (request.args.get("section") or "").strip()
    from_date = (request.args.get("fromDate") or "").strip()
    to_date = (request.args.get("toDate") or "").strip()
    years = parse_minutes_years(request.args.get("years") or "")
    meeting_id = int(request.args.get("meetingId") or 0) or None
    conditions = ["1=1"]
    params: list[Any] = []
    append_minutes_role_filter(conditions, params, role, "sp.role", "sp.title")
    if section and section != "all":
        conditions.append("s.section=%s")
        params.append(section)
    if meeting_id:
        conditions.append("s.id=%s")
        params.append(meeting_id)
    if years:
        year_ranges = []
        for year in years:
            year_ranges.append("(d.meeting_date >= %s AND d.meeting_date <= %s)")
            params.extend([f"{year}-01-01", f"{year}-12-31"])
        conditions.append(f"({' OR '.join(year_ranges)})")
    if from_date:
        conditions.append("d.meeting_date >= %s")
        params.append(from_date)
    if to_date:
        conditions.append("d.meeting_date <= %s")
        params.append(to_date)
    where = " AND ".join(conditions)
    with db_cursor() as (_, cur):
        generation = get_cache_generation(cur)
        cache_key = make_cache_key(["minutes-speakers", role, section, str(meeting_id or ""), from_date, to_date, ",".join(str(year) for year in years), str(generation)])
        cached = get_local_cache(LOCAL_MINUTES_SEARCH_CACHE, cache_key)
        if cached is not None:
            return jsonify({"items": cached})
        cur.execute(
            f"""
            SELECT sp.display_name, sp.title, sp.role, sp.speaker_group, COUNT(u.id) AS utterance_count
            FROM meeting_speakers sp
            JOIN meeting_utterances u ON u.speaker_id=sp.id
            JOIN meeting_days d ON d.id=u.day_id
            JOIN meeting_sessions s ON s.id=d.session_id
            WHERE {where}
            GROUP BY sp.id, sp.display_name, sp.title, sp.role, sp.speaker_group
            ORDER BY utterance_count DESC, display_name ASC
            LIMIT 500
            """,
            tuple(params),
        )
        rows = cur.fetchall() or []
    items = [
        {
            "displayName": row.get("display_name") or "",
            "title": row.get("title") or "",
            "role": row.get("role") or "unknown",
            "speakerGroup": row.get("speaker_group") or "",
            "utteranceCount": int(row.get("utterance_count") or 0),
        }
        for row in rows
    ]
    put_local_cache(LOCAL_MINUTES_SEARCH_CACHE, cache_key, items)
    return jsonify({"items": items})


@app.get('/api/minutes/meetings/<int:meeting_id>')
def api_minutes_meeting_detail(meeting_id: int):
    with db_cursor() as (_, cur):
        cur.execute(
            """
            SELECT
              s.id, s.section, s.meeting_name, s.title, s.source_url,
              ds.from_date,
              ds.to_date,
              COALESCE(us.utterance_count, 0) AS utterance_count,
              COALESCE(ts.table_count, 0) AS table_count
            FROM meeting_sessions s
            LEFT JOIN (
              SELECT session_id, MIN(meeting_date) AS from_date, MAX(meeting_date) AS to_date
              FROM meeting_days
              WHERE session_id=%s
              GROUP BY session_id
            ) ds ON ds.session_id=s.id
            LEFT JOIN (
              SELECT d.session_id, COUNT(*) AS utterance_count
              FROM meeting_days d
              JOIN meeting_utterances u ON u.day_id=d.id
              WHERE d.session_id=%s
              GROUP BY d.session_id
            ) us ON us.session_id=s.id
            LEFT JOIN (
              SELECT d.session_id, COUNT(*) AS table_count
              FROM meeting_days d
              JOIN meeting_tables t ON t.day_id=d.id
              WHERE d.session_id=%s
              GROUP BY d.session_id
            ) ts ON ts.session_id=s.id
            WHERE s.id=%s
            """,
            (meeting_id, meeting_id, meeting_id, meeting_id),
        )
        meeting = cur.fetchone()
        if not meeting:
            raise ValueError("会議録が見つかりません。")

        cur.execute(
            """
            SELECT d.id, d.meeting_date, d.title, d.pdf_url, d.page_url, d.page_count,
                   s.section, s.meeting_name
            FROM meeting_days d
            JOIN meeting_sessions s ON s.id=d.session_id
            WHERE d.session_id=%s
            ORDER BY d.meeting_date ASC, d.id ASC
            """,
            (meeting_id,),
        )
        day_rows = cur.fetchall() or []
        day_ids = [int(row["id"]) for row in day_rows]
        compiled_days = compiled_minutes_days_for_meeting(cur, meeting_id)
        utterances_by_day: dict[int, list[dict[str, Any]]] = {day_id: [] for day_id in day_ids}
        tables_by_day: dict[int, list[dict[str, Any]]] = {day_id: [] for day_id in day_ids}
        missing_day_ids = [day_id for day_id in day_ids if day_id not in compiled_days]
        if missing_day_ids:
            placeholders = ",".join(["%s"] * len(missing_day_ids))
            cur.execute(
                f"""
                SELECT day_id, id, utterance_order, speaker_name, speaker_title, speaker_role, speech_type, text,
                       page_start, page_end, position_top_start, position_top_end
                FROM meeting_utterances
                WHERE day_id IN ({placeholders})
                ORDER BY day_id ASC, utterance_order ASC
                """,
                tuple(missing_day_ids),
            )
            for row in cur.fetchall() or []:
                utterances_by_day.setdefault(int(row["day_id"]), []).append(row)
            cur.execute(
                f"""
                SELECT day_id, id, table_key, page, position_top, position_bottom, caption, rows_json, html, search_text, confidence
                FROM meeting_tables
                WHERE day_id IN ({placeholders})
                ORDER BY day_id ASC, page ASC, position_top ASC, id ASC
                """,
                tuple(missing_day_ids),
            )
            for row in cur.fetchall() or []:
                tables_by_day.setdefault(int(row["day_id"]), []).append(row)

    return jsonify(
        {
            "id": int(meeting["id"]),
            "section": meeting.get("section") or "",
            "meetingName": meeting.get("meeting_name") or "",
            "title": meeting.get("title") or "",
            "sourceUrl": meeting.get("source_url") or "",
            "fromDate": str(meeting["from_date"]) if meeting.get("from_date") else None,
            "toDate": str(meeting["to_date"]) if meeting.get("to_date") else None,
            "utteranceCount": int(meeting.get("utterance_count") or 0),
            "tableCount": int(meeting.get("table_count") or 0),
            "days": [
                compiled_days.get(int(day["id"]))
                or build_minutes_day_detail_payload(
                    day,
                    utterances_by_day.get(int(day["id"]), []),
                    tables_by_day.get(int(day["id"]), []),
                )
                for day in day_rows
            ],
        }
    )


@app.get('/api/minutes/days/<int:day_id>')
def api_minutes_day_detail(day_id: int):
    with db_cursor() as (_, cur):
        compiled = compiled_minutes_day_detail(cur, day_id)
        if compiled:
            return jsonify(compiled)
        cur.execute(
            """
            SELECT d.id, d.meeting_date, d.title, d.pdf_url, d.page_url, d.page_count,
                   s.section, s.meeting_name
            FROM meeting_days d
            JOIN meeting_sessions s ON s.id=d.session_id
            WHERE d.id=%s
            """,
            (day_id,),
        )
        day = cur.fetchone()
        if not day:
            raise ValueError("会議録が見つかりません。")
        cur.execute(
            """
            SELECT id, utterance_order, speaker_name, speaker_title, speaker_role, speech_type, text,
                   page_start, page_end, position_top_start, position_top_end
            FROM meeting_utterances
            WHERE day_id=%s
            ORDER BY utterance_order ASC
            """,
            (day_id,),
        )
        utterance_rows = cur.fetchall() or []
        cur.execute(
            "SELECT id, table_key, page, position_top, position_bottom, caption, rows_json, html, search_text, confidence FROM meeting_tables WHERE day_id=%s ORDER BY page ASC, position_top ASC, id ASC",
            (day_id,),
        )
        table_rows = cur.fetchall() or []
    return jsonify(build_minutes_day_detail_payload(day, utterance_rows, table_rows))


@app.get('/api/documents')
def api_document_list():
    source = (request.args.get('source') or 'all').strip()
    if source not in SOURCE_SCOPES:
        raise ValueError('source が不正です。')
    fmt = (request.args.get('format') or '').strip().lower()
    with db_cursor() as (_, cur):
        if source in ('mine-city', 'egov', 'local-public-service'):
            if source == 'mine-city':
                cur.execute(
                    """
                    SELECT id, source, title, law_type, law_number, category_path,
                           browse_category_key, browse_document_order, promulgated_at
                    FROM law_documents
                    WHERE source=%s
                    ORDER BY
                      CASE WHEN browse_category_key = '' THEN 1 ELSE 0 END,
                      browse_category_key,
                      browse_document_order,
                      law_number,
                      title
                    """,
                    (source,),
                )
            else:
                cur.execute(
                    """
                    SELECT id, source, title, law_type, law_number, category_path,
                           browse_category_key, browse_document_order, promulgated_at
                    FROM law_documents
                    WHERE source=%s
                    ORDER BY law_number, title
                    """,
                    (source,),
                )
        else:
            cur.execute(
                """
                SELECT id, source, title, law_type, law_number, category_path,
                       browse_category_key, browse_document_order, promulgated_at
                FROM law_documents
                ORDER BY
                  source,
                  CASE WHEN browse_category_key = '' THEN 1 ELSE 0 END,
                  browse_category_key,
                  browse_document_order,
                  law_number,
                  title
                """
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
    browse_categories: list[dict[str, str]] = []
    if source in ('mine-city', 'all'):
        with db_cursor() as (_, cur2):
            cur2.execute("SELECT browse_nav_json FROM sync_settings WHERE id=1")
            row = cur2.fetchone()
            raw_json = (row or {}).get("browse_nav_json") or "{}"
            try:
                nav_labels: dict[str, str] = json.loads(raw_json)
            except Exception:
                nav_labels = {}
            for key in sorted(nav_labels, key=lambda k: [int(p) if p.isdigit() else p for p in k.split(".")]):
                browse_categories.append({
                    "key": key,
                    "label": nav_labels[key],
                    "trail": build_category_trail(key, nav_labels),
                })
    return jsonify({
        'items': [
            {
                'id': int(doc['id']),
                'source': doc['source'],
                'title': doc['title'],
                'lawType': doc['law_type'] or '',
                'lawNumber': doc['law_number'] or '',
                'categoryPath': doc['category_path'] or '',
                'browseCategoryKey': doc.get('browse_category_key') or '',
                'browseDocumentOrder': int(doc.get('browse_document_order') or 0),
                'promulgatedAt': str(doc['promulgated_at']) if doc['promulgated_at'] else None,
            }
            for doc in docs
        ],
        'browseCategories': browse_categories,
    })


@app.get('/api/documents/<int:document_id>')
def api_document_detail(document_id: int):
    with db_cursor() as (_, cur):
        cur.execute(
            "SELECT id, source, external_id, title, law_type, law_number, category_path, source_url, promulgated_at, effective_at, updated_at_source, full_text, metadata_json FROM law_documents WHERE id=%s",
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
        source_document_map: dict[str, int] = {}
        if doc["source"] == "mine-city":
            cur.execute("SELECT id, external_id FROM law_documents WHERE source='mine-city'")
            source_document_map = {
                str(row["external_id"]): int(row["id"])
                for row in (cur.fetchall() or [])
                if row.get("external_id")
            }
    key_to_article_id = {str(article["article_key"]): int(article["id"]) for article in articles}
    metadata: dict[str, Any] = {}
    try:
        metadata = json.loads(doc.get("metadata_json") or "{}")
    except Exception:
        metadata = {}
    source_anchor_map = {
        str(anchor_id): key_to_article_id[str(article_key)]
        for anchor_id, article_key in (metadata.get("sourceAnchorMap") or {}).items()
        if str(article_key) in key_to_article_id
    }
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
            'sourceAnchorMap': source_anchor_map,
            'sourceDocumentMap': source_document_map,
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


def merge_candidates_by_identity(candidates: list[dict[str, Any]], limit: int = 40) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    by_key: dict[tuple[int, int | None], dict[str, Any]] = {}
    for candidate in candidates:
        key = (
            int(candidate.get("documentId") or 0),
            int(candidate["articleId"]) if candidate.get("articleId") is not None else None,
        )
        if not key[0]:
            continue
        existing = by_key.get(key)
        if existing is None:
            item = dict(candidate)
            by_key[key] = item
            merged.append(item)
            continue
        if int(candidate.get("score") or 0) > int(existing.get("score") or 0):
            existing.update(candidate)
        else:
            reasons = list(existing.get("matchReasons") or [])
            for reason in candidate.get("matchReasons") or []:
                if reason not in reasons:
                    reasons.append(reason)
            existing["matchReasons"] = reasons
    merged.sort(key=lambda item: -int(item.get("score") or 0))
    return merged[:limit]


def expand_document_candidates_to_articles(
    candidates: list[dict[str, Any]],
    terms: list[str],
    limit_per_document: int = 3,
) -> list[dict[str, Any]]:
    doc_ids = [
        int(candidate["documentId"])
        for candidate in candidates
        if candidate.get("documentId") and candidate.get("articleId") is None
    ]
    doc_ids = list(dict.fromkeys(doc_ids))
    search_terms = _dedupe_terms(
        [
            term
            for term in terms
            if len(term) >= 2 and not is_hiragana_only(term)
        ],
        limit=16,
    )
    if not doc_ids or not search_terms:
        return []
    with db_cursor() as (_, cur):
        term_ph = ",".join(["%s"] * len(search_terms))
        doc_ph = ",".join(["%s"] * len(doc_ids))
        cur.execute(
            f"""
            SELECT st.document_id, st.article_id,
                   SUM(st.weight) AS term_score,
                   COUNT(DISTINCT st.term) AS matched_terms
            FROM law_search_terms st
            WHERE st.target_type='article'
              AND st.term IN ({term_ph})
              AND st.document_id IN ({doc_ph})
              AND st.article_id IS NOT NULL
            GROUP BY st.document_id, st.article_id
            ORDER BY st.document_id ASC, term_score DESC, matched_terms DESC, st.article_id ASC
            LIMIT %s
            """,
            tuple(search_terms + doc_ids + [max(20, len(doc_ids) * limit_per_document * 3)]),
        )
        rows = cur.fetchall() or []
    selected: list[dict[str, Any]] = []
    per_doc: dict[int, int] = {}
    for row in rows:
        doc_id = int(row["document_id"])
        if per_doc.get(doc_id, 0) >= limit_per_document:
            continue
        per_doc[doc_id] = per_doc.get(doc_id, 0) + 1
        selected.append(row)
    if not selected:
        return []
    _total, details = fetch_search_detail_rows(
        selected,
        [],
        search_terms,
        " ".join(search_terms),
        limit=len(selected),
    )
    return details


def score_ask_candidate(
    candidate: dict[str, Any],
    profile: dict[str, list[str]],
    question_type: str,
) -> int:
    score = int(candidate.get("score") or 0)
    title = normalize_text(candidate.get("title") or "").lower()
    article_number = normalize_text(candidate.get("articleNumber") or "").lower()
    article_title = normalize_text(candidate.get("articleTitle") or "").lower()
    article_text = normalize_text(candidate.get("articleText") or candidate.get("snippet") or "").lower()
    haystack = " ".join([title, article_number, article_title, article_text])

    core_terms = profile.get("core", [])
    fallback_terms = profile.get("fallback", [])
    matched_core = sum(1 for term in core_terms if term and term in haystack)
    primary_term = core_terms[0] if core_terms else ""
    primary_components = [
        term
        for term in fallback_terms
        if primary_term and term in primary_term and term in haystack and len(term) >= 2
    ]
    if primary_term and primary_term not in haystack:
        if len(primary_components) >= 2:
            score -= 120
        elif len(primary_term) <= 4:
            score -= 4200
        elif matched_core > 0:
            score -= 900
        else:
            score -= 1800
    if core_terms and matched_core == 0 and not primary_components:
        score -= 900
    score += matched_core * 160 + len(primary_components) * 70

    for phrase in profile.get("phrases", []):
        if phrase in title:
            score += 420
        if phrase in article_title:
            score += 220
        if phrase in article_text:
            score += 80
    for term in profile.get("core", []):
        if term in title:
            score += 180
        if term in article_title:
            score += 95
        if term in article_number:
            score += 50
        if term in article_text:
            score += 35
    for term in profile.get("intent", []):
        if term in haystack:
            score += 25
    for boost_term in QUESTION_TYPE_BOOST_TERMS.get(question_type, ()):
        if boost_term in article_title:
            score += 80
        elif boost_term in article_text:
            score += 45
    if candidate.get("articleId") is None:
        score -= 120
    return score


def ask_candidate_search(
    query: str,
    profile: dict[str, list[str]],
    question_type: str,
    limit: int = 12,
) -> list[dict[str, Any]]:
    primary_plans: list[tuple[str, bool, int]] = []
    fallback_plans: list[tuple[str, bool, int]] = []
    phrases = profile.get("phrases", [])
    core = profile.get("core", [])
    fallback_terms = profile.get("fallback", [])
    if len(core) >= 2:
        primary_plans.append((" ".join(core[:2]), False, 24))
    if len(core) >= 3:
        primary_plans.append((" ".join(core[:3]), False, 24))
    for phrase in phrases[:4]:
        primary_plans.append((phrase, False, 14))
    if len(core) >= 2:
        for term in core[1:5]:
            fallback_plans.append((f"{core[0]} {term}", False, 18))
    for term in core[:5]:
        if len(term) >= 3 and term not in QUESTION_INTENT_TERMS:
            fallback_plans.append((term, False, 10))
    for term in fallback_terms[:8]:
        fallback_plans.append((term, False, 10))
    if core and fallback_terms:
        for term in fallback_terms[:4]:
            fallback_plans.append((f"{core[0]} {term}", False, 14))
    if core:
        fallback_plans.append((" ".join(core[:4]), True, 24))
    else:
        fallback_plans.append((query, True, 20))

    candidates: list[dict[str, Any]] = []
    seen_plans: set[tuple[str, bool]] = set()
    for idx, (plan_query, fuzzy, plan_limit) in enumerate([*primary_plans, *fallback_plans]):
        if idx >= len(primary_plans) and len(candidates) >= max(10, limit * 2):
            break
        plan_query = normalize_text(plan_query)
        if not plan_query:
            continue
        plan_key = (plan_query.lower(), fuzzy)
        if plan_key in seen_plans:
            continue
        seen_plans.add(plan_key)
        try:
            _total, items = search_documents_structured(
                [{"q": plan_query, "op": "AND"}],
                "all",
                plan_limit,
                0,
                fuzzy=fuzzy,
            )
            candidates.extend(items)
            candidates = merge_candidates_by_identity(candidates, limit=60)
        except Exception as exc:
            app.logger.warning("ask candidate search failed: %s", exc)

    candidates = merge_candidates_by_identity(candidates, limit=60)
    candidates = merge_candidates_by_identity(
        [
            *candidates,
            *expand_document_candidates_to_articles(candidates, [*phrases, *core]),
        ],
        limit=60,
    )
    enriched = enrich_candidates_with_text(candidates)
    for candidate in enriched:
        candidate["score"] = score_ask_candidate(candidate, profile, question_type)
    enriched.sort(key=lambda item: (-int(item.get("score") or 0), int(item.get("documentId") or 0), int(item.get("articleId") or 0)))
    return enriched[:limit]


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
                article_texts[int(row["id"])] = clean_link_marker_fragments(row["text"] or "")
        if doc_ids_no_article:
            ph = ",".join(["%s"] * len(doc_ids_no_article))
            cur.execute(f"SELECT id, full_text FROM law_documents WHERE id IN ({ph})", doc_ids_no_article)
            for row in cur.fetchall():
                full = clean_link_marker_fragments(row["full_text"] or "")
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
        groups[doc_id]["topScore"] = max(groups[doc_id]["topScore"], int(c.get("score") or 0))
        groups[doc_id]["articles"].append({
            "articleId": c.get("articleId"),
            "articleNumber": c.get("articleNumber"),
            "articleTitle": c.get("articleTitle"),
            "articleText": c.get("articleText", ""),
            "score": int(c.get("score") or 0),
        })
    for group in groups.values():
        group["articles"].sort(key=lambda item: -int(item.get("score") or 0))
        group["articles"] = group["articles"][:5]
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
        cache_key = make_cache_key(["ask3", normalized_query, str(generation)])
        cached = get_ask_cache(cur, cache_key)
        if cached is not None:
            record_usage_event_cur(
                cur,
                "ask",
                normalized_query,
                "all",
                len(cached.get("candidateGroups") or cached.get("candidates") or []),
                {"cache": True},
            )
            return jsonify(cached)
        profile = question_search_profile(query, cur=cur)
        keywords = expand_keywords_with_synonyms(profile["display"], cur=cur, max_keywords=20)
    candidates = ask_candidate_search(query, profile, question_type, limit=14)
    candidate_groups = group_ask_candidates(candidates)
    # answerLead をタイプに応じて生成
    type_label = QUESTION_TYPE_LABELS.get(question_type, "一般的な質問")
    if candidate_groups:
        top = candidate_groups[0]
        top_article = top["articles"][0] if top["articles"] else {}
        article_part = f"（{top_article['articleNumber']}）" if top_article.get("articleNumber") else ""
        lead = (
            f"{type_label}として、{source_label(top['source'])}の「{top['title']}」{article_part}が"
            f"最も関連すると推定されます。候補条文を確認し、必要に応じて原文で最終確認してください。"
        )
    else:
        lead = "関連する条文が見つかりませんでした。キーワードを変えて再度お試しください。"
    response_payload = {
        'query': query,
        'normalizedQuery': normalize_text(query),
        'keywords': keywords,
        'searchKeywords': profile["core"],
        'questionType': question_type,
        'questionTypeLabel': type_label,
        'answerLead': lead,
        'candidateGroups': candidate_groups,
        'candidates': candidates,  # 後方互換
    }
    with db_cursor(commit=True) as (_, cur):
        put_ask_cache(cur, cache_key, normalized_query, generation, response_payload)
        record_usage_event_cur(
            cur,
            "ask",
            normalized_query,
            "all",
            len(candidate_groups),
            {"cache": False, "questionType": question_type},
        )
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
            "SELECT id, canonical_term, synonym_term, priority, is_active, source_type, source_version"
            " FROM law_synonyms ORDER BY canonical_term, synonym_term LIMIT 500"
        )
        rows = cur.fetchall() or []
        cur.execute(
            "SELECT source_type, source_version, COUNT(*) AS count"
            " FROM law_synonyms WHERE is_active=1 GROUP BY source_type, source_version ORDER BY source_type, source_version"
        )
        stats = cur.fetchall() or []
    return jsonify({
        'items': [
            {
                'id': int(r['id']),
                'canonicalTerm': r['canonical_term'],
                'synonymTerm': r['synonym_term'],
                'priority': int(r['priority']),
                'isActive': bool(r['is_active']),
                'sourceType': r.get('source_type') or 'manual',
                'sourceVersion': r.get('source_version') or '',
            }
            for r in rows
        ],
        'stats': [
            {
                'sourceType': r.get('source_type') or 'manual',
                'sourceVersion': r.get('source_version') or '',
                'count': int(r.get('count') or 0),
            }
            for r in stats
        ],
        'compiled': compiled_synonym_dictionary_status(get_compiled_dictionary_path()),
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
            "INSERT INTO law_synonyms (canonical_term, synonym_term, priority, is_active, source_type, source_version)"
            " VALUES (%s,%s,%s,1,'manual','')"
            " ON DUPLICATE KEY UPDATE priority=%s, is_active=1, source_type='manual', updated_at=CURRENT_TIMESTAMP",
            (canonical, synonym, priority, priority),
        )
        new_id = int(cur.lastrowid) if cur.lastrowid else 0
        compile_synonym_dictionary(cur, output_path=get_compiled_dictionary_path())
        bump_cache_generation(cur)
    clear_local_caches()
    return jsonify({'id': new_id, 'canonicalTerm': canonical, 'synonymTerm': synonym, 'priority': priority, 'isActive': True, 'sourceType': 'manual', 'sourceVersion': ''})


@app.delete('/api/synonyms/<int:synonym_id>')
def api_synonyms_delete(synonym_id: int):
    with db_cursor(commit=True) as (_, cur):
        cur.execute("DELETE FROM law_synonyms WHERE id=%s", (synonym_id,))
        if cur.rowcount == 0:
            raise ValueError('同義語が見つかりません。')
        compile_synonym_dictionary(cur, output_path=get_compiled_dictionary_path())
        bump_cache_generation(cur)
    clear_local_caches()
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
        cur.execute(
            """
            SELECT event_type, COUNT(*) AS count
            FROM usage_events
            GROUP BY event_type
            """
        )
        usage_counts = {r["event_type"]: int(r.get("count") or 0) for r in (cur.fetchall() or [])}
        cur.execute("SELECT MAX(created_at) AS latest_used_at FROM usage_events")
        latest_usage = cur.fetchone() or {}

        def top_usage_queries(event_type: str) -> list[dict[str, Any]]:
            cur.execute(
                """
                SELECT normalized_query, COUNT(*) AS hits, COALESCE(SUM(result_count),0) AS result_count
                FROM usage_events
                WHERE event_type=%s AND normalized_query <> ''
                GROUP BY normalized_query
                ORDER BY hits DESC, MAX(created_at) DESC
                LIMIT 10
                """,
                (event_type,),
            )
            return [
                {
                    "query": row["normalized_query"],
                    "hits": int(row.get("hits") or 0),
                    "resultCount": int(row.get("result_count") or 0),
                }
                for row in (cur.fetchall() or [])
            ]

        top_law_usage = top_usage_queries("law-search")
        top_minutes_usage = top_usage_queries("minutes-search")
        top_ask_usage = top_usage_queries("ask")
    law_count = int(usage_counts.get("law-search") or 0)
    minutes_count = int(usage_counts.get("minutes-search") or 0)
    ask_count = int(usage_counts.get("ask") or 0)
    latest_used_at = latest_usage.get("latest_used_at")
    return jsonify({
        'totalUsageEvents': law_count + minutes_count + ask_count,
        'lawSearchCount': law_count,
        'minutesSearchCount': minutes_count,
        'askCount': ask_count,
        'latestUsedAt': str(latest_used_at) if latest_used_at else None,
        'searchCacheHits': int(sc.get('total_hits') or 0),
        'searchCacheEntries': int(sc.get('entries') or 0),
        'askCacheHits': int(ac.get('total_hits') or 0),
        'askCacheEntries': int(ac.get('entries') or 0),
        'topLawSearchQueries': top_law_usage,
        'topMinutesSearchQueries': top_minutes_usage,
        'topUsageAskQueries': top_ask_usage,
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
            'description': '美祢市例規・地方自治法・地方公務員法データベース API',
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
                        {'name': 'source', 'in': 'query', 'schema': {'type': 'string', 'enum': ['all', 'mine-city', 'egov', 'local-public-service']}},
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
            '/dictionary/compile': {
                'post': {'summary': '検索用関連語辞書コンパイル', 'responses': {'202': {'description': 'コンパイル開始'}}},
            },
            '/analytics': {'get': {'summary': '利用統計', 'responses': {'200': {'description': '統計データ'}}}},
        },
    }
    return jsonify(spec)


ensure_schema()
maybe_backfill_search_terms()


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=CFG.api_port, debug=True)
