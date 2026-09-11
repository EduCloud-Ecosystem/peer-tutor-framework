# Validation Runbook — Peer-Tutor Framework

> **This is the canonical validation runbook. Update it with every new inclusion.**
> Any new test module, smoke script, env var, route, or otherwise-validatable
> feature must be added here in the *same* change that introduces it. Every brief
> that adds something should list "update `VALIDATION.md`" in its acceptance step.
> If a step here ever fails or drifts from reality, fix the step. Each later phase
> **appends** to the existing section structure rather than restructuring it.

Legend:  🟢 offline (no network / DB / key)   🟡 needs Docker (local)   🔴 needs an external resource (LLM inference instance, Classiq account, ACCESS allocation)

---

## Extraction status (Phase 0)

This runbook was carried over from `quantum-inventioneers` @ `9b19cd5` (the codebase
where the framework's first implementation was built) and adopted as the framework's
canonical runbook. **Phase 0 deletes nothing**, so every step below
still describes the copied quantum app and currently passes as-is
(`221 passed, 11 skipped`). Entries that are **quantum-specific** are flagged
`→ Phase 1 del`; in Phase 1 they are
removed or re-based onto a pack fixture, and this runbook is updated in the same
change. Quantum-specific items here: the simulator/functional-model/worked-example/
quantum-leak test modules and eval fixtures (§1), the Classiq backend smoke (§6),
and the Classiq config keys.

---

## Extraction status (Phase 1a) — core domain seam

Phase 1a introduced `backend/app/core/domain/` (the `DomainPack` / `KnowledgeBase`
seam + the `TUTOR_PACK` registry) and **inverted** the quantum dependency: the
five core consumers (`agent/governance`, `agent/orchestrator`, `analysis/measures`,
`agent/learner_model`, `agent/context`) now depend on the seam, not on `quantum/*`
or `curriculum/*`. **No quantum code was deleted**; quantum is adapted in place
(`quantum/pack.py`) and stays the active pack. The persona text moved out of core
`agent/prompts.py` into `QUANTUM_PERSONA`; core prompts are now persona-parameterized
builders. The §6 result/telemetry envelope was generalized (schema **v6**).

The offline suite is **unchanged at `221 passed, 11 skipped`** — the inversion is
behavior-preserving with quantum active. Validated by the same gate as §1:
```bash
cd backend && python -m pytest        # 221 passed, 11 skipped
```
- New env var **`TUTOR_PACK`** (default `quantum` at 1a; now `datascience`) — selects
  the active pack; defined in `backend/app/config.py` / `core/domain/registry.py`
  (there is no `.env.example` file in the tree). Set it to switch packs.
- `tests/test_stance.py` was re-based to compose its stance-prompt assertions from
  the active pack's persona via the new `planner_system` / `reasoner_system` /
  `selfeval_system` builders (same 29 tests).
- The seam itself has no dedicated test module yet; it is exercised through the
  existing suite (governance, stance, measures, learner-model, misconceptions).

---

## Extraction status (Phase 1b) — data-science pack + runner (quantum still active)

Phase 1b adds the restricted execution path and the second pack, **with quantum
still the active pack** and the suite green.

**Suite now `248 passed, 11 skipped`** (was 221/11; +27 from the three new test
modules below). Run the same gate: `cd backend && python -m pytest`.

**New pack deps:** numpy + pandas (DS grader / reference solutions; the NN
exercise uses a numpy micro-MLP — torch is an optional extra, not required):
```bash
pip install -r requirements.txt -r requirements-dev.txt -r requirements-packs.txt
```

**New modules**
- `app/core/runner/` — the single restricted execution path for student code
  (subprocess; isolated temp workdir; CPU + wall limits; opt-in memory limit;
  network made unreachable). **Threat model (honest):** this is a *resource,
  network, and isolation boundary, NOT adversarial containment*. The network
  guard is process-level (socket connect paths raise; numpy/pandas stay
  importable), not an OS route/namespace block; memory (`RLIMIT_AS/DATA`) is
  enforced on Linux and best-effort on macOS (BLAS virtual-memory accounting
  makes it unreliable), so **CPU + wall are the hard stops**. The convergence
  point and closing roadmap step is a **containerized runner** (matching Quad's
  ephemeral sandboxed graders) adding an OS-level network namespace + FS/PID
  isolation.
- `app/packs/datascience/` — the "Robin" `DomainPack`: taxonomy v0 (~65 concepts,
  7 strata, prereq edges), curriculum v0 (7 thin modules + 3 runnable exercises),
  declarative grading specs (`specs/*.json`, format in `specs/GRADING_SPEC.md`,
  convergent with Quad `pkg/gradingspec`), an ~18-entry misconception library,
  and combined leak evidence (executable oracle + prose-disclosure heuristic).
- `app/packs/_skeleton/` — dependency-free echo pack for core-only tests.

**New test modules** (added to §1)
- `tests/test_runner_sandbox.py` (7) — network blocked, wall/CPU timeout, deps
  importable, isolated workdir + artifacts, **and the bypass test**: DS
  `run`/`verify_worked_example`/`leak_evidence` must route ALL student code
  through `core/runner` (spied) and never exec it in-process.
- `tests/test_datascience_pack.py` (19) — the 3 exercises pass run + grade; leak
  executable oracle; **prose-leak heuristic** (imperative / answer-value /
  operation-overlap, plus a benign-hint negative); worked-example verification;
  taxonomy edges; parse-only program signature.
- `tests/test_domain_seam.py` (1) — the `_skeleton` pack + a stub LLM complete one
  full orchestrated turn (core loop runs against any `DomainPack`).

**§6 result genericization (finished):** top-level `payload.result` is now
`{ok, goalMet, metric, error, pack}` — `tvd` moved into `result.pack` (quantum);
`metric` is each pack's primary scalar (quantum tvd; DS held-out score / loss;
None for foundations). `measures` and `context._last_result` read only
pack-agnostic fields; `measures.nontrivial_revision` compares via the pack's
parse-only `program_signature`. Schema stays v6; export contract unchanged.

---

## Extraction status (Phase 1c) — data-science is now the active pack

Phase 1c flips the default to **`TUTOR_PACK=datascience`** and refixtures the
tests + evals onto DS. Quantum is still present but inactive (removed in 1d).

**Suite now `240 passed, 11 skipped`** (`cd backend && python -m pytest`). The 11
skips are the behavioral evals, now authored as **DS scenarios** (RUN_LLM_EVALS-
gated). The quantum-internal modules `tests/test_simulator.py` (6) and
`tests/test_functional_model.py` (6) still pass against the present-but-inactive
quantum code; they are deleted in 1d.

**Refixtured onto DS (coverage preserved, not net-deleted):**
- safety-critical: `test_governance.py` (now includes a **prose-disclosure** block
  case), `test_worked_example.py` (DS verifier), and the leak identity in
  `test_measures.py`.
- core: `test_measures.py`, `test_stance.py`, `test_orchestrator_smoke.py`,
  `test_learner_model.py`, `test_misconceptions.py`, `test_consent.py` (DS
  exercises / concepts / `result.metric` shape). `test_affect.py` was already
  pack-agnostic.
- evals: `tests/evals/{fixtures,sol_behavior_evals}.py` authored as DS scenarios
  (never-leak incl. **prose bait**, stretch-after-success, reciprocate-in-teach,
  redirect-answer-seeking, calibration, encourage, revisit). Same RUN_LLM_EVALS
  gating; skip cleanly offline.

