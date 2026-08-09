import copy, datetime, json, os, re, time, unittest
from zoneinfo import ZoneInfo
os.environ.setdefault("OURCAL_DEMO", "1")  # keep imports side-effect free / no google
import ourcal


class TestSlug(unittest.TestCase):
    def test_lowercases_and_hyphenates_spaces(self):
        self.assertEqual(ourcal.slug("Side Project 2"), "side-project-2")

    def test_strips_unsafe_chars(self):
        self.assertEqual(ourcal.slug("Work K!"), "work-k")

    def test_collapses_repeats_and_trims(self):
        self.assertEqual(ourcal.slug("  Side   AI  "), "side-ai")


class TestConfig(unittest.TestCase):
    def test_accounts_are_well_formed(self):
        # Contents are personal and live in a git-ignored accounts.json, so
        # assert shape rather than specific addresses.
        self.assertTrue(ourcal.ACCOUNTS)
        for a in ourcal.ACCOUNTS:
            self.assertTrue(a["label"].strip(), a)
            self.assertTrue(ourcal.valid_email(a["email"]), a)

    def test_account_labels_are_unique(self):
        # Labels key the token files; a duplicate would silently share one.
        labels = [a["label"] for a in ourcal.ACCOUNTS]
        self.assertEqual(len(labels), len(set(labels)))

    def test_token_paths_are_unique_per_account(self):
        slugs = [ourcal.slug(a["label"]) for a in ourcal.ACCOUNTS]
        self.assertEqual(len(slugs), len(set(slugs)))
        self.assertTrue(all(slugs))

    def test_palettes_cover_all_accounts(self):
        self.assertGreaterEqual(len(ourcal.PALETTE_LIGHT), len(ourcal.ACCOUNTS))
        self.assertEqual(len(ourcal.PALETTE_LIGHT), len(ourcal.PALETTE_DARK))

    def test_core_constants(self):
        self.assertEqual(ourcal.PORT, 8756)
        self.assertEqual(ourcal.TIMEZONE, "America/Los_Angeles")
        self.assertEqual(ourcal.DAYS_AHEAD, 30)
        self.assertEqual(ourcal.POLL_MINUTES, 5)


class TestAccountsFile(unittest.TestCase):
    """Real addresses live in a git-ignored accounts.json; the checked-in
    defaults are placeholders so a published copy carries nobody's data."""

    def test_parses_a_valid_file(self):
        got = ourcal.parse_accounts([{"label": "Home", "email": "a@b.com"},
                                     {"label": "Work", "email": "c@d.org"}])
        self.assertEqual(got, [{"label": "Home", "email": "a@b.com"},
                               {"label": "Work", "email": "c@d.org"}])

    def test_trims_whitespace(self):
        got = ourcal.parse_accounts([{"label": " Home ", "email": " a@b.com "}])
        self.assertEqual(got, [{"label": "Home", "email": "a@b.com"}])

    def test_rejects_empty_list(self):
        self.assertIsNone(ourcal.parse_accounts([]))

    def test_rejects_non_list(self):
        for bad in [{}, "nope", None, 3]:
            self.assertIsNone(ourcal.parse_accounts(bad), repr(bad))

    def test_rejects_entry_missing_label(self):
        self.assertIsNone(ourcal.parse_accounts([{"email": "a@b.com"}]))

    def test_rejects_entry_with_bad_email(self):
        self.assertIsNone(
            ourcal.parse_accounts([{"label": "Home", "email": "not-an-email"}]))

    def test_rejects_duplicate_labels(self):
        # Two accounts sharing a label would share one token file.
        self.assertIsNone(ourcal.parse_accounts(
            [{"label": "Home", "email": "a@b.com"},
             {"label": "Home", "email": "c@d.org"}]))

    def test_load_returns_none_for_missing_file(self):
        self.assertIsNone(ourcal.load_accounts("/nonexistent/accounts.json"))

    def test_load_returns_none_for_malformed_json(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json",
                                         delete=False) as f:
            f.write("{not json")
            path = f.name
        self.addCleanup(os.unlink, path)
        self.assertIsNone(ourcal.load_accounts(path))   # falls back, no crash

    def test_load_reads_a_good_file(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json",
                                         delete=False) as f:
            json.dump([{"label": "Home", "email": "a@b.com"}], f)
            path = f.name
        self.addCleanup(os.unlink, path)
        self.assertEqual(ourcal.load_accounts(path),
                         [{"label": "Home", "email": "a@b.com"}])


class TestNormalize(unittest.TestCase):
    def _timed(self):
        return {
            "iCalUID": "abc@google.com", "summary": "Standup",
            "start": {"dateTime": "2026-07-23T09:00:00-07:00"},
            "end":   {"dateTime": "2026-07-23T09:30:00-07:00"},
            "location": "Zoom", "hangoutLink": "https://meet.google.com/xyz",
            "attendees": [{"email": "a@x.com"}, {"email": "b@x.com"}],
        }

    def test_timed_event_fields(self):
        n = ourcal.normalize(self._timed(), "Personal", "Work")
        self.assertEqual(n["uid"], "abc@google.com")
        self.assertEqual(n["title"], "Standup")
        self.assertFalse(n["allDay"])
        self.assertTrue(n["busy"])              # no transparency → opaque
        self.assertEqual(n["join"], "https://meet.google.com/xyz")
        self.assertEqual(n["location"], "Zoom")
        self.assertEqual(n["labels"], ["Personal"])
        self.assertEqual(n["calendars"], ["Work"])
        self.assertEqual(n["guests"], 2)
        self.assertEqual(n["start"], "2026-07-23T09:00:00-07:00")

    def test_all_day_event(self):
        raw = {"iCalUID": "d@g", "summary": "Trip",
               "start": {"date": "2026-08-01"}, "end": {"date": "2026-08-03"}}
        n = ourcal.normalize(raw, "Second", "Personal")
        self.assertTrue(n["allDay"])
        self.assertEqual(n["start"], "2026-08-01")

    def test_free_event_is_not_busy(self):
        raw = {"iCalUID": "f@g", "summary": "Focus",
               "transparency": "transparent",
               "start": {"dateTime": "2026-07-23T14:00:00-07:00"},
               "end":   {"dateTime": "2026-07-23T15:00:00-07:00"}}
        self.assertFalse(ourcal.normalize(raw, "Personal", "Cal")["busy"])

    def test_empty_title_defaults_to_busy(self):
        raw = {"iCalUID": "e@g",
               "start": {"dateTime": "2026-07-23T14:00:00-07:00"},
               "end":   {"dateTime": "2026-07-23T15:00:00-07:00"}}
        self.assertEqual(ourcal.normalize(raw, "L", "C")["title"], "Busy")

    def test_missing_optional_fields(self):
        raw = {"iCalUID": "m@g", "summary": "x",
               "start": {"dateTime": "2026-07-23T14:00:00-07:00"},
               "end":   {"dateTime": "2026-07-23T15:00:00-07:00"}}
        n = ourcal.normalize(raw, "L", "C")
        self.assertIsNone(n["location"])
        self.assertIsNone(n["join"])
        self.assertEqual(n["guests"], 0)


class TestCanEdit(unittest.TestCase):
    """Two independent gates gate a patch: write access to the calendar, and
    standing on the event itself."""

    def test_owner_of_own_event_may_edit(self):
        raw = {"organizer": {"self": True}}
        self.assertTrue(ourcal.can_edit(raw, "owner"))

    def test_writer_of_own_event_may_edit(self):
        raw = {"organizer": {"self": True}}
        self.assertTrue(ourcal.can_edit(raw, "writer"))

    def test_reader_may_never_edit(self):
        # A subscribed/read-only calendar rejects writes whoever organized it.
        raw = {"organizer": {"self": True}}
        self.assertFalse(ourcal.can_edit(raw, "reader"))

    def test_guest_of_someone_elses_event_may_not_edit(self):
        raw = {"organizer": {"email": "host@example.com"}}
        self.assertFalse(ourcal.can_edit(raw, "owner"))

    def test_guest_may_edit_when_organizer_allowed_it(self):
        raw = {"organizer": {"email": "host@example.com"},
               "guestsCanModify": True}
        self.assertTrue(ourcal.can_edit(raw, "owner"))

    def test_missing_organizer_block_falls_back_to_calendar_access(self):
        self.assertTrue(ourcal.can_edit({}, "owner"))
        self.assertFalse(ourcal.can_edit({}, "reader"))

    def test_missing_access_role_is_treated_as_unwritable(self):
        self.assertFalse(ourcal.can_edit({"organizer": {"self": True}}, None))


class TestNormalizeEditFields(unittest.TestCase):
    def _raw(self, **kw):
        raw = {"iCalUID": "abc@google.com", "id": "evt123", "summary": "Standup",
               "start": {"dateTime": "2026-07-23T09:00:00-07:00"},
               "end":   {"dateTime": "2026-07-23T09:30:00-07:00"},
               "organizer": {"self": True}}
        raw.update(kw)
        return raw

    def test_captures_description_as_notes(self):
        n = ourcal.normalize(self._raw(description="Bring x-rays"), "L", "C")
        self.assertEqual(n["notes"], "Bring x-rays")

    def test_notes_is_none_when_absent(self):
        self.assertIsNone(ourcal.normalize(self._raw(), "L", "C")["notes"])

    def test_source_carries_editable_true_for_own_event(self):
        n = ourcal.normalize(self._raw(), "L", "C", "c@x", access_role="owner")
        self.assertTrue(n["sources"][0]["editable"])

    def test_source_carries_editable_false_for_readonly_calendar(self):
        n = ourcal.normalize(self._raw(), "L", "C", "c@x", access_role="reader")
        self.assertFalse(n["sources"][0]["editable"])

    def test_access_role_defaults_to_owner(self):
        # Demo fixtures and older call sites pass no role; they are our own.
        n = ourcal.normalize(self._raw(), "L", "C")
        self.assertTrue(n["sources"][0]["editable"])


class TestSources(unittest.TestCase):
    """A unified row must be able to address the real events behind it."""

    def _raw(self, **kw):
        raw = {"iCalUID": "abc@google.com", "id": "evt123", "summary": "Standup",
               "start": {"dateTime": "2026-07-23T09:00:00-07:00"},
               "end":   {"dateTime": "2026-07-23T09:30:00-07:00"}}
        raw.update(kw)
        return raw

    def test_normalize_emits_one_source_with_ids(self):
        n = ourcal.normalize(self._raw(), "Personal", "Primary",
                             "personal@example.com")
        self.assertEqual(n["sources"], [{
            "label": "Personal", "calendarId": "personal@example.com",
            "eventId": "evt123", "seriesId": None, "calendarName": "Primary",
            "editable": True}])

    def test_normalize_carries_series_id_for_recurring(self):
        n = ourcal.normalize(self._raw(id="evt123_20260724T160000Z",
                                       recurringEventId="evt123"),
                             "Personal", "Primary", "personal@example.com")
        self.assertEqual(n["sources"][0]["seriesId"], "evt123")
        self.assertEqual(n["sources"][0]["eventId"], "evt123_20260724T160000Z")

    def test_series_id_none_when_not_recurring(self):
        n = ourcal.normalize(self._raw(), "L", "C", "c@x")
        self.assertIsNone(n["sources"][0]["seriesId"])

    def test_calendar_id_defaults_to_empty(self):
        n = ourcal.normalize(self._raw(), "L", "C")
        self.assertEqual(n["sources"][0]["calendarId"], "")

    def test_merge_concatenates_sources_across_accounts(self):
        a = ourcal.normalize(self._raw(), "Personal", "Primary", "a@x")
        b = ourcal.normalize(self._raw(id="other9"), "Second", "Work", "b@x")
        out = ourcal.merge_events([a, b])
        self.assertEqual(len(out), 1)
        self.assertEqual([s["label"] for s in out[0]["sources"]],
                         ["Personal", "Second"])
        self.assertEqual([s["eventId"] for s in out[0]["sources"]],
                         ["evt123", "other9"])

    def test_merge_does_not_mutate_source_lists(self):
        a = ourcal.normalize(self._raw(), "Personal", "Primary", "a@x")
        b = ourcal.normalize(self._raw(id="other9"), "Second", "Work", "b@x")
        ourcal.merge_events([a, b])
        self.assertEqual(len(a["sources"]), 1)
        self.assertEqual(len(b["sources"]), 1)

    def test_unmerged_event_keeps_its_single_source(self):
        a = ourcal.normalize(self._raw(), "Personal", "Primary", "a@x")
        out = ourcal.merge_events([a])
        self.assertEqual(len(out[0]["sources"]), 1)


class TestMerge(unittest.TestCase):
    def _n(self, uid, start, label, busy=True, guests=0, join=None, cal="C"):
        # Mirrors normalize()'s output, which always emits `notes`.
        return {"uid": uid, "title": "M", "start": start, "end": start,
                "allDay": False, "busy": busy, "location": None, "join": join,
                "notes": None, "labels": [label], "calendars": [cal],
                "guests": guests}

    def test_same_uid_start_merges_labels(self):
        a = self._n("u1", "2026-07-23T09:00:00-07:00", "Personal", busy=False)
        b = self._n("u1", "2026-07-23T09:00:00-07:00", "Work", busy=True,
                    guests=3, join="https://meet/x")
        out = ourcal.merge_events([a, b])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["labels"], ["Personal", "Work"])
        self.assertTrue(out[0]["busy"])                 # any() busy
        self.assertEqual(out[0]["guests"], 3)           # max
        self.assertEqual(out[0]["join"], "https://meet/x")  # first non-empty

    def test_missing_uid_does_not_block_merging(self):
        # Identity is the appointment (title + span), not the uid — so copies
        # still collapse when Google gives us nothing to key on.
        a = self._n("", "2026-07-23T09:00:00-07:00", "Personal")
        b = self._n("", "2026-07-23T09:00:00-07:00", "Second")
        out = ourcal.merge_events([a, b])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["labels"], ["Personal", "Second"])

    def test_does_not_mutate_inputs(self):
        a = self._n("u1", "2026-07-23T09:00:00-07:00", "Personal")
        b = self._n("u1", "2026-07-23T09:00:00-07:00", "Work")
        a_snap, b_snap = copy.deepcopy(a), copy.deepcopy(b)
        ourcal.merge_events([a, b])
        self.assertEqual(a, a_snap)
        self.assertEqual(b, b_snap)

    def test_sorted_by_start(self):
        a = self._n("u2", "2026-07-24T09:00:00-07:00", "Personal")
        b = self._n("u1", "2026-07-23T09:00:00-07:00", "Personal")
        out = ourcal.merge_events([a, b])
        self.assertEqual([e["uid"] for e in out], ["u1", "u2"])

    def test_sorted_by_instant_across_offsets(self):
        # Eastern 10:00-04:00 == 14:00 UTC; Pacific 09:00-07:00 == 16:00 UTC.
        # By real instant, the Eastern event is earlier — a lexicographic string
        # sort would wrongly place the Pacific "09:00" first.
        east = self._n("uE", "2026-07-23T10:00:00-04:00", "Personal")
        pac = self._n("uP", "2026-07-23T09:00:00-07:00", "Second")
        out = ourcal.merge_events([pac, east])
        self.assertEqual([e["uid"] for e in out], ["uE", "uP"])


class TestMergeSeparateCopies(unittest.TestCase):
    """The same appointment typed into four accounts is four Google events with
    four different iCalUIDs. It is still one appointment, and must render as one
    row carrying every calendar's badge."""

    def _n(self, uid, label, title="Dentist appointment",
           start="2026-07-24T11:00:00-07:00", end="2026-07-24T12:00:00-07:00",
           all_day=False, notes=None, location=None):
        return {"uid": uid, "title": title, "start": start, "end": end,
                "allDay": all_day, "busy": True, "location": location,
                "join": None, "notes": notes,
                "labels": [label], "calendars": [label + " cal"], "guests": 0,
                "sources": [{"label": label, "calendarId": label + "@x",
                             "eventId": uid, "seriesId": None,
                             "calendarName": label + " cal"}]}

    def test_distinct_uids_same_slot_collapse_to_one_row(self):
        out = ourcal.merge_events([self._n("u1", "Personal"), self._n("u2", "Work"),
                                   self._n("u3", "Side"), self._n("u4", "Family")])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["labels"],
                         ["Personal", "Work", "Side", "Family"])

    def test_every_copy_stays_addressable_for_delete(self):
        out = ourcal.merge_events([self._n("u1", "Personal"), self._n("u2", "Work")])
        self.assertEqual([s["eventId"] for s in out[0]["sources"]], ["u1", "u2"])

    def test_same_instant_in_different_offsets_merges(self):
        # One account reports Pacific wall time, another the same moment in UTC.
        # A raw string compare would call these two different appointments.
        pacific = self._n("u1", "Personal")
        utc = self._n("u2", "Side", start="2026-07-24T18:00:00Z",
                      end="2026-07-24T19:00:00Z")
        self.assertEqual(len(ourcal.merge_events([pacific, utc])), 1)

    def test_titles_compare_case_and_space_insensitively(self):
        a = self._n("u1", "Personal", title="Dentist Appointment")
        b = self._n("u2", "Work", title="dentist  appointment ")
        self.assertEqual(len(ourcal.merge_events([a, b])), 1)

    def test_different_titles_in_the_same_slot_stay_apart(self):
        a = self._n("u1", "Personal")
        b = self._n("u2", "Work", title="Physio")
        self.assertEqual(len(ourcal.merge_events([a, b])), 2)

    def test_different_durations_stay_apart(self):
        a = self._n("u1", "Personal")
        b = self._n("u2", "Work", end="2026-07-24T11:30:00-07:00")
        self.assertEqual(len(ourcal.merge_events([a, b])), 2)

    def test_all_day_never_merges_with_a_midnight_event(self):
        allday = self._n("u1", "Personal", start="2026-07-24", end="2026-07-25",
                         all_day=True)
        timed = self._n("u2", "Work", start="2026-07-24T00:00:00-07:00",
                        end="2026-07-25T00:00:00-07:00")
        self.assertEqual(len(ourcal.merge_events([allday, timed])), 2)

    def test_all_day_copies_across_accounts_merge(self):
        a = self._n("u1", "Personal", start="2026-07-24", end="2026-07-25",
                    all_day=True)
        b = self._n("u2", "Work", start="2026-07-24", end="2026-07-25",
                    all_day=True)
        self.assertEqual(len(ourcal.merge_events([a, b])), 1)

    def test_recurring_instances_remain_separate_rows(self):
        mon = self._n("u1", "Personal")
        tue = self._n("u1", "Personal", start="2026-07-25T11:00:00-07:00",
                      end="2026-07-25T12:00:00-07:00")
        self.assertEqual(len(ourcal.merge_events([mon, tue])), 2)

    def test_does_not_mutate_inputs(self):
        a, b = self._n("u1", "Personal"), self._n("u2", "Work")
        a_snap, b_snap = copy.deepcopy(a), copy.deepcopy(b)
        ourcal.merge_events([a, b])
        self.assertEqual(a, a_snap)
        self.assertEqual(b, b_snap)

    def test_notes_from_a_later_copy_survive_the_merge(self):
        # The Edit modal prefills from the merged row. A row that dropped the
        # only copy carrying a description would prefill Notes empty, and
        # saving would push that emptiness back over the real description.
        bare = self._n("u1", "Personal")
        detailed = self._n("u2", "Work", notes="Dial 1234 to join")
        out = ourcal.merge_events([bare, detailed])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["notes"], "Dial 1234 to join")

    def test_first_non_empty_notes_wins(self):
        a = self._n("u1", "Personal", notes="Bring x-rays")
        b = self._n("u2", "Work", notes="Different text")
        self.assertEqual(ourcal.merge_events([a, b])[0]["notes"], "Bring x-rays")

    def test_location_from_a_later_copy_survives_the_merge(self):
        bare = self._n("u1", "Personal")
        placed = self._n("u2", "Work", location="Suite 207")
        self.assertEqual(ourcal.merge_events([bare, placed])[0]["location"],
                         "Suite 207")

    def test_merging_notes_does_not_mutate_inputs(self):
        a = self._n("u1", "Personal")
        b = self._n("u2", "Work", notes="n")
        a_snap, b_snap = copy.deepcopy(a), copy.deepcopy(b)
        ourcal.merge_events([a, b])
        self.assertEqual(a, a_snap)
        self.assertEqual(b, b_snap)


