import argparse
import copy
import csv
import json 
import logging
import os
import sys
import time
import uuid
from datetime import date, datetime, timedelta, timezone

import boto3
from botocore.exceptions import BotoCoreError, ClientError
import pyarrow as pa
import pyarrow.parquet as pq
import requests
from dotenv import load_dotenv

from database import DB_PATH, load_data
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

# Load variables from .env
load_dotenv(dotenv_path=".env")

S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME", "nasa-asteroid-intelligence")
API_KEY = os.getenv("NASA_API_KEY")

ASTEROID_SCHEMA = pa.schema([
    ("id", pa.string()),
    ("name", pa.string()),
    ("closest_approach_date", pa.string()),
    ("miss_distance_km", pa.float64()),
    ("hazardous", pa.bool_())
])

URL = "https://api.nasa.gov/neo/rest/v1/feed"


def fetch_data(start=None, end=None, key=None, run_id=None):
    if start is None:
        start = date.today()
    elif isinstance(start, str):
        start = datetime.strptime(start, "%Y-%m-%d").date()

    if end is None:
        end = start + timedelta(days=6)
    elif isinstance(end, str):
        end = datetime.strptime(end, "%Y-%m-%d").date()

    key = key or API_KEY

    start_str = start.strftime("%Y-%m-%d") if isinstance(start, (date, datetime)) else str(start)
    end_str = end.strftime("%Y-%m-%d") if isinstance(end, (date, datetime)) else str(end)

    params = {
        "start_date": start_str,
        "end_date": end_str,
        "api_key": key
    }

    if run_id:
        logger.info("[%s] Fetching NASA data from %s to %s", run_id, start_str, end_str)
    else:
        logger.info("Fetching NASA data from %s to %s", start_str, end_str)

    session = get_http_session()
    response = session.get(URL, params=params, timeout=15)
    response.raise_for_status()

    return response.json()


def extract_asteroids(data):
    asteroids = data["near_earth_objects"]
    asteroid_data = []
    seen_keys = set()
    skipped_records = 0
    records_received = 0

    for date_str, asteroid_list in asteroids.items():
        for asteroid in asteroid_list:
            records_received += 1

            if not asteroid.get("id"):
                skipped_records += 1
                continue

            if not asteroid.get("name"):
                skipped_records += 1
                continue

            if not asteroid.get("close_approach_data"):
                skipped_records += 1
                continue

            approach = asteroid["close_approach_data"][0]

            approach_date = approach.get("close_approach_date")
            if not approach_date:
                skipped_records += 1
                continue

            try:
                datetime.strptime(approach_date, "%Y-%m-%d")
            except (ValueError, TypeError):
                logger.warning(
                    "Skipping asteroid record with invalid close_approach_date: %s",
                    approach_date
                )
                skipped_records += 1
                continue

            if not approach.get("miss_distance"):
                skipped_records += 1
                continue
            miss_distance = approach["miss_distance"].get("kilometers")

            if not miss_distance:
                skipped_records += 1
                continue

            try:
                miss_distance = float(miss_distance)
            except (TypeError, ValueError):
                skipped_records += 1
                continue

            if miss_distance <= 0:
                skipped_records += 1
                continue

            hazardous = asteroid.get("is_potentially_hazardous_asteroid")

            if not isinstance(hazardous, bool):
                skipped_records += 1
                continue

            dedup_key = (str(asteroid["id"]), approach_date)
            if dedup_key in seen_keys:
                logger.warning(
                    "Skipping duplicate asteroid approach: id=%s, date=%s",
                    asteroid["id"],
                    approach_date
                )
                skipped_records += 1
                continue
            seen_keys.add(dedup_key)

            asteroid_record = {
                "id": asteroid["id"],
                "name": asteroid["name"],
                "closest_approach_date": approach_date,
                "miss_distance_km": miss_distance,
                "hazardous": hazardous
            }

            asteroid_data.append(asteroid_record)

    return asteroid_data, skipped_records, records_received


