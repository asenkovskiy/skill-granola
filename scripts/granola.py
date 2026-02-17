#!/usr/bin/env python3
"""
Granola meeting sync and query CLI.
Works both as a standalone tool (for cron) and a Claude Code skill.

Usage:
    granola.py <command> [options]

Commands:
    sync       Sync all meetings to storage
    list       List/filter meetings
    get        Download specific meeting(s) to a folder
    show       Display a single meeting's details
    search   Full-text search across transcripts
"""

from __future__ import annotations

__version__ = "1.0.0"

import argparse
import json
import os
import re
import shutil
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

# Platform check (macOS only for now)
if sys.platform != "darwin" and "--help" not in sys.argv and "-h" not in sys.argv and "--version" not in sys.argv:
    print(json.dumps({
        "warning": "Unsupported platform",
        "platform": sys.platform,
        "hint": "This tool currently only works on macOS. Auth relies on the Granola desktop app."
    }), file=sys.stderr)

# Check for requests dependency
try:
    import requests
except ImportError:
    print("Error: 'requests' module not installed.", file=sys.stderr)
    print("Install with: uv pip install requests  OR  pip install requests", file=sys.stderr)
    sys.exit(1)

# Constants
SUPABASE_PATH = Path.home() / "Library/Application Support/Granola/supabase.json"
API_BASE = "https://api.granola.ai/v1"
DEFAULT_STORAGE = Path.home() / "Documents/granola-meetings"


# ============================================================================
# Configuration
# ============================================================================

def get_storage_path(override: str | None = None) -> Path:
    """Resolve storage path from: CLI arg > env var > config file > default."""
    if override:
        return Path(override).expanduser()

    if os.getenv("GRANOLA_SYNC_FOLDER"):
        return Path(os.getenv("GRANOLA_SYNC_FOLDER")).expanduser()

    config_path = Path.home() / ".config/granola/config.json"
    if config_path.exists():
        try:
            with open(config_path) as f:
                config = json.load(f)
            if config.get("storage"):
                return Path(config["storage"]).expanduser()
        except (json.JSONDecodeError, IOError):
            pass

    return DEFAULT_STORAGE


# ============================================================================
# Authentication
# ============================================================================

def get_token() -> str:
    """Get access token from Granola's local auth file.

    Reads from ~/Library/Application Support/Granola/supabase.json,
    which the Granola desktop app creates on sign-in.
    Warns on stderr if the token looks expired.
    """
    if not SUPABASE_PATH.exists():
        print(json.dumps({
            "error": "Auth file not found",
            "path": str(SUPABASE_PATH),
            "hint": "Make sure Granola (https://granola.ai) is installed and you're signed in."
        }), file=sys.stderr)
        sys.exit(1)

    with open(SUPABASE_PATH) as f:
        data = json.load(f)

    tokens = json.loads(data.get("workos_tokens", "{}"))
    token = tokens.get("access_token")

    if not token:
        print(json.dumps({
            "error": "No access token found",
            "hint": "Try signing into Granola again."
        }), file=sys.stderr)
        sys.exit(1)

    # Check expiration
    obtained_at = tokens.get("obtained_at", 0) / 1000
    expires_in = tokens.get("expires_in", 0)
    if datetime.now().timestamp() > obtained_at + expires_in:
        print(json.dumps({"warning": "Token may be expired. Open Granola to refresh."}), file=sys.stderr)

    return token


# ============================================================================
# API
# ============================================================================

def api_request(endpoint: str, payload: dict | None = None, token: str | None = None) -> dict | list:
    """Make a POST request to the Granola API. Returns parsed JSON response."""
    if token is None:
        token = get_token()

    response = requests.post(
        f"{API_BASE}/{endpoint}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=payload or {},
    )
    response.raise_for_status()
    return response.json()


def fetch_documents(token: str, limit: int = 500) -> list[dict]:
    """Fetch meeting documents from the API."""
    return api_request("get-documents", {"limit": limit}, token)


def fetch_transcript(token: str, doc_id: str) -> list[dict]:
    """Fetch transcript segments for a single document. Returns [] on failure."""
    try:
        return api_request("get-document-transcript", {"document_id": doc_id}, token)
    except Exception:
        return []


# ============================================================================
# People Helpers
# ============================================================================

