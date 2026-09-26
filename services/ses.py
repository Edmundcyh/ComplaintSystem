from functools import lru_cache

import boto3
from botocore.config import Config
from decouple import config
from starlette.concurrency import run_in_threadpool


class SESService:
    def __init__(self):
        self.key = config("AWS_ACCESS_KEY")
        self.secret = config("AWS_SECRET")
        self.ses = boto3.client(
            "ses",
            region_name=config("SES_REGION"),
            aws_access_key_id=self.key,
            aws_secret_access_key=self.secret,
            config=Config(connect_timeout=5, read_timeout=10),
        )
        self.sender = config("SES_SENDER_EMAIL")

    async def send_mail(self, subject, to_addresses, text_data):
        body = {"Text": {"Data": text_data, "Charset": "UTF-8"}}
        await run_in_threadpool(
            self.ses.send_email,
            Source=self.sender,
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
