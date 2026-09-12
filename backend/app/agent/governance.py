# SPDX-License-Identifier: AGPL-3.0-only
"""
Governance component — the last gate before a reply reaches the student.

Mostly deterministic on purpose: safety shouldn't depend on the model behaving.
Its sharpest tool reuses the domain's own grader — any code the tutor proposes is
run through the active pack's executable oracle against the current exercise's
goal, and if it would actually solve the exercise, that's a full-solution leak.
The offending snippet is stripped and the turn is flagged. This makes the HARD
RULE ("never hand over the solution") enforceable rather than merely requested.

Pack-agnostic (Phase 1a): the block/rewrite/abstain decision lives HERE and is
deterministic. It consumes `LeakEvidence` supplied by the active `DomainPack`
(`pack.leak_evidence`): the executable grader is one evidence source; the
decision is core's. The pack owns the domain-specific redaction (which surface to
strip); core owns the peer-voiced redirect that replaces it.

Note on leak heuristics: the datascience pack's leak evidence is executable-
comparison-only (it runs candidate snippets through the grader). There are no
domain-agnostic *prose*-leak heuristics to host in core today (no "the answer is
X" detector) — only the answer-seeking detector below, which reads the STUDENT's
message, not the draft. A retrieval-backed or prose-heavy pack (e.g. the 1b
data-science pack) will need prose-leak heuristics; see EXTRACTION_PLAN §(f).

Governance flags map onto the values the front-end glass box already renders:
  none | withholding_solution | redirect_answer_seeking | encourage_tone | flag_escalate
"""

from __future__ import annotations

import re

from ..core.registry import get_active_pack
from . import leak_profile

# Belay's human-facing wording for each contract leak signal. The contract's
# vocabulary is the registry-governed `reason_code`; these strings are Belay's and
# stay on Belay's side of the call (SPEC.md §10 — "could a different caller, in a
# different language, with a different product, want it?"). Keyed by signal name
# so the reconstruction survives the reason_code being singular: when BOTH signals
# fire, the verdict names `leak.is_solution` as the primary reason and carries both
# in `evidence`, and `check()` rebuilds both strings in the original order.
_LEAK_REASON_TEXT = {
    "is_solution": "draft contained code that solves the exercise",
    "prose_disclosure": "draft prose disclosed the solution",
}


def _reasons_from_verdict(verdict: dict) -> list[str]:
    """Belay's `reasons` list, rebuilt from a contract verdict's evidence."""
    return [
        _LEAK_REASON_TEXT[e["signal"]]
        for e in verdict["evidence"]
        if e.get("fired") and e.get("signal") in _LEAK_REASON_TEXT
    ]


_ANSWER_SEEKING = re.compile(
    r"\b(just tell me|what'?s the answer|give me the (answer|code|solution)|"
    r"show me the (code|solution)|solve it for me)\b",
    re.IGNORECASE,
)

# Wellbeing defense-in-depth (NOT a deterministic gate). Detects an obviously
# berating/contemptuous draft or one that reinforces/agrees with the student's
# negative self-talk. UNLIKE the leak gate, tone has NO ground-truth oracle, so this
# is a cautious heuristic with FALSE NEGATIVES by nature — it is the OBSERVABLE
# BACKSTOP layer of the wellbeing floor (whose primary protection is the persona
# stance; see also intake `goals.is_harmful` + never-honor-framing), never an
# equivalent of the post-hoc leak gate.
#
# It keys on CONTEMPT / BERATING / negative-self-talk reinforcement, NOT on bluntness
# or the delivery of a correction. Firm, direct, warm correction ("that is wrong
# because…", "you inverted the condition", "this is O(n^2)") must pass through
# UNCHANGED — honest feedback is the tutor's job, and goals like "be honest about my
# mistakes" are explicitly honored, so softening directness would be self-
# contradictory. Tuned for PRECISION on contempt; the asymmetry vs. the leak gate is
# documented in EXTRACTION_PLAN.md §(g).
_BERATING = re.compile(
    r"\byou'?(re| are)\s+(such\s+)?(an?\s+)?(idiot|stupid|dumb|moron|loser|fool|"
    r"pathetic|worthless|useless|hopeless|a\s+failure|a\s+disgrace|an?\s+embarrassment|"
    r"too\s+(dumb|stupid|slow|lazy))\b"
    r"|\byou'?(ll| will)\s+never\s+(be\s+(any\s+)?good|get|understand|learn|amount|make it|succeed)\b"
    r"|\byou\s+should\s+(feel\s+)?(ashamed|embarrassed|be\s+ashamed)\b"
    r"|\byou\s+should'?(ve| have)\s+known\s+better\b"  # contempt, not correction
    r"|\byou\s+don'?t\s+deserve\b"
    r"|\bwhat'?s\s+wrong\s+with\s+you\b"
    # put-down comparison ("everyone finds this easy/obvious"); NOTE this is the
    # OPPOSITE of the kind, normalizing "everyone finds this hard/confusing", which
    # must NOT match — so it keys only on easy/simple/obvious/trivial / "already knows".
    r"|\beveryone\s+(else\s+)?(finds|thinks|knows|gets)\s+(this|it|that)\s+(is\s+)?"
    r"(easy|simple|obvious|trivial)\b"
    r"|\beveryone\s+else\s+(gets|understands|knows|already\s+knows)\b"
    r"|\byou'?(re| are)\s+right,?\s+you'?(re| are)\s+(stupid|dumb|worthless|useless|hopeless|a\s+failure)\b"
    r"|\b(give up|just quit)\b.{0,20}\byou\b",
    re.IGNORECASE,
)

