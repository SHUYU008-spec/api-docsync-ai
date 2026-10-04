
"""Generate one-mutation OpenAPI variants and a ground-truth CSV.

This generator creates mutated OpenAPI specifications for evaluating
API documentation inconsistency detection.

IMPORTANT:
- Inspect generated cases before using them as experimental ground truth.
- Mutations are applied to OpenAPI specifications, not Python source code.
- Some mutation types may require manual verification against the source.
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

import pandas as pd
import yaml


METHODS = {"get", "post", "put", "patch", "delete"}

DEFECT_TYPES = [
    "type_mismatch",
    "required_field",
    "missing_field",
    "description_drift",
]


def load_spec(path: Path) -> dict:
    """Load an OpenAPI specification in JSON or YAML format."""
    text = path.read_text(encoding="utf-8")

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        data = yaml.safe_load(text)

    if not isinstance(data, dict):
        raise ValueError(f"OpenAPI specification must be a mapping: {path}")

    return data


def operations(spec: dict):
    """Yield (path, method, operation) tuples from an OpenAPI specification."""
    for path, path_item in spec.get("paths", {}).items():
        if not isinstance(path_item, dict):
            continue

        for method, operation in path_item.items():
            if (
                method.lower() in METHODS
                and isinstance(operation, dict)
            ):
                yield path, method.lower(), operation


def schema_candidates(spec: dict) -> list[tuple]:
    """Collect schema properties that can be used as mutation targets."""
    candidates = []

    # Component schemas
    components = spec.get("components", {})
    schemas = components.get("schemas", {}) if isinstance(components, dict) else {}

    for schema_name, schema in schemas.items():
        if not isinstance(schema, dict):
            continue

        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            continue

        for prop, definition in properties.items():
            if isinstance(definition, dict):
                location = (
                    f"components.schemas.{schema_name}"
                    f".properties.{prop}"
                )
                candidates.append(
                    (location, "component", schema_name, prop)
                )

    # Inline response schemas
    for path, method, operation in operations(spec):
        responses = operation.get("responses", {})

        if not isinstance(responses, dict):
            continue

        for status_code, response in responses.items():
            if not isinstance(response, dict):
                continue

            content = response.get("content", {})
            if not isinstance(content, dict):
                continue

            for media_type, media in content.items():
                if not isinstance(media, dict):
                    continue

                schema = media.get("schema", {})
                if not isinstance(schema, dict):
                    continue

                properties = schema.get("properties", {})
                if not isinstance(properties, dict):
                    continue

                for prop, definition in properties.items():
                    if isinstance(definition, dict):
                        location = (
                            f"{method.upper()} {path}"
                            f".responses.{status_code}"
                            f".content.{media_type}.schema"
                            f".properties.{prop}"
                        )
                        candidates.append(
                            (
                                location,
                                "response",
                                path,
                                method,
                                status_code,
                                media_type,
                                prop,
                            )
                        )

    return candidates


def get_target_properties(mutated: dict, candidate: tuple) -> dict:
    """Find the mutable properties dictionary for a candidate."""
    if candidate[1] == "component":
        schema_name = candidate[2]

        schema = (
            mutated.get("components", {})
            .get("schemas", {})
            .get(schema_name)
        )

        if not isinstance(schema, dict):
            raise ValueError(
                f"Component schema not found: {schema_name}"
            )

        properties = schema.get("properties")

    else:
        _, _, path, method, status_code, media_type, _ = candidate

        operation = mutated.get("paths", {}).get(path, {}).get(method)
        if not isinstance(operation, dict):
            raise ValueError(f"Operation not found: {method.upper()} {path}")

        response = operation.get("responses", {}).get(status_code)
        if not isinstance(response, dict):
            raise ValueError(f"Response not found: {status_code}")

        media = response.get("content", {}).get(media_type)
        if not isinstance(media, dict):
            raise ValueError(f"Media type not found: {media_type}")

        schema = media.get("schema", {})
        if not isinstance(schema, dict):
            raise ValueError("Inline response schema not found")

        properties = schema.get("properties")

    if not isinstance(properties, dict):
        raise ValueError(f"Properties not found for candidate: {candidate}")

    return properties


def get_parent_schema(mutated: dict, candidate: tuple) -> dict:
    """Find the schema object containing a candidate property."""
    if candidate[1] == "component":
        schema_name = candidate[2]
        schema = (
            mutated.get("components", {})
            .get("schemas", {})
            .get(schema_name)
        )

    else:
        _, _, path, method, status_code, media_type, _ = candidate

        operation = mutated.get("paths", {}).get(path, {}).get(method)
        response = operation.get("responses", {}).get(status_code)
        media = response.get("content", {}).get(media_type)
        schema = media.get("schema", {})

    if not isinstance(schema, dict):
        raise ValueError(f"Parent schema not found: {candidate}")

    return schema


def mutate_one(
    base: dict,
    index: int,
    candidates: list[tuple],
) -> tuple[dict, dict]:
    """Apply one mutation and return (mutated_spec, ground_truth_row)."""
    if not candidates:
        raise ValueError(
            "No schema properties available for mutation."
        )

    mutated = copy.deepcopy(base)
    candidate = candidates[index % len(candidates)]

    defect = DEFECT_TYPES[index % len(DEFECT_TYPES)]

    if candidate[1] == "component":
        location = candidate[0]
        prop = candidate[3]
    else:
        location = candidate[0]
        prop = candidate[6]

    properties = get_target_properties(mutated, candidate)
    parent_schema = get_parent_schema(mutated, candidate)

    if prop not in properties:
        raise ValueError(f"Mutation target property not found: {location}")

    # 1. Change a property's declared type.
    if defect == "type_mismatch":
        definition = properties[prop]
        old_type = definition.get("type", "string")

        alternatives = {
            "string": "integer",
            "integer": "string",
            "number": "string",
            "boolean": "string",
            "array": "string",
            "object": "string",
        }

        definition["type"] = alternatives.get(old_type, "string")

        mutation_note = (
            f"Changed the declared type of {location}"
        )

    # 2. Toggle the property's membership in the required list.
    elif defect == "required_field":
        required = parent_schema.setdefault("required", [])

        if not isinstance(required, list):
            raise ValueError(
                f"'required' must be a list in schema for {location}"
            )

        if prop in required:
            required.remove(prop)
            action = "Removed"
        else:
            required.append(prop)
            action = "Added"

        mutation_note = (
            f"{action} {prop} "
            f"{'from' if action == 'Removed' else 'to'} "
            f"the required list for {location}"
        )

    # 3. Remove a property from the schema.
    elif defect == "missing_field":
        properties.pop(prop)

        mutation_note = (
            f"Removed property {prop} from {location.rsplit('.', 1)[0]}"
        )

    # 4. Change an operation summary.
    else:
        available_operations = list(operations(mutated))

        if not available_operations:
            raise ValueError(
                "No operation available for description_drift mutation"
            )

        op_path, op_method, operation = available_operations[0]
        operation["summary"] = (
            "MUTATED summary: intentionally inconsistent with implementation."
        )

        location = f"{op_method.upper()} {op_path}"
        mutation_note = f"Changed the summary of {location}"

    gt = {
        "case_id": f"M{index + 1:03d}",
        "defect_type": defect,
        "location": location,
        "mutation_note": mutation_note,
    }

    # Always return both values, regardless of mutation type.
    return mutated, gt


def main():
    parser = argparse.ArgumentParser(
        description="Generate mutated OpenAPI specifications."
    )

    parser.add_argument(
        "--spec",
        required=True,
        help="Path to the original OpenAPI YAML or JSON file.",
    )

    parser.add_argument(
        "--out-dir",
        default="data/mutations",
        help="Directory in which to save mutations and ground truth.",
    )

    parser.add_argument(
        "--count",
        type=int,
        default=40,
        help="Number of mutated specifications to generate.",
    )

    args = parser.parse_args()

    if args.count <= 0:
        parser.error("--count must be greater than zero")

    spec_path = Path(args.spec)
    out_dir = Path(args.out_dir)

    if not spec_path.exists():
        parser.error(f"Specification file does not exist: {spec_path}")

    base = load_spec(spec_path)
    candidates = schema_candidates(base)

    if not candidates:
        parser.error(
            "No schema properties found. "
            "Check whether the OpenAPI file contains component schemas "
            "or inline response schemas with properties."
        )

    out_dir.mkdir(parents=True, exist_ok=True)

    ground_truth = []

    for index in range(args.count):
        mutated, gt = mutate_one(base, index, candidates)

        case_id = gt["case_id"]
        output_path = out_dir / f"{case_id}.json"

        output_path.write_text(
            json.dumps(mutated, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        ground_truth.append(gt)

    ground_truth_path = out_dir / "ground_truth.csv"

    pd.DataFrame(ground_truth).to_csv(
        ground_truth_path,
        index=False,
    )

    print(
        f"Wrote {args.count} mutated specs and ground_truth.csv "
        f"to {out_dir}"
    )


if __name__ == "__main__":
    main()
