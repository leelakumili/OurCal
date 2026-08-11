# OurCal — Setup Guide

OurCal unifies the calendars from all of your Google accounts into one
dashboard and lets you create, edit, sync and delete events across any selection
of them. Everything runs on your own device — nothing is uploaded anywhere.
The steps below are for a Mac or a source checkout; Android has its own
section further down.

You can try the whole interface **right now, with zero setup**:

```bash
OURCAL_DEMO=1 python3 ourcal.py
```

This opens your browser to the dashboard automatically. If it doesn't, use
the URL printed in the terminal — it ends in `?k=` plus a key minted fresh
for this run; a bare `http://127.0.0.1:8756` (no key) gets a 403.
**Demo mode shows made-up events and two placeholder accounts** — it never
contacts Google and ignores `accounts.json`. To see your real calendars, stop it and follow the steps below once.

---

## What you'll end up with

- An `accounts.json` listing the Google accounts you want unified.
- A Google Cloud project with the **Calendar API** enabled.
- An OAuth **consent screen** in *Testing* mode listing your accounts as test users.
- A **Desktop OAuth client**, downloaded as `credentials.json` (next to
  `ourcal.py` when running from source, or in
  `~/Library/Application Support/OurCal/` for the packaged Mac app).
- One `token_<label>.json` per account, created automatically on first sign-in.

## Step 0 — List your accounts

**There is no settings screen in the app.** Create a file called
**`accounts.json`** next to `ourcal.py`, listing every Google account you want
unified:

```json
[
  {"label": "Personal", "email": "you@example.com"},
  {"label": "Side",     "email": "you.second@example.com"},
  {"label": "Work",     "email": "you@work.example.com"}
]
```

- **`label`** is what appears on the coloured badges, and it names that
  account's token file (`token_personal.json`). Keep labels short and distinct.
- **`email`** must match the account you actually sign in as. OurCal verifies
  this at fetch time and refuses to use a token filed under the wrong label.
- Add, remove, or rename entries any time — then restart OurCal. Renaming a
  label orphans its old token file, so you'll sign in once more.

`accounts.json` is **git-ignored**, so your addresses stay out of the repository
even if you publish your copy. If the file is missing or malformed, OurCal falls
back to the two placeholder accounts in `ourcal.py` and the dashboard will show
sign-in errors for them — that's the signal to create it.

---

## Step 1 — Create a Google Cloud project

1. Go to <https://console.cloud.google.com/>.
2. Sign in with the Google account you want to *own* the project — your main
   personal account (e.g. `you@example.com`) is fine.
3. Click the project dropdown (top bar) → **New Project**.
4. Name it `OurCal` and click **Create**. Wait for it to finish, then make sure
   it's the selected project.

## Step 2 — Enable the Google Calendar API

1. Left menu → **APIs & Services → Library** (or go to
   <https://console.cloud.google.com/apis/library>).
2. Search for **Google Calendar API**, open it, and click **Enable**.

## Step 3 — Configure the consent screen (Google Auth Platform)

Google replaced the old single-page "OAuth consent screen" wizard with the
**Google Auth Platform**, a section with its own left-hand nav. Go to
<https://console.cloud.google.com/auth/overview> (or **APIs & Services → OAuth
consent screen**, which now redirects there). If prompted, click **Get started**
and fill in the app name and your email.

The settings you need are spread across three pages in the left nav:

| Page | What to do |
|------|------------|
| **Branding** | **App name:** `OurCal`. **User support email** and **Developer contact:** your email. Leave logo, domains, and links blank. |
| **Audience** | Confirm **User type: External** (the only option for a personal Gmail account). Under **Test users** click **+ Add users** and add **every address from your `accounts.json`**. Leave **Publishing status: Testing** — do *not* click "Publish app". |
| **Data access** | Nothing to do. OurCal requests the Calendar scope at sign-in time rather than pre-declaring it. |

Every account that will sign in must be listed as a test user while the app is
unverified — one line per address, exactly as they appear in `accounts.json`:

```
you@example.com
you.second@example.com
you@work.example.com
```

