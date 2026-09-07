"""SQLite 저장소. 신규/변경 판정과 중복 알림 방지를 담당한다."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from .config import DB_PATH
from .models import Listing

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    uid           TEXT PRIMARY KEY,
    source        TEXT NOT NULL,
    source_id     TEXT NOT NULL,
    category      TEXT,
    title         TEXT,
    region        TEXT,
    supplier      TEXT,
    notice_date   TEXT,
    apply_start   TEXT,
    apply_end     TEXT,
    status        TEXT,
    url           TEXT,
    content_hash  TEXT NOT NULL,
    raw           TEXT,
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL,
    notified_hash TEXT
);
CREATE INDEX IF NOT EXISTS idx_listings_source  ON listings(source);
CREATE INDEX IF NOT EXISTS idx_listings_end     ON listings(apply_end);

CREATE TABLE IF NOT EXISTS runs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT,
    source     TEXT,
    fetched    INTEGER,
    new_count  INTEGER,
    upd_count  INTEGER,
    error      TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def upsert_many(con: sqlite3.Connection, listings: list[Listing]) -> list[tuple[Listing, str]]:
    """저장하고, 알릴 가치가 있는 것만 (공고, 'new'|'updated')로 돌려준다."""
    now = _now()
    changes: list[tuple[Listing, str]] = []

    for item in listings:
        h = item.content_hash
        row = con.execute(
            "SELECT content_hash, notified_hash FROM listings WHERE uid = ?", (item.uid,)
        ).fetchone()

        # 판정 기준은 저장된 내용이 아니라 '알림을 보낸 내용'이다.
        # 이렇게 해야 전송이 실패했을 때(=notified_hash 미갱신) 다음 실행에서
        # 다시 잡힌다. content_hash 로만 비교하면 실패한 건이 영영 묻힌다.
        if row is None:
            change = "new"
        elif row["notified_hash"] == h:
            change = None                       # 이미 이 내용으로 알렸다
        elif row["notified_hash"] is None:
            change = "new"                      # 한 번도 알린 적 없음(첫 전송 실패 포함)
        else:
            change = "updated"

        con.execute(
            """
            INSERT INTO listings (uid, source, source_id, category, title, region, supplier,
                                  notice_date, apply_start, apply_end, status, url,
                                  content_hash, raw, first_seen, last_seen, notified_hash)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)
            ON CONFLICT(uid) DO UPDATE SET
                category=excluded.category, title=excluded.title, region=excluded.region,
                supplier=excluded.supplier, notice_date=excluded.notice_date,
                apply_start=excluded.apply_start, apply_end=excluded.apply_end,
                status=excluded.status, url=excluded.url,
                content_hash=excluded.content_hash, raw=excluded.raw,
                last_seen=excluded.last_seen
            """,
            (item.uid, item.source, item.source_id, item.category, item.title, item.region,
             item.supplier, item.notice_date, item.apply_start, item.apply_end, item.status,
             item.url, h, json.dumps(item.raw, ensure_ascii=False), now, now),
        )

        if change:
            changes.append((item, change))

    con.commit()
    return changes


def mark_notified(con: sqlite3.Connection, listings: list[Listing]) -> None:
    con.executemany(
        "UPDATE listings SET notified_hash = ? WHERE uid = ?",
        [(i.content_hash, i.uid) for i in listings],
    )
    con.commit()


def log_run(con, source: str, fetched: int, new_count: int, upd_count: int, error: str = "") -> None:
    con.execute(
        "INSERT INTO runs (started_at, source, fetched, new_count, upd_count, error) VALUES (?,?,?,?,?,?)",
        (_now(), source, fetched, new_count, upd_count, error),
    )
    con.commit()
