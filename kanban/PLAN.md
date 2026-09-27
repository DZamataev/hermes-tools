# PLAN: four Kanban fixes in the Hermes fork

Status: ready for implementation.
Target: the fork `DZamataev/hermes-agent`, branch `kanban-session-notify`,
worktree `/Users/frenzy/dev/hermes-wt/kanban-session-notify`. When it is green,
merge into `develop`, which the working install at `~/.hermes/hermes-agent`
runs. Task 5 also touches this repo (`hermes-tools/kanban`).

All `path:line` references were read at fork commit `d50be22211`. Re-read
them before editing, because lines drift.

## Why

The four changes come from one night of running the `pwa-v1` board
(pwa-looky-messenger) with the orchestrating session as the only supervisor:

| # | What went wrong | Cost |
|---|---|---|
| A | The implementer of card 32 hit the teamclaude quota, fell back to a weaker model without a word, and completed its card as `done` with half the work | found only by reading the profile's `agent.log`; a review ran on a broken tree |
| B | The same card completed with 26 uncommitted files and no commit | the review started on an uncommitted working tree |
| C | A completion reaches the session as a 200-char first line of the summary | the orchestrator ran `show --json` plus a parser on every completion (~15 times in a day) |
| D | Replacing one card in a chain took archive + create + `link` + `unlink` by hand | four commands, and swapping `parent child` silently builds the wrong graph |

## Global constraints

- Tests run only through `scripts/run_tests.sh` (for example
  `scripts/run_tests.sh tests/tools/test_kanban_tools.py`). Never call bare
  `pytest`.
- The venv comes from the working install:
  `env -u PYTHONPATH -u PYTHONHOME ~/.hermes/hermes-agent/venv/bin/python`.
- TDD. Each behaviour starts with a test that fails; show the RED run in the
  report. After GREEN, do one manual mutation check per new guarantee: break
  the source line, watch the test fail, then restore it.
- Every new `config.yaml` key goes into `DEFAULT_CONFIG`
  (`hermes_cli/config_defaults.py`, `"kanban": {` at line 1858) and has a
  runtime reader. This follows the `hermes_cli/AGENTS.md` config rule.
- Do not change `gateway/kanban_watchers_notifier.py` (the Telegram text).
  Telegram keeps its short form; only the `tui` session delivery grows.
- One commit per task. No push until the operator says so.
- If an existing test contradicts the new behaviour, do not edit it. Leave it
  red and name it in the report.

---

## Task 1. Record a model fallback in a Kanban worker and let a profile forbid it

### Today

- Fallback happens in `agent/chat_completion_helpers.py::try_activate_fallback`
  (line 2068). It is logged by `_log_fallback_activated(agent, reason,
  old_model, old_provider, fb_model, fb_provider)` (same file, line 1912; called once,
  at line 2178). This is the one place that knows every
  fact about the switch. The board never learns about it.
- `agent/served_model.py::result_model_fields` (line 59) already derives
  `requested_model` / `served_model` for the turn result. It is not a path to
  the board: the worker's `kanban_complete` handler has no `agent`.
- A worker that dies on a quota wall already exits with
  `KANBAN_RATE_LIMIT_EXIT_CODE` (75), via
  `hermes_cli/cli_single_query.py::_single_query_exit_code` (line 145). The
  dispatcher then requeues it as `rate_limited` without counting a failure
  (`hermes_cli/kanban_db_dispatch.py`, `_DeadWorker.rate_limited`, around line
  1133). That path works; it is simply never reached when a fallback succeeds.

### Change

1. **Board event.** Add `record_model_fallback_from_env(reason, old_model,
   old_provider, fb_model, fb_provider) -> bool` to `tools/kanban_tools.py`,
   next to `heartbeat_current_worker_from_env` (line 519). It uses the same
   bridge shape: return at once unless `_is_dispatcher_owned_worker()`, open
   `_board(None, quiet_close=True)`, never raise. Inside
   `kb.write_txn(conn)` it calls
   `kb._append_event(conn, tid, "model_fallback", {"from_model": old_model,
   "from_provider": old_provider, "to_model": fb_model, "to_provider":
   fb_provider, "reason": str(reason.value if reason else "")},
   run_id=_worker_run_id(tid))`.
