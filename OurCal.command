#!/bin/bash
# Double-click launcher for OurCal. Opens the dashboard in your browser.
cd "$(dirname "$0")" || exit 1

# Open the browser shortly after the server starts (runs in the background).
( sleep 2; open "http://127.0.0.1:8756" >/dev/null 2>&1 ) &

# macOS ships Python 3.9, which Google's libraries warn about on every launch.
# Prefer a newer one when it's installed; 3.9 still works if it isn't.
PY=python3
for candidate in python3.14 python3.13 python3.12 python3.11 python3.10; do
  if command -v "$candidate" >/dev/null 2>&1; then PY=$candidate; break; fi
done

"$PY" ourcal.py
status=$?

echo
if [ $status -ne 0 ]; then
  echo "OurCal exited with an error (code $status). See the messages above."
else
  echo "OurCal stopped."
fi
echo "Press any key to close this window."
read -n 1 -s
