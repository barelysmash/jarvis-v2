"""One-off interactive Google OAuth reauth. Run on a machine WITH a
browser (rosencrantz), then scp the resulting token.json to
guildenstern:/home/ocelia/jarvis-data/google/token.json.

Must request exactly the same single scope as google_auth.SCOPES —
scope drift between token and refresh request causes invalid_scope.
"""
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/calendar"]
BASE = Path(__file__).resolve().parents[1] / "config" / "google"

flow = InstalledAppFlow.from_client_secrets_file(
    str(BASE / "credentials.json"), SCOPES
)
creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")

token_path = BASE / "token.json"
token_path.write_text(creds.to_json())
print(f"Wrote {token_path}")
print(f"Has refresh_token: {bool(creds.refresh_token)}")