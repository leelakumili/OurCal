# Android release workflow — design

**Date:** 2026-08-09 · **Status:** approved, not yet implemented

## The problem

`.github/workflows/release.yml` builds and publishes the `.dmg` only. There is
no Android job, so the Releases page carries no APK and the only way to get one
is to clone the repo and run `packaging/build-android.sh` — which needs a JDK,
the Android SDK and Gradle, several GB on first run.

`README.md` already says as much, honestly. But it means the on-device sign-in
built in the previous cycle reaches nobody: the feature exists, and there is no
artifact carrying it.

The goal is that someone can download an APK from GitHub Releases, sideload it,
and sign in — with no clone, no toolchain, and no computer.

## Decisions

| Decision | Chosen | Rejected |
|---|---|---|
| APK signing | Release keystore from secrets, **falling back to debug when absent** | Debug-signed always; release-signing required (a fork could not build) |
| Job shape | Two build jobs → one publish job | Android `needs: macos` (a failed Android build leaves a published `.dmg`-only release); one job building both (macOS runners bill ~10× ubuntu) |
| Android runner | `ubuntu-latest` | `macos-14` — cost, and no benefit |
| APK verification | Four cheap assertions on the artifact | An emulator smoke test — 5–10 minutes, and the suite already covers behaviour |
| OAuth client | Written from a secret so the released APK carries it | Paste-only APK — would make the previous cycle's sign-in unusable by a downloader |

### Why the fallback, and why it must not reach a tag

`packaging/build-app.sh` already degrades this way for `APPLE_SIGN_ID`: absent,
it ad-hoc signs and still produces a runnable app. Mirroring that keeps forks
and pull requests building.

But a debug-signed APK is signed with the keystore every Android SDK ships
(password `android`), so anyone can build `com.leelakumili.ourcal`, sign it with
the same key, and have Android install it **as an update over a user's copy** —
inheriting the app's private directory, which by then holds that user's OAuth
credentials and refresh tokens. Fine on your own phone; unacceptable as a public
download.

So the fallback exists for non-tag builds only. **On a tag, the release job
fails if the APK is not release-signed**, and fails if the OAuth client secret
was absent. The degradation can never quietly ship to strangers.

---

## Part 1 — Job shape

```
macos (macos-14)              android (ubuntu-latest)
  checkout                      checkout
  setup-python 3.11             setup-python 3.11
  version consistency           version consistency
  test suite                    test suite
  build .app + .dmg             restore caches
  verify it serves              write credentials.json from secret
  upload-artifact               write keystore from secret
       │                        build + sign the APK
       │                        verify the APK (Part 4)
       │                        remove the keystore  [always()]
       │                        upload-artifact
       └───────────┬────────────┘
                   ▼
        release (needs: [macos, android])
          download both artifacts
          gate: on a tag, refuse a debug-signed APK
          create ONE release carrying .dmg + .apk
```

Nothing is published unless both builds succeed, so a version can never appear
on the Releases page carrying only one platform. If Android fails, there is no
release to clean up — fix and re-run.

Both build jobs keep the existing version-consistency check and run the suite,
because each publishes an artifact and neither should ship a build whose tests
were not run on the machine that made it.

### Caching

`NOTES-android.md:74-77` records that the first Android build pulls a JDK, the
Android SDK and Gradle — several GB and several minutes — and that CI must cache
them or every release pays it again.

Two `actions/cache` entries:
- `~/.gradle/caches` and `~/.gradle/wrapper`, keyed on `packaging/android/pyproject.toml`
- `~/.cache/briefcase`, Briefcase's tool directory on Linux, keyed on `packaging/build-android.sh`

Both take a `restore-keys` prefix so a changed key still warm-starts from the
previous cache rather than downloading everything again.

---

## Part 2 — Signing

Four repository secrets:

| Secret | Contents |
|---|---|
| `ANDROID_KEYSTORE_B64` | `base64 -i ourcal.jks` |
| `ANDROID_KEYSTORE_PASSWORD` | the store password |
| `ANDROID_KEY_ALIAS` | the key alias |
| `ANDROID_KEY_PASSWORD` | the key password |

Generated once:

```bash
keytool -genkeypair -v -keystore ourcal.jks -alias ourcal \
        -keyalg RSA -keysize 4096 -validity 10000
base64 -i ourcal.jks | pbcopy
```

**`ourcal.jks` must be backed up somewhere durable and must never be committed.**
Losing it means no future APK can ever install as an update over an existing one
— every user would have to uninstall and lose their local setup. This is the
single unrecoverable mistake available in this design, and the spec says so
because a warning buried in a workflow comment is a warning nobody reads.

