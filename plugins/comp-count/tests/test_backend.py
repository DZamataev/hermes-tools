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


def store(fn, gateways=None):
    """Build a fixture db, run fn(conn, sid), return build_timeline's output."""
    path = tempfile.mktemp(suffix=".db")
    conn = fx.build(path)
    fn(conn, "s1")
    conn.commit()
    conn.close()
    return cc.build_timeline(path, "s1", gateways or {})


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


# ...but a COMPACTION in that machine-only head anchors the segment too. Found
# on the live store: four of 17 compactions fell before the cluster's first
# prompt and vanished from the timeline entirely.
def compaction_before_first_prompt(conn, sid):
    fx.msg(conn, sid, T0, "assistant")
    fx.msg(conn, sid, T0 + 300, "assistant", content="[PRIOR CONTEXT]",
           summary=1, active=0, compacted=1)
    fx.msg(conn, sid, T0 + 900, "user")
    fx.route(conn, sid, "m1", "p1", T0, T0 + 1200)


out = store(compaction_before_first_prompt)
check("early-compaction-kept", len(out["segments"][0]["compactions"]) == 1,
      str(out["segments"][0]["compactions"]))
check("early-compaction-anchors-start", out["segments"][0]["start"] == T0 + 300,
      str(out["segments"][0]["start"]))
