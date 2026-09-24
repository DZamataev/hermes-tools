# Queueing an upstream-merge card on a running board

A board that runs for hours drifts from its base branch, and capability the
remaining cards want may land upstream meanwhile. Merging is a card like any
other — but one whose inputs you must measure yourself, because the worker reads
it cold and cannot tell which side of a conflict carries the epic's work.

## Position it after the code slices, not before

The conflict-prone files are exactly the ones in-flight cards are editing:
manifests, lockfiles, dependency declarations, generated catalogs, native project
files. Merging while a card holds them either fails or overwrites work that has
not been committed yet. Chain the merge behind the last implementation/remediation
card and re-link convergence onto the merge, so the final gate validates the
merged tree rather than certifying a pre-merge state.

## Measure the overlap before writing the body

Derive the numbers in the orchestrator session; do not ask the worker to discover
its own scope.

```bash
git fetch origin <base> <other-branch>
git rev-list --left-right --count origin/<base>...HEAD      # behind / ahead
git diff --name-only origin/<base>...HEAD | wc -l           # total divergence
```

Total divergence overstates the risk. What matters is the set changed on **both**
sides, which is the only place a human decision is needed:

```bash
BASE=$(git merge-base HEAD origin/<base>)
comm -12 \
  <(git diff --name-only "$BASE" HEAD | sort) \
  <(git diff --name-only "$BASE" origin/<base> | sort)
```

Enumerate that list verbatim in the card body, and for each file the epic owns,
state **what must survive** rather than which side wins. A worker told only
"resolve conflicts" will pick a side mechanically and quietly drop a feature flag,
a retry path, or a test-target wiring.

Recurring classes and their resolution intent:

| Class | Instruction |
|---|---|
| feature-flag / config accessor | keep both sides' keys; epic flags stay default-off |
| lockfiles, pod/gem locks | regenerate via the project's install command; never hand-merge |
| generated catalogs and tables | keep both sides' source entries and **regenerate** the artifact |
| agent-policy files (`AGENTS.md` and peers) | union of all sides' rows; dropping a row from either side is a silent regression — and the file is write-protected, so require the exact final rows in the block reason instead of an edit |
| manifest scripts | keep every side's scripts; honour the project's pinning convention |
| submodule pointers | follow the repo's submodule runbook; the root must not reference an unpushed submodule commit |

## Batch branches that share a base into one ordered pass

Two branches cut from the same base produce nearly the same conflict set. Merging
them in separate cards pays for that resolution twice. Put both in one card with
an explicit order — base first, get the tree green, then the feature branch on top
— and say *do not split this into two passes*. Name the expected narrow conflict
surface for the second merge (usually only policy files and the manifest) so the
worker treats a wider one as a signal rather than as routine.

## Demand baseline-versus-introduced classification

A moved base brings its own failing tests, lint findings and warnings. Without an
explicit rule the worker either "fixes" unrelated upstream breakage inside the
merge, or reports pre-existing failures as regressions. Require: check out the
base tip, re-run, and classify each failure as pre-existing upstream versus
introduced by the merge; fix only the latter.

This card is also the right place to pay for a full-suite run, since the whole
dependency graph moved — the convergence card runs it again afterwards on the
final tree.

Post-merge sequence worth spelling out: reinstall dependencies and native
artifacts (the graph moved, so the existing ones are stale), full typecheck and
lint, full test suite, compile every affected native target, then **one** device
case per platform as a smoke check. Not the whole device matrix — that evidence
already exists from the slices; this only has to show the merged tree still runs.

## A merge that brings tooling must prove the tooling works here

Merged-but-broken tooling is worse than absent tooling: later cards will be
written against a capability that does not actually function in this checkout.
Require a concrete probe — the setup command, then the cheapest real operation the
tool performs, then its teardown — and treat a failing probe as a blocker rather
than a note.

Some prerequisites are structurally outside a worker's reach: host security
toggles requiring elevation, per-device developer switches, anything behind a GUI
consent dialog. Name them in the body as operator-only, and instruct the worker to
block with that prerequisite as the requested action — never to work around it and
never to report the tooling verified without it.

## Divergent pins on a first-party fork cannot be resolved in the consumer

