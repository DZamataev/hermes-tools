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

import json
import logging
import os
import sqlite3
import time
from collections import Counter
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

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
        "SELECT model, billing_provider, billing_base_url, first_seen, last_seen"
        "  FROM session_model_usage"
        " WHERE session_id = ? AND task = '' AND model <> 'unknown'"
        "   AND first_seen IS NOT NULL AND last_seen IS NOT NULL"
        " ORDER BY first_seen, rowid",
        (session_id,),
    ).fetchall()
    return [{"model": model, "provider": provider or "", "base_url": url or "",
             "first_seen": float(first), "last_seen": float(last)}
            for model, provider, url, first, last in rows]


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


def build_timeline(db_path: str, session_id: str,
                   gateways: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
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

    # The route line carries the provider name the operator sees on every
    # segment, so it gets the same resolution the totals do.
    for route in routes:
        route["provider"] = provider_name(
            route.get("provider", ""), route.get("base_url", ""), gateways or {})

    segments = []
    for cluster in _cluster(events):
        # No operator prompt means this stretch was machinery — a resume, a
        # background review. The operator was not at the keyboard.
        if not any(e["prompt"] for e in cluster["events"]):
            continue
        # Trim the machine-only head so a segment reads "started when I started
        # working", not when a background turn happened to wake up. A COMPACTION
        # anchors the segment too: on a real session four compactions fell in
        # that head, and anchoring on prompts alone dropped them off the
        # timeline entirely — the one thing the panel exists to show.
        anchors = [e["ts"] for e in cluster["events"] if e["prompt"] or e["compaction"]]
        cluster["start"] = min(anchors)
        cluster["events"] = [e for e in cluster["events"] if e["ts"] >= cluster["start"]]
        segments.append(_segment(cluster, routes))

    return {
        "sessionId": session_id,
        "segments": segments,
        "generatedAt": int(time.time() * 1000),
    }


# --- provider identity ------------------------------------------------------

# Vendors whose own price list applies to a request. A gateway is not one of
# these: it forwards to a vendor and bills through a subscription.
_VENDORS = ("anthropic", "openai", "google", "openrouter", "deepseek", "zai", "nous")

# Which vendor each gateway proxies. Unlike the gateway hosts (read from the
# user's config.yaml) this is a property of the software, not of one install:
# TeamClaude speaks the Anthropic API whoever runs it.
_GATEWAY_VENDORS = {"teamclaude": "anthropic", "codex-lb": "openai", "codex-lb-oneclick": "openai"}


def _url_key(url: str) -> str:
    """Host plus path, normalised enough that one gateway has one key.

    The real store holds '/backend-api/codex' and '/backend-api/codex/' for the
    same provider, so a trailing slash must not split it in two.
    """
    parts = urlsplit((url or "").strip().lower())
    if not parts.netloc:
        return ""
    return f"{parts.netloc}{parts.path.rstrip('/')}"


def provider_name(billing_provider: str, base_url: str, gateways: Dict[str, str]) -> str:
    """The provider the operator would recognise.

    ``billing_provider`` alone is not enough: 291 rows of the real store say
    'custom', and three different gateways are hiding behind that one word. The
    base URL does separate them, matched against the user's own configured
    providers.
    """
    raw = (billing_provider or "").strip()
    # Hermes writes both 'custom:codex-lb' and a bare 'codex-lb'.
    if raw.lower().startswith("custom:"):
        return raw.split(":", 1)[1] or "custom"
    if raw and raw.lower() not in ("custom", "local", ""):
        return raw

    key = _url_key(base_url)
    if key:
        for name, url in (gateways or {}).items():
            if _url_key(url) == key:
                return name
    return raw or "unknown"


def upstream_vendor(provider: str, base_url: str) -> str:
    """The vendor whose published rates apply to this request.

    Pricing follows the vendor that actually serves the tokens, so a gateway
    resolves to the API it forwards to.
    """
    name = (provider or "").strip().lower()
    if name in _GATEWAY_VENDORS:
        return _GATEWAY_VENDORS[name]
    if name in _VENDORS:
        return name
    key = _url_key(base_url)
    for gateway, vendor in _GATEWAY_VENDORS.items():
        if gateway in key:
            return vendor
    return name or "unknown"


# --- cost -------------------------------------------------------------------


def estimate_cost(model: str, vendor: str, tokens: Dict[str, int],
                  rates: Dict[str, Dict[str, float]]) -> Optional[float]:
    """List-price cost of these tokens, or None when no rate is published.

    None and 0.0 say different things. A zero would claim the work was free;
    None says the price is unknown, which is what the panel must show for a
    model the rate source does not carry.
    """
    rate = (rates or {}).get(f"{vendor}/{model}")
    if not rate:
        return None
    return (
        tokens.get("inp", 0) * rate.get("in", 0.0)
        + tokens.get("out", 0) * rate.get("out", 0.0)
        + tokens.get("cacheRead", 0) * rate.get("cacheRead", 0.0)
        + tokens.get("cacheWrite", 0) * rate.get("cacheWrite", 0.0)
    ) / 1_000_000.0


def _read_usage(conn: sqlite3.Connection, session_id: str) -> List[Dict[str, Any]]:
    """Billed usage per model, every task included.

    Auxiliary work (titles, compression, background review) is excluded from
    the ROUTE list because the operator never chose those models — but it is
    billed, so it belongs here.
    """
    rows = conn.execute(
        "SELECT model, billing_provider, billing_base_url, SUM(api_call_count),"
        "       SUM(input_tokens), SUM(output_tokens),"
        "       SUM(cache_read_tokens), SUM(cache_write_tokens)"
        "  FROM session_model_usage"
        " WHERE session_id = ? AND model <> 'unknown'"
        " GROUP BY model, billing_provider, billing_base_url",
        (session_id,),
    ).fetchall()
    return [{"model": model, "billing_provider": provider or "", "base_url": url or "",
             "calls": int(calls or 0), "inp": int(inp or 0), "out": int(out or 0),
             "cacheRead": int(cr or 0), "cacheWrite": int(cw or 0)}
            for model, provider, url, calls, inp, out, cr, cw in rows]


def build_usage(db_path: str, session_id: str, gateways: Dict[str, str],
                rates: Dict[str, Dict[str, float]]) -> Dict[str, Any]:
    """Per-model token totals and list-price cost for the whole session.

    Session-wide on purpose. ``messages.token_count`` is empty for every row of
    the real store, so there is nothing to attribute tokens to a segment with,
    and splitting the total by elapsed time would invent a number.
    """
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = _read_usage(conn, session_id)
    finally:
        conn.close()

    models: List[Dict[str, Any]] = []
    total = 0.0
    complete = True
    # Merge AFTER resolving the provider: the store spells one gateway several
    # ways ('custom' plus a URL, 'custom:teamclaude' with none), and grouping on
    # the raw row shows one model as two identical panel rows.
    merged: Dict[tuple, Dict[str, Any]] = {}
    for row in rows:
        provider = provider_name(row["billing_provider"], row["base_url"], gateways)
        vendor = upstream_vendor(provider, row["base_url"])
        key = (row["model"], provider, vendor)
        bucket = merged.get(key)
        if bucket is None:
            merged[key] = bucket = {"model": row["model"], "provider": provider, "vendor": vendor,
                                    "calls": 0, "inp": 0, "out": 0, "cacheRead": 0, "cacheWrite": 0}
        for field in ("calls", "inp", "out", "cacheRead", "cacheWrite"):
            bucket[field] += row[field]

    for bucket in merged.values():
        cost = estimate_cost(bucket["model"], bucket["vendor"], bucket, rates)
        if cost is None:
            complete = False
        else:
            total += cost
        models.append({**bucket, "costUsd": cost})

    models.sort(key=lambda m: -(m["costUsd"] or 0.0))
    return {
        "models": models,
        "totalCostUsd": total if models else None,
        # False means the total covers only part of the session, so the panel
        # can mark it instead of presenting a short number as the whole bill.
        "costComplete": complete,
    }


# --- rate source ------------------------------------------------------------

# OpenRouter publishes current rates for 454 models over HTTP, including the
# ones this operator runs. Hermes also ships a rate table compiled into
# agent/usage_pricing.py, but it is a hand-copied snapshot that has already
# drifted — it prices gpt-5.6-sol at $5/$30 where OpenRouter says $2/$10 — and
# it carries no entry at all for claude-opus-5. So this is the only source.
_RATES_URL = "https://openrouter.ai/api/v1/models"
_RATES_TTL = 24 * 60 * 60
_rates_memo: Dict[str, Any] = {"at": 0.0, "rates": None}


def _rates_cache_path() -> str:
    base = os.environ.get("COMP_COUNT_CACHE_DIR") or os.path.expanduser("~/.hermes/cache")
    return os.path.join(base, "comp-count-rates.json")


def parse_rates(payload: Dict[str, Any]) -> Dict[str, Dict[str, float]]:
    """OpenRouter's model list reduced to dollars per million tokens.

    OpenRouter quotes dollars per single token, so every figure is scaled here
    once rather than at each use.
    """
    rates: Dict[str, Dict[str, float]] = {}
    for model in payload.get("data") or []:
        model_id = (model.get("id") or "").strip()
        # '~vendor/model' marks an alias that tracks 'latest'; the concrete
        # id is in the list too, and stored usage always names a concrete model.
        if not model_id or "/" not in model_id or model_id.startswith("~"):
            continue
        pricing = model.get("pricing") or {}

        def rate(*names: str) -> float:
            for name in names:
                value = pricing.get(name)
                if value not in (None, ""):
                    try:
                        return float(value) * 1_000_000.0
                    except (TypeError, ValueError):
                        return 0.0
            return 0.0

        entry = {"in": rate("prompt"), "out": rate("completion"),
                 "cacheRead": rate("input_cache_read"), "cacheWrite": rate("input_cache_write")}
        # A free model prices every field at zero; keeping it lets the panel say
        # "$0.00" truthfully instead of "no rate".
        rates[model_id] = entry
    return rates


def load_rates(*, now: Optional[float] = None) -> Dict[str, Dict[str, float]]:
    """Current rates, refreshed daily, surviving a restart and an outage.

    Falls back to the cached copy whenever the fetch fails: a stale rate beats
    a blank panel, and the rates move rarely.
    """
    now = time.time() if now is None else now
    if _rates_memo["rates"] is not None and now - _rates_memo["at"] < _RATES_TTL:
        return _rates_memo["rates"]

    path = _rates_cache_path()
    cached: Optional[Dict[str, Dict[str, float]]] = None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            blob = json.load(handle)
        cached = blob.get("rates")
        if cached and now - float(blob.get("at") or 0) < _RATES_TTL:
            _rates_memo.update(at=float(blob["at"]), rates=cached)
            return cached
    except (OSError, ValueError, KeyError):
        pass

    try:
        import httpx

        with httpx.Client(timeout=20.0) as client:
            response = client.get(_RATES_URL)
            response.raise_for_status()
            rates = parse_rates(response.json())
        if not rates:
            raise ValueError("rate source returned no usable models")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"at": now, "rates": rates}, handle)
        _rates_memo.update(at=now, rates=rates)
        return rates
    except Exception as exc:  # network, parse, or disk — all mean "use the cache"
        log.warning("comp-count: cannot refresh rates: %s", exc)
        if cached:
            _rates_memo.update(at=now, rates=cached)
            return cached
        return {}


