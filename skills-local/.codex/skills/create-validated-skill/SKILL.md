---
name: create-validated-skill
description: Create, revise, or evaluate reusable skills for Codex and Claude Code using matched task evidence. Use for new skills, meaningful workflow or trigger changes, and decisions to retain, simplify, or reject a skill. Excludes wording-only edits with unchanged behavior.
---

# Create and evaluate skills

A skill is a candidate intervention, not an improvement by definition. Finish the
requested work with evidence and an honest decision; rejecting an unnecessary or
harmful candidate is a useful result.

## 1. Choose the smallest mechanism

Inspect existing skills and the actual recurring problem. Prefer a deterministic
script/test for enforceable requirements, project documentation for reference,
and short directory instructions for broadly applicable conventions. Extend a
skill with the same scope instead of adding another. Keep useful domain knowledge;
do not turn one failure or task-specific workaround into a universal rule.

## 2. Define the experiment before editing

Freeze the previous accepted skill and representative user tasks. Name the real
outcomes, critical constraints, acceptable costs, and a bounded edit budget. Use
actual fixtures and external tests when the outcome is a file or working code.
Reserve separate validation cases for selection and final held-out cases for the
chosen candidate. Consult [evaluation-method.md](references/evaluation-method.md)
for comparison and adoption decisions.

## 3. Write a lean candidate

Keep this entrypoint about decisions that change the agent's work. Put conditional
detail in linked references and repeated deterministic operations in scripts.
Preserve supported agent metadata. Do not add generic quality ceremonies or
unneeded skills. Use [init_skill.py](scripts/init_skill.py) for a new package;
`--evals` optionally adds a scaffold. Test changed scripts directly.

## 4. Check structure, then behavior

Run [validate_skill.py](scripts/validate_skill.py) on the package. Structural
success is not behavioral evidence. Read the
[executor contract](references/executor-contract.md) before agent runs and the
[evaluation schema](references/evaluation-schema.md) before defining checks.
Use [run_eval.py](scripts/run_eval.py) with its Codex or Claude CLI adapter to run
fresh matched processes; it never configures authentication.

Compare no-target-skill versus candidate for a new skill, and no-target-skill,
previous accepted version, and candidate for a revision. Match cases, fixtures,
model, capabilities, limits, and settings. Grade actual deliverables independently;
self-reported success and LLM preference alone cannot justify adoption. Test native
routing separately with positive, negative, near-miss, and relevant sibling cases.
Explicit loading in a behavior trial is not routing evidence.

## 5. Decide from the limits as well as the results

Use development failures for bounded edits, validation evidence for selection,
and open the final test set only after freezing the candidate. A held-out case
used to guide a fix becomes regression data and needs a fresh replacement.

Report case-level wins, losses, ties, critical regressions, costs, and uncertainty.
Retain/promote only when the predeclared benefit is demonstrated without critical
regressions and at acceptable cost. Simplify or reject a tie with extra overhead.
Use `inconclusive` for missing comparison, weak evidence, or contamination and
`blocked` for infrastructure failure. Preserve negative results and historical
ledgers. Report package compatibility, adapter tests, and real agent validation
separately for each engine; an unavailable runtime is unrun, never passed.
