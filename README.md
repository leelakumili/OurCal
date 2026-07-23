# OurCal

A local calendar dashboard — macOS-oriented, but plain Python — that unifies
**all calendars from all of your Google accounts** into one view, and lets you
create, sync, and delete events across any selection of them. Built to scale
from one person's handful of accounts to a whole household: adding a person is
one config entry plus one sign-in.

Pure Python 3.9+ standard library for the app and UI (a local web page on
`127.0.0.1:8756`); the only external dependencies are Google's client libraries,
which OurCal installs for you on first run.

## Try it instantly (no Google setup)

```bash
OURCAL_DEMO=1 python3 ourcal.py
```

Open <http://127.0.0.1:8756> — realistic demo events, fully clickable, including
the create, sync, and delete flows. Nothing touches Google, so it's the safe
place to try deleting something.

## Real use

1. **List your accounts** in `accounts.json` next to `ourcal.py` (see
   [Configuration](#configuration)). Without it OurCal runs on placeholders and
   every account will fail to sign in.
2. **Do the one-time Google setup** — project, Calendar API, consent screen,
   Desktop OAuth client → `credentials.json`. Every address from step 1 must be
   added as a *test user*. See **[SETUP_GUIDE.md](SETUP_GUIDE.md)**.
3. **Run it:** double-click `OurCal.command`, or `python3 ourcal.py`. The first
   run installs dependencies into a private `.ourcal-venv/`, then opens one
   browser sign-in per account. The terminal names which address each prompt
   wants — read it before choosing.
4. Open <http://127.0.0.1:8756>.

## What it does

- **Unified agenda** for the next 30 days across every selected calendar of every
  configured account (including calendars synced into them, e.g. ADPList),
  auto-refreshing every 5 minutes with a **Refresh** button for an instant poll.
- **One row per appointment.** The same appointment sitting on four accounts is
  four Google events with four different IDs — OurCal collapses them into a
  single row wearing every calendar's badge, with a striped accent bar, instead
  of repeating it once per account. Deleting still reaches all four copies.
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
- **Forward an invite outside** — enter any address in the sync form and choose
  which of your accounts sends it. That copy always carries full details so the
  invite is readable, while your own mirrors stay as private as you chose.
- **Delete at the source** — hover any row and pick **Delete…**. Events merged
  across several accounts list every calendar they live in, all pre-checked, so
  you see the blast radius and can narrow it. Recurring events ask whether to
  remove one occurrence or the whole series. Deletions land in Google's trash
  and are restorable for about 30 days.

## Configuration

**Your accounts** go in `accounts.json` next to `ourcal.py`. It's git-ignored,
so your addresses never enter the repository:

```json
[
  {"label": "Personal", "email": "you@example.com"},
  {"label": "Work",     "email": "you@work.example.com"}
]
```

The `label` names the badge and that account's token file; the `email` is
verified against whoever actually signs in, so a token can't end up filed under
the wrong account. Without this file, OurCal falls back to placeholders.

**Everything else** is at the top of `ourcal.py`:

```python
TIMEZONE = "America/Los_Angeles"
DAYS_AHEAD = 30
POLL_MINUTES = 5
PORT = 8756
```

## Tests

```bash
python3 -m unittest discover tests -v
```

105 tests, no Google credentials or network needed — the suite runs in demo
mode against in-memory fixtures.

## Privacy

Everything runs locally. The UI is served only on `127.0.0.1`, and your
credentials, tokens, and calendar data never leave your machine. No server, no
telemetry. `accounts.json`, `credentials.json`, and every `token_*.json` are
git-ignored, so publishing your copy never leaks an address or a token.
