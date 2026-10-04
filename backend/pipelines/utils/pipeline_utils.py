"""Shared ingestion foundation utilities for the NASA Intelligence Platform.

Provides reusable, source-agnostic primitives for:
- HTTP requests with exponential backoff and retry
- API key redaction in URLs, text, and error messages
- S3 file uploads with user metadata and error handling
- PyArrow Parquet serialization
- Standardized lineage metadata dictionary generation
"""
from datetime import date, datetime, timezone
import json
import logging
import os
import re

import boto3
from botocore.exceptions import BotoCoreError, ClientError
import pyarrow as pa
import pyarrow.parquet as pq
import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

logger = logging.getLogger(__name__)

MANIFEST_SCHEMA_VERSION = "1.0.0"
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME", "nasa-asteroid-intelligence")
SENSITIVE_KEY_PATTERNS = {
    "api_key",
    "nasa_api_key",
    "aws_access_key_id",
    "aws_secret_access_key",
    "secret",
    "token",
    "password",
    "authorization",
}


def get_http_session(total_retries=3, backoff_factor=1):
    """Create a requests session configured with retries and exponential backoff."""
    session = requests.Session()
    retries = Retry(
        total=total_retries,
        backoff_factor=backoff_factor,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"]
    )
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def redact_api_key(text):
    """Safely redact api_key query parameters from a URL or text string."""
    if not isinstance(text, str):
        return text
    return re.sub(r'([?&]api_key=)[^&"\'\s]+', r'\g<1>REDACTED', text)


def upload_file_to_s3(local_file_path, bucket_name, s3_key, metadata=None, s3_client=None):
    """Generic S3 file upload primitive.

    Uploads a local file to S3 with optional metadata and error handling.
    The caller supplies the complete S3 key and bucket name.
    """
    extra_kwargs = {"ExtraArgs": {"Metadata": metadata}} if metadata else {}
    run_id = metadata.get("run_id") if isinstance(metadata, dict) else None
    prefix = f"[{run_id}] " if run_id else ""

    s3 = s3_client or boto3.client("s3")
    try:
        s3.upload_file(
            local_file_path,
            bucket_name,
            s3_key,
            **extra_kwargs
        )
        logger.info("%sUploaded %s to s3://%s/%s", prefix, local_file_path, bucket_name, s3_key)
    except (BotoCoreError, ClientError) as error:
        logger.error(
            "%sS3 upload failed for %s to s3://%s/%s: %s",
            prefix,
            local_file_path,
            bucket_name,
            s3_key,
            redact_api_key(str(error))
        )
        raise


def write_parquet(records, schema, output_path, compression="snappy"):
    """Generic PyArrow Parquet writer.

    Accepts an explicit PyArrow schema and list of record dicts.
    """
    table = pa.Table.from_pylist(records, schema=schema)
    pq.write_table(table, output_path, compression=compression)


def build_lineage_metadata(source_name, run_id, ingested_at):
    """Construct standard lineage metadata dictionary."""
    return {
        "run_id": run_id,
        "ingested_at": ingested_at,
        "source": source_name
    }


def build_manifest_s3_key(run_date, pipeline_run_id: str) -> str:
    """Build a deterministic, collision-resistant S3 key for a pipeline execution manifest.

    Format: metadata/pipeline_runs/year=YYYY/month=MM/day=DD/run_manifest_{pipeline_run_id}.json
    """
    if isinstance(run_date, str):
        dt_str = run_date.split("T")[0]
        dt = datetime.strptime(dt_str, "%Y-%m-%d").date()
    elif isinstance(run_date, datetime):
        dt = run_date.date()
    elif isinstance(run_date, date):
        dt = run_date
    else:
        raise ValueError(f"Invalid run_date '{run_date}'. Expected date, datetime, or YYYY-MM-DD string.")

    year = f"{dt.year:04d}"
    month = f"{dt.month:02d}"
    day = f"{dt.day:02d}"
    clean_id = str(pipeline_run_id).strip()
    if not clean_id:
        raise ValueError("pipeline_run_id must be a non-empty string.")

    return f"metadata/pipeline_runs/year={year}/month={month}/day={day}/run_manifest_{clean_id}.json"


def sanitize_manifest_data(obj):
    """Recursively scrub sensitive credential keys and redact API keys from strings."""
    if isinstance(obj, dict):
        cleaned = {}
        for k, v in obj.items():
            k_lower = str(k).strip().lower()
            if any(s in k_lower for s in SENSITIVE_KEY_PATTERNS):
                continue
            cleaned[k] = sanitize_manifest_data(v)
        return cleaned
    elif isinstance(obj, list):
        return [sanitize_manifest_data(item) for item in obj]
    elif isinstance(obj, str):
        return redact_api_key(obj)
    return obj


