# AGENT_HANDOVER

Shift-handover notebook for this repository. **Read this first** when taking over work
here; verify the live Git state before acting (§11). This is not a README (that's
`README.md`) and not a substitute for Git. All facts below were verified against
code/tests/git on 2026-10-03; anything unverifiable is marked UNKNOWN / TO VERIFY.
Sections 1–5 are the whole picture in a few minutes.

---

## 1. PROJECT SNAPSHOT

- **Project:** Hermes Agent (NousResearch upstream) on a local fork line carrying Polish
  localization + local security layers.
- **Branch:** `hermes-polish` (tracks `fork/hermes-polish`). Remotes: `fork` =
  github.com/BratPidmax/hermes-agent (push target), `origin` = NousResearch (fetch only).
- **HEAD:** `5bde0d2e6612` = Grill Me v1 checkpoint (tag `grillme-v1`, pushed to fork).
- **Status:** both security layers complete and checkpointed; no half-finished refactor
  in the tree; worktree clean (aside from this document until committed).
- **Build:** `hermes --version` ≈ `v0.21.5+…g5bde0d2` (upstream base `b2860025`).
- **Most important checkpoint:** tag `grillme-v1` → exactly `5bde0d2e6612`.

## 2. CURRENT STATE

- **Bouncer v1 — DONE** (commit `c0eb4ebaced9`, pushed; no tag exists — see §4).
- **Grill Me v1 — DONE** (commit `5bde0d2e6612`, **tag `grillme-v1`**, pushed).
- **NOT working on:** any code change — nothing is in flight.
- **Next logical step:** runtime smoke of an armed Grill Me session (§12).
- History policy: fast-forward pushes only; no rebase / force / history rewrites.

## 3. COMPLETED WORK

### Bouncer — tool permission layer
- **Goal:** "MAY the agent use this tool at all?" (allowlist enforcement, not risk).
- **Architecture:** session-scoped gate (`"bouncer" in agent.enabled_toolsets`) + fail-
  closed fuzzy tool-name repair inside turn validation. Order: exact-legal pass →
  VETO1 (name exactly foreign — registry-wide set minus session `valid_tool_names` →
  block, never repair) → VETO2 (Damerau-Levenshtein ≤1 from a foreign name → block) →
  only then fuzzy repair, accepted only when the repaired name ∈ `valid_names`.
  Sessions without the marker behave identically to before (differential harness:
  18/18 identical, DIFF=0).
- **Files:** `agent/turn_tool_validation.py` (frozen core, hash `32ae0539…`, +88/−2);
  plugin `~/.hermes/plugins/bouncer/` — **outside the repo, keep it that way** — registers
  toolset `bouncer` = exactly `memory, read_file, search_files, skills_list, web_extract`;
  enabled via `plugins.enabled: [bouncer]` in `~/.hermes/config.yaml`.
- **Checkpoint/commit/tag:** `c0eb4ebaced9` "security: harden bouncer tool validation";
  no tag.
- **Tests:** `tests/agent/test_repair_tool_call_{name,arguments}.py`,
  `test_streaming_tool_call_repair.py` (15 pass + 8 pre-existing env errors, §7);
  campaigns TEST 28–31 (verdicts: PASS / PASS WITH LIMITATIONS; final code audit clean).
- **Limitations:** multi-toolset selection is a UNION (`["bouncer","file"]` legally
  widens — by design, not a bypass); blocking is at validation only (no execution-time
  argument guard); a2a/spotify/yuanbao toolsets resolve to 0 tools in this environment.

### Grill Me v1 — plan risk layer
- **Goal:** "the agent MAY — does this planned batch carry obvious, unneeded risk?"
  A second pair of eyes before execution; never an executor.
- **Architecture:** static, pure, deterministic classifier run **once per batch** at
  seam C; priority RED > YELLOW > GREEN as an *execution decision*, not a risk ranking.
- **Files:** `agent/turn_grillme.py` (`GRILLME_MARKER:23`, `grillme_enabled:56`,
  `classify_batch:102`); `agent/turn_tool_round.py` (seam block :153–191, import :159);
  `toolsets.py:83` (zero-tool marker `"grillme"`); `tests/agent/test_turn_grillme.py`.