def extract_people(people_data: dict | list | None) -> tuple[str, list[str]]:
    """Extract creator name and all participant identifiers from a meeting's people field.

    Returns (creator_name, [all identifiers]) where identifiers are names or emails.
    Handles both dict format (creator + attendees) and list format from the API.
    """
    if not people_data:
        return "Me", []

    creator_name = "Me"
    identifiers: list[str] = []

    if isinstance(people_data, dict):
        creator = people_data.get("creator") or {}
        if creator.get("name"):
            creator_name = creator["name"]
        # Collect all identifiers (name and email) for the creator
        for field in ("name", "email"):
            if creator.get(field):
                identifiers.append(creator[field])

        for att in people_data.get("attendees") or []:
            if isinstance(att, dict):
                for field in ("name", "email"):
                    if att.get(field):
                        identifiers.append(att[field])
            elif isinstance(att, str):
                identifiers.append(att)

    elif isinstance(people_data, list):
        for p in people_data:
            if isinstance(p, dict):
                for field in ("name", "email"):
                    if p.get(field):
                        identifiers.append(p[field])

    return creator_name, identifiers


def get_attendee_names(people_data: dict | list | None) -> list[str]:
    """Get display names for all meeting participants (for transcript headers)."""
    if not people_data:
        return []

    names: list[str] = []
    if isinstance(people_data, dict):
        creator = people_data.get("creator") or {}
        if creator.get("name"):
            names.append(creator["name"])
        for att in people_data.get("attendees") or []:
            if isinstance(att, dict) and att.get("name"):
                names.append(att["name"])
            elif isinstance(att, str):
                names.append(att)
    elif isinstance(people_data, list):
        names = [p.get("name", p.get("email", "Unknown")) for p in people_data if isinstance(p, dict)]

    return names


def matches_participant(people_data: dict | list | None, query: str) -> bool:
    """Check if any participant name or email contains the query string (case-insensitive)."""
    _, identifiers = extract_people(people_data)
    query_lower = query.lower()
    return any(query_lower in ident.lower() for ident in identifiers)


# ============================================================================
# Date Helpers
# ============================================================================

def parse_date(date_str: str) -> date | None:
    """Parse a human-friendly date string.

    Supports: YYYY-MM-DD, "today", "yesterday", "last week", "last month".
    Returns None if unparseable.
    """
    date_str = date_str.lower().strip()
    today = datetime.now().date()

    if date_str == "today":
        return today
    if date_str == "yesterday":
        return today - timedelta(days=1)
    if date_str.startswith("last"):
        parts = date_str.split()
        if len(parts) >= 2:
            if "week" in parts[1]:
                return today - timedelta(days=7)
            if "month" in parts[1]:
                return today - timedelta(days=30)

    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return None


def get_meeting_date(meeting: dict) -> date | None:
    """Extract and parse the meeting's created_at date. Returns None if missing/invalid."""
    created = meeting.get("created_at", "")[:10]
    if not created:
        return None
    try:
        return datetime.strptime(created, "%Y-%m-%d").date()
    except ValueError:
        return None


# ============================================================================
# Filtering
# ============================================================================

def filter_meetings(
    meetings: list[dict],
    *,
    on_date: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    title: str | None = None,
    participant: str | None = None,
) -> list[dict]:
    """Filter meetings by date, date range, title regex, or participant name/email."""
    # Pre-parse date filters once
    target_date = parse_date(on_date) if on_date else None
    target_start = parse_date(start_date) if start_date else None
    target_end = parse_date(end_date) if end_date else None

    results = []
    for m in meetings:
        meeting_date = get_meeting_date(m)

        # Date filters (skip if meeting has no valid date)
        if target_date and (not meeting_date or meeting_date != target_date):
            continue
        if target_start and (not meeting_date or meeting_date < target_start):
            continue
        if target_end and (not meeting_date or meeting_date > target_end):
            continue

        # Title filter
        if title and not re.search(title, m.get("title", ""), re.IGNORECASE):
            continue

        # Participant filter
        if participant and not matches_participant(m.get("people"), participant):
            continue

        results.append(m)

    return results


# ============================================================================
# Transcript Formatting
# ============================================================================

