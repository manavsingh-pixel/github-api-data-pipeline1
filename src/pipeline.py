"""Small, rerunnable GitHub public API -> SQLite -> reporting pipeline."""
from __future__ import annotations
import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = Path(os.getenv("DB_PATH", str(ROOT / "data" / "github_pipeline.db")))
API_URL = "https://api.github.com/search/repositories"
PER_PAGE = int(os.getenv("PER_PAGE", "100"))
MAX_PAGES = int(os.getenv("MAX_PAGES", "3"))
QUERY = os.getenv("GITHUB_QUERY", "stars:>50")
LOG = logging.getLogger("github_pipeline")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def snapshot_day() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS raw_api_responses (
        raw_id INTEGER PRIMARY KEY AUTOINCREMENT,
        endpoint TEXT NOT NULL,
        page INTEGER NOT NULL,
        fetched_at TEXT NOT NULL,
        status_code INTEGER,
        payload_json TEXT,
        error_message TEXT
    );
    CREATE TABLE IF NOT EXISTS repositories (
        snapshot_date TEXT NOT NULL,
        repo_id INTEGER NOT NULL,
        full_name TEXT NOT NULL,
        html_url TEXT,
        description TEXT,
        language TEXT NOT NULL,
        stars INTEGER NOT NULL CHECK (stars >= 0),
        forks INTEGER NOT NULL CHECK (forks >= 0),
        open_issues INTEGER NOT NULL CHECK (open_issues >= 0),
        created_at TEXT,
        updated_at TEXT,
        pushed_at TEXT,
        license_name TEXT,
        is_archived INTEGER NOT NULL CHECK (is_archived IN (0,1)),
        ingested_at TEXT NOT NULL,
        PRIMARY KEY (snapshot_date, repo_id)
    );
    CREATE TABLE IF NOT EXISTS language_summary (
        snapshot_date TEXT NOT NULL,
        language TEXT NOT NULL,
        repo_count INTEGER NOT NULL,
        total_stars INTEGER NOT NULL,
        avg_stars REAL NOT NULL,
        open_issues_total INTEGER NOT NULL,
        PRIMARY KEY (snapshot_date, language)
    );
    CREATE INDEX IF NOT EXISTS idx_repositories_snapshot ON repositories(snapshot_date);
    CREATE INDEX IF NOT EXISTS idx_repositories_language ON repositories(language);
    """)
    conn.commit()


def normalize_repo(item: dict[str, Any], day: str, ingested_at: str) -> dict[str, Any] | None:
    """Validate required identity; normalize absent optional fields to safe values."""
    repo_id = item.get("id")
    full_name = item.get("full_name")
    if not isinstance(repo_id, int) or not isinstance(full_name, str) or not full_name.strip():
        return None
    def nonnegative_int(key: str) -> int:
        value = item.get(key, 0)
        try:
            parsed = int(value or 0)
            return max(parsed, 0)
        except (TypeError, ValueError):
            return 0
    license_obj = item.get("license") or {}
    if not isinstance(license_obj, dict):
        license_obj = {}
    language = item.get("language")
    return {
        "snapshot_date": day,
        "repo_id": repo_id,
        "full_name": full_name.strip(),
        "html_url": item.get("html_url"),
        "description": item.get("description"),
        "language": language.strip() if isinstance(language, str) and language.strip() else "Unknown",
        "stars": nonnegative_int("stargazers_count"),
        "forks": nonnegative_int("forks_count"),
        "open_issues": nonnegative_int("open_issues_count"),
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
        "pushed_at": item.get("pushed_at"),
        "license_name": license_obj.get("spdx_id") or license_obj.get("name"),
        "is_archived": int(bool(item.get("archived", False))),
        "ingested_at": ingested_at,
    }


def fetch_page(session: requests.Session, page: int) -> tuple[int, dict[str, Any]]:
    params = {"q": QUERY, "sort": "stars", "order": "desc", "per_page": PER_PAGE, "page": page}
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    last_error = None
    for attempt in range(3):
        try:
            response = session.get(API_URL, params=params, headers=headers, timeout=20)
            if response.status_code in (429, 500, 502, 503, 504):
                retry_after = int(response.headers.get("Retry-After", "0") or 0)
                time.sleep(max(retry_after, 2 ** attempt))
                continue
            if response.status_code == 403 and response.headers.get("X-RateLimit-Remaining") == "0":
                reset = int(response.headers.get("X-RateLimit-Reset", "0") or 0)
                wait = max(1, min(reset - int(time.time()), 60))
                if attempt < 2:
                    time.sleep(wait)
                    continue
            response.raise_for_status()
            body = response.json()
            if not isinstance(body, dict) or not isinstance(body.get("items"), list):
                raise ValueError("API response did not contain an items list")
            return response.status_code, body
        except (requests.RequestException, ValueError) as exc:
            last_error = str(exc)
            if attempt < 2:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"Could not fetch page {page} after retries: {last_error}")


def record_raw(conn: sqlite3.Connection, page: int, status: int | None,
               payload: dict[str, Any] | None, error: str | None) -> None:
    conn.execute("""INSERT INTO raw_api_responses
      (endpoint, page, fetched_at, status_code, payload_json, error_message)
      VALUES (?, ?, ?, ?, ?, ?)""",
      (API_URL, page, utc_now(), status,
       json.dumps(payload, ensure_ascii=False) if payload is not None else None, error))


def write_repositories(conn: sqlite3.Connection, rows: list[dict[str, Any]], day: str) -> None:
    # Re-running on the same UTC day replaces that day's snapshot, avoiding duplicate rows.
    conn.execute("DELETE FROM repositories WHERE snapshot_date = ?", (day,))
    columns = list(rows[0].keys()) if rows else []
    if rows:
        sql = f"INSERT INTO repositories ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})"
        conn.executemany(sql, [[row[col] for col in columns] for row in rows])
    conn.execute("DELETE FROM language_summary WHERE snapshot_date = ?", (day,))
    conn.execute("""INSERT INTO language_summary
      (snapshot_date, language, repo_count, total_stars, avg_stars, open_issues_total)
      SELECT snapshot_date, language, COUNT(*), SUM(stars), AVG(stars), SUM(open_issues)
      FROM repositories WHERE snapshot_date = ? GROUP BY snapshot_date, language""", (day,))


def run_pipeline() -> dict[str, Any]:
    day, started = snapshot_day(), utc_now()
    session = requests.Session()
    conn = connect()
    init_db(conn)
    raw_count = clean_count = invalid_count = 0
    unique: dict[int, dict[str, Any]] = {}
    try:
        for page in range(1, MAX_PAGES + 1):
            try:
                status, payload = fetch_page(session, page)
                with conn:
                    record_raw(conn, page, status, payload, None)
                raw_count += 1
            except Exception as exc:
                with conn:
                    record_raw(conn, page, None, None, str(exc))
                LOG.exception("Page %s failed; continuing with pages already fetched", page)
                break
            items = payload.get("items", [])
            if not items:
                break
            for item in items:
                row = normalize_repo(item, day, started)
                if row is None:
                    invalid_count += 1
                    continue
                # API search pages can overlap when data changes during pagination.
                unique[row["repo_id"]] = row
            if len(items) < PER_PAGE or len(unique) >= int(payload.get("total_count", 0)):
                break
        rows = list(unique.values())
        if not rows:
            raise RuntimeError("No valid repository records were retrieved; refusing to overwrite the snapshot")
        with conn:
            write_repositories(conn, rows, day)
        clean_count = len(rows)
        summary = conn.execute("SELECT COUNT(*) AS n, SUM(stars) AS stars FROM repositories WHERE snapshot_date=?", (day,)).fetchone()
        LOG.info("Pipeline complete: raw_pages=%s clean_repos=%s invalid=%s", raw_count, clean_count, invalid_count)
        return {"snapshot_date": day, "raw_pages_saved": raw_count, "repositories_loaded": clean_count,
                "invalid_records_skipped": invalid_count, "total_stars_in_snapshot": summary["stars"] or 0,
                "database": str(DB_PATH)}
    finally:
        conn.close()
        session.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        print(json.dumps(run_pipeline(), indent=2))
    except Exception as exc:
        LOG.exception("Pipeline failed: %s", exc)
        raise SystemExit(1)
