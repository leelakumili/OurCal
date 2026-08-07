# Android Setup Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a sideloaded Android build receive its `credentials.json`, `accounts.json` and `token_*.json` as one passphrase-encrypted string pasted into the app, and repair the platform seam that has been silently disabling every Android code path.

**Architecture:** Everything lands in `ourcal.py`. Part 0 fixes `is_android()` to probe the interpreter instead of the Java bridge, and moves Android storage to the app's private internal directory. A new `── TRANSFER ──` section adds bundle pack/unpack (stdlib crypto), a whitelisted all-or-nothing file writer, and a `--export` CLI. The existing local HTTP server gains `/setup`, `/api/status` and `/api/import`, so the same setup page serves both platforms.

**Tech Stack:** Python 3 stdlib only — `hashlib`, `hmac`, `gzip`, `base64`, `getpass`, `os.urandom`. No new dependencies on either platform.

**Spec:** `docs/superpowers/specs/2026-08-07-android-setup-import-design.md`

## Global Constraints

- **Single file.** All Python goes in `ourcal.py`. `packaging/build-android.sh:27` copies exactly that one file to `packaging/android/src/ourcal/core.py`; splitting into modules breaks the Android build. Do not create new Python modules.
- **Stdlib only.** No new entries in `packaging/android/pyproject.toml`. `cryptography` is deliberately absent from the APK and must stay absent.
- **⚠️ Tests must never write to the real data directory.** `data_dir()` in a checkout returns `APP_DIR` — the repo itself — where the developer's real `credentials.json`, `accounts.json` and four `token_*.json` live. `CONTRIBUTING.md:49` records a past session that nearly clobbered them. **Every test that writes files MUST redirect `data_dir` to a temp directory first.** Task 3 provides the helper; use it.
- Test runner: `python3 -m unittest discover tests -q` (from `CONTRIBUTING.md:35`).
- Tests import `ourcal` with `OURCAL_DEMO=1` already set at `tests/test_ourcal.py:3`.
- Bundle prefix is exactly `ourcal1.` and the MAC covers the literal `b"ourcal1"`.
- PBKDF2-HMAC-SHA256 at exactly **600000** rounds, `dklen=64`.
- Salt 16 bytes, nonce 16 bytes, MAC 32 bytes.
- Error strings shown to users are specified verbatim in the spec's error table — copy them exactly; tests assert on them.
- Work on branch `android-setup-import`, which already exists and holds the spec.
- A prior session's work is **stashed**, not committed: the `tz()` Android fallback, `VERSION = "1.0.1"`, and `TestTimezone`. So at HEAD `ourcal.tz()` does not exist and `VERSION` is `"1.0.0"` — do not reference either. The stash must be restored before any APK is built (`packaging/build-android.sh` refuses on a version mismatch, and Android has no tz database without the fallback).

---

### Task 1: Repair the platform seam

The prerequisite. Until `is_android()` returns True on a device, imported files land in Chaquopy's `AssetFinder` directory, which is regenerated on every APK install.

**Files:**
- Modify: `ourcal.py:24-48` (`is_android`, `android_data_dir`)
- Test: `tests/test_ourcal.py` (append two new classes before `if __name__ == "__main__":`)

**Interfaces:**
- Consumes: nothing.
- Produces: `is_android() -> bool`, `android_data_dir() -> str`. `data_dir()` and `token_path()` keep their existing signatures and follow the repaired branch.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ourcal.py`, immediately before the `if __name__ == "__main__":` block:

```python
class TestAndroidProbe(unittest.TestCase):
    """The bug this replaces: is_android() imported a Java package, which
    returned False on a real device. Every Android seam stayed dark and
    data_dir() fell through to APP_DIR — Chaquopy's AssetFinder directory,
    which is regenerated on every APK install. The whole suite missed it
    because every existing platform test asserts the desktop side."""

    def _fake_api_level(self):
        import sys
        sys.getandroidapilevel = lambda: 33
        self.addCleanup(lambda: delattr(sys, "getandroidapilevel"))

    def test_true_when_the_interpreter_reports_an_android_api_level(self):
        self._fake_api_level()
        self.assertTrue(ourcal.is_android())

    def test_false_on_a_plain_desktop(self):
        self.assertFalse(ourcal.is_android())

    def test_false_when_the_java_bridge_raises_a_non_importerror(self):
        # The old probe only caught ImportError. A bridge that is present but
        # not ready raises other things, and an uncaught one would crash the
        # app at import time instead of degrading.
        import builtins
        real = builtins.__import__

        def boom(name, *a, **k):
            if name.startswith("com.chaquo"):
                raise RuntimeError("bridge not ready")
            return real(name, *a, **k)

        builtins.__import__ = boom
        self.addCleanup(lambda: setattr(builtins, "__import__", real))
        self.assertFalse(ourcal.is_android())


class TestAndroidDataDir(unittest.TestCase):
    """The Android branch, exercised on the desktop by faking the probe —
    the coverage that never existed."""

    def _android(self, path="/data/data/com.leelakumili.ourcal/files"):
        real_a, real_d = ourcal.is_android, ourcal.android_data_dir
        ourcal.is_android = lambda: True
        ourcal.android_data_dir = lambda: path
        self.addCleanup(lambda: setattr(ourcal, "is_android", real_a))
        self.addCleanup(lambda: setattr(ourcal, "android_data_dir", real_d))

    def test_android_beats_both_desktop_branches(self):
        self._android()
        real = ourcal.is_bundled
        ourcal.is_bundled = lambda: True      # even bundled, Android wins
        self.addCleanup(lambda: setattr(ourcal, "is_bundled", real))
        self.assertEqual(ourcal.data_dir(),
                         "/data/data/com.leelakumili.ourcal/files")

    def test_token_path_follows_the_android_branch(self):
        self._android("/android/files")
        self.assertEqual(ourcal.token_path("Leela K"),
                         "/android/files/token_leela-k.json")

    def test_user_path_follows_the_android_branch(self):
        self._android("/android/files")
        self.assertEqual(ourcal.user_path("credentials.json"),
                         "/android/files/credentials.json")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_ourcal.TestAndroidProbe -v`

Expected: `test_true_when_the_interpreter_reports_an_android_api_level` FAILS — the current probe ignores `sys.getandroidapilevel`, so it returns False. `test_false_when_the_java_bridge_raises_a_non_importerror` FAILS with `RuntimeError: bridge not ready` escaping, because the current `except ImportError` does not catch it.

- [ ] **Step 3: Replace `is_android` and `android_data_dir`**

In `ourcal.py`, replace the whole of lines 24-48 (both functions and their docstrings) with:

```python
def is_android():
    """True when running inside the Android app.

    Probes the interpreter, never the Java bridge. The previous version
    imported `com.chaquo.python` and returned False on a real device: every
    Android seam stayed dark and data_dir() fell through to APP_DIR. Any
    CPython built for Android defines sys.getandroidapilevel, and reading an
    attribute cannot fail the way importing a Java package can.
    """
    import sys
    if hasattr(sys, "getandroidapilevel"):
        return True
    try:
        import com.chaquo.python  # noqa: F401
        return True
    except Exception:   # a present-but-unready bridge raises more than ImportError
        return False


def android_data_dir():
    """The app's private internal files directory.

    Internal, not external. This used to return getExternalFilesDir() so that
    credentials.json could be dropped in with a file manager — but Android 11+
    hides Android/data from every file manager and from MTP, so that was never
    possible on a modern device, and it is the reason setup now arrives as a
    pasted bundle. With nothing needing to reach this directory from outside,
    private storage is strictly better: not world-readable, survives app
    updates, removed on uninstall.
    """
    from com.chaquo.python import Python
    ctx = Python.getPlatform().getApplication()
    return str(ctx.getFilesDir().getAbsolutePath())
