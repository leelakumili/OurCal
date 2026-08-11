# Date view — design

**Date:** 2026-08-10 · **Status:** approved, not yet implemented

## The problem

The agenda is a rolling window: `_google_collect` sets `time_min = _iso(now)`
and `time_max = now + days` (`ourcal.py:911-912`), and the header dropdown
chooses `days` from 30 / 90 / 180 / 365. Every event OurCal has ever shown
starts at the current moment and runs forward.

So there is no way to answer "what was on my calendar last Tuesday?", and no
way to reach a specific day ahead without widening the range and scrolling to
it. A date picker was assumed to exist and did not — checked across all 106
commits, `dateSel`, `jumpTo` and `goToDate` have never appeared; the only
date-viewing control ever added is `rangeSel` in `be0dda2`.

The goal: pick any date, past or future, and see that day's schedule — without
changing anything about how the existing range views behave.

## Decisions

| Decision | Chosen | Rejected |
|---|---|---|
| Relationship to the range dropdown | A second, independent control; the two are mutually exclusive | Picker sets the window's *anchor* and the range sets its width (strictly more capable, but changes what "Next 30 days" means and turns one control into two coupled ones) |
| Reach | Any parseable date, no bound on past or future | Bounding to ±N years — nothing to protect against once unparseable input is rejected |
| Picker UI | Native `<input type="date">` | A hand-rolled calendar widget — the project has zero JS dependencies and the native control already gives month/year navigation on both desktop and Android |
| Malformed `date=` | Treated as absent; falls back to the rolling window | Returning an error — inconsistent with `clamp_days`, which turns `"banana"` into the default (`ourcal.py:572`) |
| Persistence | Not persisted | `localStorage` like `ourcal-days` — a standing preference is not the same as a one-off lookup |
| Stat tiles with a date selected | Hidden | Leaving them (four confidently wrong numbers) or recomputing them per-day (more UI duplicating the list below) |
| Rendering with a date selected | One heading, events in start order | Group by start-day as usual (files a midnight-spanning event under *yesterday's* heading while you are asking about today) |

### Why not the anchored-window design

The alternative considered was making the picker set *where* the window starts
while the dropdown keeps saying *how wide* it is, with "This day only" added as
a range option. It is strictly more capable and needs one code path rather than
two.

It was rejected because it changes the meaning of the existing control: with an
anchor of 19 Aug, "Next 30 days" is no longer next-30-days. The requirement is
explicitly to keep the current 30/90-day views as they are and add a picker
beside them, so the two-independent-controls model is what the feature asks
for.

---

## Part 1 — Server

`/api/events` gains one optional query parameter:

```
GET /api/events?date=2026-08-19
```

When `date` is present and parseable, the fetch window becomes **local midnight
of that date to local midnight of the next day**, in `TIMEZONE`. When absent or
unparseable, behaviour is exactly as today.

`days` is ignored when `date` is present — the window is one day by
construction, so there is nothing for it to mean.

### Interfaces

```python
def parse_view_date(value):
    """Parse a YYYY-MM-DD query value into a date, or None.

    None for absent, empty, malformed, or out-of-calendar values. Mirrors
    clamp_days: bad input degrades to the default view rather than erroring,
    because in practice the value always arrives from a native date input and
    a malformed one means a hand-crafted request, not a user mistake.
    """


def day_window(on_date):
    """(time_min, time_max) RFC3339 strings spanning on_date in TIMEZONE.

    Local midnight to the next local midnight, both built from tz()-aware
    datetimes so the offsets are the zone's real offsets on those dates.
    """


def get_events(now=None, days=None, on_date=None):
    """...unchanged except: on_date, when set, replaces the rolling window."""


def _google_collect(now, days=None, on_date=None):
    """...unchanged except: on_date, when set, replaces time_min/time_max."""
```

The response payload gains one key:

```json
{ "updated": "...", "days": 30, "date": "2026-08-19", "accounts": [...], "events": [...] }
```

`"date"` is `null` on a rolling request. The client reads it to confirm what it
actually got rather than assuming its request was honoured.

### DST — and a testing trap that matters more

The obvious worry is that a spring-forward day is 23 hours and a fall-back day
25, so "midnight + 24h" would drop or duplicate an hour twice a year.

**Measured, that worry is unfounded here.** Arithmetic on a ZoneInfo-aware
datetime is *wall-clock* arithmetic: `midnight + timedelta(days=1)`
re-normalises to the next local midnight and picks up the new offset.
Both forms produce identical bounds on 2026-03-08 (23h) and 2026-11-01 (25h).
The failure mode only exists with naive datetimes or fixed offsets, neither of
which this codebase uses — `tz()` returns a `ZoneInfo`.

What *is* a real trap is testing it. CPython short-circuits `b - a` to a naive
subtraction when both operands share the same `tzinfo` instance, so:

```python
a, b = both_from_day_window(date(2026, 3, 8))
(b - a).total_seconds() / 3600      # 24.0 — wrong, and passes either way
(b.astimezone(utc) - a.astimezone(utc)).total_seconds() / 3600   # 23.0 — real
```

A DST test written the first way asserts nothing: it reports 24 hours whatever
the implementation does. **The tests must convert to UTC before subtracting.**

The DST assertions are kept — not because the naive form is broken today, but
because they pin the property so a later switch to fixed offsets or naive
arithmetic fails loudly rather than silently shifting an hour of someone's day.

---

## Part 2 — Demo mode

**`get_events` currently ignores `days` in demo mode entirely** — the demo
branch returns `merge_events(_DEMO_STORE)`, the whole fixture store, no matter
what range was requested (`ourcal.py:589-592`).

This matters more than it looks: the test suite runs in demo mode. Implementing
the date filter only on the Google path would leave the demo branch returning
every fixture event for any date, so the feature would be visibly broken in
demo mode *and* every test written against it would be asserting nothing.

So the demo branch filters `_DEMO_STORE` to the requested date, using the same
day-boundary logic as the real path.

**Out of scope:** making demo mode respect `days`. That inconsistency predates
this feature and is unrelated to it.

---

## Part 3 — Client

One new control in the header, beside the existing range dropdown:

```
[ Next 30 days ▾ ]  [ 📅 dd/mm/yyyy ]  [ ✕ Clear ]  [ 🌓 ]  [ ↻ Refresh ]  [ + New event ]
```

- `<input type="date" id="dateSel">`, empty by default.
- A **✕ Clear** button, rendered only while a date is set, that clears it.

### Behaviour

| State | Result |
|---|---|
| `#dateSel` empty | Identical to today. `#rangeSel` drives the fetch; nothing changes for anyone who never touches the picker. |
| `#dateSel` set | Fetch sends `?date=` instead of `?days=`. Agenda shows that day only. **`#rangeSel` stays enabled** — see below. |
| **✕ Clear** clicked | `#dateSel` cleared, rolling agenda restored. |
| A range chosen while a date is set | Clears the date and returns to the rolling agenda. |

**Corrected after testing.** This first shipped with `#rangeSel` *disabled*
while a date was selected, reasoning that "Next 30 days" means nothing on a
single day. That was logical purity at the cost of the escape route: the
range dropdown is the control people were already using, so it is the first
thing they reach for to get back — and it was greyed out. The only way out
was a button labelled "Today", which reads as "jump to today" rather than
"leave this view".

Now there are two ways out, and the dropdown stays enabled to be one of them.
The button is labelled **✕ Clear**.

The date is **not** written to `localStorage`. `ourcal-days` persists because a
range is a standing preference; reopening OurCal to find it pinned to a date
picked days ago would be a bug, not a feature.

### The stat tiles must be hidden

`stats()` (`ourcal.py:2087-2096`) computes all four tiles from the loaded event
set, and every one of them is framed relative to *now*: `meetings today`,
`next 7 days`, `hours in meetings (7d)`, `until next meeting`.

With a single arbitrary day loaded, none of those framings survives. Selecting
a date last February would render "0 meetings today · 0 next 7 days · 0.0 hours
· — until next meeting" — not an empty state but four confidently wrong
numbers, because the events those tiles describe were never fetched.

So **the tiles are hidden while a date is selected**, and restored when it is
cleared. They answer "how does my week look", which is not a question a
single-day lookup is asking.

Rejected: recomputing them as day-specific tiles ("meetings on this day",
"hours on this day"). It is more UI, more strings, and duplicates what the day
list directly below already shows.

### Other controls while a date is selected

| Control | Behaviour |
|---|---|
| Account filter chips | Work exactly as in the rolling view — they filter the loaded set, and the counts reflect that day |
| **Refresh** | Re-fetches the selected date, not the rolling window |
| Auto-refresh (5 min) | Re-fetches the selected date, so a stale day view cannot silently drift |
| **New event / Block time** | Unchanged; it already takes its own date |

### Rendering

With a date selected the agenda renders **a single heading for the selected
date**, then every returned event in start order — rather than grouping by
`dayKey` as the rolling view does.

The reason is concrete: an event that starts at 11pm the previous day and runs
past midnight *overlaps* the requested day, so Google returns it. Grouping by
start-day would file it under the previous day's heading while the user is
looking at a single-day view — two headings, one of them the wrong date.

### Heading format

`fmtDayHeading` (`ourcal.py:2053`) renders `"Today · Tuesday, Aug 19"` and
carries **no year**. That was correct when the agenda could only reach
`MAX_DAYS_AHEAD` (730) from now; with the picker reaching arbitrary years,
`"Monday, Feb 3"` is ambiguous.

So the selected-date heading includes the year **when it differs from the
current year**: `"Monday, Feb 3, 2025"`. Within the current year the year is
omitted, keeping the common case unchanged. The `Today ·` / `Tomorrow ·`
prefixes still apply when the selected date happens to be today or tomorrow.

### Empty states

| Case | Message |
|---|---|
| Date selected, nothing on it | *"Nothing scheduled on Tuesday, 19 August"* |
| Rolling view, nothing in range | *"No events in the next 30 days 🎉"* — with the number reflecting the **selected** range |

The second is an existing bug: `ourcal.py:2130` hardcodes `"the next 30 days"`
regardless of what the dropdown says, so a user on "Next year" with an empty
calendar is told the wrong thing. It is one line, in the code this feature is
already editing, and leaving it would mean shipping a second empty-state
message beside a wrong one.

---

## Error handling

| Condition | Behaviour |
|---|---|
| `date` absent | Rolling window, exactly as today |
| `date` malformed (`"banana"`, `"2026-13-45"`, `""`) | Treated as absent — rolling window |
| `date` valid but far past/future | Honoured. Google returns whatever exists, usually nothing |
| `date` and `days` both present | `date` wins; `days` ignored |
| An account fails to fetch for that day | Per-account error banner, exactly as the rolling view already does |
| Demo mode, date with no fixtures | Empty-day message, not an error |

---

## Testing

Server-side, in demo mode, matching the style of the existing `clamp_days`
tests (`tests/test_ourcal.py:1844-1874`):

- `parse_view_date` accepts `"2026-08-19"`; returns `None` for `""`, `None`,
  `"banana"`, `"2026-13-45"`, `"19-08-2026"`.
- `day_window` spans exactly one local day, starting at local midnight.
- `day_window` is 23 hours across 2026-03-08 and 25 across 2026-11-01 in
  `America/Los_Angeles` (the configured default), **measured after converting
  both bounds to UTC** — see "DST and a testing trap" above; subtracting two
  same-`tzinfo` datetimes reports 24 hours regardless and asserts nothing.
- `get_events(on_date=...)` returns only that day's events in demo mode.
- `get_events(on_date=...)["date"]` echoes the requested date; a rolling call
  returns `None`.
- `days` is ignored when `on_date` is set.
- A past date reaches events before `now` — the property that did not exist
  before this feature.

The client JS has no test harness in this project; the range feature shipped
the same way, and adding one is out of scope here.

---

## Out of scope

- A week or month grid view.
- Navigating day-by-day with arrows from the selected date.
- Making demo mode respect `days`.
- Persisting the selected date.
- Creating an event on the selected date directly from the day view (the New
  event form already takes a date).
