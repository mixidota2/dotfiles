# Evaluation schema and CLI

Python 3 plus PyYAML are required. V2 historical manifests/ledgers remain intact;
new objective checks use version 3. Existing qualitative rubrics are not silently
regraded or treated as objective passes.

## Manifest

```yaml
name: example-skill
version: 3
tasks: [tasks/development.yaml]
validation: [tasks/validation.yaml]
holdout: [tasks/final.yaml]
```

Task files require unique safe `id`, nonempty `scenario`, and nonempty
`requirements` with unique IDs and at least one `critical: true`. Optional `fixture`
is a directory relative to evals/. `workspace_write: true` requests writable Codex
work; default is read-only. Claude writable trials report capability blocked by default. Explicit
`--claude-accept-workspace-edits` approves per-run file edits in each disposable
workspace using `acceptEdits`; only Read/Glob/Grep/Write/Edit are exposed. No shell,
network, additional directories, or persistent permission change is included.
An external trusted grader may run file tests. This transport is not claimed
runtime-validated without a real Claude run. Do not substitute a narrative task for a promised
working file. Example task:

```yaml
id: transform-input
kind: typical
fixture: fixtures/input-a
workspace_write: true
scenario: Convert input.csv into output.json using the specified schema.
requirements:
  - id: exact-output
    critical: true
    description: Output has the independently calculated values.
    objective:
      - kind: command
        argv: [python3, '{eval_root}/graders/check_output.py', '{workspace}']
        timeout_seconds: 30
```

Objective check kinds:
- `contains` / `regex`: `value` tested only against final deliverable text.
- `json_equals`: parse deliverable JSON, select optional RFC6901 `pointer`, compare `value`.
- `file_exists`: `path` is a contained workspace-relative file.
- `file_contains`: `path` plus literal `value`.
- `command`: argv list, no shell; exit zero passes. `{workspace}` and `{eval_root}`
  are substituted. Store fixed grader code under evals/, outside the fixture.

Objective checks should be exercised with both valid and deliberately wrong
artifacts. A text match checks a literal, not broad semantic correctness. Missing
objective checks produce `review_required`, never an automatic pass. Empty case
selection, invalid output, missing completed trace, and executor errors fail closed.

## Commands

```sh
python3 scripts/validate_skill.py /path/to/skill
python3 scripts/run_eval.py /path/to/candidate --engine codex --model MODEL \
  --baseline --previous-skill /path/to/previous --split validation
python3 scripts/run_eval.py /path/to/candidate --freeze-inputs /path/to/experiment/frozen-inputs.json
python3 scripts/run_eval.py /path/to/candidate --engine claude --model MODEL \
  --baseline --split holdout --frozen-inputs /path/to/experiment/frozen-inputs.json
```

`--freeze-inputs` creates an exclusive (never overwritten) hash record outside the
skill package and exits without calling a CLI. It hashes runtime files and eval
inputs (manifest, cases, fixtures, graders), excluding results, ledger, and evidence.
Have the independent coordinator keep/commit this record before final evaluation.
`--frozen-inputs` compares the staged inputs before CLI startup; any mismatch blocks
the run. The legacy `--candidate-frozen` declaration alone no longer permits holdout.
Input snapshots are also checked after execution. These checks detect input changes,
not whether the author previously saw a case or regenerated the record. They do not
replace separation of author and consumer or an independently retained holdout.

Omit `--previous-skill` for new skills. `--baseline` includes no-target-skill;
revisions automatically include it. `--case ID` narrows cases; `--trials N` repeats
fresh matched trials. `--binary` selects an existing official CLI. The runner is a
behavior harness, not a native routing test. `--no-ledger` leaves the source ledger
unchanged. `--self-test` runs infrastructure checks only, with no model calls.

Exit codes: 1 candidate objective failure; 2 infrastructure/configuration error;
3 completed but inconclusive experiment. There is intentionally no automatic
adoption exit code. `--self-test` returns 0 when its infrastructure checks pass.
`--freeze-inputs` returns 0 when the record is written, not when behavior is validated.

## Evidence

Each run has a timestamp plus random unique ID and is created exclusively, so
same-second runs cannot overwrite each other. Inputs, per-arm output, raw events,
stderr, external grader output, result YAML, and a summary are retained. Summaries
record agent/CLI/model, split, content hashes, matched case comparisons, duration,
usage, limitations, and the decision. Default model selection remains explicitly
unresolved. A hash is reproducibility evidence, not isolation or behavioral proof.
When supplied, `input_freeze` preserves the prior record in the summary and ledger.
Its evaluation hash covers source inputs; `evaluation_sha256` additionally covers
the runner's derived manifest and selected-task snapshots. Legacy `with_skill` and
current `candidate` failure annotations both feed failure-pattern history; objective
failures remain observations until a concrete remedy is supplied, never fabricated
general rules.

## Ledger paths

New ledger records are schema-marked. Result directories are skill-relative when
contained in the skill, otherwise `results_dir: null` with `results_dir_scope:
external`. Artifact paths are run-relative; out-of-scope artifacts are null and
listed as unresolved. Historical records are preserved verbatim as data. Raw
artifacts may contain local paths or sensitive user data; normalization is not
anonymization. Review artifacts before committing/sharing them. Never delete
historical failed experiments to make a candidate look better.
