#!/usr/bin/env python3
"""Generate FAKE sample/chat.db and sample/AddressBook-v22.abcddb.

The schemas mirror macOS 15's Messages and Contacts databases (same table and
column names, same date encoding, same attributedBody typedstream layout) so
chatview.py can be developed and tested without touching real data.
"""
import os
import plistlib
import random
import sqlite3
import uuid
from datetime import datetime

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample")
APPLE_EPOCH = 978307200
NS = 10**9

CHAT_SCHEMA = """
CREATE TABLE handle (ROWID INTEGER PRIMARY KEY AUTOINCREMENT UNIQUE, id TEXT NOT NULL, country TEXT, service TEXT NOT NULL, uncanonicalized_id TEXT, person_centric_id TEXT, UNIQUE (id, service) );
CREATE TABLE chat (ROWID INTEGER PRIMARY KEY AUTOINCREMENT, guid TEXT UNIQUE NOT NULL, style INTEGER, state INTEGER, account_id TEXT, properties BLOB, chat_identifier TEXT, service_name TEXT, room_name TEXT, account_login TEXT, is_archived INTEGER DEFAULT 0, last_addressed_handle TEXT, display_name TEXT, group_id TEXT, is_filtered INTEGER DEFAULT 0, successful_query INTEGER, engram_id TEXT, server_change_token TEXT, ck_sync_state INTEGER DEFAULT 0, original_group_id TEXT, last_read_message_timestamp INTEGER DEFAULT 0, cloudkit_record_id TEXT, last_addressed_sim_id TEXT, is_blackholed INTEGER DEFAULT 0, syndication_date INTEGER DEFAULT 0, syndication_type INTEGER DEFAULT 0, is_recovered INTEGER DEFAULT 0, is_deleting_incoming_messages INTEGER DEFAULT 0);
CREATE TABLE message (ROWID INTEGER PRIMARY KEY AUTOINCREMENT, guid TEXT UNIQUE NOT NULL, text TEXT, replace INTEGER DEFAULT 0, service_center TEXT, handle_id INTEGER DEFAULT 0, subject TEXT, country TEXT, attributedBody BLOB, version INTEGER DEFAULT 0, type INTEGER DEFAULT 0, service TEXT, account TEXT, account_guid TEXT, error INTEGER DEFAULT 0, date INTEGER, date_read INTEGER, date_delivered INTEGER, is_delivered INTEGER DEFAULT 0, is_finished INTEGER DEFAULT 0, is_emote INTEGER DEFAULT 0, is_from_me INTEGER DEFAULT 0, is_empty INTEGER DEFAULT 0, is_delayed INTEGER DEFAULT 0, is_auto_reply INTEGER DEFAULT 0, is_prepared INTEGER DEFAULT 0, is_read INTEGER DEFAULT 0, is_system_message INTEGER DEFAULT 0, is_sent INTEGER DEFAULT 0, has_dd_results INTEGER DEFAULT 0, is_service_message INTEGER DEFAULT 0, is_forward INTEGER DEFAULT 0, was_downgraded INTEGER DEFAULT 0, is_archive INTEGER DEFAULT 0, cache_has_attachments INTEGER DEFAULT 0, cache_roomnames TEXT, was_data_detected INTEGER DEFAULT 0, was_deduplicated INTEGER DEFAULT 0, is_audio_message INTEGER DEFAULT 0, is_played INTEGER DEFAULT 0, date_played INTEGER, item_type INTEGER DEFAULT 0, other_handle INTEGER DEFAULT 0, group_title TEXT, group_action_type INTEGER DEFAULT 0, share_status INTEGER DEFAULT 0, share_direction INTEGER DEFAULT 0, is_expirable INTEGER DEFAULT 0, expire_state INTEGER DEFAULT 0, message_action_type INTEGER DEFAULT 0, message_source INTEGER DEFAULT 0, associated_message_guid TEXT, associated_message_type INTEGER DEFAULT 0, balloon_bundle_id TEXT, payload_data BLOB, expressive_send_style_id TEXT, associated_message_range_location INTEGER DEFAULT 0, associated_message_range_length INTEGER DEFAULT 0, time_expressive_send_played INTEGER, message_summary_info BLOB, ck_sync_state INTEGER DEFAULT 0, ck_record_id TEXT, ck_record_change_tag TEXT, destination_caller_id TEXT, is_corrupt INTEGER DEFAULT 0, reply_to_guid TEXT, sort_id INTEGER, is_spam INTEGER DEFAULT 0, has_unseen_mention INTEGER DEFAULT 0, thread_originator_guid TEXT, thread_originator_part TEXT, syndication_ranges TEXT, synced_syndication_ranges TEXT, was_delivered_quietly INTEGER DEFAULT 0, did_notify_recipient INTEGER DEFAULT 0, date_retracted INTEGER, date_edited INTEGER, was_detonated INTEGER DEFAULT 0, part_count INTEGER, is_stewie INTEGER DEFAULT 0, is_kt_verified INTEGER DEFAULT 0, is_sos INTEGER DEFAULT 0, is_critical INTEGER DEFAULT 0, bia_reference_id TEXT DEFAULT NULL, fallback_hash TEXT DEFAULT NULL, associated_message_emoji TEXT DEFAULT NULL, is_pending_satellite_send INTEGER DEFAULT 0, needs_relay INTEGER DEFAULT 0, schedule_type INTEGER DEFAULT 0, schedule_state INTEGER DEFAULT 0, sent_or_received_off_grid INTEGER DEFAULT 0);
CREATE TABLE attachment (ROWID INTEGER PRIMARY KEY AUTOINCREMENT, guid TEXT UNIQUE NOT NULL, created_date INTEGER DEFAULT 0, start_date INTEGER DEFAULT 0, filename TEXT, uti TEXT, mime_type TEXT, transfer_state INTEGER DEFAULT 0, is_outgoing INTEGER DEFAULT 0, user_info BLOB, transfer_name TEXT, total_bytes INTEGER DEFAULT 0, is_sticker INTEGER DEFAULT 0, sticker_user_info BLOB, attribution_info BLOB, hide_attachment INTEGER DEFAULT 0, ck_sync_state INTEGER DEFAULT 0, ck_server_change_token_blob BLOB, ck_record_id TEXT, original_guid TEXT UNIQUE NOT NULL, sr_ck_sync_state INTEGER DEFAULT 0, sr_ck_server_change_token_blob BLOB, sr_ck_record_id TEXT, is_commsafety_sensitive INTEGER DEFAULT 0, emoji_image_content_identifier TEXT DEFAULT NULL, emoji_image_short_description TEXT DEFAULT NULL, preview_generation_state INTEGER DEFAULT 0);
CREATE TABLE chat_handle_join (chat_id INTEGER REFERENCES chat (ROWID) ON DELETE CASCADE, handle_id INTEGER REFERENCES handle (ROWID) ON DELETE CASCADE, UNIQUE(chat_id, handle_id));
CREATE TABLE chat_message_join (chat_id INTEGER REFERENCES chat (ROWID) ON DELETE CASCADE, message_id INTEGER REFERENCES message (ROWID) ON DELETE CASCADE, message_date INTEGER DEFAULT 0, PRIMARY KEY (chat_id, message_id));
CREATE TABLE message_attachment_join (message_id INTEGER REFERENCES message (ROWID) ON DELETE CASCADE, attachment_id INTEGER REFERENCES attachment (ROWID) ON DELETE CASCADE, UNIQUE(message_id, attachment_id));
CREATE INDEX chat_message_join_idx_message_date_id_chat_id ON chat_message_join(chat_id, message_date, message_id);
CREATE INDEX chat_message_join_idx_message_id_only ON chat_message_join(message_id);
CREATE INDEX message_idx_handle ON message(handle_id, date);
CREATE INDEX message_idx_date ON message(date);
CREATE INDEX message_idx_thread_originator_guid ON message(thread_originator_guid);
CREATE INDEX message_attachment_join_idx_message_id ON message_attachment_join(message_id);
CREATE INDEX chat_handle_join_idx_handle_id ON chat_handle_join(handle_id);
"""

