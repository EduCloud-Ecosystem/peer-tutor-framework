# Architecture

A publish-facing overview of the system as built. It describes the current module layout,
the contract-versus-implementation boundary, the domain seams, the sandbox runner, the
governance gate, and the trace envelope. Each section cites where it lives in the tree.

## Contract versus implementation (the license boundary)

The framework is split on purpose. The portability contract is Apache-2.0 so anyone can
write a pack, an alternative runner, or a different tutor against the same interfaces
without taking on copyleft. The running implementation is AGPL-3.0.

- **Apache-2.0 contract**, realized in `backend/app/core/domain/`: the `DomainPack`,
  `KnowledgeBase`, and `MisconceptionLibrary` protocols (`core/domain/pack.py`) and the
  value types that define them (`core/domain/types.py`: `PersonaSpec`, `Concept`,
  `Taxonomy`, `Exercise`, `Module`, `RunResult`, `WorkedExample`, `VerifyResult`,
  `LeakEvidence`, `Passage`). This package imports nothing app-internal; it is the base of
  the dependency graph, and an import-boundary tripwire fails the build if an
  implementation leaks back into it.
- **AGPL-3.0 implementation**: the active-pack registry (`core/registry.py`, moved out of
  `core/domain/` so the contract stays single-license), the agent loop (`agent/`), the
  governance gate, the sandbox runner (`core/runner/`), the datascience pack
  (`packs/datascience/`), the store (`store/`), and the integrations
  (`integrations/quad/`).

Source: `LICENSING.md` (the split and the realized paths),
`backend/tests/test_import_boundaries.py` (`test_contract_imports_nothing_app_internal`,
`test_core_does_not_import_quantum`, `test_no_classiq_imports_in_core_or_packs`).

## Domain seams

A pack is the only domain-specific code. Core depends on the protocol, never on a concrete
pack module; the dependency arrow points from packs to core.

- **`DomainPack`** (`core/domain/pack.py`): `curriculum`, `get_exercise`, `run`,
  `program_signature`, `verify_worked_example`, `misconceptions`, `leak_evidence`, and
  optional `knowledge`. The reference implementation is `packs/datascience/`; a
  dependency-free echo pack for core-only tests is `packs/_skeleton/`.
- **`KnowledgeBase`** (`core/domain/pack.py`, `search(query, k) -> list[Passage]`): an
  optional per-pack retrieval source. The datascience KB
  (`packs/datascience/knowledge/kb.py`) is a hermetic, pure-Python retriever over a
  curated, license-gated corpus; no network, model, embeddings, or secrets.
- **Selection**: the active pack is chosen at runtime by `TUTOR_PACK` via the registry
  (`core/registry.py`); core never imports a pack at module load.

### The knowledge-corpus pipeline (`app/knowledge/`)

A domain-reusable, AGPL tool that produces and serves a pack's corpus, with ingestion
decoupled from indexing so the retrieval method stays swappable behind the contract.

- **Pack-scoped corpus.** The pipeline is parameterized by pack id and sources; the
  normalized corpus is the pack's own, loaded through that pack's `knowledge()`. There is no
  shared global blob; a future quantum pack would build its own corpus with the same tool.
- **License gate (non-negotiable).** Ingestion (`app/knowledge/ingest.py`) admits only
  whitelisted-license content (public-domain, CC0, CC-BY, MIT, Apache-2.0, BSD), records
  per-passage license and attribution, and rejects everything else (share-alike,
  non-commercial, no-derivatives, GPL/AGPL) with a logged reason
  (`app/knowledge/schema.py`).
- **Decoupled indexing.** Ingestion writes a normalized corpus artifact and nothing else;
  indexing (`app/knowledge/index.py`) is a separate step. Retrieval is lexical (BM25) now;
  a local-embedding vector index could replace or supplement it later behind the same
  `KnowledgeBase` contract with no re-ingest. Any future embeddings run locally, never a
  hosted API.
