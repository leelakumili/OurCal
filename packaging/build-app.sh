#!/bin/bash
# Build OurCal.app and a drag-to-install .dmg.
#
#   ./packaging/build-app.sh
#
# Runs the same way locally and in CI, so a release can never be built by a
# path nobody has exercised. Output lands in dist/.
#
# Signing: if APPLE_SIGN_ID is set, the app is signed with it (a Developer ID
# for a release, or "-" for ad-hoc). Unset, the app is still ad-hoc signed —
# arm64 binaries must carry at least an ad-hoc signature or macOS refuses to
# run them at all, which is a different failure from the Gatekeeper prompt.
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT=$PWD
BUILD=$ROOT/build/pkg
DIST=$ROOT/dist
VERSION=$(python3 -c "import re;print(re.search(r'^VERSION = \"(.*)\"', open('ourcal.py').read(), re.M).group(1))")
ARCH=$(uname -m)
echo "OurCal $VERSION ($ARCH)"

# ── pick an interpreter ─────────────────────────────────────────────────
# 3.9 works but makes Google's libraries print end-of-life warnings, and
# PyInstaller support for it is winding down.
PY=""
for c in python3.13 python3.12 python3.11 python3.10; do
  command -v "$c" >/dev/null 2>&1 && { PY=$(command -v "$c"); break; }
done
[ -n "$PY" ] || { echo "need Python 3.10+ to build (found only $(python3 -V))"; exit 1; }
echo "building with $PY ($("$PY" -V))"

# Remove only this script's own outputs — its build tree, the .app, and any
# .dmg it previously produced — never the whole dist/ directory. build-android.sh
# writes dist/OurCal-<version>-android.apk, and a macOS build must not delete an
# Android artifact that happens to share the directory; a full "rm -rf $DIST"
# did exactly that.
rm -rf "$BUILD" "$DIST/OurCal.app"
rm -f "$DIST"/OurCal-*.dmg
mkdir -p "$BUILD" "$DIST"

# ── icon ────────────────────────────────────────────────────────────────
# Rendered from packaging/icon.html so the source of truth is editable text,
# not a binary nobody can diff.
ICONSET=$BUILD/OurCal.iconset
mkdir -p "$ICONSET"
SRC=$ROOT/packaging/icon-1024.png
[ -f "$SRC" ] || { echo "missing $SRC — regenerate it from packaging/icon.html"; exit 1; }
for sz in 16 32 128 256 512; do
  sips -z $sz $sz         "$SRC" --out "$ICONSET/icon_${sz}x${sz}.png"     >/dev/null
  sips -z $((sz*2)) $((sz*2)) "$SRC" --out "$ICONSET/icon_${sz}x${sz}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$BUILD/OurCal.icns"
echo "icon: $(du -h "$BUILD/OurCal.icns" | cut -f1)"

# ── build env ───────────────────────────────────────────────────────────
VENV=$BUILD/venv
"$PY" -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q pyinstaller google-api-python-client \
  google-auth-oauthlib pyobjc-framework-Cocoa pyobjc-framework-WebKit

# ── .app ────────────────────────────────────────────────────────────────
"$VENV/bin/pyinstaller" \
  --name OurCal --windowed --noconfirm --clean \
  --icon "$BUILD/OurCal.icns" \
  --osx-bundle-identifier com.leelakumili.ourcal \
  --distpath "$DIST" --workpath "$BUILD/work" --specpath "$BUILD" \
  --hidden-import googleapiclient --hidden-import google_auth_oauthlib \
  --hidden-import googleapiclient.discovery \
  --hidden-import google.auth.transport.requests \
  --collect-data googleapiclient \
  ourcal.py

APP=$DIST/OurCal.app
PLIST=$APP/Contents/Info.plist
# Set-or-add: PyInstaller emits some of these keys but not all, and which ones
# varies by version — assuming either way breaks the build on an upgrade.
plist_put() {   # plist_put KEY TYPE VALUE
  /usr/libexec/PlistBuddy -c "Set :$1 $3" "$PLIST" 2>/dev/null \
    || /usr/libexec/PlistBuddy -c "Add :$1 $2 $3" "$PLIST"
}
plist_put CFBundleShortVersionString string "$VERSION"
plist_put CFBundleVersion            string "$VERSION"
# Without this the OAuth sign-in browser hand-off can be blocked on modern macOS.
/usr/libexec/PlistBuddy -c "Add :NSAppTransportSecurity dict" "$PLIST" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :NSAppTransportSecurity:NSAllowsLocalNetworking bool true" "$PLIST" 2>/dev/null || true

codesign --force --deep --sign "${APPLE_SIGN_ID:--}" "$APP"
codesign --verify --deep --strict "$APP" && echo "signature: ok (${APPLE_SIGN_ID:-ad-hoc})"

# ── .dmg ────────────────────────────────────────────────────────────────
STAGE=$BUILD/dmg
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"      # the drag-to-install target
DMG=$DIST/OurCal-$VERSION-$ARCH.dmg
hdiutil create -volname "OurCal $VERSION" -srcfolder "$STAGE" \
  -ov -format UDZO "$DMG" >/dev/null

echo
echo "built: $DMG ($(du -h "$DMG" | cut -f1))"
echo "       $APP ($(du -sh "$APP" | cut -f1))"
