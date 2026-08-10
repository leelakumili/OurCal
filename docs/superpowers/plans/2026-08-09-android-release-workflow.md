# Android Release Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish a release-signed Android APK to GitHub Releases alongside the `.dmg`, so someone can download and sideload OurCal without cloning the repo or installing a toolchain.

**Architecture:** Two build jobs on their own runners feed one publish job, so a version can never appear carrying only one platform. Signing and the bundled OAuth client both come from repository secrets and both degrade to a working-but-unpublishable build when absent. The two pieces that can be tested on a laptop — the signing path in `build-android.sh` and the APK verification script — are built first and separately, because the workflow itself cannot be run without pushing.

**Tech Stack:** GitHub Actions, Briefcase/Chaquopy, `keytool`/`apksigner` from the Android SDK, bash.

**Spec:** `docs/superpowers/specs/2026-08-09-android-release-workflow-design.md`

## Global Constraints

- **`credentials.json` and any keystore must never be committed.** Both are secrets. Before every commit run `git status --short` and confirm neither appears, and that no real `credentials.json`, `accounts.json` or `token_*.json` is modified.
- **`packaging/build-android.sh` must behave exactly as it does today when no signing environment is set.** It is the one file shared between CI and a developer's laptop; a local build must not start demanding secrets.
- **Never invent a command-line flag.** Where this plan says to discover a tool's real interface, run the command and use what it prints. Record the discovered flags in the commit message.
- The Python suite (`python3 -m unittest discover tests -q`, 348 passing, 1 skipped) must stay green; none of this work touches `ourcal.py`.
- Work on branch `android-release-workflow`, which already exists and holds the spec.
- Secrets this design expects, all optional at build time and required on a tag: `ANDROID_KEYSTORE_B64`, `ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS`, `ANDROID_KEY_PASSWORD`, `ANDROID_OAUTH_CLIENT`.

---

### Task 1: Optional release signing in `build-android.sh`

Locally testable, and the only part of this feature that can be proven correct without pushing.

**Files:**
- Modify: `packaging/build-android.sh` (the build block at lines 62-74)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `build-android.sh` honours four optional environment variables — `ANDROID_KEYSTORE_PATH`, `ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS`, `ANDROID_KEY_PASSWORD`. With all four set it emits a **release-signed** APK; with any absent it emits `app-debug.apk` exactly as today. It also writes a one-line marker file `dist/.apk-signing` containing either `release` or `debug`, which Task 2's verifier and Task 3's CI job both read rather than re-deriving.

- [ ] **Step 1: Discover the real signing interface — do not guess**

The build venv already exists at `packaging/android/.build-venv`. Run:

```bash
packaging/android/.build-venv/bin/briefcase package android --help
```

Read the output and note which flags it actually offers for non-interactive signing (look for keystore, alias and password options, and for `-p`/`--packaging-format`). If the venv is absent, create it the way the script does: `python3 -m venv packaging/android/.build-venv && packaging/android/.build-venv/bin/pip install -q --upgrade pip briefcase`.

Also check what the Android SDK's `apksigner` offers, since it is the fallback if Briefcase's own signing cannot be driven non-interactively:

```bash
ls ~/Library/Caches/org.beeware.briefcase/tools/android_sdk/build-tools/*/apksigner
```

**Record both findings in your report before writing any code.** If `briefcase package android` can be driven non-interactively, prefer it — it is the tool that already owns this build. If it cannot, sign the release APK with `apksigner` instead. State which you chose and why.

- [ ] **Step 2: Write the signing block**

Replace the build block in `packaging/build-android.sh`. The shape below is the structure; substitute the real flags you discovered in Step 1 for the marked line.

```bash
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
if [ -n "${ANDROID_KEYSTORE_PATH:-}" ] && [ -n "${ANDROID_KEYSTORE_PASSWORD:-}" ] \
   && [ -n "${ANDROID_KEY_ALIAS:-}" ] && [ -n "${ANDROID_KEY_PASSWORD:-}" ]; then
  [ -f "$ANDROID_KEYSTORE_PATH" ] || {
    echo "ANDROID_KEYSTORE_PATH is set but $ANDROID_KEYSTORE_PATH does not exist"; exit 1; }
  SIGNING=release
  echo "signing with the release keystore"
  # <-- the command you discovered in Step 1 goes here
else
  echo "no release keystore in the environment — building a debug APK"
  "$VENV/bin/briefcase" build android --no-input
fi
```