class TestBuildBody(unittest.TestCase):
    def test_timed_body(self):
        p = {"title": "Sync", "date": "2026-07-23", "startTime": "09:00",
             "endTime": "09:30", "allDay": False, "notes": "n", "location": "L"}
        b = ourcal.build_event_body(p, blocking=True)
        self.assertEqual(b["summary"], "Sync")
        self.assertEqual(b["transparency"], "opaque")
        self.assertEqual(b["start"], {"dateTime": "2026-07-23T09:00:00",
                                      "timeZone": "America/Los_Angeles"})
        self.assertEqual(b["end"], {"dateTime": "2026-07-23T09:30:00",
                                    "timeZone": "America/Los_Angeles"})
        self.assertEqual(b["location"], "L")
        self.assertEqual(b["description"], "n")

    def test_all_day_exclusive_end(self):
        p = {"title": "PTO", "date": "2026-08-01", "allDay": True,
             "startTime": "", "endTime": "", "notes": "", "location": ""}
        b = ourcal.build_event_body(p, blocking=True)
        self.assertEqual(b["start"], {"date": "2026-08-01"})
        self.assertEqual(b["end"], {"date": "2026-08-02"})  # +1 exclusive

    def test_empty_title_defaults_busy_and_free(self):
        p = {"title": "  ", "date": "2026-07-23", "startTime": "10:00",
             "endTime": "10:15", "allDay": False, "notes": "", "location": ""}
        b = ourcal.build_event_body(p, blocking=False)
        self.assertEqual(b["summary"], "Busy")
        self.assertEqual(b["transparency"], "transparent")
        self.assertNotIn("location", b)
        self.assertNotIn("description", b)


class TestDetailLevel(unittest.TestCase):
    """detail="busy" hides what the event is; blocking controls whether it
    occupies time. Separate axes -- both combinations must be expressible."""

    def _p(self, **kw):
        p = {"title": "Annual physical", "date": "2026-07-28",
             "startTime": "09:25", "endTime": "10:00", "allDay": False,
             "notes": "bring insurance card", "location": "5708 E Lk Sammamish"}
        p.update(kw)
        return p

    def test_full_detail_keeps_everything(self):
        b = ourcal.build_event_body(self._p(), blocking=True, detail="full")
        self.assertEqual(b["summary"], "Annual physical")
        self.assertEqual(b["location"], "5708 E Lk Sammamish")
        self.assertEqual(b["description"], "bring insurance card")

    def test_busy_detail_strips_title_location_notes(self):
        b = ourcal.build_event_body(self._p(), blocking=True, detail="busy")
        self.assertEqual(b["summary"], "Busy")
        self.assertNotIn("location", b)
        self.assertNotIn("description", b)

    def test_busy_detail_preserves_the_time_slot(self):
        b = ourcal.build_event_body(self._p(), blocking=True, detail="busy")
        self.assertEqual(b["start"], {"dateTime": "2026-07-28T09:25:00",
                                      "timeZone": "America/Los_Angeles"})
        self.assertEqual(b["end"], {"dateTime": "2026-07-28T10:00:00",
                                    "timeZone": "America/Los_Angeles"})

    def test_detail_is_independent_of_blocking(self):
        # visible but free
        vf = ourcal.build_event_body(self._p(), blocking=False, detail="full")
        self.assertEqual(vf["summary"], "Annual physical")
        self.assertEqual(vf["transparency"], "transparent")
        # opaque but anonymous
        oa = ourcal.build_event_body(self._p(), blocking=True, detail="busy")
        self.assertEqual(oa["summary"], "Busy")
        self.assertEqual(oa["transparency"], "opaque")

    def test_defaults_to_full_detail(self):
        b = ourcal.build_event_body(self._p(), blocking=True)
        self.assertEqual(b["summary"], "Annual physical")

    def test_busy_all_day_keeps_exclusive_end(self):
        b = ourcal.build_event_body(self._p(allDay=True), blocking=True,
                                    detail="busy")
        self.assertEqual(b["start"], {"date": "2026-07-28"})
        self.assertEqual(b["end"], {"date": "2026-07-29"})
        self.assertEqual(b["summary"], "Busy")


def _FIXED_NOW():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime(2026, 7, 23, 8, 0, tzinfo=ZoneInfo("America/Los_Angeles"))


class TestDemoService(unittest.TestCase):
    def setUp(self):
        os.environ["OURCAL_DEMO"] = "1"
        ourcal.reset_demo()  # restore fixtures between tests

    def test_envelope_shape(self):
        env = ourcal.get_events()
        self.assertEqual(env["timezone"], "America/Los_Angeles")
        self.assertIn("updated", env)
        self.assertTrue(all("color" in a for a in env["accounts"]))
        self.assertTrue(len(env["events"]) >= 5)

    def test_has_meet_allday_free_and_merged(self):
        evs = ourcal.get_events()["events"]
        self.assertTrue(any(e["join"] for e in evs))            # ADPList Meet
        self.assertTrue(any(e["allDay"] for e in evs))          # all-day
        self.assertTrue(any(not e["busy"] for e in evs))        # free
        self.assertTrue(any(len(e["labels"]) >= 2 for e in evs))  # merged badges

    def test_separately_entered_copies_show_as_one_row(self):
        rows = [e for e in ourcal.get_events()["events"]
                if e["title"] == "Dentist appointment"]
        self.assertEqual(len(rows), 1)                          # not one per account
        self.assertEqual(rows[0]["labels"], ["Personal", "Work"])
        self.assertEqual(len(rows[0]["sources"]), 2)            # both stay deletable

    def test_get_events_events_stable_across_calls(self):
        one = ourcal.get_events(now=_FIXED_NOW())["events"]
        two = ourcal.get_events(now=_FIXED_NOW())["events"]
        self.assertEqual(one, two)  # no mutation between reads

    def test_create_timed_appends_and_appears(self):
        before = len(ourcal.get_events()["events"])
        r = ourcal.create_event({
            "title": "New", "date": "2026-07-24", "startTime": "10:00",
            "endTime": "10:30", "allDay": False, "notes": "", "location": "",
            "mode": "copies", "targets": [{"label": "Personal", "blocking": True}],
        })
        self.assertTrue(r["ok"])
        self.assertEqual(len(ourcal.get_events()["events"]), before + 1)

    def test_create_allday_and_invite_ok(self):
        r1 = ourcal.create_event({
            "title": "PTO", "date": "2026-08-01", "allDay": True,
            "startTime": "", "endTime": "", "notes": "", "location": "",
            "mode": "copies", "targets": [{"label": "Personal", "blocking": True}]})
        r2 = ourcal.create_event({
            "title": "1:1", "date": "2026-07-25", "startTime": "11:00",
            "endTime": "11:30", "allDay": False, "notes": "", "location": "",
            "mode": "invite", "inviteFrom": "Personal",
            "targets": [{"label": "Personal", "blocking": True},
                        {"label": "Work", "blocking": True}]})
        self.assertTrue(r1["ok"] and r2["ok"])


class TestDemoUpdate(unittest.TestCase):
    def setUp(self):
        os.environ["OURCAL_DEMO"] = "1"
        ourcal.reset_demo()

    def _find(self, title):
        return [e for e in ourcal.get_events()["events"] if e["title"] == title]

    def _sources_of(self, title):
        return self._find(title)[0]["sources"]

    def test_moving_an_event_changes_its_slot(self):
        srcs = self._sources_of("Team Standup")
        r = ourcal.update_events({
            "title": "Team Standup", "date": "2026-09-09",
            "startTime": "15:00", "endTime": "15:30", "allDay": False,
            "location": "", "notes": "", "scope": "occurrence",
            "sources": srcs})
        self.assertTrue(r["ok"])
        moved = self._find("Team Standup")[0]
        self.assertTrue(moved["start"].startswith("2026-09-09T15:00"))

    def test_retitling_is_visible_in_the_agenda(self):
        srcs = self._sources_of("Team Standup")
        ourcal.update_events({
            "title": "Renamed", "date": "2026-09-09", "startTime": "15:00",
            "endTime": "15:30", "allDay": False, "location": "", "notes": "",
            "scope": "occurrence", "sources": srcs})
        self.assertEqual(len(self._find("Renamed")), 1)
        self.assertEqual(self._find("Team Standup"), [])

    def test_updating_every_source_keeps_a_merged_row_merged(self):
        srcs = self._sources_of("Dentist appointment")
        self.assertEqual(len(srcs), 2)
        ourcal.update_events({
            "title": "Dentist appointment", "date": "2026-09-10",
            "startTime": "09:00", "endTime": "10:00", "allDay": False,
            "location": "", "notes": "", "scope": "occurrence",
            "sources": srcs})
        rows = self._find("Dentist appointment")
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]["sources"]), 2)

    def test_updating_one_source_splits_the_row(self):
        # Moving only one copy gives it a different slot, so it stops sharing
        # an identity with the copy left behind. Two rows is correct.
        srcs = self._sources_of("Dentist appointment")
        ourcal.update_events({
            "title": "Dentist appointment", "date": "2026-09-11",
            "startTime": "09:00", "endTime": "10:00", "allDay": False,
            "location": "", "notes": "", "scope": "occurrence",
            "sources": [srcs[0]]})
        self.assertEqual(len(self._find("Dentist appointment")), 2)

    def test_invalid_payload_is_rejected_before_anything_changes(self):
        srcs = self._sources_of("Team Standup")
        before = ourcal.get_events()["events"]
        r = ourcal.update_events({
            "title": "T", "date": "", "startTime": "", "endTime": "",
            "allDay": False, "location": "", "notes": "",
            "scope": "occurrence", "sources": srcs})
        self.assertFalse(r["ok"])
        self.assertTrue(r["error"])
        self.assertEqual(ourcal.get_events()["events"], before)

    def test_moving_an_all_day_event_keeps_it_all_day(self):
        # _demo_update branches on all-day, and the patch body for it carries
        # explicit nulls to clear any stored dateTime — the least-exercised path.
        srcs = self._sources_of("Offsite (all day)")
        r = ourcal.update_events({
            "title": "Offsite (all day)", "date": "2026-09-20",
            "startTime": "", "endTime": "", "allDay": True,
            "location": "", "notes": "", "scope": "occurrence",
            "sources": srcs})
        self.assertTrue(r["ok"])
        moved = self._find("Offsite (all day)")[0]
        self.assertTrue(moved["allDay"])
        self.assertEqual(moved["start"], "2026-09-20")
        self.assertEqual(moved["end"], "2026-09-21")   # end date is exclusive

    def test_a_timed_event_can_become_all_day(self):
        srcs = self._sources_of("Team Standup")
        ourcal.update_events({
            "title": "Team Standup", "date": "2026-09-20", "startTime": "09:00",
            "endTime": "09:30", "allDay": True, "location": "", "notes": "",
            "scope": "occurrence", "sources": srcs})
        moved = self._find("Team Standup")[0]
        self.assertTrue(moved["allDay"])
        self.assertEqual(moved["start"], "2026-09-20")

    def test_untouched_fields_keep_their_stored_value(self):
        # A merged row prefills from its first copy, so an untouched Notes box
        # may hold nothing while the real event holds a description. Only the
        # keys named in `changed` may land.
        srcs = self._sources_of("Team Standup")
        before = self._find("Team Standup")[0]
        ourcal.update_events({
            "title": "Renamed standup", "date": "2026-09-09",
            "startTime": "15:00", "endTime": "15:30", "allDay": False,
            "location": "", "notes": "", "scope": "occurrence",
            "changed": ["title"], "sources": srcs})
        moved = self._find("Renamed standup")[0]
        self.assertEqual(moved["start"], before["start"])
        self.assertEqual(moved["end"], before["end"])
        self.assertEqual(moved["location"], before["location"])
        self.assertEqual(moved["notes"], before["notes"])

    def test_a_time_only_change_keeps_the_title(self):
        srcs = self._sources_of("Team Standup")
        ourcal.update_events({
            "title": "Team Standup", "date": "2026-09-09", "startTime": "15:00",
            "endTime": "15:30", "allDay": False, "location": "", "notes": "",
            "scope": "occurrence", "changed": ["startTime", "endTime", "date"],
            "sources": srcs})
        moved = self._find("Team Standup")[0]
        self.assertTrue(moved["start"].startswith("2026-09-09T15:00"))

    def test_clearing_a_location_still_clears_it(self):
        srcs = self._sources_of("Team Standup")
        self.assertEqual(self._find("Team Standup")[0]["location"], "Zoom")
        ourcal.update_events({
            "title": "Team Standup", "date": "2026-09-09", "startTime": "15:00",
            "endTime": "15:30", "allDay": False, "location": "", "notes": "",
            "scope": "occurrence", "changed": ["location"], "sources": srcs})
        self.assertIsNone(self._find("Team Standup")[0]["location"])


class TestCalendarFilter(unittest.TestCase):
    def test_includes_selected(self):
        self.assertTrue(ourcal.should_include_calendar(
            {"id": "abc@group.calendar.google.com", "selected": True}))

    def test_includes_primary_even_if_not_selected(self):
        self.assertTrue(ourcal.should_include_calendar(
            {"id": "me@gmail.com", "primary": True}))

    def test_excludes_unselected_non_primary(self):
        self.assertFalse(ourcal.should_include_calendar(
            {"id": "x@group.calendar.google.com"}))

    def test_skips_holiday_contacts_addressbook(self):
        for cid in ["en.usa#holiday@group.v.calendar.google.com",
                    "#contacts@group.v.calendar.google.com",
                    "addressbook#contacts@google.com"]:
            self.assertFalse(ourcal.should_include_calendar(
                {"id": cid, "selected": True, "primary": True}))


class TestTargetId(unittest.TestCase):
    """Which Google id an edit or delete addresses depends on the chosen scope."""

    def _src(self, **kw):
        s = {"label": "Personal", "calendarId": "personal@example.com",
             "eventId": "evt_20260724T160000Z", "seriesId": "evt"}
        s.update(kw)
        return s

    def test_occurrence_scope_targets_the_instance(self):
        self.assertEqual(
            ourcal.target_id(self._src(), "occurrence"),
            "evt_20260724T160000Z")

    def test_series_scope_targets_the_series(self):
        self.assertEqual(ourcal.target_id(self._src(), "series"), "evt")

    def test_series_scope_falls_back_when_not_recurring(self):
        # A one-off event has no series; "delete series" must not blow up.
        self.assertEqual(
            ourcal.target_id(self._src(seriesId=None), "series"),
            "evt_20260724T160000Z")

    def test_unknown_scope_defaults_to_occurrence(self):
        self.assertEqual(ourcal.target_id(self._src(), "nonsense"),
                         "evt_20260724T160000Z")


