"""Provider limits — backend routes, mounted at /api/plugins/provider-limits/.

Reads the quota/usage APIs of the *custom* providers declared in ``config.yaml``
and normalises them into one shape the desktop status bar can render.

Two adapters, selected from the provider's own config (no separate plugin
config to drift out of sync):

* ``anthropic_oauth_proxy`` capability  → TeamClaude ``GET /teamclaude/quota``
  (``x-api-key``). Per-account 5h / weekly buckets plus a weighted aggregate.
* ``transport: codex_responses``        → codex-lb ``GET /v1/usage``
  (``Authorization: Bearer``). Aggregate 5h / 7d credit windows; the key's own
  ``limits`` are preferred over ``upstream_limits`` when they differ.

The API key is read from the provider's own ``key_env`` and is sent ONLY to that
provider's upstream (``x-api-key`` / ``Authorization``). It is never copied into
a response, an error string, or a log record: failures are reported as
``HTTP <status>`` or the exception's class name, so nothing key-shaped can reach
the renderer.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit, urlunsplit

import httpx
from fastapi import APIRouter, Query

log = logging.getLogger(__name__)

router = APIRouter()

# One refresh per this many seconds at most; both upstreams poll their own
# upstream on a timer, so a tighter loop buys nothing and costs a round trip
# per desktop window.
CACHE_TTL_SECONDS = 60.0
REQUEST_TIMEOUT_SECONDS = 12.0

# Public Statuspage endpoints for the services our providers ultimately proxy
# to. A quota panel answers "am I out of budget"; these answer the other half of
# "why is nothing working" — an upstream outage, which no amount of remaining
# quota fixes.
#
# `component` names the one entry on that page these providers actually depend
# on, so a red "Sora" or "claude.ai" does not read as an API outage. The names
# are matched exactly against the page's own component list; when a page renames
# one, the overall page status still shows and only the component line drops out
# (`_status_payload` treats a missing component as unknown, never as healthy).
#
# status.anthropic.com 301s to status.claude.com — the canonical host is used
# directly so the fetch needs no redirect following.
STATUS_PAGES = (
    {"id": "anthropic", "label": "Anthropic",
     "url": "https://status.claude.com/api/v2/summary.json",
     "component": "Claude API (api.anthropic.com)"},
    {"id": "openai", "label": "OpenAI",
     "url": "https://status.openai.com/api/v2/summary.json",
     "component": "Codex API"},
)
# Status pages change far more slowly than quota does, and an outage banner that
# is two minutes stale is still useful. Longer TTL, separate from the quota one.
STATUS_CACHE_TTL_SECONDS = 120.0
STATUS_TIMEOUT_SECONDS = 8.0

_cache: Optional[Dict[str, Any]] = None
_cache_expires_at = 0.0
_cache_lock = asyncio.Lock()

_status_cache: Optional[List[Dict[str, Any]]] = None
_status_cache_expires_at = 0.0
_status_lock = asyncio.Lock()


# --- config -----------------------------------------------------------------

def _providers_config() -> Dict[str, Dict[str, Any]]:
    from hermes_cli.config import load_config_readonly

    providers = load_config_readonly().get("providers")
    return providers if isinstance(providers, dict) else {}


def _api_key(entry: Dict[str, Any]) -> str:
    from hermes_cli.config import get_env_value_prefer_dotenv

    key_env = entry.get("key_env")
    if not isinstance(key_env, str) or not key_env:
        return ""
    return get_env_value_prefer_dotenv(key_env) or ""


def _origin(url: str) -> str:
    """Scheme+host+port of a URL — both APIs live at the service root, while the
    configured ``base_url`` points at a wire-protocol path (``/backend-api/codex``)."""
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return ""
    return urlunsplit((parts.scheme, parts.netloc, "", "", ""))


def _adapter_for(entry: Dict[str, Any]) -> Optional[str]:
    caps = entry.get("capabilities")
    if isinstance(caps, dict) and caps.get("anthropic_oauth_proxy"):
        return "teamclaude"
    if entry.get("transport") == "codex_responses":
        return "codex-lb"
    return None


def _base_url_for(kind: str, entry: Dict[str, Any]) -> str:
    raw = entry.get("api") if kind == "teamclaude" else entry.get("base_url")
    raw = raw or entry.get("base_url") or entry.get("api") or ""
    return _origin(str(raw))


def discover_targets() -> List[Dict[str, Any]]:
    """Every configured provider this plugin knows how to query, in config order."""
    targets = []
    for name, entry in _providers_config().items():
        if not isinstance(entry, dict):
            continue
        kind = _adapter_for(entry)
        if not kind:
            continue
        base_url = _base_url_for(kind, entry)
        if not base_url:
            continue
        targets.append({
            "id": name,
            "label": str(entry.get("name") or name),
            "kind": kind,
            "base_url": base_url,
            "key": _api_key(entry),
        })
    return targets


# --- normalisation ----------------------------------------------------------

def _pct(value: Any) -> Optional[float]:
    """A 0..1 ratio as a percentage, or None when the upstream had no number."""
    try:
        ratio = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(100.0, ratio * 100.0))


def _bucket(key: str, label: str, remaining_pct: Optional[float], reset_at: Any,
            detail: str = "") -> Optional[Dict[str, Any]]:
    if remaining_pct is None:
        return None
    return {
        "key": key,
        "label": label,
        "remainingPct": round(remaining_pct, 1),
        "resetAt": reset_at,
        "detail": detail,
    }


def _epoch_ms(value: Any) -> Optional[int]:
    """Milliseconds since epoch from either a number or an ISO-8601 string."""
    if isinstance(value, (int, float)) and value > 0:
        return int(value if value > 1e11 else value * 1000)
    if isinstance(value, str) and value:
        from datetime import datetime
        try:
            text = value.replace("Z", "+00:00")
            return int(datetime.fromisoformat(text).timestamp() * 1000)
        except ValueError:
            return None
    return None


_TEAMCLAUDE_BUCKETS = (
    # key, aggregate label, per-account label, WINDOW CLASS
    # The window class is the machine-readable one the UI selects on ("give me
    # the 5h row"); the labels are display text and may be reworded freely.
    ("fiveHour", "5 hours", "5h", "5h"),
    ("weeklyShared", "Week", "week", "7d"),
    ("weeklySonnet", "Week (Sonnet)", "week/Sonnet", "7d"),
    ("weeklyFable", "Week (Fable)", "week/Fable", "7d"),
)


def _teamclaude_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    """TeamClaude /teamclaude/quota → buckets + per-account rows.

    ``aggregate`` is weighted by seat tier (a Max-20x account counts 20x a team
    seat), which is exactly the pooled capacity the router can actually draw on.

    All four model-family buckets are carried, ``weeklySonnet`` included. It is a
    distinct upstream limit (``unified7dSonnet``) that merely FALLS BACK to the
    shared weekly figure while Anthropic does not report it separately — see
    teamclaude's ``src/quota-summary.js::accountBuckets``. Dropping it because
    today's payload happens to duplicate ``weeklyShared`` would mean a silently
    missing row on the day Sonnet gets its own quota and runs out on its own.
    Identical rows are collapsed at render time instead, so the duplication
    costs nothing while the fallback lasts.
    """
    aggregate = data.get("aggregate") if isinstance(data.get("aggregate"), dict) else {}
    buckets = []
    for key, label, _, window in _TEAMCLAUDE_BUCKETS:
        row = aggregate.get(key)
        if not isinstance(row, dict):
            continue
        known = row.get("knownAccounts")
        bucket = _bucket(
            key, label, _pct(row.get("remaining")), _epoch_ms(row.get("nextResetAt")),
            f"{known} accounts reporting" if isinstance(known, int) else "")
        if bucket:
            bucket["source"] = str(row.get("source") or "")
            bucket["window"] = window
            buckets.append(bucket)

    accounts = []
    for account in (data.get("accounts") if isinstance(data.get("accounts"), list) else []):
        if not isinstance(account, dict):
            continue
        account_buckets = []
        for key, _, short, window in _TEAMCLAUDE_BUCKETS:
            row = account.get("buckets", {}).get(key) if isinstance(account.get("buckets"), dict) else None
            if not isinstance(row, dict):
                continue
            bucket = _bucket(key, short, _pct(row.get("remaining")), _epoch_ms(row.get("resetAt")))
            if bucket:
                bucket["source"] = str(row.get("source") or "")
                bucket["window"] = window
                account_buckets.append(bucket)
        tier = account.get("tier") if isinstance(account.get("tier"), dict) else {}
        accounts.append({
            "name": str(account.get("name") or "account"),
            "disabled": bool(account.get("disabled")),
            "status": str(account.get("status") or ""),
            "tier": str(tier.get("rateLimitTier") or ""),
            "buckets": account_buckets,
        })

    return {"buckets": buckets, "accounts": accounts, "poolWindows": []}


_CODEX_WINDOW_LABELS = {"5h": "5 hours", "7d": "Week", "daily": "Day", "monthly": "Month"}
# Upstream names the same window differently depending on which list it came
# from (`weekly` on an api_key_limit, `7d` on an aggregate) — collapse to one
# key or the UI shows two rows both labelled "Week".
_CODEX_WINDOW_ALIASES = {"weekly": "7d", "hourly": "1h", "5hour": "5h"}


def _codex_limits(rows: Any, source: str) -> List[Dict[str, Any]]:
    buckets = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        try:
            max_value = float(row.get("max_value"))
            remaining = float(row.get("remaining_value"))
        except (TypeError, ValueError):
            continue
        # A zero/negative ceiling carries no ratio. Report it as an exhausted
        # limit rather than dropping the row: a quota of 0 means "nothing may be
        # spent", and silently omitting it renders as "no such limit".
        window = _CODEX_WINDOW_ALIASES.get(str(row.get("limit_window") or ""), str(row.get("limit_window") or ""))
        limit_type = str(row.get("limit_type") or "")
        pct = 0.0 if max_value <= 0 else max(0.0, min(100.0, remaining / max_value * 100.0))
        bucket = _bucket(
            f"{source}:{window}:{limit_type}", _CODEX_WINDOW_LABELS.get(window, window or "limit"),
            pct, _epoch_ms(row.get("reset_at")),
            f"{remaining:,.0f} / {max_value:,.0f} {limit_type}".strip())
        if bucket:
            bucket["limitType"] = limit_type
            bucket["window"] = window
            buckets.append(bucket)
    return buckets


def _codex_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    """codex-lb /v1/usage → aggregate credit windows.

    ``limits`` is what this API key may still spend, ``upstream_limits`` what the
    account pool behind it has left. Either can run out first, so for the SAME
    window and the SAME resource the binding one — whichever has less left — is
    what actually stops a request, and that is the one kept.

    Collapsing is keyed on ``(window, limit_type)``, never on the window alone:
    the one-click node reports a weekly ``cost_usd`` spend cap (99.9% left)
    beside a 7d ``credits`` pool (70.4% left). Those are different resources that
    run out independently, and merging them under one "Week" row would hide
    whichever is not shown — the version that keyed on the window alone did
    exactly that.
    """
    tightest: Dict[tuple, Dict[str, Any]] = {}
    for bucket in _codex_limits(data.get("limits"), "key") + _codex_limits(data.get("upstream_limits"), "pool"):
        source, window, limit_type = bucket["key"].split(":", 2)
        slot = (window, limit_type)
        current = tightest.get(slot)
        if current is None or bucket["remainingPct"] < current["remainingPct"]:
            tightest[slot] = {**bucket, "key": f"{window}:{limit_type}",
                              "detail": f"{bucket['detail']} ({'this key' if source == 'key' else 'account pool'})"}

    # Two resources in one window each need their own label, or the popover shows
    # two identical "Week" rows with different numbers.
    windows = [w for w, _ in tightest]
    for (window, limit_type), bucket in tightest.items():
        if windows.count(window) > 1 and limit_type:
            bucket["label"] = f"{bucket['label']} ({limit_type})"

    order = ["1h", "5h", "daily", "7d", "monthly"]
    buckets = sorted(
        tightest.items(),
        key=lambda kv: (order.index(kv[0][0]) if kv[0][0] in order else len(order), kv[0][1]))
    buckets = [b for _, b in buckets]

    # `account_pool_usage` is the pool's REMAINING percentage per WINDOW, despite
    # both the "usage" in its name and the account-shaped {primary, secondary}
    # field names. Confirmed in codex-lb's own source on sw1-aeza
    # (app/modules/proxy/api.py::_build_account_pool_usage →
    #  app/modules/api_keys/service.py::_compute_pooled_credits):
    #   primary   = remaining_percent_from_used(summarize_usage_window(primary_rows))
    #   secondary = ditto for secondary_rows
    # and `_build_codex_usage_payload_for_api_key` in the same file maps the pair
    # onto windows explicitly: primary = 5h (or daily), secondary = 7d (or weekly).
    # So they are time windows pooled across accounts, NOT two accounts.
    #
    # Two consequences this code must respect, both visible in live payloads:
    #  * `primary` is None when the short window has no live sample
    #    (`capacity_credits == 0 or not has_live_primary`) — absent, not zero.
    #  * a plan reporting only a weekly window has its primary row REMAPPED into
    #    secondary (`normalize_weekly_only_rows`), so `primary` can be computed
    #    over weekly data and disagree with the 5h figure in `upstream_limits`.
    # Both are why these are shown as their own rows rather than merged into the
    # limit rows above: they are a second, differently-derived opinion on the
    # same two windows.
    pool = data.get("account_pool_usage")
    pool_windows = []
    for slot, label in (("primary", "5 hours"), ("secondary", "Week")):
        remaining = pool.get(slot) if isinstance(pool, dict) else None
        if isinstance(remaining, (int, float)):
            pool_windows.append({
                "key": slot,
                "label": label,
                "remainingPct": round(max(0.0, min(100.0, float(remaining))), 1),
            })

    return {"buckets": buckets, "accounts": [], "poolWindows": pool_windows}


# --- upstream service status ------------------------------------------------

# Statuspage's own vocabulary, mapped onto the three states the UI can draw.
# Anything unrecognised is treated as trouble rather than as health: a new
# severity word must not render as green.
_HEALTHY_INDICATORS = frozenset({"none"})
_HEALTHY_COMPONENT_STATUSES = frozenset({"operational"})


def _status_payload(page: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, Any]:
    """One Statuspage summary → the fields the banner draws.

    Two independent signals are kept apart on purpose: the PAGE indicator covers
    the whole product (a broken web UI trips it), while the named COMPONENT is
    the API these providers actually call. Reporting only the page turns an
    unrelated outage into a false alarm; reporting only the component hides a
    site-wide incident the component list has not caught up with yet.
    """
    status = data.get("status") if isinstance(data.get("status"), dict) else {}
    indicator = str(status.get("indicator") or "")
    description = str(status.get("description") or "")

    component_status = ""
    for component in (data.get("components") if isinstance(data.get("components"), list) else []):
        if isinstance(component, dict) and component.get("name") == page["component"]:
            component_status = str(component.get("status") or "")
            break

    incidents = [
        str(incident.get("name") or "incident")
        for incident in (data.get("incidents") if isinstance(data.get("incidents"), list) else [])
        if isinstance(incident, dict)
    ]

    # Unknown component (renamed upstream) must not vote "healthy"; it simply
    # does not vote, and the page indicator decides.
    component_ok = component_status in _HEALTHY_COMPONENT_STATUSES if component_status else None
    page_ok = indicator in _HEALTHY_INDICATORS
    ok = page_ok if component_ok is None else (page_ok and component_ok)

    return {
        "id": page["id"],
        "label": page["label"],
        "ok": ok,
        "indicator": indicator,
        "description": description,
        "component": page["component"],
        "componentStatus": component_status,
        "incidents": incidents[:3],
        "error": None,
    }


async def _fetch_status(client: httpx.AsyncClient, page: Dict[str, Any]) -> Dict[str, Any]:
    base = {"id": page["id"], "label": page["label"], "component": page["component"]}
    try:
        response = await client.get(page["url"], timeout=STATUS_TIMEOUT_SECONDS)
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPStatusError as exc:
        return {**base, "ok": None, "error": f"HTTP {exc.response.status_code}",
                "indicator": "", "description": "", "componentStatus": "", "incidents": []}
    except Exception as exc:
        return {**base, "ok": None, "error": type(exc).__name__,
                "indicator": "", "description": "", "componentStatus": "", "incidents": []}

    if not isinstance(data, dict):
        return {**base, "ok": None, "error": "unexpected payload",
                "indicator": "", "description": "", "componentStatus": "", "incidents": []}

    try:
        return _status_payload(page, data)
    except Exception:
        log.exception("provider-limits: failed to parse %s status page", page["id"])
        return {**base, "ok": None, "error": "unparseable payload",
                "indicator": "", "description": "", "componentStatus": "", "incidents": []}


async def collect_status(force: bool = False) -> List[Dict[str, Any]]:
    """Upstream status for every configured page, cached separately from quota.

    A status-page outage must never take the quota panel down with it, so the
    caller gets rows carrying ``ok: None`` rather than an exception.
    """
    global _status_cache, _status_cache_expires_at

    async with _status_lock:
        now = time.monotonic()
        if not force and _status_cache is not None and now < _status_cache_expires_at:
            return _status_cache

        async with httpx.AsyncClient(follow_redirects=True) as client:
            rows = list(await asyncio.gather(*(_fetch_status(client, p) for p in STATUS_PAGES)))

        _status_cache = rows
        _status_cache_expires_at = time.monotonic() + STATUS_CACHE_TTL_SECONDS
        return rows


# --- fetching ---------------------------------------------------------------

async def _fetch_target(client: httpx.AsyncClient, target: Dict[str, Any]) -> Dict[str, Any]:
    base = {"id": target["id"], "label": target["label"], "kind": target["kind"]}

    if not target["key"]:
        return {**base, "ok": False, "error": "no API key configured", "buckets": [], "accounts": [], "poolWindows": []}

    if target["kind"] == "teamclaude":
        url = f"{target['base_url']}/teamclaude/quota"
        headers = {"x-api-key": target["key"]}
        parse = _teamclaude_payload
    else:
        url = f"{target['base_url']}/v1/usage"
        headers = {"Authorization": f"Bearer {target['key']}"}
        parse = _codex_payload

    try:
        response = await client.get(url, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPStatusError as exc:
        return {**base, "ok": False, "error": f"HTTP {exc.response.status_code}", "buckets": [], "accounts": [], "poolWindows": []}
    except Exception as exc:  # network, TLS, JSON — all "upstream did not answer"
        return {**base, "ok": False, "error": type(exc).__name__, "buckets": [], "accounts": [], "poolWindows": []}

    if not isinstance(data, dict):
        return {**base, "ok": False, "error": "unexpected payload", "buckets": [], "accounts": [], "poolWindows": []}

    try:
        parsed = parse(data)
    except Exception:
        log.exception("provider-limits: failed to parse %s payload", target["id"])
        return {**base, "ok": False, "error": "unparseable payload", "buckets": [], "accounts": [], "poolWindows": []}

    return {**base, "ok": True, "error": None, **parsed}


async def collect(targets: Optional[List[Dict[str, Any]]] = None,
                  force_status: bool = False) -> Dict[str, Any]:
    """Quota for every configured provider, plus upstream service status.

    The two run concurrently and are independent: status pages are public and
    keyless, so they still answer when every provider key is missing, and a
    status page being down never costs a quota row.
    """
    targets = discover_targets() if targets is None else targets

    if not targets:
        return {"fetchedAt": int(time.time() * 1000), "providers": [],
                "services": await collect_status(force=force_status)}

    async with httpx.AsyncClient(follow_redirects=False) as client:
        providers, services = await asyncio.gather(
            asyncio.gather(*(_fetch_target(client, t) for t in targets)),
            collect_status(force=force_status))

    return {"fetchedAt": int(time.time() * 1000),
            "providers": list(providers), "services": list(services)}


@router.get("/limits")
async def limits(refresh: bool = Query(False, description="Bypass the 60s cache")):
    global _cache, _cache_expires_at

    async with _cache_lock:
        now = time.monotonic()
        if not refresh and _cache is not None and now < _cache_expires_at:
            return {**_cache, "cached": True}

        payload = await collect(force_status=refresh)
        _cache = payload
        _cache_expires_at = time.monotonic() + CACHE_TTL_SECONDS
        return {**payload, "cached": False}
