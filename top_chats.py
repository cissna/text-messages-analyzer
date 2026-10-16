#!/usr/bin/env python3
"""top_chats — rank the people you text most, with a clickable breakdown.

    python3 top_chats.py [--n 50] [--gc [conservative]] [--days N] [--per-day [--min-span N]]
                         [--db PATH] [--contacts PATH ...] [--me NAME] [--out DIR] [--no-open]

Counts text messages (not reactions, edits, or group events) per person:
  * 1:1 chats: every message in the chat.
  * --gc: also counts group chats you both belong to:
        normal        all of your messages and all of theirs in each shared group.
        conservative  only messages that directly follow one from the other person
                      (their message right after yours, or yours right after theirs).
                      Reactions and events are ignored when deciding what "directly" means.

--days N limits everything to the last N days (e.g. --days 31 for the last month).

--per-day divides each count by the number of days from your first to your last counted
message with that person (both days included), and ranks by that daily rate.

--min-span N (only with --per-day) drops people whose first-to-last span is shorter than
N days, so a one-day burst of texts doesn't top the ranking.

Writes a self-contained HTML page; click a row to see the me-vs-them split
(and, with --gc, every chat you've messaged in).
"""
import argparse
import glob
import html
import os
import sqlite3
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime

from chatview import (ADDRESSBOOK_GLOBS, APPLE_EPOCH_NS, LIVE_DB, NS, OUT_DIR, SCRIPT_DIR, ChatDB, load_contacts,
                      to_unix_ns)


def count_messages(db, gc_mode, since_ns=None):
    """Return (people, chats_by_person).

    people[key] = {'name', 'me', 'them', 'first', 'last'} (first/last are unix ns of counted messages);
    chats_by_person[key] = {chat_id: {'me', 'them', 'label', 'group'}}.
    """
    chats = {r["ROWID"]: dict(style=r["style"], name=r["display_name"], members=[])
             for r in db.con.execute("SELECT ROWID, style, display_name FROM chat")}
    for cid, h in db.con.execute("SELECT chat_id, handle_id FROM chat_handle_join"):
        if cid in chats:
            chats[cid]["members"].append(h)

    rows = db.con.execute(
        "SELECT cmj.chat_id, m.ROWID, m.handle_id, m.is_from_me, m.date FROM chat_message_join cmj "
        "JOIN message m ON m.ROWID = cmj.message_id "
        "WHERE COALESCE(m.associated_message_type, 0) = 0 AND COALESCE(m.item_type, 0) = 0 "
        "AND m.date >= ? ORDER BY cmj.chat_id, m.date, m.ROWID", (since_ns or 0,))

    by_chat = defaultdict(list)  # chat_id -> [(is_me, person_key, handle_id, unix_ns)]
    for cid, _, handle, from_me, date in rows:
        is_me = bool(from_me) or not handle
        by_chat[cid].append((is_me, None if is_me else db.person_of(handle), handle, to_unix_ns(date)))

    people = {}
    chats_by_person = defaultdict(dict)

    def add(key, handle, chat_id, me_n, them_n, dates):
        if key not in people:
            people[key] = {"name": db.name_of(handle, 0), "me": 0, "them": 0, "first": None, "last": None}
        e = people[key]
        e["me"] += me_n
        e["them"] += them_n
        if dates:
            e["first"] = min(dates) if e["first"] is None else min(e["first"], min(dates))
            e["last"] = max(dates) if e["last"] is None else max(e["last"], max(dates))
        row = chats_by_person[key].setdefault(chat_id, {"me": 0, "them": 0})
        row["me"] += me_n
        row["them"] += them_n

    for cid, msgs in by_chat.items():
        ch = chats.get(cid)
        if ch is None:
            continue
        members = ch["members"]
        is_group = ch["style"] == 43 or len(members) > 1
        if not is_group:
            if not members:
                continue
            key = db.person_of(members[0])
            add(key, members[0], cid, sum(1 for m in msgs if m[0]), sum(1 for m in msgs if not m[0]),
                [m[3] for m in msgs])
            continue

        if gc_mode is None:
            continue
        # Other people in this group, keyed like person_of().
        others = {db.person_of(h): h for h in members}
        for key, handle in others.items():
            if gc_mode == "normal":
                counted = [m for m in msgs if m[0] or m[1] == key]
            else:
                counted = []
                prev = None  # None (no one yet) | "me" | person key
                for m in msgs:
                    side = "me" if m[0] else m[1]
                    if (side == "me" and prev == key) or (side == key and prev == "me"):
                        counted.append(m)
                    prev = side
            me_n = sum(1 for m in counted if m[0])
            them_n = len(counted) - me_n
            if counted:
                add(key, handle, cid, me_n, them_n, [m[3] for m in counted])
                row = chats_by_person[key][cid]
                row["label"] = ch["name"] or "Group: " + ", ".join(sorted({db.name_of(h, 0) for h in members}))
                row["group"] = True

    for key, per_chat in chats_by_person.items():
        for cid, row in per_chat.items():
            if "label" not in row:
                members = chats.get(cid, {}).get("members", [])
                row["label"] = db.name_of(members[0], 0) if members else f"chat {cid}"
                row["group"] = False
    return people, chats_by_person


