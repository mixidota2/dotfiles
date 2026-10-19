"""Merge portable, public-safe Orca settings into local-default profile-state.db.

Orca stores user preferences alongside machine-local paths, repo IDs, and
session state inside profile-state.db (table profile_state_documents,
domain='settings'). Only the portable keys in settings-additions.json are
merged into the existing settings document when profile-state.db exists.

Usage: merge_settings.py ADDITIONS TARGET_DB [--check]
"""

import hashlib
import json
import os
import sqlite3
import sys
import time

USAGE = "usage: merge_settings.py ADDITIONS TARGET_DB [--check]"


def merge(current, additions):
    """Return current with additions set, merging nested objects key by key."""
    merged = dict(current)
    for key, value in additions.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def read_additions(path):
    with open(path, encoding="utf-8") as f:
        value = json.load(f)
    if not isinstance(value, dict):
        raise ValueError("not a JSON object")
    return value


def main(argv):
    if len(argv) not in (2, 3) or (len(argv) == 3 and argv[2] != "--check"):
        print(USAGE, file=sys.stderr)
        return 2
    additions_path, db_path = argv[0], argv[1]
    check_only = len(argv) == 3
    if not os.path.exists(db_path):
        return 0
    try:
        additions = read_additions(additions_path)
        conn = sqlite3.connect(db_path, timeout=2.0)
        with conn:
            row = conn.execute(
                "SELECT payload, revision FROM profile_state_documents WHERE domain = 'settings'"
            ).fetchone()
            if row is None:
                return 0
            current = json.loads(row[0])
            if not isinstance(current, dict):
                raise ValueError("settings payload is not a JSON object")
            merged = merge(current, additions)
            if merged == current:
                print(f"settings {db_path} is up to date")
                return 0
            if check_only:
                print(f"settings {db_path} would be updated")
                return 0
            new_payload = json.dumps(merged, separators=(",", ":"), ensure_ascii=False)
            new_hash = hashlib.sha256(new_payload.encode("utf-8")).hexdigest()
            now_ms = int(time.time() * 1000)
            conn.execute(
                "UPDATE profile_state_documents "
                "SET payload = ?, content_hash = ?, revision = revision + 1, updated_at = ? "
                "WHERE domain = 'settings'",
                (new_payload, new_hash, now_ms),
            )
        conn.close()
    except (OSError, ValueError, sqlite3.Error) as e:
        print(f"cannot merge Orca settings into {db_path}: {e}", file=sys.stderr)
        return 1
    print(f"settings {db_path} updated")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
