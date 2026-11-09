#!/usr/bin/env python3
"""daily_chats — open a stacked bar chart of daily sent and received messages.

    python3 daily_chats.py [--days 31] [--min-messages 5]
                          [--gc [normal|conservative|separated]]
                          [--db PATH] [--contacts PATH ...] [--out DIR] [--no-open]

The window includes today and the preceding N-1 local calendar days. Use --days -1
for all history. A person needs at least --min-messages sent + received messages on
one day in the window to get their own color; everyone else is combined as Other.

Without --gc, only 1:1 chats count. --gc and --gc conservative behave like
top_chats.py. --gc separated counts each group once as its own series (subject to
the same daily minimum). Hover for counts; double-click to highlight a series.
The generated HTML is self-contained and works offline.
"""
import argparse
import glob
import json
import os
import sqlite3
import sys
import webbrowser
from datetime import date, datetime, time, timedelta
from pathlib import Path

from chatview import (ADDRESSBOOK_GLOBS, APPLE_EPOCH_NS, LIVE_DB, NS, OUT_DIR,
                      SCRIPT_DIR, ChatDB, load_contacts, to_unix_ns)


def window_start(days, today):
    """First of N calendar days including today, or None for unlimited history."""
    if days == -1:
        return None
    if days < 1:
        raise ValueError("--days must be a positive integer or -1 for all history")
    return date.fromordinal(max(1, today.toordinal() - days + 1))


