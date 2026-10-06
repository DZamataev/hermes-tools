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
one = fixture("codexlb-2-v1-usage.json")


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


# --- OpenCode Go ------------------------------------------------------------

ocgo = fixture("opencode-go-usage.json")
oc_rows = pl._opencode_go_payload(ocgo)["buckets"]
check("ocgo-three-windows", [(b["key"], b["window"]) for b in oc_rows]
      == [("rolling", "5h"), ("weekly", "7d"), ("monthly", "monthly")], str(oc_rows))
# The captured account is untouched: percent 0 USED must read as 100% LEFT.
check("ocgo-fixture-untouched-is-full", all(b.get("remainingPct") == 100.0 for b in oc_rows), str(oc_rows))
check("ocgo-reset-is-epoch-ms", oc_rows[0]["resetAt"] == 1791303043000, str(oc_rows[0]["resetAt"]))


def ocgo_row(**rolling) -> dict:
    return {"usage": {"rolling": rolling}}


def ocgo_one(**rolling):
    rows = pl._opencode_go_payload(ocgo_row(**rolling))["buckets"]
    return rows[0] if rows else {}


# `percent` is the USED share (subscription.ts: usage / limit * 100). Reading it
# as the remainder would invert the whole row: 30% used drawn as 30% left.
check("ocgo-percent-is-used-not-left",
      ocgo_one(status="ok", percent=30, resetsAt="2026-10-06T16:10:43.000Z").get("remainingPct") == 70.0)
check("ocgo-rate-limited-is-exhausted", ocgo_one(status="rate-limited", percent=100).get("remainingPct") == 0.0)
# The producer pins percent to 100 when limited, but the status alone must be
# enough: a "rate-limited" row with a stale low percent is still exhausted.
check("ocgo-status-outranks-percent", ocgo_one(status="rate-limited", percent=12).get("remainingPct") == 0.0)
# An unknown status word is trouble, not health.
check("ocgo-unknown-status-not-trusted", ocgo_one(status="suspended", percent=5).get("remainingPct") == 0.0)
check("ocgo-missing-percent-no-row", ocgo_one(status="ok") == {})
check("ocgo-clamps", ocgo_one(status="ok", percent=140).get("remainingPct") == 0.0
      and ocgo_one(status="ok", percent=-3).get("remainingPct") == 100.0)

for case in [{}, {"usage": None}, {"usage": "x"}, {"usage": {"rolling": None}},
             {"usage": {"rolling": "x", "weekly": 5}},
             {"usage": {"rolling": {"percent": "x", "status": None, "resetsAt": 7}}}]:
    try:
        pl._opencode_go_payload(case)
    except Exception as exc:  # noqa: BLE001
        check("ocgo-hostile", False, f"{case} raised {type(exc).__name__}: {exc}")
check("ocgo-hostile-no-throw", True)


# --- discovery --------------------------------------------------------------

def discover(providers: dict, env: dict) -> list:
    saved = pl._providers_config, pl._env
    pl._providers_config = lambda: providers
    pl._env = lambda name: env.get(name, "")
    try:
        return pl.discover_targets()
    finally:
        pl._providers_config, pl._env = saved


TC = {"teamclaude": {"name": "TeamClaude", "api": "https://tc.example:3443",
                     "capabilities": {"anthropic_oauth_proxy": True}}}

# OpenCode Go is a BUILT-IN Hermes provider: it has no `providers:` entry, only
# a key in .env. It must still show up, after the configured ones.
found = discover(TC, {"OPENCODE_GO_API_KEY": "k"})
check("ocgo-builtin-discovered",
      [(t["id"], t["kind"], t["base_url"]) for t in found]
      == [("teamclaude", "teamclaude", "https://tc.example:3443"),
          ("opencode-go", "opencode-go", "https://opencode.ai/zen/go/v1")], str(found))
# No key = not subscribed: no permanent "no API key" row for every user.
check("ocgo-absent-without-key", [t["kind"] for t in discover(TC, {})] == ["teamclaude"])
# Hermes honours OPENCODE_GO_BASE_URL; so must the usage call.
check("ocgo-base-url-override", discover({}, {"OPENCODE_GO_API_KEY": "k",
                                              "OPENCODE_GO_BASE_URL": "https://go.example/zen/go/v1/"})
      [0]["base_url"] == "https://go.example/zen/go/v1")
# A custom provider pointed at Go is that provider — no second, duplicate row.
custom = {"my-go": {"name": "Go", "base_url": "https://opencode.ai/zen/go/v1", "key_env": "MY_GO"}}
found = discover(custom, {"OPENCODE_GO_API_KEY": "k", "MY_GO": "m"})
check("ocgo-custom-entry-not-duplicated",
      [(t["id"], t["kind"], t["base_url"], t["key"]) for t in found]
      == [("my-go", "opencode-go", "https://opencode.ai/zen/go/v1", "m")], str(found))
# Zen (pay-as-you-go) has no Go plan behind it; it must not be mistaken for Go.
check("ocgo-zen-not-matched",
      discover({"zen": {"base_url": "https://opencode.ai/zen/v1"}}, {}) == [])


# --- fetch (no network: httpx MockTransport) -------------------------------

import asyncio  # noqa: E402

import httpx  # noqa: E402

seen: dict = {}


def fetch(status: int, body) -> dict:
    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(status, json=body)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await pl._fetch_target(client, {
                "id": "opencode-go", "label": "OpenCode Go", "kind": "opencode-go",
                "base_url": "https://opencode.ai/zen/go/v1", "key": "SECRET-KEY"})
    return asyncio.run(run())


ok = fetch(200, ocgo)
check("ocgo-fetch-url", seen.get("url") == "https://opencode.ai/zen/go/v1/usage", str(seen.get("url")))
check("ocgo-fetch-bearer", seen.get("auth") == "Bearer SECRET-KEY")
check("ocgo-fetch-ok", ok["ok"] is True and len(ok["buckets"]) == 3, str(ok))
# 403 is "valid key, no Go plan" (EntitlementError) — a different fix than 401.
denied = fetch(403, {"type": "error", "error": {"type": "EntitlementError"}})
check("ocgo-403-explained", denied["error"] == "HTTP 403 (no OpenCode Go subscription)", str(denied["error"]))
check("ocgo-401-plain", fetch(401, {})["error"] == "HTTP 401")
check("ocgo-key-not-in-result", "SECRET" not in json.dumps([ok, denied]))


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