```

- [ ] **Step 4: Run the full suite**

Run: `python3 -m unittest discover tests -q`

Expected: all pass, including the pre-existing `TestPlatform.test_not_android_off_android` — a Mac has no `sys.getandroidapilevel` and no Chaquopy, so `is_android()` is still False there.

- [ ] **Step 5: Commit**

```bash
git add ourcal.py tests/test_ourcal.py
git commit -m "Probe the interpreter, not the Java bridge, to detect Android

is_android() imported com.chaquo.python and returned False on a real
device. data_dir() therefore fell through to APP_DIR — Chaquopy's
AssetFinder directory, which is regenerated on every APK install — so
credentials.json was looked for somewhere nothing could put it, and the
browser Intent and split OAuth flow never ran either.

sys.getandroidapilevel is defined by any CPython built for Android and
needs no bridge. The import stays as a fallback, now catching Exception:
a present-but-unready bridge raises more than ImportError.

Storage moves to getFilesDir(). The external directory was chosen so a
file manager could reach it, which Android 11+ made impossible; a pasted
bundle replaces that, and private storage is strictly better.

Tests now exercise the Android branch by faking the probe. Every
existing platform test asserts the desktop side, which is why a green
suite said nothing about the phone."
```

---

### Task 2: Bundle pack and unpack

Pure functions over strings. No filesystem, so no risk to real credentials.

**Files:**
- Modify: `ourcal.py` — insert a new `── TRANSFER ──` section immediately **before** the `# ── HTML ──` line (currently `ourcal.py:946`)
- Test: `tests/test_ourcal.py` (append before `if __name__ == "__main__":`)

**Interfaces:**
- Consumes: `is_android` etc. from Task 1 (indirectly, via nothing yet).
- Produces: `BUNDLE_PREFIX: str`, `BundleError(Exception)`, `make_bundle(files: dict[str, str], passphrase: str) -> str`, `open_bundle(bundle: str, passphrase: str) -> dict[str, str]`.

- [ ] **Step 1: Write the failing tests**

```python
class TestBundleRoundTrip(unittest.TestCase):
    """The bundle crosses an untrusted channel — it carries live refresh
    tokens, and any convenient Mac-to-phone text route touches a cloud."""

    FILES = {"credentials.json": '{"installed": {"client_id": "abc"}}',
             "accounts.json": '[{"label": "L", "email": "l@example.com"}]',
             "token_l.json": '{"refresh_token": "1//secret"}'}

    def test_round_trips_every_file_byte_for_byte(self):
        b = ourcal.make_bundle(self.FILES, "correct horse")
        self.assertEqual(ourcal.open_bundle(b, "correct horse"), self.FILES)

    def test_bundle_is_one_pasteable_line(self):
        b = ourcal.make_bundle(self.FILES, "pw")
        self.assertTrue(b.startswith("ourcal1."))
        self.assertNotIn("\n", b)

    def test_the_plaintext_is_not_recoverable_from_the_bundle(self):
        b = ourcal.make_bundle(self.FILES, "pw")
        self.assertNotIn("1//secret", b)
        self.assertNotIn("l@example.com", b)

    def test_two_exports_of_the_same_files_differ(self):
        # Fresh salt and nonce each time; identical bundles would leak that
        # nothing changed between two exports.
        a = ourcal.make_bundle(self.FILES, "pw")
        b = ourcal.make_bundle(self.FILES, "pw")
        self.assertNotEqual(a, b)
        self.assertEqual(ourcal.open_bundle(a, "pw"),
                         ourcal.open_bundle(b, "pw"))

    def test_survives_being_pasted_with_surrounding_whitespace(self):
        b = ourcal.make_bundle(self.FILES, "pw")
        self.assertEqual(ourcal.open_bundle("\n  " + b + "  \n", "pw"),
                         self.FILES)


class TestBundleRejection(unittest.TestCase):
    FILES = {"credentials.json": '{"installed": {}}'}

    def test_wrong_passphrase(self):
        b = ourcal.make_bundle(self.FILES, "right")
        with self.assertRaises(ourcal.BundleError) as cm:
            ourcal.open_bundle(b, "wrong")
        self.assertIn("Wrong passphrase", str(cm.exception))

    def test_a_single_flipped_byte_is_caught_by_the_mac(self):
        import base64
        b = ourcal.make_bundle(self.FILES, "pw")
        body = b[len("ourcal1."):]
        raw = bytearray(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        raw[40] ^= 1                     # inside the ciphertext
        tampered = "ourcal1." + base64.urlsafe_b64encode(
            bytes(raw)).decode().rstrip("=")
        with self.assertRaises(ourcal.BundleError) as cm:
            ourcal.open_bundle(tampered, "pw")
        # Tampering and a wrong passphrase are deliberately indistinguishable.
        self.assertIn("Wrong passphrase", str(cm.exception))

    def test_missing_prefix(self):
        with self.assertRaises(ourcal.BundleError) as cm:
            ourcal.open_bundle("just some text", "pw")
        self.assertIn("doesn't look like an OurCal bundle", str(cm.exception))

    def test_truncated_bundle(self):
        b = ourcal.make_bundle(self.FILES, "pw")
        with self.assertRaises(ourcal.BundleError) as cm:
            ourcal.open_bundle(b[:20], "pw")
        self.assertIn("truncated", str(cm.exception))

    def test_empty_input(self):
        with self.assertRaises(ourcal.BundleError):
            ourcal.open_bundle("", "pw")

    def test_prefix_with_nothing_after_it(self):
        with self.assertRaises(ourcal.BundleError) as cm:
            ourcal.open_bundle("ourcal1.", "pw")
        self.assertIn("truncated", str(cm.exception))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_ourcal.TestBundleRoundTrip -v`

Expected: FAIL with `AttributeError: module 'ourcal' has no attribute 'make_bundle'`.

- [ ] **Step 3: Add the TRANSFER section**

Insert immediately before the line `# ── HTML ──` in `ourcal.py`:

```python
# ── TRANSFER ────────────────────────────────────────────────────────────
# Moving a setup onto a phone. Android 11+ hides Android/data from every file
# manager and from MTP, so a sideloaded app cannot be handed a file at all —
# the setup crosses as one pasted, passphrase-encrypted string instead.
BUNDLE_PREFIX = "ourcal1."
_KDF_ROUNDS = 600000            # the OWASP figure for PBKDF2-HMAC-SHA256
_SALT_LEN = 16
_NONCE_LEN = 16
_MAC_LEN = 32
_TRUNCATED = "The bundle looks truncated — paste the whole thing."


class BundleError(Exception):
    """Anything wrong with a bundle. The message is shown to the user."""


def _bundle_keys(passphrase, salt):
    """Split one derived secret into an encryption key and a MAC key.

    PBKDF2 rather than scrypt: scrypt is the better primitive, but its
    availability depends on OpenSSL build flags that cannot be checked on
    Chaquopy without the device. pbkdf2_hmac exists wherever hashlib does.
    """
    import hashlib
    dk = hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt,
                             _KDF_ROUNDS, dklen=64)
    return dk[:32], dk[32:]


def _keystream(enc_key, nonce, length):
    """SHA-256 in counter mode.

    Not a vetted cipher, and deliberately so: the stdlib has no AES, and
    adding `cryptography` would put a native wheel into an Android build that
    has none and currently builds clean. Conservative construction, recorded
    as an accepted risk in the design doc rather than left as an accident.
    """
    import hashlib
    out = bytearray()
    counter = 0
    while len(out) < length:
        out += hashlib.sha256(
            enc_key + nonce + counter.to_bytes(8, "big")).digest()
        counter += 1
    return bytes(out[:length])


def _xor(data, stream):
    return bytes(a ^ b for a, b in zip(data, stream))


def make_bundle(files, passphrase):
    """Pack {name: contents} into one pasteable encrypted string."""
    import base64
    import gzip
    import hashlib
    import hmac
    plain = gzip.compress(json.dumps({"v": 1, "files": files}).encode("utf-8"))
    salt, nonce = os.urandom(_SALT_LEN), os.urandom(_NONCE_LEN)
    enc_key, mac_key = _bundle_keys(passphrase, salt)
    ct = _xor(plain, _keystream(enc_key, nonce, len(plain)))
    mac = hmac.new(mac_key, b"ourcal1" + salt + nonce + ct,
                   hashlib.sha256).digest()
    raw = salt + nonce + ct + mac
    return BUNDLE_PREFIX + base64.urlsafe_b64encode(raw).decode().rstrip("=")


def open_bundle(bundle, passphrase):
    """Unpack a bundle to {name: contents}. Raises BundleError."""
    import base64
    import gzip
    import hashlib
    import hmac
    text = (bundle or "").strip()
    if not text.startswith(BUNDLE_PREFIX):
        raise BundleError("That doesn't look like an OurCal bundle.")
    body = text[len(BUNDLE_PREFIX):]
    try:
        raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
    except Exception:
        raise BundleError(_TRUNCATED)
    if len(raw) <= _SALT_LEN + _NONCE_LEN + _MAC_LEN:
        raise BundleError(_TRUNCATED)    # framing only, no ciphertext
    salt = raw[:_SALT_LEN]
    nonce = raw[_SALT_LEN:_SALT_LEN + _NONCE_LEN]
    ct = raw[_SALT_LEN + _NONCE_LEN:-_MAC_LEN]
    mac = raw[-_MAC_LEN:]
    enc_key, mac_key = _bundle_keys(passphrase, salt)
    expected = hmac.new(mac_key, b"ourcal1" + salt + nonce + ct,
                        hashlib.sha256).digest()
    # Encrypt-then-MAC: a wrong passphrase and a tampered bundle share one
    # message, and neither reaches the decrypt path below.
    if not hmac.compare_digest(mac, expected):
        raise BundleError(
            "Wrong passphrase, or the bundle was altered in transit.")
    try:
        payload = json.loads(gzip.decompress(
            _xor(ct, _keystream(enc_key, nonce, len(ct)))).decode("utf-8"))
        files = payload["files"]
        if not isinstance(files, dict) or not all(
                isinstance(k, str) and isinstance(v, str)
                for k, v in files.items()):
            raise ValueError("bad shape")
    except Exception:
        raise BundleError("The bundle is corrupt.")
    return files
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_ourcal.TestBundleRoundTrip tests.test_ourcal.TestBundleRejection -v`

Expected: all PASS. Then `python3 -m unittest discover tests -q` — still green.

- [ ] **Step 5: Commit**

```bash
git add ourcal.py tests/test_ourcal.py
git commit -m "Pack a setup into one passphrase-encrypted string

A sideloaded Android app cannot be handed a file, so the setup has to
cross as text the user pastes. That text carries live refresh tokens and
every convenient Mac-to-phone route touches a cloud, so it is encrypted:
PBKDF2 at 600k rounds, a SHA-256 counter-mode keystream, HMAC over the
ciphertext, verified before anything is decrypted.

PBKDF2 rather than scrypt because scrypt's availability depends on
OpenSSL build flags that cannot be checked on Chaquopy from here. The
keystream is a custom construction — the stdlib has no AES and adding
cryptography would put a native wheel into an APK that has none. Both
trades are recorded in the design doc.

A wrong passphrase and a tampered bundle report the same thing."
```

---

### Task 3: Whitelisted, all-or-nothing file writing

**Files:**
- Modify: `ourcal.py` — append to the `── TRANSFER ──` section from Task 2
- Test: `tests/test_ourcal.py`

**Interfaces:**
- Consumes: `BundleError`, `open_bundle` (Task 2); `parse_accounts`, `load_accounts`, `user_path`, `ensure_data_dir`, `data_dir` (existing).
- Produces: `is_user_file(name: str) -> bool`, `reload_accounts() -> list`, `write_user_files(files: dict[str, str]) -> list[str]`, `import_bundle(bundle: str, passphrase: str) -> dict` returning `{"ok": True, "written": [...], "accounts": int}`.

- [ ] **Step 1: Write the failing tests**

Note the `_TmpData` helper — **every later task that writes files reuses it.**

