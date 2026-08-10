#!/bin/bash
# Assert the things about a built APK that no test can cover, because they
# are properties of the packaged artifact rather than of the code.
#
# Each check corresponds to a failure this project has actually had:
#   1. the bundled OAuth client not reaching the APK
#   2. something personal reaching it
#   3. a debuggable release build, which lets adb read users' refresh tokens
#   4. a release that turned out to carry the universal debug signing key
#
# This script verifies the artifact it is handed. dist/.apk-signing is
# written only after build-android.sh finishes copying the APK, so a build
# that fails partway can leave the marker and APK from a previous, unrelated
# run in place. A pass here says the given APK satisfies these four checks —
# it is not proof that a build just succeeded.
#
# Usage: packaging/verify-apk.sh dist/OurCal-1.0.1-android.apk
set -euo pipefail

APK=${1:?usage: verify-apk.sh <apk>}
[ -f "$APK" ] || { echo "no such APK: $APK"; exit 1; }
ROOT=$(cd "$(dirname "$0")/.." && pwd)
SIGNING=$(cat "$ROOT/dist/.apk-signing" 2>/dev/null || echo unknown)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
unzip -q -o "$APK" -d "$WORK"
fail=0
note() { echo "  $1"; }
bad()  { echo "  FAIL: $1"; fail=1; }

echo "verifying $(basename "$APK")  [$SIGNING-signed]"

# 1. The OAuth client must be inside Chaquopy's nested app archive. Grepping
#    the extracted tree finds nothing even when it IS bundled — that false
#    negative already fooled us once. (Piping straight into `grep -q` is its
#    own false-negative trap under `set -o pipefail`: grep can close its end
#    of the pipe the instant it finds a match, SIGPIPE-ing the still-writing
#    producer, which then exits nonzero and poisons the pipeline's status
#    even though grep matched. Capture output first, then grep the capture.)
LISTING=$(unzip -l "$WORK/assets/chaquopy/app.imy" 2>/dev/null || true)
if grep -q "resources/bundled_credentials.json" <<<"$LISTING"; then
  note "OK   bundled OAuth client present"
else
  note "WARN no bundled OAuth client — this APK is paste-only"
fi

# 2. Nothing personal. A reader of the README is asked to trust this.
TOKENFILES=$(find "$WORK" -name "token_*.json" || true)
if [ -n "$TOKENFILES" ]; then
  bad "token files are bundled in the APK"
else
  note "OK   no token files"
fi

# 3 and 4 only apply to a release build; a debug fallback legitimately fails
#   both, and gating them on "is this a tag" would skip them for a
#   release-signed build that happens not to be tagged.
if [ "$SIGNING" = release ]; then
  # aapt2 is rarely on PATH; Briefcase already downloaded one alongside
  # apksigner when it built the Android SDK, so look there before giving up.
  AAPT2=$(command -v aapt2 2>/dev/null || true)
  if [ -z "$AAPT2" ]; then
    AAPT2=$(ls ~/Library/Caches/org.beeware.briefcase/tools/android_sdk/build-tools/*/aapt2 \
            ~/.cache/briefcase/tools/android_sdk/build-tools/*/aapt2 2>/dev/null | head -1 || true)
  fi
  if [ -n "$AAPT2" ]; then
    BADGING=$("$AAPT2" dump badging "$APK" 2>/dev/null || true)
    if grep -q "application-debuggable" <<<"$BADGING"; then
      bad "release APK is debuggable — adb run-as could read users' tokens"
    else
      note "OK   not debuggable"
    fi
  else
    note "SKIP debuggable check (aapt2 not found on PATH or under the Briefcase Android SDK)"
  fi

  APKSIGNER=$(ls ~/Library/Caches/org.beeware.briefcase/tools/android_sdk/build-tools/*/apksigner \
              ~/.cache/briefcase/tools/android_sdk/build-tools/*/apksigner 2>/dev/null | head -1 || true)
  if [ -n "$APKSIGNER" ]; then
    CERTS=$("$APKSIGNER" verify --print-certs "$APK" 2>/dev/null || true)
    if grep -qi "CN=Android Debug" <<<"$CERTS"; then
      bad "release APK carries the universal Android debug key"
    else
      note "OK   not signed with the debug key"
    fi
  else
    note "SKIP signing-key check (apksigner not found)"
  fi
else
  note "SKIP release-only checks (debug build)"
fi

[ "$fail" -eq 0 ] || { echo "verification FAILED"; exit 1; }
echo "verification passed"