# The machine-only head BEFORE that compaction is still trimmed.
check("head-before-anchor-trimmed", out["segments"][0]["start"] > T0,
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

# --- provider naming --------------------------------------------------------

# billing_provider is 'custom' for 291 of the real store's rows, which tells the
# operator nothing: three different gateways all report it. billing_base_url
# does distinguish them, and the mapping comes from the user's own config.yaml
# rather than a table baked in here, because the hosts are theirs to change.
GATEWAYS = {
    "teamclaude": "https://teamclaude.larid.dedyn.io:3443",
    "codex-lb": "https://codexlb.larid.dedyn.io:2499/backend-api/codex",
    "codex-lb-oneclick": "https://codex-lb.dev.looky.team/backend-api/codex",
}

check("provider-from-base-url",
      cc.provider_name("custom", "https://teamclaude.larid.dedyn.io:3443", GATEWAYS) == "teamclaude",
      cc.provider_name("custom", "https://teamclaude.larid.dedyn.io:3443", GATEWAYS))

# A trailing slash and a deeper path are the same gateway. The real store holds
# both '/backend-api/codex' and '/backend-api/codex/' for one provider.
check("provider-ignores-trailing-slash",
      cc.provider_name("custom", "https://codex-lb.dev.looky.team/backend-api/codex/", GATEWAYS)
      == "codex-lb-oneclick")

# Hermes writes the provider as 'custom:<name>' in some rows and bare in others.
check("provider-strips-custom-prefix",
      cc.provider_name("custom:codex-lb-oneclick", "", GATEWAYS) == "codex-lb-oneclick")

# An already-specific name is kept as-is; nothing to improve.
check("provider-keeps-specific-name",
      cc.provider_name("anthropic", "", GATEWAYS) == "anthropic")

# Unknown host and a generic name: keep the generic name rather than invent one.
check("provider-unknown-keeps-generic-name",
      cc.provider_name("custom", "https://example.invalid/v1", GATEWAYS) == "custom")

# Nothing at all to go on: empty, not a literal "unknown" masquerading as a
# provider name in a list beside the real ones.
check("provider-empty-when-nothing-identifies-it",
      cc.provider_name("", "", GATEWAYS) == "",
      repr(cc.provider_name("", "", GATEWAYS)))

# --- upstream vendor --------------------------------------------------------

# A gateway does not have its own price list; the vendor it proxies does.
check("vendor-teamclaude-is-anthropic",
      cc.upstream_vendor("teamclaude", "https://teamclaude.larid.dedyn.io:3443") == "anthropic")
check("vendor-codex-is-openai",
      cc.upstream_vendor("codex-lb", "https://codexlb.larid.dedyn.io:2499/backend-api/codex") == "openai")
check("vendor-passes-through-known-vendor",
      cc.upstream_vendor("anthropic", "") == "anthropic")

# --- cost -------------------------------------------------------------------

RATES = {
    "anthropic/claude-opus-5": {"in": 5.0, "out": 25.0, "cacheRead": 0.5, "cacheWrite": 6.25},
}

# Cache reads dominate a long session (82M against 1.1k input tokens on the real
# one), so a cost that ignores them is wrong by orders of magnitude.
cost = cc.estimate_cost("claude-opus-5", "anthropic",
                        {"inp": 1_000_000, "out": 1_000_000,
                         "cacheRead": 1_000_000, "cacheWrite": 1_000_000}, RATES)
check("cost-sums-all-four-rates", cost == 5.0 + 25.0 + 0.5 + 6.25, str(cost))

check("cost-scales-per-million",
      cc.estimate_cost("claude-opus-5", "anthropic",
                       {"inp": 500_000, "out": 0, "cacheRead": 0, "cacheWrite": 0}, RATES) == 2.5)

# No rate must read as "unknown", never as zero: a $0 line claims the work was
# free, which is a different and false statement.
check("cost-unknown-model-is-none",
      cc.estimate_cost("glm-4.6v-flash", "zai",
                       {"inp": 1_000_000, "out": 0, "cacheRead": 0, "cacheWrite": 0}, RATES) is None)

# --- usage payload ----------------------------------------------------------


def usage_store(fn):
    path = tempfile.mktemp(suffix=".db")
    conn = fx.build(path)
    fn(conn, "s1")
    conn.commit()
    conn.close()
    return cc.build_usage(path, "s1", GATEWAYS, RATES)


def two_models(conn, sid):
    fx.route(conn, sid, "claude-opus-5", "custom", T0, T0 + 600, calls=10,
             base_url=GATEWAYS["teamclaude"], inp=1_000_000, out=1_000_000,
             cache_read=1_000_000, cache_write=1_000_000)
    fx.route(conn, sid, "glm-4.6v-flash", "zai", T0, T0 + 600, calls=3,
             base_url="https://open.bigmodel.cn/api/paas/v4/", inp=500, out=200)


usage = usage_store(two_models)
models = {m["model"]: m for m in usage["models"]}

check("usage-lists-every-model", set(models) == {"claude-opus-5", "glm-4.6v-flash"}, str(set(models)))
check("usage-resolves-provider", models["claude-opus-5"]["provider"] == "teamclaude",
      models["claude-opus-5"]["provider"])
check("usage-carries-calls", models["claude-opus-5"]["calls"] == 10)
check("usage-priced-model-has-cost", models["claude-opus-5"]["costUsd"] == 5.0 + 25.0 + 0.5 + 6.25,
      str(models["claude-opus-5"]["costUsd"]))
check("usage-unpriced-model-has-no-cost", models["glm-4.6v-flash"]["costUsd"] is None)

# The total may only sum what was actually priced, and must say so — otherwise
# a session that is half unpriced reads as if the total covered all of it.
check("total-sums-priced-only", usage["totalCostUsd"] == 5.0 + 25.0 + 0.5 + 6.25,
      str(usage["totalCostUsd"]))
check("total-flags-partial-coverage", usage["costComplete"] is False)


def one_priced_model(conn, sid):
    fx.route(conn, sid, "claude-opus-5", "custom", T0, T0 + 600, calls=2,
             base_url=GATEWAYS["teamclaude"], inp=1_000_000, out=0)


check("total-complete-when-every-model-priced",
      usage_store(one_priced_model)["costComplete"] is True)

# The ROUTE line under each segment names the provider too, and 'custom' is as
# useless there as in the totals — the operator sees it on every segment.
route_out = store(lambda conn, sid: (
    fx.msg(conn, sid, T0, role="user"),
    fx.route(conn, sid, "claude-opus-5", "custom", T0, T0 + 600,
             base_url="https://teamclaude.larid.dedyn.io:3443"),
), gateways=GATEWAYS)
check("segment-route-resolves-provider",
      route_out["segments"][0]["routes"][0]["provider"] == "teamclaude",
      str(route_out["segments"][0]["routes"]))

# The endpoint is internal infrastructure — the panel shows a provider name,
# never a host and port. It must not ride along in the payload.
check("segment-route-hides-base-url",
      "base_url" not in route_out["segments"][0]["routes"][0],
      str(route_out["segments"][0]["routes"][0]))

# Auxiliary work (titles, compression, background review) runs on models the
# operator never chose, but it IS billed — so usage counts it even though the
# route list ignores it.
def aux_only(conn, sid):
    fx.route(conn, sid, "claude-opus-5", "custom", T0, T0 + 60, calls=1, task="title_generation",
             base_url=GATEWAYS["teamclaude"], inp=1_000_000, out=0)


aux = usage_store(aux_only)
check("usage-counts-auxiliary-tasks", len(aux["models"]) == 1 and aux["models"][0]["calls"] == 1,
      str(aux["models"]))


# Grouping happens on the RESOLVED provider, not the raw row. The real store
# writes one gateway under several spellings — 'custom' with a URL, and
# 'custom:codex-lb-oneclick' with none — and grouping before resolution split
# one model into two identical-looking rows in the panel.
def same_gateway_spelled_twice(conn, sid):
    fx.route(conn, sid, "claude-opus-5", "custom", T0, T0 + 60, calls=4,
             base_url=GATEWAYS["teamclaude"], inp=1_000_000, out=0)
    fx.route(conn, sid, "claude-opus-5", "custom:teamclaude", T0, T0 + 60, calls=6,
             base_url="", inp=1_000_000, out=0)


merged = usage_store(same_gateway_spelled_twice)
check("usage-merges-one-provider-into-one-row", len(merged["models"]) == 1, str(merged["models"]))
check("usage-merges-sums-calls", merged["models"][0]["calls"] == 10, str(merged["models"]))
check("usage-merges-sums-tokens", merged["models"][0]["inp"] == 2_000_000, str(merged["models"]))
check("usage-merges-sums-cost", merged["models"][0]["costUsd"] == 10.0, str(merged["models"]))

# --- rate parsing -----------------------------------------------------------

# Shape copied from a live openrouter.ai/api/v1/models response.
PAYLOAD = {"data": [
    {"id": "anthropic/claude-opus-5", "pricing": {
        "prompt": "0.000005", "completion": "0.000025",
        "input_cache_read": "0.0000005", "input_cache_write": "0.00000625"}},
    {"id": "~anthropic/claude-opus-latest", "pricing": {"prompt": "0.000005"}},
    {"id": "z-ai/glm-5.3-flash", "pricing": {
        "prompt": "0", "completion": "0", "input_cache_read": None}},
]}

parsed = cc.parse_rates(PAYLOAD)

# OpenRouter quotes dollars per single token; the panel works per million.
check("rates-scale-to-per-million", parsed["anthropic/claude-opus-5"]["in"] == 5.0,
      str(parsed["anthropic/claude-opus-5"]))
check("rates-read-cache-fields", parsed["anthropic/claude-opus-5"]["cacheRead"] == 0.5)
check("rates-read-cache-write", parsed["anthropic/claude-opus-5"]["cacheWrite"] == 6.25)

# '~vendor/model' is an alias for whatever is current; stored usage always
# names a concrete model, so keeping the alias would only risk a wrong match.
check("rates-skip-latest-aliases", "~anthropic/claude-opus-latest" not in parsed, str(list(parsed)))

# A missing cache field is 0, not a crash — many models publish no cache price.
check("rates-absent-field-is-zero", parsed["z-ai/glm-5.3-flash"]["cacheRead"] == 0.0)

# A free model is priced, not unpriced: "$0.00" is true, "no rate" is not.
check("rates-free-model-is-priced",
      cc.estimate_cost("glm-5.3-flash", "z-ai",
                       {"inp": 1_000_000, "out": 0, "cacheRead": 0, "cacheWrite": 0},
                       parsed) == 0.0)

check("rates-empty-payload-is-empty", cc.parse_rates({}) == {})

# --- provider config shapes -------------------------------------------------

# Hermes accepts the endpoint under either key, and the real config.yaml uses
# both: 'base_url' for the codex gateways, 'api' for teamclaude. Reading only
# one of them silently drops a provider and leaves its rows labelled 'custom'.
CONFIG = {"providers": {
    "teamclaude": {"api": "https://teamclaude.larid.dedyn.io:3443"},
    "codex-lb": {"base_url": "https://codexlb.larid.dedyn.io:2499/backend-api/codex"},
    "no-endpoint": {"model": "x"},
}}

parsed_gateways = cc.gateways_from_config(CONFIG)
check("config-reads-api-key", parsed_gateways.get("teamclaude") == "https://teamclaude.larid.dedyn.io:3443",
      str(parsed_gateways))
check("config-reads-base-url-key",
      parsed_gateways.get("codex-lb") == "https://codexlb.larid.dedyn.io:2499/backend-api/codex")
check("config-skips-endpointless-provider", "no-endpoint" not in parsed_gateways, str(parsed_gateways))
check("config-guards-empty", cc.gateways_from_config({}) == {})

# --- model name normalisation -----------------------------------------------

# Hermes and OpenRouter spell the same model differently, and a literal
# comparison silently prices nothing: the store says 'claude-opus-5-5' where
# the rate list publishes 'claude-opus-5.5'. That looked like a missing rate
# until the two lists were compared side by side.
NAMED = {
    "anthropic/claude-opus-5": {"in": 5.0, "out": 25.0, "cacheRead": 0.5, "cacheWrite": 6.25},
    "anthropic/claude-opus-5.5": {"in": 4.0, "out": 20.0, "cacheRead": 0.2, "cacheWrite": 5.0},
    "anthropic/claude-haiku-4.5": {"in": 1.0, "out": 5.0, "cacheRead": 0.1, "cacheWrite": 1.25},
}

million = {"inp": 1_000_000, "out": 0, "cacheRead": 0, "cacheWrite": 0}

check("name-dashed-version-matches-dotted",
      cc.estimate_cost("claude-opus-5-5", "anthropic", million, NAMED) == 4.0,
      str(cc.estimate_cost("claude-opus-5-5", "anthropic", million, NAMED)))

# 5.5 must price as 5.5, never fall back to 5 — the rates differ ($4 vs $5) and
# a silent substitution reports a wrong number as if it were exact.
check("name-does-not-fall-back-to-older-version",
      cc.estimate_cost("claude-opus-5-5", "anthropic", million, NAMED) != 5.0)

# The real danger is the case where the exact rate is ABSENT: rewriting must
# not then walk down to the previous version. With 5.5 unpublished, a 5.5 row
# stays unpriced rather than quietly billing at 5's higher rate.
OLDER_ONLY = {"anthropic/claude-opus-5": NAMED["anthropic/claude-opus-5"]}
check("name-missing-version-does-not-borrow-older-rate",
      cc.estimate_cost("claude-opus-5-5", "anthropic", million, OLDER_ONLY) is None,
      str(cc.estimate_cost("claude-opus-5-5", "anthropic", million, OLDER_ONLY)))

# An exact name still wins over any rewriting.
check("name-exact-match-preferred",
      cc.estimate_cost("claude-opus-5", "anthropic", million, NAMED) == 5.0)

# A dated snapshot bills at its base model's rate.
check("name-dated-snapshot-uses-base",
      cc.estimate_cost("claude-haiku-4-5-20251001", "anthropic", million, NAMED) == 1.0,
      str(cc.estimate_cost("claude-haiku-4-5-20251001", "anthropic", million, NAMED)))

# Rewriting must not invent a match: an unknown model stays unpriced rather
# than being bent into the nearest published name.
check("name-unknown-still-unpriced",
      cc.estimate_cost("glm-4.6v-flash", "z-ai", million, NAMED) is None)
check("name-near-miss-not-forced",
      cc.estimate_cost("claude-opus-9-9", "anthropic", million, NAMED) is None)

# --- rows with no provider at all -------------------------------------------

# A real session carries a 'vision' row with an empty provider AND empty URL:
# nothing to resolve it from. It must not read as a provider literally called
# "unknown" beside the real ones, and it must not silently vanish either —
# those calls happened and are billed.
def unattributed_row(conn, sid):
    fx.route(conn, sid, "claude-opus-5", "custom", T0, T0 + 600, calls=10,
             base_url=GATEWAYS["teamclaude"], inp=1_000_000, out=0)
    fx.route(conn, sid, "claude-opus-5", "", T0, T0 + 60, calls=1, task="vision",
             base_url="", inp=1000, out=10)


orphan = usage_store(unattributed_row)
providers = [m["provider"] for m in orphan["models"]]
check("unattributed-row-is-not-a-provider-named-unknown", "unknown" not in providers, str(providers))

# The vendor is still derivable from the model name, so the row stays priced
# and the session total stays complete.
check("unattributed-row-still-priced",
      all(m["costUsd"] is not None for m in orphan["models"]), str(orphan["models"]))
check("unattributed-row-keeps-total-complete", orphan["costComplete"] is True)
check("unattributed-row-counts-its-calls",
      sum(m["calls"] for m in orphan["models"]) == 11, str(orphan["models"]))

# --- report -----------------------------------------------------------------

print(f"  {checks - len(failures)}/{checks} checks passed")
for failure in failures:
    print(f"  ✗ {failure}")
sys.exit(1 if failures else 0)