def count_daily_messages(db, gc_mode=None, start_date=None, end_date=None):
    """Aggregate each person/group into local-date -> [sent, received] counts.

    Match top_chats' normal/conservative attribution and message exclusions.
    Conservative adjacency is measured among countable messages in the window,
    ordered by timestamp and ROWID, and is preserved across midnight.
    """
    if gc_mode not in (None, "normal", "conservative", "separated"):
        raise ValueError("unknown group-chat mode")
    chats = {r["ROWID"]: {"style": r["style"], "name": r["display_name"], "members": []}
             for r in db.con.execute("SELECT ROWID, style, display_name FROM chat")}
    for cid, handle in db.con.execute("SELECT chat_id, handle_id FROM chat_handle_join"):
        if cid in chats:
            chats[cid]["members"].append(handle)
    for ch in chats.values():
        ch["group"] = ch["style"] == 43 or len(ch["members"]) > 1
        ch["others"] = {db.person_of(h): h for h in ch["members"]}
        if ch["group"]:
            names = sorted({db.name_of(h, 0) for h in ch["members"]})
            ch["label"] = ch["name"] or "Group: " + (", ".join(names) or "Unknown members")

    # Support both modern nanoseconds and older seconds since Apple's epoch.
    # Separate ranges allow SQLite to use its date index for a bounded window.
    ranges, params = [], []
    for scale in (1, NS):
        clause = ["m.date >= 100000000000" if scale == 1 else "m.date < 100000000000"]
        if start_date is not None:
            since = max(0, int(datetime.combine(start_date, time.min).timestamp()) * NS - APPLE_EPOCH_NS)
            clause.append("m.date >= ?")
            params.append(since // scale)
        if end_date is not None and end_date < date.max:
            until = int(datetime.combine(end_date + timedelta(days=1), time.min).timestamp()) * NS - APPLE_EPOCH_NS
            clause.append("m.date < ?")
            params.append(until // scale)
        ranges.append("(" + " AND ".join(clause) + ")")
    rows = db.con.execute(
        "SELECT cmj.chat_id, m.handle_id, m.is_from_me, m.date "
        "FROM chat_message_join cmj JOIN message m ON m.ROWID = cmj.message_id "
        "WHERE COALESCE(m.associated_message_type, 0) = 0 AND COALESCE(m.item_type, 0) = 0 "
        "AND m.date > 0 AND (" + " OR ".join(ranges) + ") "
        "ORDER BY cmj.chat_id, CASE WHEN m.date < 100000000000 THEN m.date * 1000000000 "
        "ELSE m.date END, m.ROWID", params)

    people = {}

    def add(key, name, kind, day, is_me):
        person = people.setdefault(key, {"name": name, "kind": kind, "daily": {}})
        counts = person["daily"].setdefault(day, [0, 0])
        counts[0 if is_me else 1] += 1

    previous_chat, previous_sender = None, None
    me_key = ("me",)
    for cid, handle, from_me, timestamp in rows:
        if cid != previous_chat:
            previous_chat, previous_sender = cid, None
        ch = chats.get(cid)
        if ch is None or (ch["group"] and gc_mode is None):
            continue
        day = datetime.fromtimestamp(to_unix_ns(timestamp) // NS).date()
        is_me = bool(from_me) or not handle
        sender = me_key if is_me else db.person_of(handle)
        if not ch["group"]:
            if ch["members"]:
                member = ch["members"][0]
                add(db.person_of(member), db.name_of(member, 0), "person", day, is_me)
        elif gc_mode == "separated":
            add(("g", cid), ch["label"], "group", day, is_me)
        else:
            others = ch["others"]
            if gc_mode == "normal":
                recipients = others if is_me else ([sender] if sender in others else [])
            elif is_me:
                recipients = [previous_sender] if previous_sender in others else []
            else:
                recipients = [sender] if previous_sender == me_key and sender in others else []
            for key in recipients:
                add(key, db.name_of(others[key], 0), "person", day, is_me)
        previous_sender = sender
    return people


def build_chart_data(people, days=31, min_messages=5, gc_mode=None, me="Me", today=None):
    """Apply the daily threshold and fill missing calendar days with zeroes."""
    today = today or date.today()
    start = window_start(days, today)
    if min_messages < 0:
        raise ValueError("--min-messages must be zero or a positive integer")
    eligible = []
    other = {"name": "Other", "kind": "other", "daily": {}}
    for person in people.values():
        daily = {d: counts for d, counts in person["daily"].items()
                 if (start is None or d >= start) and d <= today and sum(counts) > 0}
        if not daily:
            continue
        if max(sum(counts) for counts in daily.values()) >= min_messages:
            eligible.append({**person, "daily": daily})
        else:
            for day, counts in daily.items():
                totals = other["daily"].setdefault(day, [0, 0])
                totals[0] += counts[0]
                totals[1] += counts[1]
    eligible.sort(key=lambda p: (-sum(sum(c) for c in p["daily"].values()), p["name"].casefold()))
    if other["daily"]:
        eligible.append(other)
    if start is None:
        start = min((d for p in eligible for d in p["daily"]), default=None)
    calendar = ([start + timedelta(days=i) for i in range((today - start).days + 1)]
                if start is not None else [])
    series = []
    for i, person in enumerate(eligible):
        values = [person["daily"].get(day, [0, 0]) for day in calendar]
        series.append({"id": f"series-{i}", "name": person["name"], "kind": person["kind"],
                       "sent": [c[0] for c in values], "received": [c[1] for c in values]})
    return {"days": days, "minMessages": min_messages, "gcMode": gc_mode, "me": me,
            "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "dates": [day.isoformat() for day in calendar], "series": series}


def render(data):
    # Contact/group names can contain HTML, including a closing script tag.
    payload = json.dumps(data, ensure_ascii=True, separators=(",", ":"))
    payload = payload.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    return Path(__file__).with_suffix(".html").read_text(encoding="utf-8").replace("__CHART_DATA__", payload)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=31,
                    help="calendar days including today (default 31); -1 for all history")
    ap.add_argument("--gc", nargs="?", const="normal", choices=["normal", "conservative", "separated"],
                    help="include group chats: normal attribution, conservative replies, or separate groups")
    ap.add_argument("--min-messages", type=int, default=5,
                    help="minimum sent + received on any one day to avoid Other (default 5; 0 keeps everyone)")
    ap.add_argument("--db", help="path to chat.db (default: ./chat.db if present, else live Messages database)")
    ap.add_argument("--contacts", action="append", help="a .vcf or AddressBook-v22.abcddb file (repeatable)")
    ap.add_argument("--me", default="Me", help='what to call you in the output (default "Me")')
    ap.add_argument("--out", default=OUT_DIR, help=f"directory for the HTML page (default {OUT_DIR})")
    ap.add_argument("--no-open", action="store_true", help="write the page without opening the browser")
    args = ap.parse_args(argv)
    today = date.today()
    try:
        start = window_start(args.days, today)
    except ValueError as e:
        ap.error(str(e))
    if args.min_messages < 0:
        ap.error("--min-messages must be zero or a positive integer")

    local_db = os.path.join(SCRIPT_DIR, "chat.db")
    db_path = os.path.expanduser(args.db or (local_db if os.path.exists(local_db) else LIVE_DB))
    contact_paths = ([os.path.expanduser(p) for p in args.contacts] if args.contacts else
                     sorted(glob.glob(os.path.join(SCRIPT_DIR, "*.vcf"))) or
                     [p for g in ADDRESSBOOK_GLOBS for p in glob.glob(os.path.expanduser(g))])
    _, by_key, used = load_contacts(contact_paths)
    print(f"Database: {db_path}")
    print(f"Contacts: loaded from {used} file(s)")
    try:
        db = ChatDB(db_path, by_key, args.me)
        try:
            people = count_daily_messages(db, args.gc, start, today)
        finally:
            db.con.close()
    except sqlite3.Error as e:
        sys.exit(f"Can't read {db_path}: {e}\nGive your terminal Full Disk Access, or pass --db PATH.")
    data = build_chart_data(people, args.days, args.min_messages, args.gc, args.me, today)
    try:
        out = Path(args.out).expanduser().resolve()
        out.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        path = out / f"daily-chats-{stamp}.html"
        path.write_text(render(data), encoding="utf-8")
    except OSError as e:
        sys.exit(f"Can't write chart: {e}")
    print(f"Chart: {len(data['dates']):,} days, {len(data['series']):,} series")
    if not data["series"]:
        print("  No messages found in this window. Try --days -1 for all history.")
    print(f"Page: {path}")
    if not args.no_open:
        webbrowser.open(path.as_uri())


if __name__ == "__main__":
    main()
