# Android setup import — design

**Date:** 2026-08-07 · **Status:** approved, not yet implemented

## The problem

OurCal's Android build ships as a sideloaded APK. It needs six files to work:
`credentials.json`, `accounts.json`, and one `token_<slug>.json` per account
(four, currently). On the desktop you drop them in a folder. On a phone you
cannot: since Android 11, `Android/data/` is hidden from every file manager and
from MTP over USB. There is no supported way to hand a sideloaded app a file.

The app is therefore installed and useless — every account fails with
`credentials.json is missing`.

## What the phone actually revealed

A screenshot of the running app showed the error naming this directory:

```
/data/data/com.leelakumili.ourcal/files/chaquopy/Ass…
```

That is Chaquopy's `AssetFinder` extraction directory — it is `APP_DIR`, the
folder `core.py` was unpacked into. It is *not* `getExternalFilesDir()`. So
`is_android()` returned **False** on the device and `data_dir()` fell through to
the desktop-checkout branch (`ourcal.py:61-63`).

Three consequences:

1. Pushing files to `/sdcard/Android/data/com.leelakumili.ourcal/files/` — by
   `adb` or anything else — could never have worked. The app does not look there.
2. Every Android seam is dead on-device. `_android_open_url`'s browser Intent
   (`ourcal.py:693`) and the split OAuth flow are all gated on `is_android()`,
   so on-device Google sign-in was equally broken.
3. `AssetFinder` is regenerated when the APK is updated, so anything written
   there is silently erased on the next install.

The same screenshot showed account chips reading "Personal" and "Work" — the
hardcoded placeholders at `ourcal.py:103-106`, confirming `accounts.json` was
being looked for in the wrong place.

**Why 217 green tests missed it.** Every platform test asserts the desktop side.
`test_not_android_off_android` (`tests/test_ourcal.py:1660`) proves
`is_android()` is False when Chaquopy is absent — true on a Mac, and silent
about a phone. The True branch has never been executed anywhere.

## Decisions

| Decision | Chosen | Rejected |
|---|---|---|
| What crosses to the phone | All six files — the phone is signed in on arrival and never runs OAuth | Just `credentials.json`, then sign in on-device (leans on an unproven flow) |
| Transport | Paste a text bundle | LAN handoff; SAF file picker |
| Bundle safety | Passphrase-encrypted, stdlib only | Plaintext; `cryptography`/AES-GCM |
| KDF | PBKDF2-HMAC-SHA256, 600k | scrypt |
| Android storage | `getFilesDir()` (private, internal) | `getExternalFilesDir()` (current) |
| Scope | Seam fix and importer in one branch, seam first | Importer alone; two separate cycles |

**Transport.** A LAN handoff (Mac serves once, phone pulls with a pairing code)
keeps secrets off every cloud and was the recommendation, but paste was chosen
for its far smaller surface. Encryption is what makes that choice safe: the
bundle carries four live refresh tokens, and any convenient Mac→phone text
channel routes through a cloud clipboard, a drafts folder or a chat backup.
Encrypted, that exposure is ciphertext.

**KDF.** scrypt is memory-hard and the better primitive, but its availability
depends on OpenSSL build flags that cannot be verified on Chaquopy without the
device in hand. `hashlib.pbkdf2_hmac` is present in every CPython build that has
`hashlib` at all. 600,000 iterations is the current OWASP figure for
PBKDF2-HMAC-SHA256; roughly 0.5–1s on a phone, paid once per import.

**Android storage.** `ourcal.py:38-48` chose external storage for one stated
reason: "you have to be able to PUT credentials.json there: this path shows up
in any file manager." Android 11+ disproved that premise, and it is the reason
this work exists. Once paste-import exists, nothing needs to reach the directory
from outside, so internal storage is strictly better: private to the app, not
world-readable, survives app updates, wiped on uninstall. That docstring is
rewritten to record why the original reasoning failed rather than quietly
flipping the value.

---

## Part 0 — Repair the platform seam

Prerequisite. Without it the importer writes into a directory Android erases on
the next APK install.

### `is_android()`

```python
def is_android():
    import sys
    # CPython built for Android defines this. It needs no Java bridge, so it
    # cannot fail the way importing a Java package can — which is how the
    # previous probe silently returned False on a real device.
    if hasattr(sys, "getandroidapilevel"):
        return True
    try:
        import com.chaquo.python  # noqa: F401
        return True
    except Exception:            # not just ImportError: the bridge can fail
        return False
```