class TestBuildPatchBody(unittest.TestCase):
    """An edit changes what the user typed and nothing else."""

    def _p(self, **kw):
        p = {"title": "Dentist", "date": "2026-07-24", "startTime": "11:00",
             "endTime": "12:00", "allDay": False, "location": "", "notes": ""}
        p.update(kw)
        return p

    def test_sets_summary_and_times(self):
        b = ourcal.build_patch_body(self._p())
        self.assertEqual(b["summary"], "Dentist")
        self.assertEqual(b["start"], {"dateTime": "2026-07-24T11:00:00",
                                      "timeZone": ourcal.TIMEZONE,
                                      "date": None})
        self.assertEqual(b["end"], {"dateTime": "2026-07-24T12:00:00",
                                    "timeZone": ourcal.TIMEZONE,
                                    "date": None})

    def test_all_day_uses_exclusive_end_date(self):
        b = ourcal.build_patch_body(self._p(allDay=True))
        self.assertEqual(b["start"], {"date": "2026-07-24",
                                      "dateTime": None, "timeZone": None})
        self.assertEqual(b["end"], {"date": "2026-07-25",
                                    "dateTime": None, "timeZone": None})

    def test_timed_edit_clears_a_stored_all_day_date(self):
        # events.patch MERGES nested objects rather than replacing them, so
        # patching {"dateTime": ...} onto a stored {"date": ...} would leave an
        # object carrying both — which the API rejects. null is the delete signal.
        b = ourcal.build_patch_body(self._p())
        self.assertIn("date", b["start"])
        self.assertIsNone(b["start"]["date"])
        self.assertIsNone(b["end"]["date"])

    def test_all_day_edit_clears_the_stored_date_time(self):
        b = ourcal.build_patch_body(self._p(allDay=True))
        self.assertIsNone(b["start"]["dateTime"])
        self.assertIsNone(b["start"]["timeZone"])
        self.assertIsNone(b["end"]["dateTime"])
        self.assertIsNone(b["end"]["timeZone"])

    def test_clearing_nulls_serialize_as_json_null(self):
        # Google reads an explicit JSON null as "delete this field"; an absent
        # key means "leave it alone". The distinction only survives if the
        # None actually reaches the wire.
        b = ourcal.build_patch_body(self._p(allDay=True))
        self.assertIn('"dateTime": null', json.dumps(b))

    def test_never_touches_busy_free_or_privacy(self):
        # The user edited a title; silently flipping the event to "busy"
        # because the create form defaults that way would be a data change
        # they never asked for.
        b = ourcal.build_patch_body(self._p())
        self.assertNotIn("transparency", b)
        self.assertNotIn("visibility", b)

    def test_location_and_notes_round_trip(self):
        b = ourcal.build_patch_body(self._p(location="Suite 207",
                                            notes="Bring x-rays"))
        self.assertEqual(b["location"], "Suite 207")
        self.assertEqual(b["description"], "Bring x-rays")

    def test_cleared_location_and_notes_are_sent_as_empty_not_dropped(self):
        # patch() ignores absent keys, so omitting these would make clearing a
        # location impossible — the old value would survive the edit.
        b = ourcal.build_patch_body(self._p(location="", notes=""))
        self.assertEqual(b["location"], "")
        self.assertEqual(b["description"], "")

    def test_does_not_mutate_the_payload(self):
        p = self._p()
        snap = copy.deepcopy(p)
        ourcal.build_patch_body(p)
        self.assertEqual(p, snap)


class TestBuildPatchBodyChangedFields(unittest.TestCase):
    """Send only what the user actually touched.

    A merged row flattens its copies: the row shows the first copy's notes and
    location, so the modal prefills what the *other* copies do not contain.
    Sending every field to every copy would overwrite their real values with
    the row's flattened view. `changed` is the list of keys whose value the
    user moved off the prefill; everything else stays absent, and patch leaves
    absent keys alone.
    """

    def _p(self, **kw):
        p = {"title": "Dentist", "date": "2026-07-24", "startTime": "11:00",
             "endTime": "12:00", "allDay": False, "location": "", "notes": ""}
        p.update(kw)
        return p

    def test_no_changed_key_emits_everything(self):
        # Back-compat: any caller that does not report what changed still gets
        # the full body it always got.
        b = ourcal.build_patch_body(self._p())
        for k in ["summary", "start", "end", "location", "description"]:
            self.assertIn(k, b)

    def test_title_only_edit_sends_only_the_title(self):
        b = ourcal.build_patch_body(self._p(changed=["title"]))
        self.assertEqual(b["summary"], "Dentist")
        for k in ["start", "end", "location", "description"]:
            self.assertNotIn(k, b)

    def test_title_only_edit_leaves_a_recurrence_alone(self):
        # The date field is prefilled from the occurrence being edited, not the
        # series' first instance. With series scope the patch addresses the
        # recurring master, whose start IS its first instance — so an absolute
        # start would rebase the whole series onto today and erase every
        # occurrence before it.
        b = ourcal.build_patch_body(self._p(changed=["title"]))
        self.assertNotIn("start", b)

    def test_date_change_sends_both_start_and_end(self):
        b = ourcal.build_patch_body(self._p(changed=["date"]))
        self.assertIn("start", b)
        self.assertIn("end", b)
        self.assertNotIn("summary", b)

    def test_time_change_sends_start_and_end(self):
        for key in ["startTime", "endTime", "allDay"]:
            b = ourcal.build_patch_body(self._p(changed=[key]))
            self.assertIn("start", b, key)
            self.assertIn("end", b, key)

    def test_location_only_edit_sends_only_the_location(self):
        b = ourcal.build_patch_body(self._p(location="Suite 207",
                                            changed=["location"]))
        self.assertEqual(b["location"], "Suite 207")
        self.assertNotIn("description", b)
        self.assertNotIn("summary", b)

    def test_notes_only_edit_sends_only_the_description(self):
        b = ourcal.build_patch_body(self._p(notes="Bring x-rays",
                                            changed=["notes"]))
        self.assertEqual(b["description"], "Bring x-rays")
        self.assertNotIn("location", b)

    def test_untouched_notes_are_never_sent(self):
        # The whole point: a merged row prefills Notes empty because the copy
        # that carried them was not the first one. Saving a time change must
        # not push that emptiness onto the copy that has real notes.
        b = ourcal.build_patch_body(self._p(notes="", changed=["startTime"]))
        self.assertNotIn("description", b)

    def test_clearing_a_field_still_clears_it(self):
        # Changed-to-empty IS a change, so the key is sent as "".
        b = ourcal.build_patch_body(self._p(location="", notes="",
                                            changed=["location", "notes"]))
        self.assertEqual(b["location"], "")
        self.assertEqual(b["description"], "")

    def test_nothing_changed_sends_nothing(self):
        b = ourcal.build_patch_body(self._p(changed=[]))
        self.assertEqual(b, {})

    def test_start_and_end_still_carry_their_clearing_nulls(self):
        b = ourcal.build_patch_body(self._p(allDay=True, changed=["allDay"]))
        self.assertIsNone(b["start"]["dateTime"])
        self.assertIsNone(b["end"]["timeZone"])

    def test_still_never_touches_busy_free_or_privacy(self):
        b = ourcal.build_patch_body(self._p(changed=["title", "date"]))
        self.assertNotIn("transparency", b)
        self.assertNotIn("visibility", b)

    def test_does_not_mutate_the_payload(self):
        p = self._p(changed=["title"])
        snap = copy.deepcopy(p)
        ourcal.build_patch_body(p)
        self.assertEqual(p, snap)


class TestEditValidation(unittest.TestCase):
    def _p(self, **kw):
        p = {"date": "2026-07-24", "startTime": "11:00", "endTime": "12:00",
             "allDay": False, "sources": [{"label": "Personal"}]}
        p.update(kw)
        return p

    def test_good_payload_has_no_error(self):
        self.assertIsNone(ourcal.edit_error(self._p()))

    def test_missing_date_is_rejected(self):
        self.assertIn("date", ourcal.edit_error(self._p(date="")).lower())

    def test_end_before_start_is_rejected(self):
        self.assertIn("end", ourcal.edit_error(
            self._p(startTime="12:00", endTime="11:00")).lower())

    def test_zero_length_event_is_rejected(self):
        self.assertIsNotNone(ourcal.edit_error(
            self._p(startTime="11:00", endTime="11:00")))

    def test_all_day_ignores_times(self):
        # An all-day edit sends no usable times; validating them would reject
        # a perfectly good payload.
        self.assertIsNone(ourcal.edit_error(
            self._p(allDay=True, startTime="", endTime="")))

    def test_no_sources_is_rejected(self):
        self.assertIn("calendar", ourcal.edit_error(self._p(sources=[])).lower())


class _FakeHttpError(Exception):
    """Stands in for googleapiclient.errors.HttpError, which carries .resp."""

    def __init__(self, status):
        super().__init__(f"HTTP {status}")
        self.resp = type("R", (), {"status": status})()


class TestAlreadyGone(unittest.TestCase):
    def test_404_is_already_gone(self):
        self.assertTrue(ourcal.is_already_gone(_FakeHttpError(404)))

    def test_410_is_already_gone(self):
        self.assertTrue(ourcal.is_already_gone(_FakeHttpError(410)))

    def test_403_is_not_already_gone(self):
        self.assertFalse(ourcal.is_already_gone(_FakeHttpError(403)))

    def test_plain_exception_is_not_already_gone(self):
        self.assertFalse(ourcal.is_already_gone(ValueError("boom")))


class _FakeExec:
    def __init__(self, ret):
        self._ret = ret

    def execute(self):
        return self._ret


class _FakeEvents:
    def __init__(self, log, errors=None):
        self._log = log
        self._errors = errors or {}

    def _maybe_raise(self, key):
        exc = self._errors.get(key)
        if exc:
            raise exc

    def insert(self, calendarId, body, sendUpdates):
        self._log.append({"op": "insert", "calendarId": calendarId,
                          "body": body, "sendUpdates": sendUpdates})
        self._maybe_raise(calendarId)
        return _FakeExec({"htmlLink": "https://calendar.google.com/x"})

    def delete(self, calendarId, eventId, sendUpdates):
        self._log.append({"op": "delete", "calendarId": calendarId,
                          "eventId": eventId, "sendUpdates": sendUpdates})
        self._maybe_raise(eventId)
        return _FakeExec("")

    def patch(self, calendarId, eventId, body, sendUpdates):
        self._log.append({"op": "patch", "calendarId": calendarId,
                          "eventId": eventId, "body": body,
                          "sendUpdates": sendUpdates})
        self._maybe_raise(eventId)
        return _FakeExec({"id": eventId})


class _FakeService:
    def __init__(self, log, errors=None):
        self._log = log
        self._errors = errors

    def events(self):
        return _FakeEvents(self._log, self._errors)


FIXTURE_ACCOUNTS = [
    {"label": "Personal", "email": "personal@example.com"},
    {"label": "Second", "email": "second@example.com"},
]


def pin_accounts(test):
    """Pin ACCOUNTS for a test: the real list comes from a git-ignored
    accounts.json, so anything asserting on emails must supply its own."""
    real = ourcal.ACCOUNTS
    ourcal.ACCOUNTS = list(FIXTURE_ACCOUNTS)
    test.addCleanup(lambda: setattr(ourcal, "ACCOUNTS", real))


class TestGoogleCreateWiring(unittest.TestCase):
    """_google_create runs only against real Google, so demo-mode create tests
    never touch it. These stub service_for to exercise the wiring directly."""

    def setUp(self):
        pin_accounts(self)
        self.calls = []      # (label, email) service_for was asked for
        self.inserts = []    # what got inserted
        self._real = ourcal.service_for

        def fake_service_for(label, email):
            self.calls.append((label, email))
            return _FakeService(self.inserts)

        ourcal.service_for = fake_service_for
        self.addCleanup(lambda: setattr(ourcal, "service_for", self._real))

    def _payload(self, **kw):
        p = {"title": "Sync", "date": "2026-07-24", "startTime": "09:00",
             "endTime": "09:30", "allDay": False, "notes": "", "location": "",
             "mode": "copies",
             "targets": [{"label": "Personal", "blocking": True},
                         {"label": "Second", "blocking": True}]}
        p.update(kw)
        return p

    def test_copies_mode_passes_label_and_email_to_service_for(self):
        r = ourcal._google_create(self._payload())
        self.assertTrue(r["ok"])
        self.assertEqual(self.calls,
                         [("Personal", "personal@example.com"),
                          ("Second", "second@example.com")])

    def test_invite_mode_passes_label_and_email_to_service_for(self):
        r = ourcal._google_create(self._payload(mode="invite",
                                                inviteFrom="Personal"))
        self.assertTrue(r["ok"])
        self.assertEqual(self.calls, [("Personal", "personal@example.com")])

    def test_invite_mode_adds_other_targets_as_attendees(self):
        ourcal._google_create(self._payload(mode="invite", inviteFrom="Personal"))
        self.assertEqual(len(self.inserts), 1)
        self.assertEqual(self.inserts[0]["body"]["attendees"],
                         [{"email": "second@example.com"}])
        self.assertEqual(self.inserts[0]["sendUpdates"], "all")

    def test_copies_mode_does_not_notify(self):
        ourcal._google_create(self._payload())
        self.assertEqual([i["sendUpdates"] for i in self.inserts],
                         ["none", "none"])


class TestGoogleDeleteWiring(unittest.TestCase):
    def setUp(self):
        pin_accounts(self)
        self.calls = []
        self.ops = []
        self.errors = {}
        real = ourcal.service_for

        def fake_service_for(label, email):
            self.calls.append((label, email))
            return _FakeService(self.ops, self.errors)

        ourcal.service_for = fake_service_for
        self.addCleanup(lambda: setattr(ourcal, "service_for", real))

    def _sources(self):
        return [
            {"label": "Personal", "calendarId": "personal@example.com",
             "eventId": "e1", "seriesId": "s1"},
            {"label": "Second", "calendarId": "second@example.com",
             "eventId": "e2", "seriesId": None},
        ]

    def test_deletes_each_source_with_its_own_calendar_and_id(self):
        r = ourcal._google_delete({"scope": "occurrence",
                                   "sources": self._sources()})
        self.assertTrue(r["ok"])
        self.assertEqual(
            [(o["calendarId"], o["eventId"]) for o in self.ops],
            [("personal@example.com", "e1"), ("second@example.com", "e2")])

    def test_series_scope_deletes_series_id(self):
        ourcal._google_delete({"scope": "series", "sources": self._sources()})
        self.assertEqual([o["eventId"] for o in self.ops], ["s1", "e2"])

    def test_never_mails_guests_a_cancellation(self):
        ourcal._google_delete({"scope": "occurrence",
                               "sources": self._sources()})
        self.assertEqual([o["sendUpdates"] for o in self.ops],
                         ["none", "none"])

    def test_passes_label_and_email_to_service_for(self):
        ourcal._google_delete({"scope": "occurrence",
                               "sources": self._sources()})
        self.assertEqual(self.calls, [("Personal", "personal@example.com"),
                                      ("Second", "second@example.com")])

    def test_missing_event_counts_as_success(self):
        self.errors["e1"] = _FakeHttpError(404)
        r = ourcal._google_delete({"scope": "occurrence",
                                   "sources": self._sources()})
        self.assertTrue(r["ok"])
        self.assertTrue(all(x["ok"] for x in r["results"]))

    def test_one_failure_does_not_abort_the_others(self):
        self.errors["e1"] = _FakeHttpError(403)
        r = ourcal._google_delete({"scope": "occurrence",
                                   "sources": self._sources()})
        self.assertFalse(r["ok"])
        self.assertFalse(r["results"][0]["ok"])
        self.assertTrue(r["results"][1]["ok"])       # second still attempted
        self.assertEqual(len(self.ops), 2)


class TestGoogleUpdateWiring(unittest.TestCase):
    def setUp(self):
        pin_accounts(self)
        self.ops = []
        self.errors = {}
        real = ourcal.service_for
        ourcal.service_for = lambda label, email: _FakeService(self.ops,
                                                               self.errors)
        self.addCleanup(lambda: setattr(ourcal, "service_for", real))

    def _payload(self, **kw):
        p = {"title": "Dentist", "date": "2026-07-24", "startTime": "11:00",
             "endTime": "12:00", "allDay": False, "location": "", "notes": "",
             "scope": "occurrence",
             "sources": [
                 {"label": "Personal", "calendarId": "personal@example.com",
                  "eventId": "e1", "seriesId": "s1"},
                 {"label": "Second", "calendarId": "second@example.com",
                  "eventId": "e2", "seriesId": None}]}
        p.update(kw)
        return p

    def test_patches_each_source_with_its_own_calendar_and_id(self):
        r = ourcal._google_update(self._payload())
        self.assertTrue(r["ok"])
        self.assertEqual([(o["calendarId"], o["eventId"]) for o in self.ops],
                         [("personal@example.com", "e1"),
                          ("second@example.com", "e2")])

    def test_uses_patch_not_insert(self):
        ourcal._google_update(self._payload())
        self.assertEqual([o["op"] for o in self.ops], ["patch", "patch"])

    def test_series_scope_patches_the_series_id(self):
        ourcal._google_update(self._payload(scope="series"))
        self.assertEqual([o["eventId"] for o in self.ops], ["s1", "e2"])

    def test_never_mails_guests(self):
        ourcal._google_update(self._payload())
        self.assertEqual([o["sendUpdates"] for o in self.ops], ["none", "none"])

    def test_body_carries_no_busy_free_state(self):
        ourcal._google_update(self._payload())
        self.assertNotIn("transparency", self.ops[0]["body"])

    def test_one_failure_does_not_abort_the_others(self):
        self.errors["e1"] = _FakeHttpError(403)
        r = ourcal._google_update(self._payload())
        self.assertFalse(r["ok"])
        self.assertFalse(r["results"][0]["ok"])
        self.assertTrue(r["results"][1]["ok"])
        self.assertEqual(len(self.ops), 2)

    def test_403_reads_as_a_permission_problem(self):
        self.errors["e1"] = _FakeHttpError(403)
        r = ourcal._google_update(self._payload())
        self.assertIn("organizer", r["results"][0]["error"].lower())

    def test_a_title_only_series_edit_does_not_move_the_recurrence(self):
        # Patching a recurring master's start rewrites DTSTART, and every
        # occurrence before the new date is gone with nothing to signal it.
        # The date box is prefilled from the occurrence, so an untouched date
        # must reach the wire as no date at all.
        ourcal._google_update(self._payload(scope="series", changed=["title"]))
        body = self.ops[0]["body"]
        self.assertEqual(self.ops[0]["eventId"], "s1")
        self.assertNotIn("start", body)
        self.assertNotIn("end", body)
        self.assertEqual(body["summary"], "Dentist")

    def test_missing_event_is_a_failure_not_a_success(self):
        # Unlike delete, where "already gone" achieved the goal, an edit that
        # cannot find its event did not land.
        self.errors["e1"] = _FakeHttpError(404)
        r = ourcal._google_update(self._payload())
        self.assertFalse(r["results"][0]["ok"])
        self.assertIn("refresh", r["results"][0]["error"].lower())


