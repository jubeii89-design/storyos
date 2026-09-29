#!/usr/bin/env python3
"""Scan files for credentials before they leave this machine.

Exists because Story OS's API keys were once kept in a shared Google Doc.
Run it on the repo before a commit, and on any doc you export:

    python3 tools/scan_secrets.py .
    python3 tools/scan_secrets.py ~/Downloads/exported-doc.txt

Exit code 1 if anything matched, so it works as a pre-commit hook:

    git config core.hooksPath .githooks

Findings print the file, line number, label, and a masked prefix only --
never the full secret -- so the scanner's own output is safe to paste.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Ordered most-specific first. Each pattern is anchored on a vendor prefix so
# that ordinary prose and base64 blobs don't trip it.
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("Anthropic API key", re.compile(r"sk-ant-(?:api|admin)[0-9]{2}-[A-Za-z0-9_\-]{24,}")),
    ("OpenAI API key", re.compile(r"sk-(?:proj|svcacct|admin)-[A-Za-z0-9_\-]{20,}")),
    ("OpenRouter key", re.compile(r"sk-or-v1-[A-Za-z0-9]{32,}")),
    ("Notion integration token", re.compile(r"(?:ntn_|secret_)[A-Za-z0-9]{32,}")),
    ("Google API key", re.compile(r"AIza[A-Za-z0-9_\-]{35}")),
    ("Google OAuth client secret", re.compile(r"GOCSPX-[A-Za-z0-9_\-]{20,}")),
    ("AWS access key id", re.compile(r"(?:AKIA|ASIA)[A-Z0-9]{16}")),
    ("GitHub token", re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}")),
    ("Hugging Face token", re.compile(r"hf_[A-Za-z0-9]{34,}")),
    ("Slack token", re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}")),
    ("Private key block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----")),
    # Generic: an assignment whose name smells like a secret and whose value is
    # long enough to be one. Deliberately last and deliberately loose.
    (
        "Assigned secret-looking value",
        re.compile(
            r"(?i)\b(?:api[_-]?key|secret|token|password|passwd|bearer)\b\s*[:=]\s*"
            r"['\"]?([A-Za-z0-9_\-]{24,})['\"]?"
        ),
    ),
]

SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    ".pytest_cache", ".ruff_cache", ".mypy_cache", "dist", "build",
}

# Files whose whole job is to document the *shape* of a secret.
ALLOWLIST_NAMES = {".env.example", "scan_secrets.py"}

# A line carrying this marker is intentionally a placeholder or a test fixture.
INLINE_ALLOW = re.compile(r"(?i)#\s*(?:pragma:\s*allowlist|noqa:\s*secret|gitleaks:allow)")

TEXT_SUFFIXES = {
    "", ".txt", ".md", ".py", ".json", ".toml", ".yaml", ".yml", ".ini", ".cfg",
    ".env", ".sh", ".bash", ".zsh", ".js", ".ts", ".html", ".csv", ".tsv",
    ".rst", ".xml", ".sql", ".rb", ".go", ".rs", ".java", ".ipynb",
}


def mask(secret: str) -> str:
    """Show enough to locate the key in a vault, not enough to use it."""
    if len(secret) <= 10:
        return secret[:2] + "*" * (len(secret) - 2)
    return f"{secret[:8]}...{secret[-2:]} ({len(secret)} chars)"


def scan_text(text: str, path: Path) -> list[tuple[int, str, str]]:
    hits: list[tuple[int, str, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if INLINE_ALLOW.search(line):
            continue
        for label, pattern in PATTERNS:
            m = pattern.search(line)
            if not m:
                continue
            # Group 1 when the pattern captures just the value, else whole match.
            value = m.group(1) if m.groups() else m.group(0)
            hits.append((lineno, label, mask(value)))
            break  # one finding per line is enough to make the point
    return hits


def iter_files(root: Path):
    if root.is_file():
        yield root
        return
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.name in ALLOWLIST_NAMES:
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            if path.stat().st_size > 5_000_000:
                continue
        except OSError:
            continue
        yield path


def main() -> int:
    ap = argparse.ArgumentParser(description="Find credentials in files before they leak.")
    ap.add_argument("paths", nargs="*", default=["."], help="files or directories (default: .)")
    ap.add_argument("-q", "--quiet", action="store_true", help="print findings only")
    args = ap.parse_args()

    total = 0
    scanned = 0
    for raw in args.paths:
        root = Path(raw).expanduser()
        if not root.exists():
            print(f"scan_secrets: no such path: {root}", file=sys.stderr)
            return 2
        for path in iter_files(root):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            scanned += 1
            for lineno, label, masked in scan_text(text, path):
                total += 1
                print(f"{path}:{lineno}: {label}: {masked}")

    if total:
        print(
            f"\n{total} finding(s) in {scanned} file(s). "
            "Rotate anything real, then remove it from the file. "
            "Placeholders can be marked with '# pragma: allowlist'.",
            file=sys.stderr,
        )
        return 1
    if not args.quiet:
        print(f"clean: no credentials found in {scanned} file(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