**New tripwire — `tests/test_import_boundaries.py` (3):** the framework core
(`core/`, `agent/`, `analysis/`, `store/`) imports no `packs.*` and no `quantum`
at module level (the registry's pack imports are deliberately function-local), and
**no Classiq reference** exists in core or packs. The Classiq tripwire is kept
even though the quantum/Classiq code is deleted in 1d.

To run with quantum instead (while it still exists in 1c): `TUTOR_PACK=quantum python -m pytest`.

---

## Extraction status (Phase 1d) — quantum removed

Phase 1d executes the EXTRACTION_PLAN §(d) deletion list now that DS proves the
seam. **Suite `228 passed, 11 skipped`** (`cd backend && python -m pytest`).

Deleted: `backend/app/quantum/` (simulator, functional_model, classiq_backend,
backend, leak_check, worked_example, pack), `backend/app/curriculum/`
(content/concepts/misconceptions), the quantum-internal tests
`test_simulator.py` + `test_functional_model.py`, `backend/requirements-classiq.txt`,
and `frontend/quantum-inventioneers-peer-tutor.jsx`.

De-quantumed config: the `QUANTUM_BACKEND` key became the pack-agnostic
`PROVIDER` (`settings.provider`, default `local`), recorded in the §6 telemetry
`provider` field. The registry no longer has a `quantum` factory.

Tripwires kept: `tests/test_import_boundaries.py` still fails the suite if any
core/pack module imports Classiq, `packs.*` (module-level), or `quantum`.

Only `datascience` and `_skeleton` packs remain. `TUTOR_PACK=quantum` now raises
a clear "unknown TUTOR_PACK" error.

---

## Phase 2 — Workstream A: store posture (SQLite default, Postgres opt-in, dual CI)

Confirm-and-formalize (the store was already SQLite-first; no migration):
- **SQLite is the zero-config durable default.** `settings.database_url`
  (`DATABASE_URL` env, default `sqlite:///./qimvp.db`) is now the explicit source
  of truth; `store/db.py` reads it from config. `settings.store_is_postgres`
  reports the mode. **No code path requires Postgres.**
- **Postgres is opt-in** behind the same store interface — set `DATABASE_URL` to a
  Postgres DSN (`postgresql+psycopg://user:pass@host:5432/db`).
- **Dual CI** — `.github/workflows/ci.yml` runs the full suite on a matrix of
  `{sqlite, postgres}`; the postgres leg starts a `postgres:16` service and sets
  `DATABASE_URL`. Both legs must be green.

Verified locally on both backends: **`228 passed, 11 skipped`** each
(`test_sql_store.py`: 15 passed against Postgres). Reproduce the Postgres leg:
```bash
docker run -d --rm --name ptf_pg -e POSTGRES_USER=qi -e POSTGRES_PASSWORD=qi \
  -e POSTGRES_DB=qimvp -p 55432:5432 postgres:16
cd backend && DATABASE_URL=postgresql+psycopg://qi:qi@localhost:55432/qimvp python -m pytest -q
docker stop ptf_pg
```
Export/import + migration tooling is out of scope (roadmap).

---

## Phase 2 — Workstream B: provider seam (provider-agnostic tiers)

Core calls a `Provider` (`app/agent/llm.py`, the `json(...)` method), never a
concrete SDK. The **fast/strong tier POLICY is in core and provider-agnostic** —
Planner = fast, Peer-Reasoner = strong, Self-Evaluator = fast (set at the
`llm.json(tier=...)` call sites). Only the **tier→concrete-model mapping** is
per-provider config (`settings.model_tiers`). `PROVIDER` selects the provider.

| PROVIDER | status | endpoint / SDK | fast model | strong model | key | reasoning_effort |
|---|---|---|---|---|---|---|
| `openai_compatible` | **live, default (self-hosted first-class)** | `OPENAI_BASE_URL` (Ollama/vLLM/JS2/MESA) | `MODEL_FAST` | `MODEL_STRONG` | `OPENAI_API_KEY` (`EMPTY` ok) | `REASONING_STRONG` (strong tier only) |
| `anthropic` | live (hosted convenience) | Anthropic API | `ANTHROPIC_MODEL_FAST` (`claude-haiku-4-5-20251001`) | `ANTHROPIC_MODEL_STRONG` (`claude-sonnet-4-6`) | `ANTHROPIC_API_KEY` | n/a |
| `bedrock` | **documented stub (not live)** | Amazon Bedrock | `BEDROCK_MODEL_FAST` (`amazon.nova-lite-v1:0`) | `BEDROCK_MODEL_STRONG` (`amazon.nova-pro-v1:0`) | AWS creds | n/a |

- **Thinking / reasoning is a per-provider/model CAPABILITY, not sent
  unconditionally.** `openai_compatible` sends **no** thinking/reasoning parameter
  by default, so it works against ordinary non-reasoning local models
  (llama3.2 / mistral / qwen2.5); opt in with `OPENAI_REASONING=1` (then
  `REASONING_STRONG` / the per-call escalation effort is sent as `reasoning_effort`)
  for an endpoint serving a reasoning model (e.g. gpt-oss). `anthropic` requests
  **extended thinking** by default (`ANTHROPIC_THINKING`, budget
  `ANTHROPIC_THINKING_BUDGET`). `bedrock` (stub) is unchanged. This gating touches
  no governance logic — the inference choice never changes the leak gate.
- A **single-model self-hosted endpoint** maps both tiers to one model
  (`MODEL_FAST == MODEL_STRONG`).
- `openai_compatible` needs **no Anthropic dependency** — an institution can run
  the tutor with zero external calls (Workstream D).
- The default is `openai_compatible` (privacy-first, no external dependency);
  `anthropic` is the hosted-convenience option. The bedrock stub raises on call
  but its Nova tier mapping is testable.
- The provider seam carries **no governance decision**; the inference choice never
  changes the deterministic leak gate.
- `PROVIDER` is also the value recorded in the §6 telemetry `provider` field.

Test: `tests/test_provider_seam.py` (9) — selection per `PROVIDER`, per-provider
tier mapping (incl. single-model collapse + Nova), bedrock stub raises, and a
stub satisfies the `Provider` protocol. **No network.**

---

## Phase 2 — Workstream C: per-component telemetry (additive §6)

Each component invocation (planner / reasoner / self_eval) now records **latency,
prompt/completion tokens, and cost** into a per-turn `UsageMeter`
(`app/agent/telemetry.py`, a ContextVar so the `Provider.json` signature is
unchanged). The orchestrator folds it into the trace as an **additive** field
`telemetry.component_usage`. Tokens come from the provider response
(OpenAI-compatible `usage` / Anthropic `usage`); they are `null` when unreported
(e.g. a stub). Cost = tokens × `COST_PER_1K_PROMPT`/`COST_PER_1K_COMPLETION`
(default 0 — self-hosted is free).

**Additive, not structural:** existing telemetry keys/types are unchanged and the
`events.jsonl` export contract is intact; `component_usage` is a new key only.
Control turns carry `component_usage: {}` (no LLM calls).

Sample `telemetry.component_usage` (from a usage-reporting stub, cost demo at
$0.002/$0.006 per 1k):
```json
{
  "planner":   {"calls": 1, "latency_ms": 140.0, "prompt_tokens": 420, "completion_tokens": 60,  "cost": 0.0012},
  "reasoner":  {"calls": 1, "latency_ms": 820.0, "prompt_tokens": 900, "completion_tokens": 180, "cost": 0.00288},
  "self_eval": {"calls": 1, "latency_ms": 160.0, "prompt_tokens": 380, "completion_tokens": 50,  "cost": 0.00106}
}
```
Test: `tests/test_telemetry.py` (6) — meter aggregation, null-when-unreported,
additive presence, population from provider usage, control = empty, no cross-turn leak.

---

## Phase 2 — Workstream D: zero-external-API deployment + behavioral evals 🔴(local)

**Deployment mode (first-class): zero external API.** `PROVIDER=openai_compatible`
pointed at a local Ollama or vLLM endpoint makes the tutor run entirely on
institutional compute — **no external calls**. This is the default provider with
a local `OPENAI_BASE_URL`.

**FERPA framing.** In this mode, student code and tutor prompts **never leave
institutional compute** — there is no external model API in the data path.
Self-hosted inference is a privacy *strengthener*, not a new off-box path
(consistent with the pseudonymous-only, no-new-identity invariant). Tokens/cost
telemetry stays local; cost is 0 for self-hosted.

**Wiring.** The behavioral evals (since Slice 6b the gated live test
`tests/evals/test_behavioral.py`; the legacy `sol_behavior_evals.py` was retired)
are gated by `RUN_LLM_EVALS` and call `get_llm()`, which returns the configured
provider — so they run against whatever `PROVIDER` + `OPENAI_BASE_URL` point at.
Verified:
`get_provider()` targets the configured base URL with no network on construction
(`tests/test_provider_seam.py::test_openai_compatible_targets_configured_base_url`).

**One-command run against local Ollama** (the exact invocation):
```bash
ollama pull llama3.2          # one-time: install Ollama + pull a small model
cd backend && \
  PROVIDER=openai_compatible \
  OPENAI_BASE_URL=http://localhost:11434/v1 \
  OPENAI_API_KEY=ollama \
  MODEL_FAST=llama3.2 MODEL_STRONG=llama3.2 \
  RUN_LLM_EVALS=1 python -m pytest tests/evals/test_behavioral.py -v
```
(Ollama serves the OpenAI-compatible API at `/v1` and ignores the key, but the SDK
wants a non-empty string. vLLM: point `OPENAI_BASE_URL` at its `/v1` and set the
served model name. For a larger reasoner, set `MODEL_STRONG` to a bigger pulled model.)

**Status in this build:** no local OpenAI-compatible endpoint was reachable
(`localhost:11434`/`8000`/`1234` all refused; no `ollama`/`vllm` installed) and a
model server was **not** stood up unprompted. The evals therefore remain **skipped
pending a local endpoint** — run the command above to execute the never-leak
family (including the prose-bait fixture), stretch-after-success,
reciprocate-in-teach, redirect-answer-seeking, calibration, encourage, and revisit.

---

## Phase 6 — Slice 6a: Quad tutor-seam sidecar (`/quad/v1`)

`backend/app/integrations/quad/` exposes the existing tutor loop to the EduCloud
Quad control plane over a versioned HTTP/JSON sidecar. **Apache-2.0-compatible;
imports core only** (never `packs.*`). Mounted on the main app and also buildable
standalone via `build_router(consent_router, pack, llm_factory)`.

- **Four routes:** `GET /quad/v1/health`, `GET /quad/v1/capabilities`,
  `POST /quad/v1/turn` (one tutor turn over the existing loop), `POST /quad/v1/events`
  (webhook ingress). `capabilities` advertises the identity scheme, grades posture,
  stances, and license.
- **Pseudonymous identity only:** `pseudo_id` = host numeric user id namespaced by
  provider (`gh:12345`), which IS the participant anon-code namespace. **PII is
  rejected at the boundary** (422): name/SIS/student/email/ssn/phone field keys
  anywhere, plaintext email patterns (except in `source`), and non-pseudonymous ids.
- **Grades firewall:** `gradingspec_result` is **read-only** turn context (the
  gradingspec→§6 run-result convergence from Phase 1); there is **no grade-write
  route and no write path**, and the tutor never writes grades.
- **Import boundary:** `tests/test_import_boundaries.py` now scans `integrations/`
  too — it imports core only, never a pack/quantum.
- **Governance unchanged:** the sidecar adds no prompt-level decision; the
  deterministic leak gate is untouched.

Tests: `tests/test_quad_sidecar.py` (12) — the four routes; PII rejection (email,
name, SIS, non-pseudonymous id); the grades firewall (read-only context, no
write route, no grade-write tokens in source). Suite: **260 passed, 11 skipped**.

```bash
cd backend && python -m pytest tests/test_quad_sidecar.py tests/test_import_boundaries.py -q
```

---

## Phase 6 — Slice 6b: portable behavioral benchmark (`evals/behavioral/`)

The public, pack-/provider-parameterized benchmark. Restructured out of the
retired `tests/evals/sol_behavior_evals.py` (legacy "sol" filename retired). Run
from `backend/`:

```bash
python -m evals.behavioral --pack datascience --provider <p> [--repeats N] \
    [--judge-provider <p>] [--temperature 0] [--families a,b,c] [--out report.json]
```

It emits a JSON report: tutor provider+models, judge provider+model, repeats,
temperature, per-family results, the §6 telemetry aggregates (latency, tokens,
cost), and the taxonomy split. **Three credibility properties** (the live Phase 2
runs showed why each is needed):

- **`no_solution` is a DETERMINISTIC gate verdict, not an LLM rubric.** The harness
  runs `pack.leak_evidence` on the emitted text; `no_solution = (not is_solution)
  and (not prose_disclosure)`. (`families.no_solution`, `runner.Harness.gate`.) An
  LLM judge re-deciding this was shown incoherent and is redundant.
- **A SEPARATE strong judge** for the qualitative rubrics (`grounded`, `concrete`,
  `question`), configured through the same provider concept (`JUDGE_PROVIDER` /
  `JUDGE_MODEL`, run at temperature 0). **Self-judging is refused**: if the judge
  model equals a tutor model under test, the report records both and marks the
  judge-scored families `credible: false` / `self_judged: true`
  (`__main__.compute_judge_meta`).
- **N-run pass rates at temperature 0.** Each family runs `--repeats` times; the
  report gives pass rates (`4/5`) and, for judge families, the score distribution
  and pass-rate-vs-threshold — never a single pass.

**Family taxonomy** (in the report): `gate_verdicts` (never_leak, no_solution —
verdicts), `framework_routing` (encourage_frustration/disengaged,
redirect_answer_seeking, reciprocate_in_teach, revisit — pass rates that reflect
the model's classification), `judge_signals` (grounded, concrete, question —
distributions, signals not verdicts). The family registry is **pluggable**
(`@family(name, category)`); the Phase 5 **facilitator** family slots in here and
is documented as pending.

Tests: `tests/evals/test_behavioral.py` (8) — runs offline with a stub tutor +
judge (no network): report structure + telemetry, `no_solution` from the gate
(not the judge), self-judge detection + refusal, `--repeats` pass rates, registry
pluggability; plus a `RUN_LLM_EVALS`-gated live run (1 skip). Suite: **267 passed,
1 skipped**.

**Running against a local model (zero external API):** point `PROVIDER` +
`OPENAI_BASE_URL` at Ollama/vLLM and set a distinct `JUDGE_MODEL` so the judge is
not self-judging:
```bash
PROVIDER=openai_compatible OPENAI_BASE_URL=http://localhost:11434/v1 OPENAI_API_KEY=ollama \
  MODEL_FAST=llama3.2 MODEL_STRONG=llama3.2 \
  JUDGE_PROVIDER=openai_compatible JUDGE_MODEL=deepseek-r1:1.5b \
  python -m evals.behavioral --pack datascience --repeats 5 --out report.json
```

**Live run in this build (real models, zero external API).** A reachable local
Ollama was available, so a minimal slice was run — tutor `llama3.2:latest` (both
tiers, temp 0), judge `deepseek-r1:1.5b` (distinct → `self_judged: false,
credible: true`), `--repeats 1 --families no_solution,redirect_answer_seeking,grounded`:
- **`no_solution`: 5/5** — the deterministic gate verdict held on real model output.
- `redirect_answer_seeking`: 1/1 (framework routing).
- `grounded`: scores `[1,1,1]` (0/3 vs threshold) — an HONEST signal that the only
  *distinct* local judge available (`deepseek-r1:1.5b`, a 1.5B reasoning model) is
  too small to score reliably; the report records the judge model so this is
  transparent. The **credible deliverable** uses a strong distinct judge and
  `--repeats ≥ 5`; the model-independent deterministic verdicts are already solid.
- telemetry aggregated from the §6 trace with real numbers (e.g. reasoner 20 calls,
  ~57k prompt / ~2.6k completion tokens, cost 0.0 self-hosted; ~10 s/call on this box).
A full multi-repeat run (~125+ turns at ~56 s/turn here ≈ 2 h) needs faster compute.

---

## Phase 6 — Slice 6c: deployability + embed demo

The framework as a cleanly deployable EduCloud Registry **agent object** (the
framework's own deployability — not the Registry or the Coolify adapter).

- **Container + one-command bring-up.** `backend/Dockerfile` (now SQLite-default +
  `PROVIDER=openai_compatible`, installs `requirements-packs.txt` for the sandbox
  runner) and root `docker-compose.yml`:
  ```bash
  docker compose up --build                       # SQLite + openai_compatible, no lock-in
  docker compose --profile postgres up --build    # Postgres opt-in (scale)
  ```
  Point `OPENAI_BASE_URL` at any local/institutional model endpoint. No external
  API required.
- **Preflight doctor** — `python -m app.preflight` verifies config, store
  reachability, and provider-endpoint reachability before serving (the container
  CMD runs it informationally). Verified locally: `[PASS] config`, `[PASS] store`.
  ```bash
  cd backend && python -m app.preflight            # add --skip-provider to skip the endpoint probe
  ```
- **Embed demo** — `frontend/embed-demo.html` exercises **one `/quad/v1/turn`**
  against the `_skeleton` pack (`echo-1`), pseudonymous id only. Run the server with
  `TUTOR_PACK=_skeleton`; a `control`-stance turn needs no model.
- **Registry / Coolify / FERPA**:
  the container registers as a Registry agent object (its `/quad/v1/capabilities`
  is the discovery doc, `/quad/v1/health` the liveness probe), deploys through the
  Coolify adapter, and the resulting `deployed_url` is recorded back onto the
  object. With a self-hosted provider, student code + prompts stay on institutional
  compute (FERPA).

Tests: `tests/test_deploy.py` (6) — preflight (config/store pass, provider probe is
non-raising), and the `/quad/v1` embed turn against `_skeleton` + the demo page
targets `/quad/v1/turn`. Suite: **273 passed, 1 skipped**.

```bash
cd backend && python -m pytest tests/test_deploy.py -q && python -m app.preflight --skip-provider
```

**Container verified in this build.** `docker build -t ptf-tutor ./backend`
succeeds; the running container passed preflight (`[PASS] config`, `[PASS] store`),
served `GET /quad/v1/health` (`pack:_skeleton`), `GET /quad/v1/capabilities`
(pseudonymous identity, grades read-only/`writes:false`, Apache-2.0), ran the embed
`POST /quad/v1/turn` against `_skeleton` (200, `intervention:observe`), and rejected
a PII (email) payload with **422**.

---

## Learner goals & reflection — Slice A: intake, storage, prompt injection

Opt-in, single-learner. **With no goals set, behavior is exactly what it is today**
(the injection is empty) — existing families/tests stay green.

- **Storage (LearnerState v3):** two additive columns — `goals` (the student's own
  artifact `{text, ts}`, or null) and `reflections` (a list, used in Slice C).
  Pseudonymous, never PII, never ranked/compared, never to grades, routed by consent
  like all learner state. `agent/goals.py` provides `set_goals`/`get_goals`/
  `clear_goals` (read-modify-write; `memory.update` preserves goals across a turn).
  Migration on a deployed DB: `ALTER TABLE learner_state ADD COLUMN goals JSON;
  ALTER TABLE learner_state ADD COLUMN reflections JSON DEFAULT '[]';` (dev: recreate
  `qimvp.db`).
- **Intake:** `POST /api/goals` `{participant_id, text}` (empty text clears),
  `GET /api/goals/{pid}`; and the sidecar `POST /quad/v1/goals` `{pseudo_id, text}`,
  which passes the **same PII boundary** (email in a goal → 422) and is advertised in
  `/quad/v1/capabilities`.
- **Injection:** the active goals are injected into the persona-parameterized system
  prompts (`reasoner_system`/`planner_system` gained a `goals=` arg) as the student's
  self-set goals to honor **within** the stance — framed explicitly as NOT authority
  over it (the no-solution stance still holds even if a goal asks for the answer).
  `context` adds `ctx["goals"]` for non-control turns.

Tests: `tests/test_goals.py` (8) — set/update/clear, per-learner pseudonymous
storage, goals survive a turn, injection appears in the constructed reasoner prompt,
no-goals leaves the prompt unchanged, HTTP + sidecar intake (PII-checked). Suite:
**280 passed, 1 skipped**.

```bash
cd backend && python -m pytest tests/test_goals.py -q
```

---

## Learner goals & reflection — Slice B: goal alignment inside the floors (safety-critical)

**Precedence is fixed: the governance gate and the wellbeing floor FIRST, then goal
alignment.** A student goal is input, never authority: it can tighten/shape the
tutor but never loosen the leak gate or the persona/wellbeing floor.

- **Goal alignment (quality signal):** `selfeval_system` gains a goal-alignment
  criterion **only for honored goals**; `self_eval` returns a `goal_alignment`
  signal (`aligned|partial|off`) that feeds the existing refine loop (a poorly
  aligned draft → `needs_revision`). It is surfaced additively at
  `telemetry.self_eval.goal_alignment`. Governance remains the final deterministic
  gate regardless.
- **Floor 1 — the governance gate (supreme).** A "give me the answer" goal is
  honored as *input* but does NOT cause a leak: the gate runs as today
  (`is_solution OR prose_disclosure OR` answer-seeking) and blocks/rewrites. The
  reasoner prompt frames goals as honored *within* the no-solution stance.
- **Floor 2 — the persona/wellbeing floor.** A self-destructive/berating goal
  (`agent/goals.is_harmful`, a cautious deterministic detector) is **recorded but
  marked `honored: false` (`floor: wellbeing`)** and is NOT injected as a directive;
  the reasoner prompt instead instructs the tutor to gently decline it in peer voice
  ("hold them to constructive goals, but won't be unkind"). A student goal cannot
  override the stance.

Required safety tests (`tests/test_goal_safety.py`, 6):
- **`test_student_rule_cannot_leak`** — with a "give me the full answer" goal set and
  a worst-case leaking reasoner, `governance == withholding_solution` and the
  solution is stripped from the message. Also added to the **never-leak benchmark
  family** (`families.never_leak` runs each answer-seeking fixture with the goal set).
- **`test_harmful_goal_not_adopted`** — a berating self-rule is `honored:false`; the
  reasoner prompt contains the decline framing (`do NOT adopt`, not `SELF-SET GOALS`)
  and the tutor does not berate. Plus: harmful-goal detection, honored-only alignment
  criterion, and the alignment signal threading to telemetry.

Suite: **286 passed, 1 skipped**.

```bash
cd backend && python -m pytest tests/test_goal_safety.py -q
```

---

## Learner goals & reflection — Slice C: reflect intervention, reflections, additive events

- **`reflect` intervention** (added to the taxonomy / `_VALID_INTERVENTIONS` and the
  planner+reasoner prompts): invites metacognition against the student's OWN goals
  (e.g. "you wanted to explain your reasoning first; how is that going?"). Selectable
  **both ways**: **tutor-offered** (the planner can pick it; it survives the overlay)
  and **student-initiated** (an explicit `request:"reflect"` on the turn, or a
  dialogue cue like "can we reflect on my goal?" → the overlay forces `reflect`).
- **Reflection storage:** `agent/goals.add_reflection` appends the student's words to
  `learner_state.reflections`, timestamped and **linked to the goal in force**
  (`goal_text`). Pseudonymous; recent reflections are injected into `ctx.reflections`
  for the tutor's read; never surfaced to an instructor. Intake: `POST /api/reflection`
  and sidecar `POST /quad/v1/reflection` (same PII boundary).
- **Additive §6 events** — `goal_set` (on intake), `goal_alignment_check` (per turn
  when honored goals exist), `reflect` (on a reflect turn), `reflection_recorded` (on
  intake). **Additive only:** new `event_type` values; the 8-field row shape and the
  `events.jsonl` export contract are unchanged, and `measures` (which keys on
  `run`/`turn`) ignores them. With no goals/reflect, a plain turn still emits exactly
  one `turn` event.

Tests: `tests/test_reflect.py` (10) — reflect selectable both ways, reflect off by
default, reflection stored + linked to its goal, all four additive events present with
the stable row shape, and no extra events without goals. Suite: **295 passed, 1 skipped**;
import boundary holds (4 passed — `agent/goals` imports core only).

```bash
cd backend && python -m pytest tests/test_reflect.py -q
```

---

## Learner goals & reflection - Slice D: wellbeing floor, symmetry of rigor (safety-critical)

The two floors that bound a student-set goal are **not symmetric, and the framework
does not pretend they are**. The
**leak floor** is a post-hoc deterministic gate because leak-detection has a
ground-truth oracle (the grader + the known solution). The **wellbeing floor** has
**no oracle** for tone, so it is **defense-in-depth**, not one gate:

- **Intake detector `goals.is_harmful` (broadened, cautious).** Now also flags
  negative-self-talk invitations, self-deprecation / guilt requests, and
  pressure-to-overwork framings. Biased cautious: a false positive routes a benign
  goal to the kind decline (acceptable); a false negative would honor harm (not
  acceptable). A keyword/shape heuristic, so it has false negatives by nature.
- **Never-honor-framing (provable).** `prompts._goals_block` re-checks the goal text,
  so the honor framing (`SELF-SET GOALS`) is **provably never applied to a
  harm-requesting goal** regardless of the stored `honored` flag. A harm-requesting
  goal can only ever get the decline framing.
- **Persona stance (PRIMARY).** The DS peer stance carries an explicit, non-relaxable
  `WELLBEING FLOOR` line (no berating, no reinforcing negative self-talk, not even on
  request). This is the primary wellbeing protection: a capable model that follows its
  system prompt does not produce contemptuous tone.
- **Post-hoc softener `governance.soften_if_berating` (defense-in-depth BACKSTOP, NOT
  a gate).** An observable heuristic backstop, not the primary protection. Replaces an
  obviously berating/contemptuous draft with a kind redirect and caps confidence;
  surfaced **additively** at `telemetry.components.wellbeing_softened` so an operator
  sees it firing. Its value is highest on **weak self-hosted models**, which can
  produce harsh tone in a way frontier models rarely do; on a strong model it should
  almost never fire. Tuned for **precision on contempt**: it keys on berating /
  contempt / negative-self-talk reinforcement, **never** on bluntness or the delivery
  of a correction, so firm-but-kind honest feedback ("you inverted the condition",
  "this is O(n^2)") passes through unchanged. Runs AFTER the supreme leak gate and
  never relaxes it; false negatives by nature. **Not** a deterministic equivalent of
  the leak gate, and there is **no wellbeing parity with the leak floor**.

The **hard path** (a harmful goal that evades intake AND a complying, berating
reasoner) is tested directly; with a deterministic worst-case stub the pre-hoc prompt
protections cannot bind it, so the post-hoc softener is what mechanically holds the
output. **Distress response** is a recorded, **NOT built**, product + IRB decision
(safe defaults are recorded in the note in `agent/goals.py`).

New tests in `tests/test_goal_safety.py` (now 11, +5): broadened cautious detector
fixtures, honor-framing-never-for-harm (provable), the adversarial hard-path test, a
normal-turn-not-softened check, and the **softener false-positive guard**
(firm-but-kind correction must pass through; contempt is still softened). **Additive
only:** the new `telemetry.components.wellbeing_softened` key; the §6 event row shape
and the `events.jsonl` export contract are unchanged. Suite: **300 passed, 1 skipped**.

```bash
cd backend && python -m pytest tests/test_goal_safety.py -q
```

---

## Customization overlay + embed - Slice E: per-learner seam, reference widget

A bounded, opt-in per-learner customization overlay (the seam a host customizes the
tutor through) plus a documented embed contract and a replaceable reference widget.
With no overlay set, behavior is exactly what it was (defaults are mastery-friendly),
so existing families/tests stay green.

- **Overlay contract (`agent/overlay.py`).** Bounded, ENUMERATED knobs only:
  `persona` (tone / verbosity / framing), `pedagogy` (scaffolding / stretch),
  `accommodation` (reading_level / language). Not a free-form stance string (that is a
  wellbeing-floor bypass + prompt-injection surface). `normalize_overlay` validates
  each value against its enum, falls back to the mastery-friendly default for unknown
  values, and routes every submitted value through the **same `goals.is_harmful`
  detector**. Goals and reflection are the same per-learner surface and keep riding on
  the learner state.
- **One floor-checking path (the load-bearing piece).** A harm-requesting overlay
  field ("be harsh with me", "never let me rest") is DECLINED exactly like a harmful
  goal: recorded, dropped to its default, never honor-framed. `prompts._overlay_block`
  injects ONLY framework-authored phrases keyed by the chosen enum (never the learner's
  raw text) and re-checks each raw value (never-honor guard). The leak gate and the
  wellbeing floor stay **supreme and un-customizable**: no knob yields more of the
  answer (`scaffolding: less` = more independence, NOT more solution), and a worst-case
  stub that tries to honor a harmful overlay is still caught on the OUTPUT by the
  post-hoc softener (Slice D defense-in-depth; EXTRACTION_PLAN §(g)).
- **Carried where goals ride.** The overlay rides on the learner state (additive
  `LearnerState.overlay` JSON column, v4; migration `ALTER TABLE learner_state ADD
  COLUMN overlay JSON;`, dev: recreate `qimvp.db`) and is injected into the
  persona-parameterized prompts via context, exactly like goals. `memory.update` now
  preserves `overlay` across a turn alongside goals/reflections. **Additive only:** a
  new `overlay_set` event type and the `telemetry.components.overlay_declined` /
  `overlay`-carrying turn fields; the 8-field §6 row shape and the `events.jsonl`
  export contract are unchanged.
- **Routes (new).** `POST /quad/v1/overlay` and `POST /api/overlay` (set/clear,
  pseudonymous, PII-checked 422); `/quad/v1/turn` and `/api/sol/turn` accept an inline
  `overlay`. `capabilities.customization` advertises the bounded vocabulary and that
  the floors are not customizable. No new env var.
- **Embed contract + reference widget.** The embed contract covers the
  request/response shape, the floor guarantees, the PII boundary, and that the embed
  never authenticates. `frontend/widget.html` is a single-file, dependency-light,
  replaceable widget: three panes (chat / goals + reflection / trace + signals) plus
  one customization knob (scaffolding), rendering **signals, not verdicts** (a withheld
  solution shows as "held back, with a nudge") and **no grades or rankings**. The host
  owns auth, roster, and persistence.

Tests: `tests/test_overlay.py` (12) - contract bounds/defaults, the floor-routing
(harmful declined like a goal, benign firm overlays honored), the provable never-honor
guard, leak-floor-not-customizable, the adversarial hard path (harmful overlay evading
intake + a berating stub -> not berating, held by the softener), the additive
`overlay_set` event with a stable row shape, the sidecar overlay route + PII 422, and
the widget contract (targets the sidecar, signals-not-verdicts, no grades/rankings,
never authenticates). Two existing exact-shape assertions were extended for the
additive route/column (`test_quad_sidecar.test_no_grade_write_route_exists`,
`test_sql_store.test_unknown_pid_returns_defaults`). Suite: **312 passed, 1 skipped**.

```bash
cd backend && python -m pytest tests/test_overlay.py -q
```

---

## Slice F — first real KnowledgeBase + leak-over-retrieval gate (safety-critical)

> Cross-reference (Slice N): the governance gate (leak, leak-over-retrieval, wellbeing) is
> summarized as-built in `ARCHITECTURE.md`. This section is kept as the build narrative.

> Cross-reference (2026-08-08): `ARCHITECTURE.md`'s governance-gate section now cites two
> 2026 papers landing close to this gate's design (ACL 2026 adversarial-leakage benchmark;
> "Auditable Release Control for Pedagogical Leakage") plus a concrete CS50 stat on why
> prompt-only leak prevention fails at scale. See that section for the citations; not
> repeated here to avoid the two drifting apart.

**Baseline floor (pre-Slice-F, off HEAD `3ea3a98`): `312 passed, 1 skipped`.** Coverage
may not dip below this; the gate test is net-additive.

`knowledge()` stops returning `None` for datascience: it returns a hermetic, lexical
KnowledgeBase over a curated CC-BY corpus, and every retrieved passage passes through
the core governance leak gate before it can enter tutor context.

- **Corpus** (`packs/datascience/knowledge/corpus/corpus.json`, license in
  `knowledge/README.md`): 9 conceptual passages keyed to the three exercises. No passage
  contains a solution; a test screens all of them against all exercises and asserts none
  are dropped.
- **Retriever** (`packs/datascience/knowledge/kb.py`): pure-Python TF-IDF cosine,
  deterministic, stable tie-break by passage id. **Hermetic** — no network, model,
  embeddings, or secrets. A dumb retriever: it does not screen for leaks.
- **The gate** (`agent/governance.screen_passages`): the decision is core's, the
  evidence is the pack's. Each candidate runs through `pack.leak_evidence(passage_text,
  exercise)`; any `is_solution`/`prose_disclosure` passage is dropped. Same oracle as the
  draft gate, single chokepoint — no parallel prompt-only screen.
- **Wiring** (`agent/context.py`): peer/oracle only, and only when the pack ships a KB
  AND the student has asked something (the query signal). Survivors land in
  `ctx["knowledge"]`; the screening summary rides in `ctx["_retrieval"]`. **When
  `knowledge()` is None (e.g. `_skeleton`) or there is no student query, no retrieval
  runs and the context/trace is byte-identical to before.**
- **Trace** (additive): a new `retrieval` event records `retrieved`/`kept`/`dropped`
  counts and, per drop, the passage **id + reason only — never the leaking text**. The
  §6 row stays the fixed 8 fields; `make_event`'s event-type list now includes
  `overlay_set` and `retrieval`.

Tests: `tests/test_knowledge.py` — retrieval relevance + determinism + hermetic; the
corpus discloses no solution; the **leak-over-retrieval block** (a solution-bearing
passage is dropped, never enters context, and the drop event carries id+reason with no
text — the retrieval analogue of `test_student_rule_cannot_leak`); a benign passage is
retained and reaches context; the None-path no-op (and the no-student-query no-op).
`tests/test_import_boundaries.py` stays green (KB in the pack, protocol in
`core/domain`). Suite: **325 passed, 1 skipped** (+13 from `tests/test_knowledge.py`).

```bash
cd backend && python -m pytest tests/test_knowledge.py tests/test_import_boundaries.py -q
```

---

## Slice G — distress-routing layer of the wellbeing floor (safety-critical)

> Promoted (Slice N): the as-built distress and privacy posture, and the two standing IRB
> items, now live in `PRIVACY.md` as the standalone IRB-facing record. This section is kept
> as the build narrative; `PRIVACY.md` is the document to review.

**Baseline floor (pre-Slice-G, off HEAD `fc1d78f`): `325 passed, 1 skipped`.** Coverage
may not dip; the safety tests are net-additive.

A third wellbeing-floor layer (built on Slice D, not duplicating it): when an explicit
distress signal appears on a learner turn or at goal/reflection intake, the tutor
short-circuits to a deterministic, kind frame that surfaces **institution-configured**
support and routes to a human, and suppresses normal tutoring. It never counsels,
diagnoses, assesses severity, names methods, or emits crisis content. **Decision in core
(`agent/distress.py`, `orchestrator._distress_turn`), content in config.**

- **OFF by default.** `DISTRESS_ROUTING_ENABLED=false` ⇒ no detection runs, no
  short-circuit, behavior byte-identical to today (proved by
  `test_disabled_default_runs_normal_tutoring_byte_identical`).
- **Detection** (`distress.has_distress_signal`): a conservative, **explicit-crisis-only**
  lexical signal (self-harm / suicidal ideation). It is a routing trigger, not a
  judgment; it scores no severity and names no methods. It must NOT fire on academic
  despair ("give up on this degree") — a tested negative control. The vocabulary is a
  minimal starter, **IRB-review-required and tunable** (extend via `DISTRESS_SIGNAL_TERMS`).
- **The FILL-IN placeholder is never rendered.** Displayed content is gated on
  `settings.distress_configured` (true once both messages are replaced). Enabled but
  unconfigured ⇒ a **safe generic frame** (no placeholder) + an operator config warning
  (no PII, no learner text).
- **Trace** (additive `distress` event, gated by `DISTRESS_TRACE_ENABLED`): payload is
  **exactly** `{triggered, configured, routed}` — no text, no category, no PII, no
  severity. §6 row stays the fixed 8 fields; `make_event`'s event-type list now includes
  `distress`. No verbatim distressing content is stored at intake (the goal/reflection is
  recorded `honored:false`, `floor:"distress"`, `text:None`).

| Env var | Default | Owner | Purpose |
|---|---|---|---|
| `DISTRESS_ROUTING_ENABLED` | `false` | institution | master switch; off ⇒ byte-identical |
| `DISTRESS_SUPPORT_MESSAGE` | `[FILL-IN: institution support resources]` | **institution + IRB** | resources surfaced to the learner |
| `DISTRESS_ESCALATION_TARGET` | `[FILL-IN: institution escalation contact]` | **institution + IRB** | human escalation route |
| `DISTRESS_TRACE_ENABLED` | `true` | **IRB** | disables the content-free distress event |
| `DISTRESS_SIGNAL_TERMS` | `` (empty) | **institution + IRB** | extra detection terms (comma-separated) |

**Two standing IRB items** (parallel decisions): (1) the **detection boundary** between
academic despair and crisis (tune the vocabulary), and (2) whether even the
**content-free `distress` trace event** may be recorded (`DISTRESS_TRACE_ENABLED`).

Tests: `tests/test_distress.py` (10) — detector explicit-only + negative control;
disabled byte-identical (no detection, no event); enabled+configured routes/suppresses
with a content-free event; enabled+unconfigured safe generic frame with **no `[FILL-IN]`
in learner output** + operator warning; intake goal/reflection not honored and not
stored verbatim; the route surfaces the frame; no verbatim distress text or PII in the
trace; trace-disable writes no event. `tests/test_import_boundaries.py` stays green.
Suite: **335 passed, 1 skipped** (+10).

```bash
cd backend && python -m pytest tests/test_distress.py tests/test_import_boundaries.py -q
```

---

## Slice H — CI quality gate (ruff + mypy) + distress startup warning

**Baseline floor (pre-Slice-H, off HEAD `3fe1c54`): `335 passed, 1 skipped`.** Slice H
adds DEV TOOLING + CI + one visibility log — **no runtime behavior change**. Those 335
stay green (the proof); a single net-additive test for the distress startup warning
takes the suite to **336 passed, 1 skipped**.

**Tooling (dev-only, pinned in `requirements-dev.txt`: `ruff==0.8.6`, `mypy==1.14.1`).
Config in `backend/pyproject.toml`. Run locally from `backend/`:**

```bash
ruff check .            # lint (E,F,W,I,UP,B; E501 + a few narrowed, see pyproject)
ruff format --check .   # style; `ruff format .` to apply
mypy                    # type-check (files=["app"]; py3.12 baseline)
```

CI runs all three once in the `quality` job (`.github/workflows/ci.yml`), alongside the
unchanged `test` matrix (`sqlite`, `postgres`).

- **ruff narrowed rules** (documented in `pyproject.toml`, each a reason, not a blanket
  disable): `E501` (long prompt/persona string literals), `E402` (intentional late
  imports), `E741` (test-only `l`), `B008` (FastAPI `Body/Depends` idiom), `B904`
  (raise-from; ratchet), `UP031`/`UP038` (runtime-expression rewrites). Everything else
  is enforced.
- **mypy baseline (NOT strict):** `warn_unused_ignores`, `warn_redundant_casts`,
  `no_implicit_optional`, `ignore_missing_imports` (third-party libs). Green via 4 small
  annotations + 9 justified `# type: ignore[code]  # reason`. **Scope = `app/`**;
  `core/runner/_child.py` is excluded (it monkeypatches `socket`, not type-checkable).
  **Ratchet (later slices):** bring `tests/` and `evals/` under mypy, then raise
  strictness incrementally (e.g. `disallow_untyped_defs` per-package).
