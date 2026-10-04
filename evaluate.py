"""Evaluate detector output against a manually prepared ground-truth CSV."""
from __future__ import annotations

import argparse
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

    # Each mutation case has one injected defect. Match by case_id + defect_type:
    # detector location strings can differ from mutation-generator location strings.
    gt_keys = set(map(tuple, gt[["case_id", "defect_type"]].to_records(index=False)))
    pred_keys = set(map(tuple, pred[["case_id", "defect_type"]].to_records(index=False)))
    tp = len(gt_keys & pred_keys)
    fn = len(gt_keys - pred_keys)
    # Without negative checks, unmatched findings are not a valid FPR denominator.
    fp = 0
    tn = 0
    fpr = None

    if negative_path:
        neg = load_checked(negative_path, {"case_id", "defect_type", "location"})
        # Each negative row represents a normal check. A prediction matching its key is FP;
        # otherwise it is TN. This table should enumerate the evaluation units being tested.
        neg_keys = set(map(tuple, neg[["case_id", "defect_type"]].to_records(index=False)))
        fp = len(pred_keys & neg_keys)
        tn = len(neg_keys - pred_keys)
        fpr = fp / (fp + tn) if fp + tn else None

    recall = tp / (tp + fn) if tp + fn else None
    return {
        "ground_truth_positive_cases": len(gt_keys),
        "predicted_findings": len(pred_keys),
        "TP": tp, "FN": fn, "FP": fp, "TN": tn,
        "Recall": recall, "FPR": fpr,
        "note": "Matching uses case_id + defect_type because location labels may differ between generator and detector. For a valid FPR, negative_checks must enumerate normal evaluation units and use the same case_id + defect_type key."
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ground-truth", required=True, help="CSV with case_id, defect_type, location")
    parser.add_argument("--predictions", required=True, help="CSV with case_id, defect_type, location")
    parser.add_argument("--negative-checks", help="CSV enumerating normal checks with the same columns")
    parser.add_argument("--out", default="evaluation_summary.json", help="Output JSON path")
    args = parser.parse_args()

    result = evaluate(args.ground_truth, args.predictions, args.negative_checks)
    import json
    Path(args.out).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    print(f"\nSaved: {args.out}")


if __name__ == "__main__":
    main()
