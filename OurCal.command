#!/bin/bash
# Double-click launcher for OurCal. Opens the dashboard in your browser.
cd "$(dirname "$0")" || exit 1

# The browser is opened by ourcal.py itself (run_server), not from here: the
# port can move if 8756 is busy, and every run mints a fresh session key
# that must be in the URL's ?k= or / and /setup answer 403. Only the process
# that started the server knows either value, so this script no longer
# guesses a URL — it just launches ourcal.py and lets it open the right one.

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