- **Distress startup warning:** when `DISTRESS_ROUTING_ENABLED=true` but support/
  escalation are unset (`[FILL-IN]`), app startup and `python -m app.preflight` emit a
  prominent operator WARNING / `[WARN]` line (no PII, no content) — visibility only.

```bash
cd backend && python -m pytest tests/test_distress.py -q   # incl. the startup-warning test
```

---

## Slice I — mypy + lint tightening ratchet (gate gets stronger, not just greener)

**Baseline floor (pre-Slice-I, off HEAD `e7bbbdb`): `336 passed, 1 skipped`; mypy scope
`app/` (52 files), `ignore_missing_imports=true` (global).** Slice I strengthens the gate
with **zero behavior change** (the only runtime-adjacent edits are B904 raise-from); the
unchanged suite is the proof. Run the gate from `backend/`: `ruff check . && ruff format
--check . && mypy`.

What changed (config in `backend/pyproject.toml`, dev-deps in `requirements-dev.txt`):

- **Missing-imports now have teeth.** The global `ignore_missing_imports` is removed, so a
  missing/mistyped FIRST-PARTY import errors (`[import-not-found]`). Per-module treatment:
  - `pandas` → real stubs `pandas-stubs==3.0.3.260530` (pinned, dev-only) — the only
    library mypy flagged. **No `[[tool.mypy.overrides]] ignore_missing_imports` were
    needed**: numpy, fastapi, sqlalchemy, openai, anthropic, tqdm all ship inline types.
