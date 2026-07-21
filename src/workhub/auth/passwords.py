import base64
import hashlib
import hmac
import os


class ScryptPasswordHasher:
    algorithm = "scrypt"
    n = 2**14
    r = 8
    p = 1
    salt_bytes = 16
    key_bytes = 32

    def hash(self, password: str) -> str:
        if len(password) < 12:
            raise ValueError("Administrator password must contain at least 12 characters")
        salt = os.urandom(self.salt_bytes)
        derived = hashlib.scrypt(
            password.encode(),
            salt=salt,
            n=self.n,
            r=self.r,
            p=self.p,
            dklen=self.key_bytes,
        )
        encoded_salt = base64.urlsafe_b64encode(salt).decode().rstrip("=")
        encoded_derived = base64.urlsafe_b64encode(derived).decode().rstrip("=")
        return (
            f"${self.algorithm}$n={self.n},r={self.r},p={self.p}${encoded_salt}${encoded_derived}"
        )

    def verify(self, password: str, encoded: str) -> bool:
        try:
            marker, algorithm, parameters, encoded_salt, encoded_derived = encoded.split("$")
            values = dict(item.split("=", 1) for item in parameters.split(","))
            n, r, p = int(values["n"]), int(values["r"]), int(values["p"])
            if marker or algorithm != self.algorithm or (n, r, p) != (self.n, self.r, self.p):
                return False
            salt = _decode(encoded_salt)
            expected = _decode(encoded_derived)
            actual = hashlib.scrypt(
                password.encode(), salt=salt, n=n, r=r, p=p, dklen=len(expected)
            )
            return hmac.compare_digest(actual, expected)
        except (KeyError, TypeError, ValueError):
            return False


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(f"{value}{'=' * (-len(value) % 4)}")
