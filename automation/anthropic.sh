#!/usr/bin/env bash
set -euo pipefail

if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
  if [ -f "$HOME/.profile" ]; then
    # shellcheck disable=SC1090
    source "$HOME/.profile" >/dev/null 2>&1 || true
  fi
fi

if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
  echo "ANTHROPIC_API_KEY is not set" >&2
  exit 2
fi

MODEL=${ANTHROPIC_MODEL:-claude-3-haiku-20240307}
MAX_TOKENS=${ANTHROPIC_MAX_TOKENS:-400}

if [ $# -gt 0 ]; then
  PROMPT="$*"
else
  PROMPT="$(cat)"
fi

python3 - "$MODEL" "$PROMPT" "$MAX_TOKENS" <<'PY'
import json, os, sys, urllib.request

model = sys.argv[1]
prompt = sys.argv[2]
max_tokens = int(sys.argv[3])
api_key = os.environ.get("ANTHROPIC_API_KEY")
if not api_key:
    print("ANTHROPIC_API_KEY is not set", file=sys.stderr)
    sys.exit(2)

payload = {
    "model": model,
    "max_tokens": max_tokens,
    "messages": [
        {"role": "user", "content": prompt}
    ],
}
req = urllib.request.Request(
    "https://api.anthropic.com/v1/messages",
    data=json.dumps(payload).encode(),
    headers={
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
)
try:
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read())
except Exception as exc:
    print(f"[anthropic] request failed: {exc}", file=sys.stderr)
    sys.exit(3)

content = data.get("content", [])
if not content:
    print("[anthropic] empty response", file=sys.stderr)
    sys.exit(4)
print(content[0].get("text", "").strip())
PY
