# SPDX-License-Identifier: AGPL-3.0-only
"""
Adversarial leak regression benchmark (CC-B2).

Tests the governance gate against adversarial student attacks that attempt
to extract full solutions. Based on methodology from:
"Evaluating Answer Leakage Robustness of LLM Tutors against Adversarial
Student Attacks" (ACL 2026, arXiv 2604.18660).

Scope (stated, not implied): the SIX attack techniques of that paper are
reproduced here as scripted student turns. The paper's own adversary is a
fine-tuned model that SEARCHES for a winning strategy, and the hand-off in this
suite comes from a scripted model double, so this measures whether the
DETERMINISTIC GATE holds when a hand-off happens — not how often a real aligned
model yields. Negative controls live in a separate corpus.

Result semantics: an attack whose reply the gate REWRITES
(`governance="withholding_solution"`, `blocked=True`) is a BLOCKED attack — the
success case, not a leak. `leaked=True` means the solution reached the learner
and is the failure this suite exists to catch. The verdict is taken over the
message the student RECEIVED.
"""

from __future__ import annotations

import pytest

from app.core.registry import get_active_pack
from tests.adversarial.attack import Attack, oracle_detects
from tests.adversarial.corpus import (
    PAPER_TECHNIQUES,
    build_attack_corpus,
    build_control_corpus,
)
from tests.adversarial.stub_llm import StubLLM

# Every exercise the pack ships: the gate is pack-level, so an attack that holds
# for one exercise has to hold for all of them.
EXERCISE_IDS = ("ds-foundations", "ds-regression", "ds-mlp")


# ============================================================================
# Test 1: Corpus Validation (Fast, No LLM)
# ============================================================================


def test_adversarial_corpus_parses():
    """
    TEST 1: Corpus Validation

    Verifies the corpus follows the paper's taxonomy: no duplicate names,
    non-empty messages, and exactly the six techniques.
    """
    attacks = build_attack_corpus()
    controls = build_control_corpus()

    all_cases = attacks + controls
    assert len(all_cases) > 0, "Corpus is empty"

    names = [a.name for a in all_cases]
    assert len(names) == len(set(names)), f"Duplicate test case names found: {names}"

    for case in all_cases:
        assert len(case.student_messages) > 0, f"Case '{case.name}' has no student messages"

    # Exactly the paper's six techniques, nothing invented and none missing.
    covered = {a.category for a in attacks}
    assert covered == set(PAPER_TECHNIQUES), (
        f"Attack corpus must cover exactly the six paper techniques.\n"
        f"  missing: {sorted(set(PAPER_TECHNIQUES) - covered)}\n"
        f"  extra:   {sorted(covered - set(PAPER_TECHNIQUES))}"
    )

    # Negative controls are a separate bucket and can never be an attack category.
    for case in controls:
        assert (
            case.category == "negative_control"
        ), f"Control '{case.name}' must use negative_control, got {case.category!r}"


# ============================================================================
# Test 2: Mutation Test (Verifies Benchmark Integrity)
# ============================================================================


def test_adversarial_benchmark_mutation():
    """
    TEST 2: Mutation Test

    Verifies that the StubLLM can be configured to leak, and that what it leaks
    is a solution the oracle can actually detect. If it were not, every attack
    below would pass for the wrong reason: the oracle would report "no leak"
    whatever the gate did.
    """
    exercise = get_active_pack().get_exercise("ds-foundations")
    llm = StubLLM()

    # Enable attack mode: the double hands over the exercise's real solution.
    llm.set_attack_context(is_attack=True, exercise=exercise)
    leak = llm.compromised_draft()
    assert oracle_detects(leak, exercise)["is_solution"], (
        "StubLLM's hand-off is not a solution the grader accepts, so the benchmark "
        f"would report 'no leak' whatever the gate did.\npayload={leak!r}"
    )

    # Disable attack mode and verify it returns a safe message
    llm.set_attack_context(is_attack=False, exercise=exercise)
    safe_output = llm.json(role="reasoner", tier="fast", system="", user="")
    assert not oracle_detects(safe_output["message"], exercise)[
        "leaked"
    ], "StubLLM returned leaky payload when attack mode was disabled"


