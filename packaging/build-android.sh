#!/bin/bash
# Build OurCal.apk with Briefcase/BeeWare.
#
#   ./packaging/build-android.sh
#
# macOS is built separately by build-app.sh; this is Android only. ourcal.py is
# the single source of truth — it is copied into the Briefcase project here
# rather than duplicated, so the phone runs the same code the Mac does.
#
# First run downloads a JDK, the Android SDK and Gradle (several GB, minutes).
# Subsequent runs reuse them. Output: dist/OurCal-<version>-android.apk
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT=$PWD
ANDROID=$ROOT/packaging/android
DIST=$ROOT/dist
VERSION=$(python3 -c "import re;print(re.search(r'^VERSION = \"(.*)\"', open('ourcal.py').read(), re.M).group(1))")
echo "OurCal $VERSION — Android"

# Version must match ourcal.py, same rule the macOS build enforces.
PYPROJ_V=$(python3 -c "import re;print(re.search(r'^version = \"(.*)\"', open('$ANDROID/pyproject.toml').read(), re.M).group(1))")
[ "$PYPROJ_V" = "$VERSION" ] || { echo "version mismatch: pyproject $PYPROJ_V != ourcal.py $VERSION"; exit 1; }

# ── single source of truth ──────────────────────────────────────────────
# Copy, never fork: the phone must run the same ourcal.py as the Mac.
cp "$ROOT/ourcal.py" "$ANDROID/src/ourcal/core.py"

# ── build interpreter ───────────────────────────────────────────────────
PY=""
for c in python3.12 python3.11 python3.10; do
  command -v "$c" >/dev/null 2>&1 && { PY=$(command -v "$c"); break; }
done
[ -n "$PY" ] || { echo "need Python 3.10-3.12 for Briefcase"; exit 1; }
echo "building with $PY ($("$PY" -V))"

VENV=$ANDROID/.build-venv
[ -d "$VENV" ] || "$PY" -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip briefcase

# ── launcher icons from the same source as the macOS icon ───────────────
# icon.html is the one icon definition; render the Android mipmap set from it.
SRC=$ROOT/packaging/icon-1024.png
[ -f "$SRC" ] || { echo "missing $SRC — regenerate from packaging/icon.html"; exit 1; }
ICONDIR=$ANDROID/src/ourcal/resources
mkdir -p "$ICONDIR"
cp "$SRC" "$ICONDIR/ourcal.png"     # Briefcase derives densities from this

# ── build ───────────────────────────────────────────────────────────────
cd "$ANDROID"
"$VENV/bin/briefcase" create android --no-input || true   # idempotent if present
"$VENV/bin/briefcase" update android -r --no-input
"$VENV/bin/briefcase" build android --no-input

APK=$(find "$ANDROID/build" -name "app-debug.apk" | head -1)
[ -n "$APK" ] || { echo "no APK produced"; exit 1; }
mkdir -p "$DIST"
OUT=$DIST/OurCal-$VERSION-android.apk
cp "$APK" "$OUT"
echo
echo "built: $OUT ($(du -h "$OUT" | cut -f1))"
