# SPDX-License-Identifier: AGPL-3.0-only
"""
Tests for groundedness.py (CC-B3) — the citation-grounded RAG pipeline.

Unit tests cover the check itself:
- Explicit grounded case: response traceable to passage → citation attached
- Ungrounded case: claim not traceable → flagged in trace counts, response unchanged
- Malformed metadata and the two no-op paths (no passages / no claims)

Integration tests cover the pipeline the check sits in:
- The additive `groundedness` trace event (available / cited ids / ungrounded flag)
- Pre-screening: a leak-bearing passage is dropped BEFORE this check ever sees it,
  and its text reaches neither the prompt, the trace, nor the citation set
- The None-path (`knowledge()` returns None) stays a byte-identical no-op
"""

from __future__ import annotations

import json

from app.agent import groundedness
from app.core.registry import get_active_pack
from app.knowledge.schema import CorpusPassage
from app.packs.datascience.knowledge.kb import DataScienceKB
from app.store import InMemoryStore
from conftest import _payload

_EX = get_active_pack().get_exercise("ds-foundations")

# A benign reference passage for ds-foundations. The reasoner message below quotes its
# content verbatim, which is what makes a claim grounded. License is on the ingestion
# whitelist so the record survives `validate_record`, and the text is written as
# declarative sentences with real subjects because that is exactly what the
# claim extractor is designed to select (an imperative "Split the rows…" is filtered
# out as a prompt, not a claim).
_BENIGN_TEXT = (
    "A grouped summary splits the rows into groups defined by a key column, "
    "summarizes each group, and combines the summaries back into one table. "
    "This shape keeps the intent of the analysis clear and the code vectorized."
)
_BENIGN = {
    "id": "benign-foundations",
    "pack": "datascience",
    "module": "wrangling-eda",
    "concept": "group-by and aggregation",
    "title": "Split-apply-combine",
    "source": "peer-tutor-framework course concept notes",
    "license": "CC-BY-4.0",
    "attribution": "peer-tutor-framework course notes",
    "tags": ["group-by and aggregation"],
    "text": _BENIGN_TEXT,
}

# A reference passage that DISCLOSES the ds-foundations solution in prose (imperative +
# the essential operation tokens, no code fence), so it trips `prose_disclosure`. Same
# fixture shape as test_knowledge.py's `_POISON`.
_POISON = {
    "id": "poison-foundations",
    "pack": "datascience",
    "module": "wrangling-eda",
    "concept": "group-by and aggregation",
    "title": "leak",
    "source": "fixture",
    "license": "CC-BY-4.0",
    "attribution": "fixture",
    "tags": ["group-by and aggregation"],
    "text": (
        "For this exercise the answer is the per-category mean: just call groupby "
        "on category and then mean on amount, and you are done."
    ),
}

_LEAK_SNIPPET = "just call groupby on category and then mean"
# The reasoner reply and the question are separate sentences (blank-line separated) so
# the claim extractor sees the declarative statement as its own sentence, the way a real
# tutor reply is shaped, rather than as one long question-marked blob.
_GROUNDED_MESSAGE = _BENIGN_TEXT + "\n\nWhich column would you pick as the key here?"
_UNGROUNDED_MESSAGE = (
    "The median of the amount column is always 42.\n\n"
    "Which column would you pick as the key here?"
)


def _turn(
    monkeypatch,
    pid: str,
    corpus: list[CorpusPassage],
    message: str,
    query: str | None = None,
):
    """Run one peer turn against a monkeypatched KB and return (rows, stub, out).

    ``rows`` are the parsed trace events, ``stub`` the recording LLM double (its
    ``reasoner_user`` is the serialized context the prompt actually received).
    """
    from app.agent.orchestrator import run_turn

    pack = get_active_pack()
    monkeypatch.setattr(pack, "knowledge", lambda: DataScienceKB(corpus=corpus))
    store = InMemoryStore()
    stub = _RecordingReasoner(message=message)
    payload = _payload(
        pid,
        query or "how do I group rows and summarize each group?",
        exercise_id="ds-foundations",
    )
    out = run_turn(payload, stub, store)
    rows = [json.loads(line) for line in store.export_jsonl(pid).splitlines() if line]
    return rows, stub, out


