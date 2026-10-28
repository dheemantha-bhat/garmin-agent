"""Convert a free-form strength workout into reviewable Garmin workout JSON.

This program only creates a local JSON draft. It never connects to Garmin and
never uploads anything. Review the generated file, then pass it to uploader.py.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

from pydantic import BaseModel, ConfigDict


ROOT = Path(__file__).resolve().parent
DEFAULT_CATALOG = ROOT / "Garmin Exercise List.txt"
CLI_DESCRIPTION = (
    "Convert an exercise list to editable Garmin workout JSON. "
    "Pay attention to rest times and keep original exercise names in notes."
)


class ExerciseDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_name: str
    exercise: str
    sets: int | None
    reps: int
    rest_seconds: int | None
    notes: str


class WorkoutDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workout_name: str | None
    exercises: list[ExerciseDraft]


def load_catalog(path: Path) -> list[str]:
    if not path.is_file():
        raise ValueError(f"Garmin exercise catalog not found: {path}")
    names = [line.strip() for line in path.read_text(encoding="utf-8-sig").splitlines()]
    # The source catalog can contain repeated display names. Preserve its order
    # while presenting each allowed name to the model only once.
    names = list(dict.fromkeys(name for name in names if name))
    if not names:
        raise ValueError(f"Garmin exercise catalog is empty: {path}")
    return names


def read_description(args: argparse.Namespace) -> str:
    if args.input:
        return Path(args.input).read_text(encoding="utf-8-sig").strip()
    if args.text:
        return args.text.strip()
    if not sys.stdin.isatty():
        return sys.stdin.read().strip()
    return input("Exercise list (include sets/reps if applicable): ").strip()


def build_instructions(catalog: list[str]) -> str:
    catalog_text = "\n".join(catalog)
    return f"""You convert a user's strength-workout text into structured data.

Rules:
- Preserve the exercise order.
- `exercise` MUST be copied exactly, including spelling and punctuation, from the
  Garmin catalog below. Map informal user wording to the closest equivalent.
- Put the user's original exercise wording in `source_name`.
- Extract reps as a positive integer. Do not invent reps.
- Extract sets when stated; otherwise use null.
- Extract rest time in seconds when stated; otherwise use null.
- A rest time written once at the end of an exercise list, without being tied
  to one named exercise, applies to every exercise in that list. Copy that
  shared rest time into each exercise's `rest_seconds` field.
- Use an empty string for notes when no note was supplied.
- Use null for workout_name when the user supplied no workout name.
- Do not add exercises that the user did not request.

GARMIN EXERCISE CATALOG (the only allowed values for `exercise`):
{catalog_text}
"""


def generate_draft(description: str, catalog: list[str], model: str) -> WorkoutDraft:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError(
            "The OpenAI package is not installed. Run: pip install -r requirements.txt"
        ) from exc

    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("Set OPENAI_API_KEY before running this command.")

    response = OpenAI().responses.parse(
        model=model,
        input=[
            {"role": "system", "content": build_instructions(catalog)},
            {"role": "user", "content": description},
        ],
        text_format=WorkoutDraft,
    )
    if response.output_parsed is None:
        raise RuntimeError("The model did not return a workout. Try clearer input.")
    return response.output_parsed


def to_workout_spec(
    draft: WorkoutDraft,
    catalog: list[str],
    requested_name: str | None,
    default_sets: int,
    default_rest: int,
) -> dict:
    if not draft.exercises:
        raise ValueError("No exercises were found in the input.")

    allowed = set(catalog)
    invalid = sorted({item.exercise for item in draft.exercises if item.exercise not in allowed})
    if invalid:
        raise ValueError(
            "The model returned names outside Garmin Exercise List.txt: "
            + ", ".join(repr(name) for name in invalid)
            + ". Re-run the command or choose an exact catalog name."
        )

    steps = []
    stated_rests = {
        item.rest_seconds for item in draft.exercises if item.rest_seconds is not None
    }
    # Models sometimes attach a trailing shared rest instruction only to the
    # final exercise. If there is exactly one stated rest value, apply it to
    # exercises where the model left rest unspecified.
    shared_rest = next(iter(stated_rests)) if len(stated_rests) == 1 else None
    for index, item in enumerate(draft.exercises, start=1):
        sets = default_sets if item.sets is None else item.sets
        rest = item.rest_seconds
        if rest is None:
            rest = shared_rest if shared_rest is not None else default_rest
        if isinstance(sets, bool) or sets <= 0:
            raise ValueError(f"Exercise {index}: sets must be a positive integer")
        if isinstance(item.reps, bool) or item.reps <= 0:
            raise ValueError(f"Exercise {index}: reps must be a positive integer")
        if isinstance(rest, bool) or rest <= 0:
            raise ValueError(f"Exercise {index}: rest must be a positive integer")

        source_note = f"Original exercise: {item.source_name.strip()}"
        notes = source_note if not item.notes.strip() else f"{source_note}. {item.notes.strip()}"
        steps.append(
            {
                "type": "repeat",
                "count": sets,
                "steps": [
                    {
                        "type": "exercise",
                        "notes": notes,
                        "exercise": item.exercise,
                        "target_type": "reps",
                        "target": item.reps,
                    },
                    {"type": "rest", "duration_seconds": rest},
                ],
            }
        )

    name = (requested_name or draft.workout_name or "Generated Strength Workout").strip()
    if not name:
        name = "Generated Strength Workout"
    return {"name": name, "sport": "strength", "steps": steps}


def open_editor(path: Path) -> None:
    editor = os.getenv("EDITOR")
    if editor:
        command = [*shlex.split(editor), str(path)]
    elif os.name == "nt":
        command = ["notepad.exe", str(path)]
    elif sys.platform == "darwin":
        command = ["open", "-W", str(path)]
    else:
        command = ["nano", str(path)]
    subprocess.run(command, check=True)


def validate_saved_file(path: Path, catalog_path: Path) -> None:
    from uploader import validate, validate_catalog_exercises

    spec = json.loads(path.read_text(encoding="utf-8"))
    validate(spec)
    validate_catalog_exercises(spec, catalog_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=CLI_DESCRIPTION + " Provider: OpenAI."
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--text", help="Workout text, for example '3x10 bench press, rest 90s'")
    source.add_argument("--input", help="Read the workout description from a text file")
    parser.add_argument("--name", help="Override the workout name")
    parser.add_argument("--output", default="workout.generated.json", help="Draft JSON path")
    parser.add_argument("--force", action="store_true", help="Overwrite an existing output file")
    parser.add_argument("--catalog", default=str(DEFAULT_CATALOG), help="Garmin exercise list")
    parser.add_argument("--model", default=os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
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

    output.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
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




