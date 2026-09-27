from functools import lru_cache

import boto3
from botocore.config import Config
from decouple import config
from starlette.concurrency import run_in_threadpool


class SESService:
    def __init__(self):
        # Settings are read when sending: email is best-effort, so missing
        # SES settings must only make the email fail, not the approval
        self._ses = None

    def _client(self):
        if self._ses is None:
            self._ses = boto3.client(
                "ses",
                region_name=config("SES_REGION"),
                aws_access_key_id=config("AWS_ACCESS_KEY"),
                aws_secret_access_key=config("AWS_SECRET"),
                config=Config(connect_timeout=5, read_timeout=10),
            )
        return self._ses

    async def send_mail(self, subject, to_addresses, text_data):
        body = {"Text": {"Data": text_data, "Charset": "UTF-8"}}
        await run_in_threadpool(
            self._client().send_email,
            Source=config("SES_SENDER_EMAIL"),
            Destination={
                "ToAddresses": to_addresses,
                "CcAddresses": [],
                "BccAddresses": [],
            },
            Message={
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": body,
            },
        )


@lru_cache
def get_ses_service():
    return SESService()