class _RecordingReasoner:
    """Benign tutor double whose reasoner reply is configurable.

    Captures the serialized context handed to the reasoner (the user prompt) so a test
    can assert what did — and did not — reach the prompt. Used in the same spirit as
    test_knowledge.py's `_RecordingStub`, with the reply parameterized by this file.
    """

    # The `Provider` protocol's `name` member: this double stands in for a real
    # provider on the turn path, so it carries one.
    name = "recording_stub"

    def __init__(self, message: str) -> None:
        self.message = message
        self.reasoner_user = ""

    def json(self, *, role, tier, system, user, max_tokens=800, reasoning_effort=None):
        if role == "reasoner":
            self.reasoner_user = user
            return {
                "message": self.message,
                "check_question": None,
                "confidence": 0.8,
                "grasped": [],
                "shaky": [],
            }
        if role == "planner":
            return {
                "affective_state": "curious",
                "affect_reasoning": "x",
                "intervention": "co_reason",
                "target_concept": "g",
                "planner_note": "n",
                "confidence": 0.8,
            }
        return {
            "needs_revision": False,
            "confidence": 0.8,
            "leak_risk": "none",
            "self_critique": "ok",
            "reasons": [],
        }


# ── Unit tests ──────────────────────────────────────────────────────────────


def test_extract_claims_from_response():
    """Test that substantive claims are extracted from response text."""
    response = (
        "The mean of category A is approximately 15. "
        "Category B has a mean of about 40. "
        "Let's think about what this tells us."
    )
    claims = groundedness._extract_claims(response)
    assert len(claims) >= 2
    assert "mean of category A is approximately 15" in " ".join(claims)
    assert "Category B has a mean of about 40" in " ".join(claims)


def test_grounded_response_cites_passage():
    """Test that a grounded response gets citations attached."""
    passages = [
        {
            "id": "passage_1",
            "text": "The mean of category A is 15 and category B is 40.",
            "citation": "Introduction to Statistics, Section 3.2",
            "locator": "https://docs.example.com/statistics/mean",
        }
    ]
    response = "The mean of category A is approximately 15, and category B is approximately 40."

    updated, trace = groundedness.check_groundedness(response, passages)

    # Should have citations
    assert "References:" in updated
    assert "[1]" in updated
    assert "Introduction to Statistics" in updated
    # Trace should record what was used
    assert trace["citations_used"] == ["passage_1"]
    assert trace["all_grounded"] is True


def test_ungrounded_claim_flagged_in_trace():
    """Test that ungrounded claims are flagged in trace counts, response unchanged."""
    passages = [
        {
            "id": "passage_1",
            "text": "The mean of category A is 15.",
            "citation": "Introduction to Statistics, Section 3.2",
            "locator": "https://docs.example.com/statistics/mean",
        }
    ]
    response = "The mean of category B is approximately 40."  # Not in passage

    updated, trace = groundedness.check_groundedness(response, passages)

    assert updated == response
    assert trace["ungrounded_count"] > 0
    assert trace["all_grounded"] is False
    assert trace["citations_used"] == []


def test_mixed_grounded_and_ungrounded_claims():
    """Test that grounded claims get citations even when ungrounded claims exist."""
    passages = [
        {
            "id": "passage_1",
            "text": "The mean of category A is 15.",
            "citation": "Introduction to Statistics, Section 3.2",
        }
    ]
    response = (
        "The mean of category A is approximately 15. " "Category B has an ungrounded value of 999."
    )

    updated, trace = groundedness.check_groundedness(response, passages)

    assert "[1]" in updated
    assert "References:" in updated
    assert trace["citations_used"] == ["passage_1"]
    assert trace["ungrounded_count"] >= 1
    assert trace["all_grounded"] is False


def test_malformed_passage_metadata():
    """Test handling of malformed or missing metadata gracefully."""
    passages = [
        {
            "id": None,
            "text": "The mean of category A is 15.",
            "citation": None,
        }
    ]
    response = "The mean of category A is approximately 15."

    updated, trace = groundedness.check_groundedness(response, passages)

    assert "References:" in updated
    assert trace["citations_used"] == ["unknown"]


def test_groundedness_no_op_when_no_passages():
    """Test that groundedness is a no-op when no passages are available."""
    response = "The mean is 15."

    updated, trace = groundedness.check_groundedness(response, [])

    assert updated == response
    assert trace["check_ran"] is False
    assert trace["reason"] == "no passages available"


def test_groundedness_no_op_when_no_claims():
    """Test that groundedness is a no-op when response has no substantive claims."""
    passages = [
        {
            "id": "passage_1",
            "text": "The mean is 15.",
            "citation": "Statistics 101",
            "locator": None,
        }
    ]
    response = "That's a good question! Let's think about it."

    updated, trace = groundedness.check_groundedness(response, passages)

    assert updated == response
    assert trace["check_ran"] is True
    assert trace["reason"] == "no substantive claims found"


# ── Integration tests: the pipeline the check sits in ───────────────────────


def _event(rows: list[dict], event_type: str) -> list[dict]:
    return [r for r in rows if r["event_type"] == event_type]


