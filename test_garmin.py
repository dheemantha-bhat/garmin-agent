
import os
from getpass import getpass

TOKEN_DIR = os.path.expanduser("~/.garminconnect")


def main():
    from garminconnect import Garmin

    email = os.getenv("GARMIN_EMAIL") or input("Garmin email: ")
    password = getpass("Garmin password: ")

    client = Garmin(
        email,
        password,
        prompt_mfa=lambda: input("MFA code: ")
    )

    client.login(TOKEN_DIR)

    print("Connected successfully!")
    print("Workout counts:")
    print(client.get_workout_count())


if __name__ == "__main__":
    main()