- **Checkpoint/commit/tag:** `5bde0d2e6612` "security: add optional Grill Me static batch
  risk layer (seam C)"; tag **`grillme-v1`** (annotated, pushed to fork).
- **Tests:** 24/24 contract unit tests green; real-`run_tool_round` integration + SQLite
  replay checks (staged TEST 1–4 campaigns; §7).
- **GREEN:** read-only tools (uses `_READ_SEARCH_TOOLS` from `model_tools.py` plus
  skills/session_search/web read tools) → executes; no warning, no message, no DB write,
  no LLM. Reading `.env`/`config.yaml` or searching "password" stays GREEN.
- **YELLOW:** plain `write_file`/`patch` (incl. config paths), neutral `terminal`,
  opaque tools (`execute_code`, `delegate_task`, unknown names), ≥3 mutations → executes
  with **exactly one** `agent._emit_warning` per batch on the status rail — not a message
  row, not model context, not a tool result.
- **RED:** destructive commands (reuses `tools.approval_detection.detect_dangerous_command`
  + verified gap patterns: shred / `truncate -s 0` / `dd of=/dev/…` / mkfs /
  `git reset --hard` / `git clean` / force push / pipe-to-shell), secret-path **writes**
  judged by the path field only (`.env`, `.ssh/`, `*.pem`, `*.key`, credentials),
  agent-home/system targets, blast radius ≥8 distinct files → halts the WHOLE batch
  before dispatch via the existing halt pattern (`_verdict("break")`, exit reason
  `grillme_red`); every call gets an explicit `grillme_stopped` result (replay-safe:
  call IDs == result IDs) and a final message that nothing was executed.
- **Seam C:** in `run_tool_round` — after Bouncer validation, post-call guardrails,
  mixed-batch filtering and tool-call DB persist — immediately before
  `agent._execute_tool_calls(...)` (`turn_tool_round.py:193`). Full batch context
  (names, args, model content, finish_reason, agent, toolsets, valid names, validation
  outcome) is available there. Activation: `hermes chat -t "hermes-cli,grillme"`
  (marker toolset resolves to zero tools; normal sessions pay one membership test).
- **Relationship to Bouncer:** strictly downstream — Bouncer → validation → guardrails →
  Grill Me → execution. A VETOed tool never reaches the seam; Grill Me never mutates
  `valid_tool_names`, never replaces or bypasses validation. Not a second Bouncer.
- **Constraints:** see §8 — static-only by design.

## 4. STABLE CHECKPOINTS

| Component | Commit | Tag | Status |
|---|---|---|---|
| Bouncer (scoped fail-closed fuzzy repair) | `c0eb4ebaced9` | — (no bouncer tag exists) | complete, pushed to fork |
| Grill Me v1 (seam C static risk layer) | `5bde0d2e6612` | `grillme-v1` → exactly this commit | complete, pushed to fork |
| Polish localization line | multiple (e.g. `9f468d29`) | `polish-stable-2026-09-25` (historic) | carried on `hermes-polish` |

Do not invent tags. Never move `grillme-v1`; new checkpoints get new tags.

## 5. ARCHITECTURE MAP

```
model response (tool_calls batch)
  → Bouncer gate + validate_tool_calls         # PERMISSION: which tools this session may use
  → post-call guardrails / mixed-batch filter  # existing turn hygiene
  → tool-call persist to session DB            # "requested", NOT "executed"
  → SEAM C: Grill Me classify_batch (1×/batch) # RISK: GREEN / YELLOW(+warning) / RED(halt)
  → agent._execute_tool_calls                  # execution
  → handle_function_call → pre-dispatch guards → registry.dispatch → results → next turn
```
- **Bouncer = tool permission control** (what may run at all).
- **Grill Me = risk assessment** (whether what may run looks obviously risky).
- Grill Me does NOT replace Bouncer — it only sees the batch Bouncer already allowed.

## 6. IMPORTANT SEAMS / FILES

