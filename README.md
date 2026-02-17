# skill-granola

A [Claude Code](https://claude.ai/code) skill that syncs and queries [Granola](https://granola.ai) meeting transcripts. Gives Claude access to your meeting notes, attendees, and transcripts through a Python CLI with structured JSON output.

## Features

- Incremental sync from Granola API to local disk
- Full-text regex search across all meeting transcripts
- Filter by date, title, or participant
- Download specific meetings into project folders
- JSON output optimized for Claude Code integration
- Uses Granola app's existing auth (no API keys to manage)

## Prerequisites

- **macOS** (auth relies on the Granola desktop app's local storage)
- **Python 3.8+**
- **[Granola](https://granola.ai)** desktop app installed and signed in
- **[Claude Code](https://claude.ai/code)** CLI

## Installation

### Personal (all projects)

```bash
git clone https://github.com/YOUR-USERNAME/skill-granola.git ~/.claude/skills/granola
cd ~/.claude/skills/granola
uv venv && uv pip install -r requirements.txt
```

### Project-specific

```bash
mkdir -p .claude/skills
git clone https://github.com/YOUR-USERNAME/skill-granola.git .claude/skills/granola
cd .claude/skills/granola
uv venv && uv pip install -r requirements.txt
```

### Verify

```bash
~/.claude/skills/granola/.venv/bin/python ~/.claude/skills/granola/scripts/granola.py --version
```

## Quick Start

1. **Sync your meetings:**
   ```bash
   ~/.claude/skills/granola/.venv/bin/python ~/.claude/skills/granola/scripts/granola.py sync
   ```

2. **Ask Claude naturally:**
   - "What meetings did I have today?"
   - "Find the standup where we discussed the migration"
   - "Search my meetings for action items about the budget"
   - "Download yesterday's planning meeting into this project"

3. **Or use the CLI directly:**
   ```bash
   # List today's meetings
   granola.py list --date today --compact --pretty

   # Search transcripts
   granola.py search "action items" --context 2 --pretty

   # Show a specific meeting
   granola.py show MEETING_ID --transcript --pretty
   ```

## Configuration

Meetings sync to `~/Documents/granola-meetings/` by default. Override with:

| Method | Example |
|--------|---------|
| CLI flag | `--storage /path/to/meetings` |
| Env var | `GRANOLA_SYNC_FOLDER=/path/to/meetings` |
| Config file | `~/.config/granola/config.json` with `{"storage": "/path"}` |

## Storage Structure

```
~/Documents/granola-meetings/
  2025-01-15_Team-Standup/
    metadata.json    # ID, title, dates, attendees, calendar event
    transcript.md    # Human-readable markdown transcript
    transcript.json  # Raw transcript data from API
    document.json    # Full API response
    notes.md         # AI-generated summary (if available)
```

## Auto-Sync with Cron

```bash
# Sync every hour (add to crontab -e)
0 * * * * ~/.claude/skills/granola/.venv/bin/python ~/.claude/skills/granola/scripts/granola.py sync --quiet
```

## Optional: Semantic Search with qmd

[qmd](https://github.com/tobi/qmd) adds semantic/natural-language search on top of the synced meetings. While this skill provides regex-based keyword search, qmd enables queries like "what did we decide about pricing?" using vector embeddings.

See [docs/qmd-integration.md](docs/qmd-integration.md) for setup instructions.

## How Auth Works

The skill reads authentication tokens from `~/Library/Application Support/Granola/supabase.json`, which the Granola desktop app creates when you sign in. Tokens expire after ~6 hours — just open Granola to refresh them. No API keys or manual token management needed.

## Troubleshooting

| Problem | Fix |
|---------|-----|
| "Auth file not found" | Sign into the Granola desktop app |
| "Token may be expired" | Open Granola to refresh (tokens last ~6 hours) |
| "requests module not found" | Run `uv pip install requests` in the skill's venv |
| Empty search results | Run `sync` first to download meetings |

## License

MIT