def calendar_days(first_ns, last_ns):
    """Days from first to last message, counting both ends (a single-day history is 1)."""
    a = datetime.fromtimestamp(first_ns // NS).date()
    b = datetime.fromtimestamp(last_ns // NS).date()
    return (b - a).days + 1


def score(p, per_day):
    total = p["me"] + p["them"]
    if not per_day:
        return total
    return total / calendar_days(p["first"], p["last"])


def fmt_score(p, per_day):
    total = p["me"] + p["them"]
    return f"{score(p, per_day):,.2f}/day" if per_day else f"{total:,}"


def render(people, chats_by_person, top, gc_mode, me, title_date, days=None, per_day=False):
    ranked = sorted(people.items(), key=lambda kv: -score(kv[1], per_day))[:top]
    show_chats = gc_mode is not None
    period = "" if days is None else f" · last {days} day{'s' if days != 1 else ''}"
    rate = " · counts per day" if per_day else ""
    mode_text = {None: "1:1 chats only", "normal": "1:1 + all shared group messages",
                 "conservative": "1:1 + group messages directly after the other person's"}[gc_mode]
    rows = []
    for rank, (key, p) in enumerate(ranked, 1):
        total = p["me"] + p["them"]
        span = calendar_days(p["first"], p["last"])
        detail = [f"<p><strong>{html.escape(me)}:</strong> {p['me']:,} · "
                  f"<strong>{html.escape(p['name'])}:</strong> {p['them']:,} · "
                  f"{total:,} total</p>",
                  f"<p>{datetime.fromtimestamp(p['first'] // NS):%Y-%m-%d} → "
                  f"{datetime.fromtimestamp(p['last'] // NS):%Y-%m-%d} ({span:,} day{'s' if span != 1 else ''})"
                  f"{f' · {total / span:,.2f}/day' if per_day else ''}</p>"]
        if show_chats:
            detail.append("<p class='label'>Chats</p><ul>")
            for cid, row in sorted(chats_by_person[key].items(), key=lambda kv: -(kv[1]["me"] + kv[1]["them"])):
                tag = " (group)" if row.get("group") else ""
                detail.append(f"<li>{html.escape(row['label'])}{tag} — {row['me']:,} from {html.escape(me)}, "
                              f"{row['them']:,} from them</li>")
            detail.append("</ul>")
        rows.append(f"<details><summary><span class='rank'>{rank}.</span> "
                    f"<span class='name'>{html.escape(p['name'])}</span> "
                    f"<span class='span'>{span:,} day{'s' if span != 1 else ''}</span> "
                    f"<span class='count'>{fmt_score(p, per_day)}</span></summary>"
                    f"<div class='detail'>{''.join(detail)}</div></details>")
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Top {top} chats</title>
<style>
  :root {{ color-scheme: light dark; --fg:#1d1d1f; --muted:#6e6e73; --bg:#fff; --pill:#f0f0f3; }}
  @media (prefers-color-scheme: dark) {{ :root {{ --fg:#f5f5f7; --muted:#a1a1a6; --bg:#1c1c1e; --pill:#2c2c2e; }} }}
  body {{ font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: var(--fg); background: var(--bg);
         max-width: 760px; margin: 0 auto; padding: 40px 24px 80px; }}
  h1 {{ font-size: 22px; margin: 0 0 4px; }}
  .meta {{ color: var(--muted); font-size: 13px; margin: 0 0 20px; }}
  details {{ border-bottom: 1px solid var(--pill); }}
  summary {{ cursor: pointer; padding: 8px 4px; display: flex; gap: 10px; align-items: baseline; list-style: none; }}
  summary::-webkit-details-marker {{ display: none; }}
  summary:hover {{ background: var(--pill); }}
  .rank {{ color: var(--muted); width: 2.5em; text-align: right; font-variant-numeric: tabular-nums; }}
  .name {{ flex: 1; }}
  .span {{ color: var(--muted); font-size: 13px; font-variant-numeric: tabular-nums; }}
  .count {{ min-width: 6em; text-align: right; font-variant-numeric: tabular-nums; font-weight: 600; }}
  .detail {{ padding: 4px 4px 12px 3.5em; color: var(--muted); font-size: 14px; }}
  .detail strong {{ color: var(--fg); font-weight: 600; }}
  .detail p {{ margin: 4px 0; }}
  .detail .label {{ margin-top: 10px; font-size: 13px; }}
  .detail ul {{ margin: 4px 0 0; padding-left: 1.2em; }}
  .detail li {{ margin: 2px 0; }}
</style></head>
<body>
<h1>Top {top} people by text count</h1>
<p class="meta">{html.escape(mode_text)}{html.escape(period)}{html.escape(rate)} · generated {html.escape(title_date)} · click a row for the breakdown</p>
{''.join(rows)}
</body></html>
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=50, help="how many people to list (default 50)")
    ap.add_argument("--gc", nargs="?", const="normal", choices=["normal", "conservative"], default=None,
                    help="include group chats you share with each person; add 'conservative' for the stricter count")
    ap.add_argument("--days", type=int, help="only count messages from the last N days (e.g. 31 for a month)")
    ap.add_argument("--per-day", action="store_true",
                    help="divide counts by days from first to last message and rank by that daily rate")
    ap.add_argument("--min-span", type=int, metavar="MIN",
                    help="with --per-day: skip people whose first-to-last message span is under MIN days "
                         "(e.g. 5 filters out one-day conversations)")
    ap.add_argument("--db", help="path to chat.db (default: ./chat.db if present, else ~/Library/Messages/chat.db)")
    ap.add_argument("--contacts", action="append",
                    help="a .vcf or AddressBook-v22.abcddb file (repeatable; default: *.vcf next to this script)")
    ap.add_argument("--me", default="Me", help='what to call you in the output (default "Me")')
    ap.add_argument("--out", default=OUT_DIR, help=f"where to write the page (default {OUT_DIR})")
    ap.add_argument("--no-open", action="store_true", help="don't open the browser")
    args = ap.parse_args()
    if args.min_span is not None and not args.per_day:
        ap.error("--min-span requires --per-day")
    if args.min_span is not None and args.min_span < 1:
        ap.error("--min-span must be a positive number")

    db_path = args.db or (os.path.join(SCRIPT_DIR, "chat.db") if os.path.exists(os.path.join(SCRIPT_DIR, "chat.db"))
                          else LIVE_DB)
    contact_paths = args.contacts or sorted(glob.glob(os.path.join(SCRIPT_DIR, "*.vcf"))) or \
        [p for g in ADDRESSBOOK_GLOBS for p in glob.glob(os.path.expanduser(g))]
    _, by_key, used = load_contacts(contact_paths)
    print(f"Database: {db_path}")
    print(f"Contacts: loaded from {used} file(s)")
    try:
        db = ChatDB(db_path, by_key, args.me)
    except sqlite3.Error as e:
        sys.exit(f"Can't open {db_path}: {e}\nGive your terminal Full Disk Access, or pass --db PATH.")

    if args.days is not None and args.days < 1:
        sys.exit("--days must be a positive number")
    since_ns = None
    if args.days is not None:
        # Clamp: a window reaching before 2001 covers everything, and avoids SQLite integer overflow.
        since_ns = max(0, (int(time.time()) - args.days * 86400) * NS - APPLE_EPOCH_NS)
    people, chats_by_person = count_messages(db, args.gc, since_ns)
    people = {k: p for k, p in people.items() if calendar_days(p["first"], p["last"]) >= (args.min_span or 1)}
    ranked = sorted(people.values(), key=lambda p: -score(p, args.per_day))[:args.n]
    if not ranked:
        print("  No people match (check --days / --min-span)." if args.days or args.min_span
              else "  No messages found.")
    for i, p in enumerate(ranked, 1):
        span = calendar_days(p["first"], p["last"])
        print(f"  {i:>3}. {p['name']:<30} {fmt_score(p, args.per_day):>14}  {span:>5,} day{'s' if span != 1 else ' '}")

    os.makedirs(args.out, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = ("" if args.gc is None else f"-gc-{args.gc}") + ("" if args.days is None else f"-{args.days}d") + \
        ("-per-day" if args.per_day else "")
    path = os.path.join(args.out, f"top-chats{suffix}-{stamp}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(render(people, chats_by_person, args.n, args.gc, args.me, datetime.now().strftime("%Y-%m-%d %H:%M"),
                       args.days, args.per_day))
    if not args.no_open:
        if sys.platform == "darwin":
            subprocess.run(["open", path])
    print(f"Page: {path}")


if __name__ == "__main__":
    main()
