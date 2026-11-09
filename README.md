# text-messages-analyzer

Export an iMessage conversation from macOS's `chat.db` as clean, AI-pastable markdown.

Search for a contact, group name, phone number, or email (case-insensitive), pick a
conversation, and a local HTML page opens showing the most recent messages with a
**Copy markdown** button. Press `y` in the terminal to load older messages; any other
key quits and leaves the page open. Pure Python standard library, no server.

The markdown includes:

- Timestamps with day headers, adding fractional seconds only when needed to tell adjacent messages apart
- Edit history (Original message, 1st Edit, …) with timestamps
- A reaction timeline, including reactions that were later removed
- Inline replies, unsent messages, attachments, voice messages, send effects, and group events (renames, adds, leaves)

## Setup

Requires macOS and Python 3.9+. Put two files in this folder (both are gitignored):

### 1. `chat.db`: your messages

Messages lives in `~/Library/Messages/chat.db`. macOS protects that folder, so either:

- **Copy it here** (a snapshot): in Finder, press ⌘⇧G, go to `~/Library/Messages`, and copy
  `chat.db` (plus `chat.db-wal` and `chat.db-shm` if present, which hold the newest messages) into this folder.
- **Or read it live**: give your terminal app Full Disk Access (System Settings → Privacy &
  Security → Full Disk Access), then run with `--db ~/Library/Messages/chat.db`.

### 2. A `.vcf` contacts export: names for phone numbers

`chat.db` only stores phone numbers and emails, not names. Open the **Contacts** app, select
all contacts (⌘A), then **File → Export → Export vCard…**, and save the `.vcf` into this folder.
Any `*.vcf` file here is picked up automatically.

## Usage

```sh
python3 chatview.py                 # prompts for a name
python3 chatview.py "jane"          # search directly
python3 chatview.py jane --n 500    # 500 messages per page (default 200)
python3 chatview.py jane --me Isaac # label your messages "Isaac" instead of "Me"
```

Viewer files are written to `~/.chat-viewer/`, outside the repo.

## Top chats

`top_chats.py` ranks the people you text most and writes an HTML page; click a row to see
the me-vs-them split (and, with `--gc`, every chat you've messaged in).

```sh
python3 top_chats.py                          # top 50 by 1:1 message count
python3 top_chats.py --n 100                  # longer list
python3 top_chats.py --gc                     # also count group chats you share with each person
python3 top_chats.py --gc conservative        # ...but only messages directly after the other person's
python3 top_chats.py --days 31                # only the last 31 days
python3 top_chats.py --per-day --min-span 7   # messages per day, ignoring histories shorter than a week
```

- **Counted:** text messages only (no reactions, edit versions, or group events). Unsent messages still count.
- **`--gc`:** counts all of your messages and all of theirs in each group you both belong to.
  **`--gc conservative`** counts only a message whose immediately preceding message in the group came from
  the other person, so a run of several messages after yours counts once.
- **`--per-day`:** divides each count by the days from your first to your last counted message with that
  person (both days included) and ranks by that rate. With `--days`, the span is measured within the window.
  Add `--min-span N` (only allowed with `--per-day`) to skip people whose span is under N days, so a
  one-day burst of texts doesn't top the list.

## Daily text chart

`daily_chats.py` opens a self-contained HTML stacked bar chart. Each bar is one local
calendar day; each color is a person, combining texts you sent and received.

```sh
python3 daily_chats.py                         # last 31 days, including today
python3 daily_chats.py --days 90               # last 90 calendar days
python3 daily_chats.py --days -1               # all history through today
python3 daily_chats.py --gc                    # attribute shared group messages to each person
python3 daily_chats.py --gc conservative       # group messages directly after the other person's
python3 daily_chats.py --gc separated          # each group is its own series
python3 daily_chats.py --min-messages 10       # require one day with at least 10 sent + received
```

- Without `--gc`, only 1:1 chats count. `--gc` (or `--gc normal`) and
  `--gc conservative` use the same attribution as Top chats. In normal mode, a group
  text you send counts for each member, so stacked totals include those repeated counts.
- `--gc separated` counts each group message once, under the group's name (or its
  members' names for unnamed groups), separately from your 1:1 conversations.
- `--min-messages` defaults to **5**. People without at least one day meeting that
  combined sent + received count **within the selected window** go into **Other**.
  Separated groups follow the same rule. Use `--min-messages 0` to show everyone.
- Hover over a segment for an instant tooltip with the name, date, and sent/received
  counts. Double-click a segment to highlight that person across all days; double-click
  again, press Escape, or use **Clear highlight** to reset. Legend buttons also highlight.
- Days without messages are included. Long histories scroll horizontally.

The script uses the same message exclusions, contact lookup, `--db`, `--contacts`,
`--me`, and `--out` options as the other viewers. Pages go to `~/.chat-viewer/` by
default and work offline with no additional dependencies. Add `--no-open` to generate
the page without opening your browser.

## Trying it without real data

```sh
python3 make_sample_db.py   # writes a fake sample/chat.db + sample/contacts.vcf
python3 chatview.py jane --db sample/chat.db --contacts sample/contacts.vcf --out sample/out
python3 daily_chats.py --db sample/chat.db --contacts sample/contacts.vcf --days -1 --gc separated --out sample/out
```

The fake database mirrors the macOS 15 schema and covers the edge cases (edits, unsends,
reaction changes, near-simultaneous messages, attachments, group events).
