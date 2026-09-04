# SPDX-License-Identifier: AGPL-3.0-only
"""
Groundedness check for retrieval-augmented tutor responses (CC-B3).

After the Reasoner writes its response, this module checks whether the
response's substantive claims are traceable to passages present in
`ctx["knowledge"]` for that turn.

Design principles:
- Deterministic overlap/entailment check (no additional model call)
- Inline claim-level citations attached to grounded claims
- Ungrounded claims flagged in trace counts (not blocked — this is a signal)
- Content-free tracing: records counts and passage IDs, never text
- Leak gate integrity: runs before governance check, ensuring full response
  with references passes through the leak gate
- No-op when `knowledge()` returns None or empty
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


def to_marker(index: int) -> str:
    """Return the inline citation marker, e.g. `[1]`."""
    return f"[{index}]"


class Citation:
    """Represents a citation to a retrieved passage."""

    def __init__(self, passage_id: str, citation_text: str, locator: str | None = None):
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


def check_groundedness(
    response: str,
    passages: list[dict],
) -> tuple[str, dict[str, Any]]:
    """
    Check if the response is grounded in the retrieved passages.

    Returns:
        (updated_response, trace_data)
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

    # Build passage map handling malformed input gracefully
    passage_map: list[tuple[str, Citation]] = []
    for p in passages:
        if not isinstance(p, dict):
            continue
        passage_text = str(p.get("text", "")).lower()
        pid = p.get("id") or "unknown"
        if passage_text:
            passage_map.append(
                (
                    passage_text,
                    Citation(
                        passage_id=pid,
                        citation_text=p.get("citation") or "Reference",
                        locator=p.get("locator"),
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


def _extract_claims(text: str) -> list[str]:
    """Extract substantive claims from the response text."""
    if not text:
        return []

    sentences = re.split(r"[.!?]\s+", text)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 10]

    non_substantive_patterns = [
        r"^(let\'?s|let us|try to|what about|can you|would you|how about|maybe we)",
        r"^(i think|i believe|i feel|in my opinion)",
        r"^(that\'?s a good|great question|good point|excellent)",
        r"^(yes|no|okay|alright|sure|absolutely)",
    ]

    claims: list[str] = []
    for s in sentences:
        s_lower = s.lower()
        if s.endswith("?"):
            continue
        is_substantive = True
        for pattern in non_substantive_patterns:
            if re.match(pattern, s_lower, re.IGNORECASE):
                is_substantive = False
                break
        if is_substantive and len(s.split()) >= 3:
            s = s.rstrip(".!")
            claims.append(s)

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
