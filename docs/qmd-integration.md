# Semantic Search with qmd

[qmd](https://github.com/tobi/qmd) is an optional companion tool that adds semantic search to your synced Granola meetings. While the skill's `search` command uses regex keyword matching, qmd enables natural-language queries using vector embeddings and BM25 ranking.

## Quick Setup

```bash
# 1. Install qmd
bun install -g qmd

# 2. Create collection from your meetings
qmd collection add ~/Documents/granola-meetings --name granola-meetings

# 3. Generate embeddings (one-time, runs locally)
qmd embed

# 4. Search
qmd search "what did we decide about pricing?" --json -n 10 -c granola-meetings
```

## When to Use Which

| Need | Tool | Example |
|------|------|---------|
| Exact keyword match | `granola.py search` | `granola.py search "budget" --pretty` |
| Pattern matching | `granola.py search` | `granola.py search "TODO\|action" --pretty` |
| Natural language question | `qmd search` | `qmd search "what was the timeline decision?"` |
| Find similar discussions | `qmd search` | `qmd search "technical debt priorities"` |

## Keeping the Index Updated

After syncing new meetings, update the qmd index:

```bash
granola.py sync
qmd embed
```

## Key Flags

| Flag | Purpose | Example |
|------|---------|---------|
| `--json` | Machine-readable output | `qmd search "query" --json` |
| `-n <num>` | Limit result count | `qmd search "query" -n 5` |
| `-c <name>` | Scope to collection | `qmd search "query" -c granola-meetings` |
| `--min-score` | Relevance threshold | `qmd search "query" --min-score 0.5` |

## MCP Server (advanced)

qmd provides an MCP server that Claude can use directly:

```json
{
  "mcpServers": {
    "qmd": {
      "command": "qmd",
      "args": ["mcp"]
    }
  }
}
```

With this, Claude can search your meetings semantically without manual CLI commands.

## Tips

- Use qmd for "what/why/how" questions about meetings
- Use regex search for exact keywords or patterns
- `qmd embed` is safe to re-run — it only processes new/changed files
- The `--json` flag is essential for scripted/Claude usage