Then replace the APK-collection block so it finds whichever variant was built, and records which:

```bash
if [ "$SIGNING" = release ]; then
  APK=$(find "$ANDROID/build" -name "*-release*.apk" | head -1)
else
  APK=$(find "$ANDROID/build" -name "app-debug.apk" | head -1)
fi
[ -n "$APK" ] || { echo "no APK produced"; exit 1; }
mkdir -p "$DIST"
OUT=$DIST/OurCal-$VERSION-android.apk
cp "$APK" "$OUT"
# Which path was taken, for the verifier and the release gate to read rather
# than re-derive. Two places deciding this independently could disagree.
echo "$SIGNING" > "$DIST/.apk-signing"
echo
echo "built: $OUT ($(du -h "$OUT" | cut -f1))  [$SIGNING-signed]"
```

- [ ] **Step 3: Prove the default path is unchanged**

With no signing environment set:

```bash
./packaging/build-android.sh 2>&1 | tail -5
cat dist/.apk-signing
```

Expected: builds as before, reports `[debug-signed]`, and `dist/.apk-signing` contains `debug`. **This is the regression check that matters most** — a developer with no secrets must be unaffected.

- [ ] **Step 4: Prove the signing path works, with a throwaway keystore**

Generate a keystore in a temp directory — **never in the repo**:

```bash
KS=$(mktemp -d)/test.jks
keytool -genkeypair -v -keystore "$KS" -alias testkey -keyalg RSA -keysize 2048 \
        -validity 30 -storepass testpass -keypass testpass \
        -dname "CN=OurCal Test, OU=Dev, O=Test, L=NA, S=NA, C=US"
ANDROID_KEYSTORE_PATH="$KS" ANDROID_KEYSTORE_PASSWORD=testpass \
ANDROID_KEY_ALIAS=testkey ANDROID_KEY_PASSWORD=testpass \
  ./packaging/build-android.sh 2>&1 | tail -5
cat dist/.apk-signing
```

Expected: reports `[release-signed]` and `dist/.apk-signing` contains `release`.

Then confirm the signature is genuinely not the debug key — find `apksigner` under the Briefcase SDK path from Step 1 and run:

```bash
<apksigner> verify --print-certs dist/OurCal-*-android.apk | head -20
```

Expected: the certificate DN is your test one (`CN=OurCal Test`), **not** `CN=Android Debug`. Record the output in your report. Then delete the temp keystore.

- [ ] **Step 5: Commit**

```bash
git add packaging/build-android.sh
git commit -m "Sign the APK with a release keystore when one is in the environment

Opt-in through four environment variables, absent by default. This script
is the one piece shared between CI and a laptop, and CI is the only caller
with a keystore — a local build must not start demanding secrets, so with
any of the four missing it produces the same app-debug.apk it always has.

It also records which path it took in dist/.apk-signing, so the verifier
and the release gate read one answer instead of each re-deriving it and
risking disagreement.

Signing invoked via: <the command discovered in Step 1>"
```

---

### Task 2: The APK verification script

Also locally testable, and reusable by CI. Four assertions, each one a failure this project has actually had.

**Files:**
- Create: `packaging/verify-apk.sh`

**Interfaces:**
- Consumes: `dist/.apk-signing` from Task 1.
- Produces: `packaging/verify-apk.sh <apk-path>` — exits 0 when every applicable assertion passes, non-zero with a named failure otherwise. Task 3's CI job calls it.

- [ ] **Step 1: Write the script**