class TestForwarding(unittest.TestCase):
    """Forwarding attaches an outside guest to one copy; mirrors stay private."""

    def setUp(self):
        pin_accounts(self)
        self.ops = []
        real = ourcal.service_for
        ourcal.service_for = lambda label, email: _FakeService(self.ops)
        self.addCleanup(lambda: setattr(ourcal, "service_for", real))

    def _payload(self, **kw):
        p = {"title": "AI Tinkerers Bash", "date": "2026-07-28",
             "startTime": "18:00", "endTime": "22:00", "allDay": False,
             "notes": "", "location": "1301 2nd Ave", "mode": "copies",
             "detail": "busy",
             "targets": [{"label": "Personal", "blocking": True},
                         {"label": "Second", "blocking": True}]}
        p.update(kw)
        return p

    def test_host_copy_gets_full_details_despite_busy_setting(self):
        ourcal._google_create(self._payload(forwardTo=["sam@example.com"],
                                            forwardFrom="Personal"))
        host = self.ops[0]["body"]
        self.assertEqual(host["summary"], "AI Tinkerers Bash")
        self.assertEqual(host["location"], "1301 2nd Ave")

    def test_mirrors_stay_anonymous(self):
        ourcal._google_create(self._payload(forwardTo=["sam@example.com"],
                                            forwardFrom="Personal"))
        mirror = self.ops[1]["body"]
        self.assertEqual(mirror["summary"], "Busy")
        self.assertNotIn("location", mirror)

    def test_only_host_carries_the_guest_and_notifies(self):
        ourcal._google_create(self._payload(forwardTo=["sam@example.com"],
                                            forwardFrom="Personal"))
        self.assertEqual(self.ops[0]["body"]["attendees"],
                         [{"email": "sam@example.com"}])
        self.assertEqual(self.ops[0]["sendUpdates"], "all")
        self.assertNotIn("attendees", self.ops[1]["body"])
        self.assertEqual(self.ops[1]["sendUpdates"], "none")

    def test_multiple_forward_addresses(self):
        ourcal._google_create(self._payload(
            forwardTo=["sam@example.com", "kim@example.org"],
            forwardFrom="Personal"))
        self.assertEqual(self.ops[0]["body"]["attendees"],
                         [{"email": "sam@example.com"},
                          {"email": "kim@example.org"}])

    def test_without_forwarding_all_copies_follow_detail(self):
        ourcal._google_create(self._payload())
        self.assertEqual([o["body"]["summary"] for o in self.ops],
                         ["Busy", "Busy"])
        self.assertTrue(all("attendees" not in o["body"] for o in self.ops))

    def test_invalid_forward_address_is_rejected_before_any_call(self):
        # Validation sits on create_event so demo and Google agree; a backend
        # that skipped it would let the demo "succeed" where Google refuses.
        r = ourcal.create_event(self._payload(forwardTo=["not-an-email"],
                                              forwardFrom="Personal"))
        self.assertFalse(r["ok"])
        self.assertIn("not-an-email", r["error"])
        self.assertEqual(self.ops, [])   # nothing created

    def test_demo_backend_rejects_bad_address_too(self):
        os.environ["OURCAL_DEMO"] = "1"
        ourcal.reset_demo()
        before = len(ourcal.get_events()["events"])
        r = ourcal.create_event(self._payload(forwardTo=["nope"],
                                              forwardFrom="Personal"))
        self.assertFalse(r["ok"])
        self.assertEqual(len(ourcal.get_events()["events"]), before)


class TestEmailValidation(unittest.TestCase):
    def test_accepts_ordinary_addresses(self):
        for addr in ["sam@example.com", "a.b+tag@sub.example.co.uk"]:
            self.assertTrue(ourcal.valid_email(addr), addr)

    def test_rejects_malformed(self):
        for addr in ["", "  ", "no-at-sign", "a@", "@b.com", "a b@c.com",
                     "a@b", None]:
            self.assertFalse(ourcal.valid_email(addr), repr(addr))


class TestAccountIdentity(unittest.TestCase):
    """A token must belong to the account it is filed under (see primary_email).

    The signed-in account's address is the id of its primary calendar, which is
    already present in the calendarList response — so verifying costs no extra
    API call.
    """

    def _cals(self, primary_id, extra=()):
        cals = [{"id": "team@group.calendar.google.com", "selected": True}]
        cals.extend(extra)
        if primary_id is not None:
            cals.append({"id": primary_id, "primary": True})
        return cals

    def test_primary_email_is_id_of_primary_calendar(self):
        self.assertEqual(
            ourcal.primary_email(self._cals("third@example.com")),
            "third@example.com")

    def test_primary_email_normalizes_case(self):
        self.assertEqual(
            ourcal.primary_email(self._cals("Personal@Example.com")),
            "personal@example.com")

    def test_primary_email_empty_when_no_primary(self):
        self.assertEqual(ourcal.primary_email(self._cals(None)), "")

    def test_no_error_when_account_matches(self):
        cals = self._cals("second@example.com")
        self.assertIsNone(
            ourcal.account_mismatch("Second", "second@example.com", cals))

    def test_no_error_when_match_differs_only_by_case(self):
        cals = self._cals("Second@EXAMPLE.com")
        self.assertIsNone(
            ourcal.account_mismatch("Second", "second@example.com", cals))

    def test_no_error_when_primary_undeterminable(self):
        # Can't identify the account -> don't block the user on a guess.
        self.assertIsNone(
            ourcal.account_mismatch("Personal", "personal@example.com",
                                    self._cals(None)))

    def test_reports_the_work_mislabel(self):
        # The real-world bug: signing in as the personal account at the
        # "Work" prompt stamped personal events with the Work badge.
        cals = self._cals("personal@example.com")
        msg = ourcal.account_mismatch("Work", "work@example.com", cals)
        self.assertIsNotNone(msg)
        self.assertIn("personal@example.com", msg)        # who actually signed in
        self.assertIn("work@example.com", msg)    # who was expected
        self.assertIn("token_work.json", msg)     # what to delete

    def test_message_names_token_file_via_slug(self):
        cals = self._cals("fourth@example.com")
        msg = ourcal.account_mismatch("Third", "third@example.com", cals)
        self.assertIn("token_third.json", msg)

    def test_mismatched_account_yields_no_events(self):
        # Wrong account must produce an error and zero events -- never events
        # filed under the wrong label.
        cals = self._cals("personal@example.com")
        self.assertIsNotNone(
            ourcal.account_mismatch("Fourth", "fourth@example.com", cals))


import threading, urllib.error, urllib.request


class TestHttp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["OURCAL_DEMO"] = "1"
        ourcal.reset_demo()
        cls.server = ourcal.make_server(0)
        cls.port = cls.server.server_address[1]
        cls.t = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.t.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def _get(self, path):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            headers={"Content-Type": "application/json",
                     "X-OurCal-Token": ourcal.SESSION_TOKEN})
        with urllib.request.urlopen(req) as r:
            return r.status, r.read().decode()

    def _post(self, path, obj):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(obj).encode(), method="POST",
            headers={"Content-Type": "application/json",
                     "X-OurCal-Token": ourcal.SESSION_TOKEN})
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read().decode())

    def test_root_serves_ourcal_html(self):
        # "/" requires the session key as a ?k= query parameter, not the
        # X-OurCal-Token header _get sends — a header is not something a
        # page navigation can attach to itself.
        status, body = self._get(f"/?k={ourcal.SESSION_TOKEN}")
        self.assertEqual(status, 200)
        self.assertIn("OurCal", body)
        self.assertNotIn("__POLL_MS__", body)  # placeholder substituted

    def test_events_endpoint_stable_twice(self):
        _, a = self._get("/api/events")
        _, b = self._get("/api/events")
        ja, jb = json.loads(a), json.loads(b)
        self.assertEqual(ja["events"], jb["events"])  # no mutation across reads

    def test_create_timed_then_allday_then_invite(self):
        _, r1 = self._post("/api/create", {
            "title": "T", "date": "2026-07-24", "startTime": "10:00",
            "endTime": "10:30", "allDay": False, "notes": "", "location": "",
            "mode": "copies", "targets": [{"label": "Personal", "blocking": True}]})
        self.assertTrue(r1["ok"])
        _, r2 = self._post("/api/create", {
            "title": "A", "date": "2026-08-01", "allDay": True, "startTime": "",
            "endTime": "", "notes": "", "location": "", "mode": "copies",
            "targets": [{"label": "Personal", "blocking": False}]})
        self.assertTrue(r2["ok"])
        _, r3 = self._post("/api/create", {
            "title": "I", "date": "2026-07-25", "startTime": "11:00",
            "endTime": "11:30", "allDay": False, "notes": "", "location": "",
            "mode": "invite", "inviteFrom": "Personal",
            "targets": [{"label": "Personal", "blocking": True},
                        {"label": "Work", "blocking": True}]})
        self.assertTrue(r3["ok"])


class _TmpData:
    """Point data_dir() at a throwaway directory.

    CONTRIBUTING.md:49 — never point tests at real credentials. In a checkout
    data_dir() is APP_DIR, the repo itself, where the developer's real
    credentials.json, accounts.json and token files live. A test that writes
    without redirecting would overwrite them.
    """

    def _tmp_data(self):
        import shutil
        import tempfile
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        real = ourcal.data_dir
        ourcal.data_dir = lambda: tmp
        self.addCleanup(lambda: setattr(ourcal, "data_dir", real))
        return tmp


class TestCallerAuth(_TmpData, unittest.TestCase):
    """/api/import writes OAuth credentials to disk and, before this guard,
    validated nothing about who asked. text/plain is CORS-safelisted, so a
    cross-origin fetch from any page the user visits is a simple request: no
    preflight, the request is delivered and processed, and only the response
    is unreadable — _local_caller's Origin check closes that hole, and its
    Host check closes DNS rebinding via a forged Host header, for every
    route at once.

    Neither check closes the non-browser hole. A native caller sends no
    Origin header, and the guard accepts `origin is None` — that is exactly
    what lets this app's own page, and every urllib-based test in this
    suite, through. On Android, where loopback is not isolated between
    apps, that same gap means any other app installed on the device can
    still drive /api/import with no Origin and no preflight to stop it.
    That hole is closed separately, by _api_token_ok's per-session token —
    this class is scoped to _local_caller's two browser-shaped checks, so
    every request built here (via _req) carries a valid token already, and
    only Origin/Host vary. See TestSessionToken for the token guard itself."""

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
        # Every route in this class is exercised over real HTTP, and the
        # guard being tested is exactly the thing that is supposed to stand
        # between an unauthenticated caller and the owner's real
        # credentials.json/accounts.json/token files. Relying on the guard
        # to 403 before any real file is touched is the same "safe because
        # something else fires first" reasoning that was rejected for
        # TestSetupRoutes — redirect data_dir() per test here too, so a
        # guard regression (or a future test added to this class that
        # reaches a real handler) can never read or write the real
        # directory. Per-test, not per-class, for the same reason
        # TestSetupRoutes does it per-test: no leftover state from one test
        # can leak into another.
        self.tmp = self._tmp_data()
        real_accounts = ourcal.ACCOUNTS
        self.addCleanup(lambda: setattr(ourcal, "ACCOUNTS", real_accounts))

    def _req(self, path, method="GET", origin=None, host=None, body=None):
        # Always carry the real token: the rejection cases in this class are
        # rejected by _local_caller (Host/Origin), which runs before the
        # token is ever examined, so sending it here doesn't change their
        # outcome — but the accepted cases need it now that /api/* requires
        # one, and TestCallerAuth's job is Host/Origin, not the token.
        headers = {"Content-Type": "application/json",
                   "X-OurCal-Token": ourcal.SESSION_TOKEN}
        if origin is not None:
            headers["Origin"] = origin
        if host is not None:
            headers["Host"] = host
        data = None
        if method == "POST":
            data = json.dumps(body or {}).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=data,
            method=method, headers=headers)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def test_no_origin_succeeds(self):
        # What the app's own page — and urllib, which every other HTTP test
        # in this suite uses — actually sends: no Origin header at all.
        status, _ = self._req("/api/status")
        self.assertEqual(status, 200)

    def test_cross_site_origin_is_rejected_on_import(self):
        # The demonstrated attack: a page on any other origin POSTing to
        # /api/import, which would otherwise write attacker-controlled
        # credentials.json to disk.
        status, body = self._req(
            "/api/import", method="POST", origin="https://evil.example",
            body={"bundle": "nonsense", "passphrase": "x"})
        self.assertEqual(status, 403)
        self.assertIn("error", json.loads(body))

    def test_cross_site_origin_is_rejected_on_create(self):
        # /api/create is exposed by the same hole; in scope alongside import.
        status, body = self._req(
            "/api/create", method="POST", origin="https://evil.example",
            body={})
        self.assertEqual(status, 403)
        self.assertIn("error", json.loads(body))

    def test_foreign_host_is_rejected_on_status(self):
        # DNS rebinding: a page whose hostname later resolves to 127.0.0.1
        # still sends its own Host header, not "127.0.0.1" or "localhost".
        status, body = self._req("/api/status", host="evil.example")
        self.assertEqual(status, 403)
        self.assertIn("error", json.loads(body))

    def test_loopback_origin_on_any_port_is_accepted(self):
        # start_server() falls back to an ephemeral port when 8756 is taken,
        # so the Origin check must accept any loopback port, not just 8756.
        status, _ = self._req("/api/status", origin="http://127.0.0.1:9999")
        self.assertEqual(status, 200)


class TestDeleteEndpoint(unittest.TestCase):
    """End-to-end over HTTP in demo mode: a deleted event stops coming back."""

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
        ourcal.reset_demo()

    def _post(self, path, obj):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(obj).encode(), method="POST",
            headers={"Content-Type": "application/json",
                     "X-OurCal-Token": ourcal.SESSION_TOKEN})
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read().decode())

    def _events(self):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/events",
            headers={"X-OurCal-Token": ourcal.SESSION_TOKEN})
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read().decode())["events"]

    def _find(self, title):
        return next(e for e in self._events() if e["title"] == title)

    def test_events_expose_sources_for_addressing(self):
        ev = self._find("Team Standup")
        self.assertEqual(len(ev["sources"]), 1)
        self.assertEqual(ev["sources"][0]["eventId"], "standup-1")

    def test_merged_event_exposes_both_sources(self):
        ev = self._find("Family dinner")
        self.assertEqual([s["label"] for s in ev["sources"]],
                         ["Personal", "Work"])

    def test_delete_removes_the_event(self):
        ev = self._find("Team Standup")
        status, res = self._post("/api/delete",
                                 {"scope": "occurrence",
                                  "sources": ev["sources"]})
        self.assertEqual(status, 200)
        self.assertTrue(res["ok"])
        self.assertNotIn("Team Standup", [e["title"] for e in self._events()])

    def test_delete_leaves_other_events_alone(self):
        before = len(self._events())
        ev = self._find("Team Standup")
        self._post("/api/delete",
                   {"scope": "occurrence", "sources": ev["sources"]})
        self.assertEqual(len(self._events()), before - 1)

    def test_deleting_one_source_of_a_merged_row(self):
        ev = self._find("Family dinner")
        one = [s for s in ev["sources"] if s["label"] == "Work"]
        res = self._post("/api/delete",
                         {"scope": "occurrence", "sources": one})[1]
        self.assertTrue(res["ok"])
        self.assertEqual([r["label"] for r in res["results"]], ["Work"])

    def test_unknown_post_route_still_404s(self):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/nope", data=b"{}",
            method="POST", headers={"Content-Type": "application/json",
                                    "X-OurCal-Token": ourcal.SESSION_TOKEN})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req)
        self.assertEqual(cm.exception.code, 404)


class TestUpdateEndpoint(unittest.TestCase):
    """End-to-end over HTTP in demo mode: an edited event comes back changed."""

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
        ourcal.reset_demo()

    def _post(self, path, obj):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(obj).encode(), method="POST",
            headers={"Content-Type": "application/json",
                     "X-OurCal-Token": ourcal.SESSION_TOKEN})
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read().decode())

    def _events(self):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/events",
            headers={"X-OurCal-Token": ourcal.SESSION_TOKEN})
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read().decode())["events"]

    def test_edit_round_trips_through_the_api(self):
        standup = [e for e in self._events() if e["title"] == "Team Standup"][0]
        status, res = self._post("/api/update", {
            "title": "Standup (moved)", "date": "2026-09-09",
            "startTime": "15:00", "endTime": "15:30", "allDay": False,
            "location": "Room 2", "notes": "", "scope": "occurrence",
            "sources": standup["sources"]})
        self.assertEqual(status, 200)
        self.assertTrue(res["ok"])
        after = [e for e in self._events() if e["title"] == "Standup (moved)"]
        self.assertEqual(len(after), 1)
        self.assertTrue(after[0]["start"].startswith("2026-09-09T15:00"))
        self.assertEqual(after[0]["location"], "Room 2")

    def test_changed_list_survives_the_wire(self):
        # The browser names the fields the user touched; if that list did not
        # reach build_patch_body the whole form would be written back again.
        standup = [e for e in self._events() if e["title"] == "Team Standup"][0]
        self.assertEqual(standup["location"], "Zoom")
        status, res = self._post("/api/update", {
            "title": "Standup (renamed)", "date": "2026-09-09",
            "startTime": "15:00", "endTime": "15:30", "allDay": False,
            "location": "", "notes": "", "changed": ["title"],
            "scope": "occurrence", "sources": standup["sources"]})
        self.assertEqual(status, 200)
        self.assertTrue(res["ok"])
        after = [e for e in self._events() if e["title"] == "Standup (renamed)"][0]
        self.assertEqual(after["location"], "Zoom")       # untouched, so unsent
        self.assertEqual(after["start"], standup["start"])

    def test_bad_payload_returns_ok_false_with_a_reason(self):
        standup = [e for e in self._events() if e["title"] == "Team Standup"][0]
        _, res = self._post("/api/update", {
            "title": "x", "date": "2026-09-09", "startTime": "15:00",
            "endTime": "14:00", "allDay": False, "location": "", "notes": "",
            "scope": "occurrence", "sources": standup["sources"]})
        self.assertFalse(res["ok"])
        self.assertIn("end", res["error"].lower())