def format_transcript(doc: dict, transcript_data: list | None = None) -> str:
    """Format a meeting document as readable markdown with speaker attribution.

    Handles two transcript formats:
    - Flat segment list (from API transcript endpoint): uses source field for speaker
    - Chapter-based (from document): uses speaker field directly
    """
    lines = []

    lines.append(f"# {doc.get('title') or 'Untitled Meeting'}")
    created = doc.get("created_at") or ""
    lines.append(f"\n**Date:** {created[:10] if created else 'Unknown'}")

    people = doc.get("people")
    creator_name, _ = extract_people(people)
    names = get_attendee_names(people)

    if names:
        lines.append(f"**Attendees:** {', '.join(names)}")

    lines.append("\n---\n")

    transcript = transcript_data or doc.get("transcript") or []

    if not transcript:
        # Fall back to chapter-based format
        for chapter in doc.get("chapters") or []:
            if chapter.get("title"):
                lines.append(f"\n## {chapter['title']}\n")
            for segment in chapter.get("transcript") or []:
                speaker = segment.get("speaker", "Unknown")
                text = segment.get("text", "")
                lines.append(f"**{speaker}:** {text}\n")
    elif isinstance(transcript, list):
        for segment in transcript:
            if not isinstance(segment, dict):
                continue
            # Map source field to speaker name
            source = segment.get("source", "")
            if source == "microphone":
                speaker = creator_name
            elif source == "system":
                speaker = "Other"
            else:
                speaker = segment.get("speaker", "Unknown")

            text = segment.get("text", "")
            if text:
                lines.append(f"**{speaker}:** {text}\n")

    return "\n".join(lines)


# ============================================================================
# Storage
# ============================================================================

def sanitize_filename(name: str, max_length: int = 50) -> str:
    """Sanitize a string for use as a folder name."""
    name = re.sub(r'[<>:"/\\|?*]', "-", name)
    name = re.sub(r"[\s-]+", "-", name)
    name = name.strip("- ")
    if len(name) > max_length:
        name = name[:max_length].rstrip("- ")
    return name or "Untitled"


def make_folder_name(doc: dict) -> str:
    """Generate a folder name like '2025-01-15_Team-Standup' from a document."""
    created = (doc.get("created_at") or "")[:10] or "unknown-date"
    title = sanitize_filename(doc.get("title") or "Untitled")
    return f"{created}_{title}"


def load_meeting_metadata(meeting_dir: Path) -> dict | None:
    """Load metadata.json from a meeting directory. Returns None if missing or corrupt."""
    metadata_file = meeting_dir / "metadata.json"
    if not metadata_file.exists():
        return None
    try:
        with open(metadata_file) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return None


def find_meeting_dir(storage_path: Path, meeting_id: str) -> Path | None:
    """Find a meeting directory by ID. Supports partial ID matching.

    Search order: direct path match → folder name contains ID → metadata.json → document.json.
    """
    if not storage_path.exists():
        return None

    # Direct match (old UUID-style folders)
    direct = storage_path / meeting_id
    if direct.exists():
        return direct

    for item in storage_path.iterdir():
        if not item.is_dir():
            continue

        # Folder name contains the ID
        if meeting_id in item.name or item.name.startswith(meeting_id):
            return item

        # Check metadata.json
        metadata = load_meeting_metadata(item)
        if metadata and metadata.get("id", "").startswith(meeting_id):
            return item

        # Fallback: check document.json
        doc_file = item / "document.json"
        if doc_file.exists():
            try:
                with open(doc_file) as f:
                    doc = json.load(f)
                if doc.get("id", "").startswith(meeting_id):
                    return item
            except (json.JSONDecodeError, IOError):
                pass

    return None


def get_all_meetings(storage_path: Path) -> list[dict]:
    """Load metadata for all meetings in storage, sorted by date (newest first)."""
    meetings = []
    if not storage_path.exists():
        return meetings

    for item in storage_path.iterdir():
        if item.is_dir():
            metadata = load_meeting_metadata(item)
            if metadata:
                meetings.append(metadata)

    meetings.sort(key=lambda m: m.get("created_at", ""), reverse=True)
    return meetings