- **Attribution travels with the passage.** The corpus-backed KB (`app/knowledge/corpus_kb.py`)
  surfaces each passage's attribution + license in the contract's `Passage.citation` field,
  which the context layer already passes to the prompt.
- **One leak gate, at scale.** Retrieved passages flow through the same Slice F
  `governance.screen_passages` (reusing `pack.leak_evidence`) before any can enter context;
  there is no second screen. Proven to drop a seeded solution-bearing passage at corpus
  scale.
- **Seed only.** Only a tiny DS+CS seed corpus ships in-tree; ingesting real sources is a
  later operator step. See `ROADMAP.md`.

Source: `backend/app/core/domain/pack.py`, `backend/app/knowledge/`,
`backend/app/packs/datascience/knowledge/`, `backend/app/core/registry.py`, `README.md`
("Adding a domain pack"), `VALIDATION.md` Slice F (the leak-over-retrieval gate) and
Slice O (the corpus pipeline).

## The agent loop

One turn (`agent/orchestrator.run_turn`) is evaluation-first: a Planner picks one
pedagogical move, a Peer-Reasoner writes the message, a Self-Evaluation critiques it
against a stance rubric, a bounded refine fixes a failing draft, the deterministic
Governance gate runs last, and Memory merges what the student now grasps. Stance
(peer / oracle / control) and the fast/strong tier policy (Planner fast, Reasoner strong,
Self-Evaluator fast) live in core and are provider-agnostic; only the tier-to-model
mapping is per-provider config.

Source: `backend/app/agent/` (`orchestrator.py`, `planner.py`, `reasoner.py`,
`self_eval.py`, `memory.py`, `context.py`, `learner_model.py`, `prompts.py`, `llm.py`,
`telemetry.py`), `README.md` ("What it is").

## The sandbox runner

Student code never executes directly in the main process or host OS environment. The runner 
(`core/runner/run_python`) routes submissions through an isolated Docker container 
(`core/runner/_sandbox.py`) when enabled, with resource limits (CPU, memory, wall clock) 
and network blocking applied. When container execution is unavailable, it falls back to a 
local process with standard limits if explicitly allowed (`SANDBOX_ALLOW_INSECURE=true`).

Grading logic runs safely on the host via pack harnesses (`packs/datascience/_harness.py`), 
exchanging structured input/output (`result.json`) with student code executing inside 
the container.

Source: `backend/app/core/runner/__init__.py` (`run_python`, `RunnerResult`),
`backend/app/core/runner/_sandbox.py`, `backend/app/core/runner/_harness.py`,
`backend/app/packs/datascience/_harness.py`, `backend/app/packs/datascience/grader.py`.

## The governance gate

Governance is deterministic and runs last, after the model has written and self-evaluated.
It is supreme over everything, including a student goal that demands the answer.

