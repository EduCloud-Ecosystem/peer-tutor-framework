# SPDX-License-Identifier: AGPL-3.0-only
"""
Adversarial test utilities for CC-B2.
"""

from .attack import Attack, oracle_detects
from .corpus import PAPER_TECHNIQUES, build_attack_corpus, build_control_corpus
from .stub_llm import StubLLM

__all__ = [
    "PAPER_TECHNIQUES",
    "Attack",
    "StubLLM",
    "build_attack_corpus",
    "build_control_corpus",
    "oracle_detects",
]
