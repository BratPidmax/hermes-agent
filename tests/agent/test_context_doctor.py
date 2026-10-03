"""Context Doctor v1 contract tests — static, advisory-only, never blocks.

Freshness rule (TEST 9): declared HEAD from '## 14. LAST VERIFIED' vs live git HEAD;
a divergence covered ONLY by handover-doc commits is fresh (PASS), code divergence is
CONFLICT, anything git cannot answer is UNKNOWN (silent). No LLM, no DB, no repo
writes — fixtures build throwaway git repos under pytest tmp_path.
"""
from __future__ import annotations

import json
import os
import subprocess
import types
from pathlib import Path

import pytest

from agent.context_doctor import (
    DOCTOR_MARKER,
    HANDOVER_NAME,
    advisory_for_batch,
    batch_mutates_repo,
    check_handover_conflict,
    doctor_enabled,
    handover_read_status,
    parse_handover_declared,
    session_check,
)


class FakeAgent:
    def __init__(self, toolsets=(DOCTOR_MARKER,)):
        self.enabled_toolsets = list(toolsets)
        self.warnings = []

    def _emit_warning(self, msg):
        self.warnings.append(msg)


def call(name, args):
    return types.SimpleNamespace(function=types.SimpleNamespace(
        name=name, arguments=json.dumps(args)))


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, check=True).stdout.strip()


def make_doc(repo: Path, declared_head: str | None):
    (repo / HANDOVER_NAME).write_text(
        "# AGENT_HANDOVER\n\n## 13. X\n\n## 14. LAST VERIFIED\n\n"
        + (f"- HEAD verified: `{declared_head}`\n- Branch: `main`\n" if declared_head else "")
        + "## end\n",
        encoding="utf-8",
    )


def init_repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "t@t")
    git(tmp_path, "config", "user.name", "t")
    (tmp_path / "code.py").write_text("v1\n", encoding="utf-8")
    git(tmp_path, "add", "code.py")
    git(tmp_path, "commit", "-qm", "code")
    return tmp_path


def commit_doc(tmp_path: Path, declared_head: str) -> str:
    make_doc(tmp_path, declared_head)
    git(tmp_path, "add", HANDOVER_NAME)
    git(tmp_path, "commit", "-qm", "docs: handover")
    return git(tmp_path, "rev-parse", "HEAD")


def commit_code(tmp_path: Path) -> str:
    import time
    (tmp_path / "code.py").write_text(f"v-{time.time_ns()}\n", encoding="utf-8")
    git(tmp_path, "add", "code.py")
    git(tmp_path, "commit", "-qm", "feat: more code")
    return git(tmp_path, "rev-parse", "HEAD")


HEAD_FULL = "a" * 40


# ---- gate -----------------------------------------------------------------
def test_gate_off_by_default():
    assert doctor_enabled(FakeAgent(toolsets=["hermes-cli"])) is False
    assert doctor_enabled(FakeAgent()) is True
    assert doctor_enabled(types.SimpleNamespace(enabled_toolsets=None)) is False


# ---- 3.1: declared == live HEAD → PASS -------------------------------------
def test_declared_equals_current_pass(tmp_path):
    repo = init_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    make_doc(repo, head)  # uncommitted doc declaring the current HEAD (exact match)
    status, _ = check_handover_conflict(FakeAgent(), repo)
    assert status == "PASS"


# ---- 3.2/3.5: HEAD is the handover commit → NO false conflict ---------------
def test_handover_commit_itself_not_conflict(tmp_path):
    repo = init_repo(tmp_path)
    checkpoint = git(repo, "rev-parse", "HEAD")   # code state the handover verifies
    commit_doc(repo, checkpoint)                   # 4511b8fa-equivalent
    status, _ = check_handover_conflict(FakeAgent(), repo)
    assert status == "PASS"   # committing the handover is NOT staleness (TEST 9 core)


def test_multiple_doc_only_commits_pass(tmp_path):
    repo = init_repo(tmp_path)
    checkpoint = git(repo, "rev-parse", "HEAD")
    commit_doc(repo, checkpoint)
    make_doc(repo, checkpoint)
    (repo / HANDOVER_NAME).write_text(
        repo.joinpath(HANDOVER_NAME).read_text() + "\n<!-- typo fix note -->\n",
        encoding="utf-8")
    git(repo, "add", HANDOVER_NAME)
    git(repo, "commit", "-qm", "docs: handover typo")   # second doc-only commit
    assert len(git(repo, "log", "--oneline").splitlines()) >= 3
    status, _ = check_handover_conflict(FakeAgent(), repo)
    assert status == "PASS"


