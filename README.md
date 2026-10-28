# Garmin strength JSON uploader

Requires Python 3.12+. Uses unofficial `cyberjunky/python-garminconnect`.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Configure Garmin Connect credentials

The uploader uses the unofficial `garminconnect` library. For the first login,
set your Garmin Connect email and password as environment variables in the
current PowerShell session:

```powershell
$env:GARMIN_EMAIL = "your-garmin-email@example.com"
$env:GARMIN_PASSWORD = "your-garmin-password"
```

These variables last only for the current PowerShell session. Alternatively,
leave them unset: when an upload needs to log in, `uploader.py` asks for the
email and securely prompts for the password without displaying it.

On the first authenticated upload, Garmin may request an MFA code. After a
successful login, the library stores reusable session tokens in:

```text
~/.garminconnect/garmin_tokens.json
```

Later uploads try those stored tokens first, so the email, password, and MFA
code normally do not need to be entered again. If the session expires, the
uploader falls back to the environment variables or interactive prompts.

Do not place Garmin credentials in a JSON workout, Python source file, README,
or any file committed to version control. The generator and preview command do
not use Garmin credentials; authentication happens only after running the
uploader with `--upload`, typing `UPLOAD`, and confirming the operation.

## Create an editable JSON draft with OpenAI

Set an API key, then convert free-form text (the generator does not connect to
Garmin or upload anything):

```powershell
$env:OPENAI_API_KEY = "your-api-key"
python exercise_to_json.py --text "Pull day: pullups 3 sets of 5, 2x10 lat pulldowns, rest 90 seconds" --edit
```

For longer input, put the workout in a text file:

```powershell
python exercise_to_json.py --input exercises.txt --output workout.generated.json --edit
```

## Create an editable JSON draft with Sarvam AI

The OpenAI script above remains available. To use Sarvam instead, set its
separate API key and run the Sarvam generator:

```powershell
$env:SARVAM_API_KEY = "your-sarvam-api-key"
python exercise2json_sarvam.py --text "Pull day: pullups 3 sets of 5, 2x10 lat pulldowns, rest 90 seconds" --edit
```

Longer workouts can be read from a file:

```powershell
python exercise2json_sarvam.py --input exercises.txt --output workout.sarvam.generated.json --edit
```

The default Sarvam model is `sarvam-105b`. Override it with `--model` or the
`SARVAM_MODEL` environment variable.

The model must choose exercise names from `Garmin Exercise List.txt`, and the
script verifies every choice locally before saving. When sets or rest are not
specified, the defaults are 1 set and 60 seconds; change these with
`--default-sets` and `--default-rest`. Existing output files are protected; use
`--force` only when you intentionally want to replace one.

A single rest instruction at the end of a list (for example, `rest 90 seconds`)
is treated as shared by all exercises that do not specify another rest time.
Each generated exercise's `notes` field also preserves the original exercise
name entered by the user.

`--edit` opens Notepad on Windows (or `$EDITOR` when configured), waits for you
to finish editing, and validates the edited file. You can also omit `--edit` and
edit the JSON in any editor. Then preview and upload as two separate steps:

PowerShell:

```powershell
python uploader.py workout.generated.json
python uploader.py workout.generated.json --upload
```

Default is dry-run, producing `garmin_payload.json`. Upload requires `--upload` and typing `UPLOAD`.

- The repeat block contains both the exercise and the rest; rest executes on every iteration, including the final one.
- Exercise names must exactly match `Garmin Exercise List.txt`. Invalid names stop with close-match suggestions; the uploader never substitutes a different exercise.
- `cardio` is mapped as the actual warm-up exercise when the installed library supports it. The warm-up preserves a duration target of 600 seconds.
- Do not commit your Garmin token directory, password, or credentials.
- Live Garmin upload and watch rendering must be tested in your account; API and device behavior can change.
