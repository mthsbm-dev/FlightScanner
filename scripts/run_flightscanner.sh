#!/bin/bash
set -euo pipefail

# Wrapper to run FlightScanner once in the repository virtualenv
cd /Users/bohm/Documents/Programmierung/AI/FlightScanner

# Activate venv if present
if [ -f .venv/bin/activate ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

# Run the single-run CLI
exec python run.py --once