class TestBootstrapGuard(unittest.TestCase):
    def test_demo_skips_bootstrap(self):
        os.environ["OURCAL_DEMO"] = "1"
        os.environ.pop("OURCAL_REEXEC", None)
        self.assertFalse(ourcal.needs_bootstrap())

    def test_reexec_guard_skips_bootstrap(self):
        os.environ.pop("OURCAL_DEMO", None)
        os.environ["OURCAL_REEXEC"] = "1"
        try:
            self.assertFalse(ourcal.needs_bootstrap())
        finally:
            os.environ["OURCAL_DEMO"] = "1"  # restore for other tests
            os.environ.pop("OURCAL_REEXEC", None)


class TestTimezone(unittest.TestCase):
    """Android has no system tz database, so tz() must survive a failing
    ZoneInfo() by reading tzdata's bytes directly."""

    def setUp(self):
        ourcal._TZ = None                       # defeat the cache per test
        self.addCleanup(lambda: setattr(ourcal, "_TZ", None))

    def test_returns_a_usable_zone(self):
        now = datetime.datetime.now(ourcal.tz())
        self.assertIsNotNone(now.utcoffset())

    def test_is_cached(self):
        self.assertIs(ourcal.tz(), ourcal.tz())

    def test_falls_back_when_zoneinfo_cannot_find_the_zone(self):
        # Simulate the Android failure: the direct constructor raises, and the
        # tzdata-bytes path must still produce the zone.
        import zoneinfo
        real = ourcal.ZoneInfo

        class NoSystemTz:
            def __init__(self, key):
                raise zoneinfo.ZoneInfoNotFoundError(key)
            from_file = staticmethod(real.from_file)

        ourcal.ZoneInfo = NoSystemTz
        self.addCleanup(lambda: setattr(ourcal, "ZoneInfo", real))
        try:
            import tzdata  # noqa: F401
        except ImportError:
            self.skipTest("tzdata not installed in this environment")
        self.assertIsNotNone(datetime.datetime.now(ourcal.tz()).utcoffset())


class TestPlatform(unittest.TestCase):
    """The same source runs on macOS and Android; the platform seams must
    behave correctly on the side we can test — the desktop side."""

    def test_not_android_off_android(self):
        # Chaquopy is unimportable here, so this must be False.
        self.assertFalse(ourcal.is_android())

    def test_data_dir_ignores_android_branch_off_android(self):
        real = ourcal.is_bundled
        ourcal.is_bundled = lambda: False
        self.addCleanup(lambda: setattr(ourcal, "is_bundled", real))
        self.assertEqual(ourcal.data_dir(), ourcal.APP_DIR)

    def test_oauth_flow_is_plain_run_local_server_off_android(self):
        # On desktop, run_oauth_flow must not touch webbrowser or defer the
        # token exchange — it just runs the standard flow, bounded the same
        # way the Android branch already is: an abandoned browser tab must
        # not wait forever and wedge _SIGNIN in "waiting" for good.
        calls = {}

        class FakeFlow:
            def run_local_server(self, **kw):
                calls["kw"] = kw
                return "creds"

        self.assertEqual(ourcal.run_oauth_flow(FakeFlow()), "creds")
        self.assertEqual(calls["kw"], {"port": 0, "timeout_seconds": 300})


class TestClampDays(unittest.TestCase):
    """The agenda window comes from a URL, so it is untrusted input."""

    def test_default_when_absent(self):
        self.assertEqual(ourcal.clamp_days(None), ourcal.DAYS_AHEAD)

    def test_default_when_not_a_number(self):
        # A bad value should still show you your calendar, not a stack trace.
        self.assertEqual(ourcal.clamp_days("banana"), ourcal.DAYS_AHEAD)
        self.assertEqual(ourcal.clamp_days(""), ourcal.DAYS_AHEAD)

    def test_accepts_a_string_because_query_params_are_strings(self):
        self.assertEqual(ourcal.clamp_days("90"), 90)

    def test_clamps_to_the_ceiling(self):
        # Each extra day is more Google calls; a mistyped URL must not cost a
        # minute of API time.
        self.assertEqual(ourcal.clamp_days(99999), ourcal.MAX_DAYS_AHEAD)

    def test_never_returns_a_useless_window(self):
        self.assertEqual(ourcal.clamp_days(0), 1)
        self.assertEqual(ourcal.clamp_days(-5), 1)


class TestEventsWindow(unittest.TestCase):
    def setUp(self):
        os.environ["OURCAL_DEMO"] = "1"
        ourcal.reset_demo()

    def test_envelope_reports_the_window_it_used(self):
        self.assertEqual(ourcal.get_events()["days"], ourcal.DAYS_AHEAD)
        self.assertEqual(ourcal.get_events(days="180")["days"], 180)

    def test_bad_window_falls_back_rather_than_failing(self):
        self.assertEqual(ourcal.get_events(days="nonsense")["days"],
                         ourcal.DAYS_AHEAD)

    def test_google_collect_honours_the_window(self):
        # The window is the only thing bounding how much Google is asked for,
        # so it must reach timeMax rather than being silently dropped.
        seen = {}
        real = ourcal.list_account_events

        def fake(label, email, time_min, time_max):
            seen["max"] = time_max
            return [], None

        ourcal.list_account_events = fake
        self.addCleanup(lambda: setattr(ourcal, "list_account_events", real))
        pin_accounts(self)
        now = datetime.datetime(2026, 7, 1, tzinfo=ZoneInfo(ourcal.TIMEZONE))
        ourcal._google_collect(now, 90)
        self.assertTrue(seen["max"].startswith("2026-09-29"))


class TestDataDir(unittest.TestCase):
    """A packaged .app is replaced on every update and is code-signed, so user
    state cannot live inside it."""

    def _bundled(self, yes):
        real = ourcal.is_bundled
        ourcal.is_bundled = lambda: yes
        self.addCleanup(lambda: setattr(ourcal, "is_bundled", real))

    def test_source_checkout_keeps_state_beside_the_script(self):
        self._bundled(False)
        self.assertEqual(ourcal.data_dir(), ourcal.APP_DIR)

    def test_bundled_app_uses_application_support(self):
        self._bundled(True)
        self.assertEqual(ourcal.data_dir(), ourcal.SUPPORT_DIR)

    def test_application_support_path_is_under_the_user_library(self):
        self.assertTrue(ourcal.SUPPORT_DIR.endswith(
            "Library/Application Support/OurCal"))
        self.assertTrue(os.path.isabs(ourcal.SUPPORT_DIR))
        self.assertNotIn("~", ourcal.SUPPORT_DIR)   # expanded, not literal

    def test_token_path_follows_the_data_dir(self):
        self._bundled(True)
        self.assertEqual(ourcal.token_path("Leela K"),
                         os.path.join(ourcal.SUPPORT_DIR, "token_leela-k.json"))
        self._bundled(False)
        self.assertEqual(ourcal.token_path("Leela K"),
                         os.path.join(ourcal.APP_DIR, "token_leela-k.json"))

    def test_source_checkout_creates_no_application_support_dir(self):
        self._bundled(False)
        self.assertEqual(ourcal.ensure_data_dir(), ourcal.APP_DIR)

    def test_bundled_install_gets_a_data_dir(self):
        import shutil
        import tempfile
        tmp = tempfile.mkdtemp()
        shutil.rmtree(tmp)                     # ensure_data_dir must create it
        self.addCleanup(shutil.rmtree, tmp, True)
        self._bundled(True)
        real = ourcal.SUPPORT_DIR
        ourcal.SUPPORT_DIR = tmp
        self.addCleanup(lambda: setattr(ourcal, "SUPPORT_DIR", real))
        self.assertEqual(ourcal.ensure_data_dir(), tmp)
        self.assertTrue(os.path.isdir(tmp))

    def test_no_silent_migration_from_the_bundle(self):
        # APP_DIR inside a packaged app points at the bundle, never at a
        # checkout, so copying from it would find nothing while looking like
        # it worked. Importing sign-ins is a documented manual copy.
        self.assertFalse(hasattr(ourcal, "migrate_user_files"))


class TestStartServer(unittest.TestCase):
    """A double-clicked .app has no terminal, so refusing to start on a busy
    port would look like an icon that does nothing."""

    def test_falls_back_to_a_free_port_when_the_preferred_one_is_taken(self):
        blocker = ourcal.make_server(0)
        taken = blocker.server_address[1]
        self.addCleanup(blocker.server_close)
        real = ourcal.PORT
        ourcal.PORT = taken
        self.addCleanup(lambda: setattr(ourcal, "PORT", real))

        server, url = ourcal.start_server()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        self.assertNotIn(f":{taken}", url)
        self.assertTrue(url.startswith("http://127.0.0.1:"))

    def test_uses_the_preferred_port_when_it_is_free(self):
        probe = ourcal.make_server(0)
        free = probe.server_address[1]
        probe.server_close()                 # release it, then claim it
        real = ourcal.PORT
        ourcal.PORT = free
        self.addCleanup(lambda: setattr(ourcal, "PORT", real))

        server, url = ourcal.start_server()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        # url now ends in "?k=<token>", not the port, so check the port shows
        # up before the query string rather than at the very end.
        self.assertIn(f":{free}/?k=", url)

    def test_the_returned_url_carries_the_session_key(self):
        # This is the actual fix: the URL handed to the in-process view (and
        # printed by run_server) is the only route by which a legitimate
        # caller ever reaches / or /setup now that both require ?k=.
        server, url = ourcal.start_server()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        self.assertIn(f"k={ourcal.SESSION_TOKEN}", url)
        with urllib.request.urlopen(url, timeout=5) as r:
            self.assertEqual(r.status, 200)

    def test_the_server_actually_answers(self):
        server, url = ourcal.start_server()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        port = server.server_address[1]
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/events",
            headers={"X-OurCal-Token": ourcal.SESSION_TOKEN})
        with urllib.request.urlopen(req, timeout=5) as r:
            self.assertEqual(r.status, 200)


class TestWantWindow(unittest.TestCase):
    def _bundled(self, yes):
        real = ourcal.is_bundled
        ourcal.is_bundled = lambda: yes
        self.addCleanup(lambda: setattr(ourcal, "is_bundled", real))

    def test_bundled_app_always_wants_a_window(self):
        self._bundled(True)
        self.assertTrue(ourcal.want_window())

    def test_plain_script_run_keeps_using_the_browser(self):
        import sys
        self._bundled(False)
        real = sys.argv
        sys.argv = ["ourcal.py"]
        self.addCleanup(lambda: setattr(sys, "argv", real))
        self.assertFalse(ourcal.want_window())

    def test_window_flag_opts_a_checkout_in(self):
        import sys
        self._bundled(False)
        real = sys.argv
        sys.argv = ["ourcal.py", "--window"]
        self.addCleanup(lambda: setattr(sys, "argv", real))
        self.assertTrue(ourcal.want_window())


class TestVenvBasePython(unittest.TestCase):
    """macOS ships 3.9, which Google's libraries warn about on every launch."""

    def test_uses_the_running_interpreter_when_new_enough(self):
        import sys
        if sys.version_info < (3, 10):
            self.skipTest("running on 3.9; covered by the fallback test")
        self.assertEqual(ourcal.venv_base_python(), sys.executable)

    def test_prefers_a_newer_interpreter_when_running_on_39(self):
        import sys
        if sys.version_info >= (3, 10):
            self.skipTest("only meaningful when running on 3.9")
        found = ourcal.venv_base_python()
        # Either it located a newer python, or none is installed and it fell
        # back to the current one — both are correct, neither may be empty.
        self.assertTrue(found)

    def test_candidate_list_is_ordered_newest_first(self):
        nums = [tuple(int(p) for p in n.split("python")[1].split("."))
                for n in ourcal.NEWER_PYTHONS]
        self.assertEqual(nums, sorted(nums, reverse=True))
        self.assertTrue(all(n >= (3, 10) for n in nums))


class TestPageStructure(unittest.TestCase):
    def test_page_has_core_markers(self):
        p = ourcal.PAGE
        for marker in ["OurCal", "New event", "id=\"agenda\"", "id=\"chips\"",
                       "id=\"modal\"", "prefers-color-scheme",
                       "data-theme", "/api/events", "/api/create", "__POLL_MS__",
                       # delete + sync
                       "/api/delete", "id=\"delModal\"", "id=\"delRows\"",
                       "id=\"wrap-scope\"", "id=\"seg-detail\"", "id=\"f-fwd\"",
                       "id=\"f-fwdfrom\"", "openSync", "openDelete",
                       "submitDelete"]:
            self.assertIn(marker, p, f"missing {marker!r}")

    def test_delete_dialog_offers_occurrence_and_series(self):
        self.assertIn('value="occurrence"', ourcal.PAGE)
        self.assertIn('value="series"', ourcal.PAGE)

    def test_detail_segment_offers_full_and_busy(self):
        self.assertIn('data-v="full"', ourcal.PAGE)
        self.assertIn('data-v="busy"', ourcal.PAGE)

    def test_target_lookup_is_scoped_to_the_account_picker(self):
        # The delete dialog renders .acct rows too, but without .inc/.seg.bf.
        # An unscoped querySelectorAll(".acct") picked those up and made every
        # later Sync/Create click die silently on a null .checked.
        self.assertNotIn('querySelectorAll(".acct")', ourcal.PAGE)
        self.assertIn('querySelectorAll("#acctRows .acct")', ourcal.PAGE)
        self.assertNotIn('querySelector(".host:checked")', ourcal.PAGE)

    def test_click_handlers_report_errors_instead_of_dying_silently(self):
        self.assertIn("function guard(", ourcal.PAGE)
        for wired in ["guard(submit)", "guard(submitDelete)", "guard(openModal)",
                      "guard(submitEdit)"]:
            self.assertIn(wired, ourcal.PAGE, f"unguarded handler: {wired}")

    def test_page_has_edit_dialog_markers(self):
        for marker in ["/api/update", "id=\"editModal\"", "id=\"editRows\"",
                       "id=\"e-title\"", "id=\"e-date\"", "id=\"e-start\"",
                       "id=\"e-end\"", "id=\"e-allday\"", "id=\"e-loc\"",
                       "id=\"e-notes\"", "id=\"wrap-escope\"",
                       "openEdit", "submitEdit"]:
            self.assertIn(marker, ourcal.PAGE, f"missing {marker!r}")

    def test_edit_dialog_offers_occurrence_and_series(self):
        self.assertIn('value="eoccurrence"', ourcal.PAGE)
        self.assertIn('value="eseries"', ourcal.PAGE)

    def test_edit_rows_are_scoped_like_the_other_pickers(self):
        # Same bug class as the delete dialog: an unscoped .acct selector picks
        # up rows from other modals and breaks unrelated buttons.
        self.assertIn('querySelectorAll("#editRows .acct")', ourcal.PAGE)

    def test_edit_button_is_disabled_when_nothing_is_editable(self):
        self.assertIn("canEdit", ourcal.PAGE)

    def test_a_disabled_row_button_looks_disabled(self):
        # .mini sets an explicit color, which beats the browser's default
        # disabled styling — without a :disabled rule a dead Edit button is
        # pixel-identical to a live one, and the explanatory title tooltip
        # never appears on a tap.
        self.assertIn(".mini:disabled", ourcal.PAGE)
        self.assertIn("not-allowed", ourcal.PAGE)

    def test_edit_targets_calendars_by_id_not_by_position(self):
        # A positional index into a re-derived array silently addresses the
        # WRONG CALENDAR if either filter expression drifts. Matching on
        # eventId fails safe: worst case is finding no source at all.
        self.assertIn('class="editsrc" data-eid=', ourcal.PAGE)
        self.assertNotIn('class="editsrc" data-i=', ourcal.PAGE)
        self.assertIn("s.eventId===c.dataset.eid", ourcal.PAGE)

    def test_delete_targets_calendars_by_identity_not_position(self):
        # Deleting is the higher-stakes half of the same bug: a drifted index
        # would remove someone's event from the WRONG calendar, irreversibly
        # from the user's point of view.
        self.assertIn('class="delsrc" checked data-eid=', ourcal.PAGE)
        self.assertNotIn('class="delsrc" checked data-i=', ourcal.PAGE)

    def test_rows_are_identified_by_event_AND_calendar(self):
        # Google gives an invited event the SAME id on every attendee's
        # calendar, so the id alone does not identify a source. Matching on it
        # would resolve every checkbox to the first calendar and write to the
        # wrong one. Both write paths must compare the pair.
        self.assertIn("data-cal=", ourcal.PAGE)
        self.assertIn('s.eventId===c.dataset.eid&&(s.calendarId||"")===c.dataset.cal',
                      ourcal.PAGE)
        self.assertNotIn("s.eventId===c.dataset.eid)", ourcal.PAGE)  # id alone

    def test_delete_rows_are_scoped_to_their_own_picker(self):
        # An unscoped selector once picked up rows from a different modal and
        # broke unrelated buttons; both write paths must stay scoped.
        self.assertIn('querySelectorAll("#delRows .acct")', ourcal.PAGE)
        self.assertNotIn('querySelectorAll(".delsrc")', ourcal.PAGE)

    def test_edit_sends_only_the_fields_the_user_touched(self):
        self.assertIn("EDIT_BEFORE", ourcal.PAGE)
        self.assertIn("changed", ourcal.PAGE)
        self.assertIn("No changes to save", ourcal.PAGE)

    def test_series_hint_says_the_date_rebases_the_series(self):
        # "shifts every occurrence" reads like a translation; what actually
        # happens is that every occurrence before the new date disappears.
        self.assertIn("rebase", ourcal.PAGE.lower())

    def test_banner_collapses_when_the_device_is_unset_up(self):
        # Four accounts with no credentials.json produced four identical walls
        # of text naming a path the user cannot reach. One banner with a way
        # out replaces them.
        self.assertIn("errs.every(e=>e.setup)", ourcal.PAGE)
        self.assertIn("isn't set up on this device yet", ourcal.PAGE)
        self.assertIn("Set up this device", ourcal.PAGE)

    def test_per_account_banners_are_the_fallback_branch_not_dead_code(self):
        # "Couldn't refresh" being present proves nothing — it predates the
        # collapse. What matters is that the per-account map is the ternary's
        # FALSE branch, gated on every error being a setup error. A regression
        # that hard-wired the collapse would leave the string as dead code and
        # a mere assertIn would still pass.
        page = ourcal.PAGE
        cond = page.index("errs.every(e=>e.setup)")
        collapse = page.index("isn't set up on this device yet")
        per_account = page.index("Couldn't refresh <b>")
        self.assertLess(cond, collapse)          # the condition gates it
        self.assertLess(collapse, per_account)   # per-account is the : branch
        self.assertIn(": errs.map(", page[collapse:per_account])

    def test_setup_stays_reachable_after_setup_succeeds(self):
        # The banner disappears once it works; re-importing after a revoked
        # token must not require breaking the app first. The link must also
        # carry the session key forward — /setup 403s without one, so a
        # stripped-down link would be a dead end.
        self.assertIn('class="setup-link" href="/setup?k=__SESSION_TOKEN__"',
                      ourcal.PAGE)

    def test_no_internal_link_is_missing_the_session_key(self):
        # Every internal navigation (to / or /setup) must carry ?k=, or
        # clicking it is a 403 the user cannot recover from without knowing
        # to retype the URL by hand. Catches a link added later that forgets
        # it, the same way the exploited href="/setup" once did.
        for href in re.findall(r'href=\\?"(/[^"\\]*)', ourcal.PAGE):
            if href.startswith("/api/"):
                continue
            self.assertIn("k=__SESSION_TOKEN__", href, href)