AB_SCHEMA = """
CREATE TABLE ZABCDRECORD (Z_PK INTEGER PRIMARY KEY, Z_ENT INTEGER, Z_OPT INTEGER, ZFIRSTNAME VARCHAR, ZLASTNAME VARCHAR, ZMIDDLENAME VARCHAR, ZNICKNAME VARCHAR, ZORGANIZATION VARCHAR, ZUNIQUEID VARCHAR);
CREATE TABLE ZABCDPHONENUMBER (Z_PK INTEGER PRIMARY KEY, Z_ENT INTEGER, Z_OPT INTEGER, ZOWNER INTEGER, ZLABEL VARCHAR, ZFULLNUMBER VARCHAR);
CREATE TABLE ZABCDEMAILADDRESS (Z_PK INTEGER PRIMARY KEY, Z_ENT INTEGER, Z_OPT INTEGER, ZOWNER INTEGER, ZLABEL VARCHAR, ZADDRESS VARCHAR);
"""

PHRASES = [
    "lol", "omg", "wait what", "yeah", "no way", "haha ok", "on my way", "running 5 min late",
    "did you see that?", "that's hilarious", "ok sounds good", "what time?", "can't tonight sorry",
    "how was the thing", "it was fine honestly", "send me the link", "brb", "good morning!",
    "night 😴", "same", "I'm so tired", "did you eat yet", "pizza?", "📍 here", "sure!",
    "let me check", "that works", "ugh mondays", "🔥🔥🔥", "thank you!!", "np", "who else is going",
]


