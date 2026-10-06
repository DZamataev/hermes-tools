---
name: dz-wrapup
description: "Use when the operator ends a work session (wrap up, done for today, close out) or asks what past wrap-ups found."
version: 1.0.0
author: Denis Zamataev
license: MIT
platforms: [macos, linux]
metadata:
  hermes:
    tags: [session, wrap-up, retrospective, worktree, handoff]
    related_skills: [dz-clean-worktrees]
---

# Wrap up a session

Closes the session so that nothing it produced is lost, nothing it started
keeps running unnoticed, and the next session starts smarter. Three outputs:
the **state** of everything the session touched, a **summary** of what was
done, and **process improvements**. The worktree, if the session made one, is
offered for removal last.

Every run ends in a **report file** — the place to read what a wrap-up found,
including one nobody watched.

## Modes

| Invocation | Does |
|---|---|
| `dz-wrapup` | steps 1–6: one confirmation, then the chosen actions |
| `dz-wrapup auto` | steps 1–3 and 6 with no question; **executes nothing** — every action and every improvement lands in the report as an open item |
| `dz-wrapup review` | no new wrap-up: walks the open items of past reports (see "Review") |

Auto changes nothing in repositories, skills or memory, and removes no
worktree. It only writes the report, so it is safe to run unattended — the
operator decides later in `review`.

## Report

Directory: `${HERMES_HOME:-~/.hermes}/wrapups/` (create it). One file per
run, `YYYY-MM-DD-HHMM-<project>-<slug>.md`, plus `INBOX.md` listing reports
that still have open items.

```markdown
# Wrap-up — <project>, <date time> (<mode>)
Session: <session id or title>  ·  Repos: <paths>  ·  Worktree: <path or none>

## Summary
(step 2)

## Improvements
- [ ] I1 <title> → <destination: skill name / memory / AGENTS.md path / new script>
  Evidence: <what happened in the session>
  Change: <the exact text or diff to apply, ready to paste>

## Actions
- [ ] A1 <action>  (why; command to run)
- [x] A2 <action>  → <result: sha / pushed / freed 1.2G>
- [-] A3 <action>  declined

## State left behind
<per repo: branch, uncommitted, unpushed; worktree: kept because …>
```

`[ ]` open, `[x]` done, `[-]` declined. Improvements carry the ready text so
`review` can apply them without reconstructing the session. After writing,
add or update the report's line in `INBOX.md`:
`- [<n> open] <date> <project> — <goal>  → <file name>`; remove the line when
nothing in the report is open. Print the report path in the final message.

The operator reads `INBOX.md` and opens a report — or runs `dz-wrapup review`.

## 1. Survey (read-only)

Build the list from the conversation, not from guesses: every repository and
worktree the session wrote to, every branch it created, every process it
started. For each repository:

```bash
git -C <repo> status --short --branch          # uncommitted, ahead/behind
git -C <repo> log --oneline @{upstream}..HEAD  # committed, not pushed
git -C <repo> stash list                       # stashes this session made
```

For a worktree the session created (`git worktree add`, `claude -w`, an
agent's `.claude/worktrees/…`), get its full status in one call:
`python3 ${HERMES_SKILL_DIR}/../dz-clean-worktrees/scripts/wt_scan.py --only <path> --json`
— merged or not, pushed or not, dirty, in use, size. Without the
`dz-clean-worktrees` skill installed, fall back to the git commands above plus
`git branch -r --contains HEAD`.

Also note: background processes and dev servers the session started (are they
still running? should they be?), temp files outside the scratch directory,
tracker tickets whose state the work changes, plans/specs that are now done.

Separate what is **verified** (a test run, a curl, a log line seen this
session) from what is only **claimed**. Unverified items go in the summary as
such — never upgrade them.

## 2. Summary

Write it for someone who was not here, in the operator's language:

```
## Session summary — <project>, <date>
**Goal:** one line.
**Done:** bullets; each with its evidence (commit sha, test run, URL).
**Decisions:** choice — why (only the ones not obvious from the code).
**Not done / open:** what remains, what blocks it, the exact next step.
**State:** per repo — branch, uncommitted, unpushed, merged where.
```

Do not repeat what commits and diffs already say; point to them.

## 3. Process improvements

Look back at how the session went, not what it built. Candidates, in this
order of value:

- **Operator corrections** — anything the operator had to say twice or
  correct. Each is a rule a skill or the memory should have held.
- **Wasted turns** — dead ends, a fact found late that a pointer in
  `AGENTS.md`/a skill would have given at once, an expensive call that a
  script would replace.
- **Mistakes a check could catch** — a test, linter or guard that would have
  stopped it.
- **Repeated manual sequences** — three or more times = a script or a skill.
- **Skill defects** — a loaded skill with a wrong command or missing step.

For each: the evidence (what happened), the fix, and where it goes (which
skill, memory, `AGENTS.md`, a new script). Check the destination first and
drop a candidate it already covers. Two to five strong items beat ten weak
ones; "nothing to improve" is a valid result.

## 4. One confirmation (skipped in `auto`)

Write the report first, with every action and improvement open, so it exists
even if the session ends here. Then show the summary, the improvements, and a
numbered action list drawn only from what step 1 found — for example:

1. commit `<files>` in `<repo>` (session's own files only, listed by name)
2. push `<branch>`
3. stop `<process>` started for `<reason>`
4. apply improvement N to `<skill/memory/AGENTS.md>`
5. write a handoff note (only when work is unfinished)
6. remove worktree `<path>` (`<size>`, merged into `<branch>`, pushed)

Ask which to run (numbers, `all`, `none`). Offer only actions that apply.
Offer the worktree item only when `wt_scan` marks it removable **and**
recommends deleting it. A worktree in a managed folder (`placement: managed`,
parent named `*-wt`) is one the operator keeps on purpose: list it as
"keep (managed folder)" with its merge state, not as an action. When it is
not removable, say in one line why it stays (unmerged, dirty, unpushed).

## 5. Execute the chosen actions, in this order (skipped in `auto`)

1. Commit — stage files by name, never `git add -A`; files the session did
   not touch are shown, not swept in.
2. Push — only when chosen here or asked earlier.
3. Stop processes, update tracker/plan state.
4. Apply improvements with the platform's skill/memory tools.
5. Worktree last, after everything else in it is committed and pushed:
   `cd` the shell out of it (the agent's own shell counts as "in use"), then
   `python3 ${HERMES_SKILL_DIR}/../dz-clean-worktrees/scripts/wt_remove.py --yes <path>`.
   It re-checks and refuses on any change. Never `rm -rf`.

Mark each item in the report as it finishes: `[x]` with its result, or `[-]`
declined; a failed one stays `[ ]` with the error.

## 6. Final message

Update `INBOX.md`, then one short block: what was executed with its result
(sha, `pushed`, `freed N M`), what is left open, and the report path. In
`auto`: the open-item count and the report path. Nothing after it.

## Review

`dz-wrapup review` (or "what did the wrap-ups find?"): read `INBOX.md`, open
each listed report, and show its open items grouped by report — improvements
with their destination and ready change, actions with their command. Re-check
each before offering it: the destination may already contain the rule, the
branch may already be pushed, the worktree may already be gone — mark those
`[x]` (already done) without asking. Ask which to apply, apply them as in
step 5, update the checkboxes and `INBOX.md`.
