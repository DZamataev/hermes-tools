"""comp-count — backend routes, mounted at /api/plugins/comp-count/.

Reads the focused session's rows out of the profile's own ``state.db`` and
returns the working segments the desktop popover draws.

Why a backend at all: the renderer's only historical door,
``host.request('session.history')``, strips exactly the two row kinds this
feature is built on — model-switch notices (dropped as ``[System:`` markers)
and compaction handoffs (dropped by the display projection). The database still
has both.

The store is opened READ-ONLY. This plugin never writes to a session store.
"""
from __future__ import annotations

import logging
import sqlite3
import time
from collections import Counter
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

log = logging.getLogger(__name__)

router = APIRouter()

# The pause that separates one working stretch from the next.
GAP_SECONDS = 3600.0
# Sparkline width. The renderer draws exactly this many glyphs.
SPARK_BUCKETS = 20
# How many tools a segment names. Three fits the panel and answers "what was
# this stretch made of" without turning the row into a table.
TOP_TOOLS = 3

# Display kinds that are machinery, never an operator prompt.
_MACHINE_KINDS = ("hidden", "auto_continue", "model_switch")


def _is_prompt(role: str, content: str, display_kind: Optional[str], compaction: bool) -> bool:
    """A message the OPERATOR actually sent.

    Excluding compaction carriers is not cosmetic: a handoff is persisted as a
    ``role='user'`` row, and counting it as a prompt reported 74 prompts on a
    real session that has 58.
    """
    if compaction or role != "user":
        return False
    if display_kind in _MACHINE_KINDS:
        return False
    # Gateway notices (model switch, personality) persist as role=user
    # "[System: ...]" so strict providers accept them mid-history.
    return not (content or "").lstrip().startswith("[System:")


def _read_events(conn: sqlite3.Connection, session_id: str) -> List[Dict[str, Any]]:
    """Every display-visible row of the session, oldest first.

    ``active = 1 OR compacted = 1`` is the display read: compaction-archived
    history is most of a long session, and ``active = 1`` alone returned 13 of
    one real session's 58 prompts.
    """
    rows = conn.execute(
        "SELECT role, timestamp, display_kind, tool_name, _compressed_summary,"
        "       substr(content, 1, 80) AS head"
        "  FROM messages"
        " WHERE session_id = ? AND (active = 1 OR compacted = 1) AND timestamp > 0"
        " ORDER BY timestamp, id",
        (session_id,),
    ).fetchall()

    events = []
    for role, ts, kind, tool_name, summary, head in rows:
        compaction = bool(summary)
        events.append({
            "ts": float(ts),
            "compaction": compaction,
            "tool": tool_name if role == "tool" else None,
            "prompt": _is_prompt(role or "", head or "", kind, compaction),
        })
    return events


def _read_routes(conn: sqlite3.Connection, session_id: str) -> List[Dict[str, Any]]:
    """Main-loop model routes, oldest first.

    ``task = ''`` only: auxiliary work (titles, vision, compression, background
    review) runs on other models — one real session compresses on
    ``glm-4.5-flash`` — and must never be presented as a model the operator chose.
    """
    rows = conn.execute(
        "SELECT model, billing_provider, first_seen, last_seen"
        "  FROM session_model_usage"
        " WHERE session_id = ? AND task = '' AND model <> 'unknown'"
        "   AND first_seen IS NOT NULL AND last_seen IS NOT NULL"
        " ORDER BY first_seen, rowid",
        (session_id,),
    ).fetchall()
    return [{"model": model, "provider": provider or "",
             "first_seen": float(first), "last_seen": float(last)}
            for model, provider, first, last in rows]


