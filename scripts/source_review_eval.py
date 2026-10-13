#!/usr/bin/env python3
"""Offline source-review comparison. Never calls a provider or a database.

Bundled fixtures are synthetic smoke cases, not production gold or calibrated
thresholds. Use independently labelled real data to assess deployment quality.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from services.review_provider import compose_result

FIXTURES = Path(__file__).parent / "fixtures/source_review"


def evaluate(labels, predictions, settings):
    """Defer counts against recall; false accept rate uses all negative labels."""
    expected = {row["id"]: row["label"] for row in labels}
    if len(expected) != len(labels) or not expected or any(v not in {"accept", "reject", "abstain"} for v in expected.values()):
        raise ValueError("invalid_labels")
    rows = {row["id"]: row for row in predictions}
    if len(rows) != len(predictions) or set(rows) != set(expected):
        raise ValueError("prediction_ids_must_match_labels")
    tp = fp = positives = negatives = deferred = 0
    for key, label in expected.items():
        row = rows[key]
        values = [row["confidence"], *(row["dimensions"][name] for name in ("quality", "relevance", "spam_probability"))]
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 1 for v in values):
            raise ValueError("invalid_prediction")
        dims = row["dimensions"]
        decision = compose_result(dims["quality"], dims["relevance"], dims["spam_probability"], row["confidence"], settings)["decision"]
        # Preserve an engine's explicit abstention or rejection; thresholds never promote it.
        if row.get("decision") in {"reject", "abstain"}:
            decision = row["decision"]
        positives += label == "accept"
        negatives += label != "accept"
        tp += decision == "accept" and label == "accept"
        fp += decision == "accept" and label != "accept"
        deferred += decision == "abstain"
    return {"count": len(labels), "true_accepts": tp, "false_accepts": fp,
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / positives if positives else None,
            "false_accept_rate": fp / negatives if negatives else None,
            "defer_rate": deferred / len(labels)}


def load_json(path):
    if path.stat().st_size > 5_000_000:
        raise ValueError("evaluation_file_too_large")
    return json.loads(path.read_text())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=["dev", "holdout"], default="holdout")
    parser.add_argument("--fixtures", type=Path)
    parser.add_argument("--predictions", type=Path, help="JSON {engine:[{id,confidence,dimensions,decision?}]} matching fixture IDs")
    parser.add_argument("--min-confidence", type=float, default=.9)
    parser.add_argument("--min-quality", type=float, default=.7)
    parser.add_argument("--max-spam-probability", type=float, default=.2)
    parser.add_argument("--sweep-confidence", type=float, nargs="+", help="Development split only; don't tune on holdout")
    args = parser.parse_args(argv)
    thresholds = [args.min_confidence, args.min_quality, args.max_spam_probability, *(args.sweep_confidence or [])]
    if any(not math.isfinite(v) or not 0 <= v <= 1 for v in thresholds):
        parser.error("thresholds must be finite values in [0,1]")
    if args.sweep_confidence and args.split != "dev":
        parser.error("confidence sweep requires --split dev; holdout is for a fixed policy")
    fixture = load_json(args.fixtures or FIXTURES / f"{args.split}.json")
    labels = fixture["labels"]
    predictions = load_json(args.predictions) if args.predictions else fixture["synthetic_predictions"]
    settings = {"min_confidence": args.min_confidence, "min_quality": args.min_quality, "max_spam_probability": args.max_spam_probability}
    reports = {}
    for confidence in args.sweep_confidence or [args.min_confidence]:
        policy = {**settings, "min_confidence": confidence}
        reports[str(confidence)] = {engine: evaluate(labels, rows, policy) for engine, rows in predictions.items()}
    print(json.dumps({"split": args.split, "synthetic": fixture.get("synthetic", False),
          "notice": "Bundled cases are synthetic; these metrics do not establish production quality or calibrated thresholds.",
          "thresholds": settings, "reports": reports}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