You do *not* need to submit the app for verification for personal use.

## Step 4 — Create the Desktop OAuth client

1. In the **Google Auth Platform** nav, click **Clients** → **+ Create client**.
   (The old path — **APIs & Services → Credentials → Create Credentials → OAuth
   client ID** — still works and lands in the same place.)
2. Application type: **Desktop app**. Name it `OurCal Desktop`. Click **Create**.
3. In the confirmation dialog, click **Download JSON**.
4. Rename the downloaded file to exactly **`credentials.json`** and place it in
   the same folder as `ourcal.py`.

> If the Auth Platform **Overview** page says *"You haven't configured any OAuth
> clients for this project yet"*, this is the step that fixes it.

> `credentials.json` and every `token_*.json` are already in `.gitignore` — they
> stay private and are never committed.

## Step 5 — First run

Double-click **`OurCal.command`**, or from a terminal in this folder:

```bash
python3 ourcal.py
```

On the very first run OurCal creates a private `.ourcal-venv/` next to the app
and installs its two dependencies (`google-api-python-client`,
`google-auth-oauthlib`), then relaunches itself from that venv. (This happens
once; later runs are instant.)

OurCal opens your browser to the dashboard automatically once the server is
up. If it doesn't, use the URL printed in the terminal instead of typing
`http://127.0.0.1:8756` by hand — that bare address now returns 403. The
URL carries a `?k=` key minted fresh each run, and the port can shift too
if 8756 is busy, so a bookmarked dashboard URL from a previous run won't
work; always use the one the current run just printed (or double-click
`OurCal.command` again). **Nothing signs you in automatically** — each
account you haven't authorised yet shows a banner with a **Sign in** link.
Click it (it opens `/setup`), then click that account's own **Sign in**
button there. OurCal only runs one Google sign-in at a time, so do this once
per account: each click opens a fresh consent screen in your browser —
approve calendar access, and the tab confirms when it's done. OurCal checks
that the account you actually reached matches the address in `accounts.json`
*before* writing anything, so picking the wrong one at the prompt is caught —
the page names the address to pick instead of silently filing that account's
events under the wrong label.

As each account finishes, OurCal writes its `token_<label>.json` so you won't
be asked again. Once every account you care about is signed in, your unified
agenda for the next 30 days appears and refreshes every 5 minutes (or
immediately via **Refresh**).

---

## On Android: sign in on the device

Everything above assumes a Mac or a source checkout, and ends with you
pasting a `credentials.json` you downloaded yourself. Skip all of that if
you're running an APK with one bundled in — the `.apk` on the **latest**
[release](../../releases) has one built in, and so does a self-built one
where `./packaging/build-android.sh` printed **"bundling the OAuth client"**
when it copied one in from the working tree. (A pre-release — a manual test
build — may not; see `README.md`'s [Development](README.md#development)
section.) A fresh install of a bundled-client APK can add an account and
sign in to Google with no computer at all:

1. Open OurCal and tap **Set up this device** (the footer link) — or tap
   **Sign in** on any account's banner, which lands on the same page.
   Either way you're now on `/setup`.
2. Under **Accounts**, enter a name (e.g. `Work`) and the Gmail address, then
   tap **Add**. The account appears in the list, marked **not signed in**.

   If this is a brand-new device with no `accounts.json` yet, OurCal starts
   you off with two placeholders, `Personal` (`you@example.com`) and `Work`
   (`you@work.example.com`). Adding your first real account doesn't replace
   them — it appends to the list. Remove both placeholders with their
   **Remove** button once you've added your own, or they sit there as
   "not signed in" banners you can never sign in to.
3. Tap that account's **Sign in** button. The page says *"Opening Google…
   finish there, then come back to this app,"* and your browser opens
   Google's account picker.
4. OurCal's bundled project is published but not verified by Google —
   verification needs a domain and a privacy policy this project doesn't
   have — so you'll land on a red **"Google hasn't verified this app"**
   screen. That's expected, and unlike Steps 1–4 above, nobody needs to be
   added as a test user first: any Google account can get past it. Tap
   **Advanced**, then **Go to OurCal (unsafe)**, then grant calendar access.
