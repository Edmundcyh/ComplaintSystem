import base64

import pytest
from fastapi import HTTPException
from sqlalchemy.engine import make_url

from utils.helpers import decode_photo
from utils.validators import check_email, normalize_iban

from tests.conftest import make_image

JPEG = make_image("JPEG")
PNG = make_image("PNG")
WEBP = make_image("WEBP")


def encode(data):
    return base64.b64encode(data).decode()


@pytest.mark.parametrize(
    "data, extension, content_type",
    [
        (JPEG, "jpg", "image/jpeg"),
        (JPEG, "jpeg", "image/jpeg"),
        (PNG, "png", "image/png"),
        (WEBP, "webp", "image/webp"),
    ],
)
def test_decode_photo(data, extension, content_type):
    assert decode_photo(encode(data), extension) == (data, content_type)


def test_decode_photo_ignores_line_breaks():
    encoded = encode(PNG)
    wrapped = encoded[:10] + "\n" + encoded[10:]
    assert decode_photo(wrapped, "png")[0] == PNG


@pytest.mark.parametrize(
    "encoded, extension",
    [
        ("not base64!", "png"),
        # Valid only if non-base64 characters are silently dropped
        (encode(PNG)[:8] + "*!*!" + encode(PNG)[8:], "png"),
        (encode(b"plain text"), "png"),
        (encode(PNG), "jpg"),
        (encode(JPEG), "png"),
        # Right signature, but the image data is cut off / missing
        (encode(PNG[:-20]), "png"),
        (encode(JPEG[: len(JPEG) // 2]), "jpg"),
        (encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32), "png"),
        # Header and end but no image data (made Pillow raise IndexError)
        ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAAAElFTkSuQmCC", "png"),
    ],
)
def test_decode_photo_rejects(encoded, extension):
    with pytest.raises(HTTPException) as ex:
        decode_photo(encoded, extension)
    assert ex.value.status_code == 400


def test_decode_photo_pixel_limit(monkeypatch):
    monkeypatch.setattr("utils.helpers.MAX_PHOTO_PIXELS", 11)
    with pytest.raises(HTTPException) as ex:
        decode_photo(encode(PNG), "png")  # 4 x 3 = 12 pixels
    assert ex.value.status_code == 400


def test_decode_photo_size_limit(monkeypatch):
    monkeypatch.setattr("utils.helpers.MAX_PHOTO_BYTES", 10)
    with pytest.raises(HTTPException):
        decode_photo(encode(PNG), "png")


@pytest.mark.parametrize(
    "value, expected",
    [
        ("DE89370400440532013000", "DE89370400440532013000"),
        ("de89 3704 0044 0532 0130 00", "DE89370400440532013000"),
        ("GB82 WEST 1234 5698 7654 32", "GB82WEST12345698765432"),
        ("NL91ABNA0417164300", "NL91ABNA0417164300"),
    ],
)
def test_valid_iban(value, expected):
    assert normalize_iban(value) == expected


@pytest.mark.parametrize(
    "value", ["DE89370400440532013001", "DE8937", "1234567890123456", "", "DE89-3704"]
)
def test_invalid_iban(value):
    with pytest.raises(ValueError):
        normalize_iban(value)


def test_check_email_keeps_value():
    assert check_email("Jane.Doe@Example.COM") == "Jane.Doe@Example.COM"
    with pytest.raises(ValueError):
        check_email("jane@")


def test_database_url_quotes_credentials(monkeypatch):
    import db

    # Set every part explicitly so a developer's .env can't interfere
    for name, value in {
        "DATABASE_URL": "",
        "DB_USER": "app user",
        "DB_PASSWORD": "p@ss:w/rd%",
        "DB_HOST": "db.internal",
        "DB_PORT": "6543",
        "DB_NAME": "complaints",
    }.items():
        monkeypatch.setenv(name, value)

    url = make_url(db._database_url())

    assert url.username == "app user"
    assert url.password == "p@ss:w/rd%"
    assert (url.host, url.port, url.database) == ("db.internal", 6543, "complaints")


def test_database_url_accepts_postgres_scheme(monkeypatch):
    import db

    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@host:5432/name")
    assert db._database_url() == "postgresql://u:p@host:5432/name"
