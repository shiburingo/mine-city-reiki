"""Literal, grouped minutes search over an atomically published FTS5 snapshot."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
import unicodedata
import zlib
from contextlib import closing
from pathlib import Path
from typing import Any, Iterable

SEARCH_VERSION = "minutes-fts-bigram-v1"
TermGroups = list[list[tuple[str, int]]]
SYNONYM_SOURCES = {"manual", "curated", "wordnet", "wikidata", "wiktionary", "wikipedia", "internet"}
EXACT_EXECUTIVE_TITLE_FILTERS = {"市長", "副市長", "教育長", "病院事業管理者", "代表監査委員", "会計管理者", "消防長"}


def normalize(value: str) -> str:
    return display_text(value).lower()


def display_text(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value or "")).strip()


def query_groups(query: str, related: bool = False, lookup=None) -> TermGroups:
    originals = sorted(set(normalize(query).split()))
    if len(originals) > 16:
        raise ValueError("検索語は16語以内で指定してください。")
    groups: TermGroups = []
    for original in originals:
        peers: dict[str, int] = {}
        if related and lookup is not None:
            for term, priority, source in lookup(original):
                term = normalize(term)
                # A containing phrase adds no substring recall. Domain extraction
                # relates organizations/topics; it does not establish synonyms.
                if not term or original in term or source not in SYNONYM_SOURCES or priority < 8:
                    continue
                peers[term] = max(peers.get(term, 0), min(900, int(priority) * 10))
        alternatives = sorted(peers.items(), key=lambda item: (-item[1], len(item[0]), item[0]))[:5]
        groups.append([(original, 1000), *alternatives])
    return groups


def flatten_groups(groups: TermGroups) -> list[tuple[str, int]]:
    weights: dict[str, int] = {}
    for group in groups:
        for term, weight in group:
            weights[term] = max(weights.get(term, 0), weight)
    return sorted(weights.items(), key=lambda item: (-item[1], item[0]))


def presence_sql(groups: TermGroups, op: str, column: str, placeholder: str = "?") -> tuple[str, list[Any]]:
    params: list[Any] = []
    clauses = []
    for group in groups:
        clauses.append("(" + " OR ".join(f"INSTR({column}, {placeholder}) > 0" for _ in group) + ")")
        params.extend(term for term, _ in group)
    return (" OR " if op == "OR" else " AND ").join(clauses) or "1=1", params


def score_sql(groups: TermGroups, column: str, placeholder: str = "?", greatest: str = "MAX") -> tuple[str, list[Any]]:
    clauses, params = [], []
    for group in groups:
        cases = []
        for term, weight in group:
            cases.append(f"CASE WHEN INSTR({column}, {placeholder}) > 0 THEN {placeholder} ELSE 0 END")
            params.extend([term, weight])
        # Several synonyms for the same concept must not inflate relevance.
        clauses.append(cases[0] if len(cases) == 1 else f"{greatest}(" + ",".join(cases) + ")")
    return " + ".join(clauses) or "0", params


def token(value: str) -> str:
    return "x" + value.encode("utf-8").hex()


def index_tokens(text: str) -> str:
    # No cap: even the last word in a very long utterance is indexed.
    grams = set(text) | {text[i:i + 2] for i in range(len(text) - 1)}
    return " ".join(sorted(token(gram) for gram in grams))


def fts_query(groups: TermGroups, op: str, include_meta: bool) -> str:
    clauses = []
    for group in groups:
        peers = []
        for term, _ in group:
            grams = [term] if len(term) == 1 else [term[i:i + 2] for i in range(len(term) - 1)]
            expression = " AND ".join(sorted({token(gram) for gram in grams}))
            peers.append(f"({expression})")
        clauses.append("(" + " OR ".join(peers) + ")")
    expression = (" OR " if op == "OR" else " AND ").join(clauses)
    return expression if include_meta else f"body : ({expression})"


def snapshot_path(root: Path, version_id: int) -> Path:
    return root / f"minutes-search-{int(version_id)}.sqlite3"


def build_snapshot(rows: Iterable[dict[str, Any]], root: Path, version_id: int) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    target = snapshot_path(root, version_id)
    fd, name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=root)
    os.close(fd)
    temp = Path(name)
    count = 0
    try:
        with closing(sqlite3.connect(temp)) as db, db:
            db.executescript("""
                PRAGMA journal_mode=OFF;
                PRAGMA synchronous=OFF;
                CREATE TABLE meta (version TEXT, compile_id INTEGER, documents INTEGER);
                CREATE TABLE documents (
                    id INTEGER PRIMARY KEY, day_id INTEGER, session_id INTEGER,
                    meeting_date TEXT, utterance_order INTEGER, section TEXT,
                    speaker_name TEXT, speaker_title TEXT, speaker_role TEXT,
                    body TEXT, speaker TEXT, display BLOB, payload TEXT
                );
                CREATE VIRTUAL TABLE terms USING fts5(body, speaker, content='', detail=column, tokenize='ascii');
            """)
            for row in rows:
                body = display_text(row.get("body_search_text") or row.get("text") or "")
                folded_body = body.lower()
                display = zlib.compress(body.encode("utf-8"), 1) if body != folded_body else None
                name, title = row.get("speaker_name") or "", row.get("speaker_title") or ""
                speaker = normalize(f"{title} {name}")
                uid = int(row.get("utterance_id") or row["id"])
                payload = {key: value for key, value in row.items() if key not in {"body_search_text", "search_text", "display_text", "text"}}
                payload["id"] = uid
                db.execute("INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                    uid, row["day_id"], row["session_id"], str(row.get("meeting_date") or ""), row["utterance_order"],
                    row.get("section") or "", name, title, row.get("speaker_role") or "unknown", folded_body, speaker, display,
                    json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":")),
                ))
                db.execute("INSERT INTO terms(rowid, body, speaker) VALUES (?,?,?)", (uid, index_tokens(body.lower()), index_tokens(speaker)))
                count += 1
                if count % 1000 == 0:
                    db.commit()
            db.execute("CREATE INDEX documents_order ON documents(meeting_date DESC,day_id,utterance_order,id)")
            db.execute("CREATE INDEX documents_speaker ON documents(speaker_name)")
            db.execute("CREATE INDEX documents_day ON documents(day_id)")
            db.execute("INSERT INTO meta VALUES (?,?,?)", (SEARCH_VERSION, version_id, count))
            db.execute("INSERT INTO terms(terms) VALUES ('optimize')")
            db.execute("INSERT INTO terms(terms) VALUES ('integrity-check')")
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise RuntimeError("Minutes search snapshot failed integrity check")
        with temp.open("rb") as handle:
            os.fsync(handle.fileno())
        os.chmod(temp, 0o640)
        os.replace(temp, target)
        return {"version": SEARCH_VERSION, "compileId": version_id, "documents": count, "bytes": target.stat().st_size}
    finally:
        temp.unlink(missing_ok=True)


def search_snapshot(root: Path, version_id: int, groups: TermGroups, *, op: str = "AND", related: bool = False,
                    include_meta: bool = False, limit: int | None = 60, cursor=None, speaker: str = "", role: str = "",
                    section: str = "", from_date: str = "", to_date: str = "", years=None,
                    meeting_id=None, day_id=None) -> list[dict[str, Any]] | None:
    path = snapshot_path(root, version_id)
    if not path.exists():
        return None
    with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro&immutable=1", uri=True)) as db:
        db.row_factory = sqlite3.Row
        meta = db.execute("SELECT * FROM meta").fetchone()
        if meta["version"] != SEARCH_VERSION or meta["compile_id"] != version_id:
            return None
        db.execute("PRAGMA mmap_size=134217728")
        column = "(d.body || ' ' || d.speaker)" if include_meta else "d.body"
        presence, presence_params = presence_sql(groups, op, column)
        score, score_params = score_sql(groups, column) if related else ("0", [])
        predicates, params = [f"({presence})"], list(presence_params)
        join = ""
        if groups:
            join = "JOIN terms ON terms.rowid=d.id"
            predicates.append("terms MATCH ?")
            params.append(fts_query(groups, op, include_meta))
        for field, value in [("day_id", day_id), ("session_id", meeting_id), ("section", section if section != "all" else "")]:
            if value:
                predicates.append(f"d.{field}=?")
                params.append(value)
        if speaker:
            exists = db.execute("SELECT 1 FROM documents WHERE speaker_name=? LIMIT 1", (speaker,)).fetchone()
            if exists:
                predicates.append("d.speaker_name=?")
                params.append(speaker)
            else:
                predicates.append("(INSTR(d.speaker_name,?)>0 OR INSTR(d.speaker_title,?)>0)")
                params.extend([speaker, speaker])
        if role and role != "all":
            if role.startswith("title:"):
                title = role.removeprefix("title:").strip()
                if title:
                    predicates.append("d.speaker_role='answerer'")
                    if title in EXACT_EXECUTIVE_TITLE_FILTERS:
                        predicates.append("d.speaker_title=?")
                        params.append(title)
                    else:
                        predicates.append("SUBSTR(d.speaker_title,-LENGTH(?))=?")
                        params.extend([title, title])
            else:
                predicates.append("d.speaker_role=?")
                params.append(role)
        if years:
            predicates.append("(" + " OR ".join("d.meeting_date BETWEEN ? AND ?" for _ in years) + ")")
            for year in years:
                params.extend([f"{year}-01-01", f"{year}-12-31"])
        for comparison, value in [(">=", from_date), ("<=", to_date)]:
            if value:
                predicates.append(f"d.meeting_date {comparison} ?")
                params.append(value)
        after, after_params = "", []
        if cursor:
            after = """WHERE match_score < ? OR (match_score=? AND (
                meeting_date < ? OR (meeting_date=? AND (day_id,utterance_order,id) > (?,?,?))))"""
            after_params = [cursor.get("match_score", 0), cursor.get("match_score", 0), cursor["meeting_date"],
                            cursor["meeting_date"], cursor["day_id"], cursor["utterance_order"], cursor["utterance_id"]]
        sql = f"""WITH candidates AS (
            SELECT d.*, ({score}) AS match_score FROM documents d {join} WHERE {' AND '.join(predicates)}
        ) SELECT * FROM candidates {after}
        ORDER BY match_score DESC, meeting_date DESC, day_id, utterance_order, id"""
        if limit is not None:
            sql += " LIMIT ?"
            after_params.append(limit)
        results = []
        for row in db.execute(sql, score_params + params + after_params):
            payload = json.loads(row["payload"])
            body_lower = row["body"]
            body = zlib.decompress(row["display"]).decode("utf-8") if row["display"] is not None else body_lower
            terms = [term for term, _ in flatten_groups(groups)]
            first = next((body_lower.find(term) for term in terms if term in body_lower), 0)
            start = max(0, first - 55)
            payload.update(text=body[start:start + 260], match_score=row["match_score"],
                           hit_scope="body" if not groups or any(term in body_lower for term in terms) else "speaker")
            results.append(payload)
        return results
