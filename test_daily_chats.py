"""Run with: python3 -m unittest -v test_daily_chats"""
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import time
import unittest
from datetime import date, datetime, timedelta

from chatview import ChatDB, Contact, ident_key
from daily_chats import build_chart_data, count_daily_messages, render, window_start
from make_sample_db import CHAT_SCHEMA, Gen, apple_ns


class DailyCountsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "chat.db"
        self.con = sqlite3.connect(self.path)
        self.addCleanup(self.con.close)
        self.con.executescript(CHAT_SCHEMA)
        self.gen = Gen(self.con)
        self.alice = self.gen.handle("+15552010001")
        self.alice_email = self.gen.handle("alice@example.com")
        self.bob = self.gen.handle("+15552010002")
        self.carol = self.gen.handle("+15552010003")
        alice = Contact("Alice", ["Alice"])
        bob = Contact("Bob", ["Bob"])
        carol = Contact("Carol", ["Carol"])
        self.contacts = {
            ident_key("+15552010001"): alice,
            ident_key("alice@example.com"): alice,
            ident_key("+15552010002"): bob,
            ident_key("+15552010003"): carol,
        }
        self.direct = self.gen.chat(45, "alice-phone", [self.alice])
        self.email = self.gen.chat(45, "alice-email", [self.alice_email])
        # Both handles belong to Alice; shared outgoing messages count once per person.
        self.group = self.gen.chat(43, "group-one", [self.alice, self.alice_email, self.bob],
                                   display_name="Friends")
        self.other_group = self.gen.chat(43, "group-two", [self.bob, self.carol],
                                         display_name="Friends")
        self.day = date(2026, 10, 27)

    def msg(self, chat, who, second=0, day=None, **kwargs):
        when = datetime.combine(day or self.day, datetime.min.time()) + timedelta(seconds=second)
        self.gen.msg(chat, apple_ns(when), handle=who, from_me=int(who == 0),
                     text="fixture message", **kwargs)

    def count(self, mode=None, start=None, end=None):
        self.con.commit()
        db = ChatDB(str(self.path), self.contacts, "Me")
        self.addCleanup(db.con.close)
        return count_daily_messages(db, mode, start, end)

    @staticmethod
    def totals(people):
        return {p["name"]: [sum(v[i] for v in p["daily"].values()) for i in (0, 1)]
                for p in people.values()}

    def test_direct_chats_merge_handles_and_ignore_reactions_edits_events(self):
        self.msg(self.direct, 0)
        self.msg(self.direct, self.alice, 1)
        self.msg(self.email, self.alice_email, 2)
        self.msg(self.direct, 0, 3, associated_message_type=2001)
        self.msg(self.direct, self.alice, 4, associated_message_type=100)
        self.msg(self.direct, 0, 5, item_type=1)
        self.msg(self.group, self.bob, 6)
        people = self.count()
        self.assertEqual(self.totals(people), {"Alice": [1, 2]})
        self.assertEqual(next(iter(people.values()))["daily"], {self.day: [1, 2]})

    def test_normal_attributes_sent_to_unique_members_and_received_to_sender(self):
        for second, who in enumerate([0, self.alice, self.alice_email, self.bob, 0]):
            self.msg(self.group, who, second)
        self.assertEqual(self.totals(self.count("normal")),
                         {"Alice": [2, 2], "Bob": [2, 1]})

    def test_conservative_counts_turn_changes_within_window(self):
        self.msg(self.group, self.alice, 86399, self.day - timedelta(days=1))
        for second, who in enumerate([0, self.alice, self.alice, 0, self.bob,
                                      self.bob, 0, 0, self.alice, self.alice_email]):
            self.msg(self.group, who, second * 10)
        self.msg(self.group, self.bob, 25, item_type=1)
        self.msg(self.group, 0, 45, associated_message_type=2000)
        self.assertEqual(self.totals(self.count("conservative", self.day, self.day)),
                         {"Alice": [1, 2], "Bob": [1, 1]})

    def test_separated_keeps_each_group_separate_and_counts_once(self):
        self.msg(self.direct, self.alice)
        for second, who in enumerate([0, self.alice, self.bob]):
            self.msg(self.group, who, second)
        self.msg(self.other_group, self.carol)
        people = self.count("separated")
        groups = [p for p in people.values() if p["kind"] == "group"]
        self.assertEqual(len(groups), 2)
        self.assertEqual([p["name"] for p in groups], ["Friends", "Friends"])
        self.assertCountEqual([p["daily"][self.day] for p in groups], [[1, 2], [0, 1]])
        self.assertEqual(sum(sum(v) for p in people.values() for v in p["daily"].values()), 5)

    def test_unnamed_group_uses_member_names(self):
        self.con.execute("UPDATE chat SET display_name = NULL WHERE ROWID = ?", (self.group,))
        self.msg(self.group, 0)
        group = next(iter(self.count("separated").values()))
        self.assertEqual(group["kind"], "group")
        self.assertIn("Alice", group["name"])
        self.assertIn("Bob", group["name"])

    def test_legacy_seconds_and_modern_nanoseconds_share_calendar_window(self):
        self.msg(self.direct, self.alice, 60)
        legacy = apple_ns(datetime.combine(self.day, datetime.min.time())) // 10**9
        self.gen.msg(self.direct, legacy, from_me=1, text="legacy timestamp")
        self.gen.msg(self.direct, legacy - 86400, from_me=1, text="outside window")
        person = next(iter(self.count(start=self.day, end=self.day).values()))
        self.assertEqual(person["daily"], {self.day: [1, 1]})

    @unittest.skipUnless(hasattr(time, "tzset"), "requires local timezone switching")
    def test_local_calendar_boundaries_across_daylight_saving_change(self):
        previous = os.environ.get("TZ")
        try:
            os.environ["TZ"] = "America/New_York"
            time.tzset()
            start, end = date(2026, 3, 8), date(2026, 3, 9)
            self.msg(self.direct, self.alice, 86399, start - timedelta(days=1))
            self.msg(self.direct, self.alice, 0, start)
            self.msg(self.direct, 0, 86399, end)
            self.msg(self.direct, self.alice, 0, end + timedelta(days=1))
            person = next(iter(self.count(start=start, end=end).values()))
            self.assertEqual(person["daily"], {start: [0, 1], end: [1, 0]})
        finally:
            if previous is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = previous
            time.tzset()


