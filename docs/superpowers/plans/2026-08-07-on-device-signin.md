# On-device Sign-in Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let someone who installs the APK add their own Google accounts and sign in entirely on the phone, with no computer and no pasted bundle.

**Architecture:** Four additions to `ourcal.py`, in an order that never leaves the app in a worse state than it started. A per-session token first (it changes every HTTP test, so later tasks write tests that already carry it). Then the OAuth client shipped inside the APK, an accounts editor, a sign-in endpoint that runs off the request thread, and finally the switch that stops `creds_for` from opening browsers as a side effect of loading the agenda.

**Tech Stack:** Python 3 standard library only — `secrets`, `threading`, `pkgutil`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-07-on-device-signin-design.md`

## Global Constraints

- **All Python goes in `ourcal.py`.** `packaging/build-android.sh:27` copies exactly that one file into the Android project; new modules break the Android build.
- **Standard library only.** No new entries in `packaging/android/pyproject.toml`.
- **Both pages must stay self-contained** — no CDN links, external stylesheets, fonts or scripts. They are served inside an Android WebView with no guaranteed connectivity.
- **⚠️ Tests must never write to the real data directory.** In a checkout `data_dir()` returns `APP_DIR` — the repo — holding the developer's live `credentials.json`, `accounts.json` and four `token_*.json`. `CONTRIBUTING.md:49` records a near-miss. `tests/test_ourcal.py` has a `_TmpData` mixin that redirects `ourcal.data_dir`; **every test that writes must use it.** Before each commit run `git status --short` and confirm none of those files is modified.
- Test runner: `python3 -m unittest discover tests -q` from the repo root. Baseline: **293 pass, 1 skipped**.
- `git add` only the files a task names. `packaging/android/build/`, `.build-venv/` and `src/ourcal/core.py` are generated and git-ignored — never stage them.
- New test classes go immediately before `if __name__ == "__main__":` in `tests/test_ourcal.py`, unless a task says to extend an existing class.
- Error strings shown to users are specified verbatim in each task — copy them exactly; tests assert on them.
- Work on branch `android-setup-import`, or a new branch off it. `credentials.json` must never be committed.

---

### Task 1: Per-session token on every `/api/*` request

Do this first. It changes every existing HTTP test, so doing it now means the later tasks write tests that already carry the header instead of retrofitting them.

**Files:**
- Modify: `ourcal.py` — add `SESSION_TOKEN` near the other constants; add `_api_token_ok` to `OurCalHandler`; call it in `do_GET` and `do_POST`; substitute the token into both page strings; add a token-aware `api()` helper to both pages' JavaScript
- Test: `tests/test_ourcal.py`

**Interfaces:**
- Consumes: `_local_caller()` (exists, `ourcal.py:2127`), `PAGE`, `SETUP_PAGE`.
- Produces: `SESSION_TOKEN: str`; `OurCalHandler._api_token_ok() -> bool`; a JavaScript `api(path, opts)` helper in both pages that all `/api/*` calls go through.

- [ ] **Step 1: Write the failing tests**

Append a new class before `if __name__ == "__main__":`:

```python
class TestSessionToken(_TmpData, unittest.TestCase):
    """The hole _local_caller could not close.

    Host and Origin only constrain browsers. A native process sends no
    Origin and is accepted — on Android, where loopback is not isolated
    between apps, that let any installed app POST credentials into this
    app's data directory. A token minted per run and only ever present
    inside pages this server itself served is the thing such a caller
    cannot guess.
    """

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

    def setUp(self):
        self._tmp_data()

    def _req(self, path, token=None, method="GET", body=None):
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["X-OurCal-Token"] = token
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(body).encode() if body is not None else None,
            method=method, headers=headers)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def test_api_without_the_token_is_refused(self):
        status, _ = self._req("/api/events")
        self.assertEqual(status, 403)

    def test_api_with_a_wrong_token_is_refused(self):
        status, _ = self._req("/api/events", token="not-the-token")
        self.assertEqual(status, 403)

    def test_api_with_the_right_token_succeeds(self):
        status, body = self._req("/api/events", token=ourcal.SESSION_TOKEN)
        self.assertEqual(status, 200)
        self.assertIn("events", json.loads(body))

    def test_a_post_without_the_token_is_refused(self):
        status, _ = self._req("/api/import", method="POST",
                              body={"bundle": "x", "passphrase": "y"})
        self.assertEqual(status, 403)

    def test_navigations_do_not_need_the_token(self):
        # This is how the token reaches the page in the first place.
        for path in ("/", "/setup"):
            status, _ = self._req(path)
            self.assertEqual(status, 200, path)

    def test_both_pages_carry_the_real_token_not_the_placeholder(self):
        for path in ("/", "/setup"):
            _, body = self._req(path)
            self.assertIn(ourcal.SESSION_TOKEN, body, path)
            self.assertNotIn("__SESSION_TOKEN__", body, path)

    def test_the_token_is_not_trivially_guessable(self):
        self.assertGreaterEqual(len(ourcal.SESSION_TOKEN), 32)
        self.assertNotIn(ourcal.SESSION_TOKEN, ("", "None", "token"))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_ourcal.TestSessionToken -v`

Expected: FAIL — `ourcal.SESSION_TOKEN` does not exist, and the unauthenticated `/api/events` currently returns 200.

- [ ] **Step 3: Add the token and the guard**

Add `import secrets` to the imports at the top of `ourcal.py` (alphabetical, after `re`).

Add next to the other constants, just below `PORT = 8756`:

```python
# Minted per run and only ever present inside pages this server itself
# served. Host and Origin constrain browsers; this constrains everything
# else, including another app on the same Android device.
SESSION_TOKEN = secrets.token_urlsafe(32)
```

Add to `OurCalHandler`, immediately after `_local_caller`:

```python
    def _api_token_ok(self):
        """Whether an /api/* request carries this run's session token.

        _local_caller closes the two browser-shaped holes; this closes the
        one it cannot. A native caller on the same device sends no Origin
        and passes that check, which on Android meant any installed app
        could POST credentials into this app's data directory. It cannot
        guess this token, because the only place the token appears is
        inside a page this server served.

        Navigations are exempt: / and /setup are how the token reaches the
        page, and neither has a side effect.
        """
        if not self.path.startswith("/api/"):
            return True
        return secrets.compare_digest(
            self.headers.get("X-OurCal-Token") or "", SESSION_TOKEN)
```

In **both** `do_GET` and `do_POST`, immediately after the existing `_local_caller` check, add:

```python
            if not self._api_token_ok():
                self._send(403, json.dumps({"error": "forbidden"}))
                return
```

- [ ] **Step 4: Substitute the token into both pages**

In `do_GET`, change the `/` branch to also substitute the token:

```python
                html = (PAGE.replace("__POLL_MS__", str(POLL_MINUTES * 60000))
                            .replace("__SESSION_TOKEN__", SESSION_TOKEN))
```

and the `/setup` branch:

```python
                self._send(200,
                           SETUP_PAGE.replace("__SESSION_TOKEN__", SESSION_TOKEN),
                           "text/html; charset=utf-8")
```

- [ ] **Step 5: Route both pages' fetches through a token-aware helper**

In `PAGE`'s `<script>`, immediately after the `const POLL_MS = __POLL_MS__;` line, add:

```javascript
const TOKEN = "__SESSION_TOKEN__";
function api(path, opts){
  opts = opts || {};
  opts.headers = Object.assign({}, opts.headers || {},
                               {"X-OurCal-Token": TOKEN});
  return fetch(path, opts);
}
```

Then change every `fetch("/api/` in `PAGE` to `api("/api/`. There are four: the events poll, and the delete, update and create posts. Verify with `grep -c 'fetch("/api/' ourcal.py` — it must reach 0 inside `PAGE`.

In `SETUP_PAGE`'s `<script>`, add the same two declarations at the top of the script block, and change both its `fetch("/api/status"` and `fetch("/api/import"` calls to `api(`.

- [ ] **Step 6: Add the header to every existing HTTP test helper**

Each HTTP test class defines its own `_get`/`_post`. Add the header to each one's `headers` dict:

```python
            headers={"Content-Type": "application/json",
                     "X-OurCal-Token": ourcal.SESSION_TOKEN},
```

The classes to update are `TestHttp`, `TestDeleteEndpoint`, `TestUpdateEndpoint`, `TestSetupRoutes` and `TestCallerAuth`. Find them with:

```bash
grep -n "class Test.*unittest.TestCase" tests/test_ourcal.py | grep -i "http\|endpoint\|routes\|auth"
```

`TestCallerAuth` deliberately probes rejection — its cross-origin and foreign-Host cases must still expect 403, and its *accepted* cases now need the token too.

- [ ] **Step 7: Run the full suite**

Run: `python3 -m unittest discover tests -q`

Expected: all pass. If an HTTP test fails with 403, it is missing the header from Step 6.

- [ ] **Step 8: Commit**

```bash
git add ourcal.py tests/test_ourcal.py
git commit -m "Require a per-run token on every /api/ request

_local_caller closes two browser-shaped holes: a foreign Host defeats
DNS rebinding, and a foreign Origin defeats the cross-site POST that
text/plain smuggles past preflight. Neither constrains a caller that is
not a browser — a native process sends no Origin and was accepted, which
on Android, where loopback is not isolated between apps, let any
installed app POST an attacker's credentials.json into this app's data
directory. That was demonstrated against a live server.

The token is minted per run and appears only inside pages this server
served, so a page on another site cannot forge it and another process
cannot guess it. Navigations stay exempt because they are how it reaches
the page and carry no side effects."
```

---

### Task 2: Ship the OAuth client inside the APK

**Files:**
- Modify: `ourcal.py` — add `bundled_credentials()` to the `── TRANSFER ──` section; change `creds_for`'s client resolution
- Modify: `packaging/build-android.sh` — copy `credentials.json` into the app resources when present
- Test: `tests/test_ourcal.py`

**Interfaces:**
- Consumes: `data_dir()`, `user_path()`, `APP_DIR`.
- Produces: `bundled_credentials() -> str | None` returning the shipped client's JSON text; `creds_for` resolves pasted-before-bundled.

- [ ] **Step 1: Write the failing tests**

```python
class TestBundledCredentials(_TmpData, unittest.TestCase):
    """The APK ships an OAuth client so a fresh install can sign in with
    no computer. A pasted client must still win, so bring-your-own keeps
    working for anyone who prefers their own Google Cloud project."""

    def test_none_when_nothing_is_bundled(self):
        self._tmp_data()
        self.assertIsNone(ourcal.bundled_credentials())

    def test_returns_the_bundled_client_when_present(self):
        self._tmp_data()
        real = ourcal.bundled_credentials
        ourcal.bundled_credentials = lambda: '{"installed": {"client_id": "B"}}'
        self.addCleanup(lambda: setattr(ourcal, "bundled_credentials", real))
        self.assertIn("B", ourcal.bundled_credentials())

    def test_a_pasted_client_wins_over_the_bundled_one(self):
        tmp = self._tmp_data()
        with open(os.path.join(tmp, "credentials.json"), "w",
                  encoding="utf-8") as f:
            f.write('{"installed": {"client_id": "PASTED"}}')
        real = ourcal.bundled_credentials
        ourcal.bundled_credentials = lambda: '{"installed": {"client_id": "BUNDLED"}}'
        self.addCleanup(lambda: setattr(ourcal, "bundled_credentials", real))
        self.assertIn("PASTED", ourcal.client_config_text())
        self.assertNotIn("BUNDLED", ourcal.client_config_text())

    def test_falls_back_to_the_bundled_client(self):
        self._tmp_data()          # nothing pasted
        real = ourcal.bundled_credentials
        ourcal.bundled_credentials = lambda: '{"installed": {"client_id": "BUNDLED"}}'
        self.addCleanup(lambda: setattr(ourcal, "bundled_credentials", real))
        self.assertIn("BUNDLED", ourcal.client_config_text())

    def test_raises_the_existing_message_when_there_is_no_client_at_all(self):
        self._tmp_data()
        real = ourcal.bundled_credentials
        ourcal.bundled_credentials = lambda: None
        self.addCleanup(lambda: setattr(ourcal, "bundled_credentials", real))
        with self.assertRaises(FileNotFoundError) as cm:
            ourcal.client_config_text()
        self.assertIn("credentials.json is missing", str(cm.exception))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_ourcal.TestBundledCredentials -v`

Expected: FAIL with `AttributeError: module 'ourcal' has no attribute 'bundled_credentials'`.

- [ ] **Step 3: Add both functions to the TRANSFER section**

Append after `setup_status`:

```python
BUNDLED_CLIENT = "resources/bundled_credentials.json"


def bundled_credentials():
    """The OAuth client shipped inside the app, or None.

    credentials.json is git-ignored and must stay that way, so it reaches
    the APK from outside the repo — build-android.sh copies it in, and CI
    writes it from a secret. An APK built without one still works; it is
    simply paste-only, the same way build-app.sh degrades without a
    signing identity.

    pkgutil.get_data is tried first because it works against Chaquopy's
    loader, which a plain path does not always reach; tz() relies on the
    same mechanism for tzdata.
    """
    try:
        import pkgutil
        raw = pkgutil.get_data(__package__ or "ourcal", BUNDLED_CLIENT)
        if raw:
            return raw.decode("utf-8")
    except Exception:
        pass
    try:
        with open(os.path.join(APP_DIR, BUNDLED_CLIENT), encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def client_config_text():
    """The OAuth client this install should use.

    A pasted credentials.json always wins over the bundled one, so anyone
    who prefers their own Google Cloud project keeps working exactly as
    before — the shipped client is a fallback, never an override.
    """
    path = user_path("credentials.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return f.read()
    bundled = bundled_credentials()
    if bundled:
        return bundled
    raise FileNotFoundError(
        f"credentials.json is missing from {data_dir()} — "
        "complete Steps 1-4 of SETUP_GUIDE.md")
```

- [ ] **Step 4: Use it in `creds_for`**

In `creds_for` (`ourcal.py:777`), replace the block that reads `cred_file` and raises, and the `InstalledAppFlow.from_client_secrets_file(cred_file, SCOPES)` line, with:

```python
        flow = InstalledAppFlow.from_client_config(
            json.loads(client_config_text()), SCOPES)
```

Delete the now-unused `cred_file` lookup and its `FileNotFoundError` — `client_config_text()` raises the identical message. Keep the `print` that names the account.

- [ ] **Step 5: Teach the build script to bundle it**

In `packaging/build-android.sh`, immediately after the line that copies `ourcal.py` to `core.py`, add:

```bash
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
```

Confirm `packaging/android/src/ourcal/resources/` is already git-ignored:

```bash
git check-ignore -v packaging/android/src/ourcal/resources/bundled_credentials.json
```

Expected: a match. If it does not match, **stop and report** — the client must never be committable.

- [ ] **Step 6: Run the full suite**

Run: `python3 -m unittest discover tests -q`

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add ourcal.py tests/test_ourcal.py packaging/build-android.sh
git commit -m "Ship the OAuth client inside the APK

A downloader had no way to obtain a credentials.json without creating
their own Google Cloud project on a computer, which made the APK
installable and unusable. The client now ships inside it.

It reaches the build from outside the repo, the way APPLE_SIGN_ID
reaches the macOS build: copied from the working tree locally, written
from a secret in CI, and absent it still builds a paste-only APK.
credentials.json stays git-ignored and the bundled copy lands in an
already-ignored directory, so it can never be committed.

A pasted client still wins over the bundled one, so anyone using their
own Google Cloud project is unaffected."
```

---

### Task 3: Accounts editor

**Files:**
- Modify: `ourcal.py` — add `add_account`, `remove_account` and their endpoints; extend `SETUP_PAGE` with an accounts section
- Test: `tests/test_ourcal.py`

**Interfaces:**
- Consumes: `parse_accounts()` (`ourcal.py:109`), `write_user_files()`, `reload_accounts()`, `token_path()`, `ACCOUNTS`.
- Produces: `add_account(label, email) -> dict`, `remove_account(label) -> dict`, `accounts_endpoint(payload) -> dict`; routes `POST /api/accounts` and `POST /api/accounts/remove`.

**Note on the route:** removal uses `POST /api/accounts/remove` rather than an HTTP `DELETE`, because `OurCalHandler` implements only `do_GET` and `do_POST` and adding a `do_DELETE` for one call is not worth the surface.

- [ ] **Step 1: Write the failing tests**

```python
class TestAccountsEditor(_TmpData, unittest.TestCase):
    """accounts.json is a text file you edit by hand. On a phone you
    cannot, so a downloader could never name an account to sign in to."""

    def setUp(self):
        self.tmp = self._tmp_data()
        real = ourcal.ACCOUNTS
        self.addCleanup(lambda: setattr(ourcal, "ACCOUNTS", real))
        ourcal.ACCOUNTS = [{"label": "One", "email": "one@example.com"}]
        with open(os.path.join(self.tmp, "accounts.json"), "w",
                  encoding="utf-8") as f:
            json.dump(ourcal.ACCOUNTS, f)

    def test_add_appends_and_reloads(self):
        r = ourcal.add_account("Two", "two@example.com")
        self.assertTrue(r["ok"])
        self.assertEqual([a["label"] for a in ourcal.ACCOUNTS], ["One", "Two"])

    def test_add_rejects_a_blank_label(self):
        r = ourcal.add_account("   ", "two@example.com")
        self.assertFalse(r["ok"])
        self.assertEqual(len(ourcal.ACCOUNTS), 1)

    def test_add_rejects_a_bad_address(self):
        r = ourcal.add_account("Two", "not-an-address")
        self.assertFalse(r["ok"])
        self.assertEqual(len(ourcal.ACCOUNTS), 1)

    def test_add_rejects_a_label_that_collides_after_slugging(self):
        # "one!" slugs to "one", which would share One's token file.
        r = ourcal.add_account("one!", "two@example.com")
        self.assertFalse(r["ok"])
        self.assertEqual(len(ourcal.ACCOUNTS), 1)

    def test_remove_deletes_the_account_and_its_token(self):
        ourcal.add_account("Two", "two@example.com")
        tok = ourcal.token_path("Two")
        with open(tok, "w", encoding="utf-8") as f:
            f.write("{}")
        r = ourcal.remove_account("Two")
        self.assertTrue(r["ok"])
        self.assertEqual([a["label"] for a in ourcal.ACCOUNTS], ["One"])
        self.assertFalse(os.path.exists(tok))   # no live refresh token left

    def test_remove_refuses_the_last_account(self):
        # An empty list makes parse_accounts return None, load_accounts
        # return None, and `or ACCOUNTS` restore the Personal/Work
        # placeholders — the app would show two accounts nobody added.
        r = ourcal.remove_account("One")
        self.assertFalse(r["ok"])
        self.assertIn("at least one account", r["error"])
        self.assertEqual([a["label"] for a in ourcal.ACCOUNTS], ["One"])

    def test_remove_an_unknown_label_is_refused(self):
        r = ourcal.remove_account("Nope")
        self.assertFalse(r["ok"])
        self.assertEqual(len(ourcal.ACCOUNTS), 1)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_ourcal.TestAccountsEditor -v`

Expected: FAIL with `AttributeError: module 'ourcal' has no attribute 'add_account'`.

- [ ] **Step 3: Add both operations to the TRANSFER section**

```python
def add_account(label, email):
    """Append an account and reload. Validation is parse_accounts'.

    The whole list is validated, not just the new entry, because the rules
    that matter are relational: a label that collides after slugging would
    silently share another account's token file.
    """
    label, email = str(label or "").strip(), str(email or "").strip()
    proposed = [dict(a) for a in ACCOUNTS] + [{"label": label, "email": email}]
    if parse_accounts(proposed) is None:
        return {"ok": False,
                "error": "That account is invalid or already added."}
    write_user_files({"accounts.json": json.dumps(proposed, indent=2)})
    reload_accounts()
    return {"ok": True, "accounts": len(ACCOUNTS)}


def remove_account(label):
    """Remove an account and delete its token file.

    One action, not two: an account you removed must not leave a live
    refresh token sitting on the device.
    """
    remaining = [dict(a) for a in ACCOUNTS if a["label"] != label]
    if len(remaining) == len(ACCOUNTS):
        return {"ok": False, "error": f"No account named {label}."}
    if not remaining:
        # parse_accounts rejects an empty list, so load_accounts would
        # return None and `or ACCOUNTS` would restore the placeholders —
        # the app would show two accounts nobody added.
        return {"ok": False,
                "error": "OurCal needs at least one account — "
                         "add the replacement first."}
    write_user_files({"accounts.json": json.dumps(remaining, indent=2)})
    try:
        os.remove(token_path(label))
    except OSError:
        pass          # never signed in, or already gone
    reload_accounts()
    return {"ok": True, "accounts": len(ACCOUNTS)}


def accounts_endpoint(payload):
    """POST /api/accounts — add an account."""
    return add_account(payload.get("label", ""), payload.get("email", ""))


def accounts_remove_endpoint(payload):
    """POST /api/accounts/remove — remove one, and its token."""
    return remove_account(payload.get("label", ""))
```

- [ ] **Step 4: Register the routes**

Extend `do_POST`'s `routes` dict with:

```python
                      "/api/accounts": accounts_endpoint,
                      "/api/accounts/remove": accounts_remove_endpoint,
```

- [ ] **Step 5: Add the accounts section to `SETUP_PAGE`**

Insert a card above the existing bundle card:

```html
  <div class="card">
    <label>Accounts</label>
    <div id="accts" style="font-size:13px;color:var(--muted)">loading&hellip;</div>
    <label for="a-label" style="margin-top:12px">Add an account</label>
    <input id="a-label" placeholder="Work" autocapitalize="words">
    <input id="a-email" type="email" placeholder="you@gmail.com"
           autocapitalize="off" autocorrect="off" style="margin-top:8px">
    <button id="doAdd">Add</button>
    <div id="acctMsg"></div>
  </div>
```

and in the script, extend `diag()`'s refresh to also render the list, plus the two handlers:

```javascript
function renderAccounts(s){
  const box = document.getElementById("accts");
  if(!s.accounts){ box.textContent = "No accounts yet."; return; }
  box.innerHTML = (s.accountLabels || []).map(function(l){
    const on = (s.signedIn || []).indexOf(l) >= 0;
    return '<div style="display:flex;align-items:center;gap:8px;padding:4px 0">'
      + '<span style="flex:1">' + esc(l) + '</span>'
      + '<span>' + (on ? "✓ signed in" : "not signed in") + '</span>'
      + '<button class="rm" data-label="' + esc(l) + '"'
      + ' style="padding:4px 8px;font-size:12px">Remove</button></div>';
  }).join("");
  box.querySelectorAll(".rm").forEach(function(b){
    b.onclick = function(){
      api("/api/accounts/remove", {method:"POST",
          headers:{"Content-Type":"application/json"},
          body: JSON.stringify({label: b.dataset.label})})
        .then(r=>r.json()).then(function(d){
          document.getElementById("acctMsg").className = d.ok ? "msg good" : "msg bad";
          document.getElementById("acctMsg").textContent = d.ok
            ? "Removed." : (d.error || "Could not remove.");
          refresh();
        });
    };
  });
}

document.getElementById("doAdd").onclick = function(){
  const out = document.getElementById("acctMsg");
  api("/api/accounts", {method:"POST",
      headers:{"Content-Type":"application/json"},
      body: JSON.stringify({label: document.getElementById("a-label").value,
                            email: document.getElementById("a-email").value})})
    .then(r=>r.json()).then(function(d){
      out.className = d.ok ? "msg good" : "msg bad";
      out.textContent = d.ok ? "Added." : (d.error || "Could not add.");
      if(d.ok){ document.getElementById("a-label").value = "";
                document.getElementById("a-email").value = ""; }
      refresh();
    });
};
```

Call `renderAccounts(s)` from `diag(s)`.

- [ ] **Step 6: Add `accountLabels` to `setup_status()`**

The page needs the labels, not just the count. Add to the returned dict:

```python
        "accountLabels": [a["label"] for a in ACCOUNTS],
```

**Update the existing exact-key assertion** in `tests/test_ourcal.py` — it asserts a sorted list of the status keys and must now include `accountLabels`. Keep it an exact-set assertion; do not loosen it to a subset check.

- [ ] **Step 7: Run the full suite**

Run: `python3 -m unittest discover tests -q`

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add ourcal.py tests/test_ourcal.py
git commit -m "Let accounts be added and removed on the device

accounts.json is a text file you edit by hand, which is impossible on a
phone — so a downloader could never even name an account to sign in to.

Validation goes through parse_accounts on the whole list rather than the
new entry alone, because the rule that matters is relational: a label
colliding after slugging would silently share another account's token
file. Removal deletes the token too, so a removed account cannot leave a
live refresh token on the device.

Removing the last account is refused. An empty list makes parse_accounts
return None and `load_accounts(...) or ACCOUNTS` restore the placeholder
Personal/Work entries — the app would show two accounts nobody added."
```

---

### Task 4: Sign-in that runs off the request thread

**Files:**
- Modify: `ourcal.py` — add the sign-in state, `start_signin`, `signin_status`, their endpoints; extend `SETUP_PAGE` with a Sign in button and polling
- Test: `tests/test_ourcal.py`

**Interfaces:**
- Consumes: `run_oauth_flow()` (`ourcal.py:749`), `client_config_text()` (Task 2), `token_path()`, `account_mismatch()`, `ACCOUNTS`.
- Produces: `start_signin(label) -> dict`, `signin_status() -> dict`, `signin_endpoint(payload) -> dict`; routes `POST /api/signin` and `GET /api/signin/status`.

- [ ] **Step 1: Write the failing tests**

```python
class TestSignInEndpoint(_TmpData, unittest.TestCase):
    """Signing in used to happen inside the agenda request and block it
    for up to 300s per account. On a phone that is a frozen screen."""

    def setUp(self):
        self.tmp = self._tmp_data()
        real = ourcal.ACCOUNTS
        self.addCleanup(lambda: setattr(ourcal, "ACCOUNTS", real))
        ourcal.ACCOUNTS = [{"label": "One", "email": "one@example.com"}]
        ourcal._SIGNIN.update({"label": None, "state": "idle", "message": ""})
        self.addCleanup(lambda: ourcal._SIGNIN.update(
            {"label": None, "state": "idle", "message": ""}))

    def _fake_flow(self, result=None, boom=None):
        def run(label, email):
            if boom:
                raise boom
            with open(ourcal.token_path(label), "w", encoding="utf-8") as f:
                f.write(result or "{}")
        real = ourcal._run_signin
        ourcal._run_signin = run
        self.addCleanup(lambda: setattr(ourcal, "_run_signin", real))

    def test_starts_and_reaches_done(self):
        self._fake_flow()
        self.assertTrue(ourcal.start_signin("One")["ok"])
        for _ in range(50):
            if ourcal.signin_status()["state"] != "waiting":
                break
            time.sleep(0.05)
        s = ourcal.signin_status()
        self.assertEqual(s["state"], "done")
        self.assertTrue(os.path.exists(ourcal.token_path("One")))

    def test_a_failure_reaches_error_with_its_message(self):
        self._fake_flow(boom=RuntimeError("no network"))
        ourcal.start_signin("One")
        for _ in range(50):
            if ourcal.signin_status()["state"] != "waiting":
                break
            time.sleep(0.05)
        s = ourcal.signin_status()
        self.assertEqual(s["state"], "error")
        self.assertIn("no network", s["message"])

    def test_a_second_signin_while_one_runs_is_refused(self):
        ourcal._SIGNIN.update({"label": "One", "state": "waiting"})
        r = ourcal.start_signin("One")
        self.assertFalse(r["ok"])
        self.assertIn("already running", r["error"])

    def test_an_unknown_label_is_refused(self):
        r = ourcal.start_signin("Nope")
        self.assertFalse(r["ok"])
        self.assertEqual(ourcal.signin_status()["state"], "idle")
```

`time` is already imported in `ourcal.py`; add `import time` to the test file's imports if absent.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_ourcal.TestSignInEndpoint -v`

Expected: FAIL — `ourcal._SIGNIN` does not exist.

- [ ] **Step 3: Add the state and the runner**

`threading` is currently imported inside functions (`start_server`, `run_app_window`), not at module scope, but the lock below is a module-level object. Add `import threading` to the top-level imports (alphabetical, after `re`), and leave the existing function-local imports alone — removing them is out of scope for this task.

```python
# One sign-in at a time: run_local_server binds a port, and the user can
# only be in one browser flow.
_SIGNIN = {"label": None, "state": "idle", "message": ""}
_SIGNIN_LOCK = threading.Lock()


def _run_signin(label, email):
    """Complete one OAuth flow and write its token. Blocking; own thread.

    Split out so tests can replace it without faking Google. The Android
    specifics — the browser Intent and waiting for DNS to come back after
    the trip to Chrome — already live in run_oauth_flow.
    """
    from google_auth_oauthlib.flow import InstalledAppFlow
    flow = InstalledAppFlow.from_client_config(
        json.loads(client_config_text()), SCOPES)
    creds = run_oauth_flow(flow)
    with open(token_path(label), "w", encoding="utf-8") as f:
        f.write(creds.to_json())


def start_signin(label):
    """Begin a sign-in on a background thread and return immediately."""
    import threading
    email = _email_for(label)
    if email is None:
        return {"ok": False, "error": f"No account named {label}."}
    with _SIGNIN_LOCK:
        if _SIGNIN["state"] == "waiting":
            return {"ok": False,
                    "error": "Another sign-in is already running — "
                             "finish it first."}
        _SIGNIN.update({"label": label, "state": "waiting", "message": ""})

    def work():
        try:
            _run_signin(label, email)
            _SIGNIN.update({"state": "done", "message": ""})
        except Exception as e:
            _SIGNIN.update({"state": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()
    return {"ok": True}


def signin_status():
    return dict(_SIGNIN)


def signin_endpoint(payload):
    return start_signin(payload.get("label", ""))
```

- [ ] **Step 4: Register the routes**

Add to `do_POST`'s `routes`:

```python
                      "/api/signin": signin_endpoint,
```

Add to `do_GET`, after the `/api/status` branch:

```python
            elif self.path == "/api/signin/status":
                self._send(200, json.dumps(signin_status()))
```

- [ ] **Step 5: Add the Sign in button and polling to `SETUP_PAGE`**

In `renderAccounts`, add a Sign in button beside Remove for accounts that are not signed in:

```javascript
      + (on ? "" : '<button class="si" data-label="' + esc(l) + '"'
        + ' style="padding:4px 8px;font-size:12px">Sign in</button>')
```

and wire it after the Remove handler:

```javascript
  box.querySelectorAll(".si").forEach(function(b){
    b.onclick = function(){
      const out = document.getElementById("acctMsg");
      out.className = "msg";
      out.textContent = "Opening Google… finish there, then come back "
                      + "to this app.";
      api("/api/signin", {method:"POST",
          headers:{"Content-Type":"application/json"},
          body: JSON.stringify({label: b.dataset.label})})
        .then(r=>r.json()).then(function(d){
          if(!d.ok){ out.className = "msg bad"; out.textContent = d.error; return; }
          const poll = setInterval(function(){
            api("/api/signin/status").then(r=>r.json()).then(function(s){
              if(s.state === "waiting") return;
              clearInterval(poll);
              out.className = s.state === "done" ? "msg good" : "msg bad";
              out.textContent = s.state === "done"
                ? "Signed in." : (s.message || "Sign-in failed.");
              refresh();
            });
          }, 1500);
        });
    };
  });
```

- [ ] **Step 6: Run the full suite**

Run: `python3 -m unittest discover tests -q`

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add ourcal.py tests/test_ourcal.py
git commit -m "Sign in from a button instead of from the agenda request

creds_for opened a browser as a side effect of loading the agenda and
blocked that HTTP request for up to 300 seconds — for each account, one
after another. On a phone that is a frozen screen with no explanation.

Sign-in now runs on its own thread with a state the page polls, so the
device can say 'finish in Chrome, then come back' while it waits. One at
a time, because run_local_server binds a port and a person can only be
in one browser flow.

_run_signin is split out so tests can drive every path — done, error,
and refusal — without faking Google itself. The Android specifics were
already in run_oauth_flow; they had simply never been called from
anywhere sensible."
```

---

### Task 5: Stop `creds_for` from opening browsers

**Files:**
- Modify: `ourcal.py` — add `NeedsSignIn`; change `creds_for`; extend `list_account_events`'s error entry; add a Sign in link to `PAGE`'s banner
- Test: `tests/test_ourcal.py`

**Interfaces:**
- Consumes: `creds_for`, `list_account_events`, `run_oauth_flow`.
- Produces: `class NeedsSignIn(Exception)`; per-account error entries gain `"signin": bool`.

- [ ] **Step 1: Write the failing tests**

```python
class TestNeedsSignIn(_TmpData, unittest.TestCase):
    """Loading the agenda must never open a browser. It did, four times,
    sequentially, inside one HTTP request."""

    def setUp(self):
        self.tmp = self._tmp_data()
        with open(os.path.join(self.tmp, "credentials.json"), "w",
                  encoding="utf-8") as f:
            f.write('{"installed": {"client_id": "x"}}')

    def test_creds_for_raises_instead_of_launching_a_browser(self):
        launched = []
        real = ourcal.run_oauth_flow
        ourcal.run_oauth_flow = lambda flow: launched.append(1)
        self.addCleanup(lambda: setattr(ourcal, "run_oauth_flow", real))
        with self.assertRaises(ourcal.NeedsSignIn):
            ourcal.creds_for("One", "one@example.com")
        self.assertEqual(launched, [])      # nothing opened

    def test_the_error_entry_is_flagged_for_sign_in(self):
        real = ourcal.service_for
        ourcal.service_for = lambda label, email: (_ for _ in ()).throw(
            ourcal.NeedsSignIn("One"))
        self.addCleanup(lambda: setattr(ourcal, "service_for", real))
        _, err = ourcal.list_account_events("One", "one@example.com", "a", "b")
        self.assertTrue(err["signin"])
        self.assertFalse(err["setup"])      # setup is fine; sign-in is not
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_ourcal.TestNeedsSignIn -v`

Expected: FAIL with `AttributeError: module 'ourcal' has no attribute 'NeedsSignIn'`.

- [ ] **Step 3: Add the exception and change `creds_for`**

Add just above `creds_for`:

```python
class NeedsSignIn(Exception):
    """This account has no usable token and a human must sign in.

    Raised rather than opening a browser: creds_for runs inside the
    agenda request, and launching a browser there blocked that request
    for up to 300 seconds per account, sequentially. Sign-in is an
    explicit button now, on both platforms.
    """
```

In `creds_for`, replace the whole `else:` branch — the `print` and the flow — with:

```python
    else:
        client_config_text()      # raises FileNotFoundError if absent at all
        raise NeedsSignIn(label)
```

Keep everything else, including the token write after a refresh.

- [ ] **Step 4: Flag it in `list_account_events`**

Add a new `except` clause **before** the existing `except FileNotFoundError`:

```python
    except NeedsSignIn:
        # Setup is fine; this account simply has no token yet. Different
        # fix, different button — so the page must be able to tell them
        # apart without reading the message text.
        return [], {"message": "not signed in", "setup": False, "signin": True}
```

Add `"signin": False` to the other two error returns so every entry has the same shape.

- [ ] **Step 5: Offer sign-in from the agenda banner**

In `PAGE`'s banner renderer, change the per-account branch so a `signin` error carries a link:

```javascript
    : errs.map(e=>`<div class="banner">⚠️ ${e.signin
        ? `<b>${esc(e.label)}</b> isn't signed in. <a class="setup-link" href="/setup" style="color:var(--accent)">Sign in</a>`
        : `Couldn't refresh <b>${esc(e.label)}</b> — ${esc(e.message)}`}</div>`).join("");
```

- [ ] **Step 6: Run the full suite**

Run: `python3 -m unittest discover tests -q`

Expected: all pass. `TestSetupErrorFlag`'s existing assertions still hold — they check `setup`, which is unchanged.

- [ ] **Step 7: Commit**

```bash
git add ourcal.py tests/test_ourcal.py
git commit -m "Never open a browser from inside the agenda request

creds_for launched an OAuth flow as a side effect of loading the agenda,
blocking that request for up to 300 seconds — per account, sequentially.
On a desktop that was four browser windows firing unbidden on first run;
on a phone it was a frozen screen.

It now raises NeedsSignIn and the page offers a button. Error entries
carry a signin flag beside the existing setup flag, so the banner can
tell 'you have not set this device up' from 'this one account needs
signing in' without reading message text."
```

---

### Task 6: Documentation

**Files:**
- Modify: `README.md` (the "Connect your accounts" section and line 87's claim), `SETUP_GUIDE.md`

- [ ] **Step 1: Amend `README.md`'s claim about shipping a client**

`README.md:87` currently asserts, as a certainty:

> Shipping a shared client secret inside a published app would get it revoked and route every user's calendar traffic through one quota — which is why this step cannot be done for you.

That is now contradicted by the shipped APK. Replace it with an accurate account:

- The Android build ships an OAuth client so the app works on a fresh install.
- `credentials.json` is an app identity, not an account key: it holds a client id and secret and no refresh token, and Google issues a token only after a person signs in and consents. An extracted secret therefore reaches nobody's Google account.
- What it does permit is impersonation — a fake app showing "OurCal" on a genuine consent screen — plus quota use, and revocation if abused, which would break the app for everyone until a new client is issued.
- Anyone who prefers their own Google Cloud project can still paste their own `credentials.json`, and it takes precedence.
- Running from source has no bundled client, so the macOS instructions are unchanged.

Verify each claim against the code before writing it.

- [ ] **Step 2: Document sign-in on the device in `SETUP_GUIDE.md`**

Add a section covering: adding an account on the phone; tapping Sign in; the *"Google hasn't verified this app"* screen and that **Advanced → Go to OurCal** is the way past it; that you must return to OurCal after Chrome finishes, because Android cuts the app's network while it is in the background and the token exchange waits for it to come back.

Add troubleshooting entries for the verbatim messages: *"Another sign-in is already running — finish it first."*, *"OurCal needs at least one account — add the replacement first."*, and the existing `account_mismatch` text.

- [ ] **Step 3: Confirm the suite is untouched**

Run: `python3 -m unittest discover tests -q`

Expected: all pass. Docs only.

- [ ] **Step 4: Commit**

```bash
git add README.md SETUP_GUIDE.md
git commit -m "Document on-device sign-in, and correct the claim about shipping a client

README asserted that shipping a shared client secret would get it
revoked, as a certainty, which the Android build now contradicts. The
honest account: credentials.json is an app identity, not an account key,
so an extracted secret reaches nobody's Google account; what it permits
is impersonation, quota use, and revocation if abused. Bring-your-own
still takes precedence.

The setup guide gains the on-device flow, including the unverified-app
warning screen and why you have to come back to OurCal after Chrome."
```

---

## Verification before calling this done

- [ ] `python3 -m unittest discover tests -q` — all pass
- [ ] `git status --short` shows no change to `credentials.json`, `accounts.json` or any `token_*.json`
- [ ] `git check-ignore packaging/android/src/ourcal/resources/bundled_credentials.json` matches — the client can never be committed
- [ ] `./packaging/build-app.sh` produces a `.dmg`, and the app serves
- [ ] `./packaging/build-android.sh` produces an APK, and it reports bundling the OAuth client
- [ ] Install it; add an account on the phone; tap Sign in; complete Google; confirm the agenda fills
- [ ] Create an event targeting all accounts and confirm it appears on each, then delete it

The last two need a human: nobody can automate tapping through a Google consent screen, and no device is attached.
