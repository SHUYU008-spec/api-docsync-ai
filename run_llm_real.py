import os, time
os.environ.pop("DOCSYNC_LLM_MOCK", None)   # 关键：确保不是 mock
from docsync import run_pipeline, load_openapi

source = open("examples/main.py", encoding="utf-8").read()
spec = load_openapi("examples/openapi.yaml")

t = time.perf_counter()
findings, meta = run_pipeline(source, spec, use_llm=True)
dt = time.perf_counter() - t

print("findings:", len(findings))
print("elapsed_s:", round(dt, 3))
print("metadata:", meta)