```bash
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
#    negative already fooled us once.
if unzip -l "$WORK/assets/chaquopy/app.imy" 2>/dev/null \
     | grep -q "resources/bundled_credentials.json"; then
  note "OK   bundled OAuth client present"
else
  note "WARN no bundled OAuth client — this APK is paste-only"
fi

# 2. Nothing personal. A reader of the README is asked to trust this.
if find "$WORK" -name "token_*.json" | grep -q .; then
  bad "token files are bundled in the APK"
else
  note "OK   no token files"
fi

# 3 and 4 only apply to a release build; a debug fallback legitimately fails
#   both, and gating them on "is this a tag" would skip them for a
#   release-signed build that happens not to be tagged.
if [ "$SIGNING" = release ]; then
  if command -v aapt2 >/dev/null 2>&1; then
    if aapt2 dump badging "$APK" 2>/dev/null | grep -q "application-debuggable"; then
      bad "release APK is debuggable — adb run-as could read users' tokens"
    else
      note "OK   not debuggable"
    fi
  else
    note "SKIP debuggable check (aapt2 not on PATH)"
  fi

  APKSIGNER=$(ls ~/Library/Caches/org.beeware.briefcase/tools/android_sdk/build-tools/*/apksigner \
              ~/.cache/briefcase/tools/android_sdk/build-tools/*/apksigner 2>/dev/null | head -1 || true)
  if [ -n "$APKSIGNER" ]; then
    if "$APKSIGNER" verify --print-certs "$APK" 2>/dev/null | grep -qi "CN=Android Debug"; then
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
```

- [ ] **Step 2: Make it executable and run it against the debug APK**

```bash
chmod +x packaging/verify-apk.sh
./packaging/verify-apk.sh dist/OurCal-1.0.1-android.apk
```

Expected: passes, reports the bundled client present, no token files, and skips the release-only checks.

- [ ] **Step 3: Prove it actually fails when it should**

The checks are worthless if they cannot fail. Force each:

```bash
# a bogus signing marker turns on the release-only checks against a debug APK
echo release > dist/apk-signing.txt
./packaging/verify-apk.sh dist/OurCal-1.0.1-android.apk; echo "exit: $?"
```

Expected: **fails**, naming the debug key. Restore `dist/apk-signing.txt` to `debug` afterwards and confirm it passes again. Record both outputs in your report.

(Marker renamed from `dist/.apk-signing` to `dist/apk-signing.txt` in a later
fix wave — `actions/upload-artifact@v4` excludes dotfiles by default, which
silently dropped the original name from the CI artifact. Written here with
the current name so this step still exercises what it claims to.)

- [ ] **Step 4: Commit**

```bash
git add packaging/verify-apk.sh
git commit -m "Add an APK verifier for the things tests cannot cover

Four assertions on the packaged artifact, each one a failure this project
has actually had: the OAuth client not reaching the APK, something
personal reaching it, a debuggable release build that would let adb read
users' refresh tokens, and a release carrying the universal debug key.

The client check looks inside Chaquopy's nested app.imy, because grepping
the extracted tree reports 'not bundled' even when it is — that false
negative already fooled us once.

Release-only checks key off dist/.apk-signing rather than off whether this
is a tag, so a release-signed build is verified whenever it is produced."
```

---

### Task 3: Restructure `release.yml` into three jobs

**Files:**
- Modify: `.github/workflows/release.yml`

**Interfaces:**
- Consumes: `packaging/build-android.sh`'s signing environment (Task 1), `packaging/verify-apk.sh` (Task 2).
- Produces: jobs `macos`, `android`, `release`. The first two upload artifacts named `dmg` and `apk`; `release` downloads both and publishes once.

- [ ] **Step 1: Turn the existing `macos` job into an artifact producer**

Remove the `softprops/action-gh-release@v2` step from the `macos` job and replace it with:

```yaml
      - uses: actions/upload-artifact@v4
        with:
          name: dmg
          path: dist/*.dmg
          if-no-files-found: error
```

Keep everything else in that job exactly as it is — the version check, the suite, the build, and the serve check with its long explanatory comment. Move the `id: version` step's output into a job-level output so the `release` job can read it:

```yaml
  macos:
    runs-on: macos-14
    outputs:
      version: ${{ steps.version.outputs.version }}
```

- [ ] **Step 2: Add the `android` job**

