from workhub.auth import ScryptPasswordHasher


def test_scrypt_password_hash_is_salted_and_verifiable() -> None:
    hasher = ScryptPasswordHasher()
    first = hasher.hash("correct-horse-battery-staple")
    second = hasher.hash("correct-horse-battery-staple")

    assert first != second
    assert "correct-horse" not in first
    assert hasher.verify("correct-horse-battery-staple", first)
    assert not hasher.verify("wrong-password", first)
    assert not hasher.verify("correct-horse-battery-staple", "malformed")


def test_scrypt_password_hash_rejects_short_passwords() -> None:
    hasher = ScryptPasswordHasher()

    try:
        hasher.hash("too-short")
    except ValueError as exc:
        assert "at least 12" in str(exc)
    else:
        raise AssertionError("short administrator password was accepted")
