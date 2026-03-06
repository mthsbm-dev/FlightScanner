#!/usr/bin/env python3
"""Helper to call Anthropic Messages API via scripts/anthropic.sh and parse JSON.

We keep this as a thin wrapper so the digest script can batch-classify emails.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[1]
ANTHROPIC_SH = WORKSPACE / "scripts" / "anthropic.sh"


def call_claude_json(prompt: str, max_tokens: int = 2000) -> object:
    import os
    env = dict(os.environ)
    # override to avoid truncated JSON
    env["ANTHROPIC_MAX_TOKENS"] = str(max_tokens)

    proc = subprocess.run(
        [str(ANTHROPIC_SH)],
        cwd=str(WORKSPACE),
        env=env,
        input=prompt,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"anthropic.sh failed ({proc.returncode}): {proc.stderr.strip()[:500]}")

    out = proc.stdout.strip()

    # anthropic.sh often wraps json in fences; also sometimes includes leading text.
    # Try to extract the first JSON object/array substring.
    if out.startswith("```"):
        lines = out.splitlines()
        # drop first fence line like ```json
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        # drop last fence line ```
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        out = "\n".join(lines).strip()

    # Heuristic: find first '{' or '[' and parse from there.
    m = re.search(r"[\[{]", out)
    if m:
        out = out[m.start():].strip()

    # If there is trailing commentary after JSON, trim after the last '}' or ']'
    last_brace = max(out.rfind('}'), out.rfind(']'))
    if last_brace != -1:
        out = out[: last_brace + 1]

    return json.loads(out)


if __name__ == "__main__":
    prompt = sys.stdin.read() if not sys.argv[1:] else " ".join(sys.argv[1:])
    obj = call_claude_json(prompt)
    print(json.dumps(obj, ensure_ascii=False))
