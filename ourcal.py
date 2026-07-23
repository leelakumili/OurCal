#!/usr/bin/env python3
"""OurCal — local unified calendar dashboard for multiple Google accounts."""
import json
import os
import re
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo

# ── CONFIG ──────────────────────────────────────────────────────────────
APP_DIR = os.path.dirname(os.path.abspath(__file__))


def slug(label):
    """Filesystem-safe token key: lowercase, non-alnum → single hyphen, trimmed."""
    s = re.sub(r"[^a-z0-9]+", "-", label.lower())
    return s.strip("-")


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def valid_email(addr):
    """Good enough to catch typos before we hand an address to Google."""
    return bool(addr and _EMAIL_RE.match(addr.strip()))


# Placeholders only. Put your real accounts in `accounts.json` next to this
# file — it is git-ignored, so a published copy of OurCal carries nobody's
# addresses. See SETUP_GUIDE.md. Format:
#   [{"label": "Personal", "email": "you@example.com"}, ...]
ACCOUNTS = [
    {"label": "Personal", "email": "you@example.com"},
    {"label": "Work",     "email": "you@work.example.com"},
]


def parse_accounts(raw):
    """Validate a decoded accounts.json payload; None if unusable.

    Labels key the token filenames, so anything that collides after slugging
    is rejected rather than silently sharing one token.
    """
    if not isinstance(raw, list) or not raw:
        return None
    out = []
    for item in raw:
        if not isinstance(item, dict):
            return None
        label = str(item.get("label", "")).strip()
        email = str(item.get("email", "")).strip()
        if not label or not slug(label) or not valid_email(email):
            return None
        out.append({"label": label, "email": email})
    if len({slug(a["label"]) for a in out}) != len(out):
        return None
    return out


def load_accounts(path):
    """accounts.json → account list, or None if absent, unreadable, or invalid."""
    try:
        with open(path) as f:
            return parse_accounts(json.load(f))
    except (OSError, ValueError):
        return None


ACCOUNTS = load_accounts(os.path.join(APP_DIR, "accounts.json")) or ACCOUNTS

TIMEZONE = "America/Los_Angeles"
DAYS_AHEAD = 30
POLL_MINUTES = 5
PORT = 8756

# Colorblind-safe categorical palette (parallel light/dark arrays).
PALETTE_LIGHT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]
PALETTE_DARK  = ["#5b9cf0", "#ff8a5c", "#3fd39c", "#ffc23d", "#ff9ec4"]


# ── NORMALIZE ───────────────────────────────────────────────────────────
def can_edit(raw, access_role):
    """Whether this account may modify this event in place.

    Two independent gates. The calendar must be writable at all, and we must
    have standing on the event: Google rejects a non-organizer's patch with 403
    unless the organizer opted into `guestsCanModify`. An event with no
    organizer block at all is ours by default — only the calendar gate applies.
    """
    if access_role not in ("owner", "writer"):
        return False
    organizer = raw.get("organizer") or {}
    if organizer and not organizer.get("self"):
        return bool(raw.get("guestsCanModify"))
    return True


def normalize(raw, label, calendar_name, calendar_id="", access_role="owner"):
    """Google event dict → normalized event (spec §6). Pure; reads inputs only.

    `sources` records where this event really lives so the UI can delete it
    later; merged rows accumulate one entry per contributing account.
    """
    start_obj = raw.get("start", {}) or {}
    end_obj = raw.get("end", {}) or {}
    all_day = "date" in start_obj
    start = start_obj.get("date") if all_day else start_obj.get("dateTime")
    end = end_obj.get("date") if all_day else end_obj.get("dateTime")
    return {
        "uid": raw.get("iCalUID", ""),
        "sources": [{
            "label": label,
            "calendarId": calendar_id or "",
            "eventId": raw.get("id", ""),
            "seriesId": raw.get("recurringEventId") or None,
            "calendarName": calendar_name,
            "editable": can_edit(raw, access_role),
        }],
        "title": (raw.get("summary") or "").strip() or "Busy",
        "start": start,
        "end": end,
        "allDay": all_day,
        "busy": raw.get("transparency", "opaque") != "transparent",
        "location": raw.get("location") or None,
        "notes": raw.get("description") or None,
        "join": raw.get("hangoutLink") or None,
        "labels": [label],
        "calendars": [calendar_name],
        "guests": len(raw.get("attendees", []) or []),
    }


# ── MERGE ───────────────────────────────────────────────────────────────
def _extend_unique(dst, src):
    for x in src:
        if x not in dst:
            dst.append(x)


def _instant(s):
    """One ISO date or dateTime → comparable tz-aware instant.

    Timed values parse with their own offset; bare dates pin to local midnight
    in TIMEZONE so an all-day event sorts at the head of its day. Robust to
    mixed offsets and DST (unlike a lexicographic string compare). Returns
    datetime.max for anything unparseable, so it sorts last instead of raising.
    """
    try:
        s = s or ""
        base = s + "T00:00:00" if len(s) == 10 else s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(base)
        # Created events carry a naive dateTime (offset lives in timeZone); all-day
        # dates are naive too. Pin any naive instant to TIMEZONE so all events are
        # comparable (real Google dateTimes already carry an offset).
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo(TIMEZONE))
        return dt
    except ValueError:
        return datetime.max.replace(tzinfo=ZoneInfo(TIMEZONE))


def _event_instant(e):
    """Sortable tz-aware instant for a normalized event's start."""
    return _instant(e.get("start"))


def _identity(e):
    """What makes two rows the same appointment.

    Deliberately *not* the iCalUID. A shared invite carries one UID across every
    guest's calendar, but the ordinary case here is one appointment typed
    separately into each account — four accounts, four Google events, four
    different UIDs, one dentist visit. What actually identifies it across
    accounts is its title and the exact span it occupies.

    Compare instants rather than the raw strings: the same moment comes back as
    11:00-07:00 from one calendar and 18:00Z from another. Duration is part of
    the identity, so a 1h block and a 30m block starting together stay separate
    rows — the app would otherwise have to pick one and misreport the other.
    """
    title = " ".join((e.get("title") or "").split()).casefold()
    return (title, _instant(e.get("start")), _instant(e.get("end")),
            bool(e.get("allDay")))


def merge_events(events):
    """Collapse copies of one appointment into a single badge-merged row.

    Copy-on-write; never mutates inputs."""
    merged = {}
    order = []
    for ev in events:
        key = _identity(ev)
        if key not in merged:
            merged[key] = {**ev, "labels": list(ev["labels"]),
                           "calendars": list(ev["calendars"]),
                           "sources": list(ev.get("sources", []))}
            order.append(key)
        else:
            m = merged[key]
            _extend_unique(m["labels"], ev["labels"])
            _extend_unique(m["calendars"], ev["calendars"])
            _extend_unique(m["sources"], ev.get("sources", []))
            m["busy"] = m["busy"] or ev["busy"]
            m["guests"] = max(m["guests"], ev["guests"])
            m["join"] = m["join"] or ev["join"]
            m["location"] = m["location"] or ev["location"]
    out = [merged[k] for k in order]
    out.sort(key=_event_instant)
    return out