2. **Call site.** In `_log_fallback_activated`, after the log line and on
   both branches, add:
   `if os.environ.get("HERMES_KANBAN_TASK"):` with a suppressed import of
   `tools.kanban_tools.record_model_fallback_from_env` and one call to it.
   Copy the guard shape from `agent/activity_tracking.py` lines 72–80, which
   calls into `tools.kanban_tools` the same way.
3. **Policy `kanban.worker_fallback`.**
   - Add the key to the `kanban` section of `DEFAULT_CONFIG` with the value
     `"allow"` and a comment. Allowed values: `allow` | `wait`.
   - At the top of `try_activate_fallback`, right after
     `switch_deferred_by_reset`, add: when
     `agent.delegation_context.owned_kanban_task()` is non-empty and the
     policy is `wait`, record an event and return `_fallback_chain_exhausted
     (agent, reason)`. The event is `model_fallback_refused` with `{"model":
     agent.model, "provider": agent.provider, "reason": ...}`, written through
     a sibling helper `record_fallback_refused_from_env(...)` of the same
     shape as the one in step 1.
   - Read the policy with `load_config()`. The worker runs with `HERMES_HOME`
     set to its profile (`kanban_db_dispatch.py:2900`), so it reads that
     profile's `config.yaml`.
   - Effect: a quota wall ends the turn with a rate-limit `failure_reason`.
     `_single_query_exit_code` maps that to exit 75, and the dispatcher
     requeues the card with a cooldown and no failure counted. That machinery
     already exists; this task only stops the fallback from hiding the wall.
   - Limit to state in the docstring: under `wait`, a fallback triggered by a
     non-quota reason (server error, empty responses) also ends the turn.
     That run counts as an ordinary failure. It is the price of never
     running on the wrong model.
4. **Completion carries it.** In `hermes_cli/kanban_db.py::complete_task`
   (line 2739), before `_completed_event_payload`, read the last
   `model_fallback` event of the closing run (`task_events WHERE task_id=?
   AND run_id=? AND kind='model_fallback' ORDER BY id DESC LIMIT 1`). Pass it
   into `_completed_event_payload` (line 2937) as the `fallback` key, holding
   `{"to_model", "to_provider", "from_model"}`.
5. **Session text.** In `tui_gateway/session_notifications.py::_kb_completed`
   (line 309), when `payload.get("fallback")` is present, append
   `f" · fallback {from_model} → {to_model}"` to the title part, before the
   handoff line.
6. **Visible in `show`.** The new event kinds appear in `hermes kanban show`
   without changes. Confirm it in a test; do not assume it.

### Tests

- `tests/tools/test_kanban_tools.py`:
  - Happy path: with a worker env (`HERMES_KANBAN_TASK`,
    `HERMES_KANBAN_RUN_ID`, dispatcher-owned context), one call to
    `record_model_fallback_from_env` writes exactly one `model_fallback` event
    with that run id and the five payload fields.
  - Without `HERMES_KANBAN_TASK` it writes nothing and returns False.
  - In a delegated-child context it writes nothing. Copy the setup from the
    existing heartbeat tests, which already cover this context.
- `tests/agent/` (new file `test_kanban_worker_fallback_policy.py`):
  - `worker_fallback: wait` plus an owned task: `try_activate_fallback`
    returns False, `agent.model` is unchanged, and one
    `model_fallback_refused` event is written.
  - `allow`: the fallback switches as it does today, and one
    `model_fallback` event is written.
  - No owned task (interactive session) plus `wait`: the fallback switches.
    The policy binds only board workers.