def save_meeting(doc: dict, storage_path: Path, token: str | None = None,
                 quiet: bool = False, force: bool = False) -> bool:
    """Save a meeting document to disk. Returns True if saved/updated, False if skipped.

    Creates a folder with metadata.json, transcript.md, transcript.json,
    document.json, and optionally notes.md.
    """
    meeting_id = doc.get("id")
    if not meeting_id:
        return False

    existing_dir = find_meeting_dir(storage_path, meeting_id)

    if existing_dir:
        # Skip if unchanged (unless force)
        if not force:
            metadata = load_meeting_metadata(existing_dir)
            if metadata and metadata.get("updated_at") == doc.get("updated_at"):
                return False
        meeting_dir = existing_dir
    else:
        folder_name = make_folder_name(doc)
        meeting_dir = storage_path / folder_name
        if meeting_dir.exists():
            folder_name = f"{folder_name}_{meeting_id[:8]}"
            meeting_dir = storage_path / folder_name

    meeting_dir.mkdir(parents=True, exist_ok=True)

    # Fetch transcript from API if we have a token
    transcript_data = fetch_transcript(token, meeting_id) if token else None

    # Write metadata
    metadata = {
        "id": meeting_id,
        "title": doc.get("title"),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
        "people": doc.get("people"),
        "calendar_event": doc.get("google_calendar_event"),
    }
    with open(meeting_dir / "metadata.json", "w") as f:
        json.dump(metadata, f, indent=2)

    # Write formatted transcript
    with open(meeting_dir / "transcript.md", "w") as f:
        f.write(format_transcript(doc, transcript_data))

    # Write raw transcript
    raw_transcript = transcript_data or doc.get("transcript") or doc.get("chapters", [])
    with open(meeting_dir / "transcript.json", "w") as f:
        json.dump(raw_transcript, f, indent=2)

    # Write full API response
    with open(meeting_dir / "document.json", "w") as f:
        json.dump(doc, f, indent=2)

    # Write notes if available
    notes = doc.get("notes_markdown") or doc.get("notes_plain")
    if notes:
        with open(meeting_dir / "notes.md", "w") as f:
            f.write(notes)

    return True


# ============================================================================
# Commands
# ============================================================================

def cmd_sync(args: argparse.Namespace) -> None:
    """Sync meetings from Granola API to local storage."""
    storage = get_storage_path(args.storage)
    storage.mkdir(parents=True, exist_ok=True)
    token = get_token()

    if not args.quiet:
        print(f"Syncing to {storage}...", file=sys.stderr)

    docs = fetch_documents(token, limit=args.limit or 500)

    if args.since:
        since_date = parse_date(args.since)
        if since_date:
            docs = [d for d in docs if d.get("created_at", "")[:10] >= since_date.isoformat()]

    synced = 0
    skipped = 0

    for i, doc in enumerate(docs, 1):
        title = (doc.get("title") or "Untitled")[:40]

        if save_meeting(doc, storage, token, args.quiet, force=args.force):
            synced += 1
            if not args.quiet:
                print(f"  [{i}/{len(docs)}] Synced: {title}", file=sys.stderr)
        else:
            skipped += 1
            if not args.quiet and not args.json:
                print(f"  [{i}/{len(docs)}] Skipped: {title}", file=sys.stderr)

    result = {"synced": synced, "skipped": skipped, "total": len(docs), "storage": str(storage)}

    if args.json:
        print(json.dumps(result, indent=2 if args.pretty else None))
    elif not args.quiet:
        print(f"\nDone! Synced {synced}, skipped {skipped} (unchanged).", file=sys.stderr)


def cmd_list(args: argparse.Namespace) -> None:
    """List meetings with optional filters. Always outputs JSON."""
    storage = get_storage_path(args.storage)
    meetings = get_all_meetings(storage)

    meetings = filter_meetings(
        meetings,
        on_date=args.date,
        start_date=args.start,
        end_date=args.end,
        title=args.title,
        participant=args.participant,
    )

    if args.compact:
        meetings = [
            {"id": m["id"], "date": m.get("created_at", "")[:10], "title": m.get("title", "Untitled")}
            for m in meetings
        ]

    print(json.dumps(meetings, indent=2 if args.pretty else None))


def cmd_show(args: argparse.Namespace) -> None:
    """Show details of a single meeting by ID (supports partial match)."""
    storage = get_storage_path(args.storage)
    meeting_dir = find_meeting_dir(storage, args.meeting_id)

    if not meeting_dir:
        print(json.dumps({"error": f"Meeting not found: {args.meeting_id}"}))
        sys.exit(1)

    result = {}

    metadata = load_meeting_metadata(meeting_dir)
    if metadata:
        result["metadata"] = metadata

    transcript_md = meeting_dir / "transcript.md"
    if transcript_md.exists() and args.transcript:
        result["transcript"] = transcript_md.read_text()

    notes_md = meeting_dir / "notes.md"
    if notes_md.exists():
        result["notes"] = notes_md.read_text()

    print(json.dumps(result, indent=2 if args.pretty else None))


