# OurCal — Delete & Sync Design

Date: 2026-07-23
Status: approved

Adds two capabilities to the unified dashboard: deleting an event at its source
calendar, and copying ("syncing") an event into other connected calendars with
an optional invite forwarded to an outside address.

## Decisions

| Question | Decision |
|----------|----------|
| Deleting a row merged across accounts | Confirm dialog lists every source, all pre-checked; user may uncheck |
| What lands in other calendars on sync | Chosen per sync: full details, or busy block with details stripped |
| Meaning of "edit" | Adjust title/time before copying only. Originals are never modified except by delete |
| Deleting a recurring occurrence | Dialog offers this occurrence or the entire series |
| Forwarding to an outside address | Guest is attached to one of the *copies*; user picks which account sends |
| Detail level for a forwarded guest | Host copy always carries full details; the user's other mirrors still honor the busy/full choice |

Explicitly out of scope: linked two-way sync, and editing an original event in
place. Both were considered and declined.

## §1 Source identity

A unified row cannot currently address the events it came from. `normalize()`
keeps `iCalUID`, the account label, and the calendar *name*, but drops Google's
`id` and `calendarId` — exactly what the delete and patch APIs need. And
`merge_events()` collapses matching events from several accounts into one row,
so one row may map to several real events.

`normalize()` therefore takes the calendar id and emits:

```python
"sources": [{"label": "Personal", "calendarId": "you@example.com",
             "eventId": "abc_20260724T160000Z",
             "seriesId": "abc",            # None when not recurring
             "calendarName": "Primary"}]
```

`merge_events()` concatenates `sources` the same way it merges `labels`. The
existing `labels` and `calendars` fields stay as they are so badge rendering
needs no rework; the redundancy is accepted in exchange for a smaller change.

`seriesId` comes from Google's `recurringEventId`, present on expanded
occurrences. Its absence is how the UI knows to skip the occurrence/series
question.

### Why not the alternatives

A server-side handle map (`{token: (label, calendarId, eventId)}`) would keep
ids off the wire, but introduces the first cross-request mutable state in an
otherwise stateless server — stale tokens after a poll, disagreement between
browser tabs, loss on restart. The privacy benefit is nil on a loopback-bound
server.

Re-looking-up the event at delete time from `(label, uid, start)` avoids the
schema change but costs extra API calls and can match the wrong event, which is
unacceptable on a destructive path.

**Constraint this relies on:** the server binds to `127.0.0.1`. Calendar and
event ids reaching the browser is safe only while that holds.

## §2 Delete — `POST /api/delete`

```json
{"scope": "occurrence",
 "sources": [{"label": "Personal", "calendarId": "...", "eventId": "..."}]}
```

`scope: "series"` deletes `seriesId` in place of `eventId`.

Per-source isolation mirrors `_google_collect`: one account failing does not
abort the others, and the response is a results array shaped like the one
`/api/create` already returns.

- `404` is treated as success — already absent is the desired end state, so the
  operation is idempotent.
- `403` is surfaced verbatim; it usually means the user is not the organizer.
- `sendUpdates="none"`, so deleting an event with guests does not silently mail
  them a cancellation.

Deleted events land in Google Calendar's trash and stay restorable for about 30
days, so this is recoverable rather than destructive.

## §3 Sync — extends `/api/create`

No new endpoint. Two new payload fields:

- `detail: "full" | "busy"` — `build_event_body()` drops summary, location, and
  description when `"busy"`, keeping only the time slot.
- `forwardTo: ["sam@example.com"]` with `forwardFrom: "Personal"`.

In the copies branch, the target whose label matches `forwardFrom` is built with
full detail regardless of `detail`, and additionally carries `attendees` and
`sendUpdates="all"`. Every other target follows `detail`. The existing `invite`
mode used by New Event is untouched.

`detail` is a separate axis from the existing per-target `blocking`: `blocking`
controls transparency (does this occupy my time), `detail` controls visibility
(can a viewer see what it is). Overloading one for the other would make
"visible but free" and "opaque but anonymous" inexpressible.

`forwardTo` addresses are validated before any API call is made.

## §4 UI

Clicking an event row opens a menu with **Sync…** and **Delete…**

- **Sync dialog** — title, date, and time prefilled from the clicked event and
  editable; target account checkboxes; full/busy radio; a forward-to field with
  a send-from account picker. Submits to `/api/create`.
- **Delete dialog** — one checkbox per source, all pre-checked, plus an
  occurrence/series radio shown only when `seriesId` is present.

## §5 Error handling

Every write path isolates failures per account and reports a per-target result,
matching the existing collect and create behavior. The UI renders the results
array so a partial success is legible — three copies made, one account failed,
and which.

## §6 Testing

Most behavior lands in pure functions, matching the existing suite's style
(which runs without Google installed):

- `normalize()` emits `sources` with ids and series id
- `merge_events()` concatenates `sources` across merged rows
- `build_event_body()` strips title/location/notes under `detail="busy"`
- forwarding attaches attendees to the host target only, not to mirrors
- delete scope selects `eventId` vs `seriesId` correctly

Wiring against Google is covered by stubbing `service_for`, the pattern
introduced for `TestGoogleCreateWiring`. That gap is what allowed a two-argument
signature change to break `_google_create` while all tests passed.

`_demo_delete` keeps the whole flow exercisable under `OURCAL_DEMO=1` without
touching real calendars.
