import logging

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from decouple import config
from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool

PRESIGNED_URL_EXPIRY = 3600  # seconds

logger = logging.getLogger(__name__)


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
        await run_in_threadpool(self.s3.delete_object, Bucket=self.bucket, Key=key)

    def presigned_url(self, photo_url):
        # photo_url is the stored object URL; the key is its last segment
        key = photo_url.rsplit("/", 1)[-1]
        return self.s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=PRESIGNED_URL_EXPIRY,
        )