def apple_ns(dt):
    return int(dt.timestamp()) * NS - APPLE_EPOCH * NS


def attributed_body(text):
    """Build a typedstream NSAttributedString blob the way Messages stores it."""
    b = text.encode("utf-8")
    ln = bytes([len(b)]) if len(b) < 0x80 else b"\x81" + len(b).to_bytes(2, "little")
    return (b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84\x12NSAttributedString\x00"
            b"\x84\x84\x08NSObject\x00\x85\x92\x84\x84\x84\x08NSString\x01\x94\x84\x01+" + ln + b +
            b"\x86\x84\x02iI\x01\x05\x92\x84\x84\x84\x0cNSDictionary\x00\x94\x84\x01i\x01\x92\x84\x96\x96"
            b"\x1d__kIMMessagePartAttributeName\x86\x92\x84\x84\x84\x08NSNumber\x00\x84\x84\x07NSValue"
            b"\x00\x94\x84\x01*\x84\x99\x99\x00\x86\x86\x86")


class Gen:
    def __init__(self, con):
        self.con = con
        self.rng = random.Random(42)

    def handle(self, ident, service="iMessage"):
        return self.con.execute("INSERT INTO handle (id, country, service) VALUES (?, 'us', ?)",
                                (ident, service)).lastrowid

    def chat(self, style, identifier, members, service="iMessage", display_name=None):
        cid = self.con.execute(
            "INSERT INTO chat (guid, style, state, chat_identifier, service_name, display_name) VALUES (?,?,3,?,?,?)",
            (f"{service};{'+' if style == 43 else '-'};{identifier}", style, identifier, service, display_name)).lastrowid
        for h in members:
            self.con.execute("INSERT INTO chat_handle_join VALUES (?, ?)", (cid, h))
        return cid

    def msg(self, chat, date, handle=0, from_me=0, text=None, null_text=False, body=True, **cols):
        guid = str(uuid.UUID(int=self.rng.getrandbits(128))).upper()
        row = dict(guid=guid, text=None if null_text else text, handle_id=0 if from_me else handle,
                   is_from_me=from_me, date=date, service=cols.pop("service", "iMessage"),
                   attributedBody=attributed_body(text) if (text and body) else None, **cols)
        rid = self.con.execute(f"INSERT INTO message ({','.join(row)}) VALUES ({','.join('?' * len(row))})",
                               list(row.values())).lastrowid
        self.con.execute("INSERT INTO chat_message_join VALUES (?, ?, ?)", (chat, rid, date))
        return guid, rid

    def attach(self, msg_rowid, name, mime, uti, is_sticker=0):
        aid = self.con.execute(
            "INSERT INTO attachment (guid, original_guid, filename, transfer_name, mime_type, uti, is_sticker, total_bytes) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), str(uuid.uuid4()), f"~/Library/Messages/Attachments/ab/11/X/{name}", name, mime, uti,
             is_sticker, self.rng.randint(1000, 4_000_000))).lastrowid
        self.con.execute("INSERT INTO message_attachment_join VALUES (?, ?)", (msg_rowid, aid))
        self.con.execute("UPDATE message SET cache_has_attachments = 1 WHERE ROWID = ?", (msg_rowid,))

    def react(self, chat, target_guid, date, kind, handle=0, from_me=0, emoji=None, quoted="that"):
        verbs = {2000: "Loved", 2001: "Liked", 2002: "Disliked", 2003: "Laughed at", 2004: "Emphasized",
                 2005: "Questioned", 2006: f"Reacted {emoji} to", 1000: "Sticker on", 2007: "Sticker on"}
        verb = verbs.get(kind) or "Removed a reaction from"
        return self.msg(chat, date, handle, from_me, text=f"{verb} “{quoted}”",
                        associated_message_guid=f"p:0/{target_guid}", associated_message_type=kind,
                        associated_message_emoji=emoji)

    def random_chatter(self, chat, people, start, count, mean_gap_s):
        """people: list of handle rowids, 0 means me. Returns list of (guid, date, text)."""
        out, t = [], start
        for _ in range(count):
            t += int(self.rng.expovariate(1 / mean_gap_s) * NS) + NS
            if self.rng.random() < 0.03:
                t += self.rng.randint(1, 3) * 86400 * NS
            who = self.rng.choice(people)
            text = self.rng.choice(PHRASES)
            if self.rng.random() < 0.05:
                text += "\n" + self.rng.choice(PHRASES)
            guid, _ = self.msg(chat, t, handle=who, from_me=int(who == 0), text=text,
                               null_text=self.rng.random() < 0.3)
            out.append((guid, t, text, who))
        return out