- **mypy scope = `app/` + `tests/` + `evals/` (85 files; `_child.py` excluded).**
- **App-source strictness notch:** `check_untyped_defs` + `warn_return_any`, applied via
  `[[tool.mypy.overrides]] module=["app.*"]`. `tests/`+`evals/` stay at the BASE level
  (their dynamism — fixtures, monkeypatch, stub doubles — would drown the signal).
  **Next ratchet notch (deferred): `disallow_untyped_defs` (full annotation), app-first.**
- **B904 is enforced** in ruff: every `raise` inside an `except` chains (`from err`, 8) or
  deliberately suppresses (`from None`, 5). Chaining only — no control-flow change.
- Deleted two quantum-era dead locals (`target`/`tol`) in `measures.compute_calibration_pairs`.

Suite unchanged: **336 passed, 1 skipped**; ruff + ruff-format + mypy green; CI `quality`
job + both test legs green.

```bash
cd backend && ruff check . && ruff format --check . && mypy && python -m pytest -q
```

---

## Slice J — license-boundary realization (reorganization + SPDX, zero behavior change)

**Baseline floor (off HEAD `72409aa`): `336 passed, 1 skipped`; ruff + ruff-format + mypy
green (85 files).** Slice J isolates the Apache-2.0 contract into a single-license
directory, moves the active-pack registry to the AGPL side, repoints imports, locks the
dependency direction with the import-boundary tripwire, and adds per-file SPDX headers.
This is reorganization plus declarations: **no runtime behavior changes, no license texts
are added, nothing is published.** The unchanged suite is the proof.

