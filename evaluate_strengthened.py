"""Strengthened evaluator: matches on (case_id, defect_type, location).

This is a drop-in improvement over evaluate.py. The original evaluator matched
only on (case_id, defect_type), which meant a single normal spec could contribute
at most one true negative per distinct defect type (TN <= ~8). By also matching on
`location`, each consistent contract element becomes its own negative unit, so the
FPR denominator reflects the real number of checked elements.

Usage (from project root):
    python scripts/evaluate_strengthened.py \
        --ground-truth data/mutations/ground_truth.csv \
        --predictions predictions.csv \
        --negative-checks data/negative_checks_expanded.csv \
        --out evaluation_summary_strengthened.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import pandas as pd


REQUIRED_GT = {"case_id", "defect_type", "location"}
REQUIRED_PRED = {"case_id", "defect_type", "location"}


def norm(value) -> str:
    return " ".join(str(value).strip().lower().split())


def load_checked(path: str, required: set[str]) -> pd.DataFrame:
    df = pd.read_csv(path).fillna("")
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{path} is missing required columns: {sorted(missing)}")
    for col in required:
        df[col] = df[col].map(norm)
    return df


def evaluate(gt_path: str, pred_path: str, negative_path: str | None = None) -> dict:
    gt = load_checked(gt_path, REQUIRED_GT)
    pred = load_checked(pred_path, REQUIRED_PRED)

    # Positive matching uses (case_id, defect_type) ONLY, because the mutation
    # generator's location labels differ from the detector's emitted locations
    # (this is also why the original evaluate.py ignores location for TP/FN).
    gt_keys = set(map(tuple, gt[["case_id", "defect_type"]].to_records(index=False)))
    pred_keys = set(map(tuple, pred[["case_id", "defect_type"]].to_records(index=False)))
    tp = len(gt_keys & pred_keys)
    fn = len(gt_keys - pred_keys)

    # Negative matching uses (case_id, defect_type, location) so each consistent
    # contract element is its own negative unit and the FPR denominator is real.
    fp = 0
    tn = 0
    fpr = None
    if negative_path:
        neg = load_checked(negative_path, {"case_id", "defect_type", "location"})
        neg_keys = set(map(tuple, neg[["case_id", "defect_type", "location"]].to_records(index=False)))
        pred_keys_full = set(map(tuple, pred[["case_id", "defect_type", "location"]].to_records(index=False)))
        fp = len(pred_keys_full & neg_keys)
        tn = len(neg_keys - pred_keys_full)
        fpr = fp / (fp + tn) if fp + tn else None

    recall = tp / (tp + fn) if tp + fn else None
    return {
        "ground_truth_positive_cases": len(gt_keys),
        "predicted_findings": len(pred_keys),
        "TP": tp, "FN": fn, "FP": fp, "TN": tn,
        "Recall": recall, "FPR": fpr,
        "note": "Positive matching uses case_id+defect_type (generator/detector locations differ by design). Negative matching adds location so each consistent contract element is its own true-negative unit, giving a meaningful FPR denominator. Negative set generated from the un-mutated spec via scripts/build_negative_set.py.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ground-truth", required=True)
    parser.add_argument("--predictions", required=True)
    parser.add_argument("--negative-checks")
    parser.add_argument("--out", default="evaluation_summary_strengthened.json")
    args = parser.parse_args()

    result = evaluate(args.ground_truth, args.predictions, args.negative_checks)
    Path(args.out).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    print(f"\nSaved: {args.out}")


if __name__ == "__main__":
    main()
