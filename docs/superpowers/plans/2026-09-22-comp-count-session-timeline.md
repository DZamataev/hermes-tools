# comp-count Session Timeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the `comp-count` status-bar chip into a popover that lists the focused session as working segments — each with a density sparkline, positioned compaction markers, its model/provider route, and what it was made of.

**Architecture:** `comp-count` moves from a renderer-only disk plugin to a unified package with two halves, exactly like the `provider-limits` package already in this repository. A Python half (`dashboard/plugin_api.py`) reads the profile's `state.db` read-only and returns normalised segments over `ctx.rest`; a renderer half (`desktop/plugin.js`) draws the chip and popover. The renderer never touches the database, and the backend never renders.

**Tech Stack:** Python 3.11 + FastAPI `APIRouter` (backend, run by Hermes's own venv), plain ESM + `@hermes/plugin-sdk` + `react/jsx-runtime` (renderer, loaded uncompiled — no JSX, no build step), `bun test` and stub-SDK Python probes for tests.

**Spec:** `docs/superpowers/specs/2026-09-22-comp-count-session-timeline-design.md`

## Global Constraints

- **Plugin id is `comp-count`.** The package directory, `plugin.yaml` `name`, `manifest.json` `name`, and the renderer's exported `id` must all read exactly `comp-count`, or the loader will not pair the halves.
- **No JSX anywhere in `desktop/plugin.js`.** The disk file is loaded uncompiled. Build UI with `jsx()` / `jsxs()` from `react/jsx-runtime`.
- **Only three import specifiers resolve in the renderer:** `@hermes/plugin-sdk`, `react`, `react/jsx-runtime`. Anything else fails to load the plugin.
- **Colors are `var(--ui-*)` theme variables only, never literals.** A disk plugin is not scanned by Tailwind; a hardcoded color breaks on theme switch.
- **The database is opened read-only.** Use `_open_session_db_for_profile(profile, read_only=True)` from `hermes_cli.web_server_sessions`, and always close it.
- **Message reads use `active = 1 OR compacted = 1`.** `active = 1` alone returns 13 of one real session's 58 prompts.
- **`GAP_SECONDS = 3600.0`** — the pause that splits one working segment from the next.
- **`SPARK_BUCKETS = 20`** — the sparkline width, everywhere.
- **`BLOCKS = " ▁▂▃▄▅▆▇█"`** — bucket glyphs, index 0 unused for non-empty buckets (see the floor rule in Task 3).
- **Panel strings are Russian.** The existing `provider-limits` panel is English and stays English; do not touch it.
- **Never write a secret, key, or token into this package.** Before any commit that touches tracked files, `gitleaks dir --redact .` must report no new findings beyond the pre-existing ones in `.env.local` and the `hermes-webui/` submodule fixtures.

---

## File Structure

| Path | Responsibility |
|---|---|
| `plugins/comp-count/plugin.yaml` | Package manifest for the agent-side plugin registry |
| `plugins/comp-count/dashboard/manifest.json` | Declares `"api": "plugin_api.py"` so the route mounts |
| `plugins/comp-count/dashboard/plugin_api.py` | Reads `state.db`, builds segments, serves `GET /timeline` |
| `plugins/comp-count/desktop/plugin.js` | Chip + popover; pure formatting helpers exported for tests |
| `plugins/comp-count/tests/run.sh` | Entry point for both suites |
| `plugins/comp-count/tests/fixture_db.py` | Builds a synthetic `state.db` for backend tests |
| `plugins/comp-count/tests/test_backend.py` | Segment/prompt/route rules |
| `plugins/comp-count/tests/test_renderer.py` | Sparkline, markers, guards, chip regression |
| `desktop-plugins/comp-count/` | **Removed** — the old renderer-only copy |
| `setup_hermes_tools.sh` | Installs the package, sets the gate, removes the stale copy |
| `scripts/check.mjs` | Points the `comp-count` suite at the new path |
| `README.md` | Layout section reflects the move |

---

### Task 1: Backend skeleton and the timeline route

**Files:**
- Create: `plugins/comp-count/plugin.yaml`
- Create: `plugins/comp-count/dashboard/manifest.json`
- Create: `plugins/comp-count/dashboard/plugin_api.py`
- Create: `plugins/comp-count/tests/fixture_db.py`
- Create: `plugins/comp-count/tests/test_backend.py`
- Create: `plugins/comp-count/tests/run.sh`

**Interfaces:**
- Consumes: nothing (first task).
- Produces:
  - `build_timeline(db_path: str, session_id: str) -> dict` — the pure core, taking a filesystem path so tests need no Hermes profile. Returns
    `{"sessionId": str, "segments": [...], "generatedAt": int}` where each segment is
    `{"start": float, "end": float, "prompts": int, "toolCalls": int, "compactions": [float], "routes": [{"model": str, "provider": str}], "topTools": [{"name": str, "count": int}], "buckets": [int], "peak": int}`.
    `start`/`end` are Unix seconds; `buckets` has exactly 20 entries of raw event counts; `peak` is `max(buckets)` or 1.
  - `GAP_SECONDS = 3600.0`, `SPARK_BUCKETS = 20` — module constants the tests import.
  - `router` — a FastAPI `APIRouter` exposing `GET /timeline`.

- [ ] **Step 1: Create the package manifests**

`plugins/comp-count/plugin.yaml`:

```yaml
name: comp-count
version: 0.2.0
description: "Session timeline (models, providers, working segments, compactions) in the desktop status bar."
author: "Denis"
kind: standalone
```

`plugins/comp-count/dashboard/manifest.json`:

```json
{
  "name": "comp-count",
  "label": "Session timeline",
  "description": "Models, providers and working segments of the focused session",
  "icon": "Activity",
  "version": "0.2.0",
  "tab": { "path": "/comp-count", "hidden": true },
  "api": "plugin_api.py"
}
```

- [ ] **Step 2: Write the fixture builder**

`plugins/comp-count/tests/fixture_db.py` — a synthetic store, so the tests assert on numbers they control. The columns match the real schema for the fields the backend reads.

```python
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
    task TEXT NOT NULL DEFAULT '',
    api_call_count INTEGER NOT NULL DEFAULT 0,
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


def route(conn, session_id, model, provider, first_seen, last_seen, *, task="", calls=1) -> None:
    conn.execute(
        "INSERT INTO session_model_usage (session_id, model, billing_provider, task,"
        " api_call_count, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (session_id, model, provider, task, calls, first_seen, last_seen))
```

- [ ] **Step 3: Write the failing backend test**

`plugins/comp-count/tests/test_backend.py`. Each case below is a defect the design probes actually produced against the live store — the comments say which.

```python
#!/usr/bin/env python3
"""Backend checks: a synthetic state.db in, normalised segments out.

Every case is a rule that measurably failed during design probing against the
real store. Run via ``tests/run.sh``.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

HERMES = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "/Users/frenzy/.hermes/hermes-agent")
sys.path.insert(0, str(HERMES))

spec = importlib.util.spec_from_file_location("cc_api", ROOT / "dashboard" / "plugin_api.py")
cc = importlib.util.module_from_spec(spec)
sys.modules["cc_api"] = cc
spec.loader.exec_module(cc)

import fixture_db as fx

failures: list[str] = []
checks = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if not condition:
        failures.append(f"{name}{f' — {detail}' if detail else ''}")


def store(fn):
    """Build a fixture db, run fn(conn, sid), return build_timeline's output."""
    path = tempfile.mktemp(suffix=".db")
    conn = fx.build(path)
    fn(conn, "s1")
    conn.commit()
    conn.close()
    return cc.build_timeline(path, "s1")


H = 3600.0
T0 = 1_000_000.0

# --- clustering -------------------------------------------------------------

# One prompt followed by an hour of tool rows is ONE working stretch. Clustering
# on prompts alone tore such turns apart and produced OVERLAPPING segments.
def one_long_turn(conn, sid):
    fx.msg(conn, sid, T0, "user")
    for i in range(1, 60):
        fx.msg(conn, sid, T0 + i * 60, "tool", tool_name="terminal")
    fx.route(conn, sid, "m1", "p1", T0, T0 + 3600)

out = store(one_long_turn)
check("one-turn-one-segment", len(out["segments"]) == 1, str(len(out["segments"])))
check("segment-spans-the-tools", out["segments"][0]["end"] >= T0 + 3000)

# A gap longer than GAP_SECONDS starts a new segment.
def two_stretches(conn, sid):
    fx.msg(conn, sid, T0, "user")
    fx.msg(conn, sid, T0 + 120, "assistant")
    fx.msg(conn, sid, T0 + 5 * H, "user")
    fx.msg(conn, sid, T0 + 5 * H + 120, "assistant")
    fx.route(conn, sid, "m1", "p1", T0, T0 + 6 * H)

out = store(two_stretches)
check("gap-splits", len(out["segments"]) == 2, str(len(out["segments"])))
check("segments-ordered-and-disjoint",
      out["segments"][1]["start"] > out["segments"][0]["end"])

# --- prompts ----------------------------------------------------------------

# A compaction handoff is persisted as a role=user carrier row. Counting it as a
# prompt reported 74 prompts on a session that has 58.
def handoff_row(conn, sid):
    fx.msg(conn, sid, T0, "user")
    fx.msg(conn, sid, T0 + 60, "user", content="[PRIOR CONTEXT]", summary=1, active=0, compacted=1)
    fx.route(conn, sid, "m1", "p1", T0, T0 + 600)

out = store(handoff_row)
check("handoff-not-a-prompt", out["segments"][0]["prompts"] == 1,
      str(out["segments"][0]["prompts"]))
check("handoff-is-a-compaction", len(out["segments"][0]["compactions"]) == 1)

# Gateway notices persist as role=user "[System: ...]" rows; they are machinery.
def system_marker(conn, sid):
    fx.msg(conn, sid, T0, "user")
    fx.msg(conn, sid, T0 + 30, "user", content="[System: model changed]", display_kind="model_switch")
    fx.msg(conn, sid, T0 + 40, "user", content="[System: something]")
    fx.msg(conn, sid, T0 + 50, "user", content="x", display_kind="hidden")
    fx.msg(conn, sid, T0 + 60, "user", content="y", display_kind="auto_continue")
    fx.route(conn, sid, "m1", "p1", T0, T0 + 600)

out = store(system_marker)
check("markers-not-prompts", out["segments"][0]["prompts"] == 1,
      str(out["segments"][0]["prompts"]))

# Compaction-archived history (active=0, compacted=1) is MOST of a long session:
# reading active=1 only returned 13 of 58 prompts on the real store.
def archived_history(conn, sid):
    for i in range(10):
        fx.msg(conn, sid, T0 + i * 60, "user", active=0, compacted=1)
    fx.msg(conn, sid, T0 + 700, "user")
    fx.route(conn, sid, "m1", "p1", T0, T0 + 900)

out = store(archived_history)
check("compacted-rows-are-read", out["segments"][0]["prompts"] == 11,
      str(out["segments"][0]["prompts"]))

# --- clusters without an operator -------------------------------------------

# A stretch with no operator prompt is machinery (resume, background review).
def machine_only(conn, sid):
    fx.msg(conn, sid, T0, "user")
    fx.msg(conn, sid, T0 + 60, "assistant")
    for i in range(5):
        fx.msg(conn, sid, T0 + 5 * H + i * 60, "assistant")
    fx.route(conn, sid, "m1", "p1", T0, T0 + 6 * H)

out = store(machine_only)
check("promptless-cluster-dropped", len(out["segments"]) == 1, str(len(out["segments"])))

# A segment begins at its first PROMPT, not at the first machine row before it.
def leading_machinery(conn, sid):
    fx.msg(conn, sid, T0, "assistant")
    fx.msg(conn, sid, T0 + 600, "user")
    fx.route(conn, sid, "m1", "p1", T0, T0 + 900)

out = store(leading_machinery)
check("segment-starts-at-prompt", out["segments"][0]["start"] == T0 + 600,
      str(out["segments"][0]["start"]))

# --- routes -----------------------------------------------------------------

# Auxiliary tasks run on OTHER models (a real session compresses on
# glm-4.5-flash) and must never be shown as a model the operator chose.
def aux_routes(conn, sid):
    fx.msg(conn, sid, T0, "user")
    fx.msg(conn, sid, T0 + 60, "assistant")
    fx.route(conn, sid, "main-model", "p1", T0, T0 + 600)
    fx.route(conn, sid, "tiny-model", "p9", T0, T0 + 600, task="compression")
    fx.route(conn, sid, "tiny-model", "p9", T0, T0 + 600, task="title_generation")

out = store(aux_routes)
names = [r["model"] for r in out["segments"][0]["routes"]]
check("aux-tasks-excluded", names == ["main-model"], str(names))

# A model named 'unknown' or a NULL first_seen carries no usable identity.
def junk_routes(conn, sid):
    fx.msg(conn, sid, T0, "user")
    fx.route(conn, sid, "unknown", "p1", T0, T0 + 600)
    fx.route(conn, sid, "m2", "p2", None, None)

out = store(junk_routes)
check("junk-routes-excluded", out["segments"][0]["routes"] == [],
      str(out["segments"][0]["routes"]))

# Two routes genuinely overlapping one segment are BOTH listed: the aggregate
# cannot order them, and inventing an order would be a lie.
def overlapping_routes(conn, sid):
    fx.msg(conn, sid, T0, "user")
    fx.msg(conn, sid, T0 + 1800, "assistant")
    fx.route(conn, sid, "m1", "p1", T0, T0 + 1200)
    fx.route(conn, sid, "m2", "p2", T0 + 900, T0 + 2400)

out = store(overlapping_routes)
check("overlapping-routes-both-listed",
      [r["model"] for r in out["segments"][0]["routes"]] == ["m1", "m2"],
      str(out["segments"][0]["routes"]))

# --- buckets and tools ------------------------------------------------------

def bucketed(conn, sid):
    fx.msg(conn, sid, T0, "user")
    for i in range(20):
        fx.msg(conn, sid, T0 + i * 60, "tool", tool_name="patch" if i % 2 else "terminal")
    fx.route(conn, sid, "m1", "p1", T0, T0 + 1200)

out = store(bucketed)
seg = out["segments"][0]
check("twenty-buckets", len(seg["buckets"]) == cc.SPARK_BUCKETS, str(len(seg["buckets"])))
check("peak-is-max", seg["peak"] == max(seg["buckets"]), f"{seg['peak']} vs {seg['buckets']}")
check("tool-calls-counted", seg["toolCalls"] == 20, str(seg["toolCalls"]))
check("top-tools-ranked",
      [t["name"] for t in seg["topTools"]][:2] == ["patch", "terminal"]
      or [t["name"] for t in seg["topTools"]][:2] == ["terminal", "patch"],
      str(seg["topTools"]))
check("top-tools-capped", len(seg["topTools"]) <= 3, str(seg["topTools"]))

# --- absent session ---------------------------------------------------------

out = store(lambda conn, sid: fx.route(conn, sid, "m1", "p1", T0, T0 + 60))
check("no-prompts-no-segments", out["segments"] == [], str(out["segments"]))

path = tempfile.mktemp(suffix=".db")
conn = fx.build(path)
conn.commit()
conn.close()
missing = cc.build_timeline(path, "does-not-exist")
check("unknown-session-is-empty-not-an-error", missing["segments"] == [])

# --- report -----------------------------------------------------------------

print(f"  {checks - len(failures)}/{checks} checks passed")
for failure in failures:
    print(f"  ✗ {failure}")
sys.exit(1 if failures else 0)
```

- [ ] **Step 4: Run the test to verify it fails**

```bash
cd plugins/comp-count
~/.hermes/hermes-agent/venv/bin/python tests/test_backend.py ~/.hermes/hermes-agent
```

Expected: FAIL — `plugin_api.py` does not exist yet, so the `spec.loader.exec_module` call raises `FileNotFoundError`.

- [ ] **Step 5: Write the backend**

`plugins/comp-count/dashboard/plugin_api.py`:

```python
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
```

- [ ] **Step 6: Write the test runner**

`plugins/comp-count/tests/run.sh`:

```bash
#!/usr/bin/env bash
# All checks for comp-count.
#
#   ./tests/run.sh      # backend + renderer, offline, no keys, no gateway
set -uo pipefail

cd "$(dirname "$0")/.."
HERMES="${HERMES_AGENT_DIR:-$HOME/.hermes/hermes-agent}"
PY="$HERMES/venv/bin/python"

if [[ ! -x "$PY" ]]; then
  echo "✗ no interpreter at $PY — set HERMES_AGENT_DIR to your hermes-agent checkout" >&2
  exit 2
fi

status=0

echo "── backend"
"$PY" tests/test_backend.py "$HERMES" || status=1

echo "── renderer"
python3 tests/test_renderer.py || status=1

if [[ $status -eq 0 ]]; then
  echo "✓ all checks passed"
else
  echo "✗ some checks failed" >&2
fi
exit $status
```

Make it executable:

```bash
chmod +x plugins/comp-count/tests/run.sh
```

- [ ] **Step 7: Run the backend test to verify it passes**

```bash
cd plugins/comp-count
~/.hermes/hermes-agent/venv/bin/python tests/test_backend.py ~/.hermes/hermes-agent
```

Expected: PASS — every check reported, exit 0. The renderer half does not exist yet, so run the backend file directly, not `run.sh`.

- [ ] **Step 8: Prove the tests can fail (mutation check, by hand)**

Do this by hand, one at a time — edit, run, observe, revert. Do not script it.

1. In `_is_prompt`, change `if compaction or role != "user":` to `if role != "user":`.
   Run the backend test. Expected: `handoff-not-a-prompt` fails.
   Revert.
2. In `_read_events`, change `(active = 1 OR compacted = 1)` to `active = 1`.
   Run. Expected: `compacted-rows-are-read` fails.
   Revert.
3. In `_read_routes`, drop `AND task = ''`.
   Run. Expected: `aux-tasks-excluded` fails.
   Revert.
4. In `build_timeline`, delete the `if not prompts: continue` guard.
   Run. Expected: `promptless-cluster-dropped` fails.
   Revert.

All four must fail as predicted and pass again after reverting. If one does not fail, that check is not testing what it claims — fix the check before moving on.

- [ ] **Step 9: Commit**

```bash
cd /Users/frenzy/dev/hermes/hermes-tools
gitleaks dir --redact plugins/comp-count
git add plugins/comp-count
git commit -m "feat(comp-count): read session working segments from state.db"
```

---

### Task 2: Verify the backend against the real store

A synthetic fixture proves the rules are implemented. It cannot prove they hold against a store nobody designed for this. This task is a read-only check against the live database and produces no product code.

**Files:**
- Test: `plugins/comp-count/tests/test_backend.py` (unchanged — this task adds no code)

**Interfaces:**
- Consumes: `build_timeline(db_path, session_id)` from Task 1.
- Produces: nothing. A verdict.

- [ ] **Step 1: Run the timeline over four real sessions**

These four were chosen during design because each breaks something different: a single-route session, a multi-route session, a long multi-day session, and one with many compactions.

```bash
cd /Users/frenzy/dev/hermes/hermes-tools/plugins/comp-count
for s in 20260922_135623_7aa682 20260903_163520_e7b087 \
         20260903_124539_810c57 20260907_112911_4b3fc5; do
  ~/.hermes/hermes-agent/venv/bin/python - "$s" <<'PY'
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("cc", "dashboard/plugin_api.py")
cc = importlib.util.module_from_spec(spec); spec.loader.exec_module(cc)
out = cc.build_timeline("/Users/frenzy/.hermes/state.db", sys.argv[1])
segs = out["segments"]
print(sys.argv[1], len(segs), "segments",
      sum(s["prompts"] for s in segs), "prompts",
      sum(len(s["compactions"]) for s in segs), "compactions")
print("  disjoint:", all(segs[i+1]["start"] > segs[i]["end"] for i in range(len(segs)-1)))
print("  routed:", all(s["routes"] for s in segs))
print("  buckets:", all(len(s["buckets"]) == 20 for s in segs))
PY
done
```

- [ ] **Step 2: Compare prompt counts against the database directly**

The timeline's prompt count must equal the store's own count under the same rule. A mismatch means the implementation drifted from the spec.

```bash
for s in 20260922_135623_7aa682 20260903_163520_e7b087 \
         20260903_124539_810c57 20260907_112911_4b3fc5; do
  sqlite3 ~/.hermes/state.db "
    SELECT '$s', COUNT(*) FROM messages
     WHERE session_id='$s' AND role='user' AND (active=1 OR compacted=1)
       AND timestamp>0 AND _compressed_summary=0
       AND COALESCE(display_kind,'') NOT IN ('hidden','auto_continue','model_switch')
       AND substr(content,1,8)<>'[System:'"
done
```

Expected, from the design probes: `20260903_124539_810c57` → 58, `20260907_112911_4b3fc5` → 186. The timeline's total may be LOWER than the store's when a prompt falls in a segment that was dropped for having no operator activity around it — but it must never be HIGHER, and the two long sessions above should match their probe figures.

- [ ] **Step 3: Record the verdict**

Every session must satisfy: segments disjoint, all 20 buckets present, no segment left without a route, prompt totals not exceeding the store's own count. If any fails, stop and fix the backend — do not proceed to the renderer with a backend that disagrees with the database.

No commit: this task changes no files.

---

### Task 3: Renderer formatting helpers

Pure functions first, with no React and no SDK in sight. They are where the visual rules live, so they get their own test cycle.

**Files:**
- Create: `plugins/comp-count/desktop/plugin.js`
- Create: `plugins/comp-count/tests/test_renderer.py`
- Modify: `plugins/comp-count/tests/run.sh` (already references the renderer suite — no edit needed if Task 1 was followed)

**Interfaces:**
- Consumes: the segment shape from Task 1.
- Produces, all named exports of `desktop/plugin.js`:
  - `BLOCKS: string` — `" ▁▂▃▄▅▆▇█"`.
  - `sparkline(buckets: number[], peak: number): string` — 20 glyphs.
  - `markerRow(segment): string` — 20 chars, `▲` where a compaction falls, spaces elsewhere.
  - `formatDuration(seconds: number): string` — `"45м"`, `"1ч12м"`, `"2д3ч"`.
  - `formatSpan(startSeconds: number, endSeconds: number): string` — `"03.09 16:35–17:47"`, carrying the end date when it differs.
  - `routeLabel(segment): string` — `"m1 · p1 → m2 · p2"`, or `"маршрут неизвестен"` when empty.
  - `toolsLabel(segment): string` — `"patch×369, terminal×288"`, or `""`.
  - `list(value): unknown[]` — defensive array read.

- [ ] **Step 1: Write the failing renderer test**

`plugins/comp-count/tests/test_renderer.py`. It stands up a throwaway `node_modules` with stub SDK exports, copies `plugin.js` in, and runs the checks under Node — the same technique `provider-limits` uses, because the real `@hermes/plugin-sdk` exists only inside the desktop app.

```python
#!/usr/bin/env python3
"""Renderer checks: build a stub SDK, load plugin.js, exercise its pure helpers.

`plugin.js` imports `@hermes/plugin-sdk`, which only exists inside the desktop
app, so this stands up a throwaway node_modules with the handful of exports the
plugin uses. The stub `jsx()` returns `{t, p}` rather than real React elements.

Run via ``tests/run.sh``.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

PLUGIN = pathlib.Path(__file__).resolve().parent.parent / "desktop" / "plugin.js"

root = pathlib.Path(tempfile.mkdtemp(prefix="comp-count-probe-"))
sdk = root / "node_modules" / "@hermes" / "plugin-sdk"
react = root / "node_modules" / "react"
sdk.mkdir(parents=True)
(react / "jsx-runtime").mkdir(parents=True)

(root / "package.json").write_text(json.dumps({"name": "probe", "type": "module"}))
(sdk / "package.json").write_text(json.dumps(
    {"name": "@hermes/plugin-sdk", "type": "module", "main": "index.js"}))
(sdk / "index.js").write_text("""
export const Button = () => null
export const Popover = () => null
export const PopoverContent = () => null
export const PopoverTrigger = () => null
export const STATUSBAR_AREAS = { left: 'statusBar.left', right: 'statusBar.right' }
export const host = { state: { focusedUsage: null, focusedStoredSessionId: null,
                               focusedSessionProfile: 'default' } }
export const useValue = value => (globalThis.__probeValues ?? new Map()).get(value) ?? value
export const useQuery = () => globalThis.__probeQuery ?? { data: null, error: null, isFetching: false }
export const useQueryClient = () => ({ setQueryData() {} })
""")
(react / "package.json").write_text(json.dumps({
    "name": "react", "type": "module",
    "exports": {".": "./index.js", "./jsx-runtime": "./jsx-runtime/index.js"}}))
(react / "index.js").write_text(
    "export const useState = initial => [initial, () => {}]\n"
    "export const useEffect = () => {}\n"
    "export default { useState, useEffect }\n")
(react / "jsx-runtime" / "index.js").write_text(
    "export const jsx = (t, p) => ({ t, p })\nexport const jsxs = (t, p) => ({ t, p })\n")

shutil.copy(PLUGIN, root / "plugin.js")

(root / "probe.mjs").write_text(r"""
import {
  BLOCKS, formatDuration, formatSpan, list, markerRow, routeLabel, sparkline, toolsLabel
} from './plugin.js'

const failures = []
let checks = 0
const check = (name, ok, detail = '') => {
  checks++
  if (!ok) failures.push(name + (detail ? ` — ${detail}` : ''))
}

// --- sparkline --------------------------------------------------------------

const twenty = n => Array.from({ length: 20 }, () => n)

check('spark-width', sparkline(twenty(3), 3).length === 20, String(sparkline(twenty(3), 3).length))

// A bucket with real work must NEVER render blank. round(1/480*8) is 0, and a
// blank glyph would claim nothing happened in an hour that had 1 event.
const sparse = sparkline([480, ...Array.from({ length: 19 }, () => 1)], 480)
check('nonempty-bucket-floor', !sparse.slice(1).includes(BLOCKS[0]), JSON.stringify(sparse))
check('empty-bucket-blank', sparkline([5, 0, ...twenty(0).slice(2)], 5)[1] === BLOCKS[0])
check('peak-is-full-block', sparkline([9, 1], 9)[0] === '█', sparkline([9, 1], 9))

// A zero peak must not divide by zero or produce NaN glyphs.
check('zero-peak-safe', sparkline(twenty(0), 0) === BLOCKS[0].repeat(20),
      JSON.stringify(sparkline(twenty(0), 0)))

// --- markers ----------------------------------------------------------------

const seg = {
  start: 1000, end: 2000, compactions: [1000, 1500, 1999],
  buckets: twenty(1), peak: 1, routes: [], topTools: [], prompts: 1, toolCalls: 0
}
const marks = markerRow(seg)
check('marker-width', marks.length === 20, String(marks.length))
check('marker-at-start', marks[0] === '▲', JSON.stringify(marks))
check('marker-at-middle', marks[10] === '▲', JSON.stringify(marks))
check('marker-at-end', marks[19] === '▲', JSON.stringify(marks))
check('no-markers-no-row', markerRow({ ...seg, compactions: [] }).trim() === '')

// --- duration ---------------------------------------------------------------

check('minutes', formatDuration(45 * 60) === '45м', formatDuration(45 * 60))
check('hours', formatDuration(72 * 60) === '1ч12м', formatDuration(72 * 60))
check('days', formatDuration(51 * 3600) === '2д3ч', formatDuration(51 * 3600))
check('sub-minute', formatDuration(30) === '<1м', formatDuration(30))

// --- span -------------------------------------------------------------------

const sameDay = formatSpan(new Date(2026, 8, 3, 16, 35).getTime() / 1000,
                           new Date(2026, 8, 3, 17, 47).getTime() / 1000)
check('same-day-span', sameDay === '03.09 16:35–17:47', sameDay)
const crossDay = formatSpan(new Date(2026, 8, 3, 23, 10).getTime() / 1000,
                            new Date(2026, 8, 4, 1, 5).getTime() / 1000)
check('cross-day-span', crossDay === '03.09 23:10–04.09 01:05', crossDay)

// --- labels -----------------------------------------------------------------

check('route-single', routeLabel({ routes: [{ model: 'm1', provider: 'p1' }] }) === 'm1 · p1')
check('route-multi',
      routeLabel({ routes: [{ model: 'm1', provider: 'p1' }, { model: 'm2', provider: 'p2' }] })
      === 'm1 · p1 → m2 · p2')
check('route-empty', routeLabel({ routes: [] }) === 'маршрут неизвестен')
// A route with no provider must not render a dangling separator.
check('route-no-provider', routeLabel({ routes: [{ model: 'm1', provider: '' }] }) === 'm1')

check('tools', toolsLabel({ topTools: [{ name: 'patch', count: 9 }, { name: 'terminal', count: 4 }] })
      === 'patch×9, terminal×4')
check('tools-empty', toolsLabel({ topTools: [] }) === '')

// --- defensive guards -------------------------------------------------------

// The payload crosses a process boundary. One malformed field must degrade a
// row, never throw during render and take the whole status bar down with it.
check('list-guards-null', list(null).length === 0)
check('list-guards-scalar', list(42).length === 0)
check('list-drops-non-objects', list([{ a: 1 }, null, 'x', 7]).length === 1)
check('spark-guards-garbage', sparkline(null, 5).length === 20)
check('markers-guard-garbage', markerRow({}).length === 20)
check('route-guards-garbage', routeLabel({}) === 'маршрут неизвестен')
check('tools-guards-garbage', toolsLabel({}) === '')
check('duration-guards-garbage', typeof formatDuration(NaN) === 'string')

console.log(`  ${checks - failures.length}/${checks} checks passed`)
for (const failure of failures) console.log(`  ✗ ${failure}`)
process.exit(failures.length ? 1 : 0)
""")

result = subprocess.run(["node", "probe.mjs"], cwd=root, capture_output=True, text=True)
sys.stdout.write(result.stdout)
sys.stderr.write(result.stderr)
shutil.rmtree(root, ignore_errors=True)
sys.exit(result.returncode)
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd plugins/comp-count && python3 tests/test_renderer.py
```

Expected: FAIL — `desktop/plugin.js` does not exist, so `shutil.copy` raises `FileNotFoundError`.

- [ ] **Step 3: Write the helpers**

Create `plugins/comp-count/desktop/plugin.js` with the imports and pure helpers. The chip and popover arrive in Task 4; this step deliberately stops at the functions the test drives.

```javascript
// comp-count — the focused session's working timeline in the status bar.
//
// The chip keeps showing the live compaction count. Tapping it opens a popover
// listing the session as working segments: where the work happened inside each
// stretch (a density sparkline), when it was compacted (markers positioned in
// the same 20 columns), which model and provider ran, and what the stretch was
// made of.
//
// Data comes from this package's own Python half
// (`~/.hermes/plugins/comp-count/dashboard/plugin_api.py`) through `ctx.rest`.
// That half is imported only when `comp-count` is in `plugins.enabled` in
// config.yaml — until then `ctx.rest` errors and the panel says so while the
// chip keeps working off live state.
import {
  Button, host, Popover, PopoverContent, PopoverTrigger, STATUSBAR_AREAS,
  useQuery, useValue
} from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'comp-count'
// Sparkline glyphs. Index 0 is the EMPTY bucket; a bucket with any activity
// never uses it (see `sparkline`).
const BLOCKS = ' ▁▂▃▄▅▆▇█'
const SPARK_BUCKETS = 20

/** Defensive array read: the payload crosses a process boundary, and one
 *  malformed field must degrade a row, never throw during render and take the
 *  whole status bar with it. */
function list(value) {
  return Array.isArray(value) ? value.filter(item => item && typeof item === 'object') : []
}

/** Finite number, or null. null/undefined/NaN all reach here from a partial payload. */
function num(value) {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

/** One glyph row, scaled to this segment's own peak.
 *
 *  The floor matters: `round(1 / 480 * 8)` is 0, so a quiet bucket beside a tall
 *  peak would render blank and claim nothing happened in an hour that had work
 *  in it. Any non-zero bucket gets at least `▁`. */
function sparkline(buckets, peak) {
  const values = Array.isArray(buckets) ? buckets : []
  const top = Math.max(1, num(peak) ?? 1)
  let out = ''
  for (let i = 0; i < SPARK_BUCKETS; i += 1) {
    const count = num(values[i]) ?? 0
    out += count <= 0 ? BLOCKS[0] : BLOCKS[Math.max(1, Math.min(8, Math.round((count / top) * 8)))]
  }
  return out
}

/** Compaction markers in the SAME 20 columns as the sparkline, so a `▲` sits
 *  under the activity it interrupted. Position is the whole point: a list of
 *  times cannot show that two compactions hit the densest stretch. */
function markerRow(segment) {
  const start = num(segment?.start) ?? 0
  const end = num(segment?.end) ?? 0
  const span = Math.max(end - start, 1)
  const row = new Array(SPARK_BUCKETS).fill(' ')
  for (const at of Array.isArray(segment?.compactions) ? segment.compactions : []) {
    const ts = num(at)
    if (ts === null) {
      continue
    }

    const index = Math.floor(((ts - start) / span) * SPARK_BUCKETS)
    row[Math.min(SPARK_BUCKETS - 1, Math.max(0, index))] = '▲'
  }
  return row.join('')
}

/** How long the stretch ran. Under a minute is `<1м`, not the `0м` a floor
 *  produces — a two-event segment is short, not instantaneous. */
function formatDuration(seconds) {
  const total = num(seconds)
  if (total === null || total < 0) {
    return ''
  }

  const minutes = Math.floor(total / 60)
  if (minutes < 1) return '<1м'
  if (minutes < 60) return `${minutes}м`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}ч${String(minutes % 60).padStart(2, '0')}м`
  return `${Math.floor(hours / 24)}д${hours % 24}ч`
}

