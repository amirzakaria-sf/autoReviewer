from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from pathlib import Path

from app.categories import DetectionResult

logger = logging.getLogger("whipguard.detectors.security")

# gitleaks-style shape matching (plan.md §2's "secret scan (gitleaks-style)")
# -- a small, real set of common secret shapes, not a stub. Static text
# scanning only, so this runs directly on the worktree, no sandbox execution
# needed: it never runs the repo's own code (plan.md §13's sandbox
# requirement is about EXECUTION, not about reading file bytes).
_SECRET_PATTERNS = [
    ("AWS Access Key ID", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("Generic API key/secret assignment", re.compile(r"(?i)(api[_-]?key|secret[_-]?key|access[_-]?token)\s*[:=]\s*['\"][A-Za-z0-9_\-]{20,}['\"]")),
    ("Private key block", re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----")),
]

_SKIP_DIRS = {"node_modules", ".git", "test-results", "playwright-report"}
_SKIP_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2")


def _redacted_finding(relative, line_no: int, label: str) -> str:
    """Never include the matched secret value in evidence that is logged,
    shown, or copied into a GitHub issue body."""
    return f"{relative}:{line_no}: {label}"


class SecurityDetector:
    def run(self, worktree_path: str, path_scope: str = "", base_url: str = "") -> DetectionResult:
        # base_url is accepted for interface parity and ignored: this check
        # reads source, so there is nothing at a deployed url for it to visit.
        # Saying so beats silently accepting a url and doing nothing with it.
        _ = base_url
        root = Path(worktree_path)
        # A scoped run walks one subtree. Resolved against the worktree so
        # a caller cannot point it outside.
        scoped_root = root
        if path_scope:
            candidate = (root / path_scope).resolve()
            if str(candidate).startswith(str(root.resolve())) and candidate.exists():
                scoped_root = candidate
        findings: list[str] = []

        findings.extend(_gitleaks_findings(scoped_root, root))

        for path in scoped_root.rglob("*"):
            if not path.is_file() or path.suffix in _SKIP_SUFFIXES:
                continue
            if any(part in _SKIP_DIRS for part in path.parts):
                continue
            try:
                content = path.read_text(errors="ignore")
            except Exception:
                continue

            relative = path.relative_to(root)
            for label, pattern in _SECRET_PATTERNS:
                for match in pattern.finditer(content):
                    line_no = content[: match.start()].count("\n") + 1
                    findings.append(_redacted_finding(relative, line_no, label))

        # Dedup: gitleaks and the regex path often name the same line.
        findings = list(dict.fromkeys(findings))

        if not findings:
            return DetectionResult(failed=False, assertion_text="No secret-shaped strings found.")

        return DetectionResult(
            failed=True,
            assertion_text="Secret scan found " + str(len(findings)) + " match(es):\n" + "\n".join(findings),
        )


def _gitleaks_findings(scoped_root: Path, worktree_root: Path) -> list[str]:
    """Run gitleaks if the binary is on PATH. Absence is not a failure — the
    regex pass still runs. Values are never copied out of the report."""
    binary = shutil.which("gitleaks")
    if not binary:
        return []
    try:
        result = subprocess.run(
            [
                binary, "detect",
                "--source", str(scoped_root),
                "--no-git",
                "--report-format", "json",
                "--report-path", "/dev/stdout",
                "--no-banner",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except Exception:
        logger.exception("gitleaks failed")
        return []
    # gitleaks exits 1 when it finds leaks; stdout is still JSON.
    raw = result.stdout.strip()
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return []
    findings: list[str] = []
    if not isinstance(payload, list):
        return findings
    for item in payload:
        if not isinstance(item, dict):
            continue
        file_path = item.get("File") or item.get("file") or ""
        line_no = item.get("StartLine") or item.get("line") or 0
        rule = item.get("RuleID") or item.get("Description") or "gitleaks"
        try:
            relative = Path(file_path).resolve().relative_to(worktree_root.resolve())
        except Exception:
            relative = file_path
        findings.append(_redacted_finding(relative, int(line_no), f"gitleaks:{rule}"))
    return findings
