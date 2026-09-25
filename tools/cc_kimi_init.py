#!/usr/bin/env python3
"""cc-kimi-init — Bootstrap a project to run Claude Code against Kimi for Coding.

Applies the four per-project requirements discovered in ADR-0059:

1. ``.claude/settings.local.json`` from the repo template (env block + central
   ``apiKeyHelper``). The API key itself is NOT copied into the project; the
   helper points to the central key store (``~/.config/cc-kimi/``).
2. Central key store ``~/.config/cc-kimi/api-key`` (0600) + generic
   ``key-helper.sh``. Seeded with ``--key-file`` when provided.
3. ``.gitignore`` entries for the local settings / legacy project helper.
4. Workspace trust (``hasTrustDialogAccepted``) in ``~/.claude.json``.

CLI contract (agent-facing):
    python3 tools/cc_kimi_init.py [TARGET_DIR] [--key-file PATH] [--force]
                                  [--no-trust] [--dry-run]

    TARGET_DIR        Project to initialize (default: cwd). Must exist.
    --key-file PATH   Seed/replace the central API key from this file.
    --force           Overwrite an existing .claude/settings.local.json.
    --no-trust        Skip writing hasTrustDialogAccepted to ~/.claude.json.
    --dry-run         Report what would change; write nothing.

Environment overrides (for tests / non-standard layouts):
    CC_KIMI_CONFIG_DIR   Central config dir (default: ~/.config/cc-kimi).
    CC_CLAUDE_JSON       Path to the claude.json state file
                         (default: ~/.claude.json).

Strict output contract: a single JSON object on stdout describing every action
taken (or that would be taken under --dry-run). All human-readable diagnostics
go to stderr. Exit code 0 on success, 2 on usage/validation errors.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import sys
from pathlib import Path
from typing import Any

TEMPLATE_RELATIVE = Path("templates/claude/settings.local.json.kimi")
GITIGNORE_ENTRIES = [".claude/settings.local.json", ".claude/kimi-key-helper.sh"]
KEY_HELPER_SCRIPT = "#!/bin/sh\n# Prints the Kimi for Coding API key (cc-kimi-init).\ncat \"$(dirname \"$0\")/api-key\"\n"


class InitError(Exception):
    """Usage/validation error (maps to exit code 2)."""


def _log(message: str) -> None:
    print(f"[cc-kimi-init] {message}", file=sys.stderr)


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _config_dir() -> Path:
    return Path(os.environ.get("CC_KIMI_CONFIG_DIR", Path.home() / ".config" / "cc-kimi"))


def _claude_json_path() -> Path:
    return Path(os.environ.get("CC_CLAUDE_JSON", Path.home() / ".claude.json"))


def _expand_api_key_helper(text: str, home: Path) -> str:
    """Resolve the literal '~' prefix in the template's apiKeyHelper path.

    The CLI runs apiKeyHelper through a shell where '~' at command position is
    expanded, but an absolute path removes any doubt across versions/entrypoints.
    """
    needle = '"apiKeyHelper": "~/'
    replacement = f'"apiKeyHelper": "{home}/'
    return text.replace(needle, replacement)


def ensure_central_store(key_file: Path | None, dry_run: bool) -> dict[str, Any]:
    """Create ~/.config/cc-kimi (key store + helper script). Idempotent."""
    cfg_dir = _config_dir()
    key_path = cfg_dir / "api-key"
    helper_path = cfg_dir / "key-helper.sh"
    actions: list[str] = []

    if key_file is not None:
        if not key_file.is_file():
            raise InitError(f"--key-file not found: {key_file}")
        if not dry_run:
            cfg_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(key_file, key_path)
            key_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        actions.append(f"key seeded from {key_file}")

    if not dry_run:
        cfg_dir.mkdir(parents=True, exist_ok=True)
        if not helper_path.exists():
            helper_path.write_text(KEY_HELPER_SCRIPT)
            helper_path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
            actions.append("key-helper.sh created")
        if not key_path.exists():
            actions.append("key store MISSING — pass --key-file or copy the key manually")
    else:
        if not helper_path.exists():
            actions.append("key-helper.sh would be created")
        if not key_path.exists() and key_file is None:
            actions.append("key store MISSING — pass --key-file")

    return {"config_dir": str(cfg_dir), "key_present": key_path.exists() or key_file is not None,
            "actions": actions}


def install_settings(target: Path, force: bool, dry_run: bool) -> dict[str, Any]:
    template = _repo_root() / TEMPLATE_RELATIVE
    if not template.is_file():
        raise InitError(f"template not found: {template}")
    destination = target / ".claude" / "settings.local.json"
    rendered = _expand_api_key_helper(template.read_text(), Path.home())

    if destination.exists() and not force:
        return {"path": str(destination), "written": False,
                "reason": "exists (use --force to overwrite)"}
    if not dry_run:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(rendered)
        destination.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return {"path": str(destination), "written": True}


def update_gitignore(target: Path, dry_run: bool) -> dict[str, Any]:
    gitignore = target / ".gitignore"
    existing = gitignore.read_text().splitlines() if gitignore.is_file() else []
    missing = [e for e in GITIGNORE_ENTRIES if e not in [line.strip() for line in existing]]
    if missing and not dry_run:
        with gitignore.open("a") as fh:
            if existing and existing[-1].strip():
                fh.write("\n")
            fh.write("# Claude Code local settings (contain API key)\n")
            for entry in missing:
                fh.write(f"{entry}\n")
    return {"path": str(gitignore), "added": missing}


def set_trust(target: Path, dry_run: bool) -> dict[str, Any]:
    claude_json = _claude_json_path()
    resolved = str(target.resolve())
    if not claude_json.is_file():
        return {"path": str(claude_json), "trusted": False, "reason": "file not found"}
    if not dry_run:
        data = json.loads(claude_json.read_text())
        data.setdefault("projects", {}).setdefault(resolved, {})["hasTrustDialogAccepted"] = True
        claude_json.write_text(json.dumps(data, indent=2))
    return {"path": str(claude_json), "trusted": True, "project": resolved}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bootstrap a project for Claude Code + Kimi for Coding.")
    parser.add_argument("target_dir", nargs="?", default=".", help="Project directory (default: cwd)")
    parser.add_argument("--key-file", type=Path, default=None, help="Seed the central key store from this file")
    parser.add_argument("--force", action="store_true", help="Overwrite existing settings.local.json")
    parser.add_argument("--no-trust", action="store_true", help="Skip writing trust to ~/.claude.json")
    parser.add_argument("--dry-run", action="store_true", help="Report changes without writing")
    args = parser.parse_args(argv)

    try:
        target = Path(args.target_dir).resolve()
        if not target.is_dir():
            raise InitError(f"target directory not found: {target}")

        result: dict[str, Any] = {
            "tool": "cc-kimi-init",
            "target_dir": str(target),
            "dry_run": args.dry_run,
            "central_store": ensure_central_store(args.key_file, args.dry_run),
            "settings": install_settings(target, args.force, args.dry_run),
            "gitignore": update_gitignore(target, args.dry_run),
            "trust": {"skipped": True} if args.no_trust else set_trust(target, args.dry_run),
        }
        print(json.dumps(result, indent=2))
        return 0
    except InitError as exc:
        print(json.dumps({"tool": "cc-kimi-init", "error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
