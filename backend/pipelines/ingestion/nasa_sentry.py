"""NASA Planetary Defense Risk Intelligence Platform — Sentry Risk Ingestion Pipeline.

Ingests Mode S (Summary of available objects) from the NASA/JPL CNEOS Sentry API,
validates object risk parameters, serializes to columnar Parquet (fact_sentry_risk_snapshot),
and stores raw payloads and analytical Parquet in Amazon S3.
"""
import argparse
from datetime import datetime, timezone
import json
import logging
import os
import sys
import time
import uuid

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv
import pyarrow as pa
import requests

from pipeline_utils import (
    build_lineage_metadata,
    get_http_session,
    redact_api_key,
    upload_file_to_s3,
    write_parquet,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Load variables from .env if present
load_dotenv(dotenv_path=".env")

S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME", "nasa-asteroid-intelligence")
SENTRY_URL = "https://ssd-api.jpl.nasa.gov/sentry.api"

SENTRY_RISK_SNAPSHOT_SCHEMA = pa.schema([
    ("snapshot_key", pa.string()),
    ("run_id", pa.string()),
    ("snapshot_time", pa.string()),
    ("sentry_id", pa.string()),
    ("designation", pa.string()),
    ("fullname", pa.string()),
    ("absolute_magnitude", pa.float64()),
    ("estimated_diameter_km", pa.float64()),
    ("impact_probability", pa.float64()),
    ("potential_impacts_count", pa.int64()),
    ("palermo_scale_cum", pa.float64()),
    ("palermo_scale_max", pa.float64()),
    ("torino_scale_max", pa.int64()),
    ("v_infinity_km_s", pa.float64()),
    ("impact_year_range", pa.string()),
    ("last_obs_date", pa.string()),
    ("last_obs_jd", pa.float64()),
])


def fetch_sentry_data(run_id=None):
    """Fetch Mode S summary data from NASA/JPL Sentry API with retries and timeout."""
    prefix = f"[{run_id}] " if run_id else ""
    logger.info("%sFetching Sentry Mode S data from %s", prefix, SENTRY_URL)

    session = get_http_session()
    response = session.get(SENTRY_URL, timeout=15)
    response.raise_for_status()

    payload = response.json()

    if not isinstance(payload, dict):
        raise ValueError("Invalid Sentry API response: expected JSON object root.")

    if "signature" not in payload:
        logger.warning("%sSentry response payload missing 'signature' block.", prefix)

    if "data" not in payload or not isinstance(payload["data"], list):
        raise ValueError("Invalid Sentry API response: missing 'data' array.")

    return payload


def save_raw_json(data, filename="sentry_risk_snapshot_raw.json"):
    """Save raw Sentry JSON payload locally with secret redaction."""
    with open(filename, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=4)


def extract_sentry_risk_snapshot(data, snapshot_key, run_id, snapshot_time):
    """Validate and extract Mode S records into normalized risk snapshot rows."""
    raw_records = data.get("data", [])
    records_received = len(raw_records)
    skipped_records = 0
    valid_records = []
    seen_ids = set()

    for item in raw_records:
        if not isinstance(item, dict):
            skipped_records += 1
            continue

        sentry_id = item.get("id")
        designation = item.get("des")

        if not sentry_id or not designation:
            skipped_records += 1
            continue

        sentry_id = str(sentry_id).strip()
        designation = str(designation).strip()

        if not sentry_id or not designation:
            skipped_records += 1
            continue

        fullname = item.get("fullname")
        fullname = str(fullname).strip() if fullname is not None else designation

        # Validate impact probability (0 < ip <= 1)
        ip = item.get("ip")
        try:
            ip_val = float(ip)
            if ip_val <= 0.0 or ip_val > 1.0:
                skipped_records += 1
                continue
        except (TypeError, ValueError):
            skipped_records += 1
            continue

        # Validate potential impacts count (n_imp >= 1)
        n_imp = item.get("n_imp")
        try:
            n_imp_val = int(n_imp)
            if n_imp_val < 1:
                skipped_records += 1
                continue
        except (TypeError, ValueError):
            skipped_records += 1
            continue

        # Validate Palermo Scale values
        ps_cum = item.get("ps_cum")
        ps_max = item.get("ps_max")
        try:
            ps_cum_val = float(ps_cum)
            ps_max_val = float(ps_max)
        except (TypeError, ValueError):
            skipped_records += 1
            continue

        # Validate Torino Scale value (ts_max >= 0)
        ts_max = item.get("ts_max")
        try:
            ts_max_val = int(ts_max)
            if ts_max_val < 0:
                skipped_records += 1
                continue
        except (TypeError, ValueError):
            skipped_records += 1
            continue

        # Validate velocity at infinity (v_inf >= 0)
        v_inf = item.get("v_inf")
        try:
            v_inf_val = float(v_inf)
            if v_inf_val < 0.0:
                skipped_records += 1
                continue
        except (TypeError, ValueError):
            skipped_records += 1
            continue

        # Validate last_obs date format (YYYY-MM-DD)
        last_obs = item.get("last_obs")
        if not last_obs:
            skipped_records += 1
            continue
        try:
            datetime.strptime(str(last_obs).strip(), "%Y-%m-%d")
            last_obs_date = str(last_obs).strip()
        except (TypeError, ValueError):
            skipped_records += 1
            continue

        range_val = item.get("range")
        impact_year_range = str(range_val).strip() if range_val is not None else ""

        # Handle optional float fields: absolute magnitude, diameter, last_obs_jd
        h_val = item.get("h")
        if h_val is None or str(h_val).strip() == "":
            absolute_magnitude = None
        else:
            try:
                absolute_magnitude = float(h_val)
            except (TypeError, ValueError):
                skipped_records += 1
                continue

        dia_val = item.get("diameter")
        if dia_val is None or str(dia_val).strip() == "":
            estimated_diameter_km = None
        else:
            try:
                estimated_diameter_km = float(dia_val)
            except (TypeError, ValueError):
                skipped_records += 1
                continue

        jd_val = item.get("last_obs_jd")
        if jd_val is None or str(jd_val).strip() == "":
            last_obs_jd = None
        else:
            try:
                last_obs_jd = float(jd_val)
            except (TypeError, ValueError):
                skipped_records += 1
                continue

        # Deduplicate by sentry_id
        if sentry_id in seen_ids:
            logger.warning("Skipping duplicate Sentry record: sentry_id=%s", sentry_id)
            skipped_records += 1
            continue
        seen_ids.add(sentry_id)

        record = {
            "snapshot_key": snapshot_key,
            "run_id": run_id,
            "snapshot_time": snapshot_time,
            "sentry_id": sentry_id,
            "designation": designation,
            "fullname": fullname,
            "absolute_magnitude": absolute_magnitude,
            "estimated_diameter_km": estimated_diameter_km,
            "impact_probability": ip_val,
            "potential_impacts_count": n_imp_val,
            "palermo_scale_cum": ps_cum_val,
            "palermo_scale_max": ps_max_val,
            "torino_scale_max": ts_max_val,
            "v_infinity_km_s": v_inf_val,
            "impact_year_range": impact_year_range,
            "last_obs_date": last_obs_date,
            "last_obs_jd": last_obs_jd,
        }
        valid_records.append(record)

    return valid_records, skipped_records, records_received


def save_to_parquet(records, filename="fact_sentry_risk_snapshot.parquet"):
    """Serialize validated Sentry records to Parquet using the explicit schema."""
    write_parquet(
        records=records,
        schema=SENTRY_RISK_SNAPSHOT_SCHEMA,
        output_path=filename,
        compression="snappy"
    )


def upload_raw_to_s3(snapshot_date, metadata=None):
    """Upload raw Sentry JSON payload to deterministic date-partitioned S3 key."""
    year = snapshot_date.strftime("%Y")
    month = snapshot_date.strftime("%m")
    day = snapshot_date.strftime("%d")
    s3_key = f"raw/sentry/risk_snapshot/year={year}/month={month}/day={day}/sentry_risk_snapshot_raw.json"

    run_id = metadata.get("run_id") if isinstance(metadata, dict) else None
    prefix = f"[{run_id}] " if run_id else ""

    s3 = boto3.client("s3")
    try:
        upload_file_to_s3(
            local_file_path="sentry_risk_snapshot_raw.json",
            bucket_name=S3_BUCKET_NAME,
            s3_key=s3_key,
            metadata=metadata,
            s3_client=s3,
        )
        logger.info("%sUploaded raw Sentry JSON to s3://%s/%s", prefix, S3_BUCKET_NAME, s3_key)
    except (BotoCoreError, ClientError) as error:
        logger.error(
            "%sRaw Sentry S3 upload failed for s3://%s/%s: %s",
            prefix,
            S3_BUCKET_NAME,
            s3_key,
            redact_api_key(str(error)),
        )
        raise


def upload_processed_to_s3(snapshot_date, metadata=None):
    """Upload processed Sentry Parquet to deterministic date-partitioned S3 key."""
    year = snapshot_date.strftime("%Y")
    month = snapshot_date.strftime("%m")
    day = snapshot_date.strftime("%d")
    parquet_key = (
        f"processed/sentry/risk_snapshot/year={year}/month={month}/day={day}/"
        "fact_sentry_risk_snapshot.parquet"
    )

    run_id = metadata.get("run_id") if isinstance(metadata, dict) else None
    prefix = f"[{run_id}] " if run_id else ""

    s3 = boto3.client("s3")
    try:
        upload_file_to_s3(
            local_file_path="fact_sentry_risk_snapshot.parquet",
            bucket_name=S3_BUCKET_NAME,
            s3_key=parquet_key,
            metadata=metadata,
            s3_client=s3,
        )
        logger.info(
            "%sUploaded processed Sentry Parquet to s3://%s/%s",
            prefix,
            S3_BUCKET_NAME,
            parquet_key,
        )
    except (BotoCoreError, ClientError) as error:
        logger.error(
            "%sProcessed Sentry Parquet S3 upload failed for s3://%s/%s: %s",
            prefix,
            S3_BUCKET_NAME,
            parquet_key,
            redact_api_key(str(error)),
        )
        raise


def parse_args():
    """Parse CLI arguments for Sentry risk ingestion."""
    parser = argparse.ArgumentParser(
        description="NASA Planetary Defense Risk Intelligence Platform — Sentry Risk Ingestion Pipeline"
    )
    parser.add_argument(
        "--snapshot-date",
        type=str,
        help="Snapshot date for Sentry catalog (YYYY-MM-DD). Default: today (UTC)",
        default=None,
    )
    return parser.parse_args()


def main(snapshot_date_str=None):
    """Execute the end-to-end Sentry Mode S ingestion workflow."""
    start_time = time.perf_counter()
    run_id = uuid.uuid4().hex[:12]
    main.current_run_id = run_id
    snapshot_time = datetime.now(timezone.utc).isoformat()

    if snapshot_date_str:
        if isinstance(snapshot_date_str, str):
            snapshot_date_str = snapshot_date_str.strip()
        try:
            resolved_snapshot_date = datetime.strptime(snapshot_date_str, "%Y-%m-%d").date()
        except ValueError:
            logger.error("Invalid --snapshot-date format: %s. Expected YYYY-MM-DD.", snapshot_date_str)
            return 1
    else:
        resolved_snapshot_date = datetime.now(timezone.utc).date()

    snapshot_key = resolved_snapshot_date.strftime("%Y-%m-%d")

    lineage_metadata = build_lineage_metadata(
        source_name="nasa_sentry_api",
        run_id=run_id,
        ingested_at=snapshot_time,
    )

    logger.info(
        "[%s] Starting NASA Sentry risk ingestion (run_id: %s, snapshot_key: %s, snapshot_time: %s)",
        run_id,
        run_id,
        snapshot_key,
        snapshot_time,
    )

    data = fetch_sentry_data(run_id=run_id)
    logger.info("[%s] Sentry API request successful", run_id)

    logger.info("[%s] Saving raw Sentry response", run_id)
    save_raw_json(data)

    logger.info("[%s] Extracting and validating Sentry risk records", run_id)
    records, skipped_records, records_received = extract_sentry_risk_snapshot(
        data=data,
        snapshot_key=snapshot_key,
        run_id=run_id,
        snapshot_time=snapshot_time,
    )
    records_valid = len(records)
    rejection_pct = (skipped_records / records_received * 100) if records_received > 0 else 0.0

    logger.info(
        "[%s] Extraction summary: received=%d, valid=%d, skipped=%d, rejection=%.1f%%",
        run_id,
        records_received,
        records_valid,
        skipped_records,
        rejection_pct,
    )

    if rejection_pct > 20.0:
        logger.warning(
            "[%s] High rejection rate: %.1f%% of received records were skipped (%d/%d)",
            run_id,
            rejection_pct,
            skipped_records,
            records_received,
        )

    if records_valid == 0:
        logger.error(
            "[%s] Data quality failure: 0 valid Sentry records produced "
            "(received: %d, valid: %d, skipped: %d). "
            "Halting pipeline to prevent uploading empty dataset to S3.",
            run_id,
            records_received,
            records_valid,
            skipped_records,
        )
        return 1

    logger.info("[%s] Saving %d Sentry risk records to Parquet", run_id, records_valid)
    save_to_parquet(records)

    logger.info("[%s] Uploading raw Sentry response to S3", run_id)
    upload_raw_to_s3(snapshot_date=resolved_snapshot_date, metadata=lineage_metadata)

    logger.info("[%s] Uploading processed Sentry Parquet to S3", run_id)
    upload_processed_to_s3(snapshot_date=resolved_snapshot_date, metadata=lineage_metadata)

    elapsed_time = time.perf_counter() - start_time
    logger.info("[%s] Sentry pipeline run completed successfully in %.2fs", run_id, elapsed_time)
    logger.info("[%s] Records received: %d", run_id, records_received)
    logger.info("[%s] Total valid records: %d", run_id, records_valid)
    logger.info("[%s] Skipped invalid records: %d", run_id, skipped_records)
    return 0


if __name__ == "__main__":
    args = parse_args()
    try:
        exit_code = main(snapshot_date_str=args.snapshot_date)
        sys.exit(exit_code or 0)
    except requests.exceptions.HTTPError as error:
        run_id_prefix = (
            f"[{getattr(main, 'current_run_id', None)}] "
            if getattr(main, "current_run_id", None)
            else ""
        )
        logger.error(
            "%sSentry API returned an HTTP error: %s",
            run_id_prefix,
            redact_api_key(str(error)),
        )
        if error.response is not None and getattr(error.response, "text", None):
            logger.error(
                "%sHTTP response body: %s",
                run_id_prefix,
                redact_api_key(error.response.text.strip())[:500],
            )
        sys.exit(1)
    except requests.exceptions.RequestException as error:
        run_id_prefix = (
            f"[{getattr(main, 'current_run_id', None)}] "
            if getattr(main, "current_run_id", None)
            else ""
        )
        logger.error(
            "%sNetwork error during Sentry ingestion: %s",
            run_id_prefix,
            redact_api_key(str(error)),
        )
        sys.exit(1)
    except Exception as error:
        run_id_prefix = (
            f"[{getattr(main, 'current_run_id', None)}] "
            if getattr(main, "current_run_id", None)
            else ""
        )
        logger.error(
            "%sSentry pipeline failure: %s",
            run_id_prefix,
            redact_api_key(str(error)),
        )
        sys.exit(1)