# ── EVENT BODY ──────────────────────────────────────────────────────────
def build_event_body(payload, blocking, detail="full"):
    """Create-payload → Google events.insert body (spec §8).

    `blocking` sets transparency (does this occupy my time); `detail` sets
    visibility (can a viewer see what it is). Independent axes — "visible but
    free" and "opaque but anonymous" are both expressible.
    """
    anonymous = detail == "busy"
    title = "Busy" if anonymous else ((payload.get("title") or "").strip()
                                      or "Busy")
    body = {
        "summary": title,
        "transparency": "opaque" if blocking else "transparent",
    }
    date = payload["date"]
    if payload.get("allDay"):
        start_d = datetime.strptime(date, "%Y-%m-%d").date()
        end_d = start_d + timedelta(days=1)  # Google end date is exclusive
        body["start"] = {"date": start_d.isoformat()}
        body["end"] = {"date": end_d.isoformat()}
    else:
        body["start"] = {"dateTime": f"{date}T{payload['startTime']}:00",
                         "timeZone": TIMEZONE}
        body["end"] = {"dateTime": f"{date}T{payload['endTime']}:00",
                       "timeZone": TIMEZONE}
    if not anonymous:
        if payload.get("location"):
            body["location"] = payload["location"]
        if payload.get("notes"):
            body["description"] = payload["notes"]
    return body


def delete_target_id(source, scope):
    """Which Google id a delete addresses — the series only when asked for it
    and the event actually has one."""
    if scope == "series" and source.get("seriesId"):
        return source["seriesId"]
    return source.get("eventId", "")


def forward_addresses(payload):
    """Non-empty, whitespace-trimmed forward targets from a create payload."""
    return [a.strip() for a in (payload.get("forwardTo") or [])
            if a and a.strip()]


# ── DEMO ────────────────────────────────────────────────────────────────
def is_demo():
    return os.environ.get("OURCAL_DEMO") == "1"


def demo_accounts():
    return [{"label": "Personal", "email": "you@example.com"},
            {"label": "Work", "email": "you@work.example.com"}]


def _iso(dt):
    return dt.replace(microsecond=0).isoformat()


def _utc(iso):
    """Same instant, re-expressed the way a UTC-configured calendar reports it."""
    return _instant(iso).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _demo_fixtures(now):
    """Realistic fixtures relative to `now` (tz-aware)."""
    def at(day, h, m=0):
        return _iso(now.replace(hour=h, minute=m, second=0, microsecond=0)
                    + timedelta(days=day))
    raw = [
        # ADPList-style mentoring session with a Meet link (2h out)
        {"iCalUID": "adplist-1", "id": "adplist-1", "summary": "ADPList Mentoring — Career",
         "start": {"dateTime": _iso(now + timedelta(hours=2))},
         "end":   {"dateTime": _iso(now + timedelta(hours=3))},
         "hangoutLink": "https://meet.google.com/adp-list-demo",
         "attendees": [{"email": "mentee@x.com"}]},
        # Ordinary meeting today
        {"iCalUID": "standup-1", "id": "standup-1", "summary": "Team Standup",
         "start": {"dateTime": at(0, 9)}, "end": {"dateTime": at(0, 9, 30)},
         "location": "Zoom", "attendees": [{"email": "a@x"}, {"email": "b@x"}]},
        # Non-blocking "free" event
        {"iCalUID": "focus-1", "id": "focus-1", "summary": "Focus block", "transparency": "transparent",
         "start": {"dateTime": at(1, 13)}, "end": {"dateTime": at(1, 15)}},
        # All-day event
        {"iCalUID": "trip-1", "id": "trip-1", "summary": "Offsite (all day)",
         "start": {"date": (now.date() + timedelta(days=2)).isoformat()},
         "end":   {"date": (now.date() + timedelta(days=3)).isoformat()}},
        # Event shared across two accounts (dedupe/badge-merge)
        {"iCalUID": "shared-1", "id": "shared-1", "summary": "Family dinner",
         "start": {"dateTime": at(3, 18)}, "end": {"dateTime": at(3, 19)}},
        # Typed separately into each account: its own uid per copy, and the
        # second calendar reports the same moment in UTC. Merges on title+span.
        {"iCalUID": "dentist-p", "id": "dentist-p", "summary": "Dentist appointment",
         "start": {"dateTime": at(4, 11)}, "end": {"dateTime": at(4, 12)}},
    ]
    personal = [normalize(r, "Personal", "Primary", "personal@demo") for r in raw]
    # same shared-1 → merges, and contributes a second source row
    work = [normalize(raw[-2], "Work", "Team", "work@demo")]
    twin = dict(raw[-1], iCalUID="dentist-w", id="dentist-w",
                start={"dateTime": _utc(at(4, 11))},
                end={"dateTime": _utc(at(4, 12))})
    work.append(normalize(twin, "Work", "Team", "work@demo"))
    return personal + work


_DEMO_STORE = None


def reset_demo(now=None):
    """(Re)seed the in-memory demo store. Used by tests and first demo read."""
    global _DEMO_STORE
    now = now or datetime.now(ZoneInfo(TIMEZONE))
    _DEMO_STORE = _demo_fixtures(now)


# ── SERVICE ─────────────────────────────────────────────────────────────
def _accounts_meta(accounts):
    return [{"label": a["label"], "email": a["email"], "color": i}
            for i, a in enumerate(accounts)]


def get_events(now=None):
    now = now or datetime.now(ZoneInfo(TIMEZONE))
    errors = []
    if is_demo():
        if _DEMO_STORE is None:
            reset_demo(now)
        accounts = demo_accounts()
        events = merge_events(_DEMO_STORE)
    else:
        accounts = ACCOUNTS
        events, errors = _google_collect(now)   # defined in GOOGLE section
    return {
        "updated": _iso(now),
        "timezone": TIMEZONE,
        "accounts": _accounts_meta(accounts),
        "events": events,
        "errors": errors,
    }


def create_event(payload):
    # Validate here, not in the backends, so demo and Google behave identically.
    bad = [a for a in forward_addresses(payload) if not valid_email(a)]
    if bad:   # reject before anything is created, so nothing is half-done
        return {"ok": False, "results": [],
                "error": "Invalid forward address: " + ", ".join(bad)}
    if is_demo():
        return _demo_create(payload)
    return _google_create(payload)               # defined in GOOGLE section


def delete_events(payload):
    if is_demo():
        return _demo_delete(payload)
    return _google_delete(payload)               # defined in GOOGLE section


def _demo_delete(payload):
    global _DEMO_STORE
    if _DEMO_STORE is None:
        reset_demo()
    scope = payload.get("scope", "occurrence")
    sources = payload.get("sources", [])
    doomed = {delete_target_id(s, scope) for s in sources}
    _DEMO_STORE = [e for e in _DEMO_STORE
                   if not any(src.get("eventId") in doomed
                              for src in e.get("sources", []))]
    return {"ok": True,
            "results": [{"label": s.get("label"), "ok": True} for s in sources]}


def _demo_create(payload):
    global _DEMO_STORE
    if _DEMO_STORE is None:
        reset_demo()
    results = []
    detail = payload.get("detail", "full")
    forward_to = forward_addresses(payload)
    for t in payload["targets"]:
        hosts_guest = bool(forward_to) and t["label"] == payload.get("forwardFrom")
        body = build_event_body(payload, t["blocking"],
                                "full" if hosts_guest else detail)
        uid = f"demo-{len(_DEMO_STORE)}-{t['label']}"
        raw = {"iCalUID": uid, "id": uid,
               "summary": body["summary"], "start": body["start"],
               "end": body["end"], "transparency": body.get("transparency"),
               "location": body.get("location"),
               "attendees": [{"email": a} for a in forward_to] if hosts_guest else []}
        _DEMO_STORE.append(normalize(raw, t["label"], "Primary",
                                     f"{slug(t['label'])}@demo"))
        results.append({"label": t["label"], "ok": True,
                        "htmlLink": "https://calendar.google.com/demo"})
        if payload.get("mode") == "invite":
            break  # invite: host creates one event; others are attendees
    return {"ok": all(r["ok"] for r in results), "results": results}