The hardest merge blocker is not a conflicted file: it is two dependency pins that
name **divergent branches of the same library**, where neither contains the other.
The shape is easy to misread as a version ordering — our side pins a higher number
and the base pins a lower one — so check ancestry rather than comparing strings:

```bash
git -C <fork clone> fetch --all --tags
git merge-base <ours> <theirs>
git merge-base --is-ancestor <ours> <tag> && echo contains-ours
```

If no published tag is an ancestor-descendant of both, each side carries work the
other lacks, and **no resolution inside the consumer repo is correct**: taking one
pin silently reverts a shipped feature of the other. The fix belongs in the
library — a release containing both lines.

**Converging two lines of a library the team owns is pre-authorized; do not block
for permission to do it.** Cutting a new version cannot break existing consumers,
because each stays pinned to the tag it names — so the convergence is additive by
construction. Published tags are never moved: a convergence, and every follow-up
fix to it, is always a *new* tag. Only the push/publish step remains an operator
gate. Still present the resolution and its cost (which feature each alternative
would revert), and confirm the consumer-side symptom rather than assuming it:
grep each tag's source for the API the base branch now calls.

### Size the library-side merge yourself before relaying the choice

A blocked worker estimates the library merge from file lists and inflates it; that
estimate is what the operator will weigh the options against. Measure it in a
**throwaway worktree of the fork**, never in its main clone:

```bash
git -C <fork clone> worktree add --detach /tmp/<probe> <ours>
cd /tmp/<probe> && git merge --no-commit --no-ff <theirs>
git diff --name-only --diff-filter=U      # the real conflict set
git merge --abort
git -C <fork clone> worktree remove --force /tmp/<probe>
```

A probe run inside the main clone is worthless: uncommitted changes there make
`checkout --detach` refuse, the merge then evaluates whatever tree is actually
checked out, and a clean result is indistinguishable from a successful probe.
Confirm the probe really is at the intended commit before trusting zero conflicts.

Then read the conflicting hunks rather than counting files — doc-comment
divergence in a header reads as a native conflict but costs nothing, and a
five-file merge of mostly comments is a very different recommendation from an
eight-file native one. Correct the worker's figure out loud when it was wrong.

## A clean automatic merge is not a correct merge

Git conflicts only where both sides edited the same lines. It cannot see that one
side deleted the *provider* of a symbol the other side still declares and calls,
so the dangerous breakage arrives in the files that merged **without** a conflict.
Two shapes recur, both of which leave a tree that reads as coherent:

- a method survives in a header and at its call sites while the implementation, or
  the class it delegated to, was removed on the other line;
- a class survives with its sole data feeder deleted — it still compiles, still
  accepts its budget and clear calls, and silently holds nothing. Before restoring
  an implementation on top of a retained class, grep for what *fed* it, not just
  for what referenced it; if the only writer was a deleted bridge, the correct
  resolution is to drop the class, keeping its public entry points as documented
  accepted no-ops so existing callers stay source-compatible.

Invert the conflict count as a signal, too. Conflicts flagged by git overstate the
work — a delete/modify pair where the surviving side never touched the file, or a
rename recorded as add/delete, resolve in seconds — while the unconflicted files
hide the real defects.

**Symbol consistency is not a build.** Checking that every declaration has a
definition misses the dependency graph: a transitive package/pod dependency can
reach the build only through the very component the other line replaced, so the
sources agree while the compile fails on a header that no longer resolves. Compile
the merged library for every target it supports *before* tagging it, and treat
"the symbols line up" as a pre-check rather than as evidence.

## Keep the version field and the tag in step

A follow-up tag that fixes the convergence must also bump the manifest `version`.
Left behind, an installed consumer reports the version of the tag whose build it
replaced, and diagnosing it means diffing files instead of reading a number. Match
the existing tag style as well — mixing a lightweight tag into a line of annotated
ones loses the author and message the others carry.


When the merge supplies something the board previously recorded as impossible —
a second product target, a device-automation path — the acceptance rows that were
parked for its absence become verifiable again. Say so in the merge card and in
the convergence card, and forbid reuse of the old justification wording: left
alone, the final card copies forward "not available in this checkout" for a thing
that is now sitting in the tree.
