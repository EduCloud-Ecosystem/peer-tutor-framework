# SPDX-License-Identifier: AGPL-3.0-only
"""
Groundedness check for retrieval-augmented tutor responses (CC-B3).

After the Reasoner writes its response, this module checks whether the
response's substantive claims are traceable to passages present in
`ctx["knowledge"]` for that turn.

Design principles:
- Deterministic overlap check (no additional model call)
- Inline claim-level citations attached to grounded claims
- Ungrounded claims flagged in trace counts (not blocked — this is a signal)
- Content-free tracing: records counts and passage IDs, never text
- Leak gate integrity: this check only ever sees passages that already survived
  `governance.screen_passages`, and it runs in ADDITION to the leak gate, never
  as a substitute for it
- No-op when `ctx["knowledge"]` is absent/empty (the `_skeleton` None-path)

WHY THIS IS NOT A LEAK CHECK: leak vs. tone vs. distress are three deliberately
separate layers. Groundedness is a fourth, equally separate signal — "is this
claim traceable to something we retrieved?" — and it never drops, blocks, or
rewrites the draft. Conflating it with leak prevention would blur a distinction
the governance gate keeps on purpose (CC-B3 §3).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from functools import lru_cache
from typing import Any

logger = logging.getLogger(__name__)


def to_marker(index: int) -> str:
    """Return the inline citation marker, e.g. `[1]`."""
    return f"[{index}]"


class Citation:
    """Represents a citation to a retrieved passage."""

    def __init__(self, passage_id: Any, citation_text: Any, locator: Any = None):
        self.passage_id = str(passage_id) if passage_id is not None else "unknown"
        self.citation_text = citation_text or "Reference"
        self.locator = locator

    def to_reference(self, index: int) -> str:
        """Return the full reference entry, e.g. `[1] Introduction to Statistics, §3.2`."""
        if self.locator:
            return f"[{index}] {self.citation_text} ({self.locator})"
        return f"[{index}] {self.citation_text}"

    def __repr__(self):
        return f"Citation(passage_id={self.passage_id!r}, citation_text={self.citation_text!r})"


def _field(passage: Any, key: str, default: Any = None) -> Any:
    """Read ``key`` off a passage that may be either a dict or a `Passage` value object.

    `screen_passages` returns `Passage` objects; `context.build_context` projects them
    into dicts for the prompt. Accepting both keeps this check callable at either site
    (and in tests) without a conversion layer, and tolerates malformed entries.
    """
    if isinstance(passage, Mapping):
        return passage.get(key, default)
    return getattr(passage, key, default)


def check_groundedness(
    response: str,
    passages: list[Any],
) -> tuple[str, dict[str, Any]]:
    """Check whether ``response``'s substantive claims are grounded in ``passages``.

    ``passages`` are the SURVIVING (post-`screen_passages`) passages for this turn —
    this function never retrieves and never screens, so it is strictly downstream of
    the leak gate. Dicts (the shape `context.build_context` writes into
    ``ctx["knowledge"]``) and `Passage` objects (what `screen_passages` returns) are
    both accepted.

    Returns ``(updated_response, trace_data)``. ``updated_response`` carries inline
    markers plus a trailing `References:` block for GROUNDED claims. Ungrounded claims
    are left completely untouched — they are not a leak, so they are not dropped or
    rewritten — and are surfaced instead as counts in ``trace_data``. When nothing is
    grounded the response is returned unchanged and no `References:` block is written:
    a reference list nothing points at is noise, not a citation.
    """
    if not passages:
        return response, {
            "passages_available": 0,
            "citations_used": [],
            "citations_count": 0,
            "claim_count": 0,
            "ungrounded_count": 0,
            "all_grounded": True,
            "check_ran": False,
            "reason": "no passages available",
        }

    claims = _extract_claims(response)

    if not claims:
        return response, {
            "passages_available": len(passages),
            "citations_used": [],
            "citations_count": 0,
            "claim_count": 0,
            "ungrounded_count": 0,
            "all_grounded": True,
            "check_ran": True,
            "reason": "no substantive claims found",
        }

    # passage text (lowercased) -> citation; malformed entries are skipped, not raised on.
    passage_map: list[tuple[str, Citation]] = []
    for p in passages:
        passage_text = str(_field(p, "text", "") or "").lower()
        if not passage_text:
            continue
        passage_map.append(
            (
                passage_text,
                Citation(
                    passage_id=_field(p, "id") or "unknown",
                    citation_text=_field(p, "citation"),
                    locator=_field(p, "locator"),
                ),
            )
        )

    claim_citations: list[tuple[str, Citation]] = []
    ungrounded_claims_count = 0

    for claim in claims:
        grounded_citation = None
        claim_lower = claim.lower()
        if len(claim) > 10:
            for passage_text, citation in passage_map:
                if claim_lower in passage_text or _fuzzy_match(claim_lower, passage_text):
                    grounded_citation = citation
                    break

        if grounded_citation:
            claim_citations.append((claim, grounded_citation))
        else:
            ungrounded_claims_count += 1

    updated_response = response
    citations_used_ids: list[str] = []

    # Attach inline citations to grounded claims regardless of ungrounded presence
    if claim_citations:
        updated_response, citations_used = _attach_inline_citations(response, claim_citations)
        citations_used_ids = [c.passage_id for c in citations_used]

    trace_data: dict[str, Any] = {
        "passages_available": len(passages),
        "citations_used": citations_used_ids,
        "citations_count": len(citations_used_ids),
        "claim_count": len(claims),
        "ungrounded_count": ungrounded_claims_count,
        "all_grounded": ungrounded_claims_count == 0,
        "check_ran": True,
    }

    return updated_response, trace_data


@lru_cache(maxsize=1)
def _nlp():
    """The claim-extraction pipeline, loaded once and LAZILY.

    Deliberately not an import-time `spacy.load`: `orchestrator` imports this module
    on every turn path, so a missing/unloadable model wheel must not be able to take
    the whole tutor loop down with it — and a full pipeline load is far too slow to
    pay per import anyway.
    """
    import spacy

    return spacy.load("en_core_web_sm", disable=["ner"])


def _extract_claims(text: str) -> list[str]:
    """Extract substantive knowledge claims using syntactic dependency parsing.

    Filters out conversational fluff, imperative prompts, questions, and 1st/2nd
    person modal statements while keeping declarative factual assertions.

    If the pipeline cannot be loaded, this returns ``[]`` and logs — "no substantive
    claims found", the same shape as a purely conversational reply — rather than
    raising into the turn loop or pretending every sentence is grounded.
    """
    if not text:
        return []

    try:
        nlp = _nlp()
    except Exception:  # noqa: BLE001 — an unloadable model must not break the turn
        logger.warning("groundedness: spaCy pipeline unavailable; claim extraction skipped")
        return []

    doc = nlp(text)
    claims = []

    for sent in doc.sents:
        sent_str = sent.text.strip()

        # 1. Skip short fragments, questions, and exclamations
        if len(sent) < 4 or sent_str.endswith("?") or sent_str.endswith("!"):
            continue

        # 2. Filter out imperative or transitional prompts (e.g., "Think about...", "Let's explore...")
        first_token = sent[0]
        if first_token.pos_ == "VERB" and not any(
            tok.dep_ == "nsubj" for tok in first_token.children
        ):
            continue

        # 3. Exclude 1st and 2nd person conversational framing (e.g., "I think", "We can see", "You should")
        nsubj = next(
            (tok for tok in sent if tok.dep_ in ("nsubj", "nsubjpass")),
            None,
        )
        if nsubj and nsubj.lower_ in {"i", "we", "you", "me", "us"}:
            continue

        # 4. Require a valid declarative structure (Subject + Finite Verb / Auxiliary)
        root = sent.root
        if root.pos_ in ("VERB", "AUX") and nsubj is not None:
            claims.append(sent_str.rstrip(".!"))

    return claims


def _fuzzy_match(claim: str, passage: str) -> bool:
    """Fuzzy match: check if significant portion of claim appears in passage."""
    if not claim or not passage:
        return False

    claim_words = set(claim.split())
    if len(claim_words) < 3:
        return False

    passage_words = set(passage.split())

    stopwords = {
        "the",
        "a",
        "an",
        "of",
        "to",
        "for",
        "with",
        "on",
        "at",
        "from",
        "by",
        "in",
        "as",
        "is",
        "was",
        "were",
        "are",
        "am",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "must",
    }

    claim_words = claim_words - stopwords
    passage_words = passage_words - stopwords

    if not claim_words:
        return False

    overlap_words = claim_words & passage_words
    return (len(overlap_words) / len(claim_words)) >= 0.5


def _attach_inline_citations(
    response: str,
    claim_citations: list[tuple[str, Citation]],
) -> tuple[str, list[Citation]]:
    """
    Attach inline markers [1], [2] next to grounded claims in the body,
    and append a References section at the bottom.
    """
    if not claim_citations:
        return response, []

    citations_used: list[Citation] = []
    citation_to_idx: dict[str, int] = {}

    for _, citation in claim_citations:
        if citation.passage_id not in citation_to_idx:
            citations_used.append(citation)
            citation_to_idx[citation.passage_id] = len(citations_used)

    updated_response = response

    for claim, citation in claim_citations:
        idx = citation_to_idx[citation.passage_id]
        marker = f" [{idx}]"

        if claim in updated_response and f"[{idx}]" not in updated_response:
            pattern = re.escape(claim) + r"([.!?]?)"

            def _add_marker(match):
                punct = match.group(1)
                return (
                    match.group(0)[: -len(punct)] + marker + punct  # noqa: B023
                    if punct
                    else match.group(0) + marker  # noqa: B023
                )

            updated_response = re.sub(pattern, _add_marker, updated_response, count=1)

    references = [c.to_reference(idx) for idx, c in enumerate(citations_used, 1)]

    if "\n\nReferences:" not in updated_response and "\nReferences:" not in updated_response:
        ref_section = "\n\nReferences:\n" + "\n".join(references)
        updated_response += ref_section

    return updated_response, citations_used


def get_groundedness_trace(
    passage_count: int,
    citations_used: list[str],
    ungrounded_count: int,
    claim_count: int,
    all_grounded: bool,
) -> dict:
    """
    Build the additive trace event payload for groundedness.
    Content-free: records citation IDs and counts, never passage or claim text.
    """
    return {
        "passages_available": passage_count,
        "citations_used": citations_used,
        "citations_count": len(citations_used),
        "claim_count": claim_count,
        "ungrounded_count": ungrounded_count,
        "all_grounded": all_grounded,
    }
