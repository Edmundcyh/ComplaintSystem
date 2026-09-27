import re

from email_validator import EmailNotValidError, validate_email


def check_email(value):
    try:
        validate_email(value, check_deliverability=False)
    except EmailNotValidError as ex:
        raise ValueError(str(ex))
    # Stored as entered, so existing logins keep matching exactly
    return value


def check_password_bytes(value):
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("Password contains invalid characters")
    # bcrypt ignores everything after 72 bytes, so a longer password would
    # match any other password that starts with the same 72 bytes; it also
    # treats a NUL byte as the end of the password
    if len(encoded) > 72:
        raise ValueError("Password must be at most 72 bytes long")
    if b"\x00" in encoded:
        raise ValueError("Password must not contain NUL characters")
    return value


def normalize_iban(value):
    """Validate an IBAN's format and check digits; return it without spaces."""
    iban = value.replace(" ", "").upper()
    if not re.fullmatch(r"[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}", iban):
        raise ValueError("Invalid IBAN")
    rearranged = iban[4:] + iban[:4]
    if int("".join(str(int(ch, 36)) for ch in rearranged)) % 97 != 1:
        raise ValueError("Invalid IBAN")
    return iban