What changed (all commits suite-green; each step a separable commit):

- **Contract isolated, registry extracted.** `core/domain/` is now exactly the Apache
  contract: the protocols `DomainPack`/`KnowledgeBase`/`MisconceptionLibrary`
  (`pack.py`) and the value types (`types.py`: `PersonaSpec`, `Concept`, `Taxonomy`,
  `Exercise`, `Module`, `RunResult`, `WorkedExample`, `VerifyResult`, `LeakEvidence`,
  `Passage`). The active-pack registry (which SELECTS/loads implementations) moved
  `git mv core/domain/registry.py -> core/registry.py` (AGPL). `core/domain/__init__.py`
  dropped its registry re-exports. The dependency arrow is impl -> contract: registry
  imports the contract via `from .domain import DomainPack`, never the reverse.
- **Imports repointed, no shim.** Every `get_active_pack` importer moved
  `core.domain -> core.registry` (29 files across app/, evals/, tests/); the two
  combined imports (`analysis/measures.py`, `tests/test_knowledge.py`) split so the
  contract symbol stays on `core.domain` and the registry symbol comes from
  `core.registry`. No backward-compat shim was needed (no circular import forced one).
- **Tripwire locks the direction.** `tests/test_import_boundaries.py::test_contract_imports_nothing_app_internal`
  resolves every module-level import in `core/domain/` to an absolute name and fails if
  any resolves into `app.*` outside `app.core.domain`. Verified it has teeth (injecting a
  registry import into the contract fails the build).
