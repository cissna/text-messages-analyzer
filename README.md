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

## Trying it without real data

```sh
python3 make_sample_db.py   # writes a fake sample/chat.db + sample/contacts.vcf
python3 chatview.py jane --db sample/chat.db --contacts sample/contacts.vcf --out sample/out
```

The fake database mirrors the macOS 15 schema and covers the edge cases (edits, unsends,
reaction changes, near-simultaneous messages, attachments, group events).