def gateways_from_config(config: Dict[str, Any]) -> Dict[str, str]:
    """Provider name to endpoint, from a loaded config mapping.

    Hermes accepts the endpoint under either 'base_url' or 'api', and this
    operator's config uses both. Reading one key only would drop a provider
    and leave its usage rows labelled with the useless 'custom'.
    """
    gateways = {}
    for name, provider in ((config or {}).get("providers") or {}).items():
        entry = provider or {}
        url = (entry.get("base_url") or entry.get("api") or "")
        url = url.strip() if isinstance(url, str) else ""
        if url:
            gateways[str(name)] = url
    return gateways


def configured_gateways() -> Dict[str, str]:
    """Provider name to base URL, straight from the operator's config.

    Read rather than hard-coded: the hosts belong to this install, and a new
    provider should show up in the panel without editing the plugin.
    """
    try:
        from hermes_cli.config import load_config_readonly

        config = load_config_readonly() or {}
    except Exception as exc:
        log.warning("comp-count: cannot read provider config: %s", exc)
        return {}
    return gateways_from_config(config)


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
        gateways = configured_gateways()
        payload = build_timeline(path, session.strip(), gateways)
        # Usage is folded into the one response the panel already fetches: a
        # second endpoint would double the round trips for data the panel never
        # shows separately.
        payload["usage"] = build_usage(path, session.strip(), gateways, load_rates())
        return payload
    except sqlite3.DatabaseError as exc:
        # A corrupt or unreadable store is an operational fault, not a bad
        # request: say so plainly rather than returning an empty timeline that
        # reads as "this session did nothing".
        log.warning("comp-count: cannot read %s: %s", path, exc)
        raise HTTPException(status_code=503, detail="Session store is unavailable") from exc
