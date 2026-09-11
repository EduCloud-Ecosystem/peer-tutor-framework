# Roadmap

The forward edges, consolidated from where they are recorded in-tree (VALIDATION.md and
code comments) into one document. Each item cites its in-tree source so it can be checked
against ground truth. This is a description of what is recorded as deferred or next, not a
schedule or a commitment.

## Known next work

- **Jailbreak/prompt-injection detection — none exists today.** A 2026-08-08 provider scan
  (`morph-full-and-provider-landscape-2026-08-08.md`, Cowork project) found this gate has no
  counterpart in the framework: the governance gate covers leak, tone, and explicit-crisis
  distress, but nothing screens for prompt injection or jailbreak attempts on the tutor
  itself. Two open-weight, self-hostable classifier models were identified as the sovereign
  fix (Meta's Prompt Guard 86M for injection/jailbreak specifically; IBM's Granite Guardian,
  Apache-2.0, for broader harm/groundedness plus bulk async reclassification of historical
  traces) — both runnable on Portage's existing sovereign inference tier, no hosted API.
  Queued: `docs/prompts/CC-B1-local-guardrail-model.md`.
- **Adversarial leak-gate regression benchmark.** The leak gate has no automated adversarial
  test suite today — only the fixed corpus/exercise tests in `tests/test_knowledge.py` and
  the draft gate's own tests. ACL 2026's adversarial-student-agent methodology
  (arXiv 2604.18660) is a reusable reference design for one. Queued:
  `docs/prompts/CC-B2-adversarial-leak-benchmark.md`.
- **Citation-grounded retrieval answers — DONE (CC-B3, Slice P).** The tutor's generated
  answer is now checked post-generation against the passages that survived
  `governance.screen_passages`: grounded claims get `Passage.citation` attached inline plus
  a trailing `References:` block, and ungrounded claims are left untouched while the
  additive `groundedness` trace event records the gap (ids + counts only). The check runs
  in addition to the leak gate and opens no new path for a passage to reach a student.
  Source: `backend/app/agent/groundedness.py`, `backend/app/agent/orchestrator.py`,
  `backend/tests/test_groundedness.py`, `VALIDATION.md` Slice P.
  Remaining edge: the claim extractor is a dependency-parsing heuristic, so a response
  written entirely in imperative/prompt form yields no claims to check (recorded rather
  than silently passed as grounded) — a stronger entailment check is a future option.
