"""Convert free-form exercises to editable Garmin JSON using Sarvam AI.

This is the Sarvam counterpart to exercise_to_json.py. It creates a local
draft only; Garmin upload remains a separate, explicitly confirmed operation.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from pydantic import ValidationError

from exercise_to_json import (
    CLI_DESCRIPTION,
    DEFAULT_CATALOG,
    WorkoutDraft,
    build_instructions,
    load_catalog,
    open_editor,
    read_description,
    to_workout_spec,
    validate_saved_file,
)


# Kept explicit instead of relying on SDK-specific schema generation so the
# payload matches Sarvam's documented strict JSON Schema format.
WORKOUT_DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "workout_name": {"type": ["string", "null"]},
        "exercises": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source_name": {"type": "string"},
                    "exercise": {"type": "string"},
                    "sets": {"type": ["integer", "null"]},
                    "reps": {"type": "integer"},
                    "rest_seconds": {"type": ["integer", "null"]},
                    "notes": {"type": "string"},
                },
                "required": [
                    "source_name",
                    "exercise",
                    "sets",
                    "reps",
                    "rest_seconds",
                    "notes",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["workout_name", "exercises"],
    "additionalProperties": False,
}


def parse_sarvam_content(content: str | None) -> WorkoutDraft:
    if not content:
        raise RuntimeError("Sarvam returned an empty workout response.")
    try:
        return WorkoutDraft.model_validate_json(content)
    except ValidationError as exc:
        raise RuntimeError(f"Sarvam returned invalid workout JSON: {exc}") from exc


def generate_draft(description: str, catalog: list[str], model: str) -> WorkoutDraft:
    try:
        from sarvamai import SarvamAI
    except ImportError as exc:
        raise RuntimeError(
            "The Sarvam SDK is not installed. Run: pip install -r requirements.txt"
        ) from exc

    api_key = os.getenv("SARVAM_API_KEY")
    if not api_key:
        raise RuntimeError("Set SARVAM_API_KEY before running this command.")

    client = SarvamAI(api_subscription_key=api_key)
    response = client.chat.completions(
        model=model,
        messages=[
            {"role": "system", "content": build_instructions(catalog)},
            {"role": "user", "content": description},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "garmin_workout_draft",
                "description": "Exercises extracted and mapped to Garmin names",
                "strict": True,
                "schema": WORKOUT_DRAFT_SCHEMA,
            },
        },
        reasoning_effort=None,
        max_tokens=4096,
    )
    if not response.choices:
        raise RuntimeError("Sarvam returned no completion choices.")
    choice = response.choices[0]
    if choice.finish_reason == "length":
        raise RuntimeError("Sarvam's response was truncated; try a shorter exercise list.")
    return parse_sarvam_content(choice.message.content)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=CLI_DESCRIPTION + " Provider: Sarvam AI."
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--text", help="Workout text, for example '3x10 bench press, rest 90s'")
    source.add_argument("--input", help="Read the workout description from a text file")
    parser.add_argument("--name", help="Override the workout name")
    parser.add_argument(
        "--output", default="workout.sarvam.generated.json", help="Draft JSON path"
    )
    parser.add_argument("--force", action="store_true", help="Overwrite an existing output file")
    parser.add_argument("--catalog", default=str(DEFAULT_CATALOG), help="Garmin exercise list")
    parser.add_argument("--model", default=os.getenv("SARVAM_MODEL", "sarvam-105b"))
    parser.add_argument("--default-sets", type=int, default=1, help="Used when sets are omitted")
    parser.add_argument("--default-rest", type=int, default=60, help="Used when rest is omitted")
    parser.add_argument(
        "--edit",
        action="store_true",
        help="Open the generated JSON in $EDITOR (Notepad on Windows), then validate it",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.default_sets <= 0 or args.default_rest <= 0:
        raise ValueError("--default-sets and --default-rest must be positive integers")

    description = read_description(args)
    if not description:
        raise ValueError("The exercise list is empty.")

    catalog_path = Path(args.catalog).resolve()
    catalog = load_catalog(catalog_path)
    output = Path(args.output).resolve()
    if output.exists() and not args.force:
        raise ValueError(f"Output already exists: {output} (use --force to replace it)")

    draft = generate_draft(description, catalog, args.model)
    spec = to_workout_spec(
        draft, catalog, args.name, args.default_sets, args.default_rest
    )
    output.write_text(
        json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"Draft saved: {output}")
    print("No Garmin upload was performed.")

    if args.edit:
        open_editor(output)
        validate_saved_file(output, catalog_path)
        print("Edited JSON is valid and all exercise names match the Garmin catalog.")

    print(f'Review it, then preview with: python uploader.py "{output}"')
    print(f'When satisfied, upload with: python uploader.py "{output}" --upload')


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"ERROR: {exc}") from exc