```python
class _TmpData:
    """Point data_dir() at a throwaway directory.

    CONTRIBUTING.md:49 — never point tests at real credentials. In a checkout
    data_dir() is APP_DIR, the repo itself, where the developer's real
    credentials.json, accounts.json and token files live. A test that writes
    without redirecting would overwrite them.
    """

    def _tmp_data(self):
        import shutil
        import tempfile
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        real = ourcal.data_dir
        ourcal.data_dir = lambda: tmp
        self.addCleanup(lambda: setattr(ourcal, "data_dir", real))
        return tmp


class TestUserFileWhitelist(unittest.TestCase):
    """A bundle is untrusted input. Names are matched against a whitelist,
    never sanitised: os.path.join with a crafted name is a traversal."""

    def test_accepts_the_two_config_files(self):
        self.assertTrue(ourcal.is_user_file("credentials.json"))
        self.assertTrue(ourcal.is_user_file("accounts.json"))

    def test_accepts_slugged_token_names(self):
        self.assertTrue(ourcal.is_user_file("token_leela.json"))
        self.assertTrue(ourcal.is_user_file("token_leela-k.json"))
        self.assertTrue(ourcal.is_user_file("token_leela-26033.json"))

    def test_rejects_traversal(self):
        for bad in ["../evil.json", "../../etc/passwd",
                    "token_a/b.json", "/etc/passwd", "token_../x.json"]:
            self.assertFalse(ourcal.is_user_file(bad), bad)

    def test_rejects_names_outside_the_whitelist(self):
        for bad in ["random.json", "TOKEN_X.json", "token_.json",
                    "token_Leela.json", "credentials.json.bak", "", "."]:
            self.assertFalse(ourcal.is_user_file(bad), bad)


class TestWriteUserFiles(_TmpData, unittest.TestCase):
    GOOD = {"credentials.json": '{"installed": {"client_id": "x"}}',
            "accounts.json": '[{"label": "L", "email": "l@example.com"}]',
            "token_l.json": '{"refresh_token": "s"}'}

    def test_writes_every_file(self):
        tmp = self._tmp_data()
        self.assertEqual(ourcal.write_user_files(self.GOOD),
                         ["accounts.json", "credentials.json", "token_l.json"])
        for name, body in self.GOOD.items():
            with open(os.path.join(tmp, name)) as f:
                self.assertEqual(f.read(), body)

    def test_files_are_owner_only(self):
        import stat
        tmp = self._tmp_data()
        ourcal.write_user_files(self.GOOD)
        for name in self.GOOD:
            mode = stat.S_IMODE(os.stat(os.path.join(tmp, name)).st_mode)
            self.assertEqual(mode, 0o600, name)

    def test_a_bad_name_writes_nothing_at_all(self):
        # All-or-nothing: validate everything before writing anything, so a
        # rejected bundle cannot leave a half-configured device.
        tmp = self._tmp_data()
        payload = dict(self.GOOD)
        payload["../evil.json"] = "{}"
        with self.assertRaises(ourcal.BundleError) as cm:
            ourcal.write_user_files(payload)
        self.assertIn("unexpected file", str(cm.exception))
        self.assertEqual(os.listdir(tmp), [])

    def test_invalid_accounts_json_writes_nothing_at_all(self):
        tmp = self._tmp_data()
        payload = dict(self.GOOD)
        payload["accounts.json"] = '[{"label": "", "email": "nope"}]'
        with self.assertRaises(ourcal.BundleError) as cm:
            ourcal.write_user_files(payload)
        self.assertIn("accounts list in the bundle is invalid",
                      str(cm.exception))
        self.assertEqual(os.listdir(tmp), [])

    def test_non_json_content_writes_nothing_at_all(self):
        tmp = self._tmp_data()
        payload = dict(self.GOOD)
        payload["token_l.json"] = "not json at all"
        with self.assertRaises(ourcal.BundleError):
            ourcal.write_user_files(payload)
        self.assertEqual(os.listdir(tmp), [])

    def test_leaves_no_temp_files_behind(self):
        tmp = self._tmp_data()
        ourcal.write_user_files(self.GOOD)
        self.assertEqual(sorted(os.listdir(tmp)), sorted(self.GOOD))


class TestReloadAccounts(_TmpData, unittest.TestCase):
    """ACCOUNTS resolves at import (ourcal.py:142), so writing accounts.json
    changes nothing until it is re-read. Without this the phone keeps showing
    the placeholder Personal/Work chips after a successful import."""

    def setUp(self):
        real = ourcal.ACCOUNTS
        self.addCleanup(lambda: setattr(ourcal, "ACCOUNTS", real))

    def test_import_replaces_the_module_global(self):
        self._tmp_data()
        files = {"credentials.json": '{"installed": {}}',
                 "accounts.json": json.dumps(
                     [{"label": "Imported", "email": "i@example.com"}])}
        bundle = ourcal.make_bundle(files, "pw")
        result = ourcal.import_bundle(bundle, "pw")
        self.assertTrue(result["ok"])
        self.assertEqual(result["accounts"], 1)
        self.assertEqual([a["label"] for a in ourcal.ACCOUNTS], ["Imported"])

    def test_a_bundle_without_accounts_leaves_them_alone(self):
        self._tmp_data()
        before = list(ourcal.ACCOUNTS)
        bundle = ourcal.make_bundle({"credentials.json": '{"installed": {}}'},
                                    "pw")
        ourcal.import_bundle(bundle, "pw")
        self.assertEqual(ourcal.ACCOUNTS, before)

    def test_a_wrong_passphrase_writes_nothing(self):
        tmp = self._tmp_data()
        bundle = ourcal.make_bundle({"credentials.json": '{"installed": {}}'},
                                    "right")
        with self.assertRaises(ourcal.BundleError):
            ourcal.import_bundle(bundle, "wrong")
        self.assertEqual(os.listdir(tmp), [])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_ourcal.TestUserFileWhitelist -v`

Expected: FAIL with `AttributeError: module 'ourcal' has no attribute 'is_user_file'`.

- [ ] **Step 3: Append to the TRANSFER section**

```python
_TOKEN_FILE_RE = re.compile(r"^token_[a-z0-9-]+\.json$")


def is_user_file(name):
    """Whether a bundle may write this name.

    An exact whitelist, never sanitisation: os.path.join(data_dir(), name)
    with a crafted name is a path traversal, and matching makes that
    structurally impossible rather than defended against. The token pattern is
    exactly what slug() produces.
    """
    return (name in ("credentials.json", "accounts.json")
            or bool(_TOKEN_FILE_RE.match(name)))


def reload_accounts():
    """Re-read accounts.json into the module global.

    ACCOUNTS resolves at import, so writing the file changes nothing until it
    is re-read. get_events, _google_collect and _email_for all read the global
    at call time, so reassignment is enough — no restart, and the placeholder
    chips become the real accounts as soon as an import lands.
    """
    global ACCOUNTS
    loaded = load_accounts(user_path("accounts.json"))
    if loaded:
        ACCOUNTS = loaded
    return ACCOUNTS


def write_user_files(files):
    """Validate every entry, then write them all. Returns the names written.

    All-or-nothing: nothing is written until everything has passed, so a bad
    bundle cannot leave a half-configured device. The six renames are still
    not one transaction — a disk failure part-way can leave a partial write —
    but validate-first removes every failure mode short of that.
    """
    for name, body in sorted(files.items()):
        if not is_user_file(name):
            raise BundleError(f"Bundle contains an unexpected file: {name} — "
                              "nothing was written.")
        try:
            parsed = json.loads(body)
        except ValueError:
            raise BundleError(f"{name} in the bundle is not valid JSON — "
                              "nothing was written.")
        if name == "accounts.json" and parse_accounts(parsed) is None:
            raise BundleError("The accounts list in the bundle is invalid — "
                              "nothing was written.")
    d = ensure_data_dir()
    written = []
    for name, body in sorted(files.items()):
        tmp = os.path.join(d, "." + name + ".tmp")
        # Mode at creation, not afterwards: never a window in which a token
        # file is readable by anything else on the device.
        fd = os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(body)
        os.replace(tmp, os.path.join(d, name))
        written.append(name)
    return written


def import_bundle(bundle, passphrase):
    """Paste-in setup: decrypt, validate, write, reload. Raises BundleError."""
    written = write_user_files(open_bundle(bundle, passphrase))
    accounts = reload_accounts() if "accounts.json" in written else ACCOUNTS
    return {"ok": True, "written": written, "accounts": len(accounts)}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest discover tests -q`

Expected: all pass. Then confirm the repo's own files were untouched: `git status --short` should show no modification to `credentials.json`, `accounts.json` or any `token_*.json`.

- [ ] **Step 5: Commit**

```bash
git add ourcal.py tests/test_ourcal.py
git commit -m "Write an imported setup safely, or not at all

A bundle is untrusted input, so names are matched against an exact
whitelist rather than sanitised — os.path.join with a crafted name is a
traversal, and matching makes it structurally impossible.

Everything is validated before anything is written, so a rejected bundle
cannot leave a half-configured device. Files are created 0600 at open
time, never chmod'd afterwards, so there is no window where a refresh
token is readable by anything else.

ACCOUNTS resolves at import, so reload_accounts() reassigns the global
after a write; every reader looks it up at call time. Without it an
import succeeds and the phone keeps showing the placeholder chips."
```

---

### Task 4: The `--export` command

**Files:**
- Modify: `ourcal.py` — append to `── TRANSFER ──`; modify `main()` (currently `ourcal.py:1845`)
- Test: `tests/test_ourcal.py`

**Interfaces:**
- Consumes: `make_bundle` (Task 2), `is_user_file` (Task 3), `data_dir` (Task 1).
- Produces: `collect_user_files() -> dict[str, str]`, `export_cli() -> int` (process exit code).

- [ ] **Step 1: Write the failing tests**

