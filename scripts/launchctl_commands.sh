#!/bin/bash
# Helper: common launchctl and FlightScanner commands
# Copy this file somewhere safe and make executable:
# chmod +x scripts/launchctl_commands.sh

PLIST=~/Library/LaunchAgents/com.flightscanner.runner.plist
LABEL=com.flightscanner.runner
REPO_DIR=/Users/bohm/Documents/Programmierung/AI/FlightScanner
WRAPPER="${REPO_DIR}/scripts/run_flightscanner.sh"
LOG=/tmp/flightscanner.log
ERR=/tmp/flightscanner.err.log

echo "FlightScanner launchctl helper"
echo

cat <<'EOF'
INSTALL
  # copy plist to LaunchAgents and load it (runs at load and every StartInterval)
  cp scripts/com.flightscanner.runner.plist ~/Library/LaunchAgents/
  launchctl load ~/Library/LaunchAgents/com.flightscanner.runner.plist

UNINSTALL
  # unload and remove
  launchctl unload ~/Library/LaunchAgents/com.flightscanner.runner.plist
  rm -f ~/Library/LaunchAgents/com.flightscanner.runner.plist

START NOW
  # start the job immediately (uses the Label)
  launchctl start ${LABEL}

STOP
  # stop the job
  launchctl stop ${LABEL}

STATUS
  # show job status
  launchctl list ${LABEL}

LOGS
  # tail logs written by the wrapper
  tail -n 200 ${LOG}
  tail -n 200 ${ERR}

RELOAD (after editing plist)
  # unload, copy edited plist, load
  launchctl unload ${PLIST}
  cp scripts/com.flightscanner.runner.plist ${PLIST}
  launchctl load ${PLIST}

HELPFUL
  # make wrapper executable (do once)
  chmod +x ${WRAPPER}

OTHER
  # reset dedupe store of sent matches
  python ${REPO_DIR}/run.py --reset-sent

  # send a test notification (Telegram/SMTP) using config.ini
  python ${REPO_DIR}/run.py --test-telegram
EOF

echo
echo "Usage examples:" 
echo "  ./scripts/launchctl_commands.sh            # prints helper and examples" 
echo "  cp scripts/com.flightscanner.runner.plist ~/Library/LaunchAgents/" 
echo "  launchctl load ~/Library/LaunchAgents/com.flightscanner.runner.plist" 
echo "  launchctl start ${LABEL} # run now" 
echo "  tail -f ${LOG}    # watch output log" 