- `tests/hermes_cli/test_kanban_db.py`: a `completed` event after a
  `model_fallback` in the same run carries `fallback`. A `model_fallback` from
  an earlier run of the same card does not.
- `tests/tui_gateway/test_kanban_notify_poller.py`: the completion text
  contains `· fallback A → B` when the payload has it, and does not otherwise.

---

## Task 2. Contract `local-commit`: do not accept `done` over an uncommitted tree

### Today

- `completion_contract` is `local-only | OWNER/REPO | <PR URL>`, validated
  in `hermes_cli/kanban_pr_acceptance.py::validate_contract` (line 17).
- Acceptance runs in `complete_task` through
  `hermes_cli/kanban_pr_acceptance_store.py::prepare_acceptance` and
  `record_acceptance`. A failure writes `tasks.last_failure_error`, and the
  tool handler (`tools/kanban_tools.py:744`) returns that text to the worker
  as the refusal. The task stays in flight.
- `hermes_cli/worktree_ops.py::_worktree_is_dirty` (line 510) already answers
  "is there anything uncommitted", and fails safe toward True.
- Nothing records the workspace HEAD at run start, so "no commit was made"
  cannot be answered today.

### Change

1. **Accept the value.** `validate_contract` accepts `"local-commit"`. Update
   the error text and the `--completion-contract` help in
   `hermes_cli/kanban_parser.py:206`.
2. **Start HEAD.** In `hermes_cli/kanban_db_dispatch.py`, directly after
   `_kbw.set_workspace_path(conn, claimed.id, str(workspace))` (line 2147),
   add: when `claimed.completion_contract == "local-commit"`, run
   `worktree_ops._git_out(["rev-parse", "HEAD"], str(workspace))` and append a
   `workspace_head` event `{"head": <sha or None>}` with
   `run_id=claimed.current_run_id`. A git failure records `None`; the check
   below then fails closed.
3. **Check.** In `prepare_acceptance`, before the PR branch, add: when
   `contract == "local-commit"`, return `snapshot, receipt`, where the receipt
   comes from a new `collect_commit_acceptance(conn, task_id, run_id)` in
   `kanban_pr_acceptance.py`:
   - Workspace: read `tasks.workspace_path`.
   - `classification = "dirty"` when `_worktree_is_dirty(path)`.
     Recovery text: `"Commit or discard every change in <path> (git status
     --porcelain is not empty), then retry kanban_complete."`
   - `classification = "no_commit"` when the current HEAD equals the recorded
     start HEAD. Recovery: `"No commit since the run started (<sha>). Commit
     your work, then retry kanban_complete; if this card intentionally
     changes nothing, say so with kanban_block."`
   - `classification = "missing"` when there is no workspace path, no
     `workspace_head` event for this run, or a `None` head.
   - `ok = True` only when the tree is clean and HEAD moved.
     `receipt["head_sha"]` is the new HEAD.
4. **Event kind.** `record_acceptance` appends `"pr_acceptance"` today. Take
   the kind from `receipt.get("event_kind", "pr_acceptance")` and set
   `"commit_acceptance"` in the new receipt. PR contracts keep their event
   name.
5. **Where it does not apply.** A card created without a contract, or with
   `local-only`, behaves exactly as today. A human `hermes kanban complete`
   on a `local-commit` card is also checked. `--force` does not bypass it
   either, the same as for PR contracts. Confirm this in
   `complete_task`: `acceptance` is computed before the force check.

### Tests

- `tests/hermes_cli/test_kanban_pr_acceptance.py`:
  - `validate_contract("local-commit")` returns the value.
  - Happy path: a real temporary git repo, `workspace_head` recorded, one new
    commit, clean tree. `complete_task` returns True and a
    `commit_acceptance` event has `ok: true`.
  - Uncommitted file: `complete_task` returns False, the task stays
    `running`, and `last_failure_error` contains `git status --porcelain`.
  - Clean tree but no new commit: refused with `no_commit`.
  - No `workspace_head` event (a card claimed before this change): refused
    with `missing`. Say in the report whether this blocks cards that were
    already in flight during the upgrade.
  - `local-only`: the dirty tree is ignored, as today.