| File | What lives there | Verified role |
|---|---|---|
| `agent/turn_tool_round.py` | `run_tool_round` (:46), Grill Me seam (:153–191), execute call (:193) | seam C placement is load-bearing |
| `agent/turn_grillme.py` | `grillme_enabled` (:56), `classify_batch` (:102) — pure, no I/O | Grill Me classifier + gate |
| `agent/turn_tool_validation.py` | `validate_tool_calls` (:145), `_scoped_veto_repair` (:128), `_damerau_le_1` (:106), `_repair_normalizations` (:72) | Bouncer core — FROZEN |
| `toolsets.py` | `"grillme"` marker (:83); `resolve_toolset("grillme") == []` | opt-in marker, zero tools |
| `model_tools.py` | `_READ_SEARCH_TOOLS` (:620); `_apply_toolset_selection` (union) | GREEN fast-path source; toolset semantics |
| `tools/approval_detection.py` | `detect_dangerous_command` | reused by Grill Me, unmodified |
| `tests/agent/test_turn_grillme.py` | 24 contract tests | durable v1 behavior spec |
| outside repo (do NOT vendor in) | `~/.hermes/plugins/bouncer/`, `~/.hermes/skills/hermes-bouncer-custom/`, `~/.hermes/config.yaml` | live user-side pieces |

## 7. TEST STATUS

**PASS (re-verified 2026-10-03):**
- `tests/agent/test_turn_grillme.py` — 24 passed.
- Repair suites (name/arguments/streaming) — 15 passed.
- `tests/hermes_cli/test_toolset_validation.py` — 33 passed;
  `tools/test_toolset_distributions.py` + delegate composite — 22 passed.
- Guardrail suites (`tool_call_guardrail_runtime`, `agent_guardrails`,
  `tool_guardrails`) — 61 passed.
- Combined grillme+repair run: **39 passed, 8 errors** — every error is in the
  pre-existing class below, none caused by Bouncer/Grill Me.

**KNOWN PRE-EXISTING (fail identically on clean HEAD — proven by stash/baseline diff;
NOT errors of the new modules):**
- `tests/home_io_guard.py` fixture errors (8) when tests import the real agent loop
  outside the sanctioned test environment.
- `tests/test_model_tools_async_bridge.py` — 3 fails (missing `pytest-asyncio` in user-site).
- `tests/gateway/test_multiplex_toolsets_profile_isolation.py` 4 fails +
  `tests/hermes_cli/test_tools_config.py` 1 fail (environment/multiplex scope).
- `tests/agent/lsp/test_client_e2e.py` (missing LSP binaries); `tests/tools/test_file_state_registry.py`
  1 fail (identical on baseline).

**UNKNOWN / TO VERIFY:**
- End-to-end runtime of an armed Grill Me session through the live gateway loop — the
  TEST 5 procedure exists; only the UNARMED check was executed (marker absent there, as
  expected). See §12.

## 8. KNOWN LIMITATIONS (confirmed; most are deliberate v1 design, not defects)

- Grill Me v1 is a **static classifier — no LLM** by design.
- **Opaque tools cannot be judged statically:** `execute_code` bodies, `delegate_task`
  goals, scripts via terminal, SQL payloads → YELLOW caution, not semantic review.
  Measured false negatives (TEST 3): `mysql … DROP`, `sed -i` overwrite, `cp` overwrite,
  `> file` truncation, `shred` without flags, `python x.py`.
- **YELLOW does not ask the user** — warn + continue (v1 decision; Hermes has an approval
  queue elsewhere, intentionally not wired here).
- **RED fires after** the tool-call row was persisted; history stays replay-safe and says
  explicitly "nothing was executed" (accepted from TEST 2/4 analysis).
- Escalation thresholds (≥3 mutations, ≥8 files) are tuned heuristics, not proofs.
- Bouncer: union semantics across toolsets; validation-time only, no runtime guard.
- Headless CI cannot verify GUI/Wayland behavior (manual desktop passes required).

## 9. DO NOT TOUCH / PROTECTED AREAS

- **Closed Bouncer** — `agent/turn_tool_validation.py` (hash `32ae0539…`) + bouncer
  plugin/skill outside the repo. Any change = fresh verification campaign first.