```python
class TestCollectUserFiles(_TmpData, unittest.TestCase):
    def test_collects_only_whitelisted_names(self):
        tmp = self._tmp_data()
        for name in ["credentials.json", "accounts.json", "token_l.json",
                     "notes.txt", "token_BAD.json", ".DS_Store"]:
            with open(os.path.join(tmp, name), "w") as f:
                f.write("{}")
        self.assertEqual(sorted(ourcal.collect_user_files()),
                         ["accounts.json", "credentials.json", "token_l.json"])

    def test_empty_when_the_directory_is_missing(self):
        real = ourcal.data_dir
        ourcal.data_dir = lambda: "/nonexistent/ourcal/nowhere"
        self.addCleanup(lambda: setattr(ourcal, "data_dir", real))
        self.assertEqual(ourcal.collect_user_files(), {})


class TestExportImportRoundTrip(_TmpData, unittest.TestCase):
    """The whole point, end to end: what --export prints is what the phone
    can open."""

    def setUp(self):
        real = ourcal.ACCOUNTS
        self.addCleanup(lambda: setattr(ourcal, "ACCOUNTS", real))

    def test_a_mac_export_imports_onto_a_fresh_device(self):
        mac = self._tmp_data()
        files = {
            "credentials.json": '{"installed": {"client_id": "x"}}',
            "accounts.json": json.dumps(
                [{"label": "Leela", "email": "l@example.com"},
                 {"label": "Leela K", "email": "lk@example.com"}]),
            "token_leela.json": '{"refresh_token": "a"}',
            "token_leela-k.json": '{"refresh_token": "b"}'}
        for name, body in files.items():
            with open(os.path.join(mac, name), "w") as f:
                f.write(body)
        bundle = ourcal.make_bundle(ourcal.collect_user_files(), "pw")

        phone = self._tmp_data()          # redirect again: a different device
        result = ourcal.import_bundle(bundle, "pw")
        self.assertEqual(result["accounts"], 2)
        self.assertEqual(sorted(os.listdir(phone)), sorted(files))
        for name, body in files.items():
            with open(os.path.join(phone, name)) as f:
                self.assertEqual(f.read(), body)


class TestExportCli(_TmpData, unittest.TestCase):
    def test_refuses_without_credentials(self):
        self._tmp_data()               # empty
        self.assertEqual(ourcal.export_cli(), 1)

    def test_refuses_when_the_passphrases_differ(self):
        import getpass
        tmp = self._tmp_data()
        with open(os.path.join(tmp, "credentials.json"), "w") as f:
            f.write("{}")
        answers = iter(["one", "two"])
        real = getpass.getpass
        getpass.getpass = lambda *a, **k: next(answers)
        self.addCleanup(lambda: setattr(getpass, "getpass", real))
        self.assertEqual(ourcal.export_cli(), 1)

    def test_prints_only_the_bundle_on_stdout(self):
        # `./ourcal.py --export | pbcopy` must pipe the bundle and nothing
        # else; warnings go to stderr and getpass prompts on the tty.
        import contextlib
        import getpass
        import io
        tmp = self._tmp_data()
        with open(os.path.join(tmp, "credentials.json"), "w") as f:
            f.write('{"installed": {}}')
        real = getpass.getpass
        getpass.getpass = lambda *a, **k: "pw"
        self.addCleanup(lambda: setattr(getpass, "getpass", real))
        out = io.StringIO()
        with contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(ourcal.export_cli(), 0)
        printed = out.getvalue().strip()
        self.assertEqual(len(printed.splitlines()), 1)
        self.assertEqual(ourcal.open_bundle(printed, "pw"),
                         {"credentials.json": '{"installed": {}}'})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_ourcal.TestCollectUserFiles -v`

Expected: FAIL with `AttributeError: module 'ourcal' has no attribute 'collect_user_files'`.

- [ ] **Step 3: Append to the TRANSFER section**

```python
def collect_user_files():
    """The whitelisted files present in data_dir(), as {name: contents}."""
    d = data_dir()
    out = {}
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return out
    for name in names:
        if not is_user_file(name):
            continue
        try:
            with open(os.path.join(d, name)) as f:
                out[name] = f.read()
        except OSError:
            pass
    return out


def export_cli():
    """`./ourcal.py --export` — print a pasteable bundle of this setup.

    Only the bundle goes to stdout, so `./ourcal.py --export | pbcopy` pipes
    exactly the thing you paste: getpass prompts on the tty and every other
    word goes to stderr.
    """
    import getpass
    import sys
    files = collect_user_files()
    if "credentials.json" not in files:
        print(f"OurCal: no credentials.json in {data_dir()} — nothing to "
              "export.", file=sys.stderr)
        return 1
    first = getpass.getpass("Passphrase for the bundle: ")
    if not first:
        print("OurCal: a passphrase is required.", file=sys.stderr)
        return 1
    if first != getpass.getpass("Repeat it: "):
        # A typo here would produce a bundle nobody can ever open.
        print("OurCal: the two passphrases differ — nothing was written.",
              file=sys.stderr)
        return 1
    print(f"OurCal: bundling {len(files)} file(s) from {data_dir()}.\n"
          "        This carries live Google refresh tokens. It is encrypted, "
          "but treat\n        it as a secret anyway.", file=sys.stderr)
    print(make_bundle(files, first))
    return 0
```

- [ ] **Step 4: Wire it into `main()`**

Replace the body of `main()` (`ourcal.py:1845-1857`) so the first three lines read:

```python
def main():
    import sys
    if "--export" in sys.argv:
        # Before ensure_deps(): export is stdlib-only and must work on a
        # machine where the Google libraries are missing or broken.
        raise SystemExit(export_cli())
    if not is_demo():
        ensure_deps()
```

Leave the rest of `main()` unchanged.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -m unittest discover tests -q`

Expected: all pass.

- [ ] **Step 6: Verify by hand, without touching your real files**

```bash
mkdir -p /tmp/ourcal-export-check
cp credentials.json accounts.json token_*.json /tmp/ourcal-export-check/
python3 - <<'PY'
import os
os.environ["OURCAL_DEMO"] = "1"
import ourcal
ourcal.data_dir = lambda: "/tmp/ourcal-export-check"
b = ourcal.make_bundle(ourcal.collect_user_files(), "test-pass")
print("bundle chars:", len(b))
print("round trip:", sorted(ourcal.open_bundle(b, "test-pass")))
PY
rm -rf /tmp/ourcal-export-check
```

Expected: around 3000-4000 characters, and the round trip lists all six filenames.

- [ ] **Step 7: Commit**

```bash
git add ourcal.py tests/test_ourcal.py
git commit -m "Add --export to print a pasteable setup bundle

Collects the whitelisted files from data_dir() and prints one encrypted
string. Only the bundle goes to stdout so 'ourcal.py --export | pbcopy'
pipes exactly what you paste; the passphrase prompt uses the tty and the
refresh-token warning goes to stderr.