The workflow decodes the keystore to a path **outside the working tree**
(`$RUNNER_TEMP`), passes the four values to Briefcase's Android packaging, and
removes the file in an `if: always()` step so a failed build does not leave it on
the runner.

`packaging/build-android.sh` gains an optional signing path: when
`ANDROID_KEYSTORE_PATH` and its companions are set in the environment it produces
a release-signed APK; unset, it behaves exactly as today and produces
`app-debug.apk`. Local builds are unaffected.

---

## Part 3 — The OAuth client

`ANDROID_OAUTH_CLIENT` holds the contents of `credentials.json`. The Android job
writes it to the repository root before building; `build-android.sh:33` already
copies it into the app's resources from there.

Without it the APK is paste-only, and the accounts editor and on-device sign-in
built in the previous cycle are unreachable for anyone who downloads it — the
app would install, open, and offer a setup screen asking for a bundle the user
cannot produce. **On a tag that is a build failure, not a warning.**

The client is written to a path the build already ignores, and the workflow does
not echo it. `credentials.json` stays git-ignored.

---

## Part 4 — What the workflow verifies about the APK

No emulator. It costs five to ten minutes, and the test suite already covers
behaviour. Four assertions instead, each corresponding to a failure this project
has actually experienced:

1. **The bundled client is inside `assets/chaquopy/app.imy`.** Chaquopy packs app
   files into a nested archive, so grepping the extracted APK gives a false
   negative — that happened during the previous cycle and briefly read as "the
   client was not bundled".
2. **The APK contains no `token_*.json`** and none of the author's addresses.
   Cheap, and it is the property a reader of the README is being asked to trust.
3. **`android:debuggable` is false** on a release build. A debuggable APK lets
   `adb shell run-as` read the app's private files, which is where users' refresh
   tokens live.
4. **The signing certificate is not the Android debug key.** This is what the
   tag gate depends on, so it is asserted rather than assumed.

Assertions 1 and 2 always run. Assertions 3 and 4 are conditioned on **whether
the build was release-signed**, not on whether it is a tag: a debug fallback
legitimately fails both, and a release-signed build must pass both even when it
is not a tag. The build step records which path it took, and the verification
step reads that rather than re-deriving it — so the two can never disagree about
what was built.

---

## Part 5 — Release notes

The existing body keeps its macOS half unchanged and gains an Android half:

- Download the `.apk` and open it on the phone.
- Play Protect will say *"App blocked to protect your device"* — **Install
  anyway** is the way past. It appears because the app is sideloaded, not
  because anything is wrong with it, and signing does not remove it.
- Open OurCal → **Set up this device** → add an account (a name and your Gmail
  address) → **Sign in**.
- Google will show *"Google hasn't verified this app"* — **Advanced → Go to
  OurCal**. The consent screen is published but unverified.
- After granting, **return to OurCal**: Android cuts a backgrounded app's
  network, and the token exchange waits for you to come back.

---

## Error handling

| Condition | Behaviour |
|---|---|
| Keystore secrets absent, non-tag build | Debug APK, build succeeds |
| Keystore secrets absent, tag build | Release job fails before publishing |
| OAuth client secret absent, non-tag build | Paste-only APK, build succeeds, logged plainly |
| OAuth client secret absent, tag build | Release job fails before publishing |
| Android build fails | No release created; the `.dmg` artifact is discarded with it |
| macOS build fails | No release created |
| Version mismatch in either job | That job fails, as today |
| Keystore left on the runner | Removed in an `always()` step |

---

## Testing

The workflow itself cannot be unit-tested, so verification is by execution:

- `workflow_dispatch` without a tag, with no secrets configured: both jobs build,
  the APK is debug-signed, verification assertions 1 and 2 pass, 3 and 4 skip,
  and a draft release carries both artifacts.
- The same with secrets configured: the APK is release-signed and all four
  assertions pass.
- A tag build with the keystore secret removed: the release job fails and nothing
  is published.
- `packaging/build-android.sh` unchanged locally: with no signing environment set
  it still produces `app-debug.apk` exactly as before. This is worth a local run,
  because the script is the one piece shared between CI and a developer's machine.

---

## Out of scope

- Google OAuth verification, a domain, or a privacy policy.
- Switching to an Android-type OAuth client.
- Play Store distribution.
- An emulator or device farm test.
- Reproducible builds.
