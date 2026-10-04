"""Run the detector against every mutated OpenAPI file and save predictions."""
from __future__ import annotations

import argparse
from pathlib import Path
import pandas as pd

from docsync import detect_inconsistencies, load_openapi, run_pipeline


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", required=True, help="Python source file")
    parser.add_argument("--mutations-dir", required=True, help="Directory containing M001.json, M002.json, ...")
    parser.add_argument("--out", default="predictions.csv")
    parser.add_argument("--llm", action="store_true",
                        help="Also run the optional OpenRouter LLM semantic layer "
                             "(needs OPENROUTER_API_KEY, or set DOCSYNC_LLM_MOCK=1 for an offline run).")
    args = parser.parse_args()

    source = Path(args.python).read_text(encoding="utf-8")
    mutation_dir = Path(args.mutations_dir)
    rows = []
    for spec_path in sorted(mutation_dir.glob("[MN]*.json")):
        case_id = spec_path.stem
        spec = load_openapi(str(spec_path))
        findings, _llm_meta = run_pipeline(source, spec, use_llm=args.llm)
        for finding in findings:
            rows.append({
                "case_id": case_id,
                "defect_type": finding["defect_type"],
                "location": finding["location"],
                "message": finding["message"],
                "evidence": finding["evidence"],
            })
    pd.DataFrame(rows, columns=[
        "case_id", "defect_type", "location", "message", "evidence"
    ]).to_csv(args.out, index=False)
    print(f"Processed {len(list(mutation_dir.glob('[MN]*.json')))} cases.")
    print(f"Saved {len(rows)} findings to {args.out}")


if __name__ == "__main__":
    main()
