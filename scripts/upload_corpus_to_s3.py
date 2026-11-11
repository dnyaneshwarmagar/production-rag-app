"""
Copy data/raw into the S3 folder the app imports from.

    python scripts/upload_corpus_to_s3.py --bucket nimbu-rag-docs --region ap-south-1

Credentials: `aws configure`, or AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY.
After this, on the server:  curl -X POST http://<ec2>:8000/s3/sync
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
RAW = ROOT / "data" / "raw"

ap = argparse.ArgumentParser()
ap.add_argument("--bucket", default="")
ap.add_argument("--prefix", default="")
ap.add_argument("--region", default="")
args = ap.parse_args()

from app import settings  # noqa: E402  (loads .env)

bucket = args.bucket or settings.S3_BUCKET
prefix = args.prefix or settings.S3_PREFIX
region = args.region or settings.AWS_REGION
if not bucket:
    raise SystemExit("Pass --bucket or set S3_BUCKET in .env")
prefix = prefix.strip().lstrip("/")
if prefix and not prefix.endswith("/"):
    prefix += "/"

client = boto3.client("s3", **({"region_name": region} if region else {}))
uploaded = 0
for path in sorted(RAW.iterdir()):
    if path.name.startswith(".") or not path.is_file():
        continue
    key = prefix + path.name
    client.upload_file(str(path), bucket, key)
    uploaded += 1
    print(f"uploaded s3://{bucket}/{key}")
print(f"\n{uploaded} objects in s3://{bucket}/{prefix}")
print("Next, on the API host:  curl -X POST http://127.0.0.1:8000/s3/sync")
