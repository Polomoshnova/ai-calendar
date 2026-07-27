from cryptography.fernet import Fernet, InvalidToken

from app.calendar_integration.errors import CalendarConfigurationError


class FernetTokenCipher:
    def __init__(self, key: str) -> None:
        try:
            self._fernet = Fernet(key.encode())
        except (ValueError, TypeError) as exc:
            raise CalendarConfigurationError(
                "CALENDAR_TOKEN_ENCRYPTION_KEY is not a valid Fernet key"
            ) from exc

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode()).decode()
        except InvalidToken as exc:
            raise CalendarConfigurationError(
                "Stored calendar token cannot be decrypted"
            ) from exc