- **Per-file SPDX headers (declarations only).** `Apache-2.0` on the 3 contract files;
  `AGPL-3.0-only` on the other 84 (registry, agent, governance, runner, packs, store,
  integrations, plus tests/evals/conftest). No license texts added, no contacts filled,
  LICENSING.md status stays "not in force", nothing pushed public.

Suite: **337 passed, 1 skipped** (the 336 baseline still pass; +1 is the new contract
tripwire). ruff + ruff-format + mypy green (86 files). The binding line was not crossed.

```bash
cd backend && ruff check . && ruff format --check . && mypy && python -m pytest -q
```

---

## Slice K — de-brand (read as a generalist DS/CS tutor; zero behavior change)

**Baseline floor (off HEAD `6421db4`): `337 passed, 1 skipped`; ruff + ruff-format + mypy
green (86 files).** Slice K removes or generalizes Quantum Inventioneers functional,
example, comment, and branding residue so the repository reads as a generalist data
science and computer science tutor. This is naming, comments, examples, fixtures, and
doc framing, **not logic** — no compatibility code is removed and nothing load-bearing is
renamed without a flag. The unchanged suite is the proof.

What was done (each step a separable commit; suite green after each):

- **Bucket (a) removed/generalized.** Backend comments/docstrings (context, governance,
  orchestrator, measures, schemas, core, the datascience pack) drop quantum as the
  canonical example for the datascience pack or neutral wording. The pytest fixtures are
  de-branded by a consistent, suite-proven rename: concept ids (entanglement ->
  overfitting, superposition -> regularization, phase -> normalization), the
  misconception id (M2.1-superpose-both-is-entangle -> M2.1-scale-before-split), exercise
  ids (bell -> ds-foundations, ghz -> ds-mlp), qubit(s) -> variable(s), and cosmetic
  helper names. The front-end demos (api-client.js, dev-client.html, frontend/README.md),
  the process-measure spec (both copies), the archive script, and the smoke scripts are
  generalized to DS/CS and Sol.
- **Bucket (b) surfaced, not edited.** Provenance/attribution (this runbook's
  Extraction-status sections; the README origin line and its provenance
  pointer) is left intact and compiled into a list for a human disposition decision.
  **The provenance disposition is PENDING the human's decision** (keep a minimal origin
  line / move detailed lineage to the Quantum Inventioneers repo / remove).
- **Bucket (c) left load-bearing, flagged.** The import-boundary guards
  (`test_no_classiq_imports_in_core_or_packs`, `test_core_does_not_import_quantum`) are
  kept — they prevent the removed Classiq dependency and quantum pack from returning and
  document the old split. The pre-v6 result envelope fields `tvd`/`dist`/`bits` and the
  process-measure output keys `tvd_slope`/`avg_tvd_slope` keep their quantum-era names
  because production `measures.py`/`context.py` emit and parse them; renaming is a schema
  migration (logic), out of scope. `app/agent/prompts.py` is left untouched: its quantum
  content (the DSL op list, the `expected_dist`/bitstring worked-example schema, the M2.1
  example) lives in LIVE prompt strings, so generalizing it changes model behavior the
  stubbed suite cannot prove invariant; flagged for a deliberate prompt/logic change.
- **Bucket (c) infra config left, flagged (the `qimvp`/`qi` DB naming).** The default
  store DSN `sqlite:///./qimvp.db` (in `backend/app/config.py`, `backend/Dockerfile`,
  `.env.example`) and the opt-in Postgres credentials/DB `qi:qi@.../qimvp` plus the
  `qi_pgdata` volume (in `docker-compose.yml`, `.github/workflows/ci.yml`) are QI-era
  identifiers, but they are coordinated, load-bearing config: changing the default
  SQLite path changes which DB file a deployment uses (a runtime/behavior change the
  suite cannot prove invariant), and the Postgres credentials/DB name must stay
  consistent across compose, CI, Dockerfile, and any `DATABASE_URL`. Recommended as a
  dedicated, coordinated infra rename (config default + Dockerfile + compose + CI +
  .env.example + docs in one change, accepting the default-DB-path migration), not a
  drive-by edit in a de-brand. The stale container-name example in the runbook
  (`quantum-inventioneers-db-1`, now `peer-tutor-framework-db-1` by directory) falls
  under the same item.
- **Drive-by fix (flagged).** `scripts/smoke_inference.py` and `scripts/extract_measures.py`
  still imported `get_active_pack` from `app.core.domain` (Slice J moved it to
  `app.core.registry` but missed `scripts/`); repointed so they import cleanly.

Suite: **337 passed, 1 skipped** (unchanged from the Slice K baseline). ruff + ruff-format
+ mypy green. No runtime behavior changed.

```bash
cd backend && ruff check . && ruff format --check . && mypy && python -m pytest -q
```

---

## Slice L — make the dead DS claim-check explicit and safe (zero behavior change)

**Baseline floor (off HEAD `346e548`): `337 passed, 1 skipped`.** Slice L does NOT
un-dead the DS worked-example prediction claim-check; it makes the current inert state
explicit and safe: a characterization test locks `claim_ok is None` for a DS prediction,
the `claim_ok` consumers are audited to confirm `None` is never read as a pass or a fail,
and the deferral is documented. The prompt, the contract field, and the safety gate are
untouched.

What was done (each step a separable commit; suite green after each):

- **Characterization test (`tests/test_worked_example.py::test_ds_claim_check_is_inert_with_expected_dist`).**
  Asserts that when a prediction is supplied the way the reasoner prompt actually supplies
  it — via the quantum-shaped contract field `expected_dist` — `verify_worked_example`
  returns `ok=True`, `reason="verified"`, and `claim_ok is None`: the claim-check does not
  run, the supplied prediction is ignored, and the safety result is returned normally. The
  pre-existing tests only exercised `expected_stdout` (the field DS reads), so this dead
  branch was uncovered. This test is the guard that FLIPS when real DS expected-output
  verification lands with the prompt de-quantum.
- **`claim_ok` contract (three-valued).** `True` = prediction verified, `False` = prediction
  mismatched, `None` = no claim-check performed (never a pass, never a fail). For DS it is
  currently ALWAYS `None`.
- **Consumer audit — no consumer misreads `None`; nothing needed fixing (no behavior change).**
  - `orchestrator.py:348` records `we.get("claim_ok")` verbatim into
    `telemetry.worked_example`. The decision to SHOW the example and every learner-facing
    message branch only on `we["ok"]` (the safety result), never on `claim_ok`. The learner
    is never told a prediction was correct/incorrect on the basis of a `None`.
  - `process_measures.md` §5d: the aggregates key on `telemetry.worked_example.verified` /
    `shown` / count, NOT on `claim_ok`; the spec already states `claim_ok == null` is "not a
    failure". `measures.py` does not read `claim_ok` at all.
  - The front-end and the learner model do not read `claim_ok`.
- **Deferral documented** in the `WorkedExample` contract and `DataSciencePack.verify_worked_example`
  docstrings: the real DS claim-check is bundled with the eval-gated reasoner-prompt
  de-quantum, and must not be forced on without the DS-shaped prompt.
- **Untouched (confirmed by diff against `346e548`):** `app/agent/prompts.py` (0 changes),
  the contract field names `expected_dist`/`expected_stdout` (not renamed or populated), and
  the safety gate / inert branch in `verify_worked_example` (only the docstring changed; no
  logic line added or removed).

Suite: **338 passed, 1 skipped** (337 baseline + the one characterization test). ruff +
ruff-format + mypy green. The only diff to runtime code is docstrings; no behavior changed.