- **Leak gate** (`governance.check`, `governance.safe_rewrite`): the no-leak rule is an
  executable gate, not a prompt instruction. Leak detection has a ground-truth oracle (the
  pack's grader plus the known solution via `pack.leak_evidence`), so the gate decides
  post-hoc and strips any full solution.
- **Leak-over-retrieval** (`governance.screen_passages`): every retrieved KB passage runs
  through the same `pack.leak_evidence` oracle before it can enter tutor context; any
  solution-bearing passage is dropped (id + reason recorded, never the leaking text).
- **Wellbeing floor**, three layers: the intake harm detector (`agent/goals.py`, guards
  the learner asking the tutor to be unkind), the tone softener
  (`governance.soften_if_berating` / `is_berating`, guards the tutor's own tone), and the
  distress-routing layer (`agent/distress.py`, routes the learner outward to a human; see
  `PRIVACY.md`).

Source: `backend/app/agent/governance.py`, `backend/app/agent/goals.py`,
`backend/app/agent/distress.py`, `PRIVACY.md`, `VALIDATION.md` Slice F (leak-over-retrieval)
and Slice G (distress).

**Academic grounding, added 2026-08-08 after a provider/prior-art scan** (full scan:
`morph-full-and-provider-landscape-2026-08-08.md`, Cowork project). Two 2026 papers land
close enough to this gate's own design to be citable prior art rather than internal
rationale alone:

- Harvard's CS50 Duck reports that **prompt-only** leak prevention measurably degrades at
  scale — 22% of responses still contained full code despite explicit anti-leak
  instructions, once the system prompt passed ~1,000 tokens ("Improving AI in CS50:
  Leveraging Human Feedback for Better Learning," ACM 2024/2025,
  `dl.acm.org/doi/10.1145/3641554.3701945`). Direct empirical evidence for why this gate is
  a ground-truth oracle and not a system-prompt instruction.
- **"Evaluating Answer Leakage Robustness of LLM Tutors against Adversarial Student
  Attacks"** (ACL 2026, arXiv 2604.18660) builds a fine-tuned adversarial-student-agent
  benchmark for jailbreaking tutors into leaking answers — the reference methodology for
  a regression suite against this gate. Not yet built here; queued as
  `docs/prompts/CC-B2-adversarial-leak-benchmark.md`.
- **"Auditable Release Control for Pedagogical Leakage in LLM Tutors"** (arXiv 2608.00515)
  describes a modular disclosure-control system — deterministic fast-checker plus optional
  semantic verifier, a graduated 5-level disclosure contract (A₀–A₄), fail-safe fallback on
  any check failure — close enough to this gate's shape to be worth reading directly if the
  binary leak/no-leak decision is ever revisited for something more graduated.

This gate remains a ground-truth oracle (`pack.leak_evidence`), not a learned classifier —
nothing above changes that; it only means the design now has published backing rather than
resting on internal rationale alone.

## The trace and the pack-result envelope

- **Run-result envelope** (`RunResult`, `core/domain/types.py`): pack-agnostic top level
  `{ok, goalMet, metric, error, pack}`. All domain-specific result data lives inside the
  namespaced `pack` envelope (for example DS checks / stdout / summary); `metric` is the
  pack's primary scalar or None. This keeps the trace schema stable across packs (schema
  v6).
- **The trace** (`store/`): an append-only event stream. Every event is the same fixed
  eight-field row (`participant_id`, `ts`, `exercise_id`, `mode`, `event_type`, `stance`,
  `payload`, `note`) built by `store/repository.make_event`. `event_type` values are
  additive (`run`, `turn`, `goal_set`, `goal_alignment_check`, `reflect`,
  `reflection_recorded`, `overlay_set`, `retrieval`, `distress`); adding one does not
  change the row or the `events.jsonl` export contract. Per-component telemetry rides in
  `payload`.

Source: `backend/app/core/domain/types.py` (`RunResult`), `backend/app/store/models.py`,
`backend/app/store/repository.py` (`make_event`), `VALIDATION.md` (the §6 result
genericization note and Phase 2 Workstream C telemetry).

## HTTP surface

The main app (`main.py`) serves the tutor loop and session routes (`/api/run`,
`/api/sol/turn`, `/api/participant`, `/api/session/{pid}/events.jsonl`, `/api/goals`,
`/api/reflection`, `/api/overlay`, `/healthz`, `/api/curriculum`). The Quad sidecar
(`integrations/quad/`, base path `/quad/v1`) exposes the same loop to the EduCloud Quad
control plane: it imports core only, advertises its posture at `/quad/v1/capabilities`
(pseudonymous identity, grades read-only with no write path, Apache-2.0), runs one tutor
turn at `/quad/v1/turn`, and rejects PII at the boundary with a 422 (`integrations/quad/pii.py`).

Source: `backend/app/main.py`, `backend/app/integrations/quad/` (`router.py`, `pii.py`,
`schemas.py`), `VALIDATION.md` Quad sidecar section, `README.md` ("Quad sidecar").

## Related records

- Privacy and the distress safety posture: `PRIVACY.md`.
- Licensing split and per-file SPDX: `LICENSING.md`.
- Build-phase narrative, test inventory, and the canonical runbook: `VALIDATION.md`.
- Forward work: `ROADMAP.md`.
