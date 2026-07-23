# OurCal — Design Spec

**Date:** 2026-07-23
**Status:** Draft for review
**Owner:** project author

## 1. Purpose

A local macOS app that unifies the calendars of several Google accounts into one
dashboard and can create events / busy-blocks across those accounts from a single
form. Ships configured with **the author's 5 accounts at the time** (four personal Gmail +
one Work work account), each surfacing every calendar in that account
(including calendars synced into it, e.g. ADPList). The `ACCOUNTS` config is a
flat list of `{label, email}` so it scales to the whole household later —
**Tilak, then the kids' calendars** — where adding a family member is one config
entry plus one OAuth sign-in. Everything runs and stays on the user's machine.

Non-goals: no cloud service, no multi-user server, no true push/webhook sync
(frequent polling + force-refresh instead — see §5a), no calendar
editing/deleting from the UI (create-only in v1), no mobile.

## 2. Success criteria

- One dashboard shows every event from every selected calendar of all 5
  configured accounts (including calendars synced *into* an account, e.g. ADPList
  bookings) for the next `DAYS_AHEAD` (30) days, auto-refreshing every
  `POLL_MINUTES` (default 5), with a Refresh button that force-polls immediately.
- The "New event / Block time" form creates an event once and pushes it to any
  selection of accounts, with per-calendar Blocking (busy/opaque) vs
  Non-blocking (free/transparent), in either **Copies** or **Invite** mode.
- Runs on a clean Python 3.9+ machine with only `credentials.json` present:
  first run self-bootstraps its dependencies.
- `OURCAL_DEMO=1` makes the whole UI exercisable with zero Google setup.

## 3. Tech constraints (fixed by requester)

- Python 3.9+ only. No Node/Electron/CDN/framework.
- UI = stdlib `ThreadingHTTPServer` on `127.0.0.1:8756` serving one
  self-contained HTML page (inline CSS/JS) + a JSON API.
- Google Calendar via `google-api-python-client` + `google-auth-oauthlib`,
  OAuth `InstalledAppFlow` with a Desktop credential in `credentials.json`,
  one `token_<slug>.json` per account (slug = filesystem-safe form of the
  label), auto-refresh of expired tokens.
- Single deliverable file `ourcal.py` with the HTML embedded as a string.

## 4. Architecture

Single file `ourcal.py`, organized top-to-bottom into labeled sections. No
extra modules (the "one file" constraint), but each section is a small set of
pure-ish functions with a clear interface so it can be reasoned about and tested
in isolation.

```
ourcal.py
├─ CONFIG          ACCOUNTS, TIMEZONE, DAYS_AHEAD, PORT, palettes
├─ BOOTSTRAP       import-guard → build .ourcal-venv → pip install → os.execve
├─ GOOGLE          OAuth/token load+refresh, calendar discovery, list, create
├─ NORMALIZE       raw Google event → normalized dict (pure)
├─ MERGE           collect across accounts, dedupe by title+span, badge-merge (pure)
├─ DEMO            in-memory fixture store used when OURCAL_DEMO=1
├─ SERVICE         get_events() / create_event() — dispatch to GOOGLE or DEMO
├─ HTTP            ThreadingHTTPServer + handler: /, /api/events, /api/create
└─ HTML            PAGE = """…single page, inline CSS/JS…"""
```

**Unit responsibilities & interfaces**

- **CONFIG** — data only. `ACCOUNTS = [{label, email}]`, `TIMEZONE`,
  `DAYS_AHEAD`, `POLL_MINUTES`, `PORT`, `PALETTE_LIGHT`, `PALETTE_DARK` (parallel
  arrays; account N gets color index `N % len`). A helper `slug(label)` derives
  the token filename (`token_<slug>.json`).
- **BOOTSTRAP** — `ensure_deps()`: try importing Google libs; on `ImportError`
  (and only when `OURCAL_REEXEC` is unset) create `.ourcal-venv` next to the
  script, `pip install` deps into it, set `OURCAL_REEXEC=1`, and `os.execve`
  the venv's python on the same script. The env-var guard prevents an infinite
  re-exec loop. Skipped entirely in demo mode (see §9).
