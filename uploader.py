"""Translate a small strength-workout JSON schema to Garmin Connect workouts.

Preview by default. Use --upload and type UPLOAD for account mutation.
"""
import argparse
import difflib
import json
import os
from pathlib import Path
from getpass import getpass


class SpecError(ValueError):
    pass


def positive_number(value, location, whole=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise SpecError(f"{location}: expected a positive number")
    if whole and (not isinstance(value, int) or isinstance(value, bool)):
        raise SpecError(f"{location}: expected a positive integer")
    return value


def validate(spec):
    if not isinstance(spec, dict):
        raise SpecError("workout must be an object")
    if not isinstance(spec.get("name"), str) or not spec["name"].strip():
        raise SpecError("name is required")
    if spec.get("sport") != "strength":
        raise SpecError("Only sport=strength is supported")

    def visit(steps, place, depth=0):
        if not isinstance(steps, list) or not steps:
            raise SpecError(f"{place}: nonempty steps list required")
        if depth > 4:
            raise SpecError(f"{place}: nesting too deep")
        for i, item in enumerate(steps):
            loc = f"{place}[{i}]"
            if not isinstance(item, dict):
                raise SpecError(f"{loc}: step must be an object")
            kind = item.get("type")
            if kind == "repeat":
                positive_number(item.get("count"), loc + ".count", whole=True)
                visit(item.get("steps"), loc + ".steps", depth + 1)
            elif kind == "rest":
                positive_number(item.get("duration_seconds"), loc + ".duration_seconds")
            elif kind in {"warmup", "exercise"}:
                if not isinstance(item.get("exercise"), str) or not item["exercise"].strip():
                    raise SpecError(f"{loc}: exercise name is required")
                if item.get("target_type") not in ("time", "reps"):
                    raise SpecError(f"{loc}: target_type must be time or reps")
                positive_number(item.get("target"), loc + ".target", whole=item["target_type"] == "reps")
                if "notes" in item and not isinstance(item["notes"], str):
                    raise SpecError(f"{loc}.notes must be a string")
                if "Notes" in item and not isinstance(item["Notes"], str):
                    raise SpecError(f"{loc}.Notes must be a string")
            else:
                raise SpecError(f"{loc}: unsupported type {kind!r}")
    visit(spec.get("steps"), "steps")
    return spec


def exercise_names(spec):
    """Yield every exercise name from a validated workout specification."""
    def visit(steps):
        for item in steps:
            if item["type"] == "repeat":
                yield from visit(item["steps"])
            elif item["type"] in {"warmup", "exercise"}:
                yield item["exercise"]
    yield from visit(spec["steps"])


def load_exercise_catalog(path=None):
    catalog_path = Path(path or Path(__file__).with_name("Garmin Exercise List.txt"))
    if not catalog_path.is_file():
        raise SpecError(f"Garmin exercise catalog not found: {catalog_path}")
    names = [line.strip() for line in catalog_path.read_text(encoding="utf-8-sig").splitlines()]
    return list(dict.fromkeys(name for name in names if name))


def validate_catalog_exercises(spec, catalog_path=None):
    catalog = load_exercise_catalog(catalog_path)
    allowed = set(catalog)
    invalid = sorted({name for name in exercise_names(spec) if name not in allowed})
    if invalid:
        details = []
        for name in invalid:
            matches = difflib.get_close_matches(name, catalog, n=3, cutoff=0.55)
            suffix = f" (close matches: {', '.join(matches)})" if matches else ""
            details.append(repr(name) + suffix)
        raise SpecError(
            "Exercise names must exactly match Garmin Exercise List.txt: " + "; ".join(details)
        )
    return spec


def preview(spec):
    print(f"\n{spec['name']} (strength)")
    def show(steps, indent=0):
        for item in steps:
            prefix = "  " * indent
            kind = item["type"]
            if kind == "repeat":
                print(f"{prefix}REPEAT x{item['count']}: (rest repeats too, including final iteration)")
                show(item["steps"], indent + 1)
            elif kind == "rest":
                print(f"{prefix}REST {item['duration_seconds']} seconds")
            else:
                unit = "seconds" if item["target_type"] == "time" else "reps"
                print(f"{prefix}{kind.upper()} {item['exercise']}: {item['target']} {unit}")
    show(spec["steps"])


def resolve_exercise(name, warnings):
    from garminconnect import exercises
    result = exercises.resolve(name)
    if result is None:
        raise SpecError(
            f"{name!r} is in Garmin Exercise List.txt but is not supported by the "
            "installed garminconnect exercise catalog. Update garminconnect or choose "
            "another exact Garmin exercise name."
        )
    return result


def build(spec):
    from garminconnect.workout import (
        StrengthWorkout, WorkoutSegment, create_strength_exercise_step,
        create_strength_timed_step, create_strength_rest_step, create_repeat_group,
    )
    warnings = []
    next_order = 0

    def make_steps(items):
        nonlocal next_order
        built = []
        for item in items:
            next_order += 1
            order = next_order
            kind = item["type"]
            if kind == "repeat":
                children = make_steps(item["steps"])
                built.append(create_repeat_group(item["count"], children, order))
            elif kind == "rest":
                built.append(create_strength_rest_step(item["duration_seconds"], order))
            else:
                pair = resolve_exercise(item["exercise"], warnings)
                notes = (item.get("notes") or item.get("Notes") or "").strip()
                args = dict(category=pair["category"], step_order=order,
                            exercise_name=pair["exercise"], description=notes or None)
                if item["target_type"] == "reps":
                    step = create_strength_exercise_step(reps=item["target"], **args)
                else:
                    step = create_strength_timed_step(duration_seconds=item["target"], **args)
                if kind == "warmup":
                    # Preserve the exercise mapping and timed target while making this
                    # a Garmin warm-up step rather than a normal interval.
                    step.stepType = {"stepTypeId": 1, "stepTypeKey": "warmup", "displayOrder": 1}
                built.append(step)
        return built

    steps = make_steps(spec["steps"])
    workout = StrengthWorkout(
        workoutName=spec["name"], estimatedDurationInSecs=0,
        workoutSegments=[WorkoutSegment(
            segmentOrder=1,
            sportType={"sportTypeId": 5, "sportTypeKey": "strength_training"},
            workoutSteps=steps,
        )],
    )
    return workout, warnings


def connect():
    from garminconnect import Garmin
    token_dir = os.path.expanduser("~/.garminconnect")
    if Path(token_dir, "garmin_tokens.json").exists():
        client = Garmin()
        try:
            client.login(token_dir)
            return client
        except Exception as exc:
            print(f"Stored session could not be reused: {exc}")
    email = os.getenv("GARMIN_EMAIL") or input("Garmin email: ").strip()
    pwd = os.getenv("GARMIN_PASSWORD") or getpass("Garmin password: ")
    client = Garmin(email, pwd, prompt_mfa=lambda: input("Garmin MFA code: ").strip())
    client.login(token_dir)
    return client


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("file", nargs="?", default="workout.json")
    parser.add_argument("--upload", action="store_true", help="Upload after explicit confirmation")
    parser.add_argument("--out", default="garmin_payload.json", help="Generated Garmin payload")
    args = parser.parse_args()
    spec = validate(json.loads(Path(args.file).read_text(encoding="utf-8")))
    validate_catalog_exercises(spec)
    preview(spec)
    workout, warnings = build(spec)
    for warning in warnings:
        print("WARNING:", warning)
    Path(args.out).write_text(json.dumps(workout.to_dict(), indent=2), encoding="utf-8")
    print(f"\nGarmin payload saved: {args.out}")
    if not args.upload:
        print("Preview only. No workout uploaded.")
        return
    if input("Type UPLOAD to create a workout in Garmin Connect: ").strip() != "UPLOAD":
        print("Cancelled.")
        return
    client = connect()
    result = client.upload_strength_workout(workout)
    print("Upload response:", json.dumps(result, indent=2, default=str))
    workout_id = result.get("workoutId") if isinstance(result, dict) else None
    if workout_id:
        saved = client.get_workout_by_id(workout_id)
        print("Verified saved workout:", saved.get("workoutName", workout_id))
    else:
        print("No workoutId returned; check Garmin Connect manually before retrying.")


if __name__ == "__main__":
    try:
        main()
    except (SpecError, ValueError, FileNotFoundError) as exc:
        raise SystemExit(f"ERROR: {exc}") from exc