The root cause of the Chaquopy import failing under Briefcase is not known and
cannot be determined without the device. The fix does not depend on knowing it:
the primary probe touches only the interpreter.

### `android_data_dir()`

Returns `ctx.getFilesDir().getAbsolutePath()` —
`/data/data/com.leelakumili.ourcal/files`. No collision with Chaquopy's
`chaquopy/` subdirectory. `data_dir()` keeps its current shape: Android first,
then bundled, then checkout.

### Diagnostics

The setup page footer displays the resolved `data_dir()` and whether the Android
branch is live, so this class of failure is visible on the phone instead of
silent for a month.

---

## Part 1 — Bundle format

```
ourcal1.<base64url-nopad( salt(16) ‖ nonce(16) ‖ ciphertext ‖ mac(32) )>
```

Plaintext, before encryption:

```json
{"v": 1, "files": {"credentials.json": "<file contents>", ...}}
```

serialised with `json.dumps`, UTF-8 encoded, then `gzip.compress`. For six files
this is ~3.6KB raw, ~2.7KB as a finished bundle.

### Crypto

All stdlib (`hashlib`, `hmac`, `os.urandom`, `base64`, `gzip`).

- **Key derivation.**
  `dk = hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt, 600_000, dklen=64)`
  → `enc_key = dk[:32]`, `mac_key = dk[32:]`.
- **Keystream.** Block `i` (from 0) is
  `hmac(enc_key, nonce ‖ i.to_bytes(8, "big"), sha256).digest()`; blocks are
  concatenated and truncated to the plaintext length, then XORed. This is the
  HKDF-Expand shape — a keyed PRF in counter mode. An earlier draft specified
  `sha256(enc_key ‖ nonce ‖ i)` instead; no break was found in it, but that is
  the secret-prefix construction HMAC exists to avoid, and switching cost
  nothing before any real bundle existed.
- **MAC.** `hmac.new(mac_key, b"ourcal1" ‖ salt ‖ nonce ‖ ciphertext, sha256)`.
  Encrypt-then-MAC. The version string is inside the MAC so the format version
  cannot be swapped. Verified with `hmac.compare_digest` **before** any
  decryption is attempted.

**Accepted risk — the keystream is a custom construction.** HMAC-SHA256 in
counter mode is a conservative, well-understood way to build a PRF stream, but
composing your own stream cipher is still not the same as using a vetted one,
and no stdlib alternative exists. This is a deliberate trade against adding a
native `cryptography` wheel to an Android build that currently has none and
builds clean. Recorded here so it is a known property of the design, not an
accident.

**Whitespace.** A pasted bundle is normalised with `"".join(text.split())`, not
`text.strip()`. The bundle crosses through a messaging app by design and those
hard-wrap long strings; stripping only the ends leaves embedded newlines that
throw off base64 padding and surface as "looks truncated" to a user who pasted
the whole thing.

**Format pinning.** One known-answer test holds a hardcoded bundle and asserts
its exact decoded contents. Every other test round-trips through this same code
and would stay green through a format change — while bundles from a different
build stopped opening. The phone runs a sideloaded APK that does not
auto-update, so a Mac one version ahead of the phone is the normal case.

**Decrypt order** (any failure aborts before the next step):
prefix check → base64 decode → length **> 64** (64 bytes of framing plus a
non-empty ciphertext) → split → derive keys → verify MAC → XOR → gunzip →
JSON parse → validate.

---

## Part 2 — Export (`./ourcal.py --export`)

Handled at the top of `main()`, before `ensure_deps()` — export is stdlib-only
and must work on a machine where the Google libraries are absent or broken.

1. Collect the files present in `data_dir()` whose names match the whitelist.
2. If `credentials.json` is absent, write an error to stderr and exit 1 —
   there is nothing worth exporting.
3. Prompt for the passphrase **twice** via `getpass.getpass`, and compare. A
   typo otherwise produces a bundle that can never be opened.
4. Write a one-line warning to **stderr** noting the bundle contains live
   refresh tokens.
5. Print the bundle to **stdout**.

The stdout/stderr split is what makes `./ourcal.py --export | pbcopy` work:
`getpass` reads and prompts on `/dev/tty`, the warning goes to stderr, only the
bundle is piped.

---

## Part 3 — Import

### Name whitelist

The bundle is untrusted input. Even though a hostile bundle needs the
passphrase, `os.path.join(data_dir(), name)` with a crafted name is a path
traversal. Names are matched against an **exact whitelist**, never sanitised:

- `credentials.json`
- `accounts.json`
- `^token_[a-z0-9-]+\.json$` — exactly what `slug()` produces (`ourcal.py:85-88`)

Any other name refuses the whole bundle. Traversal is structurally impossible
rather than defended against.

### All-or-nothing

Everything is validated before a single byte is written: every name against the
whitelist, every value parses as JSON, and `accounts.json` passes
`parse_accounts()` (`ourcal.py:109`). Only then does writing begin.

Each file is created with `os.open(..., O_CREAT|O_WRONLY|O_TRUNC, 0o600)` — mode
at creation, so there is no window where it is world-readable — then
`os.replace()`d into place from a temp name in the same directory. This matches
the existing files, which are all `-rw-------`.

**Accepted limit:** six renames are not one transaction. A disk failure part-way
can still leave a partial write. Validate-first eliminates every failure mode
short of that.

### Reloading accounts

`ACCOUNTS` resolves at **import** time (`ourcal.py:142`), so writing
`accounts.json` changes nothing until it is re-read. A new `reload_accounts()`
reassigns the module global. `get_events` (`ourcal.py:537`), `_google_collect`
(`ourcal.py:829`) and `_email_for` (`ourcal.py:838`) all read it at call time,
so reassignment suffices — no restart. This is what turns the placeholder
"Personal / Work" chips into the real accounts without closing the app.

---

## Part 4 — UI

### Endpoints

Following the existing static-`PAGE`-plus-JSON-endpoint pattern:

| Route | Returns |
|---|---|
| `GET /setup` | the setup page (static `SETUP_PAGE` string) |
| `GET /api/status` | `{dataDir, android, hasCredentials, accounts, accountsFromFile, signedIn}` |
| `POST /api/import` | `{ok, written: […], accounts: N}` or `{ok: false, error}` |

`signedIn` lists the labels whose `token_path()` exists. `accountsFromFile` is
whether `accounts.json` was actually read, distinguishing real accounts from the
placeholders at `ourcal.py:103-106` — the distinction the phone screenshot made
visible.

### Reaching the setup page

Error entries from `list_account_events` gain a machine-readable
`"setup": true` flag when the failure is the `FileNotFoundError` for a missing
`credentials.json` (`ourcal.py:819`) — the banner logic must not string-match
error text.

The banner renderer (`ourcal.py:1265`) then collapses: when there is at least
one error and **every** error carries `setup: true`, the per-account banners are
replaced by a single one reading *"OurCal isn't set up on this device yet"* with
a **Set up this device** button. Any other mix of errors renders exactly as it
does today — one expired token must not hide behind a "not set up" message.

Because that banner disappears once setup succeeds, `/setup` also stays
reachable from a small permanent link in the page footer. Re-importing after a
revoked token must not require breaking the app first.

### Setup page

Instructions for the `--export` command, a textarea for the bundle, a password
field, an Import button, and the diagnostics footer (resolved `data_dir()`,
Android branch live or not, credentials present, account count, sign-in count).

The same page is served on both platforms — that is what makes the whole feature
testable on the desktop with no phone in the loop.

---

## Error handling

Every failure is one plain sentence, and nothing is written on any of them.

| Cause | Message |
|---|---|
| Missing `ourcal1.` prefix | That doesn't look like an OurCal bundle. |
| Bad base64, or shorter than 64 bytes | The bundle looks truncated — paste the whole thing. |
| MAC mismatch | Wrong passphrase, or the bundle was altered in transit. |
| Gunzip or JSON failure after a valid MAC | The bundle is corrupt. |
| Name outside the whitelist | Bundle contains an unexpected file: `<name>` — nothing was written. |
| `accounts.json` fails `parse_accounts` | The accounts list in the bundle is invalid — nothing was written. |

A wrong passphrase and a tampered bundle share one message deliberately, and
neither reaches the decrypt path.

---

## Testing

All offline, no Google, no network, fitting the existing demo-mode suite.

**Part 0 — the tests that would have caught this bug**
- `is_android()` is True when `sys.getandroidapilevel` exists (injected, removed
  on cleanup); False on a plain desktop.
- `is_android()` is True when the probe is absent but the Chaquopy import
  succeeds (faked module).
- `is_android()` is False, not raising, when the Chaquopy import raises
  something other than `ImportError`.
- `data_dir()` returns `android_data_dir()` when `is_android()` is True, ahead
  of both the bundled and checkout branches.
- `token_path()` follows `data_dir()` onto the Android branch.

**Bundle**
- Full export→import round trip through a temp `data_dir`: six files
  byte-identical.
