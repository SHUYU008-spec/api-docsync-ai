from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pandas as pd
import streamlit as st

from docsync import (
    detect_inconsistencies,
    load_openapi,
    parse_python_source,
    run_optional_llm_check,
    run_pipeline,
)

st.set_page_config(page_title="API DocSync AI", page_icon="🔎", layout="wide")
st.title("API DocSync AI")
st.caption("Prototype: Python source ↔ OpenAPI contract consistency checker")

with st.sidebar:
    st.header("Settings")
    use_llm = st.checkbox("Optional LLM semantic check", value=False)
    st.caption("Disabled by default. Enabling it sends extracted facts and the OpenAPI document to OpenRouter.")
    if use_llm:
        st.warning("Do not use confidential source code or secrets.")
    st.divider()
    st.markdown("**Current scope**")
    st.markdown("- One Python file\n- One OpenAPI JSON/YAML file\n- Static, heuristic checks")

python_file = st.file_uploader("Upload Python source file", type=["py"])
spec_file = st.file_uploader("Upload OpenAPI specification", type=["json", "yaml", "yml"])

if python_file and spec_file:
    try:
        source = python_file.getvalue().decode("utf-8")
        spec_text = spec_file.getvalue().decode("utf-8")
        spec = load_openapi(spec_text, is_text=True)
        parsed = parse_python_source(source)
        start = time.perf_counter()
        findings, llm_metadata = run_pipeline(source, spec, use_llm=use_llm)
        elapsed = time.perf_counter() - start

        st.subheader("Summary")
        c1, c2, c3 = st.columns(3)
        c1.metric("Python routes detected", len(parsed["routes"]))
        c2.metric("Findings", len(findings))
        c3.metric("Elapsed time", f"{elapsed:.2f} s")

        if findings:
            df = pd.DataFrame(findings)
            st.dataframe(df, use_container_width=True, hide_index=True)
            csv = df.to_csv(index=False).encode("utf-8")
            st.download_button("Download findings CSV", csv, "findings.csv", "text/csv")
            st.download_button(
                "Download findings JSON",
                json.dumps(findings, ensure_ascii=False, indent=2).encode("utf-8"),
                "findings.json",
                "application/json",
            )
        else:
            st.success("No inconsistencies were detected by the current checks. This does not prove the files are fully consistent.")

        with st.expander("Extracted Python routes and models"):
            st.json(parsed)
        with st.expander("OpenAPI document summary"):
            st.write(f"OpenAPI version: {spec.get('openapi', 'not specified')}")
            st.write(f"Title: {spec.get('info', {}).get('title', 'not specified')}")
            st.write(f"Operations: {sum(1 for _, v in spec.get('paths', {}).items() if isinstance(v, dict) for k in v if k.lower() in {'get','post','put','patch','delete','options','head'})}")

        if llm_metadata:
            st.subheader("LLM usage metadata")
            st.json(llm_metadata)
            st.info("Record the actual provider dashboard cost as well; the response may not include cost.")
    except Exception as exc:
        st.error(f"Could not analyse the uploaded files: {exc}")
        st.caption("Check that the Python file is syntactically valid and the OpenAPI file is valid JSON/YAML with a paths object.")
else:
    st.info("Upload both files to start. Example files are available in the project's examples/ folder.")
