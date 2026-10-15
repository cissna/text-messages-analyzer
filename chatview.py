#!/usr/bin/env python3
"""chatview — export an iMessage conversation as AI-pastable markdown.

    python3 chatview.py [name] [--n 200] [--db PATH] [--contacts PATH ...] [--me NAME]

Searches contacts, group names, and phone numbers/emails for `name`
(case-insensitive), lets you pick a conversation, then writes a self-updating
HTML page showing the N most recent messages with a "Copy markdown" button.
In the terminal, press `y` to load N older messages; any other key quits
(the page stays open). No server needed: the page polls a sibling .js file.

Contacts come from any *.vcf file next to this script (export from the
Contacts app: select all → File → Export → Export vCard), or --contacts.
"""
import argparse
import glob
import html
import json
import os
import plistlib
import re
import sqlite3
import subprocess
import sys
import time
import urllib.parse
import webbrowser
from collections import defaultdict
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LIVE_DB = os.path.expanduser("~/Library/Messages/chat.db")
ADDRESSBOOK_GLOBS = [
    "~/Library/Application Support/AddressBook/AddressBook-v22.abcddb",
    "~/Library/Application Support/AddressBook/Sources/*/AddressBook-v22.abcddb",
]
OUT_DIR = os.path.expanduser("~/.chat-viewer")

NS = 10**9
APPLE_EPOCH_NS = 978307200 * NS
TAPBACKS = {0: "❤️", 1: "👍", 2: "👎", 3: "😂", 4: "‼️", 5: "❓"}
EFFECTS = {
    "com.apple.MobileSMS.expressivesend.impact": "slam",
    "com.apple.MobileSMS.expressivesend.loud": "loud",
    "com.apple.MobileSMS.expressivesend.gentle": "gentle",
    "com.apple.MobileSMS.expressivesend.invisibleink": "invisible ink",
}
MSG_COLS = ["guid", "text", "attributedBody", "handle_id", "is_from_me", "date", "item_type",
            "group_action_type", "other_handle", "group_title", "thread_originator_guid", "date_edited",
            "date_retracted", "is_audio_message", "expressive_send_style_id", "balloon_bundle_id",
            "message_summary_info"]


# ---------------------------------------------------------------- helpers

def open_ro(path):
    return sqlite3.connect(f"file:{urllib.parse.quote(os.path.abspath(path))}?mode=ro", uri=True)


def ident_key(s):
    """Normalize a phone/email so chat.db handles match contact entries."""
    s = (s or "").strip()
    if not s:
        return None
    if "@" in s:
        return "e:" + s.lower()
    digits = re.sub(r"\D", "", s)
    return "p:" + digits[-10:] if digits else "x:" + s.lower()


def to_unix_ns(d):
    """Apple timestamps: ns since 2001 (modern), seconds since 2001 (old / some plists), or a datetime."""
    if isinstance(d, datetime):
        return int(d.replace(tzinfo=d.tzinfo or timezone.utc).timestamp() * NS)
    if not d:
        return 0
    if abs(d) < 10**11:
        return int(round(d * NS)) + APPLE_EPOCH_NS
    return int(d) + APPLE_EPOCH_NS


def decode_attributed_body(blob):
    """Pull the plain string out of an NSAttributedString typedstream blob."""
    if not blob:
        return None
    i = blob.find(b"NSString")
    if i < 0:
        return None
    j = blob.find(b"+", i + 8, i + 20)
    if j < 0:
        return None
    p = j + 1
    n = blob[p]
    if n == 0x81:
        n, p = int.from_bytes(blob[p + 1:p + 3], "little"), p + 3
    elif n == 0x82:
        n, p = int.from_bytes(blob[p + 1:p + 5], "little"), p + 5
    else:
        p += 1
    return blob[p:p + n].decode("utf-8", errors="replace")


def summary_info(blob):
    """message_summary_info plist: `ec` = edit history per part, `rp` = unsent (retracted) part indexes."""
    try:
        info = plistlib.loads(blob) if blob else None
    except Exception:
        return {}
    return info if isinstance(info, dict) else {}


