---
name: granola
description: Sync and query Granola meeting transcripts. Use when working with meeting notes, transcripts, attendees, or searching past meetings. Supports syncing from Granola API, listing/filtering meetings, full-text search, and downloading meetings to project folders.
allowed-tools: Bash
---

# Granola Meeting Sync Skill

This skill provides access to your Granola meeting transcripts through a Python CLI optimized for Claude integration.

## When to Use

Claude will use this skill when you mention:
- Granola meetings or meeting transcripts
- Finding or searching past meetings
- Looking up what was discussed in a meeting
- Finding meetings with specific attendees
- Syncing or downloading meeting notes
- Getting meeting context for current work

## Setup

See the [README](README.md) for full installation instructions.

1. **Create venv and install dependencies** (`requirements.txt` lists `requests` + `pycryptodome`):
   ```bash
   cd <skill-dir>
   uv venv && uv pip install -r requirements.txt
   ```

2. **Sign into Granola app** — the CLI needs a signed-in desktop app.

3. **Authenticate the CLI** (see *Authentication* below), then **sync**:
   ```bash
   python <skill-dir>/scripts/granola.py sync
   ```

> **Running commands:** If a `.venv` exists in the skill directory, always use its Python:
> ```bash
> <skill-dir>/.venv/bin/python <skill-dir>/scripts/granola.py <command>
> ```
> Otherwise, fall back to system `python3`.

## Platform Support

**macOS only.**

## Authentication

Granola 7.5x+ encrypts its local auth and keeps the key in a Keychain item only its
own app can read, so the CLI can't read tokens from disk. Instead,
`scripts/refresh_auth.py` captures a fresh token pair from the running app's own
HTTPS traffic via a temporary local mitmproxy and writes a plaintext `supabase.json`
that `granola.py` reads; `granola.py` then self-refreshes the access token.

```bash
python <skill-dir>/scripts/refresh_auth.py
```

Requires `uv` on PATH and the Granola app signed in. It restarts Granola and pops
**one macOS password dialog** (to trust the mitmproxy CA) — approve it; the script
removes the CA trust and disables the proxy again when it finishes.

> **When to re-run:** if a sync fails with `"Granola auth is sealed"` or a token
> refresh error. The access token lasts ~6h and self-refreshes, but the CLI and the
> app share a rotating refresh token from separate stores, so they can eventually
> desync — re-running `refresh_auth.py` re-syncs them. For a conflict-free, longer
> -lived credential, use Granola's official public API key (`grn_…`, Business plan;
> Settings → API) instead — not yet wired into this skill.

## Available Commands

### Sync Meetings

**Default approach:** Check what's already synced, then fetch only what's new:
```bash
# 1. Get the date of the most recent synced meeting
granola.py list --compact --json | python3 -c "import json,sys; d=json.load(sys.stdin); print(d[0]['date'] if d else '')"

# 2. Sync from that date onward
granola.py sync --since <last-synced-date> --json
```

Other sync modes:
```bash
granola.py sync                        # Full incremental sync (skips unchanged, but queries all)
granola.py sync --force                # Re-download all
granola.py sync --quiet                # Quiet mode for scheduled/automated syncs
```

### Scheduling automatic syncs (macOS)

To keep meetings synced in the background, install a **launchd LaunchAgent**. It
runs inside your login session and syncs on login + every few hours, which keeps
the CLI's access token refreshed (the token self-refreshes for as long as syncs
run regularly; let it idle too long and you'll need to re-run `refresh_auth.py` —
see *Authentication*).

The CLI installs and loads the agent for you:

```bash
granola.py install-launchagent          # write, load, and run one sync now
```

This syncs on login and every 3 hours, logging to
`~/Library/Logs/granola-sync.log`. launchd runs any sync missed during
sleep/logout once on wake.

Options:

```bash
granola.py install-launchagent --interval 3600   # custom interval (seconds)
granola.py install-launchagent --log /path/to.log
granola.py install-launchagent --storage /path   # sync to a non-default folder
granola.py install-launchagent --no-run          # load but don't sync immediately
granola.py install-launchagent --uninstall       # unload and remove the agent
```

> **Manual loading:** to inspect or load the agent yourself, run
> `granola.py install-launchagent --no-load` to just write the plist to
> `~/Library/LaunchAgents/`, then `launchctl bootstrap gui/$(id -u) <plist>`.

### List/Filter Meetings
```bash
granola.py list --compact --pretty                              # All meetings
granola.py list --date today --pretty                           # By date
granola.py list --start "last week" --end today --compact       # Date range
granola.py list --title "standup" --compact --pretty            # By title (regex)
granola.py list --participant "john@example.com" --compact      # By participant
```

### Show Meeting Details
```bash
granola.py show MEETING_ID --pretty              # Metadata and notes
granola.py show MEETING_ID --transcript --pretty # Include full transcript
```

### Search Transcripts
```bash
granola.py search "keyword" --pretty             # Search all transcripts
granola.py search "action items" --context 3     # With context lines
```

### Download to Project Folder
```bash
granola.py get MEETING_ID -o ./meetings/
granola.py get ID1 ID2 ID3 -o ./project/context/
```

## Output Format

All commands return JSON for easy parsing. Use `--pretty` for readable output.

- `list --compact`: Returns `[{id, date, title}, ...]`
- `show`: Returns `{metadata, notes, transcript?}`
- `search`: Returns `[{id, title, date, matches: [{line, text, context}]}]`

## Storage Location

Meetings are stored in `~/Documents/granola-meetings/` by default.

Override with:
1. `--storage /path` CLI argument
2. `GRANOLA_SYNC_FOLDER` environment variable
3. `~/.config/granola/config.json`: `{"storage": "/path"}`

## Storage Structure

```
~/Documents/granola-meetings/
  YYYY-MM-DD_Meeting-Title/
    metadata.json    # id, title, dates, people, calendar
    transcript.md    # Formatted markdown transcript
    transcript.json  # Raw transcript data
    document.json    # Full API response
    notes.md         # AI summary (if available)
```

## Semantic Search with qmd (optional)

[qmd](https://github.com/tobi/qmd) is an optional tool for semantic/natural-language search across meetings. Use it when the user asks questions like "what did we decide about X?" where regex keyword matching isn't enough.

### Check if qmd is available
```bash
which qmd
```

### If not installed, recommend to the user
If the user asks a natural-language question about meetings and qmd is not installed, suggest they install it:
```
bun install -g qmd
```
Then set up the index:
```bash
qmd collection add ~/Documents/granola-meetings --name transcripts
qmd embed
```
This is a one-time setup. After syncing new meetings, re-run `qmd embed` to update the index.

### When to use which search
- **Exact keywords or patterns** → `granola.py search "keyword"` (regex, always available)
- **Natural language questions** → `qmd search` (semantic, requires qmd)

### How to search with qmd
```bash
qmd search "what did we decide about pricing?" --json -n 10 -c transcripts
```

The `--json` flag returns structured results. Use `-n` to control result count and `-c transcripts` to scope to the meetings collection.

### Combined workflow
1. Use `qmd search "topic" --json -c transcripts` to find relevant meetings
2. Extract the meeting folder name from the results
3. Use `granola.py show MEETING_ID --transcript --pretty` to get full details

> **Important:** If qmd is not installed, that's fine — all other skill functionality works without it. Only suggest qmd when the user would benefit from semantic search. Don't block on it.

## Token Efficiency Tips

1. Use `list --compact` for minimal output
2. Use date/title filters to narrow results
3. Use `search` before fetching full transcripts
4. Use `show` without `--transcript` first to check if meeting is relevant
