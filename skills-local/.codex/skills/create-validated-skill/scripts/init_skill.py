#!/usr/bin/env python3
"""Initialize a skill package, optionally including version-3 evaluation scaffolding."""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path


NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "assets" / "evals-template"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("name", help="lowercase kebab-case skill name")
    parser.add_argument("--path", required=True, type=Path, help="parent skills directory")
    parser.add_argument("--evals", action="store_true", help="include the optional version-3 evaluation scaffold")
    return parser.parse_args()


def skill_markdown(name: str, include_evals: bool = False) -> str:
    title = name.replace("-", " ").title()
    resources = "\n## Resources\n\n- `evals/eval.yaml`: optional behavioral evaluation manifest.\n" if include_evals else ""
    return f"""---
name: {name}
description: TODO Describe what this skill does and the concrete requests that trigger it.
---

# {title}

TODO State the outcome this skill enables.

## Workflow

1. TODO Define the first observable action.
2. TODO Define validation and completion.

{resources}"""


def main() -> int:
    args = parse_args()
    if not NAME_RE.fullmatch(args.name) or len(args.name) > 64:
        print("error: name must be lowercase kebab-case and at most 64 characters", file=sys.stderr)
        return 2

    target = args.path.expanduser().resolve() / args.name
    if target.exists() or target.is_symlink():
        print(f"error: target already exists: {target}", file=sys.stderr)
        return 2

    if args.evals and not TEMPLATE.is_dir():
        print(f"error: evaluation template is missing: {TEMPLATE}", file=sys.stderr)
        return 2

    target.mkdir(parents=True)
    (target / "SKILL.md").write_text(skill_markdown(args.name, args.evals), encoding="utf-8")
    if args.evals:
        shutil.copytree(TEMPLATE, target / "evals")
        for path in (target / "evals").rglob("*.yaml"):
            text = path.read_text(encoding="utf-8").replace("TODO-skill-name", args.name)
            path.write_text(text, encoding="utf-8")
    print(f"initialized {target}")
    print("next: replace scaffold placeholders and choose validation proportional to the change")
    if args.evals:
        print("evaluation scaffold: version 3; replace its placeholders before running evaluations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
