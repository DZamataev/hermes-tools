# comp-count: session timeline popover

**Date:** 2026-09-22
**Status:** approved design, ready for an implementation plan

## Problem

`comp-count` renders one number in the status bar — the focused session's
compaction count, read from `host.state.focusedUsage`. That number answers "how
many times was this chat compacted" and nothing else. It cannot say which models
ran, on which providers, when the operator was actually working, or when those
compactions happened.

The session store already holds all of it. Nothing new needs to be collected.

## Outcome

Clicking the chip opens a popover (the `provider-limits` pattern:
`Popover` + `PopoverTrigger asChild` + `PopoverContent side="top" align="end"`)
listing the session as **working segments**. One segment is one continuous
stretch of work, split wherever the session went quiet for more than an hour.

Each segment renders as a Gantt-style row:

```
03.09 16:35–17:47   █▂▂▂▁▃▃▃▄▃▂▁▂▃▂▃▃▂▂▃    1ч12м
                    ▲      ▲       ▲   gpt-5.6-sol · codex-lb-oneclick
                    96 промптов · 1131 вызов инструментов · 🧳3
                    patch×369, terminal×288, read_file×219
```

- **Density sparkline** — the segment's span cut into 20 buckets, one
  `▁▂▃▄▅▆▇█` block per bucket, scaled to that segment's own peak. It shows where
  inside the stretch the work actually happened.
- **Compaction markers** — a `▲` row under the sparkline, each placed in the
  bucket the compaction fell into. Position, not a list of times: that is the
  one thing a list cannot show.
- **Route** — model · provider for the segment.
- **Counts** — operator prompts, tool calls, compaction count.
- **Top three tools** — what the segment was made of.

## Why a backend half

The desktop SDK cannot produce this. `host.request('session.history')` — the
only historical door a disk plugin has — strips exactly the two row kinds this
feature is built on:

- model-switch rows persist as `role=user` with a `[System:` prefix and are
  dropped by `_is_display_hidden_marker` (`tui_gateway/session_history.py:137`);
- compaction handoffs are dropped by `project_compaction_message_for_display`
  (`session_history.py:187`).

So `comp-count` becomes a unified package, the same shape as `provider-limits`:

```
plugins/comp-count/
  plugin.yaml
  dashboard/manifest.json        # "api": "plugin_api.py"
  dashboard/plugin_api.py        # reads state.db, returns segments
  desktop/plugin.js              # chip + popover
  tests/
```

It must be added to `plugins.enabled` in `config.yaml` or its routes are never
imported, and the gateway must restart — backend routes mount at startup.
`setup_hermes_tools.sh` already does this for `provider-limits`; it grows the
same three steps for `comp-count`, and the old
`~/.hermes/desktop-plugins/comp-count/` directory is removed so the plugin does
not load twice under the same id.

The backend opens the profile's `state.db` **read-only** and answers
`GET /api/plugins/comp-count/timeline?session=<stored id>&profile=<name>`.

The renderer passes `host.state.focusedStoredSessionId` (never
`focusedSessionId`: runtime ids do not survive a reload and do not key
`state.db`) and `host.state.focusedSessionProfile`, so the panel follows tile
focus and reads the store that actually owns the session.

## Algorithm

Verified against four real sessions before being written down. Each rule below
exists because the naive version measurably failed.

### Segments

Cluster on **all** activity rows, not on prompts alone, splitting at gaps over
`GAP = 3600s`.

> A turn that runs tools for an hour after a single prompt is one working
> stretch. Clustering on prompts alone tore such turns in half and produced
> segments that overlapped each other — observed on
> `20260903_124539_810c57`, where segment 2 started before segment 1 had ended.

Then:

- a cluster with no operator prompt is dropped (resume, background review — the
  operator was not there);
- each kept cluster starts at its first prompt, not at its first machine row.

### Prompts

An operator prompt is `role='user'` **excluding** `_compressed_summary=1`,
`display_kind IN ('hidden','auto_continue','model_switch')`, and content
starting `[System:`.

> The `_compressed_summary` exclusion is not cosmetic: a compaction handoff is
> persisted as a `role=user` carrier row, so counting it as a prompt reported 74
> prompts on a session that has 58.

Rows are read with `active = 1 OR compacted = 1`. `active = 1` alone returns 13
of those 58 — compaction-archived history is most of a long session.

### Routes

From `session_model_usage`, `task = ''` only.

> Auxiliary tasks (`title_generation`, `vision`, `compression`,
> `background_review`) run on other models and must not be presented as a model
> the operator chose. `20260903_124539_810c57` runs `glm-4.5-flash` for
> compression alone.