def edit_history(info):
    """[(unix_ns, text), ...] oldest first, or None if unavailable."""
    ec = info.get("ec")
    if not isinstance(ec, dict):
        return None
    versions = []
    for part in sorted(ec, key=lambda k: int(k) if str(k).isdigit() else 0):
        for ev in ec[part] or []:
            if not isinstance(ev, dict):
                continue
            t = ev.get("t")
            text = decode_attributed_body(t) if isinstance(t, bytes) else (t if isinstance(t, str) else "")
            versions.append((to_unix_ns(ev.get("d")), (text or "").replace("\ufffc", "[attachment]").strip()))
    return versions or None


def frac_digits(times):
    """Per timestamp, the fewest decimals (0-3) that make it differ from its neighbors when shown."""
    def needed(a, b):
        for d in range(4):
            if a // 10**(9 - d) != b // 10**(9 - d):
                return d
        return 3
    out = []
    for i, t in enumerate(times):
        d = needed(times[i - 1], t) if i > 0 else 0
        if i + 1 < len(times):
            d = max(d, needed(t, times[i + 1]))
        out.append(d)
    return out


def fmt_time(ns, digits, base_ns=None):
    """HH:MM:SS[.fff]; prefixed with the date if it falls on a different day than base_ns."""
    secs, frac = divmod(ns, NS)
    dt = datetime.fromtimestamp(secs)
    s = dt.strftime("%H:%M:%S") + ("." + f"{frac:09d}"[:digits] if digits else "")
    if base_ns is not None and dt.date() != datetime.fromtimestamp(base_ns // NS).date():
        s = dt.strftime("%Y-%m-%d ") + s
    return s


def day_of(ns):
    return datetime.fromtimestamp(ns // NS).strftime("%a %Y-%m-%d")


def date_of(ns):
    return datetime.fromtimestamp(ns // NS).strftime("%Y-%m-%d")


def snippet(text, limit=60):
    text = re.sub(r"\s+", " ", (text or "").replace("\ufffc", "[attachment]")).strip()
    return text if len(text) <= limit else text[:limit - 1] + "…"


def ordinal(n):
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def getch():
    if not sys.stdin.isatty():
        while True:
            c = sys.stdin.read(1)
            if not c or not c.isspace():
                return c
    import termios
    import tty
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        return sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


# ---------------------------------------------------------------- contacts

class Contact:
    def __init__(self, name, terms):
        self.name = name
        self.terms = [t.casefold() for t in terms if t]

    def matches(self, q):
        return any(q in t for t in self.terms)


def read_vcf(path):
    """Yield (display_name, search_terms, ident_keys) per vCard."""
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = re.sub(r"\r?\n[ \t]", "", f.read()).splitlines()  # unfold continuation lines
    card = None
    for line in lines:
        upper = line.strip().upper()
        if upper == "BEGIN:VCARD":
            card = defaultdict(list)
        elif upper == "END:VCARD" and card is not None:
            unesc = lambda v: re.sub(r"\\([,;\\])", r"\1", v).replace("\\n", " ").strip()
            n = (card["N"] or [""])[0].split(";") + [""] * 5
            last, first, middle = unesc(n[0]), unesc(n[1]), unesc(n[2])
            org = unesc((card["ORG"] or [""])[0].split(";")[0])
            nick = unesc((card["NICKNAME"] or [""])[0])
            name = unesc((card["FN"] or [""])[0]) or " ".join(x for x in (first, last) if x) or nick or org
            keys = {ident_key(v) for v in card["TEL"] + card["EMAIL"]} - {None}
            yield name, [name, first, middle, last, nick, org], keys
            card = None
        elif card is not None and ":" in line:
            key, value = line.split(":", 1)
            card[key.split(";")[0].split(".")[-1].upper()].append(value)


def read_addressbook(path):
    con = open_ro(path)
    recs = {r[0]: r[1:] for r in con.execute(
        "SELECT Z_PK, ZFIRSTNAME, ZLASTNAME, ZNICKNAME, ZORGANIZATION FROM ZABCDRECORD")}
    idents = defaultdict(set)
    for owner, value in list(con.execute("SELECT ZOWNER, ZFULLNUMBER FROM ZABCDPHONENUMBER")) + \
            list(con.execute("SELECT ZOWNER, ZADDRESS FROM ZABCDEMAILADDRESS")):
        if ident_key(value):
            idents[owner].add(ident_key(value))
    con.close()
    for pk, keys in idents.items():
        if pk in recs:
            first, last, nick, org = recs[pk]
            name = " ".join(x for x in (first, last) if x) or nick or org
            yield name, [name, first, last, nick, org], keys


def load_contacts(paths):
    by_key, contacts, used = {}, [], 0
    for path in paths:
        try:
            records = list(read_vcf(path) if path.lower().endswith(".vcf") else read_addressbook(path))
            used += 1
        except (OSError, sqlite3.Error) as e:
            print(f"  (skipping contacts file {path}: {e})", file=sys.stderr)
            continue
        for name, terms, keys in records:
            if not name or not keys:
                continue
            contact = next((by_key[k] for k in keys if k in by_key), None)
            if contact is None:
                contact = Contact(name, terms)
                contacts.append(contact)
            for k in keys:
                by_key.setdefault(k, contact)
    return contacts, by_key, used


# ---------------------------------------------------------------- chat.db

class ChatDB:
    def __init__(self, path, by_key, me):
        self.con = open_ro(path)
        self.con.row_factory = sqlite3.Row
        self.me = me
        self.cols = {r[1] for r in self.con.execute("PRAGMA table_info(message)")}
        missing = [c for c in MSG_COLS if c not in self.cols]
        if missing:
            print(f"  (note: this chat.db lacks columns {missing}; those features are skipped)", file=sys.stderr)
        self.handles = {r[0]: r[1] for r in self.con.execute("SELECT ROWID, id FROM handle")}
        self.handle_contact = {h: by_key.get(ident_key(i)) for h, i in self.handles.items()}

    def col(self, name):
        return f"m.{name}" if name in self.cols else f"NULL AS {name}"

    def name_of(self, handle_id, is_from_me):
        # Messages treats handle 0 as "You" (e.g. group renames you did are stored with is_from_me = 0).
        if is_from_me or not handle_id:
            return self.me
        c = self.handle_contact.get(handle_id)
        return c.name if c else self.handles.get(handle_id) or "Unknown"

    def person_of(self, h):
        c = self.handle_contact.get(h)
        return ("c", id(c)) if c else ("h", ident_key(self.handles.get(h)))


class Candidate:
    def __init__(self, label, is_group, chat_ids, members):
        self.label, self.is_group, self.chat_ids, self.members = label, is_group, chat_ids, members
        self.count = self.last = 0


def find_candidates(db, query):
    q = query.casefold().strip()
    qdigits = re.sub(r"\D", "", q)
    matched = set()
    for h, ident in db.handles.items():
        c = db.handle_contact.get(h)
        if (c and c.matches(q)) or q in ident.casefold() or \
                (len(qdigits) >= 3 and qdigits in re.sub(r"\D", "", ident)):
            matched.add(db.person_of(h))

    chats = {r["ROWID"]: dict(style=r["style"], name=r["display_name"], members=[])
             for r in db.con.execute("SELECT ROWID, style, display_name FROM chat")}
    for cid, h in db.con.execute("SELECT chat_id, handle_id FROM chat_handle_join"):
        if cid in chats:
            chats[cid]["members"].append(h)

    people, groups = {}, []
    for cid, ch in chats.items():
        mem = ch["members"]
        if ch["style"] == 43 or len(mem) > 1:
            if (ch["name"] and q in ch["name"].casefold()) or any(db.person_of(h) in matched for h in mem):
                names = sorted({db.name_of(h, 0) for h in mem})
                groups.append(Candidate(ch["name"] or "Group: " + ", ".join(names), True, [cid], mem))
        elif mem and db.person_of(mem[0]) in matched:
            key = db.person_of(mem[0])
            if key not in people:
                people[key] = Candidate(db.name_of(mem[0], 0), False, [], [])
            people[key].chat_ids.append(cid)
            people[key].members.append(mem[0])

    out = []
    for cand in list(people.values()) + groups:
        ph = ",".join("?" * len(cand.chat_ids))
        cand.count, last = db.con.execute(
            f"SELECT COUNT(DISTINCT m.ROWID), MAX(m.date) FROM chat_message_join cmj "
            f"JOIN message m ON m.ROWID = cmj.message_id WHERE cmj.chat_id IN ({ph}) "
            f"AND COALESCE(m.associated_message_type, 0) = 0", cand.chat_ids).fetchone()
        cand.last = to_unix_ns(last)
        if cand.count:
            out.append(cand)
    out.sort(key=lambda c: (c.is_group, -c.last))
    return out


class Conversation:
    def __init__(self, db, cand):
        self.db, self.cand = db, cand
        self.ph = ",".join("?" * len(cand.chat_ids))
        rows = db.con.execute(
            f"SELECT DISTINCT m.ROWID, m.date FROM chat_message_join cmj JOIN message m ON m.ROWID = cmj.message_id "
            f"WHERE cmj.chat_id IN ({self.ph}) AND COALESCE(m.associated_message_type, 0) = 0 "
            f"AND COALESCE(m.item_type, 0) IN (0, 1, 2, 3)", cand.chat_ids)
        self.order = sorted((to_unix_ns(d), rid) for rid, d in rows)
        self.total = len(self.order)
        self.reactions = self._load_reactions()
        self.items = {}  # rowid -> rendered item (cached across pages)
        self.reply_cache = {}

    def _load_reactions(self):
        """target guid -> chronological list of (unix_ns, description), including removals."""
        rows = self.db.con.execute(
            f"SELECT m.associated_message_guid, m.associated_message_type, {self.db.col('associated_message_emoji')}, "
            f"m.handle_id, m.is_from_me, m.date FROM chat_message_join cmj JOIN message m ON m.ROWID = cmj.message_id "
            f"WHERE cmj.chat_id IN ({self.ph}) AND (m.associated_message_type = 1000 "
            f"OR m.associated_message_type BETWEEN 2000 AND 3999) ORDER BY m.date, m.ROWID", self.cand.chat_ids)
        current = {}  # (target, sender) -> current tapback label
        out = defaultdict(list)
        for g, t, emoji, h, me, date in rows:
            target = re.sub(r"^(?:p:\d+/|bp:)", "", g or "")
            sender = self.db.name_of(h, me)
            base = t % 1000
            if t == 1000 or base == 7:
                desc = f"{sender} {'removed a sticker' if t >= 3000 else 'added a sticker'}"
            else:
                label = (emoji or current.get((target, sender)) or "a custom emoji") if base == 6 \
                    else TAPBACKS.get(base, "a reaction")
                if t < 3000:
                    prev = current.get((target, sender))
                    desc = f"{sender} reacted {label}" + (f" (replacing {prev})" if prev and prev != label else "")
                    current[(target, sender)] = label
                else:
                    desc = f"{sender} removed {label}"
                    current.pop((target, sender), None)
            out[target].append((to_unix_ns(date), desc))
        return out

    def _reply_quote(self, guid):
        if guid not in self.reply_cache:
            r = self.db.con.execute("SELECT text, attributedBody, handle_id, is_from_me FROM message WHERE guid = ?",
                                    (guid,)).fetchone()
            self.reply_cache[guid] = None if r is None else (
                self.db.name_of(r["handle_id"], r["is_from_me"]),
                snippet(r["text"] or decode_attributed_body(r["attributedBody"])))
        return self.reply_cache[guid]

    def _fetch(self, rowids):
        cols = ", ".join(self.db.col(c) for c in MSG_COLS)
        for i in range(0, len(rowids), 500):
            chunk = rowids[i:i + 500]
            ph = ",".join("?" * len(chunk))
            atts = defaultdict(list)
            for r in self.db.con.execute(
                    f"SELECT maj.message_id, a.filename, a.transfer_name, a.mime_type, a.is_sticker "
                    f"FROM message_attachment_join maj JOIN attachment a ON a.ROWID = maj.attachment_id "
                    f"WHERE maj.message_id IN ({ph}) ORDER BY a.ROWID", chunk):
                name = r["transfer_name"] or os.path.basename(r["filename"] or "") or "attachment"
                if name.endswith(".pluginPayloadAttachment"):
                    continue
                mime = r["mime_type"] or ""
                kind = next((k for k in ("image", "video", "audio") if mime.startswith(k + "/")), "file")
                atts[r["message_id"]].append("[sticker]" if r["is_sticker"] else f"[{kind}: {name}]")
            for r in self.db.con.execute(f"SELECT m.ROWID AS rid, {cols} FROM message m WHERE m.ROWID IN ({ph})", chunk):
                self.items[r["rid"]] = self._render(r, atts.get(r["rid"], []))

    def _render(self, r, atts):
        sender = self.db.name_of(r["handle_id"], r["is_from_me"])
        item = dict(sender=sender, kind="msg", text="", reply=None, flags=[], edits=None, edited_at=None,
                    reactions=self.reactions.get(r["guid"], []))
        it = r["item_type"] or 0
        if it:
            item["kind"] = "event"
            target = self.db.name_of(r["other_handle"], 0)
            act = r["group_action_type"] or 0
            if it == 1:
                item["text"] = f"{sender} {'removed' if act else 'added'} {target}"
            elif it == 2:
                item["text"] = f'{sender} named the conversation "{r["group_title"]}"' if r["group_title"] \
                    else f"{sender} removed the conversation name"
            else:
                item["text"] = {0: f"{sender} left the conversation", 1: f"{sender} changed the group photo",
                                2: f"{sender} removed the group photo"}.get(act, f"{sender} updated the group")
            return item
        info = summary_info(r["message_summary_info"])
        # Unsends are recorded as `rp` in message_summary_info with the unsend time in date_edited;
        # date_retracted is usually left empty.
        unsent = bool(info.get("rp")) or bool(r["date_retracted"])
        unsent_at = to_unix_ns(r["date_retracted"] or r["date_edited"])
        msg_ns = to_unix_ns(r["date"])
        text = r["text"] or decode_attributed_body(r["attributedBody"]) or ""
        if r["is_audio_message"]:
            atts = ["[voice message]"]
        parts = text.split("\ufffc")
        tokens = [parts[0].strip()]
        for i, part in enumerate(parts[1:]):
            tokens += [atts[i] if i < len(atts) else "", part.strip()]
        text = " ".join(t for t in tokens + atts[len(parts) - 1:] if t)
        if unsent and not text:
            when = f" (unsent at {fmt_time(unsent_at, 0, msg_ns)})" if unsent_at else ""
            item.update(kind="event", text=f"{sender} unsent a message{when}")
            return item
        if not text and r["balloon_bundle_id"]:
            text = "[iMessage app content]"
        item["text"] = text or "[empty message]"
        if r["thread_originator_guid"]:
            item["reply"] = self._reply_quote(r["thread_originator_guid"])
        if unsent:
            item["flags"].append("part of this message was unsent" +
                                 (f" at {fmt_time(unsent_at, 0, msg_ns)}" if unsent_at else ""))
        history = edit_history(info)
        if history and len(history) > 1:
            item["edits"] = history
        elif r["date_edited"] and not unsent:
            item["edited_at"] = to_unix_ns(r["date_edited"])
        effect = r["expressive_send_style_id"]
        if effect:
            name = EFFECTS.get(effect) or re.sub(r"^CK|Effect$", "", effect.rsplit(".", 1)[-1]).lower()
            item["flags"].append(f"sent with {name} effect")
        return item

    def page(self, count):
        """Rendered items for the `count` most recent messages, oldest first."""
        start = max(0, self.total - count)
        need = [rid for _, rid in self.order[start:] if rid not in self.items]
        if need:
            self._fetch(need)
        sel = self.order[start:]
        digits = frac_digits([ns for ns, _ in sel])
        return [dict(self.items[rid], ns=ns, digits=d) for (ns, rid), d in zip(sel, digits)]


# ---------------------------------------------------------------- output

LEGEND = ("Each line is `time sender: text`. Indented under a message: its edit history (Original message, "
          "1st Edit, …) and a timeline of reactions, including ones later removed. `↪ Name: \"…\"` = an inline "
          "reply to that earlier message. Fractional seconds appear only where needed to tell adjacent "
          "timestamps apart; sub-items show a date when it differs from the message's.")


def sub_lines(it):
    """[(level, text, is_current)] nested under a message: edit history, then reactions."""
    lines = []
    if it["edits"]:
        ds = frac_digits([ns for ns, _ in it["edits"]])
        for i, ((ns, text), d) in enumerate(zip(it["edits"], ds)):
            label = "Original message" if i == 0 else f"{ordinal(i)} Edit"
            lines.append((1, f"{fmt_time(ns, d, it['ns'])} {label}: {text}", i == len(it["edits"]) - 1))
    if it["reactions"]:
        lines.append((1, "Reactions:", False))
        ds = frac_digits([ns for ns, _ in it["reactions"]])
        lines += [(2, f"{fmt_time(ns, d, it['ns'])} {desc}", False) for (ns, desc), d in zip(it["reactions"], ds)]
    return lines


def build_output(conv, items, me):
    cand, db = conv.cand, conv.db
    tz = datetime.now().astimezone().strftime("%Z, UTC%z")
    span = f"{date_of(items[0]['ns'])} → {date_of(items[-1]['ns'])}" if items else ""
    shown = (f"all {conv.total:,} messages" if len(items) >= conv.total
             else f"the {len(items):,} most recent of {conv.total:,} messages")
    if cand.is_group:
        title = f"Group chat: {cand.label}"
        who = "Participants: " + ", ".join(sorted({db.name_of(h, 0) for h in cand.members}) + [me])
    else:
        title = f"Messages with {cand.label}"
        who = f"1:1 conversation ({', '.join(db.handles[h] for h in cand.members)})"
    meta = f"{who} · showing {shown} ({span}) · times are local ({tz})"

    md = [f"# {title}", "", f"_{meta}_", "", f"> {LEGEND}", ""]
    hp = [f"<h1>{html.escape(title)}</h1>", f"<p class='meta'><em>{html.escape(meta)}</em></p>",
          f"<blockquote>{html.escape(LEGEND)}</blockquote>"]
    esc = lambda s: html.escape(s).replace("\n", "<br>")
    day = None
    for it in items:
        d = day_of(it["ns"])
        if d != day:
            day = d
            md += ["", f"## {d}", ""]
            hp.append(f"<h2>{d}</h2>")
        t = fmt_time(it["ns"], it["digits"])
        if it["kind"] == "event":
            md.append(f"- **{t}** _{it['text']}_")
            hp.append(f"<div class='msg'><strong>{t}</strong> <em>{esc(it['text'])}</em></div>")
            continue
        reply_md = reply_h = ""
        if it["reply"]:
            reply_md = f'↪ _{it["reply"][0]}: "{it["reply"][1]}"_ '
            reply_h = f"<span class='reply'>↪ {esc(it['reply'][0])}: “{esc(it['reply'][1])}”</span> "
        flags = list(it["flags"])
        if it["edited_at"]:
            flags.insert(0, f"edited {fmt_time(it['edited_at'], 0, it['ns'])}; earlier versions not stored")
        flags_md = " _(" + "; ".join(flags) + ")_" if flags else ""
        flags_h = f" <em class='flag'>({esc('; '.join(flags))})</em>" if flags else ""
        body_md = "_Edited Message_" if it["edits"] else it["text"].replace("\n", "\n  ")
        body_h = "<em>Edited Message</em>" if it["edits"] else esc(it["text"])
        md.append(f"- **{t} {it['sender']}:** {reply_md}{body_md}{flags_md}")
        hp.append(f"<div class='msg'><strong>{t} {esc(it['sender'])}:</strong> {reply_h}{body_h}{flags_h}")
        subs = sub_lines(it)
        if subs:
            hp.append("<ul>")
            for i, (level, text, current) in enumerate(subs):
                md.append("  " * level + "- " + text.replace("\n", "\n" + "  " * (level + 1)))
                if level == 2 and subs[i - 1][0] == 1:
                    hp.append("<ul>")
                hp.append(f"<li class='current'>{esc(text)}</li>" if current else f"<li>{esc(text)}</li>")
                if level == 2 and (i + 1 == len(subs) or subs[i + 1][0] == 1):
                    hp.append("</ul>")
            hp.append("</ul>")
        hp.append("</div>")
    return title, "\n".join(md) + "\n", "\n".join(hp)


VIEWER = """<!doctype html>
<html><head><meta charset="utf-8"><title>Chat viewer</title>
<style>
  :root { color-scheme: light dark; --fg:#1d1d1f; --muted:#6e6e73; --bg:#fff; --pill:#f0f0f3; --accent:#0a7cff; }
  @media (prefers-color-scheme: dark) { :root { --fg:#f5f5f7; --muted:#a1a1a6; --bg:#1c1c1e; --pill:#2c2c2e; } }
  body { font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: var(--fg); background: var(--bg);
         max-width: 880px; margin: 0 auto; padding: 56px 24px 80px; }
  #bar { position: fixed; top: 0; left: 0; right: 0; display: flex; justify-content: space-between; align-items: center;
         gap: 12px; padding: 10px 16px; background: var(--bg); border-bottom: 1px solid var(--pill); z-index: 1; }
  #status { color: var(--muted); font-size: 13px; }
  #copy { font: inherit; font-size: 13px; padding: 6px 14px; border: 0; border-radius: 8px; background: var(--accent);
          color: #fff; cursor: pointer; }
  h1 { font-size: 22px; margin: 8px 0 4px; } h2 { font-size: 15px; margin: 28px 0 8px; color: var(--muted); }
  .meta { color: var(--muted); margin: 0 0 12px; font-size: 13px; }
  blockquote { margin: 0 0 8px; padding: 6px 12px; border-left: 3px solid var(--pill); color: var(--muted); font-size: 13px; }
  .msg { margin: 3px 0; padding-left: 1.2em; text-indent: -1.2em; overflow-wrap: anywhere; }
  .msg strong { font-variant-numeric: tabular-nums; }
  .msg ul { margin: 2px 0 6px; padding-left: 1.6em; text-indent: 0; color: var(--muted); font-size: 14px;
            font-variant-numeric: tabular-nums; }
  .msg li.current { color: var(--fg); }
  .reply { color: var(--muted); font-style: italic; }
  .flag { color: var(--muted); font-size: 13px; }
</style></head>
<body>
<div id="bar"><span id="status">Loading…</span><button id="copy">Copy markdown</button></div>
<main id="content"></main>
<script>
const DATA_FILE = __DATA_FILE__;
let version = null, markdown = "";
window.onChatData = d => {
  if (d.version === version) return;
  version = d.version; markdown = d.markdown;
  document.title = d.title;
  document.getElementById("content").innerHTML = d.html;
  document.getElementById("status").textContent = d.status;
};
function poll() {
  const s = document.createElement("script");
  s.src = DATA_FILE + "?t=" + Date.now();
  s.onload = s.onerror = () => s.remove();
  document.head.appendChild(s);
}
poll(); setInterval(poll, 800);
document.getElementById("copy").onclick = async () => {
  const btn = document.getElementById("copy");
  try { await navigator.clipboard.writeText(markdown); }
  catch (e) {
    const t = document.createElement("textarea"); t.value = markdown; document.body.appendChild(t);
    t.select(); document.execCommand("copy"); t.remove();
  }
  btn.textContent = "Copied ✓"; setTimeout(() => btn.textContent = "Copy markdown", 1500);
};
</script>
</body></html>
"""


def write_data(js_path, conv, count, version, n, me):
    items = conv.page(count)
    title, md, body = build_output(conv, items, me)
    remaining = conv.total - len(items)
    status = f"{len(items):,} of {conv.total:,} messages · ~{len(md) // 4:,} tokens · " + (
        f"press y in the terminal for {min(n, remaining):,} more" if remaining else "everything loaded")
    payload = dict(version=version, title=title, markdown=md, html=body, status=status)
    tmp = js_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("window.onChatData(" + json.dumps(payload) + ");\n")
    os.replace(tmp, js_path)
    return len(items)


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name", nargs="?", help="name / group name / phone / email to search for")
    ap.add_argument("--n", type=int, default=200, help="messages per page (default 200)")
    ap.add_argument("--db", help="path to chat.db (default: ./chat.db if present, else ~/Library/Messages/chat.db)")
    ap.add_argument("--contacts", action="append",
                    help="a .vcf or AddressBook-v22.abcddb file (repeatable; default: *.vcf next to this script)")
    ap.add_argument("--me", default="Me", help='what to call you in the transcript (default "Me")')
    ap.add_argument("--out", default=OUT_DIR, help=f"where to write the viewer files (default {OUT_DIR})")
    ap.add_argument("--no-open", action="store_true", help="don't open the browser")
    args = ap.parse_args()

    db_path = args.db or (os.path.join(SCRIPT_DIR, "chat.db") if os.path.exists(os.path.join(SCRIPT_DIR, "chat.db"))
                          else LIVE_DB)
    contact_paths = args.contacts or sorted(glob.glob(os.path.join(SCRIPT_DIR, "*.vcf"))) or \
        [p for g in ADDRESSBOOK_GLOBS for p in glob.glob(os.path.expanduser(g))]
    contacts, by_key, used = load_contacts(contact_paths)
    print(f"Database: {db_path}")
    print(f"Contacts: {len(contacts)} loaded from {used} file(s)")
    if not contacts:
        print("  (no contacts found — names will show as phone numbers/emails; put a .vcf export next to this script)")
    try:
        db = ChatDB(db_path, by_key, args.me)
    except sqlite3.Error as e:
        sys.exit(f"Can't open {db_path}: {e}\nGive your terminal Full Disk Access, or pass --db PATH.")

    query = args.name or input("Name to search: ")
    cands = find_candidates(db, query)
    if not cands:
        sys.exit(f'No conversations match "{query}".')
    for i, c in enumerate(cands, 1):
        kind = "group" if c.is_group else "1:1"
        print(f"  {i:>2}. {c.label}  [{kind} · {c.count:,} msgs · last {date_of(c.last)}]")
    if len(cands) == 1:
        cand = cands[0]
        print(f"Only one match: {cand.label}")
    else:
        while True:
            choice = input(f"Choose 1-{len(cands)}: ").strip()
            if choice.isdigit() and 1 <= int(choice) <= len(cands):
                cand = cands[int(choice) - 1]
                break

    conv = Conversation(db, cand)
    os.makedirs(args.out, exist_ok=True)
    stem = re.sub(r"[^\w]+", "-", cand.label).strip("-")[:40] + datetime.now().strftime("-%Y%m%d-%H%M%S")
    html_path = os.path.join(args.out, stem + ".html")
    js_path = os.path.join(args.out, stem + ".data.js")
    shown = write_data(js_path, conv, args.n, time.time(), args.n, args.me)
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(VIEWER.replace("__DATA_FILE__", json.dumps(os.path.basename(js_path))))
    if not args.no_open:
        if sys.platform == "darwin":
            subprocess.run(["open", html_path])
        else:
            webbrowser.open("file://" + html_path)
    print(f"Viewer: {html_path}")

    try:
        while shown < conv.total:
            print(f"Showing {shown:,}/{conv.total:,}. Press y for {min(args.n, conv.total - shown):,} more, "
                  f"any other key to quit.", flush=True)
            if getch().lower() != "y":
                break
            shown = write_data(js_path, conv, shown + args.n, time.time(), args.n, args.me)
        else:
            print(f"All {conv.total:,} messages loaded.")
    except KeyboardInterrupt:
        pass
    print("Done — the page stays open with what was loaded.")


if __name__ == "__main__":
    main()