# ── GOOGLE ──────────────────────────────────────────────────────────────
SCOPES = ["https://www.googleapis.com/auth/calendar"]
_SKIP_SUBSTR = ("#holiday", "#contacts", "addressbook")


def should_include_calendar(item):
    cid = item.get("id", "")
    if any(s in cid for s in _SKIP_SUBSTR):
        return False
    return bool(item.get("selected") or item.get("primary"))


def token_path(label):
    return os.path.join(APP_DIR, f"token_{slug(label)}.json")


def primary_email(cals):
    """The signed-in account's address — the id of its primary calendar.

    Returns "" if no primary calendar is present (account undeterminable).
    """
    for cal in cals:
        if cal.get("primary"):
            return (cal.get("id") or "").strip().lower()
    return ""


def account_mismatch(label, expected, cals):
    """Error string if these calendars belong to a different account, else None.

    Guards against signing in with the wrong Google account at a given prompt,
    which would otherwise file that account's events under this label.
    """
    actual = primary_email(cals)
    if not actual or actual == (expected or "").strip().lower():
        return None
    return (f"signed in as {actual}, not {expected} — delete "
            f"token_{slug(label)}.json, restart, and pick {expected}")


def creds_for(label, email):
    """Load/refresh creds for an account; run InstalledAppFlow if absent."""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow
    path = token_path(label)
    creds = None
    if os.path.exists(path):
        creds = Credentials.from_authorized_user_file(path, SCOPES)
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    else:
        cred_file = os.path.join(APP_DIR, "credentials.json")
        if not os.path.exists(cred_file):
            raise FileNotFoundError(
                "credentials.json is missing from the OurCal folder — "
                "complete Steps 1-4 of SETUP_GUIDE.md")
        # Name the account before the browser opens: the prompts are identical
        # otherwise, and picking the wrong one silently mislabels its events.
        print(f"\nOurCal: sign in as {email}   (account “{label}”)\n"
              f"        Pick this exact account in the browser window.\n",
              flush=True)
        flow = InstalledAppFlow.from_client_secrets_file(cred_file, SCOPES)
        creds = flow.run_local_server(port=0)
    with open(path, "w") as f:
        f.write(creds.to_json())
    return creds


def service_for(label, email):
    from googleapiclient.discovery import build
    return build("calendar", "v3", credentials=creds_for(label, email),
                 cache_discovery=False)


