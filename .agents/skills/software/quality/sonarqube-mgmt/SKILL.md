---
name: sonarqube-mgmt
description: Manage SonarQube issues, review vulnerabilities, and automate code quality improvements. Use this when the user asks to review, fix, or monitor SonarQube metrics for a project like Cornerstone.
---

# SonarQube Management Skill

This skill allows you to interact with the SonarQube Web API to fetch, review, and fix code quality issues and security vulnerabilities.

## Core Workflows

### 1. Reviewing Project Issues
To get a summary of open issues for a project (e.g., `DAGENTIC-CornerStone`):

```bash
python scripts/sonarqube_client.py search --project DAGENTIC-CornerStone
```

### 2. Fixing Specific File Issues
If you need to fix issues in a specific file:

1.  **Fetch issues** for that file:
    ```bash
    python scripts/sonarqube_client.py search --component DAGENTIC-CornerStone:path/to/file.py
    ```
2.  **Read the file** content.
3.  **Consult** [references/fix_patterns.md](references/fix_patterns.md) for recommended fix patterns.
4.  **Apply changes** and verify that the logic remains correct.

### 3. Monitoring Project Status
Check the general status of projects:
```bash
python scripts/sonarqube_client.py projects
```

## Environment Configuration
The skill uses the following environment variables if available:
- `SONAR_HOST`: The URL of the SonarQube server (Default: `https://snr.kronosb.com`)
- `SONAR_TOKEN`: Your SonarQube User Token (A default is provided for the current environment)

## Best Practices
- **Prioritize Vulnerabilities**: Always address `VULNERABILITY` and `BUG` types before `CODE_SMELL`.
- **Verify Fixes**: After fixing an issue, ensure you haven't introduced regressions. If a local `sonar-scanner` is available, run it to verify the fix.
- **Explain Changes**: When fixing an issue, briefly explain why the change is necessary (e.g., "Replacing assert with ValueError for production safety").