```yaml
  android:
    # ubuntu, not macos-14: the Android build needs a JDK (preinstalled here)
    # and bills about a tenth of a macOS runner for a job that takes minutes
    # even with warm caches.
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Check the version is consistent
        run: |
          FILE=$(python3 -c "import re;print(re.search(r'^VERSION = \"(.*)\"', open('ourcal.py').read(), re.M).group(1))")
          PYPROJ=$(python3 -c "import re;print(re.search(r'^version = \"(.*)\"', open('packaging/android/pyproject.toml').read(), re.M).group(1))")
          [ "$FILE" = "$PYPROJ" ] || { echo "::error::ourcal.py $FILE != pyproject $PYPROJ"; exit 1; }
          if [ "${{ github.ref_type }}" = "tag" ]; then
            TAG="${GITHUB_REF_NAME#v}"
            [ "$TAG" = "$FILE" ] || { echo "::error::tag v$TAG != VERSION $FILE"; exit 1; }
          fi

      - name: Run the test suite
        # Never ship a build whose tests were not run on the machine that built it.
        run: python3 -m unittest discover -s tests -q

      # NOTES-android.md:74-77 — the first build pulls a JDK, the Android SDK
      # and Gradle, several GB and several minutes. Without these caches every
      # release pays that again.
      - uses: actions/cache@v4
        with:
          path: |
            ~/.gradle/caches
            ~/.gradle/wrapper
          key: gradle-${{ hashFiles('packaging/android/pyproject.toml') }}
          restore-keys: gradle-

      - uses: actions/cache@v4
        with:
          path: ~/.cache/briefcase
          key: briefcase-${{ hashFiles('packaging/build-android.sh') }}
          restore-keys: briefcase-

      - name: Write the OAuth client
        # Without it the APK is paste-only and the on-device sign-in is
        # unreachable for anyone who downloads it. Absent here it still
        # builds; the release job is what refuses to publish one on a tag.
        env:
          CLIENT: ${{ secrets.ANDROID_OAUTH_CLIENT }}
        run: |
          if [ -n "$CLIENT" ]; then
            printf '%s' "$CLIENT" > credentials.json
            echo "oauth client written"
          else
            echo "no ANDROID_OAUTH_CLIENT — this APK will be paste-only"
          fi

      - name: Write the release keystore
        env:
          KS_B64: ${{ secrets.ANDROID_KEYSTORE_B64 }}
        run: |
          if [ -n "$KS_B64" ]; then
            printf '%s' "$KS_B64" | base64 -d > "$RUNNER_TEMP/ourcal.jks"
            echo "ANDROID_KEYSTORE_PATH=$RUNNER_TEMP/ourcal.jks" >> "$GITHUB_ENV"
            echo "keystore written"
          else
            echo "no ANDROID_KEYSTORE_B64 — this APK will be debug-signed"
          fi

      - name: Build the APK
        env:
          ANDROID_KEYSTORE_PASSWORD: ${{ secrets.ANDROID_KEYSTORE_PASSWORD }}
          ANDROID_KEY_ALIAS: ${{ secrets.ANDROID_KEY_ALIAS }}
          ANDROID_KEY_PASSWORD: ${{ secrets.ANDROID_KEY_PASSWORD }}
        run: ./packaging/build-android.sh

      - name: Verify the APK
        run: ./packaging/verify-apk.sh dist/OurCal-*-android.apk

      - name: Remove the keystore
        if: always()
        run: rm -f "$RUNNER_TEMP/ourcal.jks"

      - uses: actions/upload-artifact@v4
        with:
          name: apk
          path: |
            dist/*.apk
            dist/.apk-signing
          if-no-files-found: error
```

- [ ] **Step 3: Add the `release` job with its tag gate**

```yaml
  release:
    needs: [macos, android]
    runs-on: ubuntu-latest
    steps:
      - uses: actions/download-artifact@v4
        with: { name: dmg, path: dist }
      - uses: actions/download-artifact@v4
        with: { name: apk, path: dist }

      - name: Refuse to publish an unsigned or paste-only APK on a tag
        # The debug fallback exists so forks and untagged builds work. It must
        # never reach strangers: a debug APK is signed with the key every
        # Android SDK ships, so anyone could build one with this package name
        # and have Android install it as an update over a user's copy,
        # inheriting their credentials and refresh tokens.
        if: github.ref_type == 'tag'
        run: |
          [ "$(cat dist/.apk-signing)" = release ] || {
            echo "::error::tagged release but the APK is debug-signed — set the ANDROID_KEYSTORE_* secrets"; exit 1; }
          echo "release-signed APK confirmed"

      - uses: softprops/action-gh-release@v2
        with:
          tag_name: ${{ github.ref_type == 'tag' && github.ref_name || format('v{0}', needs.macos.outputs.version) }}
          name: OurCal ${{ needs.macos.outputs.version }}
          files: |
            dist/*.dmg
            dist/*.apk
          generate_release_notes: true
          body: |
            ... move the existing body across verbatim, unchanged ...
```

