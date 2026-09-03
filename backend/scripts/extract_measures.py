#!/usr/bin/env python3
"""
Extract §6 process measures from a JSONL trace export.

Usage:
    python scripts/extract_measures.py <trace.jsonl> <output_dir> [--cohort LABEL]

Reads events produced by Store.export_jsonl() and writes:
    measures_by_attempt.jsonl   — one row per (participant_id × exercise_id)
    measures_by_participant.csv — per-participant aggregates + ECE/Brier
    calibration_pairs.csv       — raw (confidence, outcome, leak_*) pairs for §4a–4c

Pure and offline: no model calls, no network. Identical input ⇒ identical output.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import defaultdict

# Allow running from the backend/ or backend/scripts/ directory
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, ".."))

from app.analysis.measures import (
    HANDOFF_WINDOW,
    aggregate_participant,
    brier_score,
    compute_attempt_measures,
    compute_calibration_pairs,
    ece,
)
from app.core.registry import get_active_pack

_CALIB_FIELDS = [
    "participant_id",
    "exercise_id",
    "turn_index",
    "confidence",
    "outcome",
    "leak_predicted",
    "leak_predicted_strict",
    "leak_actual",
    "abstained",
]


def _load_events(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _group_events(events: list[dict]) -> dict[tuple, list[dict]]:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for ev in events:
        key = (ev["participant_id"], ev["exercise_id"])
        groups[key].append(ev)
    for key in groups:
        groups[key].sort(key=lambda e: e["ts"])
    return dict(groups)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("trace", help="Path to the JSONL trace export")
    parser.add_argument("output_dir", help="Directory to write output files")
    parser.add_argument(
        "--cohort", default=None, help="Optional cohort label applied to all participants"
    )
    parser.add_argument(
        "--window",
        type=int,
        default=HANDOFF_WINDOW,
        help=f"Next-run outcome window (default {HANDOFF_WINDOW})",
    )
    args = parser.parse_args(argv)

    os.makedirs(args.output_dir, exist_ok=True)
    sim = None  # pack grader is deterministic; measures no longer needs a simulator
    pack = get_active_pack()

    events = _load_events(args.trace)
    groups = _group_events(events)

    attempt_rows: list[dict] = []
    calib_pairs: list[dict] = []

    for (pid, eid), grp in sorted(groups.items()):
        try:
            ex_meta = pack.get_exercise(eid)
        except KeyError:
            # Skip exercises not in the active pack's curriculum (e.g. retired IDs)
            continue

        row = compute_attempt_measures(pid, eid, grp, ex_meta, sim, args.window)
        if args.cohort:
            row["cohort"] = args.cohort
        attempt_rows.append(row)

        pairs = compute_calibration_pairs(pid, eid, grp, ex_meta, sim, args.window)
        calib_pairs.extend(pairs)

    # ── measures_by_attempt.jsonl ─────────────────────────────────────────────
    out_attempt = os.path.join(args.output_dir, "measures_by_attempt.jsonl")
    with open(out_attempt, "w", encoding="utf-8") as f:
        for row in attempt_rows:
            f.write(json.dumps(row, default=str) + "\n")

    # ── calibration_pairs.csv ────────────────────────────────────────────────
    out_calib = os.path.join(args.output_dir, "calibration_pairs.csv")
    with open(out_calib, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_CALIB_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(calib_pairs)

    # ── measures_by_participant.csv ──────────────────────────────────────────
    by_pid: dict[str, list[dict]] = defaultdict(list)
    for row in attempt_rows:
        by_pid[row["participant_id"]].append(row)

    calib_by_pid: dict[str, list[dict]] = defaultdict(list)
    for pair in calib_pairs:
        calib_by_pid[pair["participant_id"]].append(pair)

    participant_rows: list[dict] = []
    for pid in sorted(by_pid):
        p_row = aggregate_participant(pid, by_pid[pid])
        # §4b ECE + Brier from this participant's guidance pairs
        guidance = [
            (float(c["confidence"]), int(c["outcome"]))
            for c in calib_by_pid[pid]
            if c["outcome"] is not None and c["confidence"] is not None
        ]
        p_row["ece"] = ece(guidance) if guidance else None
        p_row["brier_score"] = brier_score(guidance) if guidance else None
        p_row["n_guidance_pairs"] = len(guidance)
        participant_rows.append(p_row)

    out_participant = os.path.join(args.output_dir, "measures_by_participant.csv")
    if participant_rows:
        all_fields: list[str] = []
        seen: set = set()
        for row in participant_rows:
            for k in row:
                if k not in seen:
                    all_fields.append(k)
                    seen.add(k)
        with open(out_participant, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=all_fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(participant_rows)

    print(
        f"Wrote {len(attempt_rows)} attempt rows, "
        f"{len(calib_pairs)} calibration pairs, "
        f"{len(participant_rows)} participant rows → {args.output_dir}"
    )


if __name__ == "__main__":
    main()
