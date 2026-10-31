"""
Password hashing + JWT creation/verification.

The JWT payload carries the user's role ("sub" = user id, "role" = student
or librarian). dependencies.py reads that role back out on every request
to decide whether the endpoint is allowed to run.
"""
import os
import secrets
from datetime import datetime, timedelta
from typing import Optional

import bcrypt
from jose import jwt

from config import ACCESS_TOKEN_EXPIRE_MINUTES

# In production, load this from an environment variable / .env file -
# never commit a real secret to source control.
SECRET_KEY = os.environ.get("IPEEK_SECRET_KEY", "dev-secret-change-me")
ALGORITHM = "HS256"

# NOTE: uses bcrypt directly, NOT passlib. passlib==1.7.4's internal
# self-test is incompatible with bcrypt>=4.x's stricter 72-byte handling
# and crashes on every hash attempt. Confirmed via live testing - do not
# reintroduce passlib. See README.md section 2 & 8.


def hash_password(password: str) -> str:
    pw_bytes = password.encode("utf-8")[:72]  # bcrypt's hard input limit
    hashed = bcrypt.hashpw(pw_bytes, bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    pw_bytes = plain_password.encode("utf-8")[:72]
    return bcrypt.checkpw(pw_bytes, hashed_password.encode("utf-8"))


# ── Shared password generator (OTP self-registration + admin create-user) ──
PASSWORD_LENGTH = 12
# Look-alike characters (0 O o 1 l I) are excluded so the generated
# password stays readable when delivered by email.
_PW_LOWER = "abcdefghijkmnpqrstuvwxyz"   # no o, no l
_PW_UPPER = "ABCDEFGHJKLMNPQRSTUVWXYZ"   # no I, no O
_PW_DIGITS = "23456789"                  # no 0, no 1
_PW_POOL = _PW_LOWER + _PW_UPPER + _PW_DIGITS


def generate_password(length: int = PASSWORD_LENGTH) -> str:
    """Cryptographically random mixed-character password (secrets, never
    random). Guarantees at least one lowercase, one uppercase and one
    digit, then shuffles with a CSPRNG-backed Fisher-Yates so the
    required characters are never in predictable positions. No symbols —
    kept easy to type from an email."""
    if length < 3:
        raise ValueError("Password length must be at least 3.")
    chars = [
        secrets.choice(_PW_LOWER),
        secrets.choice(_PW_UPPER),
        secrets.choice(_PW_DIGITS),
    ]
    chars += [secrets.choice(_PW_POOL) for _ in range(length - 3)]
    for i in range(len(chars) - 1, 0, -1):
        j = secrets.randbelow(i + 1)
        chars[i], chars[j] = chars[j], chars[i]
    return "".join(chars)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