class TestAndroidProbe(unittest.TestCase):
    """The bug this replaces: is_android() imported a Java package, which
    returned False on a real device. Every Android seam stayed dark and
    data_dir() fell through to APP_DIR — Chaquopy's AssetFinder directory,
    which is regenerated on every APK install. The whole suite missed it
    because every existing platform test asserts the desktop side."""

    def _fake_api_level(self):
        import sys
        sys.getandroidapilevel = lambda: 33
        self.addCleanup(lambda: delattr(sys, "getandroidapilevel"))

    def test_true_when_the_interpreter_reports_an_android_api_level(self):
        self._fake_api_level()
        self.assertTrue(ourcal.is_android())

    def test_false_on_a_plain_desktop(self):
        self.assertFalse(ourcal.is_android())

    def test_false_when_the_java_bridge_raises_a_non_importerror(self):
        # The old probe only caught ImportError. A bridge that is present but
        # not ready raises other things, and an uncaught one would crash the
        # app at import time instead of degrading.
        import builtins
        real = builtins.__import__

        def boom(name, *a, **k):
            if name.startswith("com.chaquo"):
                raise RuntimeError("bridge not ready")
            return real(name, *a, **k)

        builtins.__import__ = boom
        self.addCleanup(lambda: setattr(builtins, "__import__", real))
        self.assertFalse(ourcal.is_android())

    def test_true_when_the_chaquopy_import_succeeds(self):
        # The other half of the fallback: `import com.chaquo.python`
        # succeeding, with no interpreter probe available. Nothing exercised
        # this before — the same shape of untested True branch this whole
        # branch exists to fix.
        import sys
        import types
        added = []
        for name in ("com", "com.chaquo", "com.chaquo.python"):
            if name not in sys.modules:
                sys.modules[name] = types.ModuleType(name)
                added.append(name)
        # Remove exactly what we added, nothing a prior/parallel test left.
        self.addCleanup(lambda: [sys.modules.pop(n, None) for n in added])
        self.assertFalse(hasattr(sys, "getandroidapilevel"))
        self.assertTrue(ourcal.is_android())


class TestAndroidDataDir(unittest.TestCase):
    """The Android branch, exercised on the desktop by faking the probe —
    the coverage that never existed."""

    def _android(self, path="/data/data/com.leelakumili.ourcal/files"):
        real_a, real_d = ourcal.is_android, ourcal.android_data_dir
        ourcal.is_android = lambda: True
        ourcal.android_data_dir = lambda: path
        self.addCleanup(lambda: setattr(ourcal, "is_android", real_a))
        self.addCleanup(lambda: setattr(ourcal, "android_data_dir", real_d))

    def test_android_beats_both_desktop_branches(self):
        self._android()
        real = ourcal.is_bundled
        ourcal.is_bundled = lambda: True      # even bundled, Android wins
        self.addCleanup(lambda: setattr(ourcal, "is_bundled", real))
        self.assertEqual(ourcal.data_dir(),
                         "/data/data/com.leelakumili.ourcal/files")

    def test_token_path_follows_the_android_branch(self):
        self._android("/android/files")
        self.assertEqual(ourcal.token_path("Leela K"),
                         "/android/files/token_leela-k.json")

    def test_user_path_follows_the_android_branch(self):
        self._android("/android/files")
        self.assertEqual(ourcal.user_path("credentials.json"),
                         "/android/files/credentials.json")

    def test_data_dir_falls_back_when_the_bridge_import_fails(self):
        # The bridge import demonstrably failed on the device that reported
        # the original bug, for reasons still unknown. If it fails again,
        # data_dir() must degrade (fall through to the desktop rule) rather
        # than raise — an uncaught raise here happens at module import time
        # (ensure_data_dir() runs at import), which would crash app startup
        # entirely instead of just resolving the wrong directory.
        real_a, real_d = ourcal.is_android, ourcal.android_data_dir
        ourcal.is_android = lambda: True

        def boom():
            raise RuntimeError("bridge not ready")

        ourcal.android_data_dir = boom
        self.addCleanup(lambda: setattr(ourcal, "is_android", real_a))
        self.addCleanup(lambda: setattr(ourcal, "android_data_dir", real_d))
        self._bundled(False)
        self.assertEqual(ourcal.data_dir(), ourcal.APP_DIR)

    def _bundled(self, yes):
        real = ourcal.is_bundled
        ourcal.is_bundled = lambda: yes
        self.addCleanup(lambda: setattr(ourcal, "is_bundled", real))


class TestBundleRoundTrip(unittest.TestCase):
    """The bundle crosses an untrusted channel — it carries live refresh
    tokens, and any convenient Mac-to-phone text route touches a cloud."""

    FILES = {"credentials.json": '{"installed": {"client_id": "abc"}}',
             "accounts.json": '[{"label": "L", "email": "l@example.com"}]',
             "token_l.json": '{"refresh_token": "1//secret"}'}

    def test_round_trips_every_file_byte_for_byte(self):
        b = ourcal.make_bundle(self.FILES, "correct horse")
        self.assertEqual(ourcal.open_bundle(b, "correct horse"), self.FILES)

    def test_bundle_is_one_pasteable_line(self):
        b = ourcal.make_bundle(self.FILES, "pw")
        self.assertTrue(b.startswith("ourcal1."))
        self.assertNotIn("\n", b)

    def test_the_plaintext_is_not_recoverable_from_the_bundle(self):
        b = ourcal.make_bundle(self.FILES, "pw")
        self.assertNotIn("1//secret", b)
        self.assertNotIn("l@example.com", b)

    def test_two_exports_of_the_same_files_differ(self):
        # Fresh salt and nonce each time; identical bundles would leak that
        # nothing changed between two exports.
        a = ourcal.make_bundle(self.FILES, "pw")
        b = ourcal.make_bundle(self.FILES, "pw")
        self.assertNotEqual(a, b)
        self.assertEqual(ourcal.open_bundle(a, "pw"),
                         ourcal.open_bundle(b, "pw"))

    def test_survives_being_pasted_with_surrounding_whitespace(self):
        b = ourcal.make_bundle(self.FILES, "pw")
        self.assertEqual(ourcal.open_bundle("\n  " + b + "  \n", "pw"),
                         self.FILES)

    def test_survives_being_hard_wrapped_by_a_mail_client(self):
        # The bundle travels through a messaging app by design, and those wrap
        # long strings. A newline every 72 chars must not read as truncation.
        b = ourcal.make_bundle(self.FILES, "pw")
        wrapped = "\n".join(b[i:i + 72] for i in range(0, len(b), 72))
        self.assertEqual(ourcal.open_bundle(wrapped, "pw"), self.FILES)


class TestBundleRejection(unittest.TestCase):
    FILES = {"credentials.json": '{"installed": {}}'}

    def test_wrong_passphrase(self):
        b = ourcal.make_bundle(self.FILES, "right")
        with self.assertRaises(ourcal.BundleError) as cm:
            ourcal.open_bundle(b, "wrong")
        self.assertIn("Wrong passphrase", str(cm.exception))

    def test_a_single_flipped_byte_is_caught_by_the_mac(self):
        import base64
        b = ourcal.make_bundle(self.FILES, "pw")
        body = b[len("ourcal1."):]
        raw = bytearray(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        raw[40] ^= 1                     # inside the ciphertext
        tampered = "ourcal1." + base64.urlsafe_b64encode(
            bytes(raw)).decode().rstrip("=")
        with self.assertRaises(ourcal.BundleError) as cm:
            ourcal.open_bundle(tampered, "pw")
        # Tampering and a wrong passphrase are deliberately indistinguishable.
        self.assertIn("Wrong passphrase", str(cm.exception))

    def test_missing_prefix(self):
        with self.assertRaises(ourcal.BundleError) as cm:
            ourcal.open_bundle("just some text", "pw")
        self.assertIn("doesn't look like an OurCal bundle", str(cm.exception))

    def test_truncated_bundle(self):
        b = ourcal.make_bundle(self.FILES, "pw")
        with self.assertRaises(ourcal.BundleError) as cm:
            ourcal.open_bundle(b[:20], "pw")
        self.assertIn("truncated", str(cm.exception))

    def test_empty_input(self):
        with self.assertRaises(ourcal.BundleError):
            ourcal.open_bundle("", "pw")

    def test_prefix_with_nothing_after_it(self):
        with self.assertRaises(ourcal.BundleError) as cm:
            ourcal.open_bundle("ourcal1.", "pw")
        self.assertIn("truncated", str(cm.exception))

    def test_a_non_dict_files_payload_is_reported_as_corrupt(self):
        # A frame with a genuinely correct MAC (built with make_bundle, the
        # module's own helper, so the HMAC check upstream of this passes)
        # whose *decrypted* payload has the wrong shape. Every other
        # corruption test is caught by the HMAC check first; this is the
        # only way to reach the value-shape check beyond it.
        b = ourcal.make_bundle(["not", "a", "dict"], "pw")
        with self.assertRaises(ourcal.BundleError) as cm:
            ourcal.open_bundle(b, "pw")
        self.assertIn("The bundle is corrupt.", str(cm.exception))

    def test_a_non_string_value_is_reported_as_corrupt(self):
        b = ourcal.make_bundle({"credentials.json": 123}, "pw")
        with self.assertRaises(ourcal.BundleError) as cm:
            ourcal.open_bundle(b, "pw")
        self.assertIn("The bundle is corrupt.", str(cm.exception))


class TestBundleWireFormat(unittest.TestCase):
    """A known-answer test, so the format cannot drift silently.

    Every other test round-trips through our own code and would stay green
    through a format change — while bundles from a different build stopped
    opening. The phone runs a sideloaded APK that does not auto-update, so a
    Mac one version ahead of the phone is the normal case.
    """

    BUNDLE = "ourcal1.gGku7vOgzDk2FEZ4T-IOtaV3R1wMn_nJb-_qX2QSP_3nnF-iTg9pzFigrc0v0eIGAwSGmccTmBOizbr4t4P_i8Dk4JGS8u-EujUGjB8SL_FyKhEgp1FrxD1rpwFzwlLC82otTh5wUbeHTnIxXOqZpaFBxahB_QdD4WnfxjFy5e8uI3bYLn0h27TZv_5TgWu-_slcn-KPnw7hre6wNB0"

    def test_opens_a_bundle_produced_by_an_earlier_build(self):
        self.assertEqual(
            ourcal.open_bundle(self.BUNDLE, "known-answer-passphrase"),
            {"credentials.json": '{"installed": {"client_id": "kat"}}'})


class TestUserFileWhitelist(unittest.TestCase):
    """A bundle is untrusted input. Names are matched against a whitelist,
    never sanitised: os.path.join with a crafted name is a traversal."""

    def test_accepts_the_two_config_files(self):
        self.assertTrue(ourcal.is_user_file("credentials.json"))
        self.assertTrue(ourcal.is_user_file("accounts.json"))

    def test_accepts_slugged_token_names(self):
        self.assertTrue(ourcal.is_user_file("token_leela.json"))
        self.assertTrue(ourcal.is_user_file("token_leela-k.json"))
        self.assertTrue(ourcal.is_user_file("token_leela-26033.json"))

    def test_rejects_traversal(self):
        for bad in ["../evil.json", "../../etc/passwd",
                    "token_a/b.json", "/etc/passwd", "token_../x.json"]:
            self.assertFalse(ourcal.is_user_file(bad), bad)

    def test_rejects_names_outside_the_whitelist(self):
        for bad in ["random.json", "TOKEN_X.json", "token_.json",
                    "token_Leela.json", "credentials.json.bak", "", "."]:
            self.assertFalse(ourcal.is_user_file(bad), bad)


class TestWriteSecretFile(_TmpData, unittest.TestCase):
    """The one writer every credential file routes through. Before this
    helper existed, creds_for's refresh write-back and _run_signin's token
    write both used a plain open(path, "w"), which on a default umask lands
    at 0644 — the same refresh token ending up world-readable or owner-only
    depending on which of three code paths wrote it last."""

    def test_writes_the_content_atomically_with_owner_only_mode(self):
        import stat
        tmp = self._tmp_data()
        path = os.path.join(tmp, "secret.json")
        ourcal.write_secret_file(path, "top secret")
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "top secret")
        mode = stat.S_IMODE(os.stat(path).st_mode)
        self.assertEqual(mode, 0o600)
        # No leftover ".tmp" file from the write-then-rename.
        self.assertEqual(os.listdir(tmp), ["secret.json"])

    def test_overwrites_existing_content_cleanly(self):
        tmp = self._tmp_data()
        path = os.path.join(tmp, "secret.json")
        ourcal.write_secret_file(path, "first")
        ourcal.write_secret_file(path, "second")
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "second")


class TestWriteUserFiles(_TmpData, unittest.TestCase):
    GOOD = {"credentials.json": '{"installed": {"client_id": "x"}}',
            "accounts.json": '[{"label": "L", "email": "l@example.com"}]',
            "token_l.json": '{"refresh_token": "s"}'}

    def test_writes_every_file(self):
        tmp = self._tmp_data()
        self.assertEqual(ourcal.write_user_files(self.GOOD),
                         ["accounts.json", "credentials.json", "token_l.json"])
        for name, body in self.GOOD.items():
            with open(os.path.join(tmp, name)) as f:
                self.assertEqual(f.read(), body)

    def test_files_are_owner_only(self):
        import stat
        tmp = self._tmp_data()
        ourcal.write_user_files(self.GOOD)
        for name in self.GOOD:
            mode = stat.S_IMODE(os.stat(os.path.join(tmp, name)).st_mode)
            self.assertEqual(mode, 0o600, name)

    def test_a_bad_name_writes_nothing_at_all(self):
        # All-or-nothing: validate everything before writing anything, so a
        # rejected bundle cannot leave a half-configured device. The bad key
        # must sort *after* every good filename (token_zz/x.json, not
        # ../evil.json) — otherwise a wrong implementation that validated and
        # wrote one file at a time would still pass this test by chance.
        tmp = self._tmp_data()
        payload = dict(self.GOOD)
        payload["token_zz/x.json"] = "{}"
        with self.assertRaises(ourcal.BundleError) as cm:
            ourcal.write_user_files(payload)
        self.assertIn("unexpected file", str(cm.exception))
        self.assertEqual(os.listdir(tmp), [])

    def test_invalid_accounts_json_writes_nothing_at_all(self):
        # Ordering weakness, known and structural: nothing whitelisted sorts
        # before "accounts.json", so this test cannot prove validate-before-
        # write the way test_a_bad_name_writes_nothing_at_all does above.
        tmp = self._tmp_data()
        payload = dict(self.GOOD)
        payload["accounts.json"] = '[{"label": "", "email": "nope"}]'
        with self.assertRaises(ourcal.BundleError) as cm:
            ourcal.write_user_files(payload)
        self.assertIn("accounts list in the bundle is invalid",
                      str(cm.exception))
        self.assertEqual(os.listdir(tmp), [])

    def test_non_json_content_writes_nothing_at_all(self):
        tmp = self._tmp_data()
        payload = dict(self.GOOD)
        payload["token_l.json"] = "not json at all"
        with self.assertRaises(ourcal.BundleError):
            ourcal.write_user_files(payload)
        self.assertEqual(os.listdir(tmp), [])

    def test_leaves_no_temp_files_behind(self):
        tmp = self._tmp_data()
        ourcal.write_user_files(self.GOOD)
        self.assertEqual(sorted(os.listdir(tmp)), sorted(self.GOOD))


class TestReloadAccounts(_TmpData, unittest.TestCase):
    """ACCOUNTS resolves at import (ourcal.py:142), so writing accounts.json
    changes nothing until it is re-read. Without this the phone keeps showing
    the placeholder Personal/Work chips after a successful import."""

    def setUp(self):
        real = ourcal.ACCOUNTS
        self.addCleanup(lambda: setattr(ourcal, "ACCOUNTS", real))

    def test_import_replaces_the_module_global(self):
        self._tmp_data()
        files = {"credentials.json": '{"installed": {}}',
                 "accounts.json": json.dumps(
                     [{"label": "Imported", "email": "i@example.com"}])}
        bundle = ourcal.make_bundle(files, "pw")
        result = ourcal.import_bundle(bundle, "pw")
        self.assertTrue(result["ok"])
        self.assertEqual(result["accounts"], 1)
        self.assertEqual([a["label"] for a in ourcal.ACCOUNTS], ["Imported"])

    def test_a_bundle_without_accounts_leaves_them_alone(self):
        self._tmp_data()
        before = list(ourcal.ACCOUNTS)
        bundle = ourcal.make_bundle({"credentials.json": '{"installed": {}}'},
                                    "pw")
        ourcal.import_bundle(bundle, "pw")
        self.assertEqual(ourcal.ACCOUNTS, before)

    def test_a_wrong_passphrase_writes_nothing(self):
        tmp = self._tmp_data()
        bundle = ourcal.make_bundle({"credentials.json": '{"installed": {}}'},
                                    "right")
        with self.assertRaises(ourcal.BundleError):
            ourcal.import_bundle(bundle, "wrong")
        self.assertEqual(os.listdir(tmp), [])


