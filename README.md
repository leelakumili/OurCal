# OurCal

**A unified calendar dashboard for every Google account you own.**

![Python](https://img.shields.io/badge/python-3.9%2B-blue)
![Platform](https://img.shields.io/badge/platform-macOS-lightgrey)
![Tests](https://img.shields.io/badge/tests-206-brightgreen)
![Dependencies](https://img.shields.io/badge/dependencies-2-brightgreen)

OurCal brings **all calendars from all of your Google accounts** into a single
view, and lets you create, edit, sync and delete events across any selection of
them. It scales from one person's handful of accounts to a whole household —
adding someone is one config entry plus one sign-in.

The app and its interface are pure Python 3.9+ standard library, served as a web
page on `127.0.0.1:8756`. The only external dependencies are Google's two client
libraries, which OurCal installs for you on first run.

---

## Contents

- [Try it instantly](#try-it-instantly)
- [Install](#install)
- [Connect your accounts](#connect-your-accounts)
- [Features](#features)
- [Configuration](#configuration)
- [Development](#development)
- [Privacy](#privacy)
- [Contributing](#contributing)
- [License](#license)

## Try it instantly

No Google account, no setup, nothing to configure:

```bash
OURCAL_DEMO=1 python3 ourcal.py
```

Open <http://127.0.0.1:8756>. Demo mode serves realistic fixtures and every flow
is clickable — create, edit, sync and delete all work against an in-memory
store, so it is the safe place to try deleting something.

## Install

### As a Mac app

Download the `.dmg` from [Releases](../../releases), drag **OurCal** to
Applications, then **right-click it → Open → Open** the first time.

That step matters. macOS will otherwise refuse with *"OurCal is damaged and
can't be opened"*. It is not damaged — the app is unsigned, and that is simply
how macOS words an unsigned download. Right-click → Open is the supported way
past it, once per install. The terminal equivalent:

```bash
xattr -dr com.apple.quarantine /Applications/OurCal.app
```

The app runs its own server internally and shows the dashboard in a real window
— no terminal, no browser tab. Your credentials and sign-ins live in
`~/Library/Application Support/OurCal/`, outside the app, so upgrading never
signs you out.

Apple Silicon only. To build it yourself: `./packaging/build-app.sh`.

### From source

```bash
git clone <your-fork-url> && cd OurCal
python3 ourcal.py            # add --window for a native window
```

First run creates a private `.ourcal-venv/` and installs the two Google
libraries into it.

## Connect your accounts

**The `.dmg` removes the Python setup, not the Google setup.** OurCal reads your
calendars with *your own* OAuth credentials, so there is a one-time Google Cloud
step: create a project, enable the Calendar API, configure a consent screen, and
download a Desktop OAuth client as `credentials.json`.

Full walkthrough: **[SETUP_GUIDE.md](SETUP_GUIDE.md)**.

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

Shipping a shared client secret inside a published app would get it revoked and
route every user's calendar traffic through one quota — which is why this step
cannot be done for you.

Put `credentials.json` and `accounts.json` in `~/Library/Application
Support/OurCal/` for the packaged app, or next to `ourcal.py` when running from
source.

## Features

- **Unified agenda** across every selected calendar of every
  configured account (including calendars synced into them, e.g. ADPList),
  auto-refreshing every 5 minutes with a **Refresh** button for an instant poll.
- **One row per appointment.** The same appointment sitting on four accounts is
  four Google events with four different IDs — OurCal collapses them into a
  single row wearing every calendar's badge, with a striped accent bar, instead
  of repeating it once per account. Deleting still reaches all four copies.
- **Choose how far ahead you look** — 30 days by default, up to a year from the
  header. Events further out than the current window simply are not fetched, so
  a wider range costs proportionally more Google calls.
- **Stat tiles** (meetings today / next 7 days / hours in meetings / countdown),
  **per-account filter chips**, live-meeting highlighting, Join links, and a
  light/dark theme (follows your system, with a manual toggle).
- **New event / Block time** form that creates an event once and pushes it to any
  selection of accounts, each marked **Blocking** (busy) or **Non-blocking**
  (free), in one of two modes:
  - **Copies** — an independent event written to each selected account's calendar.
  - **Invite** — one host account creates a single shared event and invites the
    others (real Google invite with RSVP).
- **Sync an existing event** to your other calendars — hover any row and pick
  **Sync…**. Title and time are prefilled and editable; the original is never
  modified. Each copy lands as either **full details** or a **busy block** that
  holds the time while hiding the title, location, and notes.
- **Edit an event at its source** — hover any row and pick **Edit…**. Change the
  title, date, time, location, or notes and the change is written back to the
  real Google events behind the row. A row that lives on four calendars lists
  all four with checkboxes, so one reschedule moves every copy; untick one to
  leave it where it is. Only the fields you actually touch are sent, so an edit
  never overwrites something on another copy that you did not change. Recurring
  events offer this-occurrence or whole-series, and guests are never emailed.
  Events you were invited to but do not organize show Edit disabled — Google
  only lets the organizer change those.
- **Forward an invite outside** — enter any address in the sync form and choose
  which of your accounts sends it. That copy always carries full details so the
  invite is readable, while your own mirrors stay as private as you chose.
- **Delete at the source** — hover any row and pick **Delete…**. Events merged
  across several accounts list every calendar they live in, all pre-checked, so
  you see the blast radius and can narrow it. Recurring events ask whether to
  remove one occurrence or the whole series. Deletions land in Google's trash
  and are restorable for about 30 days.

## Configuration

**Your accounts** go in `accounts.json`. It is git-ignored, so your addresses
never enter the repository:

```json
[
  {"label": "Personal", "email": "you@example.com"},
  {"label": "Work",     "email": "you@work.example.com"}
]
```

The `label` names the badge and that account's token file; the `email` is
verified against whoever actually signs in, so a token cannot end up filed under
the wrong account. Without this file, OurCal falls back to placeholders.

**Everything else** is at the top of `ourcal.py`:

```python
TIMEZONE = "America/Los_Angeles"
DAYS_AHEAD = 30
POLL_MINUTES = 5
PORT = 8756
VERSION = "1.0.0"
```

## Development

```bash
python3 -m unittest discover tests -v
```

217 tests, no Google credentials or network needed — the suite runs in demo mode
against in-memory fixtures.

| Task | Command |
|---|---|
| Run the app | `python3 ourcal.py` |
| Run with a native window | `python3 ourcal.py --window` |
| Run the demo | `OURCAL_DEMO=1 python3 ourcal.py` |
| Run the tests | `python3 -m unittest discover tests -q` |
| Build the Mac app + `.dmg` | `./packaging/build-app.sh` |

The whole application is one file, `ourcal.py`, with the interface embedded as
an HTML/CSS/JS string. That is deliberate — see [Contributing](#contributing).

**Releases** are cut by bumping `VERSION` in `ourcal.py`, then pushing a
matching tag (`git tag v1.0.1 && git push origin v1.0.1`) or running the release
workflow manually. The workflow refuses to build when the tag and `VERSION`
disagree.

Android support is proven but not yet built — see
[NOTES-android.md](NOTES-android.md).

## Privacy

Everything runs on your own machine. The interface is served only on
`127.0.0.1`, and your credentials, tokens and calendar data never leave your
computer. There is no OurCal server, no telemetry and no third party — calendar
data is fetched straight from Google to you and held in memory.

`accounts.json`, `credentials.json` and every `token_*.json` are git-ignored, so
publishing your copy never leaks an address or a token.

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for the
project's two hard constraints (single file, standard library only) and how to
run the tests.

## License

[MIT](LICENSE) — use it, fork it, ship it. No warranty.
