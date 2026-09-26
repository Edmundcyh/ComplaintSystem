import base64
import binascii

from fastapi import HTTPException

MAX_PHOTO_BYTES = 5 * 1024 * 1024
ALLOWED_PHOTO_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
# Line breaks are common in base64 output; strict decoding rejects them
_STRIP_WHITESPACE = str.maketrans("", "", " \t\r\n")


def _detect_image_type(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def decode_photo(encoded_string, extension):
    """Decode a base64 photo and check it is an image of the given type.

    Returns the photo bytes and their content type.
    """
    try:
        data = base64.b64decode(
            encoded_string.translate(_STRIP_WHITESPACE), validate=True
        )
    except (binascii.Error, ValueError):
        raise HTTPException(400, "Invalid photo encoding")
    if len(data) > MAX_PHOTO_BYTES:
        raise HTTPException(400, "Photo is too large")
    image_type = _detect_image_type(data)
    expected_type = "jpeg" if extension == "jpg" else extension
    if image_type is None or image_type != expected_type:
        raise HTTPException(400, f"Photo is not a valid {extension} image")
    return data, f"image/{image_type}"