def _cluster(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Split the session at pauses longer than ``GAP_SECONDS``.

    Clustering runs over ALL activity, not prompts alone: a turn that keeps
    running tools for an hour after one prompt is a single working stretch.
    Clustering on prompts tore those turns apart and produced segments that
    overlapped each other. Non-overlapping is a property of this construction.
    """
    clusters: List[Dict[str, Any]] = []
    for event in events:
        if clusters and event["ts"] - clusters[-1]["end"] <= GAP_SECONDS:
            clusters[-1]["end"] = event["ts"]
        else:
            clusters.append({"start": event["ts"], "end": event["ts"], "events": []})
        clusters[-1]["events"].append(event)
    return clusters


def _bucket(cluster: Dict[str, Any]) -> List[int]:
    """Raw event counts per time bucket. The renderer scales them to glyphs."""
    span = max(cluster["end"] - cluster["start"], 1.0)
    buckets = [0] * SPARK_BUCKETS
    for event in cluster["events"]:
        index = int((event["ts"] - cluster["start"]) / span * SPARK_BUCKETS)
        buckets[min(SPARK_BUCKETS - 1, max(0, index))] += 1
    return buckets


def _segment(cluster: Dict[str, Any], routes: List[Dict[str, Any]]) -> Dict[str, Any]:
    events = cluster["events"]
    tools = Counter(e["tool"] for e in events if e["tool"])
    # A route matches when its window overlaps the segment. Several may match:
    # session_model_usage is an aggregate, so a route returning later keeps its
    # original first_seen. The SET is sound; the order within is not, and
    # clamping windows to fake an order left real segments with no route at all.
    hit = [r for r in routes
           if r["first_seen"] <= cluster["end"] and r["last_seen"] >= cluster["start"]]
    seen, unique = set(), []
    for route in hit:
        key = (route["model"], route["provider"])
        if key not in seen:
            seen.add(key)
            unique.append({"model": route["model"], "provider": route["provider"]})
    buckets = _bucket(cluster)
    return {
        "start": cluster["start"],
        "end": cluster["end"],
        "prompts": sum(1 for e in events if e["prompt"]),
        "toolCalls": sum(1 for e in events if e["tool"]),
        "compactions": [e["ts"] for e in events if e["compaction"]],
        "routes": unique,
        "topTools": [{"name": name, "count": count} for name, count in tools.most_common(TOP_TOOLS)],
        "buckets": buckets,
        "peak": max(buckets) or 1,
    }


def build_timeline(db_path: str, session_id: str) -> Dict[str, Any]:
    """Working segments of ``session_id``, newest last.

    Takes a filesystem path rather than an open handle so the checks can run
    against a synthetic store with no Hermes profile in sight.
    """
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        events = _read_events(conn, session_id)
        routes = _read_routes(conn, session_id)
    finally:
        conn.close()

    segments = []
    for cluster in _cluster(events):
        prompts = [e["ts"] for e in cluster["events"] if e["prompt"]]
        # No operator prompt means this stretch was machinery — a resume, a
        # background review. The operator was not at the keyboard.
        if not prompts:
            continue
        cluster["start"] = min(prompts)
        cluster["events"] = [e for e in cluster["events"] if e["ts"] >= cluster["start"]]
        segments.append(_segment(cluster, routes))

    return {
        "sessionId": session_id,
        "segments": segments,
        "generatedAt": int(time.time() * 1000),
    }


@router.get("/timeline")
async def timeline(
    session: str = Query("", description="Stored session id"),
    profile: Optional[str] = Query(None, description="Owning profile"),
):
    if not session.strip():
        return {"sessionId": "", "segments": [], "generatedAt": int(time.time() * 1000)}

    from hermes_cli.web_server_sessions import _session_db_path_for_profile

    path = str(_session_db_path_for_profile(profile))
    try:
        return build_timeline(path, session.strip())
    except sqlite3.DatabaseError as exc:
        # A corrupt or unreadable store is an operational fault, not a bad
        # request: say so plainly rather than returning an empty timeline that
        # reads as "this session did nothing".
        log.warning("comp-count: cannot read %s: %s", path, exc)
        raise HTTPException(status_code=503, detail="Session store is unavailable") from exc