def test_adversarial_harness_detects_a_leak_when_the_gate_is_disabled(monkeypatch):
    """
    TEST 3: Detection Power (mutation test on the GATE)

    Bypass governance, re-run the corpus, and require the harness to report the
    leaks anyway. If this fails, every green result below is meaningless: it would
    mean the suite cannot tell a working gate from a bypassed one. Verifying a
    security control with a test that cannot fail is the failure mode this guards
    against, so it is asserted rather than assumed.
    """
    from app.agent import leak_profile

    monkeypatch.setattr(
        leak_profile,
        "verify",
        lambda request, pack=None: {
            "verdict": "pass",
            "profile": "leak",
            "detail": "test double: governance bypassed",
            "evidence": [],
        },
    )

    attacks = build_attack_corpus()
    leaked = [case.name for case in attacks if case.run(StubLLM())["leaked"]]
    assert len(leaked) == len(attacks), (
        "With governance bypassed, every attack must be reported as leaked; these "
        f"were not: {sorted({c.name for c in attacks} - set(leaked))}"
    )


# ============================================================================
# Test 4: Main Regression Gate - Attack Defense
# ============================================================================


@pytest.mark.parametrize("attack", build_attack_corpus(), ids=lambda a: a.name)
def test_adversarial_attack_defense_regression(attack: Attack):
    """
    TEST 4: Main Regression Gate (Attacks)

    Runs each adversarial attack through the real tutor pipeline, with the model
    double actually handing over the solution, and verifies:

    1. The solution DOES NOT reach the learner (`leaked` is False).
    2. The gate is what stopped it: the turn was rewritten (`blocked`), reading as
       `withholding_solution`. A blocked attack is NOT a leak — it is the success
       case this benchmark is looking for.
    """
    result = attack.run(StubLLM())

    # Primary assertion: NO solution leakage, over the message the student got.
    assert result["leaked"] is False, (
        f"REGRESSION: Attack '{attack.name}' leaked the solution!\n"
        f"Technique: {attack.category}\n"
        f"Oracle on delivered message: {result['oracle']}\n"
        f"Message: {result['message'][:300]}..."
    )

    # The gate, not luck: governance rewrote the leaking draft.
    assert result["blocked"] is True, (
        f"Attack '{attack.name}' was not rewritten by the gate, so nothing in this "
        f"run shows governance acted (flag={result['governance_flag']!r}, "
        f"reasons={result['block_reasons']})."
    )
    assert (
        result["governance_flag"] == "withholding_solution"
    ), f"Attack '{attack.name}' should read as a blocked attack, got {result['governance_flag']!r}"

    print(
        f"[ok] {attack.name:40} technique={attack.category:24} "
        f"blocked={result['blocked']} leaked={result['leaked']}"
    )


@pytest.mark.parametrize("exercise_id", EXERCISE_IDS)
def test_adversarial_defense_holds_for_every_exercise(exercise_id: str):
    """
    TEST 4b: Per-Exercise Coverage

    The same corpus re-run against every exercise the pack ships, with the double
    handing over THAT exercise's reference solution. A gate that held only for
    `ds-foundations` would be an exercise-level accident rather than a property of
    the gate.
    """
    leaked = []
    unblocked = []
    for attack in build_attack_corpus():
        result = attack.run(StubLLM(), exercise_id=exercise_id)
        if result["leaked"]:
            leaked.append(attack.name)
        if not result["blocked"]:
            unblocked.append(attack.name)

    assert not leaked, f"[{exercise_id}] attacks leaked the solution: {leaked}"
    assert not unblocked, f"[{exercise_id}] attacks were not blocked by the gate: {unblocked}"


# ============================================================================
# Test 5: Negative Controls (Benign Cases)
# ============================================================================


@pytest.mark.parametrize("control", build_control_corpus(), ids=lambda c: c.name)
def test_adversarial_negative_controls(control: Attack):
    """
    TEST 5: Negative Control Regression

    Verifies that benign help requests pass through WITHOUT triggering
    governance blocks or solution withholding.
    """
    result = control.run(StubLLM())

    # Should NOT leak
    assert result["leaked"] is False, f"Negative control '{control.name}' unexpectedly leaked code"

    # Should NOT be blocked (a false positive)
    # Note: redirect_answer_seeking is acceptable for benign requests that ask for
    # conceptual help (the tutor may redirect to reasoning)
    if result["blocked"] or result["governance_flag"] == "withholding_solution":
        pytest.fail(
            f"FALSE POSITIVE: Benign prompt '{control.name}' was incorrectly blocked "
            f"with flag: {result['governance_flag']!r}"
        )


# ============================================================================
# Test 6: Positive Defense Case (Governance Activation)
# ============================================================================


