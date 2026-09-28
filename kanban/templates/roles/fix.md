## Standing constraints for the fix card (read first)

- Workspace: the same tree as the implementation (`{{WORKDIR}}`). Local
  commits allowed; push, PR, merge, rebase, reset, amend, revert, force —
  forbidden.
- Read the review card's **raw** findings (`hermes kanban --board {{BOARD}}
  show <parent-id>`, the `F1…` list) and, on its parent, the implementation
  summary.
- Each finding is either applied (with a test when it is about behaviour, and
  a mutation check for that test) or rejected with proof — command output or
  the line of code that refutes it. A finding that argues with a written
  decision (decision records, glossary) is not applied; return it as
  "needs decision".
- When a finding describes a symptom, fix every path that produces it, not
  only the one cited.
- Two failed attempts on one finding: stop on it, record it, move on.
- `{{GATE}}` green at the end (output ends with `{{GATE_OK}}`).
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
- Nothing to change (the review had no findings, or you rejected all of
  them): leave the tree clean and finish with `kanban_complete` carrying
  `metadata: {"no_change": "<why, one line>"}`. Never `kanban_block` for it —
  a block wakes the operator for a card that is done.
- Summary: first line `START_HEAD..END_HEAD, N files, gate: green`, then a
  table of findings: applied / rejected (why) / needs decision, and the test
  output.

## Task

Apply or reject the findings of the review card chained before you.
