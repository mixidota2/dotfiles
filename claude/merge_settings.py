"""Merge the keys this repository manages into Claude Code's user settings.

~/.claude/settings.json also holds settings that Claude Code and other apps
(for example Orca's hooks) write themselves, so it is not linked. Only the keys
in the additions file are set; every other key is kept as it is. The file is
rewritten only when a value changes.

Usage: merge_settings.py ADDITIONS TARGET [--check]
Exit status: 0 when TARGET is (or with --check, already is) up to date or was
updated, 1 when a file cannot be read as a JSON object (TARGET is left as is).
"""

import json
import os
import sys
import tempfile

USAGE = "usage: merge_settings.py ADDITIONS TARGET [--check]"


def merge(current, additions):
    """Return current with additions set, merging nested objects key by key."""
    merged = dict(current)
    for key, value in additions.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def read_object(path):
    with open(path, encoding="utf-8") as f:
        value = json.load(f)
    if not isinstance(value, dict):
        raise ValueError("not a JSON object")
    return value


def write_atomically(path, value):
    # Write through a symlinked settings file instead of replacing the link.
    path = os.path.realpath(path)
    directory = os.path.dirname(path)
    fd, temp = tempfile.mkstemp(prefix=".settings-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            # Claude Code writes this format; keep it so unrelated lines do not change.
            f.write(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
        if os.path.exists(path):
            os.chmod(temp, os.stat(path).st_mode & 0o777)
        os.replace(temp, path)
    except BaseException:
        os.unlink(temp)
        raise


def main(argv):
    if len(argv) not in (2, 3) or (len(argv) == 3 and argv[2] != "--check"):
        print(USAGE, file=sys.stderr)
        return 2
    additions_path, target_path = argv[0], argv[1]
    check_only = len(argv) == 3
    try:
        additions = read_object(additions_path)
        current = read_object(target_path) if os.path.exists(target_path) else {}
    except (OSError, ValueError) as e:
        print(f"cannot merge Claude settings into {target_path}: {e}", file=sys.stderr)
        return 1
    merged = merge(current, additions)
    if merged == current and os.path.exists(target_path):
        print(f"settings {target_path} is up to date")
        return 0
    if check_only:
        print(f"settings {target_path} would be updated")
        return 0
    write_atomically(target_path, merged)
    print(f"settings {target_path} updated")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
