# Privacy and Safety

This document describes the as-built privacy and safety posture of the framework: what it
stores, what it never stores, and how the distress-routing layer behaves. It is the
IRB-facing record. Every section cites where the behavior is implemented so a reviewer can
read the code against the claim.

This describes behavior as built. Where a decision is still open, it is marked as a
standing IRB item.

Platform-level policy — which infrastructure may hold which class of data, and who
authorizes an exception — lives in one place:
[`educloud/docs/policy/data-destinations.md`](../educloud/docs/policy/data-destinations.md).
This document is that policy's Belay-side pointer; the module-level posture below is what
implements it here.

## Privacy by architecture

Privacy is a structural property, not a policy bolted on afterward.

- **Pseudonymous identifiers only.** Identity is a pseudonymous host id (for example
  `gh:12345`), namespaced by provider; that id is the participant anon-code. There is no
  name, email, SIS id, or other personal identifier in the identity model.
  Source: `README.md` (Privacy by architecture), `CONTRIBUTING.md` ("Privacy is enforced
  in code, not by policy"), `backend/app/store/models.py` (participants table: "anonymized
  identity + consent flag. No PII by design"), `backend/app/integrations/quad/router.py`.
- **PII is rejected at the boundary.** The Quad sidecar refuses any payload carrying PII
  (name, SIS/student id, email, ssn, phone field keys anywhere; plaintext email patterns;
  non-pseudonymous ids) with a 422 before it reaches the tutor.
  Source: `backend/app/integrations/quad/pii.py` (`pii_reason`, `assert_no_pii`,
  `PIIRejected`), `VALIDATION.md` Quad sidecar section ("PII is rejected at the boundary").
- **Raw learner text reaches the sovereign classifier only, and only when screening is
  on.** With the CC-B1 injection/jailbreak layer enabled (`INJECTION_GUARD_ENABLED`), the
  learner's message is sent to one destination and one only: the operator-configured
  **sovereign classifier endpoint** (Portage's `classifier` alias on the institutional
  tier). The guard builds its own client and never touches the tutoring provider factory,
  so a deployment configured for a hosted provider cannot receive learner text through
  this path; with the layer off — the shipped default — no learner text leaves the process
  at all. What is recorded is content-free: a bounded status, score and model, never the
  message and never an exception string.
  Source: `backend/app/agent/injection_guard.py`, `backend/app/agent/orchestrator.py`
  (`_injection_turn`), `ARCHITECTURE.md` ("Injection and jailbreak screening"),
  `VALIDATION.md` Slice Q.
- **Content-free trace.** The trace records pseudonymous ids and structured, content-free
  events. Verbatim learner text is not written to the trace, the logs, or any export.
  Source: `CONTRIBUTING.md`, `backend/app/store/models.py` (events table: "append-only
  trace ... never updated, only inserted").
- **Fixed trace row.** Every event is the same fixed eight-field row (`participant_id`,
  `ts`, `exercise_id`, `mode`, `event_type`, `stance`, `payload`, `note`). `event_type`
  values are additive; adding one does not change the row shape or the `events.jsonl`
  export contract.
  Source: `backend/app/store/repository.py` (`make_event`), `backend/app/store/models.py`
  (Event model).
- **Grades firewall.** Grading-spec results are read-only turn context; there is no
  grade-write route and no write path, and the tutor never writes grades. Goals and
  reflections are pseudonymous and never surfaced to an instructor.
  Source: `VALIDATION.md` Quad sidecar section ("Grades firewall"), `README.md`,
  `backend/app/agent/goals.py`.

## Distress-response posture (the wellbeing floor, third layer)

The distress layer reads the learner's message for an explicit distress signal and, when
routing is enabled, routes outward to a human instead of tutoring. It is the third layer
of the wellbeing floor and is different in kind from the other two: the intake harm
detector guards against the learner asking the tutor to be unkind, and the tone softener
guards the tutor's own tone; this layer routes the learner to human support.
Source: `backend/app/agent/distress.py` (module docstring), `VALIDATION.md` Slice G
("distress-routing layer of the wellbeing floor").

As-built properties:

- **Opt-in, off by default.** `DISTRESS_ROUTING_ENABLED` defaults to `false`. When off, no
  detection runs, there is no short-circuit, and behavior is byte-identical to a build
  without the layer.
  Source: `backend/app/config.py` (`distress_routing_enabled` default `False`),
  `backend/app/agent/orchestrator.py` (the `if settings.distress_routing_enabled:` gate
  before `_distress_turn`), `backend/app/agent/distress.py` (docstring: "Off by default").
- **Conservative, explicit-crisis-only detection.** Detection is a narrow, high-precision
  lexical trigger for explicit self-harm / suicidal-ideation phrasing only. It is a
  routing trigger, not a mental-health judgment: it scores no severity, diagnoses nothing,
  and names no methods. It must not fire on academic despair or frustration, which is
  normal struggle (routing that to crisis support would itself be a harm); that is a tested
  negative control. False positives route to support, the low-harm direction.
  Source: `backend/app/agent/distress.py` (`_DEFAULT_TERMS`, `has_distress_signal`, and the
  detection-boundary comment).
- **IRB-tunable vocabulary.** Institutions extend detection with whole-text terms via
  `DISTRESS_SIGNAL_TERMS` (comma-separated), matched case-insensitively as literals; this
  is IRB-owned.
  Source: `backend/app/agent/distress.py` (`extra_terms`), `backend/app/config.py`
  (`distress_signal_terms`).
- **Surfaces institution-configured support; routes to a human.** On a triggered turn the
  tutor pauses, states it is a study tool and not a counselor, surfaces the
  institution-configured support resources, and names the human escalation route. The
  framework ships only neutral scaffolding; it invents no hotline, number, or service.
  Source: `backend/app/agent/distress.py` (`_FRAME_HEAD`, `distress_frame`,
  `frame_from_settings`), `backend/app/agent/orchestrator.py` (`_distress_turn`:
  `intervention="escalate"`, `governance="flag_escalate"`, tutoring suppressed, no
  planner/reasoner/LLM call).
- **The FILL-IN placeholder is never rendered to a learner.** Displayed content is gated on
  `distress_configured` (true only once both the support message and the escalation target
  are replaced). Enabled but unconfigured renders a safe generic frame that points the
  learner outward ("reach out to someone you trust, or your institution's support
  channels") with no placeholder text.
  Source: `backend/app/agent/distress.py` (`distress_frame` configured branch vs generic
  branch), `backend/app/config.py` (`distress_configured` property: `"[FILL-IN"` not in
  either message; the `[FILL-IN: ...]` defaults).
- **Content-free, non-PII trace, IRB-disableable.** The only trace from a distress turn is
  an additive `distress` event whose payload is exactly `{triggered, configured, routed}`:
  no text, no category, no severity, no PII. At goal/reflection intake a distress signal is
  recorded `honored:false`, `floor:"distress"`, `text:None`, so no verbatim distressing
  content is stored. The whole event is gated by `DISTRESS_TRACE_ENABLED` (default `true`),
  which the IRB may set to `false` to suppress even the content-free event.
  Source: `backend/app/agent/orchestrator.py` (`_distress_turn` components.distress
  payload), `backend/app/config.py` (`distress_trace_enabled`), `VALIDATION.md` Slice G
  (trace description), `CONTRIBUTING.md` ("The distress path stores no verbatim distressing
  content and no category").
- **Startup misconfiguration warning.** If routing is enabled but unconfigured, a prominent
  operator warning (no PII, no learner text) is emitted at startup/config-load so a
  half-armed opt-in is caught in deployment testing rather than at the first triggered
  turn. This is visibility only and changes no runtime distress behavior.
  Source: `backend/app/agent/distress.py` (`warn_if_misconfigured`), `VALIDATION.md`
  Slice G follow-up.

### Configuration (owners)

| Env var | Default | Owner | Purpose |
|---|---|---|---|
| `DISTRESS_ROUTING_ENABLED` | `false` | institution | master switch; off means byte-identical behavior |
| `DISTRESS_SUPPORT_MESSAGE` | `[FILL-IN: institution support resources]` | institution + IRB | resources surfaced to the learner |
| `DISTRESS_ESCALATION_TARGET` | `[FILL-IN: institution escalation contact]` | institution + IRB | human escalation route |
| `DISTRESS_TRACE_ENABLED` | `true` | IRB | set false to suppress the content-free distress event |
| `DISTRESS_SIGNAL_TERMS` | `` (empty) | institution + IRB | extra detection terms (comma-separated) |

Source: `backend/app/config.py` (the `distress_*` fields), `VALIDATION.md` Slice G env table.

## Standing IRB items

Two decisions are deliberately left to the IRB and are marked open in the code:

1. **The detection vocabulary and its boundary.** Where the line falls between academic
   despair (normal struggle) and crisis, and how the trigger vocabulary is tuned. The
   shipped `_DEFAULT_TERMS` list is a minimal, high-precision starter, explicitly marked
   IRB-review-required.
   Source: `backend/app/agent/distress.py` (the `_DEFAULT_TERMS` comment), `VALIDATION.md`
   Slice G ("Two standing IRB items").
2. **Whether the content-free distress event may live in the research trace at all.**
   Controlled by `DISTRESS_TRACE_ENABLED`. Even though the event carries no text, no
   category, no severity, and no PII, recording that a distress routing occurred is an IRB
   decision.
   Source: `backend/app/config.py` (`distress_trace_enabled`), `VALIDATION.md` Slice G.

## Related records

- The full distress build narrative and test list: `VALIDATION.md` Slice G.
- System architecture, including the governance gate and the trace envelope:
  `ARCHITECTURE.md`.
- The licensing split: `LICENSING.md`.
