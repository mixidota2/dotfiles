# Intent, evidence, and adoption

## Sources and what they support

Official practical guidance and empirical findings answer different questions:

- [Anthropic's skill-creator guidance](https://claude.com/resources/articles/improving-skill-creator-test-measure-and-refine-agent-skills)
  distinguishes capability uplift from encoded preferences. Workflow fidelity is
  a valid purpose even when the model can already perform the component tasks.
  Its [creator](https://github.com/anthropics/skills/blob/main/skills/skill-creator/SKILL.md)
  starts from intent; execution history is one source, not an eligibility condition.
- [OpenAI's evaluation guide](https://developers.openai.com/blog/eval-skills)
  separates outcome, process, style, and efficiency checks and starts small.
  The [Codex creator](https://github.com/openai/codex/blob/main/codex-rs/skills/src/assets/samples/skill-creator/SKILL.md)
  also works from intended uses and concrete examples.
- [SkillLens](https://arxiv.org/html/2605.23899) studies experience extraction and
  downstream consumption. It finds target-dependent utility and warns that textual
  plausibility alone does not establish benefit; concrete remedies can be useful.
  Its trajectory-based experiment is not a requirement for all skill creation.
- [SkillOpt](https://arxiv.org/abs/2605.23904) motivates bounded edits selected on
  validation outcomes. We reuse that discipline, not its full optimization system.
- [Held-out generalization research](https://arxiv.org/html/2609.39148v2) identifies
  overfitting from turning task-specific details into universal rules and evaluates
  frozen artifacts on separate tasks. Its results do not guarantee local gains.
- [Evaluation/evolution survey](https://arxiv.org/html/2606.11435v1) describes gaps
  across creation, retrieval, task utility, and operational cost. These are distinct
  evidence needs, not a mandate to reproduce every benchmark.

Our design inference is to preserve required intent first, ground conditional
instructions in known inputs, and test both the generated artifact and its use.
No cited paper proves this repository's creator improves a particular model.

## Draft from intent, experience, or both

Record purpose, scope, must/never behavior, output/evidence, and optional choices
in a short note or existing task record. Do not require a new schema or file. For
example, if the user requires inspecting tests before a patch and reporting the
command/result afterward, those steps are correctness requirements. The choice of
search command or note layout is usually optional unless explicitly prescribed.

Turn requirements and reliable specifications into conditional executable steps.
When history exists, inspect successful actions as well as failures, locate the
first consequential error, and propose a bounded remedy. Without history, state
which expected benefit is a hypothesis and design a case that could refute it.
Do not manufacture logs, infer universal rules from a single failure, or reject a
candidate simply because only intent and expert knowledge are available.

Keep checks anchored to the original request/specification rather than regenerating
them from each candidate. Required behavior cannot be traded away for speed. If
requirements themselves change, record the user-directed change separately. A
checklist mention is not proof of execution; inspect the result or action evidence.

## Candidate, experiment, decision

Create candidates outside discoverable roots (for example `skill-candidates/` at
repository root). `init_skill.py` refuses known `.codex/skills`, `.claude/skills`,
`.agents/skills`, and `.pi/agent/skills` paths, including resolved symlink targets,
and adds a zero-content `.candidate` marker. The installer skips marked packages
for both Codex and Claude and never removes an existing installation on that basis.
Unmarked existing packages remain installable; there is no retroactive registry.

For revisions, use a separate copy or checkout and mark the candidate there. Do
not edit through a live installed symlink: skipping installation cannot undo such
an edit. A marker is an installer safeguard, not a native discovery control or
revocation mechanism. Keep candidates outside discovery roots even when marked.
After a documented adoption decision, remove the marker and move/copy the reviewed
version into `skills-local/.codex/skills` in a reviewed change. Installation is a
separate authorized action. Reject/blocked/inconclusive candidates stay isolated.
Do not mark all existing skills or uninstall accepted versions as a migration.

Use matched no-target-skill/candidate arms for new skills and also the accepted
version for revisions. Pin model and match task inputs, tools, permissions, limits,
and settings. Preserve the initial draft if studying optimization. Start small;
repeat only where variability or risk matters. Do not substitute a favorable
aggregate for a case-level regression.

## Author-to-consumer test and data separation

Development may inform edits; validation selects among candidates; final unseen
cases test the selected frozen artifact. Vary inputs, names, paths, schemas, and
legitimate solutions. Once a final case informs an edit, retain it as regression
data and obtain a fresh final case. Do not relabel reused tests as unseen.

For a creator revision, run old and candidate creators on the same creation brief.
Save their generated runtime packages and exact commits/hashes. Fresh consumers
then solve the same unseen downstream tasks with those artifacts and a no-target-
skill baseline. Consumers get task inputs and their assigned runtime skill, without
the author's conversation, training logs, expected answers, or grader. A generator
producing a good-looking SKILL.md is only the first stage. The consumer's observable
behavior and actual output establish whether that skill works. An independent
coordinator should retain the original requirement checks and unseen fixtures.

`run_eval.py --freeze-inputs` records candidate/evaluation hashes without a model
call. Holdout requires `--frozen-inputs`; mismatches stop before CLI startup. Keep
that record in a separate committed artifact or with the coordinator. Hashes detect
input drift but cannot prove chronology, hiddenness, or author independence. The
runner stages runtime files without evals/history and fixtures without graders;
this is data hygiene, not filesystem isolation. Global skills can still contaminate
arms, so automatic adoption remains disabled. Independent cloud tasks are suitable
when a nested CLI cannot start; never change authentication/security to force it.

## Measure and report the claim actually tested

Use external deterministic checks for exact values, files, executable behavior,
invariants, and protected contracts. Exercise graders with known-good and deliberately
wrong artifacts. Use independent review for semantic/process fidelity that cannot
be checked mechanically; LLM preference or self-reported success alone is insufficient.
V2 qualitative suites remain readable, not silently converted into objective passes.
Native routing needs separate positive, negative, near-miss, and sibling cases with
no explicit skill name/path. Distinguish availability, observed read, and compliance.

First report required/prohibited behavior, then quality and efficiency of compliant
results. Preference fidelity can justify retention at tied capability scores;
remove only optional overhead. A performance-gain claim needs matched downstream
evidence and uncertainty appropriate to the sample, beyond merely meeting checks.
A few representative cases support scoped acceptance, not statistical superiority.
Report wins/losses/ties, critical regressions, costs, missing observations, and the
adoption decision. Preserve failures and historical ledgers. Infrastructure errors
are blocked/unrun, not skill-quality failures. Package compatibility, adapter unit
tests, native routing, and real model validation remain separate per engine.