5. **Switch back to OurCal.** Android suspends a backgrounded app's network,
   and stepping into the browser backgrounds OurCal — so it's waiting for
   the network to return before it can finish exchanging the code Google
   just issued. Returning to the app is what lets that finish. Within a few
   seconds the setup page shows **Signed in.**, the row flips to **✓ signed
   in**, and the agenda fills without a restart.

Prefer your own Google Cloud project to the bundled client? A pasted
`credentials.json` always takes precedence over the bundled one, so Steps
1–4 above still work on Android: export a bundle from a Mac that already has
your `credentials.json` (`./ourcal.py --export | pbcopy`) and paste it in
under **2 · Bundle** on the same `/setup` page.

---

## Troubleshooting

**Every account shows `credentials.json is missing from the OurCal folder`.**
Exactly what it says — finish Steps 1–4 above and make sure the downloaded file
is renamed to exactly `credentials.json` and sits next to `ourcal.py`. One
banner per account is normal here; they all share the same cause.

**An account shows `signed in as <X>, not <Y> — open Set up this device,
remove <label>, add it again, and sign in as <Y>`.**
A token file already exists for this account, but it was signed in as a
different Google account than the one in `accounts.json`. OurCal refuses to
use it rather than stamping that account's events with the wrong badge. This
banner is plain text, not a link — unlike an "isn't signed in" banner, there
is no **Sign in** button on it, so don't go looking for one there.

Nothing needs restarting — tokens are read fresh on every refresh, not cached
at startup — and the message names the one remedy that works the same way on
a computer and on the phone: open `/setup`, tap **Remove** for that account
(one action deletes the entry *and* its token), add it back with the same
name and the correct email, then tap **Sign in** and pick `<Y>` this time.
The fix takes effect on the next poll or **Refresh** click.

**Sign-in says `Signed in as <X>, not <Y> — tap Sign in again and pick <Y>`.**
The on-device version of the message above: you picked the wrong account in
Google's picker *during* a sign-in you just started. OurCal checks the
account it actually reached before writing a token, so — unlike the older
message — nothing gets filed under the wrong label; no token is written at
all. Tap **Sign in** again and choose `<Y>` this time.

**Sign-in says `Another sign-in is already running — finish it first.`**
OurCal runs one Google sign-in at a time — it briefly binds a local port
waiting for Google's redirect, and a second one at once would collide. Wait
for the sign-in you already started to finish (or fail, or time out after 5
minutes), then tap **Sign in** again.

**The dashboard shows events I don't recognize, and only two accounts
(`Personal` / `Work` on `example.com` addresses).**
You're in demo mode — the app was started with `OURCAL_DEMO=1`, so it serves
built-in fixtures instead of touching Google at all. Stop it (`Ctrl-C`, or
`lsof -ti tcp:8756 | xargs kill`) and relaunch without that variable:
`python3 ourcal.py`.