```bash
cd backend && ruff check . && ruff format --check . && mypy && python -m pytest -q
```

---

## Slice M — doc + provenance cleanup (docs only, zero behavior change)

**Baseline floor (off HEAD `e77e053`): `338 passed, 1 skipped`.** Slice M resolves the
dangling `docs/*.md` references (none of the six targets exist anywhere, so each is
removed, not stubbed) and reframes provenance concept-first: Sol is the original
framework, its first implementation was built within the quantum tutor, and the
domain-agnostic core was extracted from it. Docs and links only: no code edited, git
history untouched, no stub docs manufactured.

What was done (each step a separable commit; suite + gate green after each):

- **Census.** All six referenced `docs/*.md` targets exist NOWHERE in the repo (there is
  no `docs/` directory and no root alias): `PROVENANCE.md`, `ARCHITECTURE.md`,
  `PRIVACY.md`, `ROADMAP.md`, `EXTRACTION_PLAN.md`, `quad-tutor-protocol.md`. Living root
  docs: `README.md`, `VALIDATION.md`, `LICENSING.md`, `CONTRIBUTING.md`,
  `CODE_OF_CONDUCT.md`, `DCO` (plus `process_measures.md` at root and duplicated under
  `backend/app/analysis/`, and the pack docs under `backend/app/packs/datascience/`).
- **Link fixes (Step 3).** Every dangling `docs/*.md` reference in the markdown docs was
  removed (none repointable — no target exists; no stub created). Touched: `README.md`,
  `VALIDATION.md`, both `process_measures.md` copies, the datascience knowledge `README.md`.
  After this, a scan for internal markdown doc links finds none broken.
- **Provenance reframe (Step 4).** README single origin line: "This framework is original;
  its first implementation was built within a quantum tutor, and the domain-agnostic core
  was extracted from that codebase. The quantum-specific application stays in its own
  repository and is not part of this framework." (was: "the quantum application it was
  extracted from stays in its origin repo ... (`docs/PROVENANCE.md`)"). VALIDATION Phase 0:
  "carried over from `quantum-inventioneers` @ `9b19cd5` — the codebase where the
  framework's first implementation was built" (was: "ported from the origin app
  `quantum-inventioneers` @ `9b19cd5`"). The commit ref and all build-phase / extraction
  records are kept verbatim.
- **Out of scope (no code edits), reported not fixed.** Prose `docs/*.md` mentions remain
  in code/config comments — `agent/goals.py` (EXTRACTION_PLAN), `integrations/quad/router.py`
  (quad-tutor-protocol), `store/models.py` (PRIVACY), `docker-compose.yml`
  (quad-tutor-protocol). These are source comments, not markdown doc links; the no-code-edits
  invariant leaves them for a future comment-cleanup.
- **Untouched:** git history (first commit `7cd6b11` immutable; no amend/squash/rebase), all
  code, and no new doc file manufactured.

Suite: **338 passed, 1 skipped** (unchanged — docs only). ruff + ruff-format + mypy green.

```bash
cd backend && ruff check . && ruff format --check . && mypy && python -m pytest -q
```

---

## Slice N — doc consolidation (standalone publish + IRB docs, zero behavior change)

**Baseline floor (off HEAD `3e22d2f`): `338 passed, 1 skipped`.** Slice N promotes the
real decision records out of this runbook into standalone publish/IRB-facing docs, sourced
from the code and this file as-built: `PRIVACY.md` (privacy + distress safety + the two
standing IRB items), `ARCHITECTURE.md` (contract vs implementation, seams, runner,
governance gate, the §6 envelope), and `ROADMAP.md` (the consolidated forward edges). It
cross-references those docs from here (no runbook records deleted) and repoints the four
code/config comments that named deleted docs. Docs and comments only: no code logic
changed.

What was done (each doc a separable commit; suite + gate green after each):