function pad(value) {
  return String(value).padStart(2, '0')
}

function clock(date) {
  return `${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function dayMonth(date) {
  return `${pad(date.getDate())}.${pad(date.getMonth() + 1)}`
}

/** `03.09 16:35–17:47`, carrying the end date only when the stretch crosses
 *  midnight — a segment that ends "01:05" without a date reads as the same day. */
function formatSpan(startSeconds, endSeconds) {
  const start = num(startSeconds)
  const end = num(endSeconds)
  if (start === null || end === null) {
    return ''
  }

  const from = new Date(start * 1000)
  const to = new Date(end * 1000)
  const tail = from.toDateString() === to.toDateString() ? clock(to) : `${dayMonth(to)} ${clock(to)}`
  return `${dayMonth(from)} ${clock(from)}–${tail}`
}

/** `m1 · p1 → m2 · p2`. Several routes is the honest reading of an aggregate
 *  that cannot order them; see the backend's `_segment`. */
function routeLabel(segment) {
  const names = list(segment?.routes)
    .map(route => [route.model, route.provider].filter(Boolean).join(' · '))
    .filter(Boolean)
  return names.length ? names.join(' → ') : 'маршрут неизвестен'
}

function toolsLabel(segment) {
  return list(segment?.topTools)
    .map(tool => (tool.name && num(tool.count) !== null ? `${tool.name}×${tool.count}` : ''))
    .filter(Boolean)
    .join(', ')
}

// Exported for tests; the app only consumes the default export.
export {
  BLOCKS, formatDuration, formatSpan, list, markerRow, num, routeLabel, sparkline, toolsLabel
}
```

- [ ] **Step 4: Run the renderer test to verify it passes**

```bash
cd plugins/comp-count && python3 tests/test_renderer.py
```

Expected: PASS. The suite exercises only the exported helpers, which now exist.

- [ ] **Step 5: Prove the tests can fail (mutation check, by hand)**

1. In `sparkline`, change `Math.max(1, Math.min(8, ...))` to `Math.min(8, ...)`.
   Run. Expected: `nonempty-bucket-floor` fails. Revert.
2. In `markerRow`, change `Math.floor` to `Math.ceil`.
   Run. Expected: `marker-at-start` or `marker-at-end` fails. Revert.
3. In `formatSpan`, always return the short tail (drop the `toDateString` comparison).
   Run. Expected: `cross-day-span` fails. Revert.

- [ ] **Step 6: Commit**

```bash
cd /Users/frenzy/dev/hermes/hermes-tools
git add plugins/comp-count
git commit -m "feat(comp-count): sparkline, markers and span formatting"
```

---

### Task 4: Chip and popover

**Files:**
- Modify: `plugins/comp-count/desktop/plugin.js` (append the components and the default export)
- Modify: `plugins/comp-count/tests/test_renderer.py` (append the chip and panel checks)

**Interfaces:**
- Consumes: every helper from Task 3; `GET /timeline` from Task 1.
- Produces: the plugin default export `{ id: 'comp-count', name, description, register(ctx) }`.

- [ ] **Step 1: Append the failing chip checks to the renderer test**

Insert these before the `console.log` report line in `probe.mjs` inside `test_renderer.py`, and add `plugin` to the import line (`import plugin, { BLOCKS, ... } from './plugin.js'`):

```javascript
// --- chip -------------------------------------------------------------------

const renderChip = () => {
  let contribution
  plugin.register({
    register(value) { contribution = value },
    rest: async () => ({ segments: [] }),
    onDispose() {}
  })
  const element = contribution.render()
  return { contribution, node: element.type ? element.type(element.props) : element }
}

check('plugin-id', plugin.id === 'comp-count', plugin.id)
const { contribution } = renderChip()
check('chip-area', contribution.area === 'statusBar.right', contribution.area)

// The chip must never regress: whatever the backend does, it keeps printing the
// live compaction count with the clamp it always had.
const { compactionLabel } = plugin
check('chip-count', compactionLabel({ compressions: 3 }) === '🧳 3', compactionLabel({ compressions: 3 }))
check('chip-clamps-float', compactionLabel({ compressions: 2.9 }) === '🧳 2')
check('chip-clamps-negative', compactionLabel({ compressions: -4 }) === '🧳 0')
check('chip-clamps-garbage', compactionLabel({ compressions: 'nonsense' }) === '🧳 0')
check('chip-clamps-null', compactionLabel(null) === '🧳 0')
check('chip-clamps-missing', compactionLabel({}) === '🧳 0')
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd plugins/comp-count && python3 tests/test_renderer.py
```

Expected: FAIL — `plugin.js` has no default export yet, so `plugin.id` throws or reads undefined.

- [ ] **Step 3: Append the components and the default export**

Add to `plugins/comp-count/desktop/plugin.js`, after the helpers:

```javascript
// The namespaced REST door, captured in `register`. Components render inside
// the app's own React tree, outside register's closure, so it is module state
// rather than a prop threaded through every node.
let rest = async () => { throw new Error('plugin not registered') }
// The live <style> element, so a second register() replaces it instead of
// stacking another copy in <head>.
let styleElement = null
// Segments only change when a turn ends; this is not a live counter.
const REFETCH_MS = 60_000

// Disk plugins are not scanned by Tailwind, so layout lives here. Colors are
// theme variables only — never literals, or a theme switch breaks them.
const CSS = `
.cc-chip{display:inline-flex;align-items:center;gap:4px;height:100%;padding:0 6px;font-size:.6875rem;font-variant-numeric:tabular-nums;color:var(--ui-text-tertiary)}
.cc-popover{width:360px;max-width:calc(100vw - 24px)}
.cc-panel{display:flex;flex-direction:column;gap:10px;max-height:min(60dvh,460px);overflow-y:auto;overscroll-behavior:contain}
.cc-head{display:flex;align-items:baseline;justify-content:space-between;gap:8px}
.cc-title{font-size:.75rem;font-weight:600;color:var(--ui-text-primary)}
.cc-sub{font-size:.65rem;color:var(--ui-text-quaternary)}
.cc-error{font-size:.65rem;color:var(--dt-destructive,var(--ui-text-tertiary))}
.cc-seg{display:flex;flex-direction:column;gap:1px;padding-bottom:8px;border-bottom:1px solid var(--ui-stroke-quaternary)}
.cc-seg-top{display:flex;align-items:baseline;justify-content:space-between;gap:8px}
.cc-span{font-size:.65rem;color:var(--ui-text-secondary);font-variant-numeric:tabular-nums}
.cc-dur{font-size:.62rem;color:var(--ui-text-quaternary);font-variant-numeric:tabular-nums}
/* The sparkline and its markers must line up column for column, so both rows
   are monospace with identical tracking. A proportional font shifts the ▲ off
   the block it belongs to. */
.cc-spark,.cc-marks{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.7rem;line-height:1.05;letter-spacing:0;white-space:pre}
.cc-spark{color:var(--ui-accent)}
.cc-marks{color:var(--dt-destructive,var(--ui-accent))}
.cc-route{font-size:.65rem;font-weight:600;color:var(--ui-text-secondary);overflow:hidden;white-space:nowrap;text-overflow:ellipsis}
.cc-counts{font-size:.62rem;color:var(--ui-text-tertiary)}
.cc-tools{font-size:.62rem;color:var(--ui-text-quaternary);overflow:hidden;white-space:nowrap;text-overflow:ellipsis}
`

/** The chip's text: the live compaction count, clamped. The host can report a
 *  count before session state settles, and "🧳 NaN" or a negative count in the
 *  status bar is worse than showing zero. */
function compactionLabel(usage) {
  return `🧳 ${Math.max(0, Math.trunc(Number(usage?.compressions) || 0))}`
}

function Segment({ segment }) {
  const marks = markerRow(segment)
  const counts = [
    `${num(segment.prompts) ?? 0} промптов`,
    `${num(segment.toolCalls) ?? 0} вызовов инструментов`,
    (Array.isArray(segment.compactions) ? segment.compactions.length : 0) > 0
      ? `🧳${segment.compactions.length}`
      : ''
  ].filter(Boolean).join(' · ')
  const tools = toolsLabel(segment)

  return jsxs('div', {
    className: 'cc-seg',
    children: [
      jsxs('div', {
        className: 'cc-seg-top',
        children: [
          jsx('span', { className: 'cc-span', children: formatSpan(segment.start, segment.end) }),
          jsx('span', {
            className: 'cc-dur',
            children: formatDuration((num(segment.end) ?? 0) - (num(segment.start) ?? 0))
          })
        ]
      }),
      jsx('div', { className: 'cc-spark', children: sparkline(segment.buckets, segment.peak) }),
      // The marker row is dropped entirely when nothing was compacted, rather
      // than rendering 20 blanks that push every segment a line taller.
      marks.trim() && jsx('div', { className: 'cc-marks', children: marks }),
      jsx('div', { className: 'cc-route', title: routeLabel(segment), children: routeLabel(segment) }),
      jsx('div', { className: 'cc-counts', children: counts }),
      tools && jsx('div', { className: 'cc-tools', title: tools, children: tools })
    ]
  })
}

function Panel({ sessionId, profile }) {
  const { data, error } = useQuery({
    queryKey: [ID, 'timeline', sessionId, profile],
    queryFn: () => rest(`/timeline?session=${encodeURIComponent(sessionId)}`
      + `&profile=${encodeURIComponent(profile)}`),
    enabled: Boolean(sessionId),
    refetchInterval: REFETCH_MS,
    staleTime: REFETCH_MS,
    retry: false
  })
  const segments = list(data?.segments)

  return jsxs('div', {
    className: 'cc-panel',
    children: [
      jsxs('div', {
        className: 'cc-head',
        children: [
          jsx('span', { className: 'cc-title', children: 'Хронология сессии' }),
          jsx('span', { className: 'cc-sub', children: segments.length ? `${segments.length} отрезков` : '' })
        ]
      }),
      !sessionId && jsx('div', { className: 'cc-sub', children: 'Сессия ещё не начата.' }),
      error && jsx('div', {
        className: 'cc-error',
        children: 'Бэкенд недоступен — добавьте "comp-count" в plugins.enabled '
          + `и перезапустите шлюз. (${String(error.message ?? error)})`
      }),
      sessionId && !error && segments.length === 0 && jsx('div', {
        className: 'cc-sub',
        children: 'В этой сессии ещё нет рабочих отрезков.'
      }),
      ...segments.map((segment, index) => jsx(Segment, { segment }, `${segment.start}-${index}`))
    ]
  })
}

function Chip() {
  const usage = useValue(host.state.focusedUsage)
  // The STORED id, never focusedSessionId: runtime ids do not survive a reload
  // and do not key state.db, which is what the backend reads.
  const sessionId = useValue(host.state.focusedStoredSessionId) || ''
  const profile = useValue(host.state.focusedSessionProfile) || 'default'
  const label = compactionLabel(usage)
  const title = `Компакций в этой сессии: ${label.replace('🧳 ', '')}`

  return jsxs(Popover, {
    children: [
      jsx(PopoverTrigger, {
        asChild: true,
        children: jsx(Button, {
          variant: 'ghost',
          size: 'micro',
          'aria-label': title,
          title,
          children: jsx('span', { className: 'cc-chip', children: label })
        })
      }),
      jsx(PopoverContent, {
        side: 'top',
        align: 'end',
        className: 'cc-popover',
        'aria-label': 'Хронология сессии',
        children: jsx(Panel, { sessionId, profile })
      })
    ]
  })
}

export default {
  id: ID,
  name: 'Comp Count',
  description: 'Session timeline — models, providers, working segments and compactions.',
  compactionLabel,
  register(ctx) {
    rest = path => ctx.rest(path)

    // Idempotent: a hot reload can call register again on a module instance
    // whose previous style element is still in the document.
    styleElement?.remove()
    const style = document.createElement('style')
    style.textContent = CSS
    document.head.append(style)
    styleElement = style
    ctx.onDispose(() => {
      style.remove()
      if (styleElement === style) {
        styleElement = null
      }
    })

    ctx.register({
      id: 'status',
      area: STATUSBAR_AREAS.right,
      order: 120,
      render: () => jsx(Chip, {})
    })
  }
}
```

Also extend the export line from Task 3 to include the new helper:

```javascript
export {
  BLOCKS, compactionLabel, formatDuration, formatSpan, list, markerRow, num,
  routeLabel, sparkline, toolsLabel
}
```

The probe runs under Node with no DOM, and `register` touches
`document.createElement`. Add this as the FIRST line of `probe.mjs` in
`test_renderer.py`, before the imports, so `register` runs headless:

```javascript
globalThis.document ??= { createElement: () => ({ remove() {} }), head: { append() {} } }
```

- [ ] **Step 4: Run the renderer test to verify it passes**

```bash
cd plugins/comp-count && python3 tests/test_renderer.py
```

Expected: PASS — all helper checks plus the six chip-regression checks.

- [ ] **Step 5: Run both suites together**

```bash
cd plugins/comp-count && ./tests/run.sh
```

Expected: `✓ all checks passed`.

- [ ] **Step 6: Commit**

```bash
cd /Users/frenzy/dev/hermes/hermes-tools
git add plugins/comp-count
git commit -m "feat(comp-count): session timeline popover behind the chip"
```

---

### Task 5: Installer, repository wiring, and retiring the old copy

**Files:**
- Modify: `setup_hermes_tools.sh`
- Modify: `scripts/check.mjs:22`
- Modify: `README.md`
- Delete: `desktop-plugins/comp-count/plugin.js`
- Delete: `desktop-plugins/comp-count/plugin.test.mjs`

**Interfaces:**
- Consumes: the finished package from Tasks 1–4.
- Produces: an installed, enabled plugin under `~/.hermes/plugins/comp-count/`.

- [ ] **Step 1: Point the test runner at the new suite**

In `scripts/check.mjs`, replace line 22:

```javascript
  { name: 'comp-count', cmd: ['bash', 'plugins/comp-count/tests/run.sh'] },
```

- [ ] **Step 2: Remove the old renderer-only copy from the repository**

```bash
cd /Users/frenzy/dev/hermes/hermes-tools
git rm -r desktop-plugins/comp-count
```

The chip's behaviour is not lost: its six clamp cases moved into
`tests/test_renderer.py` in Task 4.

- [ ] **Step 3: Rewrite the installer's comp-count half**

In `setup_hermes_tools.sh`, `comp-count` becomes a package installed exactly like `provider-limits`. Replace the `comp_count_source`/`comp_count_dir`/`comp_count_target` variables (lines 15–17) with:

```sh
comp_count_source="$script_dir/plugins/comp-count"
comp_count_target="$hermes_home/plugins/comp-count"
```

Replace the source check (lines 21–24) with:

```sh
if test ! -f "$comp_count_source/desktop/plugin.js" \
  || test ! -f "$comp_count_source/dashboard/plugin_api.py"; then
  printf 'setup_hermes_tools: comp-count source is incomplete: %s\n' "$comp_count_source" >&2
  exit 1
fi
```

Replace the single-file install (lines 40–45) with the tree copy, mirroring the `provider-limits` block and its reasoning:

```sh
# comp-count is now a directory with two halves, so compare the whole tree and
# replace it wholesale: copying file-by-file would leave a file deleted upstream
# behind in the installed copy, and a stale plugin_api.py still gets imported.
comp_count_changed=false
comp_count_backend_changed=false
if ! diff -r -q \
  -x '.git' -x '__pycache__' -x '*.pyc' \
  "$comp_count_source" "$comp_count_target" >/dev/null 2>&1; then
  if ! diff -r -q \
    -x '__pycache__' -x '*.pyc' \
    "$comp_count_source/dashboard" "$comp_count_target/dashboard" >/dev/null 2>&1; then
    comp_count_backend_changed=true
  fi
  install -d -m 0755 "$hermes_home/plugins"
  rm -rf "$comp_count_target.tmp"
  cp -R "$comp_count_source" "$comp_count_target.tmp"
  rm -rf "$comp_count_target.tmp/.git"
  find "$comp_count_target.tmp" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
  rm -rf "$comp_count_target"
  mv "$comp_count_target.tmp" "$comp_count_target"
  comp_count_changed=true
fi

# The renderer-only disk copy from the previous layout must go, or the app loads
# TWO plugins claiming id "comp-count": the stale one wins or they collide, and
# either way the popover never appears. Electron re-materializes the package's
# desktop half into this directory itself, with a .hermes-package.json marker —
# a directory without that marker is the retired hand-installed copy.
legacy_comp_count="$hermes_home/desktop-plugins/comp-count"
comp_count_legacy_removed=false
if test -d "$legacy_comp_count" && test ! -f "$legacy_comp_count/.hermes-package.json"; then
  rm -rf "$legacy_comp_count"
  comp_count_legacy_removed=true
fi

# The backend half must appear in plugins.enabled or its routes are never
# imported (GHSA-mcfc-hp25-cjv7). Check before enabling: `plugins enable` is
# idempotent but reports success either way, and treating that as a change
# would restart the gateway on every run.
comp_count_enabled=false
if "$hermes_bin" config get plugins.enabled 2>/dev/null | grep -qx -- '- comp-count'; then
  comp_count_enabled=true
fi
comp_count_gate_changed=false
if test "$comp_count_enabled" = false; then
  "$hermes_bin" plugins enable --no-allow-tool-override comp-count
  comp_count_gate_changed=true
fi
```

Extend the reporting block (lines 87–96) so the new states are named:

```sh
if test "$comp_count_gate_changed" = true; then
  installed="${installed:+$installed, }comp-count backend gate"
fi
if test "$comp_count_legacy_removed" = true; then
  installed="${installed:+$installed, }retired the old comp-count disk copy"
fi
```

Extend the reconcile nudge condition (line 110) and the restart condition (line 123) so a comp-count change triggers them too:

```sh
if { test "$provider_limits_changed" = true || test "$comp_count_changed" = true; } \
  && test -d "$desktop_root"; then
```

```sh
if test "$provider_limits_backend_changed" = true || test "$provider_limits_gate_changed" = true \
  || test "$comp_count_backend_changed" = true || test "$comp_count_gate_changed" = true; then
```

- [ ] **Step 4: Check the installer against the existing lifecycle suite**

```bash
cd /Users/frenzy/dev/hermes/hermes-tools
sh tests/test-setup-hermes-tools.sh
```

Expected: PASS. If the suite asserts on the old single-file comp-count path, update those assertions to the package layout — the suite describes the installer's contract, and the contract changed.

- [ ] **Step 5: Update the README layout section**

In `README.md`, replace the `desktop-plugins/comp-count/` bullet with:

```markdown
- `plugins/comp-count/` — unified plugin (Python backend + desktop UI) showing
  the focused session's working timeline: models, providers, segments and
  compactions.
```

and amend the `setup_hermes_tools.sh` bullet to read "installs both plugins as unified packages under `~/.hermes/plugins`".

- [ ] **Step 6: Run every suite**

```bash
cd /Users/frenzy/dev/hermes/hermes-tools && bun run check
```

Expected: all suites pass, `comp-count` among them, running from its new path.

- [ ] **Step 7: Commit**

```bash
cd /Users/frenzy/dev/hermes/hermes-tools
gitleaks dir --redact .
git add -A
git commit -m "feat(comp-count): install as a unified package and retire the disk copy"
```

The `gitleaks` run must report no findings beyond the pre-existing ones in `.env.local` and the `hermes-webui/` submodule fixtures.

---

### Task 6: Install and verify on the real desktop

The suites prove the code behaves. This proves the plugin actually loads, mounts, and draws — which no offline check can.

**Files:** none. This task verifies.

**Interfaces:**
- Consumes: everything above.
- Produces: a verdict, and a `docs/` note only if a defect is found.

- [ ] **Step 1: Install**

```bash
cd /Users/frenzy/dev/hermes/hermes-tools && ./setup_hermes_tools.sh
```

Expected output names `comp-count` and reports a gateway restart (the backend half is new and the gate is newly set).

Before running this, confirm no session is mid-turn — a gateway restart ends live work. Check the desktop for a running turn, or ask the operator.

- [ ] **Step 2: Confirm the backend mounted**

```bash
grep -i "comp-count" ~/.hermes/logs/errors.log | tail -5
hermes config get plugins.enabled
```

Expected: `- comp-count` present in the enabled list, and NO `Failed to load plugin comp-count API routes` line in the error log after the restart. Compare timestamps — an old line from a previous run is not evidence about this one.

- [ ] **Step 3: Confirm only one copy of the plugin exists**

```bash
ls -la ~/.hermes/plugins/comp-count
ls -la ~/.hermes/desktop-plugins/comp-count 2>/dev/null
```

Expected: the package exists under `plugins/`. Under `desktop-plugins/` there is either nothing, or a directory containing `.hermes-package.json` — the copy Electron materializes itself. A `desktop-plugins/comp-count/plugin.js` with no marker means Step 3 of Task 5 did not run; remove it and re-check.

- [ ] **Step 4: Open the popover on a real session**

Open the desktop, focus a long-running chat, click the 🧳 chip. Check by eye:
- segments are listed newest-last with plausible times;
- the sparkline is 20 glyphs wide and its `▲` markers sit under blocks, not offset;
- the route line names a model and provider you recognise for that session;
- counts and tools look sane for the work you remember doing.

- [ ] **Step 5: Cross-check one segment against the database**

Take the first segment's start time from the panel and confirm the store agrees:

```bash
sqlite3 ~/.hermes/state.db "
  SELECT datetime(timestamp,'unixepoch','localtime'), role, substr(content,1,60)
    FROM messages
   WHERE session_id='<stored id>' AND (active=1 OR compacted=1)
   ORDER BY timestamp LIMIT 5"
```

The panel's first segment must start at the first real operator prompt, not earlier.

- [ ] **Step 6: Check the failure path**

Temporarily disable the backend gate and confirm the panel degrades honestly rather than going blank or crashing the status bar:

```bash
hermes plugins disable comp-count && hermes gateway restart
```

Expected: the chip still shows `🧳 N`; the popover shows the "бэкенд недоступен" line. Then restore:

```bash
hermes plugins enable --no-allow-tool-override comp-count && hermes gateway restart
```

- [ ] **Step 7: Report**

State what was verified and anything that did not match. If a defect appears, fix it in the owning task's file, re-run `bun run check`, and re-verify — do not paper over it in the renderer.

---

## Self-Review

**Spec coverage.** Every spec section maps to a task: the backend package and read-only store access, prompt rule, `active OR compacted` read, clustering rule, promptless-cluster drop, route `task=''` filter and the accepted aggregate-ordering limit → Task 1, proven against real data in Task 2. Sparkline, marker placement, the non-empty-bucket floor, duration/span formatting, defensive guards, theme variables, the ~360px panel and monospace alignment → Tasks 3–4. Chip non-regression → Task 4, Step 1. Failure and empty states (backend absent, no session, no segments, route unknown) → Task 4, Step 3 and Task 6, Step 6. The excluded items (cost, context growth, day ribbon) appear nowhere, which is correct. Installer, gate, stale-copy removal, `check.mjs`, README → Task 5.

**Placeholders.** None: every code step carries the actual content, every test step names the command and the expected result, and the mutation steps say which check must fail.

**Type consistency.** The segment shape declared in Task 1's Produces block (`start`, `end`, `prompts`, `toolCalls`, `compactions`, `routes`, `topTools`, `buckets`, `peak`) is the shape the Task 3 helpers read and the Task 4 components render; `SPARK_BUCKETS = 20` is one value in both halves; `compactionLabel` is defined in Task 4 and exported there, and the Task 4 probe imports it from the default export where it is attached.
