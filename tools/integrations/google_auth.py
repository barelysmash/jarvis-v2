"""Google OAuth: handles authentication and token refresh."""

from pathlib import Path
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

# /calendar covers /calendar.events; requesting both during refresh
# triggered invalid_scope responses from Google when the token was
# granted /calendar only (which is what the rosencrantz reauth flow
# requests). One scope here, one scope on the token, refresh works.
SCOPES = [
    "https://www.googleapis.com/auth/calendar",
]


class GoogleAuth:
    """Handles Google OAuth with persistent token storage and refresh."""

    def __init__(
        self,
        credentials_path: str = "config/google/credentials.json",
        token_path: str = "config/google/token.json",
    ):
        self.credentials_path = Path(credentials_path)
        self.token_path = Path(token_path)
        self._creds: Optional[Credentials] = None

    def get_credentials(self) -> Credentials:
        """Returns valid credentials, refreshing if needed.

        Headless OAuth is never attempted automatically — guildenstern
        has no browser, so flow.run_local_server() can only crash. When
        the saved token can't be refreshed, raise a clear error telling
        the operator to run the reauth flow on a host with a browser
        and scp token.json here.
        """
        if self._creds and self._creds.valid:
            return self._creds

        if not self.token_path.exists():
            raise RuntimeError(
                f"No Google token at {self.token_path}. "
                "Run the reauth flow on a host with a browser, "
                "then scp token.json to that path."
            )

        # Load WITHOUT passing SCOPES — keeping the token file's own
        # scope list avoids the refresh mismatch documented above.
        self._creds = Credentials.from_authorized_user_file(str(self.token_path))

        if self._creds.valid:
            return self._creds

        if not self._creds.refresh_token:
            raise RuntimeError(
                f"Token at {self.token_path} has no refresh_token. "
                "Run reauth and scp the fresh token.json here."
            )

        try:
            self._creds.refresh(Request())
        except Exception as exc:
            raise RuntimeError(
                f"Google token refresh failed: {exc}. "
                "Likely causes: refresh_token revoked, OAuth app in test "
                "mode hit 7-day expiry, or scope drift. "
                "Run reauth and scp the fresh token.json here."
            ) from exc

        self._save_token()
        return self._creds

    def _save_token(self):
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.token_path, "w") as f:
            f.write(self._creds.to_json())

    def revoke(self):
        """Clear stored credentials."""
        if self.token_path.exists():
            self.token_path.unlink()
        self._creds = None
