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
- Summary: first line `START_HEAD..END_HEAD, N files, gate: green`, then a
  table of findings: applied / rejected (why) / needs decision, and the test
  output.

## Task

Apply or reject the findings of the review card chained before you.
