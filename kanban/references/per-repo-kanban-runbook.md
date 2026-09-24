# Writing a per-repo Kanban runbook

A project's runbook must be **self-contained for that repo's readers**: a
coordinator or human opening it has neither this skill nor any sibling repo's
docs in front of them. Copy the generic procedure in (one writer per worktree,
worktree warm-up, role profiles and memory rewrite, card preamble, blind review,
gates, notifications with the truncation table, observation, failure modes,
board bring-up, coordinator duties) and rewrite every example in this repo's
terms. A "delta from `../<other-repo>/docs/...`" file is rejected by the
operator: a path into another checkout is a dependency on a repo the reader may
not have, and the pointer rots the day that file moves. Open the file by saying
it is a per-repo runbook and linking only the public product docs.

**Do not pin model ids or providers in the runbook.** The operator configures
role models externally and changes them with quota and availability; a table
saying "`lsimpl` — `<model>` via `<provider>`" is stale within days and reads as
an instruction to restore it. Record the *rule* instead (reviewer on a different
model family than the author; a weak reviewer produces false confidence; models
bind at next spawn) plus the two check commands (`config get model.default`,
`grep "OpenAI client created" .../agent.log`).

## Port by rewriting, not by reference

Copying a runbook from a repo you already ran a board on is the fastest start and
the easiest way to ship lies. Walk the source file section by section and sort
each paragraph into: generic (keep, it belongs in this file too), structural
(keep, rewrite the example), or source-repo-specific (**replace with this repo's
equivalent or delete**).

The paragraphs that survive a careless port and are wrong in the new repo:

- **The warm-up command.** Every repo has one and none of them match. Derive it
  from what a fresh checkout lacks: installed dependencies, downloaded binary
  artifacts, an engine's generated import cache. A worker on a cold cache spends
  its opening turns on it and reads the failure as a code defect.
- **The test invocation and its success criterion.** Copy the repo's own rule
  verbatim from its agent-rules file, including any criterion beyond the exit
  code — a suite that counts a crashed case as passed needs both the summary line
  and the exit code stated, or workers will report green runs that hid an error.
- **Timeouts.** `--max-runtime` calibrated against another repo's suite is a
  guess here. Size it from the slowest thing a card actually does; a card that
  builds and deploys to a device needs several times what a headless unit card
  needs.
- **The traps list.** Replace wholesale. Mine the repo's own agent-rules file for
  the pitfalls that already cost someone debugging time and restate the ones a
  worker could hit, in the worker's terms.
- **The role templates' defect lists.** The reviewer preamble names the defect
  classes of the source repo's language and runtime (a reused buffer in a game
  sim, dictionary iteration order); a web client's classes are different (UI
  awaiting the network, listeners never removed, XSS in rendered content,
  ordering by storage row id). Rewrite the list, keep the blindness rules.
- **Ports and shared local services.** When the repo's e2e starts its own
  servers on fixed ports, two cards running e2e at once collide; state the
  ports in the runbook as one more reason implement cards are serialised.
- **A missing role.** A source repo may have had no research cards; if this
  repo's map has research tickets, add a `research` template (write one new
  file, cite every claim, `UNVERIFIED:` for what could not be checked, never
  call a real-side-effect endpoint such as sending an SMS) and a matching role
  in the card script.

## Never carry over the source repo's results table

A "results from the first run" section — wall-clock, finding counts, suite sizes —
is the single most tempting paragraph to keep, and it is fabricated evidence the
moment it appears under a different project's name. Replace it with an empty
calibration section naming the numbers to record after this repo's first board.
Say plainly in your report that no run has happened here yet, rather than letting
a formatted table imply one has.

## Language and register follow the repo

Write the runbook in the language and voice the rest of that repo's documentation
uses, not the language of the file you ported from. A runbook that reads as
foreign in its own docs tree gets skipped.

## Name the repo's un-reviewable class explicitly

Every project has behaviour that agents structurally cannot check — states only a
human sees on real hardware, timing and feel, anything the headless harness cannot
reach because its input path does not exist there. Enumerate that class in the
runbook and route it somewhere concrete: an operator gate card, or the repo's
existing manual checklist. Left unnamed, it is silently assigned to the board and
ships broken.

## Finish with the repo's own checks

Run whatever the project uses to validate documentation (link checkers, doc
linters) after writing the file and again after adding the pointer to it. Adding
that pointer usually means editing the repo's write-protected agent-rules file,
which needs the operator's approval — do it from the orchestrator session, never
from a card.