def build_addressbook(path):
    con = sqlite3.connect(path)
    con.executescript(AB_SCHEMA)
    people = [
        (1, "Jane", "Doe", None, None, ["+1 (555) 201-0001"], ["Jane.Doe@example.com"]),
        (2, "Bob", "Smith", "Bobby", None, ["555-201-0002"], []),
        (3, "Janet", "Lee", None, None, ["(555) 201-0003"], []),
        (4, "Alex", "Kim", None, "Example Corp", ["+15552010004"], ["alex@example.org"]),
        (5, "Nobody", "Texts", None, None, ["+15552010099"], []),
    ]
    for pk, first, last, nick, org, phones, emails in people:
        con.execute("INSERT INTO ZABCDRECORD (Z_PK, ZFIRSTNAME, ZLASTNAME, ZNICKNAME, ZORGANIZATION) VALUES (?,?,?,?,?)",
                    (pk, first, last, nick, org))
        for p in phones:
            con.execute("INSERT INTO ZABCDPHONENUMBER (ZOWNER, ZLABEL, ZFULLNUMBER) VALUES (?, '_$!<Mobile>!$_', ?)", (pk, p))
        for e in emails:
            con.execute("INSERT INTO ZABCDEMAILADDRESS (ZOWNER, ZLABEL, ZADDRESS) VALUES (?, '_$!<Home>!$_', ?)", (pk, e))
    con.commit()
    con.close()
    with open(os.path.join(OUT, "contacts.vcf"), "w") as f:
        for pk, first, last, nick, org, phones, emails in people:
            f.write(f"BEGIN:VCARD\r\nVERSION:3.0\r\nPRODID:-//Apple Inc.//macOS 15.7.3//EN\r\nN:{last};{first};;;\r\n"
                    f"FN:{first} {last}\r\n")
            if nick:
                f.write(f"NICKNAME:{nick}\r\n")
            if org:
                f.write(f"ORG:{org};\r\n")
            for i, p in enumerate(phones):
                f.write(f"TEL;type=CELL;type=VOICE{';type=pref' if i == 0 else ''}:{p}\r\n")
            for i, e in enumerate(emails, 1):
                f.write(f"item{i}.EMAIL;type=INTERNET;type=pref:{e}\r\nitem{i}.X-ABLabel:_$!<Other>!$_\r\n")
            f.write("PHOTO;ENCODING=b;TYPE=JPEG:/9j/4AAQSkZJRgABAQAAAQABAAD\r\n  2wBDAAgGBgcGBQgHBwcJCQgKDA\r\nEND:VCARD\r\n")


