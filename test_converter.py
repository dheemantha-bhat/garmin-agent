import tempfile
import unittest
from pathlib import Path

from exercise_to_json import (
    ExerciseDraft,
    WorkoutDraft,
    build_instructions,
    to_workout_spec,
)
from exercise2json_sarvam import parse_sarvam_content
from uploader import SpecError, validate, validate_catalog_exercises


class ConverterTests(unittest.TestCase):
    def setUp(self):
        self.catalog = ["Pull-up", "Lat Pull-down"]

    def test_shared_api_prompt_contains_rest_and_source_name_rules(self):
        prompt = build_instructions(self.catalog)
        self.assertIn("shared rest time", prompt)
        self.assertIn("source_name", prompt)
        self.assertIn("Pull-up", prompt)

    def test_draft_becomes_uploader_schema_with_defaults(self):
        draft = WorkoutDraft(
            workout_name="Pull day",
            exercises=[
                ExerciseDraft(
                    source_name="pullups",
                    exercise="Pull-up",
                    sets=None,
                    reps=5,
                    rest_seconds=None,
                    notes="",
                )
            ],
        )
        spec = to_workout_spec(draft, self.catalog, None, 1, 60)
        self.assertEqual(spec["steps"][0]["count"], 1)
        self.assertEqual(spec["steps"][0]["steps"][0]["exercise"], "Pull-up")
        self.assertEqual(
            spec["steps"][0]["steps"][0]["notes"], "Original exercise: pullups"
        )
        self.assertEqual(spec["steps"][0]["steps"][1]["duration_seconds"], 60)
        validate(spec)

    def test_one_stated_rest_is_shared_and_original_names_are_notes(self):
        draft = WorkoutDraft(
            workout_name="Pull day",
            exercises=[
                ExerciseDraft(
                    source_name="pullups",
                    exercise="Pull-up",
                    sets=3,
                    reps=5,
                    rest_seconds=None,
                    notes="strict",
                ),
                ExerciseDraft(
                    source_name="lat pulldowns",
                    exercise="Lat Pull-down",
                    sets=2,
                    reps=10,
                    rest_seconds=90,
                    notes="",
                ),
            ],
        )
        spec = to_workout_spec(draft, self.catalog, None, 1, 60)
        self.assertEqual(spec["steps"][0]["steps"][1]["duration_seconds"], 90)
        self.assertEqual(spec["steps"][1]["steps"][1]["duration_seconds"], 90)
        self.assertEqual(
            spec["steps"][0]["steps"][0]["notes"],
            "Original exercise: pullups. strict",
        )
        self.assertEqual(
            spec["steps"][1]["steps"][0]["notes"],
            "Original exercise: lat pulldowns",
        )

    def test_model_name_outside_catalog_is_rejected(self):
        draft = WorkoutDraft(
            workout_name=None,
            exercises=[
                ExerciseDraft(
                    source_name="pullups",
                    exercise="Pullups",
                    sets=3,
                    reps=5,
                    rest_seconds=60,
                    notes="",
                )
            ],
        )
        with self.assertRaisesRegex(ValueError, "outside Garmin"):
            to_workout_spec(draft, self.catalog, None, 1, 60)

    def test_user_edit_outside_catalog_is_rejected_with_suggestion(self):
        spec = {
            "name": "Test",
            "sport": "strength",
            "steps": [
                {
                    "type": "exercise",
                    "exercise": "Pullup",
                    "target_type": "reps",
                    "target": 5,
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            catalog_path = Path(directory) / "catalog.txt"
            catalog_path.write_text("Pull-up\nLat Pull-down\n", encoding="utf-8")
            with self.assertRaisesRegex(SpecError, "Pull-up"):
                validate_catalog_exercises(validate(spec), catalog_path)

    def test_sarvam_json_is_parsed_into_shared_draft(self):
        draft = parse_sarvam_content(
            '{"workout_name":"Pull day","exercises":[{'
            '"source_name":"pullups","exercise":"Pull-up","sets":3,'
            '"reps":5,"rest_seconds":60,"notes":""}]}'
        )
        self.assertEqual(draft.exercises[0].exercise, "Pull-up")
        self.assertEqual(draft.exercises[0].sets, 3)


if __name__ == "__main__":
    unittest.main()
