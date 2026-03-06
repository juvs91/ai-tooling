# Project Setup Template

## Overview

Template for setting up new projects with Claude Code, Proxy, and MCP integration.

## Prerequisites

- Existing source project with `.claude/settings.json` configuration
- MCP servers configured (see `MCP_VALIDATION_REPORT.md`)
- MCP scripts organized in `mcp-scripts/`

## Quick Setup

### 1. Create Target Directory

```bash
mkdir -p /path/to/new-project
cd /path/to/new-project
```

### 2. Copy Configuration Files

```bash
# Claude Code settings (includes skills, permissions, MCP config)
cp /path/to/source/.claude/settings.json .claude/

# MCP servers configuration
cp /path/to/source/.mcp.json .

# Environment variables
cp /path/to/source/.env .
cp /path/to/source/.cloudsql-env .

# Git ignore rules
cp /path/to/source/.gitignore .
```

### 3. Copy MCP Scripts

```bash
# Copy mcp-scripts directory
cp -r /path/to/source/mcp-scripts ./mcp-scripts
```

### 4. Create README

Document the setup in `README.md` (see template below).

## Project Structure

```
new-project/
├── .claude/
│   ├── settings.json          # Claude Code settings
│   ├── memory/               # Auto-created by Claude
│   └── projects/
│       └── -new-project/
├── .mcp.json               # MCP servers configuration
├── .env                     # Environment variables
├── .cloudsql-env           # CloudSQL environment (optional)
├── mcp-scripts/            # MCP scripts
└── README.md                # Project documentation
```

## .claude/settings.json Structure

```json
{
  "permissions": {
    "allow": ["Bash(command -v:*)", "Bash(node:*)", "..."],
    "deny": []
  },
  "project": {
    "name": "Project Name",
    "code": "PROJCODE",
    "description": "Project description"
  },
  "skills": {
    "skill-name": {
      "description": "Skill description",
      "category": "category",
      "command": "command-or-mcp-tool",
      "documentation": "docs/skill-usage.md"
    }
  },
  "mcpServers": {
    "server-name": {
      "command": "node|npx|bash|uvx",
      "args": [...],
      "env": {...}
    }
  }
}
```

## README.md Template

```markdown
# [Project Name]

## Overview

Brief description of the project.

## Setup

### Components

- **Proxy**: [Proxy configuration]
- **MCP Servers**: [List of servers]
- **Skills**: [List of skills]

### Configuration Files

- `.claude/settings.json` - Claude Code settings
- `.mcp.json` - MCP servers configuration
- `.env` - Environment variables
- `.cloudsql-env` - CloudSQL environment (optional)

### Skills Configured

[List skills with descriptions]

## MCP Scripts

Located in `mcp-scripts/`:
- [List MCP scripts]

## MCP Servers

| Server | Purpose | Status |
|--------|---------|--------|
| [Table of MCP servers] |

## Usage

1. **Open project:**
   ```bash
   cd /path/to/project
   # Open in Claude Code or VS Code
   ```

2. **Verify MCP servers:**
   - Check Claude Code MCP panel
   - Test connections

3. **Use project skills:**
   [List available skills]

## Validation

### MCP Validation
```bash
# Check all MCPs
bash mcp-scripts/check-mcp-status.sh
```

### Proxy Health Check
```bash
# Test proxy
curl http://localhost:8083/health | jq .

# Test stats
curl http://localhost:8083/api/stats | jq .
```

## Troubleshooting

[Project-specific troubleshooting steps]
```

## Common MCP Servers

| MCP | Command | Purpose | Env Variables |
|------|---------|---------|---------------|
| **alloydb** | node | PostgreSQL queries | DB_MAIN_URL |
| **atlassian** | uvx mcp-atlassian | Jira/Confluence/Bitbucket | CONFLUENCE_URL, CONFLUENCE_API_TOKEN, JIRA_URL, JIRA_API_TOKEN |
| **squit** | npx mcp-remote | Legacy SP search | X-API-Key |
| **cloudsql** | bash ./scripts/cloudsql-mcp.sh | CloudSQL wrapper | .cloudsql-env |
| **context7** | npx @upstash/context7-mcp | Documentation search | - |
| **serper** | npx serper-search-scrape-mcp-server | Web search | SERPER_API_KEY |
| **playwright** | npx @executeautomation/playwright-mcp-server | Browser automation | PLAYWRIGHT_BROWSERS |

## Common Scripts

| Script | Purpose | Location |
|---------|---------|-----------|
| **check-mcp-status.sh** | MCP health check | mcp-scripts/ |
| **cloudsql-mcp.sh** | CloudSQL wrapper | mcp-scripts/ |
| **serper-mcp.sh** | Serper launcher | mcp-scripts/ |
| **serper-mcp.py** | Serper Python launcher | mcp-scripts/ |

## References

- [Project Documentation]
- [MCP Validation Report](../MCP_VALIDATION_REPORT.md)
- [MCP Setup Guide](../MCP_SETUP.md)
