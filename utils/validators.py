import re

from email_validator import EmailNotValidError, validate_email


def check_email(value):
    try:
        validate_email(value, check_deliverability=False)
    except EmailNotValidError as ex:
        raise ValueError(str(ex))
    # Addresses are treated as case-insensitive (as mail providers do), so
    # one person can't hold two accounts and logging in with "Jane@..." works
    return value.lower()


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


def strip_iban(value):
    """Remove spaces and uppercase an IBAN, without validating it."""
    return value.replace(" ", "").upper()


def normalize_iban(value):
    """Validate an IBAN's format and check digits; return it without spaces."""
    iban = strip_iban(value)
    if not re.fullmatch(r"[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}", iban):
        raise ValueError("Invalid IBAN")
    rearranged = iban[4:] + iban[:4]
    if int("".join(str(int(ch, 36)) for ch in rearranged)) % 97 != 1:
        raise ValueError("Invalid IBAN")
    return iban
