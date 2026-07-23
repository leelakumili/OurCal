#!/bin/bash
# Double-click launcher for OurCal. Opens the dashboard in your browser.
cd "$(dirname "$0")" || exit 1

# Open the browser shortly after the server starts (runs in the background).
( sleep 2; open "http://127.0.0.1:8756" >/dev/null 2>&1 ) &

python3 ourcal.py
status=$?

echo
if [ $status -ne 0 ]; then
  echo "OurCal exited with an error (code $status). See the messages above."
else
  echo "OurCal stopped."
fi
echo "Press any key to close this window."
read -n 1 -s