def list_account_events(label, email, time_min, time_max):
    """Return (normalized_events, error_or_None) for one account."""
    try:
        svc = service_for(label, email)
        events = []
        page_token = None
        cals = []
        while True:
            resp = svc.calendarList().list(pageToken=page_token).execute()
            cals.extend(resp.get("items", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        mismatch = account_mismatch(label, email, cals)
        if mismatch:
            return [], mismatch   # never file another account's events here
        for cal in cals:
            if not should_include_calendar(cal):
                continue
            cal_name = cal.get("summaryOverride") or cal.get("summary") or cal["id"]
            page = None
            while True:
                resp = svc.events().list(
                    calendarId=cal["id"], singleEvents=True, orderBy="startTime",
                    timeMin=time_min, timeMax=time_max, pageToken=page,
                    maxResults=2500).execute()
                for raw in resp.get("items", []):
                    events.append(normalize(raw, label, cal_name, cal["id"],
                                            cal.get("accessRole")))
                page = resp.get("nextPageToken")
                if not page:
                    break
        return events, None
    except FileNotFoundError as e:  # setup incomplete — "re-auth" would mislead
        return [], str(e)
    except Exception as e:  # per-account isolation
        return [], f"{type(e).__name__} — re-auth or check access"


def _google_collect(now):
    time_min = _iso(now)
    time_max = _iso(now + timedelta(days=DAYS_AHEAD))
    all_events, errors = [], []
    for a in ACCOUNTS:
        evs, err = list_account_events(a["label"], a["email"], time_min, time_max)
        all_events.extend(evs)
        if err:
            errors.append({"label": a["label"], "message": err})
    return merge_events(all_events), errors


def _email_for(label):
    for a in ACCOUNTS:
        if a["label"] == label:
            return a["email"]
    return None


def is_already_gone(exc):
    """A delete that 404s/410s already achieved its goal — count it a success."""
    status = getattr(getattr(exc, "resp", None), "status", None)
    return status in (404, 410)


def _google_delete(payload):
    scope = payload.get("scope", "occurrence")
    results = []
    for s in payload.get("sources", []):
        label = s.get("label")
        try:
            svc = service_for(label, _email_for(label))
            svc.events().delete(calendarId=s.get("calendarId") or "primary",
                                eventId=delete_target_id(s, scope),
                                sendUpdates="none").execute()
            results.append({"label": label, "ok": True})
        except Exception as e:   # per-source isolation, as with collect
            if is_already_gone(e):
                results.append({"label": label, "ok": True,
                                "note": "already gone"})
            else:
                results.append({"label": label, "ok": False, "error": str(e)})
    return {"ok": all(r["ok"] for r in results), "results": results}


def _google_create(payload):
    mode = payload.get("mode", "copies")
    targets = payload["targets"]
    results = []
    forward_to = forward_addresses(payload)
    if mode == "invite":
        host = payload["inviteFrom"]
        attendees = [{"email": _email_for(t["label"])}
                     for t in targets if t["label"] != host]
        host_blocking = next((t["blocking"] for t in targets
                              if t["label"] == host), True)
        body = build_event_body(payload, host_blocking)
        if attendees:
            body["attendees"] = attendees
        try:
            svc = service_for(host, _email_for(host))
            ev = svc.events().insert(calendarId="primary", body=body,
                                     sendUpdates="all").execute()
            results.append({"label": host, "ok": True,
                            "htmlLink": ev.get("htmlLink")})
        except Exception as e:
            results.append({"label": host, "ok": False, "error": str(e)})
    else:  # copies
        detail = payload.get("detail", "full")
        forward_from = payload.get("forwardFrom")
        for t in targets:
            # The copy hosting an outside guest must be readable to them,
            # whatever privacy the user's own mirrors are getting.
            hosts_guest = bool(forward_to) and t["label"] == forward_from
            body = build_event_body(payload, t["blocking"],
                                    "full" if hosts_guest else detail)
            if hosts_guest:
                body["attendees"] = [{"email": a} for a in forward_to]
            try:
                svc = service_for(t["label"], _email_for(t["label"]))
                ev = svc.events().insert(
                    calendarId="primary", body=body,
                    sendUpdates="all" if hosts_guest else "none").execute()
                results.append({"label": t["label"], "ok": True,
                                "htmlLink": ev.get("htmlLink")})
            except Exception as e:
                results.append({"label": t["label"], "ok": False,
                                "error": str(e)})
    return {"ok": all(r["ok"] for r in results), "results": results}


# ── HTML ────────────────────────────────────────────────────────────────
PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OurCal</title>
<style>
  :root{
    --bg:#f5f6f8; --card:#ffffff; --card2:#fafbfc; --text:#161a1d; --muted:#67717b;
    --border:#e4e7eb; --shadow:0 1px 2px rgba(0,0,0,.06),0 4px 12px rgba(0,0,0,.05);
    --accent:#2a78d6; --live:#1baf7a; --danger:#d64545; --chip:#eef1f4;
    --free-bg:#e9f3ec; --free-fg:#2f7d4f; color-scheme:light;
  }
  @media (prefers-color-scheme: dark){
    :root:not([data-theme="light"]){
      --bg:#0f1419; --card:#171d24; --card2:#1c232b; --text:#e6eaed; --muted:#98a2ac;
      --border:#29323b; --shadow:0 1px 2px rgba(0,0,0,.4),0 6px 18px rgba(0,0,0,.35);
      --accent:#5b9cf0; --live:#3fd39c; --danger:#f07a7a; --chip:#222b34;
      --free-bg:#15241d; --free-fg:#5fd39c; color-scheme:dark;
    }
  }
  :root[data-theme="dark"]{
    --bg:#0f1419; --card:#171d24; --card2:#1c232b; --text:#e6eaed; --muted:#98a2ac;
    --border:#29323b; --shadow:0 1px 2px rgba(0,0,0,.4),0 6px 18px rgba(0,0,0,.35);
    --accent:#5b9cf0; --live:#3fd39c; --danger:#f07a7a; --chip:#222b34;
    --free-bg:#15241d; --free-fg:#5fd39c; color-scheme:dark;
  }
  *{box-sizing:border-box}
  html,body{margin:0}
  body{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;font-size:15px;line-height:1.4}
  .wrap{max-width:880px;margin:0 auto;padding:20px 16px 90px}
  header{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:18px}
  h1{font-size:22px;margin:0;font-weight:750;letter-spacing:-.02em}
  .stamp{color:var(--muted);font-size:13px}
  .grow{flex:1}
  button{font:inherit;cursor:pointer;border-radius:9px;border:1px solid var(--border);background:var(--card);color:var(--text);padding:8px 12px}
  button:hover{border-color:var(--accent)}
  .btn-primary{background:var(--accent);color:#fff;border-color:var(--accent);font-weight:600}
  .btn-primary:hover{filter:brightness(1.06)}
  .icon-btn{padding:8px 10px}
  .tiles{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:16px}
  .tile{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:12px 14px;box-shadow:var(--shadow)}
  .tile .n{font-size:23px;font-weight:750;letter-spacing:-.02em}
  .tile .l{color:var(--muted);font-size:12px;margin-top:2px}
  .chips{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:18px}
  .chip{display:inline-flex;align-items:center;gap:7px;background:var(--chip);border:1px solid transparent;border-radius:999px;padding:5px 11px;font-size:13px;cursor:pointer;user-select:none}
  .chip .dot{width:9px;height:9px;border-radius:50%;flex-shrink:0}
  .chip.off{opacity:.42;text-decoration:line-through}
  .chip .ct{color:var(--muted);font-variant-numeric:tabular-nums}
  .banner{background:var(--card);border:1px solid var(--border);border-left:3px solid var(--danger);border-radius:10px;padding:10px 12px;margin-bottom:10px;font-size:13px}
  .day{margin:20px 0 8px;font-weight:700;font-size:14px;color:var(--muted)}
  .ev{display:flex;gap:12px;background:var(--card);border:1px solid var(--border);border-radius:12px;padding:11px 13px 11px 16px;margin-bottom:8px;box-shadow:var(--shadow);position:relative;overflow:hidden}
  .ev .bar{position:absolute;left:0;top:0;bottom:0;width:4px}
  .ev.live{outline:2px solid var(--live);outline-offset:-2px}
  .ev .when{min-width:92px;flex-shrink:0}
  .ev .when .t1{font-weight:600}
  .ev .when .t2{color:var(--muted);font-size:12px}
  .ev .body{flex:1;min-width:0}
  .ev .title{font-weight:600;margin-bottom:3px;word-break:break-word}
  .ev .meta{display:flex;flex-wrap:wrap;gap:6px 10px;align-items:center;font-size:12px;color:var(--muted)}
  .badge{display:inline-flex;align-items:center;font-size:11px;font-weight:600;padding:1px 8px;border-radius:999px}
  .pill-free{background:var(--free-bg);color:var(--free-fg);font-weight:600;font-size:11px;padding:1px 8px;border-radius:999px}
  .join{color:var(--accent);text-decoration:none;font-weight:600}
  .join:hover{text-decoration:underline}
  .live-tag{color:var(--live);font-weight:700;font-size:11px;margin-left:6px}
  .empty{color:var(--muted);text-align:center;padding:56px 0}
  .modal{position:fixed;inset:0;background:rgba(0,0,0,.45);display:none;align-items:flex-start;justify-content:center;padding:24px;overflow:auto;z-index:50}
  .modal.open{display:flex}
  .sheet{background:var(--card);border:1px solid var(--border);border-radius:16px;box-shadow:var(--shadow);width:100%;max-width:520px;padding:20px}
  .sheet h2{margin:0 0 14px;font-size:18px}
  .field{margin-bottom:12px}
  .field>label{display:block;font-size:12px;color:var(--muted);margin-bottom:4px}
  .field input[type=text],.field input[type=date],.field input[type=time],.field textarea{width:100%;padding:8px 10px;border:1px solid var(--border);border-radius:9px;background:var(--card2);color:var(--text);font:inherit}
  .row2{display:flex;gap:10px}
  .row2 .field{flex:1}
  .seg{display:inline-flex;border:1px solid var(--border);border-radius:8px;overflow:hidden}
  .seg button{border:none;border-radius:0;background:var(--card2);padding:5px 10px;font-size:12px}
  .seg button.on{background:var(--accent);color:#fff}
  .modes{display:flex;gap:8px}
  .mode{flex:1;border:1px solid var(--border);border-radius:10px;padding:8px 10px;cursor:pointer}
  .mode.on{border-color:var(--accent);background:var(--card2)}
  .mode .mt{font-weight:600;font-size:13px}
  .mode .md{color:var(--muted);font-size:11px}
  .acct{display:flex;align-items:center;gap:9px;padding:9px 0;border-top:1px solid var(--border)}
  .acct .dot{width:10px;height:10px;border-radius:50%;flex-shrink:0}
  .acct .who{flex:1;min-width:0}
  .acct .who .nm{font-weight:600;font-size:13px}
  .acct .who .em{color:var(--muted);font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .acct label.hostwrap{font-size:11px;color:var(--muted);display:inline-flex;align-items:center;gap:4px}
  .actions{display:flex;gap:10px;justify-content:flex-end;margin-top:16px}
  .btn-danger{background:#d1443c;color:#fff;border-color:#d1443c;font-weight:600}
  .btn-danger:hover{filter:brightness(1.08)}
  .rowacts{display:flex;gap:6px;align-items:center;margin-left:auto;flex-shrink:0;opacity:0;transition:opacity .12s}
  .ev:hover .rowacts,.ev:focus-within .rowacts{opacity:1}
  .mini{font-size:11px;padding:3px 9px;border-radius:7px;color:var(--muted)}
  .mini:hover{color:var(--text)}
  .mini.danger:hover{color:#d1443c;border-color:#d1443c}
  .hint{font-size:11px;color:var(--muted);margin-top:5px}
  .delsum{background:var(--card2);border:1px solid var(--border);border-radius:9px;padding:10px 12px;margin-bottom:14px}
  .delsum .dt{font-weight:600}
  .delsum .dw{font-size:12px;color:var(--muted);margin-top:2px}
  .scoperow{display:flex;align-items:center;gap:7px;padding:5px 0;font-size:13px}
  .scoperow .sub{color:var(--muted);font-size:11px}
  @media (max-width:560px){ .rowacts{opacity:1} }
  .toasts{position:fixed;right:16px;bottom:16px;display:flex;flex-direction:column;gap:8px;z-index:60}
  .toast{background:var(--card);border:1px solid var(--border);border-left:3px solid var(--live);border-radius:10px;padding:10px 14px;box-shadow:var(--shadow);font-size:13px;max-width:320px}
  .toast.err{border-left-color:var(--danger)}
  @media (max-width:640px){ .tiles{grid-template-columns:repeat(2,1fr)} }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>OurCal</h1>
    <span class="stamp" id="stamp"></span>
    <span class="grow"></span>
    <button class="icon-btn" id="themeBtn" title="Toggle light / dark">🌓</button>
    <button id="refreshBtn" title="Refresh now">↻ Refresh</button>
    <button class="btn-primary" id="newBtn">+ New event / Block time</button>
  </header>
  <div class="tiles" id="tiles"></div>
  <div class="chips" id="chips"></div>
  <div id="banner"></div>
  <div id="agenda"></div>
</div>

<div class="modal" id="modal">
  <div class="sheet">
    <h2>New event / Block time</h2>
    <div class="field"><label>Title</label><input type="text" id="f-title" placeholder="Busy"></div>
    <div class="row2">
      <div class="field"><label>Date</label><input type="date" id="f-date"></div>
      <div class="field" id="wrap-start"><label>Start</label><input type="time" id="f-start"></div>
      <div class="field" id="wrap-end"><label>End</label><input type="time" id="f-end"></div>
    </div>
    <div class="field"><label style="display:inline-flex;gap:6px;align-items:center;color:var(--text)"><input type="checkbox" id="f-allday"> All-day</label></div>
    <div class="field"><label>Location</label><input type="text" id="f-loc"></div>
    <div class="field"><label>Notes</label><textarea id="f-notes" rows="2"></textarea></div>
    <div class="field">
      <label>Mode</label>
      <div class="modes">
        <div class="mode on" id="mode-copies"><div class="mt">Copies</div><div class="md">An independent event on each selected calendar.</div></div>
        <div class="mode" id="mode-invite"><div class="mt">Invite</div><div class="md">One host emails the others a shared invite (RSVP).</div></div>
      </div>
    </div>
    <div class="field"><label>Send to</label><div id="acctRows"></div></div>
    <div class="field" id="wrap-detail">
      <label>Copy as</label>
      <span class="seg" id="seg-detail"><button type="button" class="on" data-v="full">Full details</button><button type="button" data-v="busy">Busy block only</button></span>
      <div class="hint">Busy block hides the title, location, and notes — only the time is held.</div>
    </div>
    <div class="field" id="wrap-fwd">
      <label>Forward invite to (optional)</label>
      <input type="text" id="f-fwd" placeholder="sam@example.com, kim@example.org">
      <div class="hint">Sent from <select id="f-fwdfrom"></select> — that copy always carries full details so the invite is readable.</div>
    </div>
    <div class="actions">
      <button id="cancelBtn">Cancel</button>
      <button class="btn-primary" id="createBtn">Create</button>
    </div>
  </div>
</div>

<div class="modal" id="delModal">
  <div class="sheet">
    <h2>Delete event</h2>
    <div class="delsum" id="del-summary"></div>
    <div class="field"><label>Remove from</label><div id="delRows"></div></div>
    <div class="field" id="wrap-scope" style="display:none">
      <label>Scope</label>
      <div class="scoperow"><input type="radio" name="delscope" id="scope-occ" value="occurrence" checked><label for="scope-occ" style="color:var(--text)">This occurrence only</label></div>
      <div class="scoperow"><input type="radio" name="delscope" id="scope-ser" value="series"><label for="scope-ser" style="color:var(--text)">The entire series <span class="sub">— every occurrence, past and future</span></label></div>
    </div>
    <div class="hint">Deleted events go to your Google Calendar trash and can be restored for about 30 days.</div>
    <div class="actions">
      <button id="delCancelBtn">Cancel</button>
      <button class="btn-danger" id="delConfirmBtn">Delete</button>
    </div>
  </div>
</div>
<div class="toasts" id="toasts"></div>

<script>
const POLL_MS = __POLL_MS__;
const PAL_LIGHT = ["#2a78d6","#eb6834","#1baf7a","#eda100","#e87ba4"];
const PAL_DARK  = ["#5b9cf0","#ff8a5c","#3fd39c","#ffc23d","#ff9ec4"];
let DATA = null;
const hidden = new Set();
let mode = "copies";
let ROWS = [];        // events as currently rendered, indexed by row buttons
let DEL_EV = null;    // event awaiting delete confirmation

function isDark(){
  const t = document.documentElement.getAttribute("data-theme");
  if(t) return t === "dark";
  return matchMedia("(prefers-color-scheme: dark)").matches;
}
function colorFor(i){ return (isDark()?PAL_DARK:PAL_LIGHT)[i % PAL_LIGHT.length]; }
function acctByLabel(l){ return (DATA.accounts||[]).find(a=>a.label===l); }
function colorForLabel(l){ const a=acctByLabel(l); return colorFor(a?a.color:0); }
function tz(){ return DATA ? DATA.timezone : "UTC"; }
function esc(s){ return (s==null?"":String(s)).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c])); }
function val(id){ return document.getElementById(id).value.trim(); }

function dayKey(iso, allDay){
  if(allDay) return String(iso).slice(0,10);
  return new Intl.DateTimeFormat("en-CA",{timeZone:tz(),year:"numeric",month:"2-digit",day:"2-digit"}).format(new Date(iso));
}
function todayKey(){
  return new Intl.DateTimeFormat("en-CA",{timeZone:tz(),year:"numeric",month:"2-digit",day:"2-digit"}).format(new Date());
}
function fmtTime(iso){
  return new Intl.DateTimeFormat("en-US",{timeZone:tz(),hour:"numeric",minute:"2-digit"}).format(new Date(iso));
}
function fmtDayHeading(key){
  const [y,m,d] = key.split("-").map(Number);
  const dt = new Date(Date.UTC(y,m-1,d,12));
  const wd = new Intl.DateTimeFormat("en-US",{timeZone:"UTC",weekday:"long",month:"short",day:"numeric"}).format(dt);
  if(key===todayKey()) return "Today · "+wd;
  const tmr=new Date(); tmr.setDate(tmr.getDate()+1);
  const tk=new Intl.DateTimeFormat("en-CA",{timeZone:tz(),year:"numeric",month:"2-digit",day:"2-digit"}).format(tmr);
  if(key===tk) return "Tomorrow · "+wd;
  return wd;
}
function durText(ev){
  if(ev.allDay) return "All day";
  const mins=Math.max(0,Math.round((new Date(ev.end)-new Date(ev.start))/60000));
  const h=Math.floor(mins/60), m=mins%60;
  return (h?h+"h":"")+(h&&m?" ":"")+((m||!h)?m+"m":"");
}
function isLive(ev){
  if(ev.allDay) return false;
  const now=Date.now();
  return new Date(ev.start)<=now && now<new Date(ev.end);
}
function visible(ev){ return ev.labels.some(l=>!hidden.has(l)); }
function labelCount(l){ return DATA.events.filter(e=>e.labels.includes(l)).length; }

function nextDayKeys(n){
  const keys=new Set(), base=new Date();
  for(let i=0;i<n;i++){
    const d=new Date(base.getTime()+i*86400000);
    keys.add(new Intl.DateTimeFormat("en-CA",{timeZone:tz(),year:"numeric",month:"2-digit",day:"2-digit"}).format(d));
  }
  return keys;
}
function computeStats(evs){
  const tk=todayKey(), now=Date.now(), week=nextDayKeys(7);
  let today=0,next7=0,hours=0,nextStart=null;
  for(const ev of evs){
    if(ev.allDay) continue;               // all tiles use envelope-tz day keys
    const k=dayKey(ev.start,false);
    const s=new Date(ev.start), e=new Date(ev.end);
    if(k===tk) today++;
    if(week.has(k)){ next7++; hours+=Math.max(0,(e-s)/3600000); }
    if(s.getTime()>now && (!nextStart || s<nextStart)) nextStart=s;
  }
  return {today,next7,hours,nextStart};
}
function countdown(nextStart){
  if(!nextStart) return "—";
  let m=Math.round((nextStart-Date.now())/60000);
  if(m<1) return "now";
  const h=Math.floor(m/60); m%=60;
  return "in "+(h?h+"h ":"")+m+"m";
}

function render(){
  if(!DATA) return;
  try{ document.getElementById("stamp").textContent="updated "+new Intl.DateTimeFormat("en-US",{timeZone:tz(),hour:"2-digit",minute:"2-digit"}).format(new Date(DATA.updated)); }catch(e){}
  const s=computeStats(DATA.events);
  const tiles=[[s.today,"meetings today"],[s.next7,"next 7 days"],[s.hours.toFixed(1),"hours in meetings (7d)"],[countdown(s.nextStart),"until next meeting"]];
  document.getElementById("tiles").innerHTML=tiles.map(t=>`<div class="tile"><div class="n">${esc(t[0])}</div><div class="l">${t[1]}</div></div>`).join("");

  const chips=document.getElementById("chips");
  chips.innerHTML=(DATA.accounts||[]).map(a=>`<span class="chip ${hidden.has(a.label)?"off":""}" data-label="${esc(a.label)}"><span class="dot" style="background:${colorFor(a.color)}"></span><span>${esc(a.label)}</span><span class="ct">${labelCount(a.label)}</span></span>`).join("");
  chips.querySelectorAll(".chip").forEach(c=>c.onclick=()=>{ const l=c.getAttribute("data-label"); hidden.has(l)?hidden.delete(l):hidden.add(l); render(); });

  const banner=document.getElementById("banner");
  banner.innerHTML=(DATA.errors&&DATA.errors.length)?DATA.errors.map(e=>`<div class="banner">⚠️ Couldn't refresh <b>${esc(e.label)}</b> — ${esc(e.message)}</div>`).join(""):"";

  const box=document.getElementById("agenda");
  const evs=DATA.events.filter(visible);
  if(!evs.length){ box.innerHTML='<div class="empty">No events in the next 30 days 🎉</div>'; return; }
  const groups={}, keys=[];
  for(const ev of evs){ const k=dayKey(ev.start,ev.allDay); if(!groups[k]){groups[k]=[];keys.push(k);} groups[k].push(ev); }
  keys.sort();
  let html="";
  ROWS=[];
  for(const k of keys){
    html+=`<div class="day">${esc(fmtDayHeading(k))}</div>`;
    for(const ev of groups[k]){ try{ html+=evRow(ev,ROWS.push(ev)-1); }catch(e){} }
  }
  box.innerHTML=html;
  box.querySelectorAll(".mini").forEach(b=>b.onclick=()=>{
    const ev=ROWS[Number(b.dataset.idx)];
    if(!ev) return;
    guard(()=>b.dataset.act==="sync"?openSync(ev):openDelete(ev))();
  });
}
/* One row can belong to several calendars. Segment the accent bar so that
   reads at a glance, instead of the row wearing only its first label's color. */
function barFill(labels){
  const cs=labels.map(colorForLabel);
  if(cs.length<2) return cs[0]||"transparent";
  const step=100/cs.length;
  return `linear-gradient(${cs.map((c,i)=>`${c} ${i*step}% ${(i+1)*step}%`).join(",")})`;
}
function evRow(ev,idx){
  const c=barFill(ev.labels);
  const live=isLive(ev);
  const when=ev.allDay?'<div class="t1">All day</div>':`<div class="t1">${esc(fmtTime(ev.start))}</div><div class="t2">${esc(durText(ev))}</div>`;
  const badges=ev.labels.map(l=>`<span class="badge" style="color:${colorForLabel(l)};background:${colorForLabel(l)}22">${esc(l)}</span>`).join("");
  const free=ev.busy?"":'<span class="pill-free">free</span>';
  const loc=ev.location?`<span>📍 ${esc(ev.location)}</span>`:"";
  const guests=ev.guests>0?`<span>👥 ${ev.guests}</span>`:"";
  const safeJoin=(ev.join&&/^https?:\/\//i.test(ev.join))?ev.join:null;
  const join=safeJoin?`<a class="join" href="${encodeURI(safeJoin)}" target="_blank" rel="noopener">Join ↗</a>`:"";
  const livetag=live?'<span class="live-tag">● LIVE</span>':"";
  const canDelete=(ev.sources||[]).some(s=>s.eventId);
  const acts=`<div class="rowacts"><button class="mini" data-act="sync" data-idx="${idx}">Sync…</button>`+
    (canDelete?`<button class="mini danger" data-act="del" data-idx="${idx}">Delete…</button>`:"")+`</div>`;
  return `<div class="ev ${live?"live":""}"><span class="bar" style="background:${c}"></span><div class="when">${when}</div><div class="body"><div class="title">${esc(ev.title)}${livetag}</div><div class="meta">${badges}${free}${loc}${guests}${join}</div></div>${acts}</div>`;
}

function load(){
  fetch("/api/events").then(r=>r.json()).then(d=>{ DATA=d; render(); })
    .catch(()=>{ document.getElementById("banner").innerHTML='<div class="banner">⚠️ Could not reach the OurCal server.</div>'; });
}

/* theme */
function applyTheme(t){ t?document.documentElement.setAttribute("data-theme",t):document.documentElement.removeAttribute("data-theme"); }
(function(){ const s=localStorage.getItem("ourcal-theme"); if(s) applyTheme(s); })();
document.getElementById("themeBtn").onclick=()=>{
  const cur=document.documentElement.getAttribute("data-theme");
  const next=cur==="dark"?"light":(cur==="light"?"dark":(isDark()?"light":"dark"));
  applyTheme(next); localStorage.setItem("ourcal-theme",next); if(DATA) render();
};
matchMedia("(prefers-color-scheme: dark)").addEventListener("change",()=>{ if(!document.documentElement.getAttribute("data-theme")&&DATA) render(); });

/* modal + create */
function pad(n){ return String(n).padStart(2,"0"); }
function buildAcctRows(preselect){
  const box=document.getElementById("acctRows"); box.innerHTML="";
  (DATA.accounts||[]).forEach((a,idx)=>{
    const on=preselect?preselect.has(a.label):idx===0;
    const row=document.createElement("div"); row.className="acct";
    row.innerHTML=`<input type="checkbox" class="inc" ${on?"checked":""} data-label="${esc(a.label)}">`+
      `<span class="dot" style="background:${colorFor(a.color)}"></span>`+
      `<span class="who"><div class="nm">${esc(a.label)}</div><div class="em">${esc(a.email)}</div></span>`+
      `<label class="hostwrap" style="display:none"><input type="radio" name="host" class="host" value="${esc(a.label)}" ${idx===0?"checked":""}> host</label>`+
      `<span class="seg bf"><button type="button" class="on" data-v="busy">Busy</button><button type="button" data-v="free">Free</button></span>`;
    box.appendChild(row);
  });
  box.querySelectorAll(".seg.bf button").forEach(b=>b.onclick=()=>{ const seg=b.parentElement; seg.querySelectorAll("button").forEach(x=>x.classList.remove("on")); b.classList.add("on"); });
  box.querySelectorAll(".host").forEach(r=>r.onchange=applyMode);
  applyMode();
}
function applyMode(){
  document.getElementById("mode-copies").classList.toggle("on",mode==="copies");
  document.getElementById("mode-invite").classList.toggle("on",mode==="invite");
  const invite=mode==="invite";
  document.querySelectorAll("#acctRows .acct").forEach(row=>{
    const hw=row.querySelector(".hostwrap"), hostR=row.querySelector(".host"), seg=row.querySelector(".seg.bf");
    hw.style.display=invite?"":"none";
    seg.style.display=(invite && !hostR.checked)?"none":"";
  });
  // detail + forwarding describe independent copies; invite mode makes one
  // shared event and carries its own guest list.
  document.getElementById("wrap-detail").style.display=invite?"none":"";
  document.getElementById("wrap-fwd").style.display=invite?"none":"";
}
function openModal(){
  const now=new Date();
  document.getElementById("f-date").value=new Intl.DateTimeFormat("en-CA",{timeZone:tz(),year:"numeric",month:"2-digit",day:"2-digit"}).format(now);
  const st=new Date(now); st.setMinutes(now.getMinutes()<30?30:60,0,0);
  document.getElementById("f-start").value=pad(st.getHours())+":"+pad(st.getMinutes());
  const en=new Date(st.getTime()+30*60000);
  document.getElementById("f-end").value=pad(en.getHours())+":"+pad(en.getMinutes());
  document.getElementById("f-title").value=""; document.getElementById("f-loc").value=""; document.getElementById("f-notes").value="";
  document.getElementById("f-allday").checked=false; document.getElementById("wrap-start").style.display=""; document.getElementById("wrap-end").style.display="";
  document.querySelector("#modal h2").textContent="New event / Block time";
  document.getElementById("createBtn").textContent="Create";
  document.getElementById("f-fwd").value=""; setDetail("full");
  mode="copies"; buildAcctRows(); buildFwdFrom();
  document.getElementById("modal").classList.add("open");
}
function closeModal(){ document.getElementById("modal").classList.remove("open"); }

/* ---- sync an existing event into other calendars ---- */
function dateVal(iso,allDay){ return dayKey(iso,allDay); }
function timeVal(iso){
  const p=new Intl.DateTimeFormat("en-GB",{timeZone:tz(),hour:"2-digit",minute:"2-digit",hourCycle:"h23"}).formatToParts(new Date(iso));
  const g=t=>(p.find(x=>x.type===t)||{}).value||"00";
  return g("hour")+":"+g("minute");
}
function setDetail(v){
  document.querySelectorAll("#seg-detail button").forEach(b=>b.classList.toggle("on",b.dataset.v===v));
}
function currentDetail(){
  const b=document.querySelector("#seg-detail button.on");
  return b?b.dataset.v:"full";
}
function buildFwdFrom(){
  const sel=document.getElementById("f-fwdfrom");
  sel.innerHTML=(DATA.accounts||[]).map(a=>`<option value="${esc(a.label)}">${esc(a.label)}</option>`).join("");
}
function openSync(ev){
  document.querySelector("#modal h2").textContent="Sync event to other calendars";
  document.getElementById("createBtn").textContent="Sync";
  document.getElementById("f-title").value=ev.title||"";
  document.getElementById("f-date").value=dateVal(ev.start,ev.allDay);
  document.getElementById("f-allday").checked=!!ev.allDay;
  const h=ev.allDay?"none":"";
  document.getElementById("wrap-start").style.display=h;
  document.getElementById("wrap-end").style.display=h;
  if(!ev.allDay){
    document.getElementById("f-start").value=timeVal(ev.start);
    document.getElementById("f-end").value=timeVal(ev.end);
  }
  document.getElementById("f-loc").value=ev.location||"";
  document.getElementById("f-notes").value="";
  document.getElementById("f-fwd").value="";
  setDetail("full");
  mode="copies";
  // Default to the calendars this event is NOT already on — that's the point.
  const already=new Set(ev.labels||[]);
  const others=(DATA.accounts||[]).filter(a=>!already.has(a.label)).map(a=>a.label);
  buildAcctRows(new Set(others.length?others:(DATA.accounts||[]).map(a=>a.label)));
  buildFwdFrom();
  const from=document.getElementById("f-fwdfrom");
  if(already.size) from.value=[...already][0];
  document.getElementById("modal").classList.add("open");
}

/* ---- delete an event at its source ---- */
function openDelete(ev){
  DEL_EV=ev;
  const srcs=(ev.sources||[]).filter(s=>s.eventId);
  const when=ev.allDay?"All day":fmtTime(ev.start);
  document.getElementById("del-summary").innerHTML=
    `<div class="dt">${esc(ev.title)}</div><div class="dw">${esc(fmtDayHeading(dayKey(ev.start,ev.allDay)))} · ${esc(when)}</div>`;
  const box=document.getElementById("delRows"); box.innerHTML="";
  srcs.forEach((s,i)=>{
    const row=document.createElement("div"); row.className="acct";
    row.innerHTML=`<input type="checkbox" class="delsrc" checked data-i="${i}">`+
      `<span class="dot" style="background:${colorForLabel(s.label)}"></span>`+
      `<span class="who"><div class="nm">${esc(s.label)}</div><div class="em">${esc(s.calendarName||"")}</div></span>`;
    box.appendChild(row);
  });
  const recurring=srcs.some(s=>s.seriesId);
  document.getElementById("wrap-scope").style.display=recurring?"":"none";
  document.getElementById("scope-occ").checked=true;
  document.getElementById("delModal").classList.add("open");
}
function closeDelete(){ document.getElementById("delModal").classList.remove("open"); DEL_EV=null; }
function submitDelete(){
  if(!DEL_EV) return;
  const srcs=(DEL_EV.sources||[]).filter(s=>s.eventId);
  const chosen=[...document.querySelectorAll(".delsrc")].filter(c=>c.checked).map(c=>srcs[Number(c.dataset.i)]);
  if(!chosen.length){ toast("Pick at least one calendar",true); return; }
  const ser=document.getElementById("scope-ser");
  const scope=(ser&&ser.checked)?"series":"occurrence";
  const btn=document.getElementById("delConfirmBtn"); btn.disabled=true;
  fetch("/api/delete",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({scope,sources:chosen})})
    .then(r=>r.json()).then(res=>{
      const ok=(res.results||[]).filter(x=>x.ok).map(x=>x.label);
      const bad=(res.results||[]).filter(x=>!x.ok).map(x=>x.label);
      if(res.ok) toast("Deleted from "+ok.join(", "));
      else toast("Deleted from "+(ok.join(", ")||"none")+" · failed: "+bad.join(", "),true);
      closeDelete(); load();
    }).catch(e=>toast("Delete failed: "+e,true))
    .finally(()=>{ btn.disabled=false; });
}
function toast(msg,err){
  const t=document.createElement("div"); t.className="toast"+(err?" err":""); t.textContent=msg;
  document.getElementById("toasts").appendChild(t);
  setTimeout(()=>t.remove(), err?7000:4000);
}
/* A thrown error must never look like a dead button — always say something. */
function guard(fn){
  return function(){
    try{ return fn.apply(null,arguments); }
    catch(e){ console.error(e); toast("Something went wrong: "+(e&&e.message?e.message:e),true); }
  };
}
function submit(){
  const allday=document.getElementById("f-allday").checked;
  const payload={title:val("f-title"),date:val("f-date"),startTime:val("f-start"),endTime:val("f-end"),allDay:allday,notes:val("f-notes"),location:val("f-loc"),mode:mode,targets:[],inviteFrom:null,detail:"full",forwardTo:[],forwardFrom:null};
  if(mode==="copies"){
    payload.detail=currentDetail();
    payload.forwardTo=val("f-fwd").split(/[,;\s]+/).filter(Boolean);
    payload.forwardFrom=document.getElementById("f-fwdfrom").value||null;
    const bad=payload.forwardTo.filter(a=>!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(a));
    if(bad.length){ toast("Not a valid address: "+bad.join(", "),true); return; }
  }
  if(!payload.date){ toast("Pick a date",true); return; }
  if(!allday){
    if(!payload.startTime||!payload.endTime){ toast("Set a start and end time",true); return; }
    if(payload.endTime<=payload.startTime){ toast("End time must be after start",true); return; }
  }
  // Scope to #acctRows: the delete dialog also renders .acct rows, and those
  // have no .inc / .seg.bf.
  document.querySelectorAll("#acctRows .acct").forEach(row=>{
    const inc=row.querySelector(".inc"); if(!inc||!inc.checked) return;
    const seg=row.querySelector(".seg.bf button.on");
    payload.targets.push({label:inc.dataset.label, blocking:seg?seg.dataset.v==="busy":true});
  });
  if(!payload.targets.length){ toast("Pick at least one calendar",true); return; }
  if(payload.forwardTo.length && !payload.targets.some(t=>t.label===payload.forwardFrom)){
    toast("Tick the calendar you're sending the invite from ("+payload.forwardFrom+")",true); return;
  }
  if(mode==="invite"){
    const h=document.querySelector("#acctRows .host:checked"); const hv=h?h.value:null;
    payload.inviteFrom=(hv && payload.targets.some(t=>t.label===hv))?hv:payload.targets[0].label;
  }
  document.getElementById("createBtn").disabled=true;
  fetch("/api/create",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)})
    .then(r=>r.json()).then(res=>{
      const ok=(res.results||[]).filter(x=>x.ok).map(x=>x.label);
      const bad=(res.results||[]).filter(x=>!x.ok).map(x=>x.label);
      if(res.ok) toast("Created on "+ok.join(", "));
      else toast("Created on "+(ok.join(", ")||"none")+" · failed: "+bad.join(", "),true);
      closeModal(); load();
    }).catch(e=>toast("Create failed: "+e,true))
    .finally(()=>{ document.getElementById("createBtn").disabled=false; });
}

document.getElementById("newBtn").onclick=guard(openModal);
document.getElementById("cancelBtn").onclick=closeModal;
document.getElementById("createBtn").onclick=guard(submit);
document.getElementById("refreshBtn").onclick=load;
document.getElementById("mode-copies").onclick=()=>{mode="copies";applyMode();};
document.getElementById("mode-invite").onclick=()=>{mode="invite";applyMode();};
document.getElementById("f-allday").onchange=e=>{ const h=e.target.checked?"none":""; document.getElementById("wrap-start").style.display=h; document.getElementById("wrap-end").style.display=h; };
document.getElementById("modal").onclick=e=>{ if(e.target.id==="modal") closeModal(); };
document.querySelectorAll("#seg-detail button").forEach(b=>b.onclick=()=>setDetail(b.dataset.v));
document.getElementById("delCancelBtn").onclick=closeDelete;
document.getElementById("delConfirmBtn").onclick=guard(submitDelete);
document.getElementById("delModal").onclick=e=>{ if(e.target.id==="delModal") closeDelete(); };
document.addEventListener("keydown",e=>{ if(e.key==="Escape"){ closeModal(); closeDelete(); } });

/* optional deep links: ?theme=dark|light  and  ?new=1 (open create form) */
const params=new URLSearchParams(location.search);
const qt=params.get("theme"); if(qt==="dark"||qt==="light") applyTheme(qt);

load();
setInterval(load, POLL_MS);

if(params.get("new")==="1"){ const iv=setInterval(()=>{ if(DATA){ clearInterval(iv); openModal(); if(params.get("mode")==="invite"){ mode="invite"; applyMode(); } } }, 80); }
</script>
</body>
</html>"""


# ── HTTP ────────────────────────────────────────────────────────────────
class OurCalHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # quiet

    def _send(self, code, body, ctype="application/json"):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        try:
            if self.path == "/" or self.path.startswith("/?"):
                html = PAGE.replace("__POLL_MS__", str(POLL_MINUTES * 60000))
                self._send(200, html, "text/html; charset=utf-8")
            elif self.path == "/api/events":
                self._send(200, json.dumps(get_events()))
            else:
                self._send(404, json.dumps({"error": "not found"}))
        except Exception as e:
            self._send(500, json.dumps({"error": str(e)}))

    def do_POST(self):
        try:
            routes = {"/api/create": create_event, "/api/delete": delete_events}
            handler = routes.get(self.path)
            if handler is None:
                self._send(404, json.dumps({"error": "not found"}))
                return
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) or b"{}")
            self._send(200, json.dumps(handler(payload)))
        except Exception as e:
            self._send(500, json.dumps({"error": str(e)}))


def make_server(port):
    return ThreadingHTTPServer(("127.0.0.1", port), OurCalHandler)


def run_server():
    try:
        server = make_server(PORT)
    except OSError as e:
        print(f"OurCal: cannot bind 127.0.0.1:{PORT} ({e}). "
              f"Something may already use it — change PORT at the top of "
              f"ourcal.py or free the port (lsof -ti tcp:{PORT}).")
        raise SystemExit(1)
    url = f"http://127.0.0.1:{PORT}"
    print(f"OurCal running at {url}  (Ctrl-C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


# ── BOOTSTRAP ───────────────────────────────────────────────────────────
DEPS = ["google-api-python-client", "google-auth-oauthlib"]
VENV_DIR = os.path.join(APP_DIR, ".ourcal-venv")


def _google_importable():
    try:
        import googleapiclient  # noqa: F401
        import google_auth_oauthlib  # noqa: F401
        return True
    except ImportError:
        return False


def needs_bootstrap():
    if is_demo() or os.environ.get("OURCAL_REEXEC") == "1":
        return False
    return not _google_importable()


def ensure_deps():
    if not needs_bootstrap():
        return
    import subprocess
    import venv
    print("OurCal: first-run setup — creating .ourcal-venv and installing deps…")
    venv.EnvBuilder(with_pip=True).create(VENV_DIR)
    py = os.path.join(VENV_DIR, "bin", "python")
    subprocess.check_call([py, "-m", "pip", "install", "-q", "--upgrade", "pip"])
    subprocess.check_call([py, "-m", "pip", "install", "-q", *DEPS])
    env = dict(os.environ, OURCAL_REEXEC="1")
    os.execve(py, [py, os.path.abspath(__file__)], env)


def main():
    if not is_demo():
        ensure_deps()
    run_server()


if __name__ == "__main__":
    main()
