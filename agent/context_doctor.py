"""Context Doctor v1 — session context sanity check (static, advisory-only).

Answers one question: does the agent have CURRENT and SUFFICIENT metadata context to
start/continue work, per the agreed contract:

  1. CONFLICT candidate — AGENT_HANDOVER.md declares a HEAD (section
     ``## 14. LAST VERIFIED``) that differs from the live git HEAD. This means the
     handover document is a candidate OUTDATED, never a project error: warn, continue.
  2. Advisory — a batch mutates files inside the repo while this session has not
     read AGENT_HANDOVER.md (tracked by the EXISTING tools/file_state registry —
     this module never duplicates staleness detection).

Deliberately NOT a second Bouncer (no tool-name decisions), NOT a second Grill Me
(no risk classification of arguments), NOT FileState (no read/write stamps), NOT an
LLM. Everything is pure/cheap: one stat + one small text parse + at most one git
call per session (cached on the agent object), warnings via ``agent._emit_warning``
only. Never blocks; there is no RED state. Activation mirrors the Grill Me pattern:
session-scoped opt-in via the zero-tool marker ``context-doctor`` in
``agent.enabled_toolsets`` — un-armed sessions never import or run anything here.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

DOCTOR_MARKER = "context-doctor"
HANDOVER_NAME = "AGENT_HANDOVER.md"

# Mutation tools this advisory recognizes (names only — Grill Me owns argument risk).
_MUTATION_TOOLS = frozenset({"write_file", "patch"})

# The repo the running code belongs to; the handover/git pair we compare always lives
# here. Not configurable in v1 on purpose (no new config surface).
REPO_ROOT = Path(__file__).resolve().parents[1]


def doctor_enabled(agent: Any) -> bool:
    """Session-scoped gate, same shape as the Grill Me/Bouncer marker."""
    toolsets = getattr(agent, "enabled_toolsets", None) or []
    return DOCTOR_MARKER in toolsets


def _git(root: Path, *args: str) -> Optional[str]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True, text=True, timeout=2.0,
        )
        return proc.stdout.strip() if proc.returncode == 0 else None
    except Exception:
        return None


def parse_handover_declared(handover_path: Path) -> Optional[Dict[str, str]]:
    """Extract ONLY the '## 14. LAST VERIFIED' section facts. No global regex over
    the whole document — historical hashes elsewhere are not conflicts.
    Returns None when file/section/HEAD-declaration is missing (UNKNOWN, never guess)."""
    try:
        text = handover_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = re.search(r"^##\s*\d+\.\s*LAST VERIFIED\b(.*?)(?=^##\s|\Z)", text,
                  re.MULTILINE | re.DOTALL | re.IGNORECASE)
    if not m:
        return None
    section = m.group(1)
    head = re.search(r"\b([0-9a-f]{40})\b", section) or re.search(r"\b([0-9a-f]{12})\b", section)
    if not head:
        return None
    branch = re.search(r"[Bb]ranch:\s*`?([A-Za-z0-9._/-]+)`?", section)
    declared = {"head": head.group(1)}
    if branch:
        declared["branch"] = branch.group(1)
    return declared


def _only_handover_changed_since(root: Path, declared: str) -> Optional[bool]:
    """Freshness rule (TEST 9): committing the handover is itself a commit, so
    declared != current HEAD must NOT be a conflict by itself. The handover is still
    FRESH when the total tree diff declared..HEAD touches nothing but the handover
    document (one or more doc-only commits after the verified checkpoint). ``None``
    when git cannot answer (unknown hash, non-git dir) -> caller stays UNKNOWN/silent."""
    out = _git(root, "diff", "--name-only", declared, "HEAD", "--")
    if out is None:
        return None
    files = [line for line in out.splitlines() if line.strip()]
    return all(f == HANDOVER_NAME for f in files)


def check_handover_conflict(agent: Any, root: Path = REPO_ROOT) -> Tuple[str, str]:
    """(status, detail) with status in PASS / CONFLICT / UNKNOWN.
    Cached once per session on the agent object (no global state, no repeated git)."""
    cached = getattr(agent, "_cd_handover_status", None)
    if cached is not None:
        return cached  # type: ignore[return-value]
    result: Tuple[str, str]
    handover = root / HANDOVER_NAME
    if not handover.exists():
        result = ("UNKNOWN", f"no {HANDOVER_NAME} in {root.name}")
    else:
        declared = parse_handover_declared(handover)
        if declared is None:
            result = ("UNKNOWN", "handover has no parsable LAST VERIFIED HEAD")
        else:
            actual_head = _git(root, "rev-parse", "HEAD")
            if actual_head is None:
                result = ("UNKNOWN", "git HEAD unavailable")
            elif actual_head.startswith(declared["head"]):
                result = ("PASS", "")
            else:
                fresh = _only_handover_changed_since(root, declared["head"])
                if fresh is None:
                    result = ("UNKNOWN", "cannot compare declared HEAD with current HEAD")
                elif fresh:
                    # Only doc commits since the verified checkpoint: normal case of
                    # committing the handover itself (and later doc fixes) — not stale.
                    result = ("PASS", "")
                else:
                    result = (
                        "CONFLICT",
                        f"AGENT_HANDOVER może być nieaktualny — dokument zweryfikowany "
                        f"przy HEAD {declared['head'][:12]}, aktualny HEAD to "
                        f"{actual_head[:12]} i od wtedy zmienił się kod projektu.",
                    )
    agent._cd_handover_status = result
    return result


def session_check(agent: Any, root: Path = REPO_ROOT) -> None:
    """Seam A entry: run once per agent; CONFLICT/UNKNOWN only ever warn on CONFLICT
    (UNKNOWN must stay silent — absence of metadata is not a finding)."""
    if getattr(agent, "_cd_session_checked", False):
        return
    agent._cd_session_checked = True
    status, detail = check_handover_conflict(agent, root)
    if status == "CONFLICT" and not getattr(agent, "_cd_conflict_warned", False):
        agent._cd_conflict_warned = True
        agent._emit_warning(f"⚠️ Context Doctor (CONFLICT): {detail}")


def handover_read_status(known_reads: Sequence[str],
                         handover_path: Path = REPO_ROOT / HANDOVER_NAME) -> str:
    """YES/NO/UNKNOWN against the existing FileStateRegistry view. UNKNOWN when the
    registry is disabled/empty-ambiguous is NOT possible here: the caller passes the
    list only when the guard is active (see advisory_for_batch)."""
    resolved = str(handover_path.resolve())
    return "YES" if resolved in set(known_reads) else "NO"


def batch_mutates_repo(tool_calls: Sequence[Any], root: Path = REPO_ROOT) -> bool:
    """True if any call in the batch writes/patches a path inside *root*. Names and
    path prefix only — no risk judgement (that stays with Grill Me)."""
    import json
    for tc in tool_calls or ():
        try:
            fn = tc.function
            if fn.name not in _MUTATION_TOOLS:
                continue
            args = json.loads(fn.arguments) if isinstance(fn.arguments, str) else dict(fn.arguments or {})
            raw = args.get("path") or args.get("file_path") or args.get("filepath")
            if not raw:
                continue
            if Path(str(raw)).expanduser().resolve().is_relative_to(root):
                return True
        except Exception:
            continue
    return False


def advisory_for_batch(agent: Any, tool_calls: Sequence[Any],
                       task_id: Optional[str],
                       root: Path = REPO_ROOT) -> Optional[str]:
    """Seam C entry: 'mutating the repo without having read the handover' advisory.
    Purely additive — never changes Grill Me's GREEN/YELLOW/RED outcome."""
    handover = root / HANDOVER_NAME
    if not handover.exists() or not task_id:
        return None
    from tools import file_state
    if file_state.guard_disabled():
        return None  # cannot know reads -> NO advisory (never guess)
    known = file_state.known_reads(task_id)
    if handover_read_status(known, handover) == "YES":
        return None
    if not batch_mutates_repo(tool_calls, root):
        return None
    return (f"sesja wykonuje zmianę w repo ({root.name}) bez wcześniejszego odczytu "
            f"{HANDOVER_NAME} — sprawdź aktualny stan projektu.")
