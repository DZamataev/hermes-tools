# Sweeping tuning constants to unblock a decision card

Use when a worker blocks asking the operator to choose numeric constants
(durations, costs, health, income) and the repository already has a
deterministic gate that scores them — a seeded simulation, a benchmark harness,
a property run.

## Before measuring: can each proposed lever move the metric at all?

Read the gate's own definition of what it reports, then classify every knob:

- **Cannot move it.** A metric counted in discrete phases is invariant to the
  length of a phase; a metric over a ratio is invariant to a term that scales
  both sides. These belong to a different question (usually feel on the device)
  and must be excluded from the sweep and said so, or the grid wastes runs.
- **Moves it the wrong way.** Anything that strengthens a restored-every-cycle
  baseline lengthens a run that is already too long.
- **Moves it, not on the worker's list.** The objective's own durability, the
  damage that reaches it, the cap on producers. A worker proposes knobs its card
  touched; the effective lever often sits in a file the card never opened.

State this classification to the operator with the sweep results — it is the
reason the chosen number is the right kind of number, not just the one that
scored best.

## The loop

One script per case, driven from a pristine copy of the generator source:

1. `cp` the untouched generator aside once, at the start; every case restores
   from that copy before editing. Editing the already-edited file compounds
   substitutions and the third case silently measures the second.
2. Substitute the case's values, regenerate the data artifacts, run the gate,
   grep the summary lines into a per-case log.
3. Restore the pristine copy at the end of the sweep, and leave the measurement
   worktree clean — a stray edit there becomes a phantom diff next session.

Vary **one** parameter per axis. Two changed knobs in one case cannot be
attributed, and the operator will ask which one did it.

## Pitfalls

- **Assert the substitution matched, not that the text changed.** The baseline
  case sets a parameter to the value it already has, so a `new != old` guard
  fails on the one case that must succeed. Use a counting substitution
  (`re.subn`, assert exactly one match) — it catches both the typo'd pattern and
  a pattern that matches in two places, which a diff check never does.
- **Do not edit the generated artifacts.** Repos that generate resource files
  from a script regenerate them on the next run; patch the generator and run its
  regenerate command, or the sweep measures whatever was last committed.
- **Pilot on 2–3 seeds to prove the harness runs, then throw those numbers
  away.** They establish the per-run cost and that the gate reaches its summary;
  they establish nothing about the outcome.
- **Read every threshold in the pilot output.** A gate that fails the threshold
  you came for often fails others, and those may be outside what any sweep of
  these knobs can fix — that is a separate finding for the operator, not
  something to fold into the same change.
- **Drive one case per invocation, not the whole grid from one driver.** Size
  each case to finish inside a single call and write its own log; whatever kills
  the driver mid-grid then costs one case instead of every case already
  computed, and any single case can be re-run on its own afterwards.

## Choosing among the passing candidates

- **Confirm the leader on a second seed series before reporting it.** A grid of
  20 seeds can hand the same configuration a clean pass on one series and a
  failed completion threshold on another — the series, not the parameter, moved.
  Re-run the leading candidate at a different starting seed; if the verdict
  flips, the sample is too small for the decision, so widen every case (40+
  seeds) and re-rank. Say which seed series the final numbers came from: they
  are not a property of the parameter alone.
- **Prefer the candidate that lands mid-band over one that lands on the band
  edge.** Several configurations can satisfy an acceptance range while sitting
  at its boundary, where one step of noise drops them out; the one in the middle
  survives the next change to unrelated content. Give the operator this as the
  reason for the recommendation, alongside the table.
- **Do not rank on a secondary distribution the grid cannot resolve.** Win-share
  or category-balance ratios move by tens of percent between series at these
  sample sizes, so a candidate that looks fairer is usually noise; report the
  ratios, state that they are not decisive, and rank on the primary metric and
  the completion rate.
