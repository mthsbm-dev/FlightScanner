#!/usr/bin/env bash
set -euo pipefail

MODEL=${OLLAMA_MODEL:-phi3}
API_URL=${OLLAMA_URL:-http://127.0.0.1:11434/api/generate}

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required" >&2
  exit 1
fi

if [ $# -gt 0 ]; then
  PROMPT="$*"
else
  PROMPT="$(cat)"
fi

LOCAL_TIMEOUT=${LOCAL_LLM_TIMEOUT:-10}

# Hard timeout so callers (heartbeats/crons) can't hang forever.
# Uses GNU coreutils `timeout` (available on this host).
timeout "${LOCAL_TIMEOUT}s" python3 - "$API_URL" "$MODEL" "$PROMPT" <<'PY'
import json, sys, urllib.request

api_url = sys.argv[1]
model = sys.argv[2]
prompt = sys.argv[3]
payload = {
    "model": model,
    "prompt": prompt,
    # stream defaults to true; we consume the NDJSON stream below
}
req = urllib.request.Request(
    api_url,
    data=json.dumps(payload).encode(),
    headers={"Content-Type": "application/json"},
)
try:
    # Keep this short; the shell-level `timeout` is the real guardrail.
    with urllib.request.urlopen(req, timeout=8) as resp:
        chunks = []
        for raw_line in resp:
            line = raw_line.decode().strip()
            if not line:
                continue
            event = json.loads(line)
            chunks.append(event.get("response", ""))
            if event.get("done"):
                break
except Exception as exc:
    print(f"[local_llm] request failed: {exc}", file=sys.stderr)
    sys.exit(2)
print("".join(chunks).strip())
PY