def sanitize_raw_data(data):
    """Sanitize API-key-bearing URL values in top-level and per-asteroid links."""
    if not isinstance(data, dict):
        return data

    sanitized = copy.deepcopy(data)

    if "links" in sanitized and isinstance(sanitized["links"], dict):
        for key, val in sanitized["links"].items():
            if isinstance(val, str):
                sanitized["links"][key] = redact_api_key(val)

    if "near_earth_objects" in sanitized and isinstance(sanitized["near_earth_objects"], dict):
        for asteroid_list in sanitized["near_earth_objects"].values():
            if isinstance(asteroid_list, list):
                for asteroid in asteroid_list:
                    if isinstance(asteroid, dict) and "links" in asteroid and isinstance(asteroid["links"], dict):
                        for key, val in asteroid["links"].items():
                            if isinstance(val, str):
                                asteroid["links"][key] = redact_api_key(val)

    return sanitized


def save_raw_json(data, filename="asteroids_raw.json"):
    sanitized = sanitize_raw_data(data)
    with open(filename, "w", encoding="utf-8") as file:
        json.dump(sanitized, file, indent=4)

def upload_raw_to_s3(start_date=None, metadata=None):
    if start_date is None:
        start_date = date.today()
    elif isinstance(start_date, str):
        start_date = datetime.strptime(start_date, "%Y-%m-%d").date()

    year = start_date.strftime("%Y")
    month = start_date.strftime("%m")
    day = start_date.strftime("%d")
    s3_key = f"raw/year={year}/month={month}/day={day}/asteroids_raw.json"

    run_id = metadata.get("run_id") if isinstance(metadata, dict) else None
    prefix = f"[{run_id}] " if run_id else ""

    s3 = boto3.client("s3")
    try:
        upload_file_to_s3(
            "asteroids_raw.json",
            S3_BUCKET_NAME,
            s3_key,
            metadata=metadata,
            s3_client=s3,
        )
        logger.info("%sUploaded raw JSON to s3://%s/%s", prefix, S3_BUCKET_NAME, s3_key)
    except (BotoCoreError, ClientError) as error:
        logger.error(
            "%sRaw S3 upload failed for s3://%s/%s: %s",
            prefix,
            S3_BUCKET_NAME,
            s3_key,
            redact_api_key(str(error)),
        )
        raise

def upload_processed_to_s3(start_date=None, metadata=None):
    if start_date is None:
        start_date = date.today()
    elif isinstance(start_date, str):
        start_date = datetime.strptime(start_date, "%Y-%m-%d").date()

    year = start_date.strftime("%Y")
    month = start_date.strftime("%m")
    day = start_date.strftime("%d")
    parquet_key = f"processed/year={year}/month={month}/day={day}/asteroids.parquet"
    csv_key = f"processed_csv/year={year}/month={month}/day={day}/asteroids.csv"

    run_id = metadata.get("run_id") if isinstance(metadata, dict) else None
    prefix = f"[{run_id}] " if run_id else ""

    s3 = boto3.client("s3")
    try:
        upload_file_to_s3(
            "asteroids.parquet",
            S3_BUCKET_NAME,
            parquet_key,
            metadata=metadata,
            s3_client=s3,
        )
        logger.info("%sUploaded processed Parquet to s3://%s/%s", prefix, S3_BUCKET_NAME, parquet_key)
    except (BotoCoreError, ClientError) as error:
        logger.error(
            "%sProcessed Parquet S3 upload failed for s3://%s/%s: %s",
            prefix,
            S3_BUCKET_NAME,
            parquet_key,
            redact_api_key(str(error)),
        )
        raise

    try:
        upload_file_to_s3(
            "asteroids.csv",
            S3_BUCKET_NAME,
            csv_key,
            metadata=metadata,
            s3_client=s3,
        )
        logger.info("%sUploaded processed CSV to s3://%s/%s", prefix, S3_BUCKET_NAME, csv_key)
    except (BotoCoreError, ClientError) as error:
        logger.error(
            "%sProcessed CSV S3 upload failed for s3://%s/%s (Parquet upload already succeeded at s3://%s/%s): %s",
            prefix,
            S3_BUCKET_NAME,
            csv_key,
            S3_BUCKET_NAME,
            parquet_key,
            redact_api_key(str(error)),
        )
        raise

def save_neows_summary(summary_data, filename="neows_summary.json"):
    """Save authoritative NeoWs ingestion summary as deterministic JSON."""
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(summary_data, f, indent=4)


