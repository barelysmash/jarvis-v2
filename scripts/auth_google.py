"""One-time script to authorize Google Calendar access."""

import os
import sys

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

from tools.integrations.google_auth import GoogleAuth


def main():
    print("Starting Google OAuth flow...")
    print("Your browser will open. Sign in and grant calendar access.\n")

    auth = GoogleAuth()
    creds = auth.get_credentials()

    print("Authentication successful")
    print(f"Token saved to: {auth.token_path}")
    print(f"Scopes granted: {creds.scopes}")


if __name__ == "__main__":
    main()