def generate_run_manifest(
    pipeline_run_id: str,
    execution_mode: str,
    started_at: str,
    completed_at: str | None = None,
    duration_seconds: float | None = None,
    overall_status: str | None = None,
    workflow_metadata: dict | None = None,
    stages: dict | None = None,
    published_s3_keys: list[str] | None = None,
    run_date: date | datetime | str | None = None,
    manifest_s3_key: str | None = None,
) -> dict:
    """Generate a lightweight, standardized pipeline execution manifest.

    Provides a consolidated operational lineage record for an entire
    production or historical backfill orchestration run.
    """
    if not pipeline_run_id or not str(pipeline_run_id).strip():
        raise ValueError("pipeline_run_id must be a non-empty string.")

    clean_pipeline_id = str(pipeline_run_id).strip()
    mode = str(execution_mode).strip().upper()
    if mode not in ("CURRENT_PRODUCTION", "HISTORICAL_BACKFILL"):
        raise ValueError(
            f"Invalid execution_mode '{execution_mode}'. Must be 'CURRENT_PRODUCTION' or 'HISTORICAL_BACKFILL'."
        )

    # Duration calculation
    if duration_seconds is not None:
        calc_duration = max(0.0, round(float(duration_seconds), 2))
    elif started_at and completed_at:
        try:
            st = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            cp = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
            calc_duration = max(0.0, round((cp - st).total_seconds(), 2))
        except (ValueError, TypeError):
            calc_duration = 0.0
    else:
        calc_duration = 0.0

    # Clean and sanitize stages
    raw_stages = stages or {}
    clean_stages = sanitize_manifest_data(raw_stages)

    # In historical backfill mode, ensure Sentry stage is explicitly None or omitted
    if mode == "HISTORICAL_BACKFILL" and "sentry" in clean_stages:
        if clean_stages["sentry"] is not None:
            clean_stages["sentry"] = None

    # Overall status determination
    if overall_status is not None:
        status_norm = str(overall_status).strip().upper()
        if status_norm not in ("SUCCESS", "FAILED"):
            raise ValueError(f"Invalid overall_status '{overall_status}'. Expected 'SUCCESS' or 'FAILED'.")
        final_status = status_norm
    else:
        has_failure = False
        for stg_data in clean_stages.values():
            if not isinstance(stg_data, dict):
                continue
            stg_status = str(stg_data.get("status", "")).strip().upper()
            if stg_status in ("FAILED", "FAILURE", "ERROR"):
                has_failure = True
                break
            if any(
                stg_data.get(k) is False
                for k in ("dq_gate_passed", "failure_gate_passed", "crosswalk_gate_passed", "checks_passed")
            ):
                has_failure = True
                break
        final_status = "FAILED" if has_failure else "SUCCESS"

    # Manifest S3 Key
    if manifest_s3_key:
        final_s3_key = manifest_s3_key
    else:
        target_date = run_date or started_at or date.today()
        final_s3_key = build_manifest_s3_key(target_date, clean_pipeline_id)

    # Aggregate published S3 object keys
    s3_keys = set()
    if published_s3_keys:
        for k in published_s3_keys:
            if k and isinstance(k, str):
                s3_keys.add(redact_api_key(k.strip()))

    for stg_data in clean_stages.values():
        if isinstance(stg_data, dict):
            for k, v in stg_data.items():
                if "s3_key" in k.lower():
                    if isinstance(v, str) and v.strip():
                        s3_keys.add(redact_api_key(v.strip()))
                    elif isinstance(v, list):
                        for item in v:
                            if isinstance(item, str) and item.strip():
                                s3_keys.add(redact_api_key(item.strip()))

    clean_wf_metadata = sanitize_manifest_data(workflow_metadata or {})

    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "pipeline_run_id": clean_pipeline_id,
        "execution_mode": mode,
        "overall_status": final_status,
        "started_at": started_at,
        "completed_at": completed_at,
        "duration_seconds": calc_duration,
        "workflow_metadata": clean_wf_metadata,
        "stages": clean_stages,
        "published_s3_keys": sorted(s3_keys),
        "manifest_s3_key": final_s3_key,
    }
    return manifest


def save_run_manifest(manifest: dict, local_path: str = "run_manifest.json") -> str:
    """Save manifest dictionary locally as formatted JSON."""
    dir_name = os.path.dirname(os.path.abspath(local_path))
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)
    with open(local_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    return local_path


def upload_run_manifest_to_s3(
    manifest: dict,
    local_file_path: str = "run_manifest.json",
    bucket_name: str | None = None,
    s3_client=None,
) -> str:
    """Upload run manifest to deterministic metadata/ S3 partition."""
    bucket = bucket_name or S3_BUCKET_NAME
    s3_key = manifest.get("manifest_s3_key")
    if not s3_key:
        raise ValueError("Manifest missing 'manifest_s3_key' field.")

    metadata = build_lineage_metadata(
        source_name="pipeline_manifest_generator",
        run_id=manifest["pipeline_run_id"],
        ingested_at=manifest.get("completed_at")
        or manifest.get("started_at")
        or datetime.now(timezone.utc).isoformat(),
    )
    upload_file_to_s3(
        local_file_path=local_file_path,
        bucket_name=bucket,
        s3_key=s3_key,
        metadata=metadata,
        s3_client=s3_client,
    )
    return s3_key
