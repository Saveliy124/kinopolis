from __future__ import annotations

import hashlib
import hmac
import re
import secrets


EMAIL_RE = re.compile(r"^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,63}$", re.IGNORECASE)
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
PBKDF2_ITERATIONS = 310_000


def normalize_email(value: str) -> str:
    return value.strip().lower()


def validate_email(value: str) -> bool:
    return len(value) <= 254 and EMAIL_RE.fullmatch(value) is not None


def validate_password(value: str) -> list[str]:
    errors: list[str] = []
    if len(value) < 8:
        errors.append("Пароль должен содержать не менее 8 символов")
    if len(value) > 128:
        errors.append("Пароль должен содержать не более 128 символов")
    if not re.search(r"[a-zа-яё]", value):
        errors.append("Добавьте строчную букву")
    if not re.search(r"[A-ZА-ЯЁ]", value):
        errors.append("Добавьте заглавную букву")
    if not re.search(r"\d", value):
        errors.append("Добавьте цифру")
    return errors


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    scrypt = getattr(hashlib, "scrypt", None)
    if scrypt is not None:
        digest = scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=SCRYPT_N,
            r=SCRYPT_R,
            p=SCRYPT_P,
            dklen=32,
        )
        return (
            f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}$"
            f"{salt.hex()}${digest.hex()}"
        )

    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PBKDF2_ITERATIONS,
        dklen=32,
    )
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        parts = encoded.split("$")
        algorithm = parts[0]
        if algorithm == "scrypt" and len(parts) == 6:
            scrypt = getattr(hashlib, "scrypt", None)
            if scrypt is None:
                return False
            _, n, r, p, salt_hex, digest_hex = parts
            digest = scrypt(
                password.encode("utf-8"),
                salt=bytes.fromhex(salt_hex),
                n=int(n),
                r=int(r),
                p=int(p),
                dklen=len(bytes.fromhex(digest_hex)),
            )
        elif algorithm == "pbkdf2_sha256" and len(parts) == 4:
            _, iterations, salt_hex, digest_hex = parts
            digest = hashlib.pbkdf2_hmac(
                "sha256",
                password.encode("utf-8"),
                bytes.fromhex(salt_hex),
                int(iterations),
                dklen=len(bytes.fromhex(digest_hex)),
            )
        else:
            return False
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (AttributeError, TypeError, ValueError):
        return False


def new_token(size: int = 32) -> str:
    return secrets.token_urlsafe(size)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
