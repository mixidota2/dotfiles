# Skill validation cost and scoped follow-up

This is a protocol revision after `3bcd5ac0d9afec525e07464c0b664167e2cb51b5`.
Author/consumer evidence collected with authoring commit
`6d496ef8130979fd84812d72b7562277b96af6ab` remains evidence for that version only.
The intervening `3bcd5ac` change fixes deterministic input hashing. Neither earlier
experiment establishes the behavior or speed of the revised authoring instructions.

## What was measured

One local measurement per check on Linux, Python 3.12.14, in an already running
workspace at `3bcd5ac`; these are observations, not limits or latency guarantees.
CLI measurements include Python startup. No real model or agent CLI was invoked.

| Check | Elapsed seconds |
| --- | ---: |
| Creator package structure | 0.053 |
| Runner self-test | 0.052 |
| Creator input freeze | 0.077 |
| Runtime copy and hash, in process (21 files, 94,672 bytes) | 0.003 |
| Full 120-test repository suite | 5.402 |
| Nine-trial harness with transport replaced by a mock | 0.103 |

The full repository suite is an implementation regression check, not a required
per-skill authoring step. The mock timing excludes agent startup, inference, and
tool execution. The one-time research, creation-method comparison, environment
startup, artifact handoff failures, and retries must not be reported as a fixed
cost of routine skill validation. End-to-end model latency remains unmeasured here.

## Smallest protocol changes

The old runner added no-skill whenever `--previous-skill` was supplied. A revision
with three selected cases and one trial therefore started nine agents in sequence.
It now compares candidate/previous (six agents in that example). New-skill comparisons
use `--baseline` for candidate/no-skill; both flags explicitly retain three-arm
research. This reduces requested invocations by one third in that example; it is
not a measured end-to-end speedup, and each agent may make multiple API calls.

Cheap structure and relevant script checks remain required. Behavior changes start
with scoped smoke; new capabilities, major revisions, high-risk operations, changed
models/tools, or inconsistent results warrant broader checks. Meaning-preserving
edits may omit behavior runs with a recorded reason; changing an instruction's
meaning is behavioral even if the patch only changes words. Required intent and
prohibitions remain must-pass criteria. Frozen final evidence must remain independent
of edits, but a single untouched smoke case can serve a scoped final check.

The rationale and primary sources are in the
[evaluation method](../skills-local/.codex/skills/create-validated-skill/references/evaluation-method.md).
They support focused checks and purpose-dependent comparisons, not universal trial
counts or time ceilings. The runner still executes sequentially and preserves each
case's artifacts; no scheduler, cache, automatic resume, or retry loop was added.
Independent cloud trials may be parallelized by their coordinator. A known startup
blocker should be resolved before requesting further runs; timeouts or interrupted
work are not passes. Existing timeouts and authentication/permission behavior remain.

## Planned real Codex smoke (not yet executed)

Use fresh formal cloud Codex tasks with the new commit pinned, existing authorized
capabilities, isolated working copies, and the revised creator explicitly loaded.
Do not run a nested CLI. Use new fixtures drawn from the protocol requirements;
do not reuse or optimize against the published creation-method comparison cases.

1. **Meaning-preserving edit:** ask Codex to fix a spelling error in an existing
   instruction-only skill while preserving intent, triggers, required/prohibited
   actions, and output. Inspect the actual diff and check results: structure should
   be validated, behavior-run omission explained, and no full comparison launched
   or behavioral success claimed. This checks the cheap path, not model performance.
2. **Meaning-changing edit:** ask Codex to revise a small CSV-summary skill from
   allowing unit omission to requiring supplied units and explicitly reporting
   missing units. This is a text edit with behavioral consequences. Verify that it
   selects checks for that change and the existing prohibition on inventing data,
   chooses candidate/previous rather than automatically adding no-skill, and records
   scope and omissions. Freeze the generated package. The coordinator then gives
   the same new, previously hidden CSV to fresh candidate and previous consumers,
   in parallel if supported, and checks their actual output against the original
   unit and no-invention requirements. The author does not receive the expected
   answers. A tie is valid; no performance-gain claim is required.

Record source commit, selected cases/arms, actual invocations, outcomes and missing
observations, agent duration/usage where available, and separate end-to-end duration
including startup/handoff. Must-pass failure prevents adoption; incomplete checks
remain unrun/inconclusive or infrastructure-blocked. If feedback informs an edit,
retain that case as regression data and obtain a fresh final case. Expand only for
an observed mismatch or risk, not to rerun the previous full research comparison.