- **GOOGLE** — `creds_for(label)` loads `token_<slug(label)>.json`, refreshes if
  expired (needs `refresh_token`) and rewrites the file; if no token exists,
  runs `InstalledAppFlow` (opens the browser) and saves one.
  `service_for(label)` builds a `calendar v3` client.
  `list_account_events(label, email, time_min, time_max)` → discovers
  calendars and returns normalized events for that account.
  `create_copy(label, body)` and `create_invite(host_label, body, attendees)`
  create events and return `(ok, htmlLink_or_error)`.
- **NORMALIZE** — `normalize(raw, label, calendar_name)` → normalized dict
  (§6). Pure; reads only its inputs.
- **MERGE** — `merge_events(list_of_normalized)` → deduped list (§7). Pure and
  copy-on-write: never mutates input dicts.
- **SERVICE** — thin dispatch: if `OURCAL_DEMO`, call DEMO; else call GOOGLE.
  Keeps HTTP handler ignorant of the backend.
- **HTTP** — routing + JSON I/O only; no calendar logic.
- **HTML** — one string; the only frontend.

## 5. Data flow

**Dashboard read — `GET /api/events`:**
1. `time_min = now`, `time_max = now + DAYS_AHEAD days` (both tz-aware,
   RFC3339).
2. For each account (sequential — 5 accounts, fine; documented as the
   simple choice, threadable later if latency grows):
   - load/refresh creds → `calendarList.list`.
   - keep a calendar if `selected` **or** `primary`; skip any id containing
     `#holiday`, `#contacts`, or `addressbook`.
   - `events.list(calendarId, singleEvents=True, orderBy="startTime",
     timeMin, timeMax)` → `normalize` each item.
3. `merge_events(all_normalized)` → dedupe + badge-merge.
4. Sort by start (all-day sorts by date at day start).
5. Respond with an envelope:
   ```json
   {
     "updated":  "<ISO8601>",
     "timezone": "America/Los_Angeles",
     "accounts": [{"label":"Personal","email":"…","color":0}, …],
     "events":   [ <normalized event>, … ],
     "errors":   [{"label":"Work","message":"re-auth needed"}]
   }
   ```
   `accounts` (with color index) and `timezone` let the frontend build chips,
   colors, and tz-correct formatting without hardcoding.

**Event create — `POST /api/create`:**
Payload:
```json
{ "title","date","startTime","endTime","allDay","notes","location",
  "mode": "copies" | "invite",
  "targets": [{"label","blocking"}],
  "inviteFrom": "<label, invite mode only>" }
```
- Build the Google event body from date/time (§8). Empty title → `"Busy"`.
- **Copies:** for each target, `create_copy` on that account's `primary`
  calendar with `transparency = "opaque"` if `blocking` else `"transparent"`.
- **Invite:** host = `inviteFrom`; `create_invite` on the host's `primary` with
  `attendees = [email of every other target]`, `sendUpdates="all"`, and
  `transparency` from the host target's `blocking`.
- Respond `{"ok": bool, "results": [{"label","ok","error?","htmlLink?"}]}`
  so the UI can toast full success vs partial failure.

### 5a. Freshness / "realtime" (feature 2)

