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
      sorted(t["name"] for t in seg["topTools"][:2]) == ["patch", "terminal"],
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