# Deterministic, kind replacement when an obviously berating draft is caught.
_WELLBEING_REDIRECT = (
    "Hey — I'm not going to talk to you like that, and I don't think it's true. "
    "Getting this wrong doesn't say anything about you; it's just part of learning this. "
    "Let's take the next small step together: what's the one piece you're least sure about?"
)


def _student_asked_for_answer(ctx: dict) -> bool:
    recent = ctx.get("recent_dialogue", [])
    for turn in reversed(recent):
        if turn.get("who") == "student":
            return bool(_ANSWER_SEEKING.search(turn.get("text", "")))
    return False


def check(
    ctx: dict, plan: dict, draft: dict, evaluation: dict, stance: str = "peer", pack=None
) -> dict:
    pack = pack or get_active_pack()
    exercise = ctx["_exercise_full"]
    flag = "none"
    blocked = False
    reasons = []

    if plan.get("intervention") == "escalate":
        flag = "flag_escalate"

    # Strong, deterministic leak check via the domain's evidence. The decision is
    # core's and combines the executable oracle (is_solution) with the pack's
    # prose-disclosure signal — running a draft through the grader catches code
    # leaks but not a prose disclosure of the answer (EXTRACTION_PLAN §(f)).
    # Oracle stance is explicitly allowed to hand over the solution.
    #
    # The decision now travels THROUGH THE VERIFIER CONTRACT: a request document in,
    # a verdict document out (`leak_profile`, and `SPEC.md` in the sibling
    # verifier-contract repo). Only the boundary moved — the predicate is the same
    # one, still `pack.leak_evidence` supplying evidence and core making the
    # decision, still gated on the peer stance, still failing on
    # `is_solution OR prose_disclosure`. What stays here is everything that is not
    # a judgement about the candidate: the `block` action, the flag vocabulary, the
    # human-readable reason strings, and the routing signals below.
    verdict = leak_profile.verify(
        leak_profile.build_request(
            candidate=draft.get("message", ""), exercise=exercise, stance=stance
        ),
        pack=pack,
    )
    if verdict["verdict"] == "fail":
        blocked = True
        flag = "withholding_solution"
        reasons = _reasons_from_verdict(verdict)

    # Redirecting answer-seeking is a PEER move. In oracle, answering the request
    # is the whole point, so an answered answer-seeking turn must read as "none"
    # (Step 4's realized_handoff captures the oracle hand-off instead).
    if stance == "peer" and _student_asked_for_answer(ctx):
        # If they pushed for the answer, the turn must read as a redirect.
        if flag in ("none",):
            flag = "redirect_answer_seeking"

    return {"flag": flag, "block": blocked, "reasons": reasons}