**"Google hasn't verified this app" warning.**
Expected — either because your own consent screen is still in *Testing* mode
(the desktop flow above), or because an Android build's bundled client is
*Published* but not Google-verified (see ["On Android: sign in on the
device"](#on-android-sign-in-on-the-device)). Click **Advanced → Go to
OurCal (unsafe)** → **Continue** either way; it's your own app, this is
normal.

**An account shows a "re-auth" banner / a calendar is missing.**
- OurCal only shows calendars that are **selected (visible)** in that account,
  plus each account's **primary** calendar. In Google Calendar, make sure the
  calendar is checked/visible in the left sidebar of *that account*.
- Calendars whose IDs contain `#holiday`, `#contacts`, or `addressbook` are
  intentionally skipped (holidays and auto-generated contact birthdays).
- To force a fresh sign-in for one account, open `/setup` and click that
  account's **Sign in** (or **Sign in again**) button. Sign-in is a button
  now, not something a restart triggers — `creds_for` no longer opens a
  browser as a side effect of loading the agenda.

**Adding a work / Workspace account (e.g. `you@work.example.com`).**
Add it to `accounts.json` (by hand, or via **Accounts** on `/setup`), add the
address as a test user on the **Audience** page, restart if you edited the
file by hand, then click **Sign in** for that account. Be aware that
corporate Workspace accounts are often restricted by an admin policy that
blocks third-party OAuth apps, or that prevents adding the address as an
external test user. If IT blocks it, that account shows a re-auth banner and
the others keep working normally; that's an org-policy limitation, not an
OurCal bug.

**Removing an account.**
On `/setup`, tap that account's **Remove** button — one action removes it
from the list *and* deletes its `token_<label>.json`, so no live refresh
token is left on the device, and the change takes effect without a restart.
(You can still do it by hand instead: stop OurCal, delete the entry from
`accounts.json` and its `token_<label>.json`, then restart — a still-running
OurCal holds the old account list in memory and would re-create the token
file.)

Removing your only account is refused, with *"OurCal needs at least one
account — add the replacement first."* Add the replacement before removing
the one you no longer want.

**Port 8756 is already in use.**
Another program (or a previous OurCal) holds the port. Free it with
`lsof -ti tcp:8756 | xargs kill`, or change `PORT` at the top of `ourcal.py`.

**Reset everything.**
Delete all `token_*.json` to sign every account out — this does *not* sign
you back in by itself; open `/setup` and click **Sign in** for each account
afterward. Delete `.ourcal-venv/` to force a dependency reinstall on next
run. `credentials.json` stays.

**On Android, every account says `credentials.json is missing`.**
This APK was built without a bundled OAuth client — a paste-only build (see
`packaging/build-android.sh`, which prints "no credentials.json — building a
paste-only APK" when it makes one of these). Tap **Set up this device**, or open
`/setup` from the footer link, and paste a bundle from `./ourcal.py
--export`. If instead your accounts show "isn't signed in" rather than this
message, the client is already bundled — see ["On Android: sign in on the
device"](#on-android-sign-in-on-the-device) and just tap **Sign in**. The
footer of the `/setup` page also shows the directory the app resolved and
states the diagnosis directly: `android branch: live` or `android branch:
not active`, with a separate line if the Java bridge is unavailable. If it
says `not active` on a real device, the app is running desktop code
paths — rebuild from a source tree that includes the `is_android()`
interpreter probe.

**The APK won't install — "App not installed", or a package-conflict error.**
Android identifies an app by its package name *and* its signing key, so an
APK signed with a different key than the one already on the phone is refused
outright rather than treated as an update. You hit this moving between a
self-built APK (debug-signed unless you set the `ANDROID_KEYSTORE_*`
variables) and one downloaded from a tagged release (signed with the
project's keystore) — in either direction.

Export a bundle first (`./ourcal.py --export`), then uninstall, install the
new APK, and paste the bundle back in. **Uninstalling deletes the app's
private directory, so every account and token goes with it** — that is the
whole reason to export first. Releases signed with the same keystore install
over each other cleanly, so this is a one-time cost when the key changes,
not something that recurs on every update.

**A date I picked shows fewer events than I expected.**
The day view fetches only that day, and lists an event when it *overlaps* the
day — so an 11pm-to-1am meeting appears on both dates it touches, and a
meeting that ended before midnight does not carry over. If a day looks emptier
than it should, check the account filter chips above the agenda: they still
apply while a date is selected, and an account switched off stays off when you
change dates. The stat tiles disappear on purpose here — they are all measured
from now, and only the selected day was fetched.

**The setup page rejects my bundle.**
`Wrong passphrase, or the bundle was altered in transit` means exactly that,
and the two cases are indistinguishable on purpose. Re-export and re-paste,
taking care that nothing wrapped or truncated the text — some chat apps insert
line breaks into long strings.

---

## Privacy

Everything runs on your own machine. The interface is served only on
`127.0.0.1`. Your `credentials.json` and OAuth tokens never leave your computer,
and calendar data is fetched straight from Google to you and held only in
memory. There is no OurCal server, no telemetry, and no third party.