# ---- 3.3/3.4: code commits after the checkpoint → CONFLICT ------------------
def test_one_code_commit_after_conflict(tmp_path):
    repo = init_repo(tmp_path)
    checkpoint = git(repo, "rev-parse", "HEAD")
    commit_doc(repo, checkpoint)
    commit_code(repo)
    status, detail = check_handover_conflict(FakeAgent(), repo)
    assert status == "CONFLICT"
    assert "AGENT_HANDOVER" in detail


def test_several_code_commits_conflict(tmp_path):
    repo = init_repo(tmp_path)
    checkpoint = git(repo, "rev-parse", "HEAD")
    commit_doc(repo, checkpoint)
    commit_code(repo)
    commit_code(repo)
    status, _ = check_handover_conflict(FakeAgent(), repo)
    assert status == "CONFLICT"


# ---- mixed doc+code commit after checkpoint → CONFLICT ----------------------
def test_mixed_commit_after_checkpoint_conflict(tmp_path):
    repo = init_repo(tmp_path)
    checkpoint = git(repo, "rev-parse", "HEAD")
    commit_doc(repo, checkpoint)
    (repo / "code.py").write_text("v3\n", encoding="utf-8")
    make_doc(repo, checkpoint)
    git(repo, "add", "code.py", HANDOVER_NAME)
    git(repo, "commit", "-qm", "feat: code + doc touch")   # NOT doc-only → stale
    status, _ = check_handover_conflict(FakeAgent(), repo)
    assert status == "CONFLICT"


# ---- session_check: warn once, cached, never blocks --------------------------
def test_session_check_warns_once_and_never_blocks(tmp_path):
    repo = init_repo(tmp_path)
    checkpoint = git(repo, "rev-parse", "HEAD")
    commit_doc(repo, checkpoint)
    commit_code(repo)
    ag = FakeAgent()
    session_check(ag, repo)
    session_check(ag, repo)
    assert len(ag.warnings) == 1
    assert ag.warnings[0].startswith("⚠️ Context Doctor (CONFLICT)")


# ---- 3.7: missing / broken / unanswerable → UNKNOWN, silent ------------------
def test_missing_handover_unknown_no_warn(tmp_path):
    ag = FakeAgent()
    session_check(ag, tmp_path)
    assert ag._cd_handover_status[0] == "UNKNOWN"
    assert ag.warnings == []


def test_no_last_verified_section_unknown(tmp_path):
    (tmp_path / HANDOVER_NAME).write_text("# HANDOVER\nno sections here\n", encoding="utf-8")
    status, _ = check_handover_conflict(FakeAgent(), tmp_path)
    assert status == "UNKNOWN"


def test_section_without_head_unknown(tmp_path):
    (tmp_path / HANDOVER_NAME).write_text(
        "## 14. LAST VERIFIED\n\n- Date: 2026-10-03\n", encoding="utf-8")
    status, _ = check_handover_conflict(FakeAgent(), tmp_path)
    assert status == "UNKNOWN"


def test_unknown_declared_hash_not_conflict(tmp_path):
    # Declared hash never existed in this repo: git can't answer → UNKNOWN (never guess)
    repo = init_repo(tmp_path)
    commit_doc(repo, HEAD_FULL)
    status, _ = check_handover_conflict(FakeAgent(), repo)
    assert status == "UNKNOWN"


def test_no_git_dir_unknown(tmp_path):
    make_doc(tmp_path, HEAD_FULL)  # no git init
    status, _ = check_handover_conflict(FakeAgent(), tmp_path)
    assert status == "UNKNOWN"


# ---- 3.6: historical hashes outside LAST VERIFIED are ignored ----------------
def test_historical_hashes_ignored(tmp_path):
    repo = init_repo(tmp_path)
    checkpoint = git(repo, "rev-parse", "HEAD")
    commit_doc(repo, checkpoint)
    commit_code(repo)
    other = "b" * 40
    (repo / HANDOVER_NAME).write_text(
        (repo / HANDOVER_NAME).read_text() +
        f"\n## 9. OLD\nCommit `{other}` was the previous checkpoint.\n", encoding="utf-8")
    status, detail = check_handover_conflict(FakeAgent(), repo)
    assert status == "CONFLICT"      # only the LAST VERIFIED value drives it
    assert other not in detail       # historical hash never compared


# ---- 3.8 branch divergence: checked tree-diff is branch-agnostic -------------
def test_diverged_sibling_branch_conflict(tmp_path):
    repo = init_repo(tmp_path)
    checkpoint = git(repo, "rev-parse", "HEAD")
    commit_doc(repo, checkpoint)
    git(repo, "checkout", "-qb", "side")
    commit_code(repo)                 # code on side branch, HEAD moved
    status, _ = check_handover_conflict(FakeAgent(), repo)
    assert status == "CONFLICT"       # diff declared..HEAD includes code.py either way