def save_to_parquet(asteroid_data, filename="asteroids.parquet", run_id=None):
    if run_id:
        table = pa.Table.from_pylist(asteroid_data, schema=ASTEROID_SCHEMA)
        existing_meta = table.schema.metadata or {}
        existing_meta[b"run_id"] = str(run_id).encode("utf-8")
        table = table.replace_schema_metadata(existing_meta)
        pq.write_table(table, filename, compression="snappy")
    else:
        write_parquet(asteroid_data, schema=ASTEROID_SCHEMA, output_path=filename, compression="snappy")

def save_to_csv(asteroid_data, filename="asteroids.csv"):
    with open(filename, "w", newline="", encoding="utf-8") as file:

        fieldnames = [
    "id",
    "name",
    "closest_approach_date",
    "miss_distance_km",
    "hazardous"
]           

        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames
        )

        writer.writeheader()
        writer.writerows(asteroid_data)


def parse_args():
    parser = argparse.ArgumentParser(
        description="NASA Asteroid Intelligence Platform — Ingestion & ETL Pipeline"
    )
    parser.add_argument(
        "--start-date",
        type=str,
        help="Start date for NASA feed (YYYY-MM-DD). Default: today",
        default=None
    )
    parser.add_argument(
        "--end-date",
        type=str,
        help="End date for NASA feed (YYYY-MM-DD). Default: start_date + 6 days",
        default=None
    )
    parser.add_argument(
        "--summary-file",
        type=str,
        help="Path for authoritative NeoWs summary JSON. Default: neows_summary.json",
        default="neows_summary.json"
    )
    return parser.parse_args()