class ChartDataTests(unittest.TestCase):
    today = date(2026, 10, 28)

    @staticmethod
    def person(name, daily, kind="person"):
        return {"name": name, "kind": kind, "daily": daily}

    def test_default_window_includes_today_and_empty_days(self):
        data = build_chart_data({}, today=self.today)
        self.assertEqual(data["days"], 31)
        self.assertEqual(data["minMessages"], 5)
        self.assertIsNone(data["gcMode"])
        self.assertEqual(len(data["dates"]), 31)
        self.assertEqual(data["dates"][0], "2026-09-28")
        self.assertEqual(data["dates"][-1], "2026-10-28")
        self.assertEqual(data["series"], [])
        self.assertEqual(window_start(1, self.today), self.today)
        self.assertEqual(window_start(31, self.today), date(2026, 9, 28))
        self.assertIsNone(window_start(-1, self.today))

    def test_no_limit_starts_at_first_message_and_includes_gaps(self):
        people = {("p", 1): self.person("Alice", {self.today - timedelta(days=2): [2, 3]})}
        data = build_chart_data(people, days=-1, today=self.today)
        self.assertEqual(data["dates"], ["2026-10-26", "2026-10-27", "2026-10-28"])
        self.assertEqual(data["series"][0]["sent"], [2, 0, 0])
        self.assertEqual(data["series"][0]["received"], [3, 0, 0])
        self.assertEqual(build_chart_data({}, days=-1, today=self.today)["dates"], [])

    def test_threshold_is_combined_daily_peak_and_other_preserves_totals(self):
        yesterday = self.today - timedelta(days=1)
        people = {
            ("p", 1): self.person("Alice", {yesterday: [2, 3]}),
            ("p", 2): self.person("Bob", {yesterday: [0, 3], self.today: [3, 1]}),
            ("g", 1): self.person("Quiet group", {self.today: [1, 1]}, "group"),
        }
        data = build_chart_data(people, days=2, today=self.today)
        series = {s["name"]: s for s in data["series"]}
        self.assertEqual(set(series), {"Alice", "Other"})
        self.assertEqual(series["Other"]["kind"], "other")
        self.assertEqual(series["Other"]["sent"], [0, 4])
        self.assertEqual(series["Other"]["received"], [3, 2])
        self.assertEqual(sum(sum(s["sent"]) + sum(s["received"]) for s in series.values()), 14)
        unbucketed = build_chart_data(people, days=2, min_messages=0, today=self.today)
        self.assertEqual({s["name"] for s in unbucketed["series"]}, {"Alice", "Bob", "Quiet group"})

    def test_real_person_named_other_does_not_merge_with_other_bucket(self):
        people = {
            ("p", 1): self.person("Other", {self.today: [3, 2]}),
            ("p", 2): self.person("Bob", {self.today: [0, 1]}),
        }
        series = build_chart_data(people, days=1, today=self.today)["series"]
        self.assertEqual(len(series), 2)
        self.assertEqual({s["kind"] for s in series}, {"person", "other"})
        self.assertEqual(len({s["id"] for s in series}), 2)

    def test_large_day_outside_window_does_not_qualify_person(self):
        people = {("p", 1): self.person("Alice", {
            self.today - timedelta(days=1): [20, 20], self.today: [1, 0],
        })}
        data = build_chart_data(people, days=1, today=self.today)
        self.assertEqual(data["series"][0]["kind"], "other")
        self.assertEqual(data["series"][0]["sent"], [1])
        self.assertEqual(data["series"][0]["received"], [0])

    def test_render_embeds_contact_names_as_data_without_script_injection(self):
        name = '</script><script>globalThis.PWNED=1</script> & " < >'
        people = {("p", 1): self.person(name, {self.today: [3, 2]})}
        data = build_chart_data(people, days=1, today=self.today)
        page = render(data)
        self.assertNotIn(name, page)
        match = re.search(r'<script\b[^>]*\bid="chart-data"[^>]*>(.*?)</script>', page, re.S)
        self.assertIsNotNone(match)
        decoded = json.loads(match.group(1))
        self.assertEqual(decoded["series"][0]["name"], name)
        self.assertNotIn("__CHART_DATA__", page)


if __name__ == "__main__":
    unittest.main()
