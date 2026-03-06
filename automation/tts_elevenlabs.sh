#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/tts_elevenlabs.sh "text" [output.mp3|wav|ogg]
       scripts/tts_elevenlabs.sh --file input.txt [output.mp3|wav|ogg]

Generates speech via ElevenLabs (model eleven_turbo_v2_5) using the configured voice.
EOF
}

if [[ ${1:-} == "" ]]; then
  usage
  exit 1
fi

TEXT=""
if [[ ${1:-} == "--file" ]]; then
  [[ ${2:-} != "" ]] || { echo "Missing file after --file" >&2; exit 1; }
  TEXT=$(<"$2")
  shift 2
else
  TEXT="$1"
  shift
fi

OUTPUT=${1:-/tmp/tts-elevenlabs.mp3}
mkdir -p "$(dirname "$OUTPUT")"

API_KEY_FILE=${ELEVENLABS_API_KEY_FILE:-$HOME/.config/elevenlabs/api_key}
VOICE_ID_FILE=${ELEVENLABS_VOICE_ID_FILE:-$HOME/.config/elevenlabs/voice_id}
[[ -f $API_KEY_FILE ]] || { echo "Missing API key file: $API_KEY_FILE" >&2; exit 2; }
[[ -f $VOICE_ID_FILE ]] || { echo "Missing voice id file: $VOICE_ID_FILE" >&2; exit 2; }
API_KEY=$(<"$API_KEY_FILE")
VOICE_ID=${ELEVENLABS_VOICE_ID:-$(<"$VOICE_ID_FILE")}
MODEL_ID=${ELEVENLABS_MODEL_ID:-eleven_turbo_v2_5}

PAYLOAD=$(ELEVEN_TEXT="$TEXT" ELEVEN_MODEL_ID="$MODEL_ID" python3 - <<'PY'
import json, os
text = os.environ.get('ELEVEN_TEXT', '')
model_id = os.environ.get('ELEVEN_MODEL_ID', 'eleven_turbo_v2_5')
payload = {
    "text": text,
    "model_id": model_id,
    "voice_settings": {
        "stability": 0.42,
        "similarity_boost": 0.7
    }
}
print(json.dumps(payload))
PY
)

curl -s -X POST "https://api.elevenlabs.io/v1/text-to-speech/$VOICE_ID" \
  -H "xi-api-key: $API_KEY" \
  -H "Content-Type: application/json" \
  -H "Accept: audio/mpeg" \
  -d "$PAYLOAD" \
  -o "$OUTPUT"

EXT=${OUTPUT##*.}
if [[ $EXT == "wav" || $EXT == "ogg" ]]; then
  TMP_MP3=$(mktemp /tmp/tts-elevenlabs-XXXXXX.mp3)
  mv "$OUTPUT" "$TMP_MP3"
  ffmpeg -loglevel warning -y -i "$TMP_MP3" "$OUTPUT"
  rm -f "$TMP_MP3"
fi

echo "ElevenLabs audio saved to $OUTPUT"