def build_chat_db(path):
    con = sqlite3.connect(path)
    con.executescript(CHAT_SCHEMA)
    g = Gen(con)

    jane_p = g.handle("+15552010001")
    jane_e = g.handle("jane.doe@example.com")
    bob = g.handle("+15552010002")
    janet = g.handle("+15552010003", "SMS")
    alex = g.handle("+15552010004")
    stranger = g.handle("+15559990000", "SMS")

    jane_chat = g.chat(45, "+15552010001", [jane_p])
    jane_email_chat = g.chat(45, "jane.doe@example.com", [jane_e])
    janet_chat = g.chat(45, "+15552010003", [janet], service="SMS")
    ski = g.chat(43, "chat111111", [jane_p, bob, alex], display_name="Ski Trip 🎿")
    unnamed = g.chat(43, "chat222222", [jane_p, janet])
    stranger_chat = g.chat(45, "+15559990000", [stranger], service="SMS")

    start = apple_ns(datetime(2026, 3, 1, 9, 0, 0))

    # Jane: long history across two handles (Messages shows these as one conversation).
    hist = g.random_chatter(jane_chat, [jane_p, 0], start, 560, 3 * 3600)
    hist += g.random_chatter(jane_email_chat, [jane_e, 0], start + 3600 * NS, 60, 30 * 3600)
    for guid, t, text, who in g.rng.sample(hist, 90):
        reactor = 0 if who else jane_p
        g.react(jane_chat, guid, t + 5 * NS, g.rng.choice([2000, 2001, 2003, 2004]), handle=reactor,
                from_me=int(reactor == 0), quoted=text)

    # Deterministic edge cases at the end of the Jane conversation.
    t = max(x[1] for x in hist) + 86400 * NS
    t = apple_ns(datetime.fromtimestamp((t + APPLE_EPOCH * NS) // NS).replace(hour=18, minute=0, second=0))
    q, _ = g.msg(jane_chat, t, jane_p, text="are you coming tonight?")
    g.react(jane_chat, q, t + 3 * NS, 2000, from_me=1, quoted="are you coming tonight?")
    g.react(jane_chat, q, t + 9 * NS, 2003, from_me=1, quoted="are you coming tonight?")  # replaces ❤️
    t += 20 * NS
    g.msg(jane_chat, t, from_me=1, text="yes! what time", thread_originator_guid=q, thread_originator_part="0:0:23")
    t += 61 * NS
    r, _ = g.msg(jane_chat, t, jane_p, text="8ish")
    g.react(jane_chat, r, t + 2 * NS, 2001, from_me=1, quoted="8ish")
    g.react(jane_chat, r, t + 4 * NS, 3001, from_me=1, quoted="8ish")  # removed -> no reaction shown
    # Burst: 0.4s, 0.04s, 0.004s, 0s apart, then a 1.5s gap.
    t += 120 * NS
    for dt, txt in [(0, "wait"), (400_000_000, "actually"), (40_000_000, "hold on"),
                    (4_000_000, "ok"), (0, "same instant"), (1_500_000_000, "back to normal")]:
        t += dt
        g.msg(jane_chat, t, jane_p, text=txt, date_edited=t + 5 * NS if txt == "back to normal" else None)
    t += 300 * NS
    versions = [(t, "I got tix"), (t + 30 * NS, "I got the tickets"), (t + 30 * NS + 250_000_000, "I got the tickets!!")]
    summary = plistlib.dumps({"ec": {"0": [{"d": d, "t": attributed_body(v), "bcg": str(uuid.uuid4()).upper()}
                                           for d, v in versions]}, "ep": [0], "otr": {"0": {"lo": 0, "le": 9}}},
                             fmt=plistlib.FMT_BINARY)
    fire, _ = g.msg(jane_chat, t, from_me=1, text=versions[-1][1], date_edited=versions[-1][0],
                    message_summary_info=summary)
    g.react(jane_chat, fire, t + 86400 * NS, 2004, from_me=1, quoted="I got the tickets!!")  # next day
    g.react(jane_chat, fire, t + 40 * NS, 2006, jane_p, emoji="🔥", quoted="I got the tickets")
    g.react(jane_chat, fire, t + 42 * NS, 1000, jane_p, quoted="I got the tickets")
    t += 90 * NS
    # Unsent the way real databases store it: rp in message_summary_info, unsend time in date_edited.
    g.msg(jane_chat, t, jane_p, text=None, is_empty=1, cache_has_attachments=1, date_edited=t + 9 * NS,
          message_summary_info=plistlib.dumps({"amc": 0, "ust": True, "otr": {"0": {"lo": 0, "le": 1}}, "rp": [0]},
                                              fmt=plistlib.FMT_BINARY))
    t += 60 * NS
    _, m = g.msg(jane_chat, t, jane_p, text="￼Look at this view")
    g.attach(m, "IMG_4821.HEIC", "image/heic", "public.heic")
    t += 15 * NS
    _, m = g.msg(jane_chat, t, jane_p, text="￼￼", null_text=True)
    g.attach(m, "IMG_4822.HEIC", "image/heic", "public.heic")
    g.attach(m, "IMG_4823.MOV", "video/quicktime", "com.apple.quicktime-movie")
    t += 40 * NS
    _, m = g.msg(jane_chat, t, from_me=1, text="￼", is_audio_message=1)
    g.attach(m, "Audio Message.caf", "audio/x-caf", "com.apple.coreaudio-format")
    t += 200 * NS
    g.msg(jane_chat, t, jane_p, text="ok so the plan is:\n1. dinner at 7\n2. show at 8\n3. *maybe* drinks after_")
    t += 30 * NS
    long = ("Honestly this is a really long message that only lives in attributedBody because newer macOS "
            "versions leave the text column NULL — it has émojis 🎉🎟️ and accents to check UTF-8 decoding.")
    g.msg(jane_chat, t, from_me=1, text=long, null_text=True)
    t += 45 * NS
    _, m = g.msg(jane_chat, t, jane_p, text="https://example.com/tickets",
                 balloon_bundle_id="com.apple.messages.URLBalloonProvider")
    g.attach(m, "B1A2.pluginPayloadAttachment", None, "dyn.ah62d4rv4ge80w5pq")
    t += 20 * NS
    g.msg(jane_chat, t, from_me=1, text="HAPPY BIRTHDAY",
          expressive_send_style_id="com.apple.MobileSMS.expressivesend.impact")
    g.msg(jane_chat, t + 5 * NS, from_me=1, text="🎉", expressive_send_style_id="com.apple.messages.effect.CKConfettiEffect")

    # Ski trip group with events and multi-person reactions.
    gt = start + 40 * 86400 * NS
    g.msg(ski, gt, handle=0, from_me=0, text=None, item_type=2, group_title="Ski Trip 🎿")  # how real db stores "You named…"
    gt += 60 * NS
    g.msg(ski, gt, from_me=1, text=None, item_type=1, group_action_type=0, other_handle=alex)
    hist = g.random_chatter(ski, [jane_p, bob, alex, 0], gt, 110, 2 * 3600)
    tgt_guid, tgt_t, tgt_text, _ = hist[-5]
    g.react(ski, tgt_guid, tgt_t + NS, 2000, jane_p, quoted=tgt_text)
    g.react(ski, tgt_guid, tgt_t + 2 * NS, 2000, bob, quoted=tgt_text)
    g.react(ski, tgt_guid, tgt_t + 3 * NS, 2003, from_me=1, quoted=tgt_text)
    g.msg(ski, hist[-1][1] + 60 * NS, bob, text=None, item_type=3, group_action_type=0)

    g.random_chatter(unnamed, [jane_p, janet, 0], start + 10 * 86400 * NS, 20, 6 * 3600)
    g.random_chatter(janet_chat, [janet, 0], start + 5 * 86400 * NS, 30, 12 * 3600)
    con.execute("UPDATE message SET service = 'SMS' WHERE ROWID IN (SELECT message_id FROM chat_message_join WHERE chat_id = ?)",
                (janet_chat,))
    g.random_chatter(stranger_chat, [stranger, 0], start + 50 * 86400 * NS, 5, 600)
    con.commit()
    con.close()


def main():
    os.makedirs(OUT, exist_ok=True)
    for name in ("chat.db", "AddressBook-v22.abcddb", "contacts.vcf"):
        p = os.path.join(OUT, name)
        if os.path.exists(p):
            os.remove(p)
    build_chat_db(os.path.join(OUT, "chat.db"))
    build_addressbook(os.path.join(OUT, "AddressBook-v22.abcddb"))
    print(f"Wrote fake data to {OUT}/")


if __name__ == "__main__":
    main()