# ---- 5-8: HANDOVER READ + advisory ------------------------------------------
def test_handover_read_yes_no():
    resolved = "/x/repo/AGENT_HANDOVER.md"
    assert handover_read_status([resolved], Path(resolved)) == "YES"
    assert handover_read_status(["/x/repo/other.md"], Path(resolved)) == "NO"


def test_advisory_none_without_mutation(tmp_path):
    make_doc(tmp_path, HEAD_FULL)
    ag = FakeAgent()
    calls = [call("read_file", {"path": str(tmp_path / "a.txt")})]
    assert advisory_for_batch(ag, calls, "t1", tmp_path) is None


def test_advisory_fires_on_repo_mutation_without_read(tmp_path):
    make_doc(tmp_path, HEAD_FULL)
    ag = FakeAgent()
    calls = [call("write_file", {"path": str(tmp_path / "new.txt"), "content": "x"})]
    note = advisory_for_batch(ag, calls, "t1", tmp_path)
    assert note is not None and HANDOVER_NAME in note


def test_advisory_silent_when_handover_read(tmp_path):
    make_doc(tmp_path, HEAD_FULL)
    handover = str((tmp_path / HANDOVER_NAME).resolve())
    from tools import file_state
    reg = file_state.get_registry()
    reg.record_read("t2", handover)
    try:
        ag = FakeAgent()
        calls = [call("patch", {"path": handover, "old_string": "a", "new_string": "b"})]
        assert advisory_for_batch(ag, calls, "t2", tmp_path) is None
    finally:
        reg.forget_task("t2")


def test_advisory_silent_when_guard_disabled(tmp_path, monkeypatch):
    make_doc(tmp_path, HEAD_FULL)
    monkeypatch.setenv("HERMES_DISABLE_FILE_STATE_GUARD", "1")
    ag = FakeAgent()
    calls = [call("write_file", {"path": str(tmp_path / "f.txt"), "content": "x"})]
    assert advisory_for_batch(ag, calls, "t3", tmp_path) is None  # never guess


def test_advisory_silent_without_task_id(tmp_path):
    make_doc(tmp_path, HEAD_FULL)
    ag = FakeAgent()
    calls = [call("write_file", {"path": str(tmp_path / "f.txt"), "content": "x"})]
    assert advisory_for_batch(ag, calls, None, tmp_path) is None


# ---- 9: net-new file, batch detection -----------------------------------------
def test_batch_mutates_repo_paths_only_inside():
    root = Path("/repo")
    assert batch_mutates_repo([call("write_file", {"path": "/repo/a/b.txt"})], root) is True
    assert batch_mutates_repo([call("write_file", {"path": "/elsewhere/b.txt"})], root) is False
    assert batch_mutates_repo([call("read_file", {"path": "/repo/a/b.txt"})], root) is False
    assert batch_mutates_repo([call("terminal", {"command": "ls"})], root) is False
    assert batch_mutates_repo([], root) is False


def test_batch_survives_broken_arguments():
    root = Path("/repo")
    broken = types.SimpleNamespace(function=types.SimpleNamespace(
        name="write_file", arguments="{not json"))
    good = call("write_file", {"path": "/repo/x.txt"})
    assert batch_mutates_repo([broken, good], root) is True
    assert batch_mutates_repo([broken], root) is False


# ---- 12: determinism + caching -------------------------------------------------
def test_deterministic_and_cached(tmp_path):
    repo = init_repo(tmp_path)
    checkpoint = git(repo, "rev-parse", "HEAD")
    commit_doc(repo, checkpoint)
    commit_code(repo)
    ag = FakeAgent()
    a = check_handover_conflict(ag, repo)
    b = check_handover_conflict(ag, repo)
    assert a == b and ag._cd_handover_status == a


# ---- live-repo fact: current HEAD IS the handover commit → rule gives PASS ----
@pytest.mark.skipif(not Path(os.path.expanduser("~/.hermes/hermes-agent/AGENT_HANDOVER.md")).exists(),
                    reason="live repo handover absent")
def test_live_repo_handover_rule():
    from agent.context_doctor import REPO_ROOT
    declared = parse_handover_declared(REPO_ROOT / HANDOVER_NAME)
    assert declared is not None and len(declared["head"]) in (12, 40)
    status, _ = check_handover_conflict(FakeAgent(), REPO_ROOT)
    assert status == "PASS"   # the TEST 9 scenario resolved on the real repo