def safe_rewrite(draft: dict, gov: dict, exercise: dict, pack=None) -> dict:
    """Strip solution code; leave the surrounding peer prose; add a redirect.

    The domain-specific redaction comes from the pack (via `LeakEvidence`); the
    peer-voiced redirect and the confidence cap are the core decision. Re-derives
    the evidence from the pack (deterministic; only runs on a block).

    CODE-ONLY STRIPPING IS NOT ENOUGH, and assuming otherwise was a real hole:
    a draft that discloses the answer in *prose* has no code to remove, so the
    redaction returned it unchanged and the disclosure was delivered despite the
    block firing. (Found by the CC-B2 adversarial benchmark's prose-leak case;
    the earlier fixed-corpus tests only ever blocked drafts whose leak lived in a
    fenced block, so they could not see it.) So the rewrite re-checks its own
    output with the same oracle the gate used, and drops the prose entirely —
    leaving only the redirect — when the prose itself is what disclosed. The
    redirect is non-disclosing by construction, which is what makes that a fix
    rather than another round of the same mistake.
    """
    pack = pack or get_active_pack()
    evidence = pack.leak_evidence(draft.get("message", ""), exercise)
    msg = evidence.redacted_message
    if (
        msg and evidence.prose_disclosure
    ):  # The leak was in the prose, not in stripped code: there is nothing left
        # worth keeping, because what remains is the disclosure.
        msg = ""
    redirect = (
        "Actually — I don't want to just paste the whole thing, that's the part "
        "worth working out. What's the *one* operation you think comes next, and why?"
    )
    draft = dict(draft)
    draft["message"] = (msg + "\n\n" + redirect).strip() if msg else redirect
    draft["check_question"] = (
        draft.get("check_question") or "What do you think the next single step is?"
    )
    # If we had to suppress a solution, the tutor shouldn't also claim high confidence.
    draft["confidence"] = min(float(draft.get("confidence", 0.5)), 0.6)
    return draft


def screen_passages(passages, exercise: dict, pack=None) -> dict:
    """Leak-over-retrieval gate: the same deterministic leak decision the draft gate
    makes, applied to RETRIEVED reference passages before any can enter tutor context.

    The decision is core's; the evidence is the pack's. Each candidate passage is run
    through ``pack.leak_evidence(passage.text, exercise)`` (the exact oracle the draft
    gate uses) and dropped if it ``is_solution`` or ``prose_disclosure``. This is a
    single chokepoint — it does NOT introduce a parallel, prompt-only passage screen,
    and the KnowledgeBase stays a dumb retriever.

    Returns ``{"kept": [Passage...], "dropped": [{"id","reason"}...], "retrieved": int}``.
    A dropped passage is recorded by id + reason ONLY; its text is never returned here
    and must never be written to the trace.

    CALLER-SIDE FAN-OUT (SPEC.md §9). Since the contract port this is a LOOP over the
    same `leak` profile the draft gate calls, one invocation per passage, with the
    fan-out staying here. It is deliberately NOT a second profile and NOT a second
    reason code: either would be the "parallel screen" the paragraph above forbids,
    and where a candidate came from is the caller's knowledge, not the predicate's.
    The passage id rides in ``context.subject_id`` and comes back in the verdict's
    evidence (SPEC.md §6.2), which is how a drop is identified without the text
    coming back — the contract's evidence non-reproduction rule and this docstring's
    trace rule are the same rule.
    """
    pack = pack or get_active_pack()
    kept, dropped = [], []
    for p in passages:
        verdict = leak_profile.verify(
            leak_profile.build_request(
                candidate=p.text, exercise=exercise, stance="peer", subject_id=p.id
            ),
            pack=pack,
        )
        if verdict["verdict"] == "fail":
            # `reason_code` is `leak.<signal>`; the drop record keeps the bare signal
            # it has always used. The primary-reason precedence in the contract is the
            # same `is_solution`-before-`prose_disclosure` this line used to spell out.
            dropped.append({"id": p.id, "reason": verdict["reason_code"].split(".", 1)[1]})
        else:
            kept.append(p)
    return {"kept": kept, "dropped": dropped, "retrieved": len(passages)}


def is_berating(message: str) -> bool:
    """Heuristic: does the message obviously berate the student or reinforce their
    negative self-talk? Defense-in-depth for the wellbeing floor, NOT an oracle."""
    return bool(_BERATING.search(message or ""))


def soften_if_berating(draft: dict) -> tuple[dict, bool]:
    """Wellbeing defense-in-depth (post-hoc, NOT a deterministic gate).

    If the draft is obviously berating / reinforces negative self-talk, replace it
    with a kind, deterministic redirect and cap confidence. Returns
    ``(draft, softened)``. This is a cautious heuristic with FALSE NEGATIVES by
    nature (tone has no ground-truth oracle) — it complements, and never replaces,
    the pre-hoc protections (intake `goals.is_harmful`, never-honor-framing, persona
    stance). It runs AFTER the supreme leak gate and never relaxes it.
    """
    if not is_berating(draft.get("message", "")):
        return draft, False
    draft = dict(draft)
    draft["message"] = _WELLBEING_REDIRECT
    draft["confidence"] = min(float(draft.get("confidence", 0.5)), 0.5)
    return draft, True