The requester wants new events on any account to appear in the unified view in
"realtime or force polling." True push (Google Calendar `watch` channels) posts
notifications to a **public HTTPS webhook**, which a local `127.0.0.1` app cannot
receive — so push is out of scope for a local app. Instead:
- The frontend **auto-polls `GET /api/events` every `POLL_MINUTES`** (default 5;
  lower it in CONFIG for fresher-but-chattier updates — Google read quota is
  generous for one user's handful of calendars).
- The **Refresh button force-polls immediately** — the "force polling" the
  requester asked for; use it right after adding an event elsewhere to see it now.
- Each successful poll updates the "updated HH:MM" stamp so staleness is visible.

## 6. Normalized event shape

```json
{ "uid": "<iCalUID>", "title": "…",
  "start": "<RFC3339 or YYYY-MM-DD>", "end": "…",
  "allDay": true|false,
  "busy":  true|false,          // transparency != "transparent"
  "location": "…"|null,
  "join":  "<hangoutLink>"|null,
  "labels": ["Tilak"],          // list from the start, so merge just appends
  "calendars": ["ADPList"],     // parallel context for badges/tooltip
  "guests": <int> }             // attendees count
```
`labels`/`calendars` are lists even pre-merge (single element) so `merge_events`
only appends — no shape change on merge.

## 7. Dedupe / merge rules

Key = `(title, start instant, end instant, allDay)` — the appointment, not the
`uid`. A shared invite carries one `iCalUID` across every guest's calendar, but
the ordinary case is one appointment typed separately into each account: four
accounts → four Google events → four distinct UIDs → one dentist visit. Keying
on `uid` would leave those as four identical rows.

- Titles compare casefolded with whitespace collapsed.
- Compare **instants**, not raw strings: the same moment arrives as
  `11:00-07:00` from one calendar and `18:00Z` from another.
- Duration is part of the key, so a 1h and a 30m block starting together stay
  separate rows rather than having one silently misreport the other.
- `allDay` is part of the key, so an all-day event never absorbs a real event
  that happens to start at local midnight.

When two normalized events share the key:
- Start from a **copy** of the first-seen dict (never mutate inputs).
- `labels` = union preserving first-seen order; same for `calendars`.
- `sources` = union, so every underlying copy stays addressable for delete.
- `busy` = `any(copy.busy)` — if it blocks in *any* account, show it as busy.
- `guests` = `max`. `join` / `location` = first non-empty.

Accepted trade-off: two genuinely unrelated events that share a title, a start
and a duration collapse into one row. For a household view that is the intent —
the row means "this exact slot is taken on these calendars" — and no source is
lost, so the row can still be deleted per account.

**Mutation-safety test:** calling `get_events()` twice must return deep-equal
results, and the second `merge_events` over the same source list must not have
altered the first result. This is an explicit verification step.

## 8. Google event body construction

- **All-day** (`allDay=true`): `start={"date":"YYYY-MM-DD"}`,
  `end={"date": start + 1 day}` (Google's exclusive end date).
- **Timed:** `start={"dateTime":"YYYY-MM-DDTHH:MM:00","timeZone":TIMEZONE}`,
  `end` likewise.
- `transparency`: `"opaque"` (blocking) or `"transparent"` (free).
- `summary = title or "Busy"`; `location`, `description=notes` when present.
- Invite adds `attendees=[{"email":…}]` and is created with `sendUpdates="all"`;
  copies use `sendUpdates="none"`.

## 9. Demo mode (`OURCAL_DEMO=1`)

- **Skips BOOTSTRAP and all Google imports** → runs on pure stdlib, so the UI is
  testable with zero setup or network.
- Uses a **fixed demo account set (`Personal`, `Work`) independent of
  `ACCOUNTS`**, so multi-account chips, dedupe badges, and the invite host radio
  are all exercisable without real OAuth.
- Seeds an in-memory list with realistic fixtures relative to "now": an
  ADPList-style mentoring session with a Meet `join` link, an all-day event, a
  non-blocking "free" event, a couple of ordinary meetings, and one event that
  appears under two accounts (exercises dedupe/badge-merge).
- `get_events()` returns the (merged) fixtures; `create_event()` appends to the
  store and returns a success result, so the create form round-trips visibly.

## 10. UI (single HTML page)

Faithful to the requester's UI spec. Key structural decisions:

- **Theme:** CSS custom properties. Default from `prefers-color-scheme`; a manual
  toggle sets `data-theme="light|dark"` on `<html>`, which wins. Account colors
  come from `PALETTE_LIGHT`/`PALETTE_DARK` (colorblind-safe: `#2a78d6 #eb6834
  #1baf7a #eda100 #e87ba4`, dark-adjusted steps). System font stack, no external
  fonts/CDN.
- **tz-correct formatting:** all day-grouping, "today", stat tiles, and time
  labels use `Intl.DateTimeFormat('en-US', {timeZone})` with the `timezone` from
  the API envelope, so display is correct regardless of the browser's own tz.
- **Header:** OurCal title, "updated HH:MM", dark-mode toggle, Refresh,
  primary "+ New event / Block time".
- **Stat tiles:** meetings today; meetings next 7 days; hours in meetings over
  7 days (timed only, all-day excluded); countdown to next meeting.
- **Filter chips:** one per account (color dot + live event count); click
  toggles that account's visibility. Counts respect dedupe (a merged event
  counts under each of its labels).
- **Agenda:** grouped by day — "Today · Thursday, Jul 23", "Tomorrow · …", then
  weekday + date. Each event row: colored account bar, time + duration, title,
  account badges, "free" tag when non-blocking, location, guest count, "Join ↗"
  when `join` present. The currently-live meeting is highlighted.
- **Create modal:** title, date, start/end, all-day checkbox, location, notes;
  mode selector (Copies vs Invite, each with a one-line explanation); then one
  row per account — include checkbox, color dot, label + email, and a Busy/Free
  segmented toggle. In **Invite** mode a radio picks the host and only the host
  row shows the Busy/Free toggle. Toasts report success / partial failure using
  the per-target `results`.

## 11. Error handling

- **Per-account isolation:** an account that fails to auth/list is caught,
  added to `errors[]`, and never blanks the dashboard — the other accounts
  still render, and the UI shows an inline "re-auth `<label>`" notice.
- **Token refresh:** automatic; a token without a usable `refresh_token`
  surfaces as a re-auth error rather than a crash.
- **Work / Workspace account (`you@work.example.com`):** a corporate Google
  Workspace may block third-party OAuth apps or disallow adding the address as a
  test user on a personal GCP project (admin policy). If so, that account simply
  fails to authorize and appears once in `errors[]` as "re-auth Work" — the
  four personal accounts are unaffected. Documented as a known limitation in
  SETUP_GUIDE, not a bug.
- **Port in use:** starting the server on a taken `PORT` prints a clear message
  (what's likely using it, how to change `PORT`) and exits non-zero.
- **Create failures:** returned per-target; the response is `ok:false` if any
  target failed, and the UI toasts which labels failed and why.

## 12. Project deliverables

- `ourcal.py` — the app (runnable directly; HTML embedded).
- `SETUP_GUIDE.md` — Google Cloud project, enable Calendar API, External consent
  screen in **Testing** mode with all 5 emails as test users, Desktop OAuth
  client, download `credentials.json`; first-run (one browser sign-in per
  account); troubleshooting (unverified-app warning, missing calendars, port
  conflict, token reset, **work/Workspace account blocked by org policy**);
  privacy note (everything stays local).
- `OurCal.command` — double-click launcher (`cd` to script dir, run `python3
  ourcal.py`, keep window open on error).
- `README.md` — short overview + demo-mode one-liner.
- `.gitignore` — `credentials.json`, `token_*.json`, `.ourcal-venv/`,
  `__pycache__/`.

## 13. Verification plan (before "done")

1. `python3 -m py_compile ourcal.py` — syntax.
2. Boot in demo mode; confirm the server serves `/` and `/api/events`.
3. `GET /api/events` twice; assert deep-equal (catches dedupe/normalize
   mutation bugs).
4. `POST /api/create` for: a timed event, an all-day event, and an invite
   payload; assert `ok` and correct per-target results; confirm they appear on
   a subsequent `GET`.
5. Headless browser: screenshot light theme, dark theme, and the open create
   form; visually check for clipping/overflow.

## 14. Accounts config (resolved)

Ships with the author's 5 accounts. The household grows later by appending entries and
doing one OAuth sign-in each. `token_<slug>.json`, chips, colors, and form rows
all key off `label`; `slug(label)` makes the label filesystem-safe.
```python
ACCOUNTS = [
    {"label": "Personal",       "email": "you@example.com"},         # primary
    {"label": "Second",     "email": "you.second@example.com"},
    {"label": "Third", "email": "you.third@example.com"},
    {"label": "Fourth",    "email": "you.ai@example.com"},
    {"label": "Work",   "email": "you@work.example.com"},     # work
    # Household later — one entry + one OAuth sign-in each:
    # {"label": "Tilak", "email": "<TILAK_EMAIL>"},
    # {"label": "Kids",  "email": "<KIDS_EMAIL>"},
]
```
Labels above are the working default (all five accounts are the author's, so the
labels distinguish *which* account, not *who*). They are trivially editable in
this block; requester to confirm or rename at review. There are exactly 5
palette colors, so each account gets a distinct color. **Invite** mode works
across these accounts (host emails the others), though inviting one's own
addresses is a niche use — **Copies** is the primary mode; §9 demo still
exercises Invite.
