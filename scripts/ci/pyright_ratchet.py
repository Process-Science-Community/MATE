#!/usr/bin/env python3
"""Pyright ratchet: no file may gain type errors; fixed errors lower the ceiling.

    uv run python scripts/ci/pyright_ratchet.py            # check (CI + `make ci-static`)
    uv run python scripts/ci/pyright_ratchet.py --update   # rewrite the baseline after fixing errors

`main` carried 306 strict-mode errors when CI was introduced (2026-11-18). Failing
on all of them would make the check useless; ignoring them lets the count grow.
The ratchet keeps a per-file error ceiling in scripts/ci/pyright-baseline.json:
a file above its ceiling (or a new file with errors) fails; a file below it
asks for `--update` so the improvement is locked in.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BASELINE = Path(__file__).with_name("pyright-baseline.json")


def current_errors() -> Counter[str]:
    proc = subprocess.run(["pyright", "--outputjson"], capture_output=True, text=True, cwd=REPO)
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        sys.stderr.write(proc.stdout + proc.stderr)
        raise SystemExit("pyright did not return JSON") from None
    counts: Counter[str] = Counter()
    for diag in data.get("generalDiagnostics", []):
        if diag.get("severity") == "error":
            counts[str(Path(diag["file"]).resolve().relative_to(REPO))] += 1
    return counts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--update", action="store_true")
    args = ap.parse_args()

    now = current_errors()
    if args.update:
        BASELINE.write_text(json.dumps(dict(sorted(now.items())), indent=1) + "\n")
        print(f"baseline written: {sum(now.values())} errors in {len(now)} files")
        return 0

    base: dict[str, int] = json.loads(BASELINE.read_text()) if BASELINE.exists() else {}
    worse = {f: (base.get(f, 0), n) for f, n in now.items() if n > base.get(f, 0)}
    better = {f: (n, now.get(f, 0)) for f, n in base.items() if now.get(f, 0) < n}

    print(f"pyright: {sum(now.values())} errors (baseline {sum(base.values())})")
    for f, (b, n) in sorted(worse.items()):
        print(f"::error file={f}::pyright errors rose {b} -> {n} (run `uv run pyright {f}`)")
    if better:
        print(
            f"{len(better)} file(s) improved - lock it in with "
            "`uv run python scripts/ci/pyright_ratchet.py --update`."
        )
    return 1 if worse else 0


if __name__ == "__main__":
    raise SystemExit(main())