- `tests/tools/test_kanban_tools.py`: the tool path returns the refusal text
  to the worker. The task is not mutated, and a second `kanban_complete`
  after a commit succeeds.

---

## Task 3. The session gets the whole summary

### Today

- The `completed` event payload holds only the first line of the summary,
  capped at 400 characters (`kanban_db.py::_completed_event_payload`, line
  2951).
- `_kb_completed` trims that again to 200 characters
  (`session_notifications.py:310`).
- The full text lives in `task_runs.summary` for the closing run
  (`Run.summary`, `kanban_db.py:796`; `get_run` at 4431).

### Change

1. In the poller loop in `tui_gateway/session_notifications.py` (around lines
   405–440, where `_format_kanban_event_text(sub, task, ev, slug)` is
   called), for `ev.kind == "completed"` with `ev.run_id`: read
   `_kb.get_run(conn, ev.run_id)`. Pass its `summary` to the formatter as a
   new keyword argument `full_summary`.
2. `_format_kanban_event_text(..., full_summary: Optional[str] = None)` →
   `_kb_completed`: when `full_summary` is set, the handoff is the whole text
   capped at `_TUI_SUMMARY_LIMIT = 4000` characters, with a trailing
   `"\n… (truncated; hermes kanban show <id>)"` when cut. Otherwise the
   behaviour is unchanged.
3. Leave the block reason alone. It is already the whole reason capped at 160
   characters, and it is written short by rule.
4. Telegram is unaffected. `gateway/kanban_watchers_notifier.py` has its own
   formatter and is not touched.

### Tests

- `tests/tui_gateway/test_kanban_notify_poller.py`:
  - Happy path: a completion whose run summary has 5 lines. The session text
    contains all 5.
  - A summary over 4000 characters: the text is exactly 4000 characters of
    summary plus the truncation line.
  - A completion with no run id (a legacy row): the text is as today.

---

## Task 4. `hermes kanban replace OLD --with NEW`

### Today

Swapping a card in a chain means archiving OLD and running `link NEW child`
and `unlink OLD child` for each child. It is also easy to forget that the
session subscription lives on OLD. The pieces exist in `hermes_cli/kanban_db.py`:

- `link_tasks` (1642) refuses a running child and cycles, and calls
  `_inherit_notify_subs`.
- `unlink_tasks` (1719) runs `recompute_ready` after itself.
- `child_ids` (1748), `parent_ids` (1744).
- `_inherit_notify_subs(conn, child_id, parents)` (1472) copies the given
  parents' subscriptions onto the child, with the cursor caught up.
- `archive_task` (3892) opens its own txn and ends a running worker after
  commit.

### Change

1. `replace_task(conn, old_id, new_id) -> dict` in `kanban_db.py`, next to
   `unlink_tasks`:
   - Refuse with `ValueError` when OLD equals NEW, either id is unknown, OLD
     is `running` or `review` (message: `block or complete <old> first — a
     live worker is not replaced`), or NEW is `archived`.
   - In one `write_txn`:
     - for every `c in child_ids(conn, old_id)`: check `_would_cycle(conn,
       new_id, c)` first; any cycle aborts the whole txn. Then `_link(conn,
       new_id, c)`, delete the `old_id → c` row, and append `linked` /
       `unlinked` events the way `link_tasks` / `unlink_tasks` do;
     - `_inherit_notify_subs(conn, new_id, (old_id,))`;
     - `_append_event(conn, old_id, "replaced", {"by": new_id})` and
       `_append_event(conn, new_id, "replaces", {"old": old_id})`.
   - After commit: `recompute_ready(conn)`, then `archive_task(conn, old_id)`.
   - Return `{"old": old_id, "new": new_id, "moved_children": [...],
     "subscriptions": n}`.
   - OLD's parents are **not** copied to NEW: NEW was created with its own
     parents. State that in the docstring and the CLI help.
