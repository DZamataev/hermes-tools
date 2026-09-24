# Card body skeleton

Copy, fill the bracketed parts, delete what does not apply. The shared preamble
is identical across every card in a run — build it once as a string and
concatenate the per-card specifics, so no card silently drifts from the rules.

## Shared preamble (every card)

```
Work only in the assigned workspace. Read [repo agent-instructions file] and the
relevant indexed runbooks/specs before editing. The approved plan is [absolute
path] and the decision record is [absolute path]. Prefix every repository command
with [required env, e.g. locale vars].

[Toolchain env the interactive shell provides but a worker does not, e.g. SDK
roots.] Known-benign baseline warnings: [list]. Do not fix these as part of this
slice.

Local commits are authorized; push, PR, merge, rebase, reset, amend, revert,
force operations, and branch deletion are forbidden.

[Product/compatibility invariant: which behavior is the default that must not
change, and which other target must be classified and verified.]

For code changes add test coverage, run it, prove it by mutating the real source
and observing the targeted assertion fail, restore the source, then run
changed-path typecheck and lint. Do NOT run [the expensive full suite] — the
convergence card owns it.

Native commands may use only [explicit device serials/UDIDs]. Never open an
interactive selector.

[Gates that survive general authorization, e.g. copy/translation mutations that
repo policy still requires approval for.]

Record objective blockers instead of guessing.

Before editing record START_HEAD. Begin the completion summary's FIRST LINE with
outcome then blockers — notifications deliver only that line, truncated near 200
characters. Keep the summary neutral for a downstream adversarial reviewer:
START_HEAD, END_HEAD, changed files, verification commands and results, mutation
evidence, blockers. Do not explain intended behavior.
```

## Implementation card

```
[Shared preamble]

[One paragraph: the work item, the constraint that makes it non-obvious, and the
specific contract to honor. Name the docs to update when behavior requires it.]
```

## Adversarial review card

No preamble — the reviewer must stay ignorant of plan and intent.

```
Act as a fresh adversarial reviewer. Do not read the issue, approved plan,
implementation task body, or author identity. Use only the implementation
handoff's START_HEAD/END_HEAD and inspect that git diff plus directly relevant
surrounding source, tests, and specs. Reconstruct intent from the diff and try to
refute correctness. Do not modify files or commit.

Return raw indexed findings F1... with path:line, consequence, and a concrete
fix; state explicitly when there are zero actionable findings. Check behavior
preservation, cross-target parity where applicable, test quality, and whether the
mutation evidence actually targets each new behavior.
```

## Remediation card

```
[Shared preamble]

Read the parent implementation handoff and the raw review findings. Read the
issue, plan, and relevant specs. Apply every actionable finding; if a finding is
invalid, document concrete evidence rather than accommodating it. Stop after two
failed attempts on the same finding and report it as a blocker.

Run targeted tests, typecheck, lint, and mutation proof for changed behavior,
then make a local milestone commit. Summary lists findings resolved and rejected
with evidence, changed files, the commit, verification, residual risk.
```

## Operator gate card

Create with `--initial-status blocked`, parented to the card producing its inputs.

```
Needs operator judgment: [the decision only a human can make, and why metrics
cannot settle it].

Artifacts: [ABSOLUTE paths]
How to inspect: [copy-pasteable command]
Decision needed: [numbered, answerable questions]

On answer, record the resolution in [absolute path] and complete this card, which
releases [dependent card id].
```
