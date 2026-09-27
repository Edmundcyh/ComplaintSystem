import logging
from functools import lru_cache

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from decouple import config
from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool

PRESIGNED_URL_EXPIRY = 3600  # seconds

logger = logging.getLogger(__name__)


def photo_key(photo_url):
    # photo_url is the stored object URL; the key is its last segment
    return photo_url.rsplit("/", 1)[-1]


class S3Service:
    def __init__(self):
        self.key = config("AWS_ACCESS_KEY")
        self.secret = config("AWS_SECRET")
        self.region = config("AWS_REGION")
        self.s3 = boto3.client(
            "s3",
            region_name=self.region,
            aws_access_key_id=self.key,
            aws_secret_access_key=self.secret,
            config=Config(
                signature_version="s3v4",
                # Regional host in presigned URLs (the global one can redirect,
                # which breaks the signature)
                s3={"addressing_style": "virtual"},
                connect_timeout=5,
                read_timeout=10,
                retries={"max_attempts": 3, "mode": "standard"},
            ),
        )
        self.bucket = config("AWS_BUCKET")

    async def upload_photo(self, data, key, content_type):
        # Photos may contain personal data, so they are private and only
        # shared through short-lived presigned URLs (see presigned_url).
        try:
            await run_in_threadpool(
                self.s3.put_object,
                Bucket=self.bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
            )
        except (BotoCoreError, ClientError):
            logger.exception("S3 upload of %s failed", key)
            raise HTTPException(502, "S3 is not available at the moment")
        return f"https://{self.bucket}.s3.{self.region}.amazonaws.com/{key}"

    async def delete_photo(self, key):
        # Deleting a key that doesn't exist also succeeds, so retries are safe
        try:
            await run_in_threadpool(self.s3.delete_object, Bucket=self.bucket, Key=key)
        except (BotoCoreError, ClientError):
            logger.exception("S3 delete of %s failed", key)
            raise HTTPException(502, "S3 is not available at the moment")

    def presigned_url(self, photo_url):
        return self.s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": photo_key(photo_url)},
            ExpiresIn=PRESIGNED_URL_EXPIRY,
        )


@lru_cache
def get_s3_service():
    return S3Service()
