#!/usr/bin/env python3
"""Backend checks: fixtures in, normalised rows out. No network, no server.

Every case here is a defect that actually shipped once, or a payload shape the
live upstreams produce. Run via ``tests/run.sh``.

The fixtures are real captured responses with the account identities replaced;
the NUMBERS are untouched, which is what makes the arithmetic assertions here
meaningful.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"

# The backend imports hermes_cli.config, so it needs the agent on sys.path.
HERMES = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "/Users/frenzy/.hermes/hermes-agent")
sys.path.insert(0, str(HERMES))

spec = importlib.util.spec_from_file_location("pl_api", ROOT / "dashboard" / "plugin_api.py")
pl = importlib.util.module_from_spec(spec)
sys.modules["pl_api"] = pl
spec.loader.exec_module(pl)

failures: list[str] = []
checks = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if not condition:
        failures.append(f"{name}{f' — {detail}' if detail else ''}")


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


team = fixture("teamclaude-quota.json")
codex = fixture("codexlb-v1-usage.json")
one = fixture("codexlb-oneclick-v1-usage.json")


# --- quota normalisation ----------------------------------------------------

one_rows = pl._codex_payload(one)
labels = [b["label"] for b in one_rows["buckets"]]
# A weekly cost_usd cap and a 7d credits pool are different resources that run
# out independently; collapsing them under one "Week" row hid whichever lost.
check("units-not-merged", sorted(labels) == ["5 hours", "Week (cost_usd)", "Week (credits)"], str(labels))
check("window-field-present", all(b.get("window") in {"5h", "7d"} for b in one_rows["buckets"]))

team_rows = pl._teamclaude_payload(team)
team_keys = [b["key"] for b in team_rows["buckets"]]
# weeklySonnet only MIRRORS weeklyShared while Anthropic does not report it
# separately (quota-summary.js: `unified7dSonnet ?? unified7d`). Dropping it as
# a duplicate would mean a silently missing row the day it diverges.
check("sonnet-carried", "weeklySonnet" in team_keys, str(team_keys))

divergent = json.loads(json.dumps(team))
divergent["aggregate"]["weeklySonnet"] = {
    "remaining": 0.02, "nextResetAt": 1790038800000, "knownAccounts": 3, "source": "unified7dSonnet"}
rows = {b["key"]: b["remainingPct"] for b in pl._teamclaude_payload(divergent)["buckets"]}
check("sonnet-independent", rows.get("weeklySonnet") == 2.0 and rows.get("weeklyShared") == 40.7, str(rows))

# A ceiling of zero means "nothing may be spent"; omitting the row rendered as
# "no such limit", the opposite of the truth.
zero = {"limits": [{"limit_type": "credits", "limit_window": "5h",
                    "max_value": 0, "remaining_value": 0, "reset_at": "2026-09-21T12:00:12Z"}]}
zero_rows = pl._codex_payload(zero)["buckets"]
check("zero-ceiling-is-exhausted",
      len(zero_rows) == 1 and zero_rows[0]["remainingPct"] == 0.0, str(zero_rows))

# account_pool_usage is a REMAINING percentage per WINDOW despite its name and
# its account-shaped field names (codex-lb: _compute_pooled_credits).
pool = {w["label"]: w["remainingPct"] for w in pl._codex_payload(codex)["poolWindows"]}
check("pool-windows-labelled", set(pool) == {"5 hours", "Week"}, str(pool))
check("pool-is-remaining-not-used", pool.get("Week") == 95.5, str(pool))

check("poolwindows-always-present",
      all("poolWindows" in fn(data) for fn, data in
          ((pl._teamclaude_payload, team), (pl._codex_payload, codex))))

# --- malformed payloads -----------------------------------------------------

hostile = [{}, {"accounts": 1}, {"accounts": "x"}, {"aggregate": 7}, {"limits": "x"},
           {"limits": [None, 5]}, {"account_pool_usage": "x"},
           {"account_pool_usage": {"primary": None, "secondary": "x"}}]
for case in hostile:
    for fn in (pl._teamclaude_payload, pl._codex_payload):
        try:
            fn(case)
        except Exception as exc:  # noqa: BLE001 — that is the point
            check(f"hostile-{fn.__name__}", False, f"{case} raised {type(exc).__name__}: {exc}")
check("hostile-no-throw", True)

# A key must never reach a response object.
check("key-not-in-payload", all(
    "SECRET" not in json.dumps(fn(data))
    for data in (team, codex, one) for fn in (pl._teamclaude_payload, pl._codex_payload)))


# --- status pages -----------------------------------------------------------

PAGE = {"id": "openai", "label": "OpenAI", "url": "x", "component": "Codex API"}


def status(indicator: str, component: str, *, name: str = "Codex API", incidents=()) -> dict:
    return pl._status_payload(PAGE, {
        "status": {"indicator": indicator, "description": "probe"},
        "components": [{"name": name, "status": component}],
        "incidents": [{"name": n} for n in incidents]})


check("status-healthy", status("none", "operational")["ok"] is True)
# The page can be green while OUR component is on fire, and vice versa — both
# signals are consulted, so neither outage shape can hide.
check("status-component-outage", status("none", "major_outage")["ok"] is False)
check("status-page-outage", status("critical", "operational")["ok"] is False)
# An unrelated component (Sora, claude.ai) is not our problem.
check("status-unrelated-ignored", status("none", "major_outage", name="Sora")["ok"] is True)
# A renamed component stops voting; it must never vote "healthy".
check("status-renamed-falls-back",
      status("none", "operational", name="Codex API v2")["ok"] is True
      and status("major", "operational", name="Codex API v2")["ok"] is False)
# A severity word we have never seen is trouble, not health.
check("status-unknown-indicator-is-trouble", status("apocalyptic", "operational")["ok"] is False)
check("status-incidents-carried", status("critical", "operational", incidents=["Elevated errors"])["incidents"]
      == ["Elevated errors"])

for case in [{}, {"status": "x"}, {"components": "x"}, {"components": [None, 5]},
             {"incidents": "x"}, {"incidents": [None]}, {"status": {"indicator": None}}]:
    try:
        pl._status_payload(PAGE, case)
    except Exception as exc:  # noqa: BLE001
        check("status-hostile", False, f"{case} raised {type(exc).__name__}: {exc}")
check("status-hostile-no-throw", True)


# --- report -----------------------------------------------------------------

if failures:
    print(f"backend: {len(failures)}/{checks} FAILED")
    for line in failures:
        print(f"  ✗ {line}")
    sys.exit(1)

print(f"backend: {checks} checks passed")