- **Closed Grill Me v1** — tag `grillme-v1`. Do not "improve" speculatively.
- **Seam placement** — Grill Me stays after validation/persist, before
  `_execute_tool_calls`. Never into the executor, a global `pre_tool_call` hook, or
  middleware (process-global; duplicates the Bouncer slot).
- **Existing guardrails** — guardrail-halt, mixed-batch, persistence-fail behavior must
  stay identical for sessions without markers.
- **Existing tests** — don't rewrite expectations to fit new code; fix forward.
- **Git** — no rebase/filter/reset/force-push; push to `fork` only; never move
  `grillme-v1`; fast-forward branches.
- **Secrets** — nothing from `~/.hermes/.env` or tokens in any file; pre-push secret scan
  is part of the push ritual (last scan at `c0eb4ebaced9`: clean).

> Rule: do not improve a closed module just because you could. First confirm the problem
> actually belongs to the current task.

## 10. DEVELOPMENT RULES (working practice on this repo)

1. Audit first (read-only), report findings.
2. Then test/prototype (temporary, reversible instrumentation only).
3. Only then implement.
4. After implementation: full regression (targeted suites + baseline comparison).
5. No changes outside the agreed scope.
6. Temporary changes must be fully removed; hashes restored and verified.
7. Commit only after positive tests.
8. Push only after an approved checkpoint.
9. Never hide test failures — classify PASS / PRE-EXISTING / TO VERIFY honestly.
10. For security changes: smallest possible diff; reuse existing mechanisms.
11. Don't build a new mechanism when an existing one is safely reusable
    (guardrail-halt, `_emit_warning`, `detect_dangerous_command` are the examples).
12. Never bypass existing safeguards during tests.

## 11. HOW TO START A NEW SESSION

1. Read `AGENT_HANDOVER.md` (§§1–5 first).
2. `git status --short` — expect clean.
3. `git rev-parse HEAD` — expect `5bde0d2e6612` (or newer if work continued).
4. `git branch --show-current` — expect `hermes-polish`.
5. `git rev-parse grillme-v1^{commit}` — the last stable checkpoint.
6. Read only the documentation the current task needs (`AGENTS.md` routing table).
7. Don't touch closed components (§9) without a reason tied to the actual task.

**Do not assume the Handover state is eternal — verify live Git before changing code.**

## 12. NEXT WORK

- **Ready:** Bouncer v1 (closed), Grill Me v1 (closed, tagged), durable contract tests,
  this handover.
- **Next:** TEST 5 runtime smoke in an armed session
  (`hermes chat -t "hermes-cli,grillme"`): GREEN read / YELLOW write + terminal (one
  warning) / RED `rm -rf` on a safe `/tmp` target (halt, nothing executes) / RED `.env`
  write / mixed batch (RED wins) / OFF control (normal path). Record results; do not fix
  forward unless behavior deviates from §3.
- **Not implemented (deliberately, out of v1 scope):** LLM escalation of YELLOW/RED,
  interactive Grill Me approval, a `bouncer-v1` tag, upstream PRs for either layer.
- Anything beyond the above: NEXT STEP: TO BE DEFINED.

## 13. HANDOVER MAINTENANCE

`AGENT_HANDOVER.md` **must be updated after every larger stable checkpoint**: a module
closing, an architecture change, a security-mechanism change, a new checkpoint. Order of
operations: 1) update this document, 2) run the relevant tests, 3) verify Git facts
(HEAD / tags / status / sync), 4) only then create the checkpoint (commit/tag/push per
§10). Do **not** update the Handover after every minor code line — it documents
milestones, not churn.

## 14. LAST VERIFIED

- Date: **2026-10-03** (CEST)
- HEAD verified: `5bde0d2e6612eb10e8bf024eff375cde504b77f0`
- Branch: `hermes-polish`, synced with `fork/hermes-polish` (0 local-only commits)
- Tests: §7 (24+15+33+22+61 PASS; pre-existing failures listed, not hidden)
- Last stable checkpoint: tag `grillme-v1` → `5bde0d2e6612`