Move the release body from the old `macos` job into this step **exactly as it
is**, character for character. Task 4 appends the Android half; this task only
relocates what is already there, so a diff of this step should show a pure move.

- [ ] **Step 4: Validate the workflow parses**

GitHub Actions YAML errors surface only on push, so check locally first:

```bash
python3 -c "import yaml,sys; d=yaml.safe_load(open('.github/workflows/release.yml')); print('jobs:', list(d['jobs'])); print('release needs:', d['jobs']['release']['needs'])"
```

Expected: `jobs: ['macos', 'android', 'release']` and `release needs: ['macos', 'android']`. If `yaml` is unavailable, `pip install pyyaml` into a temp venv rather than skipping this — a YAML typo costs a full push-and-wait cycle to discover.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "Build both platforms, publish once

The workflow built only the .dmg, so the Releases page carried no APK and
the on-device sign-in shipped in the last cycle reached nobody.

Two build jobs feed one publish job rather than each publishing its own
file: if the Android build fails, there is no release to clean up. The
previous shape would have left a published, downloadable release carrying
only half the platforms.

Android builds on ubuntu — it has a JDK already and bills a tenth of a
macOS runner. Both Gradle and the Briefcase tool directory are cached;
without that every release re-downloads several GB.

The release job refuses to publish a debug-signed or paste-only APK on a
tag, so the fallbacks that keep forks building can never reach strangers."
```

---

### Task 4: Release notes and documentation

**Files:**
- Modify: `.github/workflows/release.yml` (the release body), `README.md`, `SETUP_GUIDE.md`

- [ ] **Step 1: Add the Android half of the release body**

Keep the existing macOS text unchanged and append:

```markdown
    ## Android

    1. Download the `.apk` below and open it on your phone.
    2. Play Protect will say **"App blocked to protect your device"** — tap
       **Install anyway**. It says that because the app is sideloaded rather
       than from the Play Store, not because anything is wrong with it.
    3. Open OurCal → **Set up this device** → add an account (a name and your
       Gmail address) → **Sign in**.
    4. Google shows **"Google hasn't verified this app"** — tap **Advanced**,
       then **Go to OurCal**. The consent screen is published but unverified.
    5. **Come back to OurCal after granting access.** Android cuts a
       backgrounded app's network, and the sign-in waits for you to return.

    Your calendar data and tokens stay on your phone.
```

- [ ] **Step 2: Correct the two docs that say there is no Android release**

`README.md` currently states there is no Android release job and that you build from source. That becomes false with this change. Find that passage (grep for `no release job`) and rewrite it to describe downloading the `.apk` from Releases, keeping the source-build instructions for anyone who wants them.

Check `SETUP_GUIDE.md` for the same claim and correct it too. Grep both files for `release job`, `build from source` and `sideload` and fix every hit that is now wrong — this repository has shipped self-contradicting docs twice, both times because one sentence was updated and its neighbour was not.

- [ ] **Step 3: Verify no contradictions remain**

Read both documents end to end and confirm nothing still says an Android release does not exist. Report what you checked.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/release.yml README.md SETUP_GUIDE.md
git commit -m "Document downloading the APK from Releases

The release body gains Android install steps: the Play Protect warning
and why it appears, the unverified-app screen and the way past it, and
the one non-obvious step — coming back to OurCal after granting, because
Android cuts a backgrounded app's network and the sign-in waits for it.

README and SETUP_GUIDE said there is no Android release job. True until
this branch; false now."
```

---

## Verification before calling this done

- [ ] `python3 -m unittest discover tests -q` — 348 pass, 1 skipped (unchanged; no Python was touched)
- [ ] `git status --short` shows no `credentials.json`, no `*.jks`, and no modified `accounts.json` or `token_*.json`
- [ ] `./packaging/build-android.sh` with no signing environment still produces a debug APK, exactly as before
- [ ] `./packaging/verify-apk.sh` passes on that APK, and **fails** when `dist/apk-signing.txt` is forced to `release`
- [ ] The workflow YAML parses and declares three jobs with the right dependencies

**Cannot be verified without pushing:** that the workflow actually runs. The
first real test is a `workflow_dispatch` run with no secrets set — both jobs
should build, the APK should be debug-signed, and the release job should publish
a non-tag release carrying both artifacts. Only then are the secrets worth
adding.