def main(start_date_str=None, end_date_str=None, summary_filename="neows_summary.json"):
    start_time = time.perf_counter()
    run_id = uuid.uuid4().hex[:12]
    main.current_run_id = run_id
    ingested_at = datetime.now(timezone.utc).isoformat()
    lineage_metadata = build_lineage_metadata(
        source_name="nasa_neows_api",
        run_id=run_id,
        ingested_at=ingested_at,
    )

    if not API_KEY:
        logger.error("NASA_API_KEY was not found.")
        logger.error("Check your .env file.")
        return 1

    if start_date_str:
        if isinstance(start_date_str, str):
            start_date_str = start_date_str.strip()
        try:
            resolved_start = datetime.strptime(start_date_str, "%Y-%m-%d").date()
        except ValueError:
            logger.error("Invalid --start-date format: %s. Expected YYYY-MM-DD.", start_date_str)
            return 1
    else:
        resolved_start = date.today()

    if end_date_str:
        if isinstance(end_date_str, str):
            end_date_str = end_date_str.strip()
        try:
            resolved_end = datetime.strptime(end_date_str, "%Y-%m-%d").date()
        except ValueError:
            logger.error("Invalid --end-date format: %s. Expected YYYY-MM-DD.", end_date_str)
            return 1
    else:
        resolved_end = resolved_start + timedelta(days=6)

    if resolved_end < resolved_start:
        logger.error("End date (%s) cannot be before start date (%s).", resolved_end, resolved_start)
        return 1

    days_diff = (resolved_end - resolved_start).days
    if days_diff > 7:
        logger.warning(
            "NASA NeoWs API limits queries to 7 days per request (requested: %d days). "
            "NASA may reject or truncate the response.",
            days_diff
        )

    logger.info("[%s] Starting NASA asteroid pipeline (run_id: %s, ingested_at: %s)", run_id, run_id, ingested_at)

    data = fetch_data(start=resolved_start, end=resolved_end, key=API_KEY, run_id=run_id)
    logger.info("[%s] API request successful", run_id)

    logger.info("[%s] Saving raw NASA response", run_id)
    save_raw_json(data)

    logger.info("[%s] Extracting and validating asteroid data", run_id)

    asteroid_data, skipped_records, records_received = extract_asteroids(data)
    records_valid = len(asteroid_data)
    rejection_pct = (skipped_records / records_received * 100.0) if records_received > 0 else 0.0
    gate_passed = (records_valid > 0) and (rejection_pct < 20.0)

    summary_data = {
        "run_id": run_id,
        "start_date": resolved_start.strftime("%Y-%m-%d"),
        "end_date": resolved_end.strftime("%Y-%m-%d"),
        "records_received": records_received,
        "valid_records": records_valid,
        "skipped_records": skipped_records,
        "rejection_pct": round(rejection_pct, 2),
        "rejection_threshold_pct": 20.0,
        "gate_passed": gate_passed,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        save_neows_summary(summary_data, filename=summary_filename)
        logger.info("[%s] Saved NeoWs execution summary to %s", run_id, summary_filename)
    except Exception as e:
        logger.error(
            "[%s] Failed to write NeoWs authoritative summary to %s: %s. "
            "Halting pipeline to prevent unmonitored production output.",
            run_id,
            summary_filename,
            e,
        )
        return 1

    logger.info(
        "[%s] Extraction summary: received=%d, valid=%d, skipped=%d, rejection=%.1f%%, gate_passed=%s",
        run_id,
        records_received,
        records_valid,
        skipped_records,
        rejection_pct,
        gate_passed,
    )

    if rejection_pct > 20.0:
        logger.warning(
            "[%s] High rejection rate: %.1f%% of received records were skipped (%d/%d)",
            run_id,
            rejection_pct,
            skipped_records,
            records_received,
        )

    if not gate_passed:
        logger.error(
            "[%s] NeoWs Data Quality Gate Failure: valid_records=%d, rejection_pct=%.1f%% (threshold < 20.0%%). "
            "Halting pipeline before CSV, Parquet, SQLite, or S3 outputs.",
            run_id,
            records_valid,
            rejection_pct,
        )
        return 1

    logger.info("[%s] Saving asteroid data to CSV and Parquet", run_id)

    save_to_csv(asteroid_data)
    save_to_parquet(asteroid_data, run_id=run_id)

    load_data(asteroid_data)
    logger.info("[%s] Loaded %d valid records into SQLite database (%s)", run_id, records_valid, DB_PATH)

    logger.info("[%s] Uploading raw NASA response to S3", run_id)
    upload_raw_to_s3(start_date=resolved_start, metadata=lineage_metadata)

    logger.info("[%s] Uploading processed data to S3", run_id)
    upload_processed_to_s3(start_date=resolved_start, metadata=lineage_metadata)

    elapsed_time = time.perf_counter() - start_time
    logger.info("[%s] Pipeline run %s completed successfully in %.2fs", run_id, run_id, elapsed_time)
    logger.info("[%s] CSV and Parquet created successfully", run_id)
    logger.info("[%s] Records received: %d", run_id, records_received)
    logger.info("[%s] Total valid asteroids: %d", run_id, records_valid)
    logger.info("[%s] Skipped invalid records: %d", run_id, skipped_records)
    return 0


if __name__ == "__main__":
    args = parse_args()
    try:
        exit_code = main(
            start_date_str=args.start_date,
            end_date_str=args.end_date,
            summary_filename=args.summary_file,
        )
        sys.exit(exit_code or 0)

    except requests.exceptions.HTTPError as error:
        run_id_prefix = f"[{getattr(main, 'current_run_id', None)}] " if getattr(main, 'current_run_id', None) else ""
        logger.error(
            "%sNASA API returned an HTTP error: %s",
            run_id_prefix,
            redact_api_key(str(error))
        )
        if error.response is not None and getattr(error.response, "text", None):
            logger.error(
                "%sHTTP response body: %s",
                run_id_prefix,
                redact_api_key(error.response.text.strip())[:500]
            )
        sys.exit(1)

    except requests.exceptions.RequestException as error:
        run_id_prefix = f"[{getattr(main, 'current_run_id', None)}] " if getattr(main, 'current_run_id', None) else ""
        logger.error(
            "%sNetwork error: %s",
            run_id_prefix,
            redact_api_key(str(error))
        )
        sys.exit(1)

    except Exception as error:
        run_id_prefix = f"[{getattr(main, 'current_run_id', None)}] " if getattr(main, 'current_run_id', None) else ""
        logger.error(
            "%sPipeline failure: %s",
            run_id_prefix,
            redact_api_key(str(error))
        )
        sys.exit(1)
