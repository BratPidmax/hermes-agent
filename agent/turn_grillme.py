"""Grill Me v1: static, second-opinion risk check of a validated tool-call batch.

Answers "the agent MAY use these tools — does the planned action carry obvious,
unneeded risk?" one batch at a time, at seam C (after ``validate_tool_calls`` and
post-call guardrails, immediately before ``agent._execute_tool_calls``). It never
changes ``valid_tool_names``, never dispatches, never uses the network or an LLM.

Activation is per-session and opt-in, mirroring the Bouncer scoped gate: the
``"grillme"`` toolset marker (a zero-tool entry in ``toolsets.TOOLSETS``) must
appear in ``agent.enabled_toolsets``. The marker resolves to no tools — its
only effect is arming this layer. Normal sessions never list it and never pay
for the classifier (one membership test off the hot path).

Levels (execution priority, not a risk ranking): RED > YELLOW > GREEN.
"""
from __future__ import annotations

import json
import re
from contextlib import suppress
from typing import Any, Dict, Iterable, List, Sequence, Tuple

GRILLME_MARKER = "grillme"

# Read-only fast path: single source of truth in model_tools plus session-local
# read-only conveniences. Anything not listed and not mutating defaults to YELLOW.
_EXTRA_SAFE_TOOLS = frozenset({
    "skills_list", "session_search", "web_search", "web_extract", "vision_analyze",
})

_MUTATING_PATH_TOOLS = frozenset({"write_file", "patch"})

# Secrets are judged by the PATH FIELD only — never by searching the whole
# arguments blob (reading ".env" or writing a doc that mentions "rm -rf"
# must not look dangerous).
_SECRET_PATH_RE = re.compile(
    r"(\.env(\.|$)|\.ssh/|id_rsa|id_ed25519|\.pem$|\.key$|credentials|secret)", re.IGNORECASE)
_CONFIG_PATH_RE = re.compile(
    r"(config\.ya?ml$|\.git/|settings\.json$|pyproject\.toml$|Cargo\.toml$|package\.json$)")

# Agent-home / system-state targets (verified in Test 3).
_HOME_STATE_RE = re.compile(
    r"(/\.hermes/(config|state\.db|\.env)|\bflatpak\b|systemctl|reboot\b)")

# Minimal extension over tools.approval_detection.detect_dangerous_command for
# the gaps measured in Test 3 (shred, truncate, disk writes, git history ops).
_EXTRA_DESTRUCTIVE_RE = re.compile(
    r"(\bdd\b.*\bof=|shred\b|truncate\b.*-s\s*0|>\s*/dev/(sd|nvme)|mkfs\b|>\s*\S+\.(db|sqlite)\b"
    r"|\bmv\b.*\s/dev/null\b|\bgit\s+(clean\b|reset\s+--hard)|git\s+push\b.*--force"
    r"|filter-branch|filter-repo|history\s*--all)",
    re.IGNORECASE)

_QUOTED_RE = re.compile(r'''("[^"]*"|'[^']*')''')


def grillme_enabled(agent: Any) -> bool:
    """Scoped session gate, conceptually identical to the Bouncer toolset gate."""
    return GRILLME_MARKER in (getattr(agent, "enabled_toolsets", None) or [])


def _green_tools() -> frozenset:
    from model_tools import _READ_SEARCH_TOOLS  # late import avoids the root-module cycle
    return frozenset(_READ_SEARCH_TOOLS) | _EXTRA_SAFE_TOOLS


def _dangerous_command(command: str) -> bool:
    """Existing approval detection + quote-mask demotion + Test-3 gap patterns.

    ``echo 'rm -rf is dangerous'`` is a mention, not a deletion: a pattern that
    disappears when quoted spans are masked never escalates on its own.
    """
    try:
        from tools.approval_detection import detect_dangerous_command
        if detect_dangerous_command(command)[0]:
            if not detect_dangerous_command(_QUOTED_RE.sub("''", command))[0]:
                return False
            return True
    except Exception:
        pass  # detection unavailable: fall back to the local gap patterns only
    return bool(_EXTRA_DESTRUCTIVE_RE.search(command))


def _arg_dict(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        with suppress(Exception):
            parsed = json.loads(raw or "{}")
            if isinstance(parsed, dict):
                return parsed
    return {}


def _first_str(args: Dict[str, Any], *keys: str) -> str:
    for k in keys:
        v = args.get(k)
        if isinstance(v, str) and v:
            return v
    return ""


def classify_batch(tool_calls: Sequence[Any]) -> Tuple[bool, str, Tuple[str, ...]]:
    """One deterministic classification per batch.

    Returns ``(triggered, level, reasons)`` with ``level`` in
    ``{"GREEN", "YELLOW", "RED"}``. Pure: no I/O, no execution, no model call.
    """
    reasons: List[Tuple[str, str]] = []  # (level, reason)
    green = _green_tools()
    mutation_paths: set = set()
    mutation_count = 0
    for tc in tool_calls:
        name = tc.function.name
        args = _arg_dict(tc.function.arguments)
        if name in green:
            continue
        if name == "terminal":
            command = _first_str(args, "command") or json.dumps(args, ensure_ascii=False)
            if _dangerous_command(command):
                reasons.append(("RED", "destructive command"))
            elif _HOME_STATE_RE.search(command):
                reasons.append(("RED", "touches agent home or system state"))
            else:
                reasons.append(("YELLOW", "command execution"))
            continue
        if name in _MUTATING_PATH_TOOLS:
            path = _first_str(args, "path", "file_path")
            if _SECRET_PATH_RE.search(path):
                reasons.append(("RED", "writes a secret/credential path"))
            elif _CONFIG_PATH_RE.search(path):
                reasons.append(("YELLOW", "modifies a config file"))
            else:
                reasons.append(("YELLOW", "file mutation"))
            mutation_count += 1
            if path:
                mutation_paths.add(path)  # only paths actually present in args
            continue
        reasons.append(("YELLOW", f"opaque tool '{name}'"))  # execute_code/delegate_task/unknowns
    # Batch escalation (Test-3 verified thresholds): many distinct files is a
    # blast-radius RED; several mutations stays YELLOW (write_file is not per se RED).
    if len(mutation_paths) >= 8:
        reasons.append(("RED", f"bulk blast radius: {len(mutation_paths)} files"))
    elif mutation_count >= 3:
        reasons.append(("YELLOW", f"batch mutation count: {mutation_count}"))
    levels = {lvl for lvl, _ in reasons}
    all_reasons = tuple(sorted({r for _, r in reasons}))
    if "RED" in levels:
        return True, "RED", tuple(sorted({r for lvl, r in reasons if lvl == "RED"})) or all_reasons
    if "YELLOW" in levels:
        return True, "YELLOW", all_reasons
    return False, "GREEN", ()