- **Reasoner-prompt de-quantum.** The live reasoner prompt still carries quantum-shaped
  worked-example guidance (the DSL op list, the `expected_dist` / bitstring schema). The
  DS-shaped prompt is deferred; it is eval-gated because a prompt change alters model
  behavior the stubbed suite cannot prove invariant.
  Source: `backend/app/core/domain/types.py` (the `WorkedExample` deferral comment),
  `backend/app/packs/datascience/pack.py` (`verify_worked_example` docstring),
  `VALIDATION.md` Slice L ("the real DS claim-check is bundled with the eval-gated
  reasoner-prompt de-quantum").
- **DS worked-example claim-check.** The optional prediction claim-check is currently inert
  for DS (the prompt populates `expected_dist`; the verifier reads `expected_stdout`, which
  is never populated, so `claim_ok` stays None). It lands with the prompt de-quantum above.
  A characterization test locks the inert state today and will flip when it is wired.
  Source: `backend/app/packs/datascience/pack.py` (`verify_worked_example`),
  `backend/tests/test_worked_example.py::test_ds_claim_check_is_inert_with_expected_dist`,
  `VALIDATION.md` Slice L.
- **Measure-key cleanup and fallback retirement.** The process-measure output keys
  `tvd_slope` / `avg_tvd_slope` keep quantum-era names, and `measures.py` / `context.py`
  retain a legacy `tvd` / `dist` / `bits` fallback for pre-v6 traces. Renaming the keys and
  retiring the fallback is a coordinated schema change, deferred.
  Source: `backend/app/analysis/measures.py` (the `tvd_slope` keys and the `_metric`
  legacy fallback), `VALIDATION.md` (the bucket-c note that these keep quantum-era names
  because production code emits/parses them).
- **Live strong-judge benchmark.** The deterministic benchmark axes (`no_solution`,
  `never_leak`) are credible today; the separate strong-judge run at higher repeats for the
  qualitative rubrics is pending a local endpoint.
  Source: `README.md` ("the strong-judge run at higher repeats is pending"),
  `VALIDATION.md` ("A SEPARATE strong judge for the qualitative rubrics"; "pending a local
  endpoint").
- **Knowledge corpus: real source ingestion (operator step).** The license-gated,
  pack-scoped ingestion pipeline is in place (`app/knowledge/`), and only a tiny DS+CS seed
  corpus ships in-tree. Ingesting real, openly-licensed sources into a larger corpus is a
  deliberate operator step (point the pipeline at sources; it rejects anything not on the
  license whitelist), not a code change.
  Source: `app/knowledge/ingest.py`, `backend/app/packs/datascience/knowledge/`,
  `VALIDATION.md` Slice O.
- **Per-pack corpora, including a future quantum corpus.** The pipeline is pack-parameterized;
  a future quantum pack builds its own corpus with the same tool and loads it through its own
  `knowledge()`. No shared global blob.
  Source: `app/knowledge/ingest.py` (pack id parameter), `ARCHITECTURE.md` (the pipeline).
- **Local-embedding vector indexing (future option).** Indexing is decoupled from ingestion
  and sits behind the `KnowledgeBase` contract; a local-embedding vector index could replace
  or supplement the lexical BM25 index without re-ingesting or touching the contract.
  Embeddings, if added, run locally, never a hosted API.
  Source: `app/knowledge/index.py`, `app/knowledge/corpus_kb.py`, `ARCHITECTURE.md`.
- **Instructor mode.** There is no authenticated role or privileged mode today: the API has
  no auth, and "instructor" appears only as prompt wording and the `flag_escalate` label
  ("Flagged for instructor"). **Now designed in-tree: `INSTRUCTOR_MODE.md`** (July 2026) —
  pluggable auth dependency (token / OIDC via Waypoint Keycloak), a deterministic,
  content-free instructor surface (escalation queue, aggregates with a suppression floor,
  status), the `events.jsonl` exposure closed, goals/reflections and grades excluded by
  test. Alpha targeted end of August (Line E, E0); build scope in its §5.
  Source: `backend/app/agent/orchestrator.py` (`flag_escalate` label),
  `backend/app/main.py` (no auth dependency on the routes), `PRIVACY.md` (grades firewall,
  goals/reflections never surfaced to an instructor).

## Deferred edges

- **Containerized runner.** The current runner is an out-of-process sandbox; a
  containerized runner (matching Quad's adversarial-containment posture) is the recorded
  roadmap convergence point.
  Source: `VALIDATION.md` ("the containerized runner is the roadmap convergence point";
  the Phase 1b closing roadmap step), `backend/app/packs/datascience/specs/GRADING_SPEC.md`
  ("containerized runner is the roadmap convergence point").
- **Facilitator benchmark family.** The behavioral benchmark registry takes new families
  via `@family(name, category)`; the Phase 5 facilitator family slots in there and is
  documented as pending.
  Source: `VALIDATION.md` (the family registry note; "the Phase 5 facilitator family slots
  in here and is documented as pending").
- **Cohort-level analysis.** The per-attempt measures already carry a `cohort` field;
  condition-level comparison is downstream (the mixed-effects models), not part of this
  layer.
  Source: `process_measures.md` / `backend/app/analysis/process_measures.md` (the
  `measures_by_attempt` row carrying `cohort`; "Condition-level comparison is downstream").
- **More packs.** The pack seam and the `_skeleton` echo pack make additional domains a
  matter of implementing `DomainPack`; the datascience pack is the reference.
  Source: `README.md` ("Adding a domain pack"), `backend/app/packs/_skeleton/`,
  `ARCHITECTURE.md` (Domain seams).
- **Export/import + migration tooling.** Recorded as out of scope for the current store
  posture, on the roadmap.
  Source: `VALIDATION.md` ("Export/import + migration tooling is out of scope (roadmap)").

## Quality ratchet

- **mypy strictness.** The app source is at a strictness notch above the base; the recorded
  next ratchet is `disallow_untyped_defs` (full annotation), app-first.
  Source: `VALIDATION.md` Slice I ("Next ratchet notch (deferred): `disallow_untyped_defs`
  (full annotation), app-first").

## Related records

- As-built privacy and distress safety: `PRIVACY.md`.
- System architecture: `ARCHITECTURE.md`.
- Build-phase narrative and the canonical runbook: `VALIDATION.md`.
