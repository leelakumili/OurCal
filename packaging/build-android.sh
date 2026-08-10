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

# The OAuth client ships inside the APK so a fresh install can sign in with
# no computer. credentials.json is git-ignored and stays that way — it comes
# from the working tree locally, and from a secret in CI. Absent, the build
# still succeeds and produces a paste-only APK.
if [ -f "$ROOT/credentials.json" ]; then
  mkdir -p "$ANDROID/src/ourcal/resources"
  cp "$ROOT/credentials.json" "$ANDROID/src/ourcal/resources/bundled_credentials.json"
  echo "bundling the OAuth client from $ROOT/credentials.json"
else
  rm -f "$ANDROID/src/ourcal/resources/bundled_credentials.json"
  echo "no credentials.json — building a paste-only APK"
fi

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

# Release signing is opt-in through the environment, absent by default. A
# laptop build must never start demanding secrets: this script is the one
# piece shared between CI and a developer's machine, and CI is the only
# caller that has a keystore. Without all four values it produces the same
# app-debug.apk it always has.
SIGNING=debug
MISSING=""
[ -n "${ANDROID_KEYSTORE_PATH:-}" ]     || MISSING="$MISSING ANDROID_KEYSTORE_PATH"
[ -n "${ANDROID_KEYSTORE_PASSWORD:-}" ] || MISSING="$MISSING ANDROID_KEYSTORE_PASSWORD"
[ -n "${ANDROID_KEY_ALIAS:-}" ]         || MISSING="$MISSING ANDROID_KEY_ALIAS"
[ -n "${ANDROID_KEY_PASSWORD:-}" ]      || MISSING="$MISSING ANDROID_KEY_PASSWORD"
if [ -z "$MISSING" ]; then
  [ -f "$ANDROID_KEYSTORE_PATH" ] || {
    echo "ANDROID_KEYSTORE_PATH is set but $ANDROID_KEYSTORE_PATH does not exist"; exit 1; }
  SIGNING=release
  echo "signing with the release keystore"
  # `briefcase package android --help` shows --adhoc-sign and -i/--identity
  # are both "Ignored; signing is not supported" for Android, so Briefcase
  # can only hand us an unsigned release APK (-p apk). It relocates that APK
  # from build/.../outputs/apk/release/app-release-unsigned.apk to its own
  # dist/ dir, renamed to OurCal-<version>.apk. apksigner, from the same
  # Android SDK Briefcase already downloaded, does the actual signing, in
  # place, so there is exactly one release APK to find afterwards.
  rm -rf "$ANDROID/dist"   # clean slate so the find below can't pick up a stale file
  "$VENV/bin/briefcase" package android -p apk --no-input
  # Briefcase's tool cache lives under ~/Library/Caches on macOS and under
  # ~/.cache/briefcase on Linux (verify-apk.sh already searches both; this is
  # the same fix applied where the APK actually gets signed). ANDROID_HOME
  # covers a runner or developer machine that points at its own SDK instead
  # of Briefcase's cache — belt and braces alongside pinning ANDROID_HOME
  # empty in the CI job so Briefcase's cache is what actually gets used
  # there. Searching all three unconditionally is NOT safe on its own: under
  # `set -euo pipefail`, `find` exits nonzero on whichever start path does
  # not exist on this OS, and that nonzero status — not just its stderr —
  # propagates through the pipe and kills the script right here, before the
  # `[ -n "$APKSIGNER" ]` guard below ever runs. `2>/dev/null` only hides the
  # message; `|| true` is what stops the exit status from doing that.
  APKSIGNER=$(find ~/Library/Caches/org.beeware.briefcase/tools/android_sdk/build-tools \
                   ~/.cache/briefcase/tools/android_sdk/build-tools \
                   "${ANDROID_HOME:-/nonexistent}/build-tools" \
                -name apksigner 2>/dev/null | sort -V | tail -1) || true
  [ -n "$APKSIGNER" ] || { echo "apksigner not found under the Android SDK build-tools"; exit 1; }
  # Same `pipefail` hazard here from a different direction: `head -1` can
  # close its end of the pipe as soon as it has its line, SIGPIPE-ing a
  # still-writing `find` and poisoning the pipeline's exit status even
  # though a match was found. `|| true` covers both failure modes.
  UNSIGNED=$(find "$ANDROID/dist" -name "*.apk" | head -1) || true
  [ -n "$UNSIGNED" ] || { echo "no unsigned release APK produced"; exit 1; }
  "$APKSIGNER" sign --ks "$ANDROID_KEYSTORE_PATH" --ks-key-alias "$ANDROID_KEY_ALIAS" \
    --ks-pass env:ANDROID_KEYSTORE_PASSWORD --key-pass env:ANDROID_KEY_PASSWORD "$UNSIGNED"
else
  echo "no release keystore in the environment — missing:$MISSING — building a debug APK"
  "$VENV/bin/briefcase" build android --no-input
fi

if [ "$SIGNING" = release ]; then
  # briefcase package relocates the release APK to $ANDROID/dist (renamed,
  # no "release" in the filename), not under $ANDROID/build like the debug
  # APK — see the signing block above.
  APK=$(find "$ANDROID/dist" -name "*.apk" | head -1) || true
else
  APK=$(find "$ANDROID/build" -name "app-debug.apk" | head -1) || true
fi
[ -n "$APK" ] || { echo "no APK produced"; exit 1; }
mkdir -p "$DIST"
OUT=$DIST/OurCal-$VERSION-android.apk
cp "$APK" "$OUT"
# Which path was taken, for the verifier and the release gate to read rather
# than re-derive. Two places deciding this independently could disagree.
# Not dotfile-prefixed: actions/upload-artifact@v4 (>=4.4.0) excludes hidden
# files by default, which would silently drop this from the CI artifact.
echo "$SIGNING" > "$DIST/apk-signing.txt"
echo
echo "built: $OUT ($(du -h "$OUT" | cut -f1))  [$SIGNING-signed]"
