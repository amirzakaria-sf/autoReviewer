from __future__ import annotations

import re
from pathlib import Path

from app.categories import DetectionResult

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
                    findings.append(f"{relative}:{line_no}: {label}: {match.group(0)[:60]}")

        if not findings:
            return DetectionResult(failed=False, assertion_text="No secret-shaped strings found.")

        return DetectionResult(
            failed=True,
            assertion_text="Secret scan found " + str(len(findings)) + " match(es):\n" + "\n".join(findings),
        )
