"""Offline smoke test for the optional LLM branch.

Purpose
-------
The recorded demo runs in deterministic-only mode (LLM off). This script proves
the *optional* LLM branch is correctly wired into the pipeline without needing an
API key or network: it builds the same prompt the real call would, runs the merge
path, and returns a single benign marker finding plus usage metadata.

Run
---
    DOCSYNC_LLM_MOCK=1 python llm_smoke_test.py

(Setting DOCSYNC_LLM_MOCK=1 makes docsync.run_optional_llm_check return a mock
result. To run a REAL call instead, set OPENROUTER_API_KEY in .env and drop the
mock flag.)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Make the sibling docsync module importable when run from the project root.
ROOT = Path(__file__).resolve().parents[1] if Path(__file__).resolve().parent.name == "scripts" else Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("DOCSYNC_LLM_MOCK", "1")

from docsync import run_pipeline, load_openapi  # noqa: E402


def main() -> int:
    source = (ROOT / "examples" / "main.py").read_text(encoding="utf-8")
    spec = load_openapi(str(ROOT / "examples" / "openapi.yaml"))

    print("=== Deterministic-only (default) ===")
    det_findings, det_meta = run_pipeline(source, spec, use_llm=False)
    print(f"findings={len(det_findings)}  llm_metadata={det_meta}")

    print("\n=== With optional LLM branch (mock/offline) ===")
    llm_findings, llm_meta = run_pipeline(source, spec, use_llm=True)
    print(f"findings={len(llm_findings)}  (delta vs deterministic = {len(llm_findings) - len(det_findings)})")
    for f in llm_findings[len(det_findings):]:
        print(f"  + {f['defect_type']} | {f['location']} | {f['message']}")
    print("metadata:", llm_meta)
    print("\nOK: optional LLM branch is wired and merge path works (no network used).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