The passphrase is asked twice — a typo would otherwise produce a bundle
nobody can open. Handled before ensure_deps() because export is
stdlib-only and has to work where the Google libraries are broken."
```

---

### Task 5: Status endpoint and the setup error flag

**Files:**
- Modify: `ourcal.py:819-822` (`list_account_events` error returns), `ourcal.py:825-834` (`_google_collect`), `ourcal.py:799-801` (mismatch return), and append `setup_status()` to `── TRANSFER ──`
- Test: `tests/test_ourcal.py`

**Interfaces:**
- Consumes: `data_dir`, `is_android` (Task 1); `user_path`, `token_path`, `ACCOUNTS` (existing).
- Produces: `setup_status() -> dict` with keys `dataDir`, `android`, `hasCredentials`, `accounts`, `accountsFromFile`, `signedIn`. `list_account_events` now returns `(events, error_or_None)` where the error is a **dict** `{"message": str, "setup": bool}` rather than a string.

- [ ] **Step 1: Write the failing tests**

```python
class TestSetupStatus(_TmpData, unittest.TestCase):
    """The diagnostics footer. A month of Android breakage was invisible
    because nothing on the phone ever reported which directory it resolved."""

    def setUp(self):
        real = ourcal.ACCOUNTS
        self.addCleanup(lambda: setattr(ourcal, "ACCOUNTS", real))

    def test_reports_an_empty_device(self):
        tmp = self._tmp_data()
        s = ourcal.setup_status()
        self.assertEqual(s["dataDir"], tmp)
        self.assertFalse(s["android"])
        self.assertFalse(s["hasCredentials"])
        self.assertFalse(s["accountsFromFile"])
        self.assertEqual(s["signedIn"], [])

    def test_reports_credentials_and_sign_ins(self):
        tmp = self._tmp_data()
        ourcal.ACCOUNTS = [{"label": "Leela", "email": "l@example.com"},
                           {"label": "Leela K", "email": "lk@example.com"}]
        for name in ["credentials.json", "accounts.json", "token_leela.json"]:
            with open(os.path.join(tmp, name), "w") as f:
                f.write("{}")
        s = ourcal.setup_status()
        self.assertTrue(s["hasCredentials"])
        self.assertTrue(s["accountsFromFile"])
        self.assertEqual(s["accounts"], 2)
        self.assertEqual(s["signedIn"], ["Leela"])   # Leela K has no token

    def test_reports_the_android_branch(self):
        self._tmp_data()
        real = ourcal.is_android
        ourcal.is_android = lambda: True
        self.addCleanup(lambda: setattr(ourcal, "is_android", real))
        self.assertTrue(ourcal.setup_status()["android"])


class TestSetupErrorFlag(unittest.TestCase):
    """The banner must not string-match error text to decide whether to offer
    setup — a missing credentials.json is a different thing from a dead
    token, and only the first one has a way out on the phone."""

    def _events_with(self, exc):
        real = ourcal.service_for
        ourcal.service_for = lambda label, email: (_ for _ in ()).throw(exc)
        self.addCleanup(lambda: setattr(ourcal, "service_for", real))
        return ourcal.list_account_events("L", "l@example.com", "a", "b")

    def test_missing_credentials_is_flagged_as_setup(self):
        _, err = self._events_with(FileNotFoundError("credentials.json is missing"))
        self.assertTrue(err["setup"])
        self.assertIn("credentials.json is missing", err["message"])

    def test_any_other_failure_is_not_flagged_as_setup(self):
        _, err = self._events_with(RuntimeError("token revoked"))
        self.assertFalse(err["setup"])
        self.assertIn("re-auth", err["message"])

    def test_collect_carries_the_flag_through_with_the_label(self):
        real_accounts = ourcal.ACCOUNTS
        ourcal.ACCOUNTS = [{"label": "Only", "email": "o@example.com"}]
        self.addCleanup(lambda: setattr(ourcal, "ACCOUNTS", real_accounts))
        real = ourcal.list_account_events
        ourcal.list_account_events = lambda *a: (
            [], {"message": "m", "setup": True})
        self.addCleanup(lambda: setattr(ourcal, "list_account_events", real))
        _, errors = ourcal._google_collect(
            datetime.datetime(2026, 8, 7, tzinfo=datetime.timezone.utc))
        self.assertEqual(errors,
                         [{"label": "Only", "message": "m", "setup": True}])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_ourcal.TestSetupStatus -v`

Expected: FAIL with `AttributeError: module 'ourcal' has no attribute 'setup_status'`.

- [ ] **Step 3: Change the three error returns in `list_account_events`**

Replace the mismatch return (currently `ourcal.py:800-801`):

```python
        if mismatch:
            # never file another account's events here
            return [], {"message": mismatch, "setup": False}
```

Replace both `except` clauses (currently `ourcal.py:819-822`):

```python
    except FileNotFoundError as e:
        # Setup incomplete, not an auth failure: "re-auth" would mislead. The
        # flag lets the page offer setup without string-matching this text.
        return [], {"message": str(e), "setup": True}
    except Exception as e:  # per-account isolation
        return [], {"message": f"{type(e).__name__} — re-auth or check access",
                    "setup": False}
```

- [ ] **Step 4: Merge the label in `_google_collect`**

Replace the append (currently `ourcal.py:832-833`):

```python
        if err:
            errors.append({"label": a["label"], **err})
```

- [ ] **Step 5: Append `setup_status()` to the TRANSFER section**

```python
def setup_status():
    """What this install actually has — the setup page's diagnostics footer.

    `dataDir` and `android` are here because their being wrong is exactly the
    failure that went unnoticed for a month: nothing on the phone ever
    reported which directory it had resolved.
    """
    return {
        "dataDir": data_dir(),
        "android": is_android(),
        "hasCredentials": os.path.exists(user_path("credentials.json")),
        "accounts": len(ACCOUNTS),
        "accountsFromFile": os.path.exists(user_path("accounts.json")),
        "signedIn": [a["label"] for a in ACCOUNTS
                     if os.path.exists(token_path(a["label"]))],
    }
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python3 -m unittest discover tests -q`

Expected: all pass. `datetime` is already imported at `tests/test_ourcal.py:1`.

- [ ] **Step 7: Commit**

```bash
git add ourcal.py tests/test_ourcal.py
git commit -m "Flag setup failures, and report what a device resolved

A missing credentials.json is not a dead token: only the first has a way
out on the phone. Per-account errors now carry a boolean instead of the
page string-matching the message text.

setup_status() reports the resolved data directory and whether the
Android branch is live. Their being wrong is the failure that went
unnoticed for a month, because nothing on the phone ever said which
directory it had picked."
```

---

### Task 6: The setup page and its routes

**Files:**
- Modify: `ourcal.py` — add `SETUP_PAGE` to the `── TRANSFER ──` section; modify `OurCalHandler.do_GET` (`ourcal.py:1668-1681`) and `do_POST` (`ourcal.py:1683-1695`)
- Test: `tests/test_ourcal.py`

**Interfaces:**
- Consumes: `import_bundle`, `setup_status` (Tasks 3 and 5), `BundleError` (Task 2).
- Produces: `SETUP_PAGE: str`, `import_endpoint(payload: dict) -> dict`. Routes `GET /setup`, `GET /api/status`, `POST /api/import`.

- [ ] **Step 1: Write the failing tests**

```python
class TestSetupRoutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["OURCAL_DEMO"] = "1"
        cls.server = ourcal.make_server(0)
        cls.port = cls.server.server_address[1]
        cls.t = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.t.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def _get(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}") as r:
            return r.status, r.read().decode()

    def _post(self, path, obj):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(obj).encode(), method="POST",
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read().decode())

    def test_setup_page_is_served(self):
        status, body = self._get("/setup")
        self.assertEqual(status, 200)
        self.assertIn("Set up this device", body)
        self.assertIn("/api/import", body)

    def test_status_endpoint_shape(self):
        _, body = self._get("/api/status")
        s = json.loads(body)
        self.assertEqual(sorted(s), ["accounts", "accountsFromFile", "android",
                                     "dataDir", "hasCredentials", "signedIn"])

    def test_import_reports_a_bad_bundle_without_a_500(self):
        status, body = self._post("/api/import",
                                  {"bundle": "nonsense", "passphrase": "x"})
        self.assertEqual(status, 200)
        self.assertFalse(body["ok"])
        self.assertIn("doesn't look like an OurCal bundle", body["error"])

    def test_import_reports_a_wrong_passphrase(self):
        bundle = ourcal.make_bundle({"credentials.json": "{}"}, "right")
        _, body = self._post("/api/import",
                             {"bundle": bundle, "passphrase": "wrong"})
        self.assertFalse(body["ok"])
        self.assertIn("Wrong passphrase", body["error"])

    def test_import_writes_a_real_bundle(self):
        import shutil
        import tempfile
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        real_dir, real_accounts = ourcal.data_dir, ourcal.ACCOUNTS
        ourcal.data_dir = lambda: tmp
        self.addCleanup(lambda: setattr(ourcal, "data_dir", real_dir))
        self.addCleanup(lambda: setattr(ourcal, "ACCOUNTS", real_accounts))
        bundle = ourcal.make_bundle(
            {"credentials.json": '{"installed": {}}',
             "accounts.json": json.dumps(
                 [{"label": "Phone", "email": "p@example.com"}])}, "pw")
        _, body = self._post("/api/import",
                             {"bundle": bundle, "passphrase": "pw"})
        self.assertTrue(body["ok"])
        self.assertEqual(body["written"],
                         ["accounts.json", "credentials.json"])
        self.assertEqual(body["accounts"], 1)
        self.assertEqual(sorted(os.listdir(tmp)),
                         ["accounts.json", "credentials.json"])


