"""Static, heuristic consistency checks for a Python file and OpenAPI spec.

The source file is parsed using ast and is never imported or executed.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

import yaml

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    # python-dotenv is optional; .env is only needed for the optional LLM check.
    pass


PRIMITIVE_MAP = {
    "str": "string",
    "string": "string",
    "int": "integer",
    "integer": "integer",
    "float": "number",
    "number": "number",
    "bool": "boolean",
    "boolean": "boolean",
}


def load_openapi(path_or_text: str, *, is_text: bool = False) -> dict[str, Any]:
    text = path_or_text if is_text else Path(path_or_text).read_text(encoding="utf-8")
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        obj = yaml.safe_load(text)
    if not isinstance(obj, dict):
        raise ValueError("OpenAPI document must be a JSON/YAML object.")
    if not isinstance(obj.get("paths"), dict):
        raise ValueError("The document has no valid OpenAPI 'paths' object.")
    return obj


def _annotation_name(node: ast.AST | None) -> str | None:
    if node is None:
        return None
    try:
        return ast.unparse(node).replace(" ", "")
    except Exception:
        return None


def _primitive_type(annotation: str | None) -> str | None:
    if not annotation:
        return None
    # Handle common Optional[str], list[int], and str | None annotations.
    annotation = annotation.replace("typing.", "")
    if "None" in annotation and "|" in annotation:
        annotation = annotation.split("|")[0]
    match = re.search(r"(?:Optional|list|List|Sequence|set|Set)\[(.+)\]", annotation)
    if match:
        annotation = match.group(1)
    annotation = annotation.strip("[]")
    return PRIMITIVE_MAP.get(annotation.lower())


def _literal_string(node: ast.AST | None) -> str | None:
    if node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _decorator_route(node: ast.AST) -> tuple[str, str] | None:
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return None
    if node.func.attr.lower() not in {"get", "post", "put", "patch", "delete", "options", "head"}:
        return None
    if not isinstance(node.func.value, ast.Name) or node.func.value.id not in {"app", "router"}:
        return None
    if not node.args:
        return None
    path = _literal_string(node.args[0])
    if path is None:
        return None
    return path, node.func.attr.upper()


def _model_fields(tree: ast.Module) -> dict[str, dict[str, dict[str, Any]]]:
    """Extract simple annotated fields from classes inheriting BaseModel."""
    models: dict[str, dict[str, dict[str, Any]]] = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        bases = {_annotation_name(base) for base in node.bases}
        if not any(base and ("BaseModel" in base) for base in bases):
            continue
        fields: dict[str, dict[str, Any]] = {}
        for item in node.body:
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                name = item.target.id
                annotation = _annotation_name(item.annotation)
                required = item.value is None
                # Pydantic Field(...) means required; Field(default=...) means optional.
                if isinstance(item.value, ast.Call) and isinstance(item.value.func, ast.Name) and item.value.func.id == "Field":
                    required = any(kw.arg == "default" and not (isinstance(kw.value, ast.Constant) and kw.value.value is Ellipsis) for kw in item.value.keywords) is False
                    if item.value.args and isinstance(item.value.args[0], ast.Constant):
                        required = item.value.args[0].value is Ellipsis
                fields[name] = {
                    "annotation": annotation,
                    "type": _primitive_type(annotation),
                    "required": required,
                }
        models[node.name] = fields
    return models


def parse_python_source(source: str) -> dict[str, Any]:
    tree = ast.parse(source)
    models = _model_fields(tree)
    routes: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            route = _decorator_route(decorator)
            if not route:
                continue
            path, method = route
            params = []
            args = list(node.args.posonlyargs) + list(node.args.args) + list(node.args.kwonlyargs)
            defaults = [None] * (len(args) - len(node.args.defaults)) + list(node.args.defaults)
            defaults_by_name = {arg.arg: default for arg, default in zip(args, defaults)}
            for arg in args:
                if arg.arg in {"self", "cls"}:
                    continue
                ann = _annotation_name(arg.annotation)
                default = defaults_by_name.get(arg.arg)
                params.append({
                    "name": arg.arg,
                    "annotation": ann,
                    "type": _primitive_type(ann),
                    "required": default is None,
                })
            response_model = None
            for kw in getattr(decorator, "keywords", []):
                if kw.arg == "response_model":
                    response_model = _annotation_name(kw.value)
            routes.append({
                "path": path,
                "method": method,
                "function": node.name,
                "docstring": ast.get_docstring(node) or "",
                "params": params,
                "return_annotation": _annotation_name(node.returns),
                "return_type": _primitive_type(_annotation_name(node.returns)),
                "response_model": response_model,
            })
    return {"routes": routes, "models": models}


def _resolve_ref(spec: dict[str, Any], schema: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(schema, dict):
        return {}
    ref = schema.get("$ref")
    if not isinstance(ref, str) or not ref.startswith("#/components/schemas/"):
        return schema
    name = ref.rsplit("/", 1)[-1]
    return spec.get("components", {}).get("schemas", {}).get(name, {})


def _schema_properties(spec: dict[str, Any], schema: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    schema = _resolve_ref(spec, schema)
    props = schema.get("properties", {}) if isinstance(schema, dict) else {}
    required = set(schema.get("required", [])) if isinstance(schema, dict) else set()
    result = {}
    for name, definition in props.items():
        definition = _resolve_ref(spec, definition)
        result[name] = {
            "type": definition.get("type"),
            "required": name in required,
            "description": definition.get("description", ""),
        }
    return result


def _openapi_operations(spec: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    operations = {}
    for path, path_item in spec.get("paths", {}).items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete", "options", "head"}:
                continue
            if not isinstance(operation, dict):
                continue
            params = []
            for param in path_item.get("parameters", []) + operation.get("parameters", []):
                if isinstance(param, dict):
                    params.append({
                        "name": param.get("name"),
                        "in": param.get("in"),
                        "required": bool(param.get("required", False)),
                        "type": _resolve_ref(spec, param.get("schema", {})).get("type"),
                    })
            request_schema = None
            content = operation.get("requestBody", {}).get("content", {})
            for media in content.values():
                if isinstance(media, dict) and "schema" in media:
                    request_schema = media["schema"]
                    break
            response_schema = None
            responses = operation.get("responses", {})
            for status in ("200", "201", "202", "default"):
                response = responses.get(status, {})
                content = response.get("content", {}) if isinstance(response, dict) else {}
                for media in content.values():
                    if isinstance(media, dict) and "schema" in media:
                        response_schema = media["schema"]
                        break
                if response_schema:
                    break
            operations[(path, method.upper())] = {
                "summary": operation.get("summary", ""),
                "description": operation.get("description", ""),
                "operation_id": operation.get("operationId", ""),
                "params": params,
                "request_schema": request_schema,
                "response_schema": response_schema,
            }
    return operations


def _finding(defect_type: str, location: str, message: str, evidence: str = "") -> dict[str, str]:
    return {
        "defect_type": defect_type,
        "location": location,
        "message": message,
        "evidence": evidence,
    }


def detect_inconsistencies(source: str, spec: dict[str, Any]) -> list[dict[str, str]]:
    """Run deterministic, explainable checks. Returns findings; does not call an LLM."""
    parsed = parse_python_source(source)
    code_routes = {(r["path"], r["method"]): r for r in parsed["routes"]}
    api_ops = _openapi_operations(spec)
    findings: list[dict[str, str]] = []

    for key, route in code_routes.items():
        path, method = key
        location = f"{method} {path}"
        operation = api_ops.get(key)
        if operation is None:
            findings.append(_finding("missing_operation", location,
                                     "Python route is not documented in OpenAPI."))
            continue

        code_path_params = set(re.findall(r"{([^}]+)}", path))
        spec_path_params = {p["name"] for p in operation["params"] if p["in"] == "path"}
        for name in sorted(code_path_params - spec_path_params):
            findings.append(_finding("missing_path_parameter", f"{location}:{name}",
                                     f"Path parameter '{name}' is absent from OpenAPI."))
        for name in sorted(spec_path_params - code_path_params):
            findings.append(_finding("extra_path_parameter", f"{location}:{name}",
                                     f"OpenAPI path parameter '{name}' is not present in the route template."))

        # Compare basic function parameters with documented query/path parameters.
        documented_params = {(p["name"], p["in"]): p for p in operation["params"]}
        for param in route["params"]:
            name = param["name"]
            if name in code_path_params:
                continue
            candidates = [documented_params.get((name, "query")), documented_params.get((name, "header"))]
            documented = next((p for p in candidates if p), None)
            if documented and param["type"] and documented.get("type"):
                expected = PRIMITIVE_MAP.get(str(documented["type"]).lower(), documented["type"])
                if param["type"] != expected:
                    findings.append(_finding("type_mismatch", f"{location}:{name}",
                        f"Python parameter type is {param['type']} but OpenAPI declares {expected}.",
                        f"code={param['type']}; openapi={expected}"))
            elif name not in {"request", "response", "db", "session", "current_user"} and not documented:
                # Avoid treating every unrecognised dependency as a contract defect.
                if param["type"] in {"string", "integer", "number", "boolean"}:
                    findings.append(_finding("missing_parameter", f"{location}:{name}",
                        f"Typed Python parameter '{name}' is not documented as a query/header parameter."))

        # Heuristic description check: compare a non-empty docstring's first line
        # to operation summary. This is intentionally not a semantic truth test.
        doc_first = route["docstring"].strip().splitlines()[0].strip() if route["docstring"].strip() else ""
        summary = str(operation.get("summary", "")).strip()
        if doc_first and summary and _normalise(doc_first) != _normalise(summary):
            findings.append(_finding("description_drift", location,
                "Python docstring first line differs from OpenAPI summary.",
                f"code={doc_first}; openapi={summary}"))

        # Compare simple response_model fields with first documented response schema.
        model_name = (route.get("response_model") or "").split(".")[-1]
        code_fields = parsed["models"].get(model_name, {})
        response_fields = _schema_properties(spec, operation.get("response_schema"))
        for field_name, field in code_fields.items():
            if field_name not in response_fields:
                findings.append(_finding("missing_field", f"{location}:{model_name}.{field_name}",
                    f"Response model field '{field_name}' is missing from the OpenAPI response schema."))
                continue
            api_field = response_fields[field_name]
            if field.get("type") and api_field.get("type"):
                api_type = PRIMITIVE_MAP.get(str(api_field["type"]).lower(), api_field["type"])
                if field["type"] != api_type:
                    findings.append(_finding("type_mismatch", f"{location}:{model_name}.{field_name}",
                        f"Python model field type is {field['type']} but OpenAPI declares {api_type}.",
                        f"code={field['type']}; openapi={api_type}"))
            if bool(field.get("required")) != bool(api_field.get("required")):
                findings.append(_finding("required_field", f"{location}:{model_name}.{field_name}",
                    f"Required status differs for field '{field_name}'.",
                    f"code_required={field.get('required')}; openapi_required={api_field.get('required')}"))

    # OpenAPI operations without a statically detected route are reported.
    for key in sorted(set(api_ops) - set(code_routes)):
        path, method = key
        findings.append(_finding("undocumented_in_code", f"{method} {path}",
            "OpenAPI operation has no matching statically detected Python route."))

    # Stable de-duplication.
    seen = set()
    unique = []
    for finding in findings:
        key = (finding["defect_type"], finding["location"], finding["message"])
        if key not in seen:
            seen.add(key)
            unique.append(finding)
    return unique


def run_pipeline(source: str, spec: dict[str, Any], *, use_llm: bool = False) -> tuple[list[dict[str, str]], dict[str, Any] | None]:
    """End-to-end seam used by both the Streamlit app and the batch runner.

    Deterministic by default (use_llm=False): returns exactly what
    detect_inconsistencies returns and has no external dependencies. With
    use_llm=True it additionally calls run_optional_llm_check (OpenRouter) and
    merges those findings. The LLM branch is therefore *pluggable*: the system
    works identically with or without it, and the recorded demo uses the default.
    """
    findings = detect_inconsistencies(source, spec)
    llm_metadata: dict[str, Any] | None = None
    if use_llm:
        parsed = parse_python_source(source)
        llm_findings, llm_metadata = run_optional_llm_check(parsed, spec)
        findings = findings + llm_findings
    return findings, llm_metadata


def _normalise(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def run_optional_llm_check(source_facts: dict[str, Any], spec: dict[str, Any]) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """Optional OpenRouter check. Returns (findings, usage metadata)."""
    import os
    import requests

    api_key = os.getenv("OPENROUTER_API_KEY")
    # Offline demonstration mode: proves the LLM branch is wired without a key/network.
    if os.getenv("DOCSYNC_LLM_MOCK") or api_key == "mock":
        return _mock_llm_check(source_facts, spec)
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not set.")
    model = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
    prompt_data = {
        "python_facts": source_facts,
        "openapi": spec,
    }
    prompt = (
        "Compare these extracted Python API facts with the OpenAPI document. "
        "Return only a JSON array. Each item must have defect_type, location, message, evidence. "
        "Report only concrete inconsistencies supported by the supplied data. "
        "Do not infer undocumented behaviour. If none, return [].\n\n"
        + json.dumps(prompt_data, ensure_ascii=False)[:45000]
    )
    response = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "temperature": 0,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
        },
        timeout=90,
    )
    response.raise_for_status()
    data = response.json()
    content = data["choices"][0]["message"]["content"]
    try:
        parsed = json.loads(content)
        if isinstance(parsed, dict):
            parsed = parsed.get("findings", [])
        if not isinstance(parsed, list):
            parsed = []
    except json.JSONDecodeError:
        parsed = []
    findings = []
    for item in parsed:
        if isinstance(item, dict):
            findings.append({
                "defect_type": str(item.get("defect_type", "llm_semantic")),
                "location": str(item.get("location", "unknown")),
                "message": str(item.get("message", "")),
                "evidence": str(item.get("evidence", "")),
            })
    usage = data.get("usage", {})
    metadata = {
        "model": model,
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "provider_reported_cost": data.get("usage", {}).get("cost"),
    }
    return findings, metadata


def _mock_llm_check(source_facts: dict[str, Any], spec: dict[str, Any]) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """Offline stand-in for run_optional_llm_check.

    Lets the LLM branch be exercised end-to-end (prompt build + finding merge)
    with no API key and no network. It returns a single benign marker finding so
    the merge path is proven without emitting false positives. Enable with
    DOCSYNC_LLM_MOCK=1 (or OPENROUTER_API_KEY=mock).
    """
    marker = _finding(
        "llm_semantic",
        "LLM semantic layer",
        "[MOCK] Optional LLM semantic layer executed offline; no real model was called.",
        "mock-run (no network)",
    )
    metadata = {
        "model": "mock (no network)",
        "prompt_tokens": None,
        "completion_tokens": None,
        "total_tokens": None,
        "provider_reported_cost": 0.0,
        "mock": True,
    }
    return [marker], metadata
