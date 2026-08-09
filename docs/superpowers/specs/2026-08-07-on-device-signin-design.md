# On-device Google sign-in — design

**Date:** 2026-08-07 · **Status:** approved, not yet implemented

## The problem

The Android build can receive a setup pasted in from a Mac, and nothing else. It
cannot add an account and cannot sign in to Google. A stranger who downloads the
APK installs it, opens it, taps **Set up this device**, and is asked for a bundle
exported from a computer they do not have. The app is installable and unusable.

The goal is that someone who downloads the APK can add their own accounts and
sign in to Google entirely on the phone.

## What a downloader will experience

```
install APK → open OurCal
        │
        ▼
  ⚠ OurCal isn't set up yet   [Set up this device]
        │
        ▼
  Add account   Name [Work]  Email [me@gmail.com]  [Add]
        │
        ▼
  Work   me@gmail.com   not signed in  [Sign in]
        │
        ▼  Chrome opens Google's picker
        │  ⚠ "Google hasn't verified this app" → Advanced → Go to OurCal
        │  → Allow → switch back to OurCal
        ▼
  Work   me@gmail.com   ✓ signed in    [Remove]
        │
        ▼
   their agenda
```

No computer, no Google Cloud project, no pasted bundle.

## Decisions

| Decision | Chosen | Rejected |
|---|---|---|
| Distribution | Published-unverified consent screen: anyone can sign in, sees a warning, ~100-user cap | Full verification (domain, privacy policy, demo video, 2–6 weeks); Testing mode (100 hand-added users, 7-day tokens) |
| OAuth client | Ship the existing **Desktop** client inside the APK | **Android** client type (no secret, but custom-scheme redirect + manifest intent-filter + PKCE, abandoning the proven loopback path); bring-your-own-client only |
| Sign-in trigger | Explicit button, on both platforms | Implicit on agenda load (today's behaviour) |
| Account removal | Removes the account **and** deletes its token file | Leave an orphaned token behind |
| Order | This cycle; the release workflow follows separately | Both together |

### Why ship the Desktop client, given its secret is extractable

`credentials.json` is an **app identity**, not an account key. It contains
`client_id` and `client_secret` and no `refresh_token`. Google issues a token
only after a human signs in on `accounts.google.com` and consents, so an
extracted secret grants access to **no** Google account, including the author's.
Verified: the built APK contains no `token_*.json` and zero occurrences of any
of the author's addresses.

What an extracted secret does permit is impersonation — a fake app showing
"OurCal" on a genuine consent screen — plus quota consumption and, if abuse is
detected, revocation of the client, which breaks the app for everyone until a new
one is issued and users re-authorise.

That risk is accepted because it is small at this scale (an unknown project's
identity has little phishing value), the failure is recoverable by rotation
plus a re-release — existing installs break until users re-sideload, since
there is no Android release job today — and the alternative trades it for a
large certain cost: abandoning the loopback flow
the spike proved on real hardware for a custom-scheme route unproven under
Chaquopy. This project has already lost a month to one unproven Android
assumption.

**`README.md:87` must be amended.** It currently states that shipping a shared
client secret *"would get it revoked"*, as a certainty. The honest statement is
that the secret is extractable, the exposure is impersonation and quota rather
than account access, and the mitigation is rotation.

**What would change this decision:** expecting more than ~100 users. Verification
becomes mandatory at that point, and switching to an Android client belongs in
that same work.

---

## Part 1 — Shipping the OAuth client

`credentials.json` is git-ignored and stays that way. It reaches the APK from
outside the repository, mirroring how `APPLE_SIGN_ID` reaches the macOS build.

- `packaging/build-android.sh` copies `./credentials.json`, when present, to
  `packaging/android/src/ourcal/resources/bundled_credentials.json`. That
  directory is already git-ignored.
- In CI it is written from a GitHub secret before the build.
- When absent the build still succeeds and produces a paste-only APK — the same
  graceful degradation `build-app.sh` already uses for signing.

At runtime a new `bundled_credentials()` returns the shipped client's contents
or `None`. It reads through `pkgutil.get_data`, which works against Chaquopy's
loader (already relied on by `tz()` for `tzdata`), falling back to a path beside
the module, and returning `None` on any failure.

**Precedence: `data_dir()/credentials.json` always wins.** A user who pastes in
their own client keeps using it. The bundled copy is the fallback, not an
override — so bring-your-own-client continues to work unchanged.

`creds_for` resolves the client in that order and raises the existing
`FileNotFoundError` only when neither exists.

---

## Part 2 — Accounts editor

Two endpoints, both on the setup page.

`POST /api/accounts {label, email}` — appends to the account list, validates the
whole list through `parse_accounts()` (`ourcal.py:109`), which already rejects a
blank label, an invalid address, and a label that collides after slugging.
Writes through `write_user_files()`, so the existing all-or-nothing and `0600`
guarantees apply. Calls `reload_accounts()` so the account appears without a
restart.

`DELETE /api/accounts {label}` — removes the account **and deletes its token
file**. One action rather than two: an account you removed must not leave a live
refresh token on the device.

**Removing the last account is refused**, with *"OurCal needs at least one
account — add the replacement first."* The reason is not arbitrary:
`parse_accounts()` rejects an empty list (`ourcal.py:115`), so `load_accounts`
would return `None`, and `ACCOUNTS = load_accounts(...) or ACCOUNTS`
(`ourcal.py:149`) would silently restore the `Personal` / `Work` placeholders —
the app would look like it had two accounts nobody added. Refusing is simpler
than teaching `ACCOUNTS` to distinguish "the file says empty" from "there is no
file", and it costs the user only the order of two operations: add the
replacement, then remove the wrong one.

The setup page grows an accounts section listing each account with its sign-in
state, an **Add** form, and **Remove** per row.

---

## Part 3 — Non-blocking sign-in

### The behaviour change

Today `creds_for` (`ourcal.py:749`) opens a browser as a side effect of loading
the agenda, and blocks that HTTP request for up to 300 seconds — for each
account, sequentially. On a phone that is a frozen screen.

`creds_for` will instead raise a new `NeedsSignIn` when there is no usable token,
and never launch a browser. Only the sign-in endpoint launches one.

This changes the desktop too, deliberately: a first run shows an agenda with
per-account **Sign in** buttons instead of firing four browser windows
automatically. Uniform across platforms, and better on both.

`list_account_events` catches `NeedsSignIn` and returns an error entry carrying
`"signin": True`, alongside the existing `"setup"` flag. The banner's collapse
rule is unchanged; a `signin` error renders with a **Sign in** button.

### The endpoints

`POST /api/signin {label}` starts the flow on a background thread and returns
immediately. `GET /api/signin/status` returns the current state, which the page
polls.

State lives in a module-level dict guarded by a lock:

```
{"label": <str|None>, "state": "idle"|"waiting"|"done"|"error", "message": str}
```

**One sign-in at a time.** A second `POST` while one is `waiting` returns 409:
`run_local_server` binds a port and the user can only be in one browser flow.

The background thread calls the existing `run_oauth_flow` (`ourcal.py:724`),
which already overrides the browser launch with `_android_open_url`'s Intent and
defers the token exchange until `_await_network` sees DNS return. Both were
written after the spike and have never run, because `is_android()` was returning
False until this branch fixed it.

On success it writes `token_<label>.json` and sets `done`. On
`account_mismatch`, timeout, or any exception it sets `error` with a message the
page shows verbatim.

---

## Part 4 — The session token

The previous review demonstrated that `POST /api/import` was writable by any web
page, and `_local_caller` now closes that. But an `Origin` check authenticates
only a browser: a native Android app sends no `Origin`, is accepted, and was
demonstrated writing an attacker-controlled `credentials.json`. That gap is
recorded in `2026-08-07-android-setup-import-design.md`.

This cycle closes it, because it touches the same surface and because sign-in
adds a second credential-writing endpoint.

- `SESSION_TOKEN = secrets.token_urlsafe(32)`, minted once at import.
- Substituted into `PAGE` and `SETUP_PAGE` at serve time, exactly as
  `__POLL_MS__` already is — including into every in-page link between the
  two pages, not only the JS constant.
- Every `/api/*` request must carry it in an `X-OurCal-Token` header; `403`
  otherwise. `GET /` and `GET /setup` also require it, as a `?k=` query
  parameter rather than a header, since a header is not something a page
  navigation can attach to itself.

**First-draft version of this section was wrong.** It argued "a local
attacker cannot read the token without already being able to read the page,
which requires passing the Host and Origin checks" — and the paragraph twelve
lines above this one already states that the Android attacker *passes* those
checks by construction (no `Origin` header at all), so that argument
concludes the hole is closed using the exact premise that leaves it open. It
was: exempt `GET /` and `GET /setup` from the token entirely, so a native
caller — which the rest of this document establishes clears `_local_caller`
for free — could fetch either page with no token, read `SESSION_TOKEN` out of
the served HTML, and replay it against every `/api/*` route. Demonstrated
live: `POST /api/import` returned `{"ok": true}` with an attacker-controlled
`credentials.json` on disk this way.

What the fix above actually provides: the token now gates `/` and `/setup`
themselves, so there is no unauthenticated request that returns either page's
HTML at all. The only route the token reaches a legitimate caller by is the
URL this process itself constructs and hands to the in-process view —
`start_server()` bakes `?k=SESSION_TOKEN` into the URL passed to WKWebView on
macOS and to Toga's WebView on Android — plus the in-page links between `/`
and `/setup`, which carry the key forward the same way. A caller that never
sees that URL, including a user who hand-types
`http://127.0.0.1:8756/` instead of using the one the app opened, gets `403`
from both pages and from every `/api/*` route, with no bootstrap path back
in.

**Cost:** every existing HTTP test must send the header. That is mechanical but
touches several test classes, and the plan must account for it rather than
discovering it mid-task.

---

## Error handling

| Cause | Message |
|---|---|
| Sign-in already in progress | Another sign-in is already running — finish it first. |
| Sign-in timed out | Timed out waiting for Google — tap Sign in again. |
| Wrong Google account picked | (existing `account_mismatch` text, shown verbatim) |
| No network after the browser trip | Couldn't reach Google — check your connection and try again. |
| Duplicate or invalid account | That account is invalid or already added. |
| No OAuth client at all | (existing `credentials.json is missing` text) |

---

## Testing

All offline, no Google, no network.

**Shipped client:** `bundled_credentials()` returns `None` when absent; a
pasted `credentials.json` takes precedence over a bundled one; `creds_for` uses
the bundled copy when nothing is pasted.

**Accounts editor:** add succeeds and reloads `ACCOUNTS`; blank label, bad
address, and a label colliding after slugging are all rejected and write
nothing; remove deletes both the entry and its token file; removing the last
account leaves a usable empty state.

**Sign-in:** the state machine driven with a faked flow through every path —
`waiting` → `done`, `waiting` → `error`; a second `POST` during `waiting`
returns 409; `creds_for` raises `NeedsSignIn` rather than launching a browser;
`list_account_events` turns that into an error entry with `signin: True`.

**Session token:** an `/api/*` request without the header returns 403; with the
correct header succeeds; `GET /` and `GET /setup` return 403 without the
correct `?k=` and succeed with it — including the composition case a first
draft of this suite missed: a caller with no key at all cannot fetch either
page and read the token out of it to bootstrap one; the token appears in both
served pages (once the key is supplied) and differs from the placeholder.

**Regression:** the existing agenda, create, delete and update paths still work
with the header added.

---

## Verification after implementation

Machine-verifiable: the full suite; the `.dmg` builds and its app serves; the
APK builds and carries the new code; account add and remove against a scratch
data directory; the sign-in endpoint's state machine; token refresh against the
real Google accounts; creating an event across all four accounts and reading it
back, then deleting it — with explicit permission before anything writes to a
real calendar.

Needs a human: **clicking through Google's consent screen**, which cannot be
automated, and **installing the APK and running it on the phone**. The critical
on-device check remains the setup page's footer — it now states `android
branch: live` or `android branch: not active` directly (plus a separate line
if the Java bridge is unavailable), not the `chaquopy/AssetFinder` heuristic
this doc originally described, which appears nowhere in the code.

---

## Out of scope

- The release workflow — keystore, signing secrets, the CI Android job, and
  publishing the APK to GitHub Releases. Next cycle.
- Google OAuth verification, a domain, or a privacy policy.
- Switching to an Android-type OAuth client.
- `X-Frame-Options` / `frame-ancestors`, noted as pre-existing in the prior
  design's follow-on.