class TestCollectUserFiles(_TmpData, unittest.TestCase):
    def test_collects_only_whitelisted_names(self):
        tmp = self._tmp_data()
        for name in ["credentials.json", "accounts.json", "token_l.json",
                     "notes.txt", "token_BAD.json", ".DS_Store"]:
            with open(os.path.join(tmp, name), "w") as f:
                f.write("{}")
        self.assertEqual(sorted(ourcal.collect_user_files()),
                         ["accounts.json", "credentials.json", "token_l.json"])

    def test_empty_when_the_directory_is_missing(self):
        real = ourcal.data_dir
        ourcal.data_dir = lambda: "/nonexistent/ourcal/nowhere"
        self.addCleanup(lambda: setattr(ourcal, "data_dir", real))
        self.assertEqual(ourcal.collect_user_files(), {})


class TestExportImportRoundTrip(_TmpData, unittest.TestCase):
    """The whole point, end to end: what --export prints is what the phone
    can open."""

    def setUp(self):
        real = ourcal.ACCOUNTS
        self.addCleanup(lambda: setattr(ourcal, "ACCOUNTS", real))

    def test_a_mac_export_imports_onto_a_fresh_device(self):
        mac = self._tmp_data()
        files = {
            "credentials.json": '{"installed": {"client_id": "x"}}',
            "accounts.json": json.dumps(
                [{"label": "Leela", "email": "l@example.com"},
                 {"label": "Leela K", "email": "lk@example.com"}]),
            "token_leela.json": '{"refresh_token": "a"}',
            "token_leela-k.json": '{"refresh_token": "b"}'}
        for name, body in files.items():
            with open(os.path.join(mac, name), "w") as f:
                f.write(body)
        bundle = ourcal.make_bundle(ourcal.collect_user_files(), "pw")

        phone = self._tmp_data()          # redirect again: a different device
        result = ourcal.import_bundle(bundle, "pw")
        self.assertEqual(result["accounts"], 2)
        self.assertEqual(sorted(os.listdir(phone)), sorted(files))
        for name, body in files.items():
            with open(os.path.join(phone, name)) as f:
                self.assertEqual(f.read(), body)

    def test_a_non_ascii_account_label_survives_the_round_trip(self):
        # The bundle is a cross-machine format: a non-ASCII label must
        # survive on purpose, not merely because both Mac and Android happen
        # to default to UTF-8 — collect_user_files/write_user_files/
        # load_accounts must say encoding="utf-8" explicitly.
        #
        # The trouble: this machine's own ambient locale is *also* UTF-8, so
        # every assertion below on file *contents* would still pass even if
        # a future edit quietly dropped one of those explicit encodings and
        # fell back to the platform default — exactly the bug this test is
        # supposed to catch (verified against a real regression: reverting
        # load_accounts's encoding="utf-8" does not fail any assertion in
        # this test on this machine). A test cannot force the interpreter
        # into a non-UTF-8 locale mid-process either — open()'s default
        # encoding is resolved inside the C `_io` module straight from the
        # OS locale; patching the Python-level locale.getpreferredencoding
        # has no effect on it (checked by hand: monkeypatching it and
        # writing a non-ASCII string through a plain open() with no
        # encoding= still writes UTF-8 on this interpreter).
        #
        # So alongside the behavioral round trip, this spies on every
        # open() of the two files it touches and asserts encoding="utf-8"
        # was passed explicitly each time. That assertion is about what the
        # source code asks for, not what this machine's locale happens to
        # produce, so it stays meaningful regardless of the ambient locale
        # — and it would have caught the load_accounts miss even here.
        mac = self._tmp_data()
        files = {
            "credentials.json": '{"installed": {"client_id": "x"}}',
            "accounts.json": json.dumps(
                [{"label": "Renée家", "email": "l@example.com"}],
                ensure_ascii=False)}
        for name, body in files.items():
            with open(os.path.join(mac, name), "w", encoding="utf-8") as f:
                f.write(body)

        import builtins
        encodings_seen = []
        real_open = builtins.open

        def spy_open(file, *args, **kwargs):
            if isinstance(file, str) and os.path.basename(file) in files:
                encodings_seen.append(kwargs.get("encoding"))
            return real_open(file, *args, **kwargs)

        builtins.open = spy_open
        try:
            bundle = ourcal.make_bundle(ourcal.collect_user_files(), "pw")
            phone = self._tmp_data()      # redirect again: a different device
            result = ourcal.import_bundle(bundle, "pw")
        finally:
            builtins.open = real_open

        # collect_user_files read both files on "mac"; reload_accounts (via
        # import_bundle) read accounts.json back on "phone". Every one of
        # those open() calls must have named utf-8 explicitly — none may
        # have fallen through to the platform default (encoding=None).
        self.assertTrue(encodings_seen)
        self.assertTrue(all(enc == "utf-8" for enc in encodings_seen),
                        encodings_seen)

        self.assertEqual(result["accounts"], 1)
        self.assertEqual(ourcal.ACCOUNTS[0]["label"], "Renée家")
        with open(os.path.join(phone, "accounts.json"),
                 encoding="utf-8") as f:
            self.assertEqual(f.read(), files["accounts.json"])


class TestExportCli(_TmpData, unittest.TestCase):
    def test_refuses_without_credentials(self):
        self._tmp_data()               # empty
        self.assertEqual(ourcal.export_cli(), 1)

    def test_refuses_when_the_passphrases_differ(self):
        import getpass
        tmp = self._tmp_data()
        with open(os.path.join(tmp, "credentials.json"), "w") as f:
            f.write("{}")
        answers = iter(["correct horse battery", "correct horse battery!"])
        real = getpass.getpass
        getpass.getpass = lambda *a, **k: next(answers)
        self.addCleanup(lambda: setattr(getpass, "getpass", real))
        self.assertEqual(ourcal.export_cli(), 1)

    def test_refuses_a_short_passphrase(self):
        # The design's threat model assumes the ciphertext is obtained, at
        # which point PBKDF2 is the only defence and a weak passphrase is the
        # whole game — this bundle carries live Google refresh tokens.
        import contextlib
        import getpass
        import io
        tmp = self._tmp_data()
        with open(os.path.join(tmp, "credentials.json"), "w") as f:
            f.write("{}")
        real = getpass.getpass
        getpass.getpass = lambda *a, **k: "short"
        self.addCleanup(lambda: setattr(getpass, "getpass", real))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(ourcal.export_cli(), 1)
        self.assertIn("12", err.getvalue())
        self.assertEqual(os.listdir(tmp), ["credentials.json"])  # nothing new

    def test_prints_only_the_bundle_on_stdout(self):
        # `./ourcal.py --export | pbcopy` must pipe the bundle and nothing
        # else; warnings go to stderr and getpass prompts on the tty.
        import contextlib
        import getpass
        import io
        tmp = self._tmp_data()
        with open(os.path.join(tmp, "credentials.json"), "w") as f:
            f.write('{"installed": {}}')
        real = getpass.getpass
        getpass.getpass = lambda *a, **k: "a passphrase over 12 chars"
        self.addCleanup(lambda: setattr(getpass, "getpass", real))
        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(ourcal.export_cli(), 0)
        printed = out.getvalue().strip()
        self.assertEqual(len(printed.splitlines()), 1)
        self.assertEqual(
            ourcal.open_bundle(printed, "a passphrase over 12 chars"),
            {"credentials.json": '{"installed": {}}'})
        # The live-refresh-token warning is a specified requirement — it must
        # actually reach stderr, not just avoid stdout.
        self.assertIn("refresh tokens", err.getvalue())


class TestSetupStatus(_TmpData, unittest.TestCase):
    """The diagnostics footer. A month of Android breakage was invisible
    because nothing on the phone ever reported which directory it resolved."""

    def setUp(self):
        real = ourcal.ACCOUNTS
        self.addCleanup(lambda: setattr(ourcal, "ACCOUNTS", real))

    def test_reports_an_empty_device(self):
        tmp = self._tmp_data()
        s = ourcal.setup_status()
        self.assertEqual(s["dataDir"], tmp)
        self.assertFalse(s["android"])
        self.assertFalse(s["hasCredentials"])
        self.assertIsNone(s["credentialsSource"])
        self.assertFalse(s["accountsFromFile"])
        self.assertEqual(s["signedIn"], [])

    def test_reports_credentials_and_sign_ins(self):
        tmp = self._tmp_data()
        ourcal.ACCOUNTS = [{"label": "Leela", "email": "l@example.com"},
                           {"label": "Leela K", "email": "lk@example.com"}]
        for name in ["credentials.json", "accounts.json", "token_leela.json"]:
            with open(os.path.join(tmp, name), "w") as f:
                f.write("{}")
        s = ourcal.setup_status()
        self.assertTrue(s["hasCredentials"])
        self.assertEqual(s["credentialsSource"], "pasted")
        self.assertTrue(s["accountsFromFile"])
        self.assertEqual(s["accounts"], 2)
        self.assertEqual(s["signedIn"], ["Leela"])   # Leela K has no token

    def test_reports_bundled_when_nothing_is_pasted(self):
        # A fresh install running purely on the shipped client must not be
        # misreported as "credentials.json: missing" — that would send
        # someone debugging a working install in the wrong direction.
        self._tmp_data()
        real = ourcal.bundled_credentials
        ourcal.bundled_credentials = lambda: '{"installed": {"client_id": "B"}}'
        self.addCleanup(lambda: setattr(ourcal, "bundled_credentials", real))
        s = ourcal.setup_status()
        self.assertTrue(s["hasCredentials"])
        self.assertEqual(s["credentialsSource"], "bundled")

    def test_reports_the_android_branch(self):
        self._tmp_data()
        real = ourcal.is_android
        ourcal.is_android = lambda: True
        self.addCleanup(lambda: setattr(ourcal, "is_android", real))
        self.assertTrue(ourcal.setup_status()["android"])

    def test_bridge_is_false_off_android(self):
        # The real android_data_dir() imports com.chaquo.python, which does
        # not exist here — bridge must report that without raising.
        self._tmp_data()
        self.assertFalse(ourcal.setup_status()["bridge"])

    def test_bridge_reflects_whether_android_data_dir_actually_works(self):
        # android and bridge are independent: this is the "android branch
        # live but bridge unavailable" combination the footer must be able
        # to show, distinct from a plain desktop where android is False too.
        self._tmp_data()
        real_a, real_d = ourcal.is_android, ourcal.android_data_dir
        ourcal.is_android = lambda: True
        ourcal.android_data_dir = lambda: (_ for _ in ()).throw(
            RuntimeError("bridge not ready"))
        self.addCleanup(lambda: setattr(ourcal, "is_android", real_a))
        self.addCleanup(lambda: setattr(ourcal, "android_data_dir", real_d))
        s = ourcal.setup_status()
        self.assertTrue(s["android"])
        self.assertFalse(s["bridge"])

    def test_bridge_is_true_when_android_data_dir_succeeds(self):
        self._tmp_data()
        real_d = ourcal.android_data_dir
        ourcal.android_data_dir = lambda: "/android/files"
        self.addCleanup(lambda: setattr(ourcal, "android_data_dir", real_d))
        self.assertTrue(ourcal.setup_status()["bridge"])


class TestSetupErrorFlag(unittest.TestCase):
    """The banner must not string-match error text to decide whether to offer
    setup — a missing credentials.json is a different thing from a dead
    token, and only the first one has a way out on the phone."""

    def _events_with(self, exc):
        real = ourcal.service_for
        ourcal.service_for = lambda label, email: (_ for _ in ()).throw(exc)
        self.addCleanup(lambda: setattr(ourcal, "service_for", real))
        return ourcal.list_account_events("L", "l@example.com", "a", "b")

    def test_missing_credentials_is_flagged_as_setup(self):
        _, err = self._events_with(FileNotFoundError("credentials.json is missing"))
        self.assertTrue(err["setup"])
        self.assertIn("credentials.json is missing", err["message"])

    def test_any_other_failure_is_not_flagged_as_setup(self):
        _, err = self._events_with(RuntimeError("token revoked"))
        self.assertFalse(err["setup"])
        self.assertIn("re-auth", err["message"])

    def test_collect_carries_the_flag_through_with_the_label(self):
        real_accounts = ourcal.ACCOUNTS
        ourcal.ACCOUNTS = [{"label": "Only", "email": "o@example.com"}]
        self.addCleanup(lambda: setattr(ourcal, "ACCOUNTS", real_accounts))
        real = ourcal.list_account_events
        ourcal.list_account_events = lambda *a: (
            [], {"message": "m", "setup": True})
        self.addCleanup(lambda: setattr(ourcal, "list_account_events", real))
        _, errors = ourcal._google_collect(
            datetime.datetime(2026, 8, 7, tzinfo=datetime.timezone.utc))
        self.assertEqual(errors,
                         [{"label": "Only", "message": "m", "setup": True}])

    def test_a_wrong_signed_in_account_is_not_flagged_as_setup(self):
        # Drives the real list_account_events to the mismatch branch, which no
        # other test reaches: service_for is faked to SUCCEED and return
        # somebody else's primary calendar. Left as a bare string, this site
        # would blow up at **err in _google_collect only for a real user.
        class _Cals:
            def list(self, pageToken=None):
                return self

            def execute(self):
                return {"items": [{"id": "someone.else@example.com",
                                   "primary": True}]}

        class _Svc:
            def calendarList(self):
                return _Cals()

        real = ourcal.service_for
        ourcal.service_for = lambda label, email: _Svc()
        self.addCleanup(lambda: setattr(ourcal, "service_for", real))
        events, err = ourcal.list_account_events(
            "Leela", "leela@example.com", "a", "b")
        self.assertEqual(events, [])
        self.assertFalse(err["setup"])          # not a setup problem
        self.assertIn("someone.else@example.com", err["message"])
        self.assertIn("leela@example.com", err["message"])


class TestSetupRoutes(_TmpData, unittest.TestCase):
    """CONTRIBUTING.md:49 — never let a test reach the real data directory.
    Every test in this class posts to live routes over HTTP, and a bad bundle
    is meant to fail before any write happens — but that is an implementation
    detail, one bug away from overwriting the owner's real credentials.json,
    accounts.json and four token files. data_dir() is redirected per-test
    (setUp), not once for the class, so no test can see another test's
    leftover files and no ordering assumption is load-bearing."""

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
        self.tmp = self._tmp_data()
        real_accounts = ourcal.ACCOUNTS
        self.addCleanup(lambda: setattr(ourcal, "ACCOUNTS", real_accounts))

    def _get(self, path):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            headers={"Content-Type": "application/json",
                     "X-OurCal-Token": ourcal.SESSION_TOKEN})
        with urllib.request.urlopen(req) as r:
            return r.status, r.read().decode()

    def _post(self, path, obj):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(obj).encode(), method="POST",
            headers={"Content-Type": "application/json",
                     "X-OurCal-Token": ourcal.SESSION_TOKEN})
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read().decode())

    def test_setup_page_is_served(self):
        # /setup requires ?k=, not the X-OurCal-Token header _get sends.
        status, body = self._get(f"/setup?k={ourcal.SESSION_TOKEN}")
        self.assertEqual(status, 200)
        self.assertIn("Set up this device", body)
        self.assertIn("/api/import", body)

    def test_status_endpoint_shape(self):
        # data_dir() is redirected to a fresh temp dir per test (setUp), so
        # this no longer reads the real device — assert key names and types,
        # not specific paths or counts that would depend on that real state.
        _, body = self._get("/api/status")
        s = json.loads(body)
        self.assertEqual(sorted(s), ["accountLabels", "accounts",
                                     "accountsFromFile", "android", "bridge",
                                     "credentialsSource", "dataDir",
                                     "hasCredentials", "signedIn"])
        self.assertIsInstance(s["dataDir"], str)
        self.assertIsInstance(s["android"], bool)
        self.assertIsInstance(s["bridge"], bool)
        self.assertIsInstance(s["hasCredentials"], bool)
        self.assertIn(s["credentialsSource"], (None, "pasted", "bundled"))
        self.assertIsInstance(s["accounts"], int)
        self.assertIsInstance(s["accountsFromFile"], bool)
        self.assertIsInstance(s["accountLabels"], list)
        self.assertIsInstance(s["signedIn"], list)

    def test_import_reports_a_bad_bundle_without_a_500(self):
        status, body = self._post("/api/import",
                                  {"bundle": "nonsense", "passphrase": "x"})
        self.assertEqual(status, 200)
        self.assertFalse(body["ok"])
        self.assertIn("doesn't look like an OurCal bundle", body["error"])
        self.assertEqual(os.listdir(self.tmp), [])

    def test_import_reports_a_wrong_passphrase(self):
        bundle = ourcal.make_bundle({"credentials.json": "{}"}, "right")
        _, body = self._post("/api/import",
                             {"bundle": bundle, "passphrase": "wrong"})
        self.assertFalse(body["ok"])
        self.assertIn("Wrong passphrase", body["error"])
        self.assertEqual(os.listdir(self.tmp), [])

    def test_import_writes_a_real_bundle(self):
        bundle = ourcal.make_bundle(
            {"credentials.json": '{"installed": {}}',
             "accounts.json": json.dumps(
                 [{"label": "Phone", "email": "p@example.com"}])}, "pw")
        _, body = self._post("/api/import",
                             {"bundle": bundle, "passphrase": "pw"})
        self.assertTrue(body["ok"])
        self.assertEqual(body["written"],
                         ["accounts.json", "credentials.json"])
        self.assertEqual(body["accounts"], 1)
        self.assertEqual(sorted(os.listdir(self.tmp)),
                         ["accounts.json", "credentials.json"])