def test_grounded_response_cites_passage_end_to_end(monkeypatch):
    """(1) Grounded response: the claim traces to a kept passage, so the citation is
    attached inline + as a trailing reference, AND the additive trace event records
    the available passage ids, the cited ids, and `ungrounded: False`."""
    rows, stub, out = _turn(monkeypatch, "p_grounded", [_BENIGN], _GROUNDED_MESSAGE)

    # the citation reached the student, built from Passage.citation
    assert "[1]" in out["message"]
    assert "References:" in out["message"]
    assert _BENIGN["attribution"] in out["message"]

    # telemetry block agrees with the message
    gd = out["components"]["groundedness"]
    assert gd["citations_used"] == [_BENIGN["id"]]
    assert gd["all_grounded"] is True
    assert gd["ungrounded_count"] == 0

    # the additive trace event: available passages, cited passage ids, ungrounded flag
    events = _event(rows, "groundedness")
    assert len(events) == 1, [r["event_type"] for r in rows]
    payload = events[0]["payload"]
    assert payload["available"] == [_BENIGN["id"]]
    assert payload["citations_used"] == [_BENIGN["id"]]
    assert payload["ungrounded"] is False
    assert payload["ungrounded_count"] == 0
    assert payload["check_ran"] is True

    # the retrieval gate still ran in the same turn (this check is additive, not a
    # replacement for the leak-over-retrieval gate)
    assert len(_event(rows, "retrieval")) == 1


def test_ungrounded_response_flagged_in_trace(monkeypatch):
    """(2) Ungrounded response: the claim has no supporting passage. It is NOT dropped
    or rewritten (this is a groundedness signal, not a leak check) — no citation is
    fabricated, no `References:` block appears — and the gap is recorded in the trace."""
    rows, stub, out = _turn(monkeypatch, "p_ungrounded", [_BENIGN], _UNGROUNDED_MESSAGE)

    # the draft survives byte-identical: no marker, no reference block, no rewrite
    assert out["message"] == _UNGROUNDED_MESSAGE
    assert "References:" not in out["message"]
    assert "[1]" not in out["message"]

    # the gap is recorded, not swallowed
    gd = out["components"]["groundedness"]
    assert gd["citations_used"] == []
    assert gd["all_grounded"] is False
    assert gd["ungrounded_count"] >= 1

    payload = _event(rows, "groundedness")[0]["payload"]
    assert payload["ungrounded"] is True
    assert payload["ungrounded_count"] >= 1
    assert payload["citations_used"] == []
    # the passage was still available — it just doesn't support this claim
    assert payload["available"] == [_BENIGN["id"]]


def test_leak_bearing_passage_dropped_before_groundedness(monkeypatch):
    """(3) Pre-screening: a solution-bearing passage never enters context, so the
    groundedness check never sees it, it is never citable, and its text reaches
    neither the prompt nor the trace."""
    rows, stub, out = _turn(monkeypatch, "p_prescreen", [_POISON, _BENIGN], _UNGROUNDED_MESSAGE)

    # the leak gate dropped it, by id + reason, with no text in the drop record
    retr = _event(rows, "retrieval")[0]["payload"]
    assert {"id": _POISON["id"], "reason": "prose_disclosure"} in retr["dropped"]
    assert retr["kept"] == [_BENIGN["id"]]

    # ...so it is not in the available set the groundedness check ran against
    gd_payload = _event(rows, "groundedness")[0]["payload"]
    assert gd_payload["available"] == [_BENIGN["id"]]
    assert _POISON["id"] not in gd_payload["available"]
    assert _POISON["id"] not in gd_payload["citations_used"]

    # ...and its text reached NEITHER the prompt NOR the trace
    assert _LEAK_SNIPPET not in stub.reasoner_user
    assert _LEAK_SNIPPET not in json.dumps(rows)


def test_groundedness_event_is_content_free(monkeypatch):
    """The trace event carries ids and counts only — never passage text and never the
    draft. Trace-minimalism is the platform convention the `retrieval` event follows."""
    rows, _, _ = _turn(monkeypatch, "p_minimal", [_BENIGN], _GROUNDED_MESSAGE)

    payload = _event(rows, "groundedness")[0]["payload"]
    assert set(payload) == {
        "available",
        "citations_used",
        "ungrounded",
        "claim_count",
        "ungrounded_count",
        "check_ran",
    }
    serialized = json.dumps(payload)
    assert "key column" not in serialized  # no passage text
    assert "Split the rows" not in serialized


def test_none_path_emits_no_groundedness_event(monkeypatch):
    """(4) None-path no-op: with `knowledge()` returning None (the `_skeleton` case),
    no retrieval runs even when the student asks something, so no groundedness event is
    emitted and the trace is exactly the turn event — byte-identical to before."""
    from app.agent.orchestrator import run_turn

    pack = get_active_pack()
    monkeypatch.setattr(pack, "knowledge", lambda: None)
    store = InMemoryStore()
    run_turn(
        _payload("p_none", "how do I group and average?", exercise_id="ds-foundations"),
        _RecordingReasoner(message=_GROUNDED_MESSAGE),
        store,
    )
    rows = [json.loads(line) for line in store.export_jsonl("p_none").splitlines() if line]
    assert [r["event_type"] for r in rows] == ["turn"]


