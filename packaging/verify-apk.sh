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
# This script verifies the artifact it is handed. dist/apk-signing.txt is
# written only after build-android.sh finishes copying the APK, so a build
# that fails partway can leave the marker and APK from a previous, unrelated
# run in place. A pass here says the given APK satisfies these four checks —
# it is not proof that a build just succeeded.
#
# "verification passed" always names how many of the four checks actually
# ran (2 for a legitimate debug build, 4 for a release one). A check that
# could not run — a missing tool, a missing marker, a poisoned pipe — is
# never silently folded into a pass: "I could not check" and "I checked and
# it was fine" must never produce the same outcome, and a release build that
# ran fewer than all four checks fails rather than passing quietly short.
#
# Usage: packaging/verify-apk.sh dist/OurCal-1.0.1-android.apk
set -euo pipefail

APK=${1:?usage: verify-apk.sh <apk>}
[ -f "$APK" ] || { echo "no such APK: $APK"; exit 1; }
ROOT=$(cd "$(dirname "$0")/.." && pwd)
SIGNING=$(cat "$ROOT/dist/apk-signing.txt" 2>/dev/null || echo unknown)
echo "verifying $(basename "$APK")  [$SIGNING-signed]"

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
# Guarded rather than left to `set -e`: an unguarded failure here would abort
# with unzip's raw stderr and no indication of which APK or what was being
# attempted — the "verifying ..." line above at least gives that much before
# a corrupt/truncated APK is reported with context.
UNZIP_ERR=$(unzip -q -o "$APK" -d "$WORK" 2>&1) || {
  echo "  FAIL: cannot extract $APK — is it a valid APK/zip?"
  [ -n "$UNZIP_ERR" ] && echo "  $UNZIP_ERR"
  exit 1
}
fail=0
ran=0
note() { echo "  $1"; }
bad()  { echo "  FAIL: $1"; fail=1; }

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
elif [ "$SIGNING" = debug ]; then
  note "WARN no bundled OAuth client — this APK is paste-only"
else
  # dist/apk-oauth-client.txt only records whether the secret was non-empty
  # when the build ran — it is a proxy for what CI passed in, not proof of
  # what actually reached the artifact. This check inspects the artifact
  # itself, so on a release (or unverifiable-marker) build it is the one
  # that gets to fail the pipeline, not just warn: a release must not ship
  # paste-only silently because bundling failed after the secret was set.
  bad "no bundled OAuth client in a $SIGNING build — a release APK must not ship paste-only unverified"
fi
ran=$((ran+1))

# 2. Nothing personal — no token_*.json anywhere a user's refresh token could
#    ship. Token files land in packaging/android/src/ourcal/resources/,
#    which — exactly like the OAuth client above — is packed inside
#    Chaquopy's nested app.imy, not the extracted tree. Searching only the
#    extracted tree would reproduce the exact false negative check 1 exists
#    to avoid, so this greps $LISTING (already read for check 1) as well.
TOKENFILES=$(find "$WORK" -name "token_*.json" 2>/dev/null || true)
if [ -n "$TOKENFILES" ] || grep -q "token_.*\.json" <<<"$LISTING"; then
  bad "token files are bundled in the APK"
else
  note "OK   no token files"
fi
ran=$((ran+1))

# 3 and 4 only apply to a release build. A debug fallback legitimately
#   skips both, and gating them on "is this a tag" would skip them for a
#   release-signed build that happens not to be tagged. A missing or
#   unreadable marker (SIGNING=unknown) is treated the same as release here,
#   not as debug: refusing to verify is safer than silently asserting a pass
#   nobody earned.
if [ "$SIGNING" != debug ]; then
  # aapt2 is rarely on PATH; Briefcase already downloaded one alongside
  # apksigner when it built the Android SDK, so look there before giving up.
  # ANDROID_HOME covers a machine that points at its own SDK instead of
  # Briefcase's cache. `|| true` matters here for the same reason it does in
  # build-android.sh: under `set -o pipefail`, `find` exits nonzero on
  # whichever of these start paths does not exist on this OS, and that
  # would otherwise kill the script at the assignment.
  AAPT2=$(command -v aapt2 2>/dev/null || true)
  if [ -z "$AAPT2" ]; then
    AAPT2=$(find ~/Library/Caches/org.beeware.briefcase/tools/android_sdk/build-tools \
                 ~/.cache/briefcase/tools/android_sdk/build-tools \
                 "${ANDROID_HOME:-/nonexistent}/build-tools" \
            -name aapt2 2>/dev/null | sort -V | tail -1) || true
  fi
  if [ -n "$AAPT2" ]; then
    BADGING=$("$AAPT2" dump badging "$APK" 2>/dev/null || true)
    if grep -q "application-debuggable" <<<"$BADGING"; then
      bad "release APK is debuggable — adb run-as could read users' tokens"
    else
      note "OK   not debuggable"
    fi
    ran=$((ran+1))
  else
    # A release build with no way to check this is not a pass — it is an
    # unverified release. Skipping quietly here would let "verification
    # passed" mean "the two checks that matter never ran." Not counted in
    # $ran either: a missing tool means we never actually inspected the
    # artifact, so this must not read as a check that ran.
    bad "cannot verify debuggable flag — aapt2 not found, and a release APK must not go out unverified"
  fi

  APKSIGNER=$(find ~/Library/Caches/org.beeware.briefcase/tools/android_sdk/build-tools \
                   ~/.cache/briefcase/tools/android_sdk/build-tools \
                   "${ANDROID_HOME:-/nonexistent}/build-tools" \
              -name apksigner 2>/dev/null | sort -V | tail -1) || true
  if [ -n "$APKSIGNER" ]; then
    CERTS=$("$APKSIGNER" verify --print-certs "$APK" 2>/dev/null || true)
    if grep -qi "CN=Android Debug" <<<"$CERTS"; then
      bad "release APK carries the universal Android debug key"
    else
      note "OK   not signed with the debug key"
    fi
    ran=$((ran+1))
  else
    bad "cannot verify signing key — apksigner not found, and a release APK must not go out unverified"
  fi
else
  note "SKIP release-only checks ($SIGNING build)"
fi

# The structural guard, not just the per-check ones above: fail=0 must never
# mean both "passed" and "only some checks could run." A release (or
# unverifiable-marker) build that did not actually run all four checks is a
# failure regardless of which individual bad() calls did or didn't fire.
if [ "$SIGNING" != debug ] && [ "$ran" -lt 4 ]; then
  bad "only $ran of 4 checks actually ran for a $SIGNING build — refusing to call this verified"
fi

[ "$fail" -eq 0 ] || { echo "verification FAILED"; exit 1; }
echo "verification passed ($ran checks ran)"