class TestSetupPageStructure(unittest.TestCase):
    def test_page_has_the_markers_it_needs(self):
        for marker in ['id="bundle"', 'id="passphrase"', 'id="doImport"',
                       'id="result"', 'id="diag"', "/api/import",
                       "/api/status", "--export", "prefers-color-scheme"]:
            self.assertIn(marker, ourcal.SETUP_PAGE, f"missing {marker!r}")

    def test_page_reports_the_resolved_data_dir(self):
        # The diagnostic that would have made the seam bug obvious.
        self.assertIn("dataDir", ourcal.SETUP_PAGE)
        self.assertIn("android", ourcal.SETUP_PAGE)

    def test_page_distinguishes_a_dead_bridge_from_an_inactive_branch(self):
        # "android branch live" and "android branch live but the bridge is
        # unavailable" are different diagnoses — the footer must be able to
        # tell them apart, not just report the android flag.
        self.assertIn("s.bridge", ourcal.SETUP_PAGE)
        self.assertIn("bridge is", ourcal.SETUP_PAGE.lower())

    def test_no_internal_link_is_missing_the_session_key(self):
        # Same requirement as PAGE's: an internal link with no ?k= is a 403
        # the user cannot recover from without knowing to retype the URL.
        for href in re.findall(r'href=\\?"(/[^"\\]*)', ourcal.SETUP_PAGE):
            if href.startswith("/api/"):
                continue
            self.assertIn("k=__SESSION_TOKEN__", href, href)

    def test_accounts_list_does_not_show_placeholders_before_import(self):
        # Personal/you@example.com and Work/you@work.example.com are
        # placeholders until accounts.json exists. Listing them as if real —
        # with working Remove/Sign in buttons — made a stranger's first "Add"
        # of an account named "Work" (the design's own mock-up example)
        # collide with a placeholder of the same name they never added.
        self.assertIn("accountsFromFile", ourcal.SETUP_PAGE)
        self.assertIn("No accounts yet", ourcal.SETUP_PAGE)

    def test_sign_in_is_always_offered_not_only_before_first_success(self):
        # A revoked grant or dead token leaves the token file present, so
        # signedIn stays true — the button must not disappear once it's ever
        # been true, or a dead account has no way back.
        self.assertNotIn('(on ? "" :', ourcal.SETUP_PAGE)
        self.assertIn("Sign in again", ourcal.SETUP_PAGE)


class TestSessionToken(_TmpData, unittest.TestCase):
    """The hole _local_caller could not close.

    Host and Origin only constrain browsers. A native process sends no
    Origin and is accepted — on Android, where loopback is not isolated
    between apps, that let any installed app POST credentials into this
    app's data directory. A token minted per run closes that gap — but only
    if a caller with no key cannot get one by fetching an unguarded page and
    reading it out of the HTML; that composition is what
    test_a_caller_without_the_key_cannot_bootstrap_a_token exists to prove,
    not just that /api/* is gated and pages carry the token.
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

    def test_navigations_require_the_key_in_the_url(self):
        # Renamed from test_navigations_do_not_need_the_token, which asserted
        # the false half of the bug this class exists to catch: navigations
        # DO need the token now, carried as ?k= rather than a header, because
        # a header is not something a page load can attach to itself. This is
        # how the token reaches the page in the first place.
        for path in ("/", "/setup"):
            status, _ = self._req(f"{path}?k={ourcal.SESSION_TOKEN}")
            self.assertEqual(status, 200, path)

    def test_a_caller_without_the_key_cannot_bootstrap_a_token(self):
        # The hole this token exists to close. A native caller passes the
        # Host/Origin checks by construction, so if it can fetch a page it can
        # read the token out of the HTML and replay it. Both halves of that
        # were separately asserted as correct; nothing asserted the
        # composition. It is the same shape as the is_android() bug: a green
        # test proving one side of a property, silent about the side that
        # matters.
        status, body = self._req("/")
        self.assertEqual(status, 403)
        self.assertNotIn(ourcal.SESSION_TOKEN, body)
        status, body = self._req("/setup")
        self.assertEqual(status, 403)
        self.assertNotIn(ourcal.SESSION_TOKEN, body)

    def test_both_pages_carry_the_real_token_not_the_placeholder(self):
        for path in ("/", "/setup"):
            _, body = self._req(f"{path}?k={ourcal.SESSION_TOKEN}")
            self.assertIn(ourcal.SESSION_TOKEN, body, path)
            self.assertNotIn("__SESSION_TOKEN__", body, path)

    def test_the_token_is_not_trivially_guessable(self):
        self.assertGreaterEqual(len(ourcal.SESSION_TOKEN), 32)
        self.assertNotIn(ourcal.SESSION_TOKEN, ("", "None", "token"))

    def test_the_navigation_exemption_is_an_allowlist_not_a_prefix_check(self):
        # _api_token_ok exempts exactly "/" and "/setup" (query string aside)
        # when the ?k= key matches, not "anything that doesn't start with
        # /api/". A path that merely avoids the /api/ prefix without being
        # one of the two real navigations must still require the header
        # token — the query key does nothing for it.
        for path in ("/api", "/setup/../api/events"):
            status, _ = self._req(f"{path}?k={ourcal.SESSION_TOKEN}")
            self.assertEqual(status, 403, path)
        for path in ("/", "/setup"):
            status, _ = self._req(f"{path}?k={ourcal.SESSION_TOKEN}")
            self.assertEqual(status, 200, path)


class TestBundledCredentials(_TmpData, unittest.TestCase):
    """The APK ships an OAuth client so a fresh install can sign in with
    no computer. A pasted client must still win, so bring-your-own keeps
    working for anyone who prefers their own Google Cloud project."""

    def test_none_when_nothing_is_bundled(self):
        self._tmp_data()
        self.assertIsNone(ourcal.bundled_credentials())

    def test_reads_a_bundled_client_from_beside_the_module(self):
        # The desktop path: pkgutil finds nothing for a top-level module, so
        # the plain-path fallback is what answers.
        import shutil
        import tempfile
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        os.makedirs(os.path.join(tmp, "resources"))
        with open(os.path.join(tmp, ourcal.BUNDLED_CLIENT), "w",
                  encoding="utf-8") as f:
            f.write('{"installed": {"client_id": "FROM-DISK"}}')
        real = ourcal.APP_DIR
        ourcal.APP_DIR = tmp
        self.addCleanup(lambda: setattr(ourcal, "APP_DIR", real))
        self.assertIn("FROM-DISK", ourcal.bundled_credentials())

    def test_reads_a_bundled_client_through_the_package_loader(self):
        # The Android path: Chaquopy serves app files through its loader, so
        # pkgutil.get_data answers and the plain path is never reached. tz()
        # relies on the same mechanism for tzdata.
        import pkgutil
        real = pkgutil.get_data
        pkgutil.get_data = lambda pkg, res: (
            b'{"installed": {"client_id": "FROM-LOADER"}}'
            if res == ourcal.BUNDLED_CLIENT else None)
        self.addCleanup(lambda: setattr(pkgutil, "get_data", real))
        self.assertIn("FROM-LOADER", ourcal.bundled_credentials())

    def test_a_corrupt_bundled_file_is_treated_as_absent(self):
        # UnicodeDecodeError is a ValueError, not an OSError — a corrupt
        # bundled file must fall through to None, not a raw traceback that
        # would surface through client_config_text() and creds_for().
        import shutil
        import tempfile
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        os.makedirs(os.path.join(tmp, "resources"))
        with open(os.path.join(tmp, ourcal.BUNDLED_CLIENT), "wb") as f:
            f.write(b"\xff\xfe\x00bad-utf8")
        real = ourcal.APP_DIR
        ourcal.APP_DIR = tmp
        self.addCleanup(lambda: setattr(ourcal, "APP_DIR", real))
        self.assertIsNone(ourcal.bundled_credentials())

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


class TestFirstRunAddAccount(_TmpData, unittest.TestCase):
    """A fresh install has no accounts.json, so ACCOUNTS holds the
    Personal/you@example.com and Work/you@work.example.com placeholders. Before
    this fix, add_account seeded `proposed` from ACCOUNTS, so a stranger's
    first action — adding "Work", the exact label the design's own mock-up
    uses — collided with the placeholder of the same name and failed with
    "That account is invalid or already added", an account they never added.
    A non-colliding label would have been worse: it would have materialised
    both placeholders into a real accounts.json.
    """

    def setUp(self):
        self.tmp = self._tmp_data()   # no accounts.json written here
        real = ourcal.ACCOUNTS
        self.addCleanup(lambda: setattr(ourcal, "ACCOUNTS", real))
        # Exactly what a fresh install's ACCOUNTS holds: the module-level
        # placeholders, resolved because no accounts.json exists yet.
        ourcal.ACCOUNTS = [
            {"label": "Personal", "email": "you@example.com"},
            {"label": "Work", "email": "you@work.example.com"},
        ]

    def test_adding_work_on_a_fresh_install_succeeds(self):
        r = ourcal.add_account("Work", "me@gmail.com")
        self.assertTrue(r["ok"], r)

    def test_the_first_real_account_replaces_the_placeholders(self):
        ourcal.add_account("Work", "me@gmail.com")
        self.assertEqual(
            [a["label"] for a in ourcal.ACCOUNTS], ["Work"])
        with open(os.path.join(self.tmp, "accounts.json"),
                  encoding="utf-8") as f:
            written = json.load(f)
        self.assertEqual(written, [{"label": "Work", "email": "me@gmail.com"}])


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

    def test_a_timed_out_flow_gets_the_spec_message(self):
        # An abandoned browser tab makes run_local_server raise a timeout
        # after 300s (fix for the desktop branch waiting forever otherwise);
        # the raw library text is not something to show on a phone screen.
        self._fake_flow(boom=TimeoutError("Authorization flow timed out"))
        ourcal.start_signin("One")
        for _ in range(50):
            if ourcal.signin_status()["state"] != "waiting":
                break
            time.sleep(0.05)
        s = ourcal.signin_status()
        self.assertEqual(s["state"], "error")
        self.assertEqual(s["message"],
                          "Timed out waiting for Google — tap Sign in again.")

    def test_a_dns_failure_gets_the_spec_message(self):
        self._fake_flow(boom=OSError("[Errno -2] Name or service not known"))
        ourcal.start_signin("One")
        for _ in range(50):
            if ourcal.signin_status()["state"] != "waiting":
                break
            time.sleep(0.05)
        s = ourcal.signin_status()
        self.assertEqual(s["state"], "error")
        self.assertEqual(s["message"],
                          "Couldn't reach Google — check your connection "
                          "and try again.")


class TestSignInAccountCheck(_TmpData, unittest.TestCase):
    """The consent screen shows only the address, and it looks identical for
    every account — picking the wrong one is easy. Before this check,
    _run_signin wrote whatever token came back regardless of who it
    belonged to, filing a stranger's token under this label and reporting
    it as success.

    google_auth_oauthlib and googleapiclient are optional runtime deps not
    installed where the suite runs (see CONTRIBUTING.md — CI runs tests
    before either is installed), so both are stubbed via sys.modules
    rather than required. run_oauth_flow is monkeypatched, same as the
    existing off-android test for it.
    """

    def setUp(self):
        self.tmp = self._tmp_data()
        with open(os.path.join(self.tmp, "credentials.json"), "w",
                  encoding="utf-8") as f:
            f.write('{"installed": {"client_id": "cid"}}')

        import sys
        import types

        class _FakeInstalledAppFlow:
            @classmethod
            def from_client_config(cls, config, scopes):
                return cls()

        flow_mod = types.ModuleType("google_auth_oauthlib.flow")
        flow_mod.InstalledAppFlow = _FakeInstalledAppFlow
        oauthlib_pkg = types.ModuleType("google_auth_oauthlib")
        disc_mod = types.ModuleType("googleapiclient.discovery")
        apiclient_pkg = types.ModuleType("googleapiclient")
        stubs = {
            "google_auth_oauthlib": oauthlib_pkg,
            "google_auth_oauthlib.flow": flow_mod,
            "googleapiclient": apiclient_pkg,
            "googleapiclient.discovery": disc_mod,
        }
        originals = {name: sys.modules.get(name) for name in stubs}
        sys.modules.update(stubs)

        def _restore():
            for name, mod in originals.items():
                if mod is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = mod
        self.addCleanup(_restore)
        self.disc_mod = disc_mod

        class _Creds:
            def to_json(self):
                return '{"token": "tok"}'
        real_flow = ourcal.run_oauth_flow
        ourcal.run_oauth_flow = lambda flow: _Creds()
        self.addCleanup(lambda: setattr(ourcal, "run_oauth_flow", real_flow))

    def _serving(self, primary_email):
        # pageToken=None accepted (and ignored, one page): the pagination fix
        # calls .list(pageToken=page_token) on every iteration, matching
        # list_account_events's identical loop.
        class _Cals:
            def list(self, pageToken=None):
                return self

            def execute(self):
                return {"items": [{"id": primary_email, "primary": True}]}

        class _Svc:
            def calendarList(self):
                return _Cals()
        self.disc_mod.build = lambda *a, **kw: _Svc()

    def _serving_no_primary(self):
        class _Cals:
            def list(self, pageToken=None):
                return self

            def execute(self):
                return {"items": [{"id": "team@group.calendar.google.com",
                                   "selected": True}]}

        class _Svc:
            def calendarList(self):
                return _Cals()
        self.disc_mod.build = lambda *a, **kw: _Svc()

    def test_a_mismatched_account_is_refused_and_writes_no_token(self):
        self._serving("someone.else@example.com")
        with self.assertRaises(RuntimeError) as cm:
            ourcal._run_signin("One", "one@example.com")
        self.assertIn("someone.else@example.com", str(cm.exception))
        self.assertIn("one@example.com", str(cm.exception))
        self.assertFalse(os.path.exists(ourcal.token_path("One")))

    def test_a_matching_account_writes_the_token(self):
        self._serving("one@example.com")
        ourcal._run_signin("One", "one@example.com")
        self.assertTrue(os.path.exists(ourcal.token_path("One")))

    def test_the_written_token_is_owner_only(self):
        # FIX 3: _run_signin used a plain open(path, "w"), landing at 0644 on
        # a default umask — the same secret write_user_files always made
        # 0600. Both writers must agree now that both route through
        # write_secret_file.
        import stat
        self._serving("one@example.com")
        ourcal._run_signin("One", "one@example.com")
        mode = stat.S_IMODE(os.stat(ourcal.token_path("One")).st_mode)
        self.assertEqual(mode, 0o600)

    def test_a_primary_calendar_on_a_later_page_is_still_checked(self):
        # The bug: calendarList().list() was unpaginated, so an account whose
        # primary calendar fell on page 2 made primary_email() return "" and
        # silently skipped the mismatch check below — the exact check this
        # whole flow exists to run. Fails open, not closed. This fakes a
        # two-page response where the mismatch is only visible on page 2, and
        # proves both pages actually get walked.
        calls = []

        class _Cals:
            def list(self, pageToken=None):
                calls.append(pageToken)
                return self

            def execute(self):
                if len(calls) == 1:
                    return {"items": [{"id": "other@x.com", "selected": True}],
                            "nextPageToken": "p2"}
                return {"items": [{"id": "someone.else@example.com",
                                   "primary": True}]}

        class _Svc:
            def calendarList(self):
                return _Cals()
        self.disc_mod.build = lambda *a, **kw: _Svc()

        with self.assertRaises(RuntimeError) as cm:
            ourcal._run_signin("One", "one@example.com")
        self.assertIn("someone.else@example.com", str(cm.exception))
        self.assertEqual(calls, [None, "p2"])   # both pages were walked
        self.assertFalse(os.path.exists(ourcal.token_path("One")))

    def test_writes_the_token_when_the_primary_calendar_is_undeterminable(self):
        # Deliberate, documenting the same fallback account_mismatch uses
        # elsewhere (see TestAccountIdentity.test_no_error_when_primary_
        # undeterminable): if no primary calendar is ever returned at all,
        # the account cannot be identified, and the check does not block the
        # user on a guess. This is NOT the page-2 bug above — full pagination
        # ran and genuinely found no primary calendar anywhere.
        self._serving_no_primary()
        ourcal._run_signin("One", "one@example.com")
        self.assertTrue(os.path.exists(ourcal.token_path("One")))


class TestNeedsSignIn(_TmpData, unittest.TestCase):
    """Loading the agenda must never open a browser. It did, four times,
    sequentially, inside one HTTP request.

    google-auth is an optional runtime dep not installed where the suite
    runs (CONTRIBUTING.md; CI installs neither it nor google-auth-oauthlib
    before testing), so its two modules that creds_for imports at its top —
    google.oauth2.credentials and google.auth.transport.requests — are
    stubbed via sys.modules, same as TestSignInAccountCheck does for the
    modules _run_signin imports. The account here has no token file, so
    neither stand-in's attribute is ever called; it only needs to exist.
    """

    def setUp(self):
        self.tmp = self._tmp_data()
        with open(os.path.join(self.tmp, "credentials.json"), "w",
                  encoding="utf-8") as f:
            f.write('{"installed": {"client_id": "x"}}')

        import sys
        import types

        google_pkg = types.ModuleType("google")
        oauth2_pkg = types.ModuleType("google.oauth2")
        credentials_mod = types.ModuleType("google.oauth2.credentials")
        credentials_mod.Credentials = type("Credentials", (), {})
        auth_pkg = types.ModuleType("google.auth")
        transport_pkg = types.ModuleType("google.auth.transport")
        requests_mod = types.ModuleType("google.auth.transport.requests")
        requests_mod.Request = type("Request", (), {})
        stubs = {
            "google": google_pkg,
            "google.oauth2": oauth2_pkg,
            "google.oauth2.credentials": credentials_mod,
            "google.auth": auth_pkg,
            "google.auth.transport": transport_pkg,
            "google.auth.transport.requests": requests_mod,
        }
        originals = {name: sys.modules.get(name) for name in stubs}
        sys.modules.update(stubs)

        def _restore():
            for name, mod in originals.items():
                if mod is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = mod
        self.addCleanup(_restore)

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


if __name__ == "__main__":
    unittest.main()
