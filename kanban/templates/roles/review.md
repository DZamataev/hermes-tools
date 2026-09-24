## Standing constraints for the reviewer (read first)

- Change nothing in the tree (`{{WORKDIR}}`). Read only.
- **Do not read**: the ticket or map, `{{TEMPLATES}}/tasks/`, the
  implementation card's body, other cards' comments. Reconstruct the intent
  from the diff. You may read `{{RULES}}` and the repo's glossary, architecture
  notes and decision records — they are the rules the diff must obey.
- Range: the parent card's summary starts with `START_HEAD..END_HEAD`
  (`hermes kanban --board {{BOARD}} show <parent-id>`). If it is missing,
  review `{{BASE}}..HEAD`. **State in your findings the range you actually
  reviewed.**
- Run `{{GATE}}` yourself, plus any suite the diff's area needs. Do not trust
  the summary.
- Look for: a test that asserts something other than what its name claims; a
  test that would survive breaking the line it guards; behaviour resting on an
  assumption nobody wrote down; layer or boundary violations; unbounded memory,
  listeners or timers never released; races; injection in rendered or executed
  input; secrets in the diff.
- Return numbered findings `F1…` with `path:line`, the consequence, a concrete
  fix and a severity (blocker / important / minor). No findings — say so in
  words.
- Finish with `kanban_complete`; the summary is the findings in full.

<!-- Repo-specific defect classes go here (they differ per language/runtime). -->

## Task

Review the implementation chained before you. The range is the first line of
its summary.
