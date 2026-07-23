from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError


class PasswordHasher:
    minimum_length = 12

    def __init__(self) -> None:
        self._password_hash = PasswordHash.recommended()

    def hash(self, password: str) -> str:
        if len(password) < self.minimum_length:
            raise ValueError(
                f"Administrator password must contain at least {self.minimum_length} characters"
            )
        return self._password_hash.hash(password)

    def verify(self, password: str, encoded: str) -> bool:
        try:
            return self._password_hash.verify(password, encoded)
        except (TypeError, ValueError, UnknownHashError):
            return False
