# Audit cards that compare against an external product

A request to "check our pipeline matches what Instagram/Telegram/<product> does"
is an invitation to fabricate. The model has absorbed plausible-sounding numbers
for every well-known product, and a worker asked to compare will emit them with
full confidence. A confidently wrong external baseline is worse than an
acknowledged gap, because it becomes the justification for changing working code.

Write the card so that fabrication is structurally inadmissible.

## The admissibility rule, stated in the card body

No claim about an external product is usable unless it is one of:

1. **Measured** from an artifact on this machine — cite the absolute path and the
   exact probe output that produced the number;
2. **Read** from that product's published source — cite repository, file and line.

Anything else is written as `unknown — not verifiable here`. Say explicitly that
an acknowledged gap is the preferred outcome and that memory, training data,
blog posts and inference are all inadmissible. This single constraint is the
reason the card exists; put it above the audit checklist, not in a footer.

## A delivered artifact evidences delivery, not the producer's encoder

A file downloaded from a service has been re-encoded by that service for
delivery. It is solid evidence of the **delivery target** — geometry, codec,
colour tags, pixel range, audio profile the viewer actually receives — and no
evidence at all of the settings the service used internally or of what the
original uploader supplied. Spell out that distinction in the card, or the audit
will quote a delivery bitrate as the competitor's encoder configuration.

Gather several samples through more than one extraction route before treating a
property as characteristic; one file cannot distinguish a service-wide policy
from one clip's content-adaptive outcome.

## Reading a competitor's source is allowed; copying is not

Many clients are published under a copyleft licence (GPL family) that forbids
incorporation into proprietary software. Studying an implementation and citing
what it does is not derivation — ideas are not protected, expression is. So the
card may require *reading and citing* published source, and must forbid copying
any of it into the repository.

Check the licence before recommending adoption of anything from such a codebase,
and state the conclusion plainly rather than leaving it implied. Note also that
deeply app-coupled implementations are rarely extractable even when licensing
allows it, and that a competitor's older approach may sit at a *lower* level than
a current first-party framework already provides — compare levels before treating
their choice as the target.

## Audit our own claims too: spec versus implementation

The other half of the audit is internal. Require verification that the
implementation does what its own spec asserts, and treat a spec that describes
absent behaviour as a defect in its own right rather than a documentation nit.

The highest-value target is any place where **metadata and content can disagree**:
a correctly tagged output whose pixels were never converted passes every tag-based
check and every playback smoke test, and only measurement of the content itself
exposes it. Name that failure mode in the card and require measured content, not
declared properties, as the evidence — on both the audit card and its remediation
card.

## Fence the audit off from settled decisions

List the operator decisions already recorded (and where) as closed, together with
the known-and-accepted gaps. Without that fence an audit re-opens a quality or
policy choice the operator already made, and the remediation card then "fixes"
it. Permit reporting *new evidence* that a decision was wrong; forbid treating it
as an open question.

## Split audit from remediation

The audit card is findings-only: no edits, no commits, no fixing-while-there.
Remediation is a separate card that also inherits the admissibility rule — a
finding whose external comparison carries no citation is not actionable, and the
fix card must say so rather than changing the pipeline to match an unverified
number.
