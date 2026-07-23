from workhub.auth import PasswordHasher


def test_argon2_password_hash_is_salted_and_verifiable() -> None:
    hasher = PasswordHasher()
    first = hasher.hash("correct-horse-battery-staple")
    second = hasher.hash("correct-horse-battery-staple")

    assert first.startswith("$argon2")
    assert first != second
    assert hasher.verify("correct-horse-battery-staple", first)
    assert not hasher.verify("wrong-password", first)
    assert not hasher.verify("correct-horse-battery-staple", "malformed")


def test_argon2_password_hash_rejects_short_passwords() -> None:
    hasher = PasswordHasher()

    try:
        hasher.hash("too-short")
    except ValueError as exc:
        assert "at least 12" in str(exc)
    else:
        raise AssertionError("short administrator password was accepted")