2. CLI: add `replace` to the command list in `hermes_cli/kanban_parser.py`
   (next to `link` at line 274), with a positional `old_id`, a required
   `--with NEW`, and `--json`. The handler is `_cmd_replace` in
   `hermes_cli/kanban.py`, next to `_cmd_link` (706), and is registered in the
   dispatch dict (around line 1335). Add `"replace"` to
   `_DELEGATED_CHILD_DENIED_ACTIONS` (`kanban.py:~205`).
3. No model tool. Replacement is an orchestrator action.

### Tests

- `tests/hermes_cli/test_kanban_db.py` (or a new
  `test_kanban_replace.py`):
  - Happy path: chain `A → OLD → C` plus a separate `NEW` whose parent is `A`.
    After `replace`: `C` has parent `NEW` and not `OLD`, `OLD` is `archived`,
    `NEW` carries OLD's `tui` subscription, and `C` stays `todo` until `NEW`
    is done.
  - OLD `running`: refused, nothing changed.
  - NEW is a descendant of C: refused as a cycle, nothing changed (no edge
    was moved).
  - OLD without children: only the subscription and the archive happen.
- `tests/hermes_cli/test_kanban_cli.py`: `replace OLD --with NEW --json`
  prints the dict. A missing `--with` is an argparse error.

---

## Task 5. Methodology (this repo, after Tasks 1–4 are merged into `develop`)

- `scripts/kanban-card.sh`: for the `impl` and `fix` roles, add
  `--completion-contract local-commit` to `ARGS`. `research`, `review` and
  `gate` do not get it: `templates/roles/research.md:4` forbids research
  cards to commit, and a reviewer changes nothing.
- `templates/config.env`: `KANBAN_WORKER_FALLBACK` is **not** added. The
  policy lives in each worker profile's `config.yaml`
  (`kanban.worker_fallback: wait`). `kanban-profiles.sh` writes it when it
  creates the impl and fix profiles, next to its `config set model.*` calls
  (lines 106–107), and leaves review on `allow`.
- `SKILL.md`:
  - Section 10, "Hermes build": list the three new capabilities.
  - Board mechanics: add `replace` as the way to swap a card, instead of the
    link/unlink recipe.
- `tests/run.sh`: the fake `hermes` accepts `--completion-contract` and the
  `replace` subcommand. Add one assertion that an impl card is created with
  `local-commit`.

---

## Acceptance (operator, on the working install after the merge into `develop`)

1. **Fallback, `allow` path.** On `pwaimpl` set the primary to a model that
   answers 429 (a closed teamclaude account, or a fake `base_url`), keep
   `worker_fallback: allow`, and run one small card. The session gets
   `✔ … · fallback <primary> → <fallback>`.
2. **Fallback, `wait` path.** The same with `worker_fallback: wait`. The card
   goes back to `ready` as `rate_limited`, runs on nothing else, and `show`
   has `model_fallback_refused`.
3. **Commit gate.** An impl card whose worker is told in the body not to
   commit: `kanban_complete` is refused with the `git status --porcelain`
   text, and the card stays `running`.
4. **Full summary.** A completion with a multi-line summary arrives in the
   session in full, without `show`.
5. **Replace.** Swap one card of a live chain with `replace`. The next card
   waits for NEW, and NEW's completion arrives in the session.

## Blockers

None for Tasks 1–4. Task 5 waits for 1–4 to be merged into `develop` and
the working install to be updated
(`git fetch ~/dev/hermes/hermes-agent develop && git merge --ff-only FETCH_HEAD`
in `~/.hermes/hermes-agent`, then restart the gateway and desktop).

## Report back

Besides RED/GREEN and the mutations: what in this plan was wrong, ambiguous
or decided by you. Do not be polite about it. That part matters more than
the code.
