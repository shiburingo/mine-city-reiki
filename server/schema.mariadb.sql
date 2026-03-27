CREATE TABLE IF NOT EXISTS law_documents (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  source ENUM('mine-city','egov') NOT NULL,
  external_id VARCHAR(128) NOT NULL,
  title VARCHAR(255) NOT NULL,
  normalized_title VARCHAR(255) NOT NULL DEFAULT '',
  law_type VARCHAR(64) NOT NULL DEFAULT '',
  law_number VARCHAR(128) NOT NULL DEFAULT '',
  category_path VARCHAR(255) NOT NULL DEFAULT '',
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

CREATE TABLE IF NOT EXISTS sync_settings (
  id TINYINT UNSIGNED NOT NULL PRIMARY KEY,
  enabled TINYINT(1) NOT NULL DEFAULT 0,
  day_of_month TINYINT UNSIGNED NOT NULL DEFAULT 1,
  hour TINYINT UNSIGNED NOT NULL DEFAULT 3,
  minute TINYINT UNSIGNED NOT NULL DEFAULT 0,
  timezone VARCHAR(32) NOT NULL DEFAULT '+09:00',
  source_scope ENUM('all','mine-city','egov') NOT NULL DEFAULT 'all',
  last_started_at VARCHAR(64) NULL,
  last_finished_at VARCHAR(64) NULL,
  last_success_at VARCHAR(64) NULL,
  last_error TEXT NULL,
  updated_by VARCHAR(128) NOT NULL DEFAULT '',
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

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