- Wrong passphrase rejected; directory untouched.
- One flipped ciphertext byte rejected by the MAC.
- Truncated bundle, and a bundle with no `ourcal1.` prefix, both rejected.
- Two exports of the same files produce different bundles (fresh salt/nonce).

**Import**
- Rejected names: `../evil`, `token_a/b.json`, `random.json`, `TOKEN_X.json`.
- An invalid `accounts.json` in the bundle leaves the directory untouched —
  all-or-nothing verified by listing the directory, not by return value.
- Written files are mode `0600`.
- `ACCOUNTS` reflects the imported `accounts.json` after import, asserted on the
  module global.

**HTTP**
- `GET /setup` serves the page.
- `GET /api/status` returns the documented shape.
- `POST /api/import` round-trips a real bundle over the demo server.
- An error carrying `setup: true` appears in `/api/events` output when
  `credentials.json` is missing.

---

## Follow-on: the debug APK undercuts Part 0

Not part of this work, but it is a direct consequence of Part 0 and must not be
forgotten.

The intended distribution is a GitHub release that people sideload — no Play
Store, which correctly avoids the $25 Play Console fee, the closed-testing
requirement, and Google review. Because OurCal is bring-your-own OAuth client
(`README.md:81-91`), it also needs no OAuth verification: anyone may install it,
and anyone willing to do their own Google Cloud step can use it.

But `build-android.sh:55` ships `app-debug.apk`, and the generated
`build.gradle` configures only a `debug` build type. That means:

- **The universal debug keystore.** Every Android SDK ships the same key
  (password `android`). A publicly downloadable APK signed with it can be
  replaced by anyone's build of the same package name, installing as an update.
- **`android:debuggable="true"`.** `adb shell run-as <pkg>` reads the app's
  private files on any device with USB debugging enabled — which is precisely
  how this project's own `run-as` fallback was proposed. Part 0 moves refresh
  tokens into private internal storage; debuggable makes that privacy nominal.

A public release therefore needs a release build signed with a project keystore,
and `.github/workflows/release.yml` — currently `.dmg` only — needs an Android
job with the SDK and Gradle caching called for in `NOTES-android.md:74-77`.

## Follow-on: the caller guard closes the browser hole, not the Android one

Found by the whole-branch review and confirmed against a live server, so it is
recorded here rather than left in a scratch file.

`POST /api/import` writes OAuth credentials to disk. The server binds
`127.0.0.1`, which does not make it private: `Content-Type: text/plain` is
CORS-safelisted, so a cross-origin POST is a *simple* request — delivered and
processed, with only the response unreadable. A hostile page could therefore
replace `credentials.json` with its own OAuth client, wipe the tokens, and
harvest the re-authorisation with full calendar scope.

`_local_caller()` closes that: it rejects a foreign `Host` (defeating DNS
rebinding reads of `/api/status`, which leaks the data directory and every
account label) and a foreign `Origin` (defeating the cross-site POST). It also
closes the same pre-existing exposure on `/api/create`, `/api/delete` and
`/api/update`.

**It does not close the non-browser case, and that is the Android case.** An
`Origin` check can only authenticate a browser. A native app sends no `Origin`,
`origin is None` is accepted, and a no-`Origin` POST writing an
attacker-controlled `credentials.json` was demonstrated. Android does not
isolate loopback between apps, `PORT` is a fixed 8756 in public source, and the
API is public source — so this is feasible for a targeted attacker on the exact
platform this work exists to serve.

The fix is a per-session token: mint `os.urandom` hex at startup, embed it in
`PAGE` and `SETUP_PAGE`, require it as a header on every `/api/*` request, and
reject requests without it. Roughly fifteen lines. It was deferred rather than
bolted on at the end of this work, and belongs with the on-device sign-in cycle,
which touches this same HTTP surface and should design the two together.

Also unaddressed and pre-existing: no `X-Frame-Options` or `frame-ancestors`, so
a hostile page can iframe the UI and clickjack it — the framed page's own
fetches then carry a valid `Origin`. One header closes it.

## Out of scope

- LAN handoff and QR transport.
- SAF / `ACTION_OPEN_DOCUMENT` file picking.
- Fixing on-device Google OAuth end to end. Part 0 revives the seam it depends
  on, but proving the split flow works on the device is separate work.
- Play Store distribution.

## Docs to update

`README.md` and `SETUP_GUIDE.md` gain an Android section covering `--export`,
the paste flow, and the fact that the bundle carries live refresh tokens.
