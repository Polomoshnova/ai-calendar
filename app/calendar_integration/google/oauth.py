from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx

from app.calendar_integration.errors import (
    CalendarAuthorizationError,
    CalendarProviderError,
    CalendarReconnectRequiredError,
    CalendarUnavailableError,
)

AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
REVOCATION_ENDPOINT = "https://oauth2.googleapis.com/revoke"


@dataclass(frozen=True)
class GoogleOAuthConfig:
    client_id: str
    client_secret: str
    redirect_uri: str
    scopes: tuple[str, ...]


@dataclass(frozen=True)
class GoogleTokenSet:
    access_token: str
    refresh_token: str | None
    expires_at: datetime | None
    scopes: tuple[str, ...]


class GoogleOAuthClient:
    def __init__(self, client: httpx.AsyncClient, config: GoogleOAuthConfig) -> None:
        self._client = client
        self._config = config

    def authorization_url(self, state: str, *, prompt_consent: bool = False) -> str:
        parameters = {
            "response_type": "code",
            "client_id": self._config.client_id,
            "redirect_uri": self._config.redirect_uri,
            "scope": " ".join(self._config.scopes),
            "state": state,
            "access_type": "offline",
            "include_granted_scopes": "true",
        }
        if prompt_consent:
            parameters["prompt"] = "consent select_account"
        return f"{AUTHORIZATION_ENDPOINT}?{urlencode(parameters)}"

    async def exchange_code(self, code: str) -> GoogleTokenSet:
        return await self._token_request(
            {
                "code": code,
                "client_id": self._config.client_id,
                "client_secret": self._config.client_secret,
                "redirect_uri": self._config.redirect_uri,
                "grant_type": "authorization_code",
            }
        )

    async def refresh_access_token(self, refresh_token: str) -> GoogleTokenSet:
        return await self._token_request(
            {
                "refresh_token": refresh_token,
                "client_id": self._config.client_id,
                "client_secret": self._config.client_secret,
                "grant_type": "refresh_token",
            }
        )

    async def revoke_token(self, token: str) -> None:
        try:
            response = await self._client.post(
                REVOCATION_ENDPOINT, data={"token": token}
            )
        except httpx.RequestError as exc:
            raise CalendarUnavailableError(
                "Google token revocation is temporarily unavailable"
            ) from exc
        if response.status_code in {200, 400}:
            return
        if response.status_code >= 500:
            raise CalendarUnavailableError(
                "Google token revocation is temporarily unavailable"
            )
        raise CalendarProviderError("Google token revocation failed")

    async def _token_request(self, data: dict[str, str]) -> GoogleTokenSet:
        try:
            response = await self._client.post(TOKEN_ENDPOINT, data=data)
        except httpx.RequestError as exc:
            raise CalendarUnavailableError(
                "Google OAuth is temporarily unavailable"
            ) from exc
        if response.status_code != 200:
            error = _safe_error_code(response)
            if error == "invalid_grant":
                raise CalendarReconnectRequiredError(
                    "Google Calendar access must be reauthorized."
                )
            if response.status_code >= 500:
                raise CalendarUnavailableError(
                    "Google OAuth is temporarily unavailable"
                )
            raise CalendarAuthorizationError("Google OAuth token request failed")
        try:
            payload = response.json()
            access_token = str(payload["access_token"])
            expires_in = int(payload.get("expires_in", 0))
        except (ValueError, KeyError, TypeError) as exc:
            raise CalendarProviderError(
                "Google OAuth returned a malformed response"
            ) from exc
        scopes = tuple(str(payload.get("scope", "")).split())
        return GoogleTokenSet(
            access_token=access_token,
            refresh_token=(
                str(payload["refresh_token"]) if payload.get("refresh_token") else None
            ),
            expires_at=(
                datetime.now(UTC) + timedelta(seconds=expires_in)
                if expires_in > 0
                else None
            ),
            scopes=scopes or self._config.scopes,
        )


def _safe_error_code(response: httpx.Response) -> str | None:
    try:
        payload = response.json()
    except ValueError:
        return None
    error = payload.get("error") if isinstance(payload, dict) else None
    return str(error) if error else None
