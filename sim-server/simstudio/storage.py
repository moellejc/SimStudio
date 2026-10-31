from __future__ import annotations

import boto3
from botocore.client import Config

from simstudio.config import Settings


class S3Storage:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url or None,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            region_name=settings.aws_default_region,
            config=Config(s3={"addressing_style": "path"}),
        )

    def ensure_bucket(self) -> None:
        try:
            self.client.head_bucket(Bucket=self.settings.s3_bucket)
        except Exception:
            self.client.create_bucket(Bucket=self.settings.s3_bucket)

    def put_bytes(self, key: str, data: bytes, content_type: str) -> int:
        self.client.put_object(Bucket=self.settings.s3_bucket, Key=key, Body=data, ContentType=content_type)
        return len(data)

    def get_bytes(self, key: str) -> bytes:
        body = self.client.get_object(Bucket=self.settings.s3_bucket, Key=key)["Body"]
        return body.read()