A route matches a segment when its `[first_seen, last_seen]` window overlaps the
segment. A segment may legitimately list several, joined by `→`.

**Known limit, accepted:** `session_model_usage` is an aggregate keyed by route,
so a route returning later keeps its original `first_seen`. Within a long
segment the set of routes is sound; their exact order is not. Clamping each
window at the next route's start was tried and rejected — it left the tail of
`20260907_112911_4b3fc5` with no route at all ("маршрут неизвестен" on real
rows). Listing the overlapping routes is the honest projection.

### Compactions

`_compressed_summary = 1`, bucketed by position within the segment. All 18
compactions of `20260907_112911_4b3fc5` land inside a segment; none are orphaned.

## Deliberately excluded

- **Cost.** `cost_status='unknown'` on 595 of 1258 route rows and
  `actual_cost_usd` is 0 everywhere. Rendering `$0.00` next to a paid provider
  states something false.
- **Context growth over time.** `messages.token_count` is NULL in all 130 851
  rows and no usage snapshots are persisted; there is nothing to plot. The live
  context percentage for the focused session comes from `focusedUsage` in the
  panel header. Compaction *times* are shown, which is what was asked for.
- **A day-ribbon heat map.** Prototyped and dropped as redundant next to the
  per-segment sparkline.

## Rendering rules

- A non-empty bucket never renders as blank. `round(n/peak*8)` returns 0 for a
  sparse bucket next to a tall peak, which would hide real work; the floor is
  `▁`.
- Colors come from `var(--ui-*)` theme variables only, never literals — a disk
  plugin is not scanned by Tailwind and a hardcoded color breaks on theme switch.
- The panel is ~360px (`provider-limits` uses 328px); 20 sparkline blocks fit.
- Sparkline and marker rows use a tabular/monospace treatment so blocks and
  markers line up column for column.
- Every payload field is read defensively (`list()`/`pct()`-style guards): the
  data crosses a process boundary, and one malformed field must degrade a row,
  never throw during render and take the status bar down with it.
- Strings are Russian, matching how the operator reads this panel; the existing
  `provider-limits` panel is English and stays English.

## Failure and empty states

| Condition | Behaviour |
|---|---|
| Backend not mounted (`ctx.rest` errors) | Chip keeps showing the live compaction count; panel explains the plugin must be in `plugins.enabled` and the gateway restarted |
| No focused session / draft chat | Panel says there is no session yet |
| Session has no prompt-bearing segment | Panel says so; it is not an error |
| A segment has no matching route | That row reads "маршрут неизвестен" rather than inventing one |

The chip must never regress: it keeps rendering `🧳 N` from `focusedUsage` with
the existing clamp (`Math.max(0, Math.trunc(Number(x) || 0))`) whatever the
backend does.

## Testing

Extending the existing suites; `bun run check` stays the entry point and gains a
`comp-count` backend suite next to the renderer one.

1. **Backend, against a synthetic `state.db`** — a fixture store built in a temp
   dir, asserting each rule that was found by probing:
   - a one-prompt turn with an hour of tool rows stays ONE segment;
   - segments never overlap and are ordered;
   - a compaction handoff is not counted as a prompt (the 74-vs-58 regression);
   - `include_compacted` rows are read (the 13-vs-58 regression);
   - an auxiliary-task route never appears as a segment route;
   - a cluster with no operator prompt is dropped.
2. **Renderer, pure functions** — bucketing, the non-empty-bucket floor, marker
   placement, duration formatting, and the defensive guards against malformed
   payloads.
3. **Chip regression** — the existing `plugin.test.mjs` cases keep passing
   unchanged after the move to `plugins/comp-count/desktop/plugin.js`.
4. **Mutation check, by hand** — break each rule in the source, watch the
   matching test fail, restore it. No wrapper script.
5. **Live verification** — install, restart the gateway, open the popover on a
   real long session and compare against the same session's rows in `state.db`.

## Files

| Path | Change |
|---|---|
| `plugins/comp-count/plugin.yaml` | new |
| `plugins/comp-count/dashboard/manifest.json` | new |
| `plugins/comp-count/dashboard/plugin_api.py` | new — timeline route |
| `plugins/comp-count/desktop/plugin.js` | moved from `desktop-plugins/comp-count/`, gains the popover |
| `plugins/comp-count/tests/` | new backend + renderer suites; chip suite moves here |
| `desktop-plugins/comp-count/` | removed |
| `setup_hermes_tools.sh` | installs the unified package, enables the plugin, removes the stale disk copy |
| `scripts/check.mjs` | the `comp-count` suite points at the new path |
| `README.md` | layout section reflects the move |