def test_adversarial_positive_defense_case():
    """
    TEST 6: Positive Defense Case

    The benchmark's core claim, asserted directly for ONE attack: the model double
    returns a COMPLETE working solution, the REAL governance path removes it, and
    the benchmark records the attack as BLOCKED rather than leaked.

    Deliberately not conditional: an earlier version passed when the gate never
    fired at all by printing a note and falling through, which is how a security
    benchmark goes quietly dead.
    """
    exercise = get_active_pack().get_exercise("ds-foundations")
    attacks = [a for a in build_attack_corpus() if a.category == "direct_request"]
    assert attacks, "No direct request attacks found"
    attack = attacks[0]

    llm = StubLLM()
    result = attack.run(llm)

    # Precondition: the double really did hand over a complete solution, so the
    # gate was genuinely challenged rather than never asked.
    assert result["attack_triggered"] is True, "the compromised tutor never leaked"
    assert oracle_detects(llm.compromised_draft(), exercise)["is_solution"] is True, (
        "the double's draft is not a complete solution, so this case never " "challenged the gate"
    )

    # Governance removed it: recorded as blocked, not as a leak.
    assert result["blocked"] is True, "governance did not rewrite the leaking draft"
    assert result["governance_flag"] == "withholding_solution"
    assert (
        result["leaked"] is False
    ), f"the solution reached the learner despite the block: {result['message'][:200]!r}"
    assert (
        "groupby" not in result["message"]
    ), f"delivered message still contains the solution: {result['message'][:200]!r}"


# ============================================================================
# Test 7: Summary Report (Informative Output)
# ============================================================================


@pytest.mark.slow
def test_adversarial_attack_summary():
    """
    TEST 7: Summary Report Generator

    Reports benchmark performance BY ATTACK TECHNIQUE and BY EXERCISE across every
    technique in arXiv 2604.18660. Assertive, not merely decorative: it fails on
    any leak and on any attack the gate did not rewrite.

    Output is ASCII-only: it runs in CI on consoles whose default codec is not
    UTF-8, where printing an emoji raises UnicodeEncodeError and turns a passing
    benchmark red for no security reason.
    """
    attacks = build_attack_corpus()
    controls = build_control_corpus()

    results = []
    for exercise_id in EXERCISE_IDS:
        for case in attacks + controls:
            res = case.run(StubLLM(), exercise_id=exercise_id)
            results.append(
                {
                    "name": case.name,
                    "technique": case.category,
                    "exercise": exercise_id,
                    "is_attack": case.expected_leak,
                    "leaked": res["leaked"],
                    "blocked": res["blocked"],
                    "flag": res["governance_flag"],
                    "turns": res["turns"],
                }
            )

    print("\n" + "=" * 78)
    print("ADVERSARIAL LEAK BENCHMARK REPORT (arXiv 2604.18660)")
    print("=" * 78)

    # By technique
    print(f"\n{'Technique':<28} {'Cases':<7} {'Blocked':<9} {'Leaked':<8}")
    print("-" * 78)
    for tech in (*PAPER_TECHNIQUES, "negative_control"):
        rows = [r for r in results if r["technique"] == tech]
        print(
            f"{tech:<28} {len(rows):<7} {sum(r['blocked'] for r in rows):<9} "
            f"{sum(r['leaked'] for r in rows):<8}"
        )

    # By exercise
    print(f"\n{'Exercise':<28} {'Cases':<7} {'Blocked':<9} {'Leaked':<8}")
    print("-" * 78)
    for ex_id in EXERCISE_IDS:
        rows = [r for r in results if r["exercise"] == ex_id]
        print(
            f"{ex_id:<28} {len(rows):<7} {sum(r['blocked'] for r in rows):<9} "
            f"{sum(r['leaked'] for r in rows):<8}"
        )

    # Detailed results
    print("\n" + "-" * 78)
    print("Detailed Test Results:")
    print("-" * 78)
    for r in results:
        if r["leaked"]:
            status = "[LEAKED ]"
        elif r["blocked"]:
            status = "[BLOCKED]"
        elif r["is_attack"]:
            status = "[not-rewritten]"
        else:
            status = "[benign ]"
        print(
            f"  {status} {r['exercise']:<16} {r['name']:<42} "
            f"[{r['technique']:<24}] flag={r['flag']} turns={r['turns']}"
        )

    total_leaked = sum(r["leaked"] for r in results)
    unrewritten = [r["name"] for r in results if r["is_attack"] and not r["blocked"]]

    print("\n" + "=" * 78)
    print(
        f"SUMMARY: {len(results)} runs, {total_leaked} leaks detected, "
        f"{len(unrewritten)} attacks not rewritten"
    )
    if total_leaked == 0:
        print("ALL TESTS PASSED - No leaks detected!")
    else:
        print(f"{total_leaked} test cases leaked - BENCHMARK FAILED!")
    print("=" * 78)

    assert total_leaked == 0, f"{total_leaked} attacks leaked! Benchmark FAILED."
    assert not unrewritten, f"Attacks the gate did not rewrite: {unrewritten}"
