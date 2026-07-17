CREATE TABLE IF NOT EXISTS law_documents (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  source ENUM('mine-city','egov','local-public-service') NOT NULL,
  external_id VARCHAR(128) NOT NULL,
  title VARCHAR(255) NOT NULL,
  normalized_title VARCHAR(255) NOT NULL DEFAULT '',
  search_tokens LONGTEXT NOT NULL,
  law_type VARCHAR(64) NOT NULL DEFAULT '',
  law_number VARCHAR(128) NOT NULL DEFAULT '',
  category_path VARCHAR(255) NOT NULL DEFAULT '',
  browse_category_key VARCHAR(128) NOT NULL DEFAULT '',
  browse_document_order INT NOT NULL DEFAULT 0,
  source_url VARCHAR(512) NOT NULL,
  promulgated_at DATE NULL,
  effective_at DATE NULL,
  updated_at_source VARCHAR(64) NOT NULL DEFAULT '',
  content_hash CHAR(64) NOT NULL,
  full_text LONGTEXT NOT NULL,
  metadata_json LONGTEXT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_law_documents_source_external (source, external_id),
  KEY idx_law_documents_title (normalized_title),
  KEY idx_law_documents_type (law_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

ALTER TABLE law_documents
  ADD COLUMN IF NOT EXISTS search_tokens LONGTEXT NOT NULL AFTER normalized_title;

CREATE TABLE IF NOT EXISTS law_articles (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  document_id BIGINT UNSIGNED NOT NULL,
  article_key VARCHAR(128) NOT NULL,
  article_number VARCHAR(128) NOT NULL,
  article_title VARCHAR(255) NOT NULL DEFAULT '',
  parent_path VARCHAR(255) NOT NULL DEFAULT '',
  sort_key INT NOT NULL DEFAULT 0,
  text LONGTEXT NOT NULL,
  search_text LONGTEXT NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_law_articles_document (document_id, sort_key),
  KEY idx_law_articles_number (article_number),
  CONSTRAINT fk_law_articles_document FOREIGN KEY (document_id) REFERENCES law_documents(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS law_search_terms (
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
  KEY idx_law_search_terms_target_term_doc_article (target_type, term, document_id, article_id),
  KEY idx_law_search_terms_document (document_id),
  KEY idx_law_search_terms_article (article_id),
  CONSTRAINT fk_law_search_terms_document FOREIGN KEY (document_id) REFERENCES law_documents(id) ON DELETE CASCADE,
  CONSTRAINT fk_law_search_terms_article FOREIGN KEY (article_id) REFERENCES law_articles(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS sync_settings (
  id TINYINT UNSIGNED NOT NULL PRIMARY KEY,
  enabled TINYINT(1) NOT NULL DEFAULT 0,
  day_of_month TINYINT UNSIGNED NOT NULL DEFAULT 1,
  hour TINYINT UNSIGNED NOT NULL DEFAULT 3,
  minute TINYINT UNSIGNED NOT NULL DEFAULT 0,
  timezone VARCHAR(32) NOT NULL DEFAULT '+09:00',
  source_scope ENUM('all','mine-city','egov','local-public-service') NOT NULL DEFAULT 'all',
  cache_generation BIGINT UNSIGNED NOT NULL DEFAULT 1,
  last_started_at VARCHAR(64) NULL,
  last_finished_at VARCHAR(64) NULL,
  last_success_at VARCHAR(64) NULL,
  last_error TEXT NULL,
  updated_by VARCHAR(128) NOT NULL DEFAULT '',
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

ALTER TABLE sync_settings
  ADD COLUMN IF NOT EXISTS cache_generation BIGINT UNSIGNED NOT NULL DEFAULT 1 AFTER source_scope;

ALTER TABLE sync_settings
  ADD COLUMN IF NOT EXISTS browse_nav_json LONGTEXT NULL;

INSERT INTO sync_settings (id, enabled, day_of_month, hour, minute, timezone, source_scope)
VALUES (1, 0, 1, 3, 0, '+09:00', 'all')
ON DUPLICATE KEY UPDATE updated_at = CURRENT_TIMESTAMP;

CREATE TABLE IF NOT EXISTS sync_runs (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  run_type ENUM('manual','scheduled') NOT NULL DEFAULT 'manual',
  status ENUM('running','success','failed') NOT NULL DEFAULT 'running',
  started_at VARCHAR(64) NOT NULL,
  finished_at VARCHAR(64) NULL,
  summary_json LONGTEXT NULL,
  error_text LONGTEXT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_sync_runs_started (created_at),
  KEY idx_sync_runs_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS law_synonyms (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  canonical_term VARCHAR(191) NOT NULL,
  synonym_term VARCHAR(191) NOT NULL,
  priority TINYINT UNSIGNED NOT NULL DEFAULT 10,
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  source_type ENUM('builtin','manual','wordnet','domain','minutes-domain','curated','wikidata','internet','wikipedia','wiktionary') NOT NULL DEFAULT 'manual',
  source_version VARCHAR(64) NOT NULL DEFAULT '',
  pair_term_low VARBINARY(764) AS (LEAST(BINARY canonical_term, BINARY synonym_term)) VIRTUAL,
  pair_term_high VARBINARY(764) AS (GREATEST(BINARY canonical_term, BINARY synonym_term)) VIRTUAL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_law_synonyms_pair (canonical_term, synonym_term),
  UNIQUE KEY uq_law_synonyms_undirected_pair (pair_term_low, pair_term_high),
  KEY idx_law_synonyms_canonical (canonical_term, is_active),
  KEY idx_law_synonyms_synonym (synonym_term, is_active),
  KEY idx_law_synonyms_source (source_type, is_active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

ALTER TABLE law_synonyms
  ADD COLUMN IF NOT EXISTS source_type ENUM('builtin','manual','wordnet','domain','minutes-domain','curated','wikidata','internet','wikipedia','wiktionary') NOT NULL DEFAULT 'manual' AFTER is_active;

ALTER TABLE law_synonyms
  MODIFY COLUMN source_type ENUM('builtin','manual','wordnet','domain','minutes-domain','curated','wikidata','internet','wikipedia','wiktionary') NOT NULL DEFAULT 'manual';

ALTER TABLE law_synonyms
  ADD COLUMN IF NOT EXISTS source_version VARCHAR(64) NOT NULL DEFAULT '' AFTER source_type;

ALTER TABLE law_synonyms
  ADD INDEX IF NOT EXISTS idx_law_synonyms_source (source_type, is_active);

CREATE TABLE IF NOT EXISTS dictionary_sources (
  source_key VARCHAR(64) NOT NULL PRIMARY KEY,
  display_name VARCHAR(128) NOT NULL,
  source_type VARCHAR(32) NOT NULL,
  endpoint VARCHAR(512) NOT NULL,
  license_name VARCHAR(128) NOT NULL DEFAULT '',
  license_url VARCHAR(512) NOT NULL DEFAULT '',
  priority TINYINT UNSIGNED NOT NULL DEFAULT 8,
  is_enabled TINYINT(1) NOT NULL DEFAULT 1,
  cursor_json LONGTEXT NULL,
  cycle_count INT UNSIGNED NOT NULL DEFAULT 0,
  processed_items BIGINT UNSIGNED NOT NULL DEFAULT 0,
  discovered_pairs BIGINT UNSIGNED NOT NULL DEFAULT 0,
  last_started_at DATETIME NULL,
  last_success_at DATETIME NULL,
  last_error LONGTEXT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_dictionary_sources_enabled (is_enabled, source_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS dictionary_pair_evidence (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  canonical_term VARCHAR(191) NOT NULL,
  synonym_term VARCHAR(191) NOT NULL,
  source_key VARCHAR(64) NOT NULL,
  source_item_id VARCHAR(191) NOT NULL DEFAULT '',
  source_url VARCHAR(512) NOT NULL DEFAULT '',
  priority TINYINT UNSIGNED NOT NULL DEFAULT 8,
  confidence DECIMAL(5,4) NOT NULL DEFAULT 0,
  observation_count INT UNSIGNED NOT NULL DEFAULT 0,
  metadata_json LONGTEXT NULL,
  first_seen_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_seen_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_dictionary_pair_evidence_pair_source (canonical_term, synonym_term, source_key),
  KEY idx_dictionary_pair_evidence_canonical (canonical_term, synonym_term),
  KEY idx_dictionary_pair_evidence_source_seen (source_key, last_seen_at),
  CONSTRAINT fk_dictionary_pair_evidence_source FOREIGN KEY (source_key) REFERENCES dictionary_sources(source_key) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO dictionary_sources
  (source_key, display_name, source_type, endpoint, license_name, license_url, priority, is_enabled)
VALUES
  ('jawikipedia-redirects', '日本語Wikipediaリダイレクト', 'wikipedia', 'https://ja.wikipedia.org/w/api.php', 'CC BY-SA 4.0', 'https://creativecommons.org/licenses/by-sa/4.0/', 9, 1),
  ('jawiktionary-redirects', '日本語Wiktionaryリダイレクト', 'wiktionary', 'https://ja.wiktionary.org/w/api.php', 'CC BY-SA 4.0', 'https://creativecommons.org/licenses/by-sa/4.0/', 12, 1),
  ('wikidata-ja-aliases', 'Wikidata日本語別名', 'wikidata', 'https://www.wikidata.org/w/api.php', 'CC0 1.0', 'https://creativecommons.org/publicdomain/zero/1.0/', 8, 1),
  ('curated-ja-seeds', '管理済み日本語シード', 'curated', 'internal://curated-ja-seeds', 'Project data', '', 10, 1)
ON DUPLICATE KEY UPDATE
  display_name=VALUES(display_name), source_type=VALUES(source_type), endpoint=VALUES(endpoint),
  license_name=VALUES(license_name), license_url=VALUES(license_url), priority=VALUES(priority);

INSERT IGNORE INTO law_synonyms (canonical_term, synonym_term, priority, source_type, source_version) VALUES
  ('地方自治法', '自治法', 20, 'builtin', '0.1.0'),
  ('地方自治法', '自治体法', 12, 'builtin', '0.1.0'),
  ('本市', '美祢市', 8, 'builtin', '0.1.0'),
  ('例規', '条例', 14, 'builtin', '0.1.0'),
  ('例規', '規則', 12, 'builtin', '0.1.0'),
  ('例規', '要綱', 12, 'builtin', '0.1.0'),
  ('職員', '職員等', 8, 'builtin', '0.1.0'),
  ('休暇', '休業', 8, 'builtin', '0.1.0'),
  ('休み', '休暇', 6, 'builtin', '0.1.0'),
  ('手当', '給与', 8, 'builtin', '0.1.0'),
  ('会計年度任用職員', '会計年度職員', 18, 'builtin', '0.1.0'),
  ('任用職員', '会計年度任用職員', 12, 'builtin', '0.1.0'),
  ('議会', '議員', 8, 'builtin', '0.1.0'),
  ('個人情報', '個人情報保護', 14, 'builtin', '0.1.0'),
  ('情報公開', '開示', 10, 'builtin', '0.1.0');

CREATE TABLE IF NOT EXISTS law_document_history (
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS search_query_cache (
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS ask_query_cache (
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS usage_events (
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
