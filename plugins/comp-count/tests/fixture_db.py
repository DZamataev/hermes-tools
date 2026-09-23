#!/usr/bin/env python3
"""Build a throwaway state.db for the backend checks.

Only the columns comp-count reads are created. The real schema has ~50 more;
copying it here would rot silently against upstream, and the backend would not
notice either way because it names every column it selects.
"""
from __future__ import annotations

import sqlite3

SCHEMA = """
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    started_at REAL NOT NULL,
    title TEXT,
    cwd TEXT
);
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT,
    tool_name TEXT,
    timestamp REAL NOT NULL,
    display_kind TEXT,
    _compressed_summary INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    compacted INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE session_model_usage (
    session_id TEXT NOT NULL,
    model TEXT NOT NULL,
    billing_provider TEXT NOT NULL DEFAULT '',
    billing_base_url TEXT NOT NULL DEFAULT '',
    task TEXT NOT NULL DEFAULT '',
    api_call_count INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    first_seen REAL,
    last_seen REAL
);
"""


def build(path: str, session_id: str = "s1", started_at: float = 1_000_000.0) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO sessions (id, started_at, title, cwd) VALUES (?, ?, ?, ?)",
                 (session_id, started_at, "fixture", "/tmp"))
    return conn


def msg(conn, session_id, ts, role="user", *, content="hi", tool_name=None,
        display_kind=None, summary=0, active=1, compacted=0) -> None:
    conn.execute(
        "INSERT INTO messages (session_id, role, content, tool_name, timestamp,"
        " display_kind, _compressed_summary, active, compacted)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (session_id, role, content, tool_name, ts, display_kind, summary, active, compacted))


def route(conn, session_id, model, provider, first_seen, last_seen, *, task="", calls=1,
          base_url="", inp=0, out=0, cache_read=0, cache_write=0) -> None:
    conn.execute(
        "INSERT INTO session_model_usage (session_id, model, billing_provider, billing_base_url,"
        " task, api_call_count, input_tokens, output_tokens, cache_read_tokens,"
        " cache_write_tokens, first_seen, last_seen)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (session_id, model, provider, base_url, task, calls, inp, out, cache_read,
         cache_write, first_seen, last_seen))