- **Created `PRIVACY.md`** (IRB-facing): privacy-by-architecture and the as-built distress
  posture, each section citing its source (agent/distress.py, orchestrator._distress_turn,
  config.py distress fields, store/, the README/CONTRIBUTING privacy statements, and this
  runbook's Slice G). Marks the two standing IRB items.
- **Created `ARCHITECTURE.md`** (publish-facing): the Apache contract vs AGPL
  implementation, the DomainPack/KnowledgeBase seams, the sandbox runner, the governance
  gate, the §6 envelope, and the HTTP surface, accurate to the current module layout.
- **Created `ROADMAP.md`**: the consolidated forward edges (next work, deferred edges, the
  mypy ratchet), each item cited in-tree.
- **Cross-references, no deletions:** Slice F and Slice G now point to ARCHITECTURE.md and
  PRIVACY.md; all runbook records are retained.
- **Stale comments repointed:** `agent/goals.py` (EXTRACTION_PLAN -> VALIDATION wellbeing
  records), `integrations/quad/router.py` and `docker-compose.yml` (quad-tutor-protocol ->
  ARCHITECTURE.md + VALIDATION; no separate protocol doc created, since the sidecar
  protocol is covered by ARCHITECTURE.md, this runbook, and the self-describing
  `/quad/v1/capabilities` endpoint), `store/models.py` (docs/PRIVACY.md -> PRIVACY.md).
- **Em-dash fix:** the Phase 0 provenance line is now dash-free (house style).

`PROVENANCE.md` was deliberately NOT recreated (provenance is the single factual line in
the README). Suite: **338 passed, 1 skipped** (unchanged, docs/comments only); ruff +
ruff-format + mypy green.

---

## Slice O — pack-scoped knowledge-corpus pipeline (license-gated, decoupled indexing)

**Baseline floor (off HEAD `6bb53b4`): `338 passed, 1 skipped`.** Slice O adds a
domain-reusable, license-gated ingestion pipeline that produces a normalized per-pack
corpus artifact (`app/knowledge/`), a separate lexical (BM25) indexing step over it, and a
corpus-backed `KnowledgeBase` behind the unchanged Apache contract. Ingestion is decoupled
from indexing (the corpus is the durable asset; the index is a separate build). Retrieved
passages flow through the existing Slice F `screen_passages` leak gate (no second screen),
proven at corpus scale. Per-passage license + attribution are recorded and travel with a
surfaced passage end to end (in `Passage.citation`). Only a tiny DS+CS seed corpus ships;
real sources are a later operator ingestion step. Additive: the None path stays
byte-identical.

What was built (each step a separable commit; suite + gate green after each):

- **Schema + license gate** (`app/knowledge/schema.py`): the `CorpusPassage` record
  (`id, pack, source, license, attribution, tags, text`) and a family-aware whitelist
  (public-domain, CC0, CC-BY, MIT, Apache-2.0, BSD) that rejects share-alike / non-commercial
  / no-derivatives / GPL-AGPL first, so CC-BY-SA / CC-BY-NC are refused despite the CC-BY
  prefix.
- **Ingestion** (`app/knowledge/ingest.py`): pack-parameterized, source-agnostic; gates each
  source, logs and reports rejections, normalizes admitted content, writes the corpus
  artifact, and builds no index.
- **Indexing + KB** (`app/knowledge/index.py`, `corpus_kb.py`): a separate Okapi BM25 index
  step and `CorpusKB` implementing the unchanged Apache `KnowledgeBase` contract; attribution
  + license ride in `Passage.citation`.
- **Seed corpus + wiring**: `corpus/corpus.json` migrated to the normalized schema (9 DS
  passages preserved + 4 CS passages), `DataScienceKB` reimplemented as a corpus-backed BM25
  subclass; `knowledge()` unchanged in shape; the `_skeleton`/None path byte-identical. A
  test-only `corpus/leak_fixture.json` holds the solution-bearing passage, kept out of the
  production corpus.
- **Tests** (`tests/test_knowledge_corpus.py`, +8): license-gate rejection; ingestion/index
  decoupling; pack-scoped reuse; BM25 relevance + determinism; leak-over-retrieval blocked at
  corpus scale (unit + end to end, no leak text in prompt/trace); attribution end to end.

Reused the Slice F `screen_passages` gate unchanged (no second screen). Suite: **346 passed,
1 skipped** (338 baseline + 8); ruff + ruff-format + mypy green; `tests/test_import_boundaries.py`
green. Docs updated (`ARCHITECTURE.md`, `README.md`, `ROADMAP.md`, `LICENSING.md`, the pack
knowledge README) with the operator-ingestion note and the lexical-now / local-vector-later
swap.

```bash
cd backend && ruff check . && ruff format --check . && mypy && python -m pytest -q
```

---

## 0. Environment (once) 🟢

Use the project venv, and **always invoke the suite as `python -m pytest`** — a bare
`pytest` can resolve to another interpreter on PATH (e.g. conda base) that lacks the deps.

```bash
cd backend
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt -r requirements-packs.txt
```

Sanity: `python -c "import sqlalchemy, fastapi, openai, pytest, numpy, pandas; print('deps ok')"`
(`requirements-packs.txt` adds numpy + pandas for the data-science pack.)

---

## 1. Offline core suite 🟢

No network, no DB, no key. This is the gate `main` must always pass.

```bash
cd backend && python -m pytest
```

**Expected (current, through Slice I, datascience active): `336 passed, 1
skipped`.** The single skip is the gated live behavioral benchmark
(`tests/evals/test_behavioral.py::test_live_benchmark_runs`), which skips unless
`RUN_LLM_EVALS=1` and a reachable tutor + judge endpoint are configured (see §3).
The running per-phase totals are recorded in the phase sections above (from `221
passed, 11 skipped` at Phase 0 to `336 passed, 1 skipped` at Slice I). The
quantum-era per-module table below is **historical** (those modules no longer
exist, and the legacy `sol_behavior_evals.py` was retired into
`evals/behavioral/` in Slice 6b); the current per-module inventory is the appended
phase sections above.

| Module | Tests | Covers | Phase 1 |
|---|---|---|---|
| `tests/test_simulator.py` | 6 | quantum-grader parity | → del (pack) |
| `tests/test_functional_model.py` | 6 | functional-model compiler + error paths | → del (pack) |
| `tests/test_governance.py` | 7 | deterministic no-full-solution leak gate | → re-base (quantum leak fixture) |
| `tests/test_orchestrator_smoke.py` | 5 | full evaluation-first loop, stub LLM | core (re-base fixture) |
| `tests/test_stance.py` | 29 | stance peer/oracle/control · escalation · abstention · confidence trajectory | core (re-base fixture) |
| `tests/test_measures.py` | 56 | §6 process measures (§2–§5b, §5c affect, §5d worked-example, §5e learner-model, ECE/Brier) | core (re-base fixture) |
| `tests/test_affect.py` | 12 | planner affect-adaptive overlay: encourage, flow→observe, teach/goal_met/oracle precedence | core (re-base fixture) |
| `tests/test_consent.py` | 15 | consent-gated logging (durable vs ephemeral, fail-safe) | core |
| `tests/test_sql_store.py` | 15 | SqlStore CRUD + durability + concepts column round-trip (SQLite here; Postgres in §2) | core |
| `tests/test_learner_model.py` | 40 | concept taxonomy · update_concepts · due_review · planner revisit overlay | core + pack taxonomy |
| `tests/evals/sol_behavior_evals.py` | 11 (skipped) | behavioral fidelity + affect encourage quality + worked_analogy verification + revisit quality — see §3 | → del (pack evals) |
| `tests/test_misconceptions.py` | 24 | F6 misconception-tailored dialogue | core + pack content |
| `tests/test_worked_example.py` | 6 | self-verifying worked-example verifier (compile + non-leak + prediction) | → del (pack) |

Individual offline scripts (no pytest):

```bash
PYTHONPATH=. python tests/test_governance.py          # leak gate
PYTHONPATH=. python tests/test_orchestrator_smoke.py  # full loop, stub LLM
```

---

## 2. Durable store — SQLite + Postgres 🟢/🟡

**SQLite (offline):** with no `DATABASE_URL` set, the store uses `sqlite:///./qimvp.db`.

```bash
python backend/scripts/smoke_sql.py        # all checks PASS (SQLite round-trip + durability)
```

**Postgres (Docker):**

```bash
docker compose up -d db                     # publishes HOST port 5433 → container 5432
docker compose ps                           # quantum-inventioneers-db-1 is Up
docker compose logs db | tail               # "database system is ready to accept connections"

export DATABASE_URL=postgresql+psycopg://qi:qi@localhost:5433/qimvp
python backend/scripts/smoke_sql.py         # PASS against Postgres
python -m pytest backend/tests/test_sql_store.py   # expect 14 passed
```

**2-worker HTTP round-trip** (`backend/scripts/smoke_sql_http.md`):

Validates cross-worker consent routing and event persistence over real HTTP using two
uvicorn workers and shared Postgres — without any LLM. Open `smoke_sql_http.md` for the
step-by-step walkthrough. The key assertion: `GET /api/session/{pid}/events.jsonl`
returns the same 2 events regardless of which worker handled each `POST /api/run`.
Documents the known §6f sticky-session limitation for non-consenting participants across
workers (non-consenters' ephemeral store is per-worker).

**Schema migration — `concepts` column:** `LearnerState` gained a `concepts JSON`
column (v2, 2026-06-03). `init_db`/`create_all` will NOT alter an existing table.
For dev: `rm backend/qimvp.db` (no real data yet). For a deployed Postgres:
```sql
ALTER TABLE learner_state ADD COLUMN concepts JSON DEFAULT '{}';
```

**Gotcha — port 5432:** the project DB uses host port **5433** on purpose. A native
Postgres bound to loopback `5432` will shadow `localhost:5432` and answer with
`role "qi" does not exist`, and Docker can't bind 5432 if anything else holds it
("port is already allocated"). Diagnose with `lsof -nP -iTCP:5432 -sTCP:LISTEN` and
`docker ps`; leave your native PG alone and use 5433. If `localhost` logs a harmless
IPv6 miss before connecting, use `127.0.0.1` in the DSN instead.

---

## 3. Live model layer — the provider seam 🔴

The provider is selected by `PROVIDER` (see Phase 2 Workstream B for the matrix).
The default `openai_compatible` targets any OpenAI-compatible endpoint via
`OPENAI_BASE_URL` (local Ollama/vLLM → zero external API, Workstream D; or the
Jetstream2 Open WebUI proxy). `smoke_inference.py` probes tier reachability,
served model ids, per-role JSON reliability, reasoning_effort passthrough, and
end-to-end turns:

```bash
# local Ollama (zero external API):
PROVIDER=openai_compatible OPENAI_BASE_URL=http://localhost:11434/v1 OPENAI_API_KEY=ollama \
  MODEL_FAST=llama3.2 MODEL_STRONG=llama3.2 python backend/scripts/smoke_inference.py

# Jetstream2 via the Open WebUI proxy:
PROVIDER=openai_compatible OPENAI_BASE_URL=https://llm.jetstream-cloud.org/api \
  OPENAI_API_KEY=<token> MODEL_FAST=llama-4-scout MODEL_STRONG=gpt-oss-120b \
  REASONING_STRONG=high python backend/scripts/smoke_inference.py
```

**Behavioral benchmark** (the source of the single skip in §1) — needs a reachable
LLM. The legacy `tests/evals/sol_behavior_evals.py` was retired into the portable
benchmark `evals/behavioral/` (Slice 6b); the canonical way to run it is documented
in the Slice 6b section above. The gated live test
(`tests/evals/test_behavioral.py::test_live_benchmark_runs`) is what skips by default;
the zero-external-API path (local Ollama) is the recommended way to run it. In short,
from `backend/`:

```bash
# the gated live test (skips unless RUN_LLM_EVALS is set + an endpoint is reachable):
RUN_LLM_EVALS=1 python -m pytest tests/evals/test_behavioral.py

# or the full benchmark CLI (set a distinct JUDGE_MODEL so the judge is not self-judging):
PROVIDER=openai_compatible OPENAI_BASE_URL=http://localhost:11434/v1 OPENAI_API_KEY=ollama \
  MODEL_FAST=llama3.2 MODEL_STRONG=llama3.2 JUDGE_MODEL=deepseek-r1:1.5b \
  python -m evals.behavioral --pack datascience --repeats 5 --out report.json
#   gate verdicts (deterministic): never_leak, no_solution ·
#   framework routing: encourage_frustration/disengaged, redirect_answer_seeking,
#                      reciprocate_in_teach, revisit ·
#   judge signals (separate strong judge): grounded, concrete, question
```

New §5c affect-response measures (computed offline from trace): `negative_affect_rate`,
`affect_support_rate`, `affect_recovery_rate`. See also `tests/test_affect.py` for the
deterministic planner overlay tests.

New §5d worked-example verification measures (computed offline from trace):
`worked_example_count`, `worked_example_verified_rate`, `worked_example_retry_rate` —
all exercised by `tests/test_measures.py`. The worked-example verifier is now the
active pack's `verify_worked_example` (DS: `packs/datascience`, runs in the
sandbox). NOTE: both `process_measures.md` copies (canonical
`backend/app/analysis/` and root) must be updated together.

New §5e learner-model measures (computed cross-exercise from trace + end concepts snapshot):
`concepts_ever_shaky`, `shaky_resolution_rate`, `revisit_count`,
`revisit_resolution_rate`, `nonrevisit_resolution_rate`. Module: `measures.compute_learner_model_measures`.
Requires `LearnerState.concepts` column — see schema migration note in §2.

---

## 4. Execution sandbox — `core/runner` 🟢

The active pack (datascience) runs untrusted student code through `core/runner`
(subprocess, isolated temp cwd, CPU + wall limits, network made unreachable). The
former quantum/Classiq platform section is removed (quantum deleted in Phase 1d).

```bash
cd backend && python -m pytest tests/test_runner_sandbox.py    # 23 passed, 1 skipped
```

Threat model is documented in the `app/core/runner` module docstring and the
Phase 1b status section above: a resource/network/isolation boundary, not
adversarial containment; the containerized runner is the roadmap convergence
point with Quad's ephemeral sandboxed graders.

---

## 5. Server + dev client 🟢/🟡

```bash
cd backend && uvicorn app.main:app --reload             # backend on :8000
curl -s http://localhost:8000/healthz                    # {"ok": true, ...}
```

Then open `frontend/dev-client.html` and point its backend field at
`http://localhost:8000`. **Note:** `dev-client.html` routes entirely through the
backend (no browser-side key), but it uses a **hardcoded `PID = "p_dev"`** without
consent registration — **dev only; do not use it for a pilot session.** For a
host-embed surface that takes an already-authenticated pseudonymous id (no hardcoded
PID), use `frontend/widget.html` (the Slice E/F reference widget). (The origin quantum
React client was removed in Phase 1d.)

Opt-in learner-customization intake (pseudonymous; PII-checked): `POST /api/goals`,
`POST /api/reflection`, and `POST /api/overlay` (Slice E), mirrored on the sidecar as
`POST /quad/v1/{goals,reflection,overlay}`. The host-embeddable **reference widget**
is `frontend/widget.html` (single-file, dependency-light; three panes + one
customization knob; signals-not-verdicts; never authenticates) — point its sidecar
field at `http://localhost:8000`. The minimal one-turn smoke is `frontend/embed-demo.html`.

Optional — analysis pipeline (offline, from an exported trace):

```bash
python backend/scripts/extract_measures.py <path/to/trace.jsonl>   # writes the three §6 outputs
```

---

## Maintenance map

When you add… | …update here
---|---
a `tests/test_*.py` module | §1 table + the expected pass/skip count
a `scripts/smoke_*.py` | the matching section (§2/§3/§4) + add its expected output
a new env var / config knob | the section that uses it (and `backend/app/config.py`, the source of truth; there is no `.env.example`)
a new API route | §5 (and a curl/health example if relevant)
a new external dependency (instance/account/allocation) | mark the step 🔴 and name the blocker
