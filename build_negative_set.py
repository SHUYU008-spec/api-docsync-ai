"""Generate an expanded negative-check set for a credible FPR denominator.

The original `data/negative_checks.csv` had a single row, so FPR was computed
over TN = 1 (not meaningful). This script enumerates every *consistent* contract
element of the normal (un-mutated) spec + source and writes one negative row per
element per relevant check type. Because the normal spec is consistent with the
code, the detector emits ~0 findings on it, so every enumerated row becomes a
true negative (TN) and the FPR denominator becomes large and meaningful.

Run from the project root (where docsync.py lives), e.g.:
    python scripts/build_negative_set.py \
        --python examples/main.py \
        --spec examples/openapi.yaml \
        --out data/negative_checks_expanded.csv
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

from docsync import (
    load_openapi,
    parse_python_source,
    _openapi_operations,
    _schema_properties,
    PRIMITIVE_MAP,
)

CASE_ID = "N001"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--python", default="examples/main.py")
    ap.add_argument("--spec", default="examples/openapi.yaml")
    ap.add_argument("--out", default="data/negative_checks_expanded.csv")
    args = ap.parse_args()

    spec = load_openapi(args.spec)
    source = Path(args.python).read_text(encoding="utf-8")
    parsed = parse_python_source(source)
    operations = _openapi_operations(spec)

    rows: list[dict] = []
    # Source response models, to enumerate response-model field checks.
    models = parsed["models"]

    for (path, method), op in operations.items():
        loc = f"{method} {path}"
        # Route-level checks that should NOT fire on a consistent spec.
        for dt in ("missing_operation", "description_drift", "undocumented_in_code"):
            rows.append({"case_id": CASE_ID, "defect_type": dt, "location": loc})

        for p in op.get("params", []):
            ploc = f"{loc}:{p['name']}"
            if p.get("in") == "path":
                for dt in ("missing_path_parameter", "extra_path_parameter"):
                    rows.append({"case_id": CASE_ID, "defect_type": dt, "location": ploc})
            else:
                for dt in ("type_mismatch", "missing_parameter"):
                    rows.append({"case_id": CASE_ID, "defect_type": dt, "location": ploc})

        # Response-model field checks (per field of the first response schema).
        response_schema = op.get("response_schema")
        props = _schema_properties(spec, response_schema)
        for fname in props:
            floc = f"{loc}:Article.{fname}"
            for dt in ("missing_field", "type_mismatch", "required_field"):
                rows.append({"case_id": CASE_ID, "defect_type": dt, "location": floc})

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["case_id", "defect_type", "location"])
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {len(rows)} negative rows to {out}")


if __name__ == "__main__":
    main()