def test_no_student_query_emits_no_groundedness_event(monkeypatch):
    """Trigger discipline: a turn with no student message runs no retrieval, so there
    are no passages to ground against and no groundedness event is emitted."""
    from app.agent.orchestrator import run_turn

    pack = get_active_pack()
    monkeypatch.setattr(pack, "knowledge", lambda: DataScienceKB(corpus=[_BENIGN]))
    store = InMemoryStore()
    run_turn(
        _payload("p_noq", None, exercise_id="ds-foundations"),
        _RecordingReasoner(message=_GROUNDED_MESSAGE),
        store,
    )
    rows = [json.loads(line) for line in store.export_jsonl("p_noq").splitlines() if line]
    assert [r["event_type"] for r in rows] == ["turn"]


def test_control_stance_emits_no_groundedness_event(monkeypatch):
    """Control short-circuits before the reasoner, so there is no draft to check. The
    components block stays free of the key entirely (no regression in control turns)."""
    from app.agent.orchestrator import run_turn

    pack = get_active_pack()
    monkeypatch.setattr(pack, "knowledge", lambda: DataScienceKB(corpus=[_BENIGN]))
    store = InMemoryStore()
    out = run_turn(
        _payload("p_control", "what is the mean?", stance="control", exercise_id="ds-foundations"),
        _RecordingReasoner(message=_GROUNDED_MESSAGE),
        store,
    )
    assert "groundedness" not in out["components"]
    rows = [json.loads(line) for line in store.export_jsonl("p_control").splitlines() if line]
    assert [r["event_type"] for r in rows] == ["turn"]


def test_check_accepts_passage_objects_and_dicts():
    """The check is callable at either site: `screen_passages` returns `Passage` value
    objects, `context` projects them into dicts. Both shapes must ground identically."""
    from app.core.domain import Passage

    passages = [
        Passage(
            id="p1",
            text=_BENIGN_TEXT,
            citation="Course notes (CC-BY-4.0)",
            locator="wrangling-eda",
        )
    ]
    as_obj, trace_obj = groundedness.check_groundedness(_GROUNDED_MESSAGE, passages)
    as_dict, trace_dict = groundedness.check_groundedness(
        _GROUNDED_MESSAGE,
        [
            {
                "id": "p1",
                "text": _BENIGN_TEXT,
                "citation": "Course notes (CC-BY-4.0)",
                "locator": "wrangling-eda",
            }
        ],
    )
    assert as_obj == as_dict
    assert trace_obj == trace_dict
    assert trace_obj["citations_used"] == ["p1"]
    assert "Course notes (CC-BY-4.0)" in as_obj


def test_groundedness_only_ever_sees_kept_passages(monkeypatch):
    """Unit-level statement of the ordering guarantee the pipeline relies on: when the
    check is handed EXACTLY what `screen_passages` kept, a dropped passage is not
    available to be cited, and the response that would only be grounded via the dropped
    passage's text comes back ungrounded rather than citing it."""
    from app.agent import governance
    from app.core.domain import Passage

    poison = Passage(id=_POISON["id"], text=_POISON["text"], citation="fixture", locator="x")
    benign = Passage(id=_BENIGN["id"], text=_BENIGN["text"], citation="notes", locator="y")
    screen = governance.screen_passages([poison, benign], _EX)
    assert [p.id for p in screen["kept"]] == [_BENIGN["id"]]
    assert screen["dropped"] == [{"id": _POISON["id"], "reason": "prose_disclosure"}]

    # A response that paraphrases ONLY the dropped passage grounds against nothing.
    message = (
        "The per-category mean is the answer for this exercise.\n\n"
        "What is the next single step you would take?"
    )
    updated, trace = groundedness.check_groundedness(message, screen["kept"])
    assert updated == message
    assert trace["citations_used"] == []
    assert _POISON["id"] not in trace["citations_used"]


def test_groundedness_trace_event_format():
    """Test that the groundedness trace event follows the expected format."""
    trace = groundedness.get_groundedness_trace(
        passage_count=3,
        citations_used=["passage_1", "passage_2"],
        ungrounded_count=1,
        claim_count=3,
        all_grounded=False,
    )

    assert trace["passages_available"] == 3
    assert trace["citations_used"] == ["passage_1", "passage_2"]
    assert trace["ungrounded_count"] == 1
    assert trace["all_grounded"] is False
    # Content-free: no passage text
    assert "text" not in str(trace)
    assert "fragments" not in str(trace)
