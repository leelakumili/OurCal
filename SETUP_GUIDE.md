# OurCal — Setup Guide

OurCal unifies the calendars from all of your Google accounts into one
dashboard and lets you create, edit, sync and delete events across any selection
of them. Everything runs on your own Mac — nothing is uploaded anywhere.

You can try the whole interface **right now, with zero setup**:

```bash
OURCAL_DEMO=1 python3 ourcal.py
```

Then open <http://127.0.0.1:8756>. **Demo mode shows made-up events and two
placeholder accounts** — it never contacts Google and ignores `accounts.json`. To see your real calendars, stop it and follow the steps below once.

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

On the very first run OurCal will:

1. Create a private `.ourcal-venv/` next to the app and install its two
   dependencies (`google-api-python-client`, `google-auth-oauthlib`), then
   relaunch itself from that venv. (This happens once; later runs are instant.)
2. Open your browser to sign in — **once per account**. Before each prompt the
   terminal prints which address that prompt is for:
   ```
   OurCal: sign in as you.second@example.com   (account “Side”)
           Pick this exact account in the browser window.
   ```
   **Read the terminal before choosing** — Google's prompts look identical,
   and if you're already signed into several accounts the chooser order will not
   match OurCal's. Each sign-in writes a `token_<label>.json` so you won't be
   asked again. If you pick the wrong one, OurCal detects it and tells you which
   token file to delete rather than silently mislabeling that account's events.

When it's done, open <http://127.0.0.1:8756>. Your unified agenda for the next
30 days appears and refreshes every 5 minutes (or immediately via **Refresh**).

---

## Troubleshooting

**Every account shows `credentials.json is missing from the OurCal folder`.**
Exactly what it says — finish Steps 1–4 above and make sure the downloaded file
is renamed to exactly `credentials.json` and sits next to `ourcal.py`. One
banner per account is normal here; they all share the same cause.

**An account shows `signed in as <X>, not <Y> — delete token_<label>.json`.**
You picked the wrong Google account at that prompt, so its token is filed under
the wrong label. OurCal refuses to use it rather than stamping that account's
events with the wrong badge. Delete the named token file, restart, and pick the
account the terminal names — each prompt now prints which address it wants
before opening the browser. Watch the terminal, not just the browser.

**The dashboard shows events I don't recognize, and only two accounts
(`Personal` / `Work` on `example.com` addresses).**
You're in demo mode — the app was started with `OURCAL_DEMO=1`, so it serves
built-in fixtures instead of touching Google at all. Stop it (`Ctrl-C`, or
`lsof -ti tcp:8756 | xargs kill`) and relaunch without that variable:
`python3 ourcal.py`.

**"Google hasn't verified this app" warning.**
Expected while the consent screen is in *Testing* mode. Click **Advanced →
Go to OurCal (unsafe)** → **Continue**. It's your own app; this is normal.

**An account shows a "re-auth" banner / a calendar is missing.**
- OurCal only shows calendars that are **selected (visible)** in that account,
  plus each account's **primary** calendar. In Google Calendar, make sure the
  calendar is checked/visible in the left sidebar of *that account*.
- Calendars whose IDs contain `#holiday`, `#contacts`, or `addressbook` are
  intentionally skipped (holidays and auto-generated contact birthdays).
- To force a fresh sign-in for one account, delete its `token_<label>.json`
  and run OurCal again.

**Adding a work / Workspace account (e.g. `you@work.example.com`).**
Append it to `accounts.json`,
add the address as a test user on the **Audience** page, and restart — OurCal
will prompt for sign-in once. Be aware that corporate Workspace accounts are
often restricted by an admin policy that blocks third-party OAuth apps, or that
prevents adding the address as an external test user. If IT blocks it, that
account shows a re-auth banner and the others keep working normally; that's an
org-policy limitation, not an OurCal bug.

**Removing an account.**
Stop OurCal, delete its entry from `accounts.json`, delete its
`token_<label>.json`, then restart. Do it in that order — a still-running OurCal
holds the old account list in memory and will re-create the token file.

**Port 8756 is already in use.**
Another program (or a previous OurCal) holds the port. Free it with
`lsof -ti tcp:8756 | xargs kill`, or change `PORT` at the top of `ourcal.py`.

**Reset everything.**
Delete all `token_*.json` (re-signs in) and/or `.ourcal-venv/` (re-installs
deps on next run). `credentials.json` stays.

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

---

## Privacy

Everything runs on your own machine. The interface is served only on
`127.0.0.1`. Your `credentials.json` and OAuth tokens never leave your computer,
and calendar data is fetched straight from Google to you and held only in
memory. There is no OurCal server, no telemetry, and no third party.
