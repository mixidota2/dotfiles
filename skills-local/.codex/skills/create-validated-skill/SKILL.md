---
name: create-validated-skill
description: Create, revise, or evaluate reusable skills for Codex and Claude Code from user intent, specifications, domain knowledge, and/or execution evidence. Use for new skills, meaningful workflow or trigger changes, and adoption decisions. Excludes wording-only edits with unchanged behavior.
---

# Create and evaluate skills

A skill should preserve the user's intent and help its consumer do the work.
Writing plausible instructions is not evidence that using them helps. Creation,
behavioral validation, and adoption are separate outcomes.

## 1. Capture intent and choose the mechanism

Read the request, existing skills, relevant specifications, and available examples.
Briefly state the purpose, applicable and inapplicable situations, required and
forbidden behavior, expected output/evidence, and choices free to optimize. A short
paragraph in the working notes is enough; do not invent a required contract file.
Distinguish capability uplift from encoding a user's workflow or preferences; a
skill may serve both. Required procedures are part of correctness, even if slower.

Accept intent/specifications, successful and failed experience, or both as inputs.
With no execution history, create a candidate from the explicit requirements and
reliable domain knowledge, label untested assumptions, and derive cases to test
those assumptions. Do not demand past failures or refuse intent-driven creation.
If sources conflict, preserve explicit user requirements and expose unresolved
conflicts instead of silently changing the goal.

Prefer a script/test for deterministic enforcement and documentation for reference.
Reuse an existing skill's scope where appropriate. This mechanism choice must not
remove requested guidance or required steps merely because they cost time.

## 2. Define checks from the original requirements

Before drafting, keep the original intent and previous accepted version available.
Derive a small set of must-pass checks from that intent, independently of the skill
text, so dropping a requirement from the candidate cannot erase its check. Separate
fidelity (including process and prohibitions) from output quality and efficiency.
Start with a representative task, a requirement-sensitive boundary, and a changed
input; include non-applicable requests to catch unnecessary procedures. Use actual
fixtures and external checks for files/code and independent review for semantics.

Define an edit budget and acceptable costs for optional choices. Use development
cases to improve the candidate, validation cases to select it, and separate unseen
cases for the final consumer trial. See
[evaluation-method.md](references/evaluation-method.md) for evidence and decisions.

## 3. Write a grounded candidate

For each non-obvious step, explain why it is needed, when it applies, the concrete
action, what to avoid, and how completion is observed. Ground it in the request,
a specification/domain source, or observed behavior; mark a proposed remedy as a
hypothesis until tested. When logs exist, examine both successful actions and the
first consequential failure rather than copying the whole trajectory.

Keep required knowledge; remove unsupported universal rules and redundant rituals.
Use conditions for exceptions instead of making a task's paths, names, schemas,
tools, or extra output files universal. Put conditional detail in references and
repeated deterministic operations in scripts. Preserve supported agent metadata.
Use [init_skill.py](scripts/init_skill.py) with `--path ./skill-candidates` for a
new package; `--evals` is optional. Keep revisions in a separate, undiscovered copy
or checkout, preserving the installed accepted version. The initializer marks new
packages with `.candidate`; keep that marker until adoption. Test changed scripts.

## 4. Validate the author and the consumer separately

Run [validate_skill.py](scripts/validate_skill.py). Structural success is not
behavioral evidence. Freeze the generated runtime package, then give it to a fresh
consumer with an unseen task and the same capabilities/limits as the comparison
arms. The author must not solve the measured task for the consumer. Test the actual
result against the original requirements, not the author's self-report. When
changing a skill creator, compare what each creator generates *and* what separate
consumers accomplish using those generated skills.

Read the [executor contract](references/executor-contract.md) and
[evaluation schema](references/evaluation-schema.md) before using
[run_eval.py](scripts/run_eval.py). An independently arranged fresh agent task is
also valid; nested CLI execution is not required. Compare no-target-skill/candidate
for new skills and include the previous accepted version for revisions. Test native
routing separately: explicit loading is not discovery evidence. If execution is
unavailable, finish the candidate and report the behavior trial as blocked/unrun.

## 5. Decide without changing the goal

Evaluate must-pass fidelity first; a faster requirement violation is not a win.
Then compare quality, regressions, and cost among compliant alternatives. An encoded
preference can justify a skill on demonstrated workflow fidelity even with tied
capability scores. Simplify optional overhead on ties, preserving required behavior.
Do not claim statistical improvement from mere acceptance-criteria compliance.

Open final cases only after selecting and freezing the candidate. The runner checks
an earlier input-hash record; this detects edits, not whether a case was unseen.
A final case used to guide a fix becomes regression data and needs a replacement.
Report case-level wins/losses/ties, requirement failures, costs, and uncertainty.
Retain/promote only with evidence for the stated purpose and no critical regression;
otherwise reject, keep inconclusive, or report an infrastructure blocker. Keep
candidates out of discovery/install paths until that decision. Preserve histories
and existing accepted installations. Report structure, adapter tests, native routing,
and real consumer validation separately for Codex and Claude; unrun is not passed.