def cmd_get(args: argparse.Namespace) -> None:
    """Download meeting(s) to a specific folder (from local storage or API)."""
    storage = get_storage_path(args.storage)
    output = Path(args.output).expanduser()
    output.mkdir(parents=True, exist_ok=True)

    token = get_token()
    results = []

    for meeting_id in args.meeting_ids:
        source_dir = find_meeting_dir(storage, meeting_id)

        if source_dir:
            dest_dir = output / source_dir.name
            if dest_dir.exists():
                shutil.rmtree(dest_dir)
            shutil.copytree(source_dir, dest_dir)
            results.append({"id": source_dir.name, "source": "storage", "path": str(dest_dir)})
        else:
            docs = fetch_documents(token)
            doc = next((d for d in docs if d.get("id", "").startswith(meeting_id)), None)
            if doc:
                save_meeting(doc, output, token)
                created_dir = find_meeting_dir(output, doc["id"])
                results.append({"id": doc["id"], "source": "api", "path": str(created_dir)})
            else:
                results.append({"id": meeting_id, "error": "Not found"})

    print(json.dumps(results, indent=2 if args.pretty else None))


def cmd_search(args: argparse.Namespace) -> None:
    """Search across all meeting transcripts using regex."""
    storage = get_storage_path(args.storage)
    pattern = re.compile(args.query, re.IGNORECASE)
    results = []

    for item in storage.iterdir():
        if not item.is_dir():
            continue

        transcript_file = item / "transcript.md"
        if not transcript_file.exists():
            continue

        lines = transcript_file.read_text().split("\n")
        matches = []

        for i, line in enumerate(lines):
            if pattern.search(line):
                start = max(0, i - args.context)
                end = min(len(lines), i + args.context + 1)
                context = "\n".join(lines[start:end])
                matches.append({
                    "line": i + 1,
                    "text": line.strip(),
                    "context": context if args.context > 0 else None,
                })

        if matches:
            metadata = load_meeting_metadata(item)
            results.append({
                "id": item.name,
                "title": metadata.get("title", "Untitled") if metadata else "Untitled",
                "date": metadata.get("created_at", "")[:10] if metadata else "",
                "matches": matches,
            })

    print(json.dumps(results, indent=2 if args.pretty else None))


# ============================================================================
# CLI
# ============================================================================

def main() -> None:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--storage", help="Override storage path")
    common.add_argument("--json", action="store_true", help="JSON output")
    common.add_argument("--pretty", action="store_true", help="Pretty-print JSON")
    common.add_argument("--quiet", action="store_true", help="Suppress progress output")

    parser = argparse.ArgumentParser(description="Granola meeting sync and query CLI")
    parser.add_argument("--version", action="version", version=f"granola {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # sync
    sync_p = subparsers.add_parser("sync", parents=[common], help="Sync meetings from Granola API")
    sync_p.add_argument("--force", action="store_true", help="Re-download all meetings")
    sync_p.add_argument("--since", help="Only sync meetings since date")
    sync_p.add_argument("--limit", type=int, help="Limit number of meetings to sync")
    sync_p.set_defaults(func=cmd_sync)

    # list
    list_p = subparsers.add_parser("list", parents=[common], help="List meetings")
    list_p.add_argument("--date", help="Filter by date (YYYY-MM-DD, today, yesterday)")
    list_p.add_argument("--start", help="Start date for range")
    list_p.add_argument("--end", help="End date for range")
    list_p.add_argument("--title", help="Filter by title (regex)")
    list_p.add_argument("--participant", help="Filter by participant email/name")
    list_p.add_argument("--compact", action="store_true", help="Minimal output (id, date, title)")
    list_p.set_defaults(func=cmd_list)

    # show
    show_p = subparsers.add_parser("show", parents=[common], help="Show meeting details")
    show_p.add_argument("meeting_id", help="Meeting ID (can be partial)")
    show_p.add_argument("--transcript", action="store_true", help="Include full transcript")
    show_p.set_defaults(func=cmd_show)

    # get
    get_p = subparsers.add_parser("get", parents=[common], help="Download meeting(s) to folder")
    get_p.add_argument("meeting_ids", nargs="+", help="Meeting ID(s)")
    get_p.add_argument("--output", "-o", required=True, help="Output folder")
    get_p.set_defaults(func=cmd_get)

    # search
    search_p = subparsers.add_parser("search", parents=[common], help="Search transcripts")
    search_p.add_argument("query", help="Search query (regex)")
    search_p.add_argument("--context", "-C", type=int, default=0, help="Context lines")
    search_p.set_defaults(func=cmd_search)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
