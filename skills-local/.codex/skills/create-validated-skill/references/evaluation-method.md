# Evidence before adoption

## What the evidence says

SkillsBench 1.1 reports configuration-dependent degradation from self-generated
skills and mixed task-level effects even for curated skills. Execution-feedback
optimization can help, but neither a polished instruction nor a self-score proves
local benefit. These findings concern agent task performance, not model-weight
deterioration. Useful sources:

- https://www.skillsbench.ai/blogs/skillsbench-1-1
- https://github.com/microsoft/SkillOpt
- https://arxiv.org/abs/2609.39148 (held-out generalization)
- https://arxiv.org/abs/2608.11888 (functional failures and efficiency regressions)
- https://arxiv.org/abs/2605.24050 (library selection effects)

These motivate controls, not a universal numeric adoption threshold.

## Candidate, experiment, decision

Keep creation, evaluation, and adoption separate. Before editing, record the user
problem, existing mechanism, previous accepted version, intended improvement,
critical constraints, expected efficiency cost, and edit budget. Preserve the
one-shot version if studying optimization, rather than replacing its evidence.

Use matched no-target-skill/candidate arms for new skills. Add the previous
accepted version for revisions. Every release case must have the comparison arms
needed for the claim. Pin model and match prompts, fixtures, tools, limits,
permissions, and settings. Counterbalance order across repeated trials. A single
trial is a smoke test; increase repetitions only for meaningful variability or
risk. Do not substitute a favorable aggregate for case-level regressions.

## Separate data by purpose

- Development cases may inform edits.
- Validation cases select among candidates.
- Final held-out cases are opened after a candidate is selected and frozen.

Vary task families, names, paths, schemas, constraints, and legitimate solutions.
Once a final case informs an edit, mark it as development/regression data and
obtain a fresh final case. Do not quietly relabel reused tests as held out.

The runner stages runtime skill files without evals/history, and fixtures without
graders. This prevents accidental disclosure, not malicious filesystem access.
It retains the existing authenticated agent context, which can discover global
skills. Therefore its built-in decision stays inconclusive unless it finds a
candidate defect or infrastructure blocker. It never claims airtight no-skill
isolation or automatically installs a candidate. Use an independently verified
environment and record its boundaries when stronger causal claims are needed.

## Measure actual outcomes

Use external deterministic tests for artifact existence, exact values, executable
behavior, invariants, and protected contracts. Test graders against a known-good
artifact and deliberate bad artifacts. Broad keyword checks are smoke assertions,
not proof of design quality. Check final files, not prose promising to write them.

Narrative outcomes can require independent qualitative review with a fixed rubric.
Blind reviewers to candidate identity where feasible. Preserve disagreements;
LLM-only ratings and self-reported success do not count as adoption evidence. V2
semantic/text suites remain readable but require review; the new runner does not
silently convert their old ratings into objective results.

## Routing is separate

Behavior trials explicitly load the candidate. Native discovery trials must not
mention its name/path in the user request. Test positive, negative, near-miss,
and plausible sibling competition in the actual engine. Record availability,
observed successful read, and correct application separately. A read is not
compliance; a missing trace observation is not proof of non-use. Tiny routing
samples are smoke evidence, not stable population rates.

## Decision and report

A predeclared adoption rule should require no critical regression, demonstrated
benefit on held-out outcomes, acceptable guardrails, and bounded overhead. An
equal mean can be justified by measured repeatability/error-prevention, never by
an unsupported narrative. Ties with extra work usually justify simplification.

Report retain/promote, reject, inconclusive, or blocked with reasons. Infrastructure
errors are not skill-quality failures and may not be silently discarded. Preserve
failed candidates, raw events, commands, versions, hashes, costs, and the cases
that disagree. Separate package-compatible, adapter-unit-tested, and behaviorally
validated labels per agent. Do not claim repository-wide improvement from one
small successful fixture.
