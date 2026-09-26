import base64
import binascii
import io

from fastapi import HTTPException
from PIL import Image

MAX_PHOTO_BYTES = 5 * 1024 * 1024
# Checked before decoding: a small file can expand to a huge image in memory
MAX_PHOTO_PIXELS = 40_000_000
ALLOWED_PHOTO_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
_PILLOW_FORMATS = {"jpeg": "JPEG", "png": "PNG", "webp": "WEBP"}
# Line breaks are common in base64 output; strict decoding rejects them
_STRIP_WHITESPACE = str.maketrans("", "", " \t\r\n")


def decode_photo(encoded_string, extension):
    """Decode a base64 photo and check it is a complete image of the given type.

    Returns the photo bytes and their content type. CPU-bound; call it from a
    worker thread.
    """
    try:
        data = base64.b64decode(
            encoded_string.translate(_STRIP_WHITESPACE), validate=True
        )
    except (binascii.Error, ValueError):
        raise HTTPException(400, "Invalid photo encoding")
    if len(data) > MAX_PHOTO_BYTES:
        raise HTTPException(400, "Photo is too large")
    image_type = "jpeg" if extension == "jpg" else extension
    formats = [_PILLOW_FORMATS[image_type]]
    try:
        with Image.open(io.BytesIO(data), formats=formats) as image:
            if image.width * image.height > MAX_PHOTO_PIXELS:
                raise HTTPException(400, "Photo dimensions are too large")
            # Structure and checksums (e.g. a PNG cut off before its end)
            image.verify()
        with Image.open(io.BytesIO(data), formats=formats) as image:
            # Decodes all pixel data, so truncated or corrupt images fail here
            image.load()
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError):
        raise HTTPException(400, f"Photo is not a valid {extension} image")
    return data, f"image/{image_type}"
