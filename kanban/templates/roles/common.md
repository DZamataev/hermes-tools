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
  coordinator.
- TDD: a failing test first at the lowest layer that can express the
  behaviour; quote the RED output. For every new guarantee, a manual mutation
  check: break the real source line, run, quote the failure, restore, run
  green. No helper scripts for mutations.
- Constants that define what counts as a difference (tolerances, thresholds,
  quanta) come from the repo or the task — never invent a second one.
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
