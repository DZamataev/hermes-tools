## Standing constraints (read first)

- Workspace: `{{WORKDIR}}` (the card's workspace field). Only that tree. Its
  branch already exists; local commits are **allowed**. push, PR, merge,
  rebase, reset, amend, revert, force, deleting branches — **forbidden**.
- Read `{{RULES}}` at the root of the tree and the ticket named in the task
  before any edit. What they say is not optional. Agent-policy files
  (`AGENTS.md`, `CLAUDE.md`) are write-protected: if one needs an edit, put the
  exact text in your summary and do not attempt the write.
- Gate: `{{GATE}}` exits 0 and its output ends with `{{GATE_OK}}`. Quote the
  final lines. Run the extra suites the task names; leave the others to the
  orchestrator.
- TDD: a failing test first at the lowest layer that can express the
  behaviour; quote the RED output. For every new guarantee, a manual mutation
  check: break the real source line, run, quote the failure, restore, run
  green. No helper scripts for mutations.
- Evidence as you go: append every RED run and every mutation's failing and
  green output to `$TMPDIR/$HERMES_KANBAN_TASK-evidence.log` the moment you get
  it, and build the summary from that file. Your context may be compacted
  before the end; output you did not save is gone. Not in the tree: a stray
  file there fails the clean-tree check at `kanban_complete`.
- Long suites (e2e and the like) run in the background with a completion
  notice and you wait for it: a foreground call hits the tool timeout. While
  one runs, do not touch the tree — no mutations, no edits: a dev server
  reloads on the change and the running tests fail for that reason, not for
  the code. Mutations come after the suite has finished.
- Constants that define what counts as a difference (tolerances, thresholds,
  quanta) come from the repo or the task — never invent a second one.
- A question only the orchestrator can settle (which of two readings of the
  ticket, a naming or UX choice): ask on your own card and wait —
  `kanban_comment(task_id=$HERMES_KANBAN_TASK, body="<question + the default
  you would pick>", await_reply_minutes=10)`. The reply comes back in `replies`;
  empty means no answer: proceed on the default and note it in the summary, or
  block if you cannot. Do not ask what the repo or the ticket already answers.
- An objective obstacle (missing access, an ambiguous acceptance check, a
  contradiction with a decision record): `kanban_block` with a reason that
  **starts with the action required and the path**. Do not guess a workaround.
- Finish only with `kanban_complete`. The summary is **neutral**: first line
  exactly `START_HEAD..END_HEAD, N files, gate: green` (no words about
  purpose), then the changed files, the commands run with their output, the
  mutation proofs, blockers. Not a word about why the change was made.

<!-- Repo-specific constraints go below: layers, specs that must move with the
code, tools that must not be used, the environment a worker lacks. -->

## Task