class TestSetupPageStructure(unittest.TestCase):
    def test_page_has_the_markers_it_needs(self):
        for marker in ['id="bundle"', 'id="passphrase"', 'id="doImport"',
                       'id="result"', 'id="diag"', "/api/import",
                       "/api/status", "--export", "prefers-color-scheme"]:
            self.assertIn(marker, ourcal.SETUP_PAGE, f"missing {marker!r}")

    def test_page_reports_the_resolved_data_dir(self):
        # The diagnostic that would have made the seam bug obvious.
        self.assertIn("dataDir", ourcal.SETUP_PAGE)
        self.assertIn("android", ourcal.SETUP_PAGE)
```

`threading` and `urllib.request` are imported at `tests/test_ourcal.py:1396`, above where this class is appended, so no new imports are needed.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_ourcal.TestSetupRoutes -v`

Expected: FAIL — `/setup` returns 404 and `ourcal.SETUP_PAGE` does not exist.

- [ ] **Step 3: Add `SETUP_PAGE` to the TRANSFER section**

```python
SETUP_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OurCal — set up this device</title>
<style>
  :root{--bg:#f5f6f8;--card:#fff;--text:#161a1d;--muted:#67717b;--border:#e4e7eb;
        --accent:#2a78d6;--danger:#d64545;--ok:#1baf7a;color-scheme:light}
  @media (prefers-color-scheme:dark){:root{--bg:#0f1419;--card:#171d24;
        --text:#e6eaed;--muted:#98a2ac;--border:#29323b;--accent:#5b9cf0;
        --danger:#f07a7a;--ok:#3fd39c;color-scheme:dark}}
  *{box-sizing:border-box}html,body{margin:0}
  body{background:var(--bg);color:var(--text);font-size:15px;line-height:1.45;
       font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
  .wrap{max-width:640px;margin:0 auto;padding:20px 16px 60px}
  h1{font-size:20px;margin:0 0 4px;font-weight:750;letter-spacing:-.02em}
  a{color:var(--accent)}
  .sub{color:var(--muted);font-size:13px;margin-bottom:20px}
  .card{background:var(--card);border:1px solid var(--border);border-radius:12px;
        padding:14px;margin-bottom:14px}
  label{display:block;font-size:13px;font-weight:600;margin:0 0 6px}
  textarea,input{width:100%;font:inherit;padding:9px 11px;border-radius:9px;
        border:1px solid var(--border);background:var(--bg);color:var(--text)}
  textarea{min-height:120px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
        font-size:12px;resize:vertical;word-break:break-all}
  code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;
        background:var(--bg);border:1px solid var(--border);border-radius:6px;
        padding:2px 5px}
  button{font:inherit;cursor:pointer;border-radius:9px;padding:10px 16px;
        border:1px solid var(--accent);background:var(--accent);color:#fff;
        font-weight:600;margin-top:12px}
  button:disabled{opacity:.5;cursor:not-allowed}
  .msg{margin-top:12px;font-size:13px;padding:10px 12px;border-radius:9px;
        border:1px solid var(--border);border-left:3px solid var(--muted)}
  .msg.bad{border-left-color:var(--danger)}
  .msg.good{border-left-color:var(--ok)}
  .diag{color:var(--muted);font-size:12px;margin-top:18px;
        font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
        word-break:break-all}
</style>
</head>
<body><div class="wrap">
  <h1>Set up this device</h1>
  <div class="sub"><a href="/">&larr; back to the agenda</a></div>

  <div class="card">
    <label>1 &middot; On your computer</label>
    <div style="font-size:13px;color:var(--muted)">
      Run <code>./ourcal.py --export | pbcopy</code>, choose a passphrase, then
      send yourself the bundle. It is encrypted, but it carries live Google
      refresh tokens &mdash; treat it as a secret.
    </div>
  </div>

  <div class="card">
    <label for="bundle">2 &middot; Bundle</label>
    <textarea id="bundle" placeholder="ourcal1&hellip;" spellcheck="false"
              autocapitalize="off" autocorrect="off"></textarea>
    <label for="passphrase" style="margin-top:12px">3 &middot; Passphrase</label>
    <input id="passphrase" type="password" autocomplete="off">
    <button id="doImport">Import</button>
    <div id="result"></div>
  </div>

  <div class="diag" id="diag">checking this device&hellip;</div>
</div>
<script>
function esc(s){return String(s).replace(/[&<>"]/g,c=>(
  {"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;"}[c]));}

function diag(s){
  document.getElementById("diag").innerHTML =
    "data dir: " + esc(s.dataDir) + "<br>" +
    "android branch: " + (s.android ? "live" : "not active") + "<br>" +
    "credentials.json: " + (s.hasCredentials ? "present" : "missing") + "<br>" +
    "accounts: " + s.accounts + (s.accountsFromFile ? "" : " (placeholders)") +
    "<br>signed in: " + (s.signedIn.length ? esc(s.signedIn.join(", ")) : "none");
}

function refresh(){
  fetch("/api/status").then(r=>r.json()).then(diag)
    .catch(e=>{document.getElementById("diag").textContent =
      "could not read device status: " + e;});
}

document.getElementById("doImport").onclick = function(){
  const btn = this, out = document.getElementById("result");
  btn.disabled = true;
  out.className = "msg";
  out.textContent = "Importing… this takes a moment (the passphrase is "
                  + "deliberately slow to check).";
  fetch("/api/import", {method:"POST",
      headers:{"Content-Type":"application/json"},
      body: JSON.stringify({
        bundle: document.getElementById("bundle").value,
        passphrase: document.getElementById("passphrase").value})})
    .then(r=>r.json())
    .then(function(d){
      btn.disabled = false;
      if(d.ok){
        out.className = "msg good";
        out.innerHTML = "Imported " + d.written.length + " file(s): "
          + esc(d.written.join(", ")) + ".<br>" + d.accounts
          + " account(s) configured. <a href=\"/\">Open the agenda</a>.";
        document.getElementById("bundle").value = "";
        document.getElementById("passphrase").value = "";
      } else {
        out.className = "msg bad";
        out.textContent = d.error || "Import failed.";
      }
      refresh();
    })
    .catch(function(e){
      btn.disabled = false;
      out.className = "msg bad";
      out.textContent = "Import failed: " + e;
    });
};

refresh();
</script>
</body></html>"""


def import_endpoint(payload):
    """POST /api/import. A bad bundle is a normal answer, not a 500."""
    try:
        return import_bundle(payload.get("bundle", ""),
                             payload.get("passphrase", ""))
    except BundleError as e:
        return {"ok": False, "error": str(e)}
```

- [ ] **Step 4: Add the routes**

In `do_GET`, insert two branches after the `/api/events` branch and before the `else`:

```python
            elif self.path == "/setup" or self.path.startswith("/setup?"):
                self._send(200, SETUP_PAGE, "text/html; charset=utf-8")
            elif self.path == "/api/status":
                self._send(200, json.dumps(setup_status()))
```

In `do_POST`, extend the routes dict:

```python
            routes = {"/api/create": create_event, "/api/delete": delete_events,
                      "/api/update": update_events, "/api/import": import_endpoint}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -m unittest discover tests -q`

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add ourcal.py tests/test_ourcal.py
git commit -m "Serve a setup page that accepts a pasted bundle

GET /setup, GET /api/status and POST /api/import, following the existing
static-page-plus-JSON-endpoint pattern. A bad bundle is a normal answer
with an explanation, not a 500.

The same page is served on both platforms, which is what makes the whole
feature testable on a desktop with no phone in the loop. Its footer
reports the resolved data directory and whether the Android branch is
live — the diagnostic whose absence hid the seam bug."
```

---

### Task 7: Offer setup from the agenda's error banner

**Files:**
- Modify: `ourcal.py:1265` (banner render) and the footer area of `PAGE`
- Test: `tests/test_ourcal.py` (extend `TestPageStructure`)

**Interfaces:**
- Consumes: the `setup` flag on error entries (Task 5), `/setup` (Task 6).
- Produces: no Python API. Page markers `setup-link` and `href="/setup"`.

- [ ] **Step 1: Write the failing tests**

Add these methods to the existing `TestPageStructure` class:

```python
    def test_banner_collapses_when_the_device_is_unset_up(self):
        # Four accounts with no credentials.json produced four identical walls
        # of text naming a path the user cannot reach. One banner with a way
        # out replaces them.
        self.assertIn("errs.every(e=>e.setup)", ourcal.PAGE)
        self.assertIn("isn't set up on this device yet", ourcal.PAGE)
        self.assertIn("Set up this device", ourcal.PAGE)

    def test_per_account_banners_survive_for_other_failures(self):
        # One expired token must not hide behind a "not set up" message.
        self.assertIn("Couldn't refresh", ourcal.PAGE)

    def test_setup_stays_reachable_after_setup_succeeds(self):
        # The banner disappears once it works; re-importing after a revoked
        # token must not require breaking the app first.
        self.assertIn('class="setup-link" href="/setup"', ourcal.PAGE)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_ourcal.TestPageStructure -v`

Expected: three FAILs on the missing markers.

- [ ] **Step 3: Replace the banner renderer**

Replace `ourcal.py:1265` (the single `banner.innerHTML=...` line) with:

```javascript
  const banner=document.getElementById("banner");
  const errs=DATA.errors||[];
  // Every account failing for the same missing credentials.json is one
  // problem with one fix, not N problems. Any other mix keeps the
  // per-account banners: an expired token must not hide behind "not set up".
  banner.innerHTML = (errs.length && errs.every(e=>e.setup))
    ? `<div class="banner">⚠️ OurCal isn't set up on this device yet. <a class="setup-link" href="/setup">Set up this device</a></div>`
    : errs.map(e=>`<div class="banner">⚠️ Couldn't refresh <b>${esc(e.label)}</b> — ${esc(e.message)}</div>`).join("");
```

- [ ] **Step 4: Add the permanent footer link**

Find the closing `</div>` of `<div class="wrap">` in `PAGE` and insert immediately before it:

```html
<div style="text-align:center;margin-top:28px;font-size:12px">
  <a class="setup-link" href="/setup" style="color:var(--muted)">Set up this device</a>
</div>
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -m unittest discover tests -q`

Expected: all pass.

- [ ] **Step 6: Verify by eye in demo mode**

```bash
OURCAL_DEMO=1 python3 ourcal.py
```

Open the printed URL, confirm the agenda still renders and the footer "Set up this device" link opens a working setup page. Ctrl-C when done.

- [ ] **Step 7: Commit**

```bash
git add ourcal.py tests/test_ourcal.py
git commit -m "Offer setup from the banner instead of naming an unreachable path

A phone with no credentials.json showed one banner per account, each
naming a directory Android does not let the user reach. When every
account fails for that same reason it is one problem with one fix, so
the banners collapse into a single one with a button.

Any other mix keeps the per-account banners — an expired token must not
hide behind a 'not set up' message. A permanent footer link keeps /setup
reachable once the banner is gone, so re-importing after a revoked token
does not require breaking the app first."
```

---

### Task 8: Document the Android path

**Files:**
- Modify: `README.md` (after the "Connect your accounts" section, around `README.md:88`), `SETUP_GUIDE.md` (new section after the troubleshooting entries)

**Interfaces:**
- Consumes: `--export` and `/setup` from Tasks 4 and 6.
- Produces: no code.

- [ ] **Step 1: Add an Android section to `README.md`**

Insert after the line `Full walkthrough: **[SETUP_GUIDE.md](SETUP_GUIDE.md)**.`:

```markdown
### On Android

The Google Cloud step happens on a computer — a phone cannot realistically
create a project and download a client. Move the finished setup across instead:

```bash
./ourcal.py --export | pbcopy      # choose a passphrase when asked
```

Send yourself the bundle, open OurCal on the phone, tap **Set up this device**,
paste it in, and enter the same passphrase. Your accounts appear without a
restart.

The bundle is encrypted, but it carries live Google refresh tokens. Use a
passphrase you would use for a password, and delete the message afterwards.

Android 11+ hides `Android/data` from every file manager and from MTP, so
copying the files onto the phone directly is not possible — pasting is not a
workaround for a missing cable, it is the only supported route.
```

- [ ] **Step 2: Add a troubleshooting entry to `SETUP_GUIDE.md`**

Append to the troubleshooting section:

```markdown
**On Android, every account says `credentials.json is missing`.**
The device has not been set up. Tap **Set up this device** in the banner, or
open `/setup` from the footer link, and paste a bundle from `./ourcal.py
--export`. The footer of that page shows the directory the app resolved: if it
contains `chaquopy/AssetFinder`, the Android branch is not active and the app
is running desktop code paths — rebuild from a source tree that includes the
`is_android()` interpreter probe.

**The setup page rejects my bundle.**
`Wrong passphrase, or the bundle was altered in transit` means exactly that,
and the two cases are indistinguishable on purpose. Re-export and re-paste,
taking care that nothing wrapped or truncated the text — some chat apps insert
line breaks into long strings.
```

- [ ] **Step 3: Verify the suite is untouched**

Run: `python3 -m unittest discover tests -q`

Expected: all pass. Docs only.

- [ ] **Step 4: Commit**

```bash
git add README.md SETUP_GUIDE.md
git commit -m "Document the Android setup path

The Google Cloud step happens on a computer, so the finished setup moves
across as a pasted bundle. Says plainly that Android 11+ makes copying
the files impossible, so this is the only route rather than a workaround
for a missing cable, and that the bundle carries live refresh tokens.

Troubleshooting names the AssetFinder path as the symptom of an inactive
Android branch, since that is what a broken build actually looks like."
```

---

## Verification before calling this done

- [ ] `python3 -m unittest discover tests -q` — all pass
- [ ] `git status --short` shows no change to `credentials.json`, `accounts.json` or any `token_*.json`
- [ ] `./packaging/build-android.sh` completes and produces an APK
- [ ] Install it, open OurCal, tap **Set up this device**, and confirm the diagnostics footer reads a path **without** `chaquopy/AssetFinder` — that is Part 0 proven on the device
- [ ] Paste a real bundle and confirm the agenda fills with all four accounts

The last two are the whole point and cannot be checked from a desktop.
