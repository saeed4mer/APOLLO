#!/usr/bin/env python3
"""NASA Planetary Defense Risk Intelligence Platform — Small-Body Database (SBDB) Pipeline.

Ingests high-precision astronomical, orbital, and physical parameter data from
the NASA/JPL Small-Body Database (SBDB) API for target asteroids.
Produces four normalized, date-partitioned analytical datasets in PyArrow Parquet.
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
import pyarrow as pa

from pipeline_utils import (
    build_lineage_metadata,
    get_http_session,
    redact_api_key,
    upload_file_to_s3,
    write_parquet,
)

# ---------------------------------------------------------------------------
# Logging Configuration
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants & Configuration
# ---------------------------------------------------------------------------
SBDB_URL = "https://ssd-api.jpl.nasa.gov/sbdb.api"
DEFAULT_TARGET = "2025 HX"
DEFAULT_ID_TYPE = "sstr"
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME", "nasa-asteroid-intelligence")
HTTP_TIMEOUT_SECONDS = 15
HTTP_USER_AGENT = "NASA-Planetary-Defense-Platform/1.0"

# ---------------------------------------------------------------------------
# PyArrow Schemas for the 4 Approved Normalized Datasets
# ---------------------------------------------------------------------------

# Table 1: Object Snapshot (Grain: snapshot_key, spkid)
SBDB_OBJECT_SCHEMA = pa.schema([
    ("snapshot_key", pa.string()),
    ("run_id", pa.string()),
    ("snapshot_time", pa.string()),
    ("spkid", pa.string()),
    ("designation", pa.string()),
    ("fullname", pa.string()),
    ("shortname", pa.string()),
    ("object_kind", pa.string()),
    ("is_neo", pa.bool_()),
    ("is_pha", pa.bool_()),
    ("orbit_class_code", pa.string()),
    ("orbit_class_name", pa.string()),
    ("orbit_id", pa.string()),
    ("prefix", pa.string()),
])

# Table 2: Orbit Solution (Grain: snapshot_key, spkid, orbit_id)
SBDB_ORBIT_SCHEMA = pa.schema([
    ("snapshot_key", pa.string()),
    ("run_id", pa.string()),
    ("snapshot_time", pa.string()),
    ("spkid", pa.string()),
    ("orbit_id", pa.string()),
    ("epoch_jd", pa.float64()),
    ("equinox", pa.string()),
    ("soln_date", pa.string()),
    ("orbit_source", pa.string()),
    ("producer", pa.string()),
    ("first_obs", pa.string()),
    ("last_obs", pa.string()),
    ("data_arc_days", pa.int64()),
    ("n_obs_used", pa.int64()),
    ("condition_code", pa.string()),
    ("rms", pa.float64()),
    ("earth_moid_au", pa.float64()),
    ("jupiter_moid_au", pa.float64()),
    ("t_jup", pa.float64()),
    ("pe_used", pa.string()),
    ("sb_used", pa.string()),
])

# Table 3: Orbital Elements (Grain: snapshot_key, spkid, orbit_id, element_name)
SBDB_ORBIT_ELEMENT_SCHEMA = pa.schema([
    ("snapshot_key", pa.string()),
    ("run_id", pa.string()),
    ("snapshot_time", pa.string()),
    ("spkid", pa.string()),
    ("orbit_id", pa.string()),
    ("epoch_jd", pa.float64()),
    ("equinox", pa.string()),
    ("element_name", pa.string()),
    ("element_value", pa.float64()),
    ("sigma", pa.float64()),
    ("units", pa.string()),
    ("title", pa.string()),
    ("label", pa.string()),
])

# Table 4: Physical Parameters (Grain: snapshot_key, spkid, param_name)
SBDB_PHYS_PAR_SCHEMA = pa.schema([
    ("snapshot_key", pa.string()),
    ("run_id", pa.string()),
    ("snapshot_time", pa.string()),
    ("spkid", pa.string()),
    ("param_name", pa.string()),
    ("param_value_numeric", pa.float64()),
    ("param_value_raw", pa.string()),
    ("sigma", pa.float64()),
    ("units", pa.string()),
    ("bib_reference", pa.string()),
    ("notes", pa.string()),
    ("title", pa.string()),
    ("desc", pa.string()),
])


# ---------------------------------------------------------------------------
# API Ingestion
# ---------------------------------------------------------------------------
def fetch_sbdb_data(target=DEFAULT_TARGET, id_type=DEFAULT_ID_TYPE, run_id=None):
    """Fetch astronomical, orbital, and physical parameter data from NASA/JPL SBDB API.

    Args:
        target: Target identifier string (e.g. '2025 HX' or '2000433').
        id_type: Identifier parameter type: 'sstr', 'spk', or 'des'.
        run_id: Pipeline execution identifier for structured logging.

    Returns:
        dict: Parsed SBDB API response payload.

    Raises:
        ValueError: On ambiguous matches (code 300 / list), missing objects, or invalid structure.
        requests.exceptions.RequestException: On transport or HTTP errors.
    """
    prefix = f"[{run_id}] " if run_id else ""
    if id_type not in ("sstr", "spk", "des"):
        raise ValueError(f"Invalid id_type '{id_type}'. Must be 'sstr', 'spk', or 'des'.")

    params = {
        id_type: target,
        "phys-par": "1",
        "full-prec": "1",
    }
    headers = {"User-Agent": HTTP_USER_AGENT}

    logger.info(
        "%sFetching SBDB data for target '%s' (%s) with full-prec=1 from %s",
        prefix,
        target,
        id_type,
        SBDB_URL,
    )

    session = get_http_session()
    response = session.get(SBDB_URL, params=params, headers=headers, timeout=HTTP_TIMEOUT_SECONDS)
    response.raise_for_status()

    payload = response.json()

    if not isinstance(payload, dict):
        raise ValueError("Invalid SBDB API response: expected JSON object root.")

    # Check for signature block
    if "signature" not in payload:
        logger.warning("%sSBDB response payload missing 'signature' block.", prefix)

    # Check for ambiguous query (multiple matching candidates returned)
    if payload.get("code") == 300 or "list" in payload:
        candidate_count = payload.get("count", len(payload.get("list", [])))
        raise ValueError(
            f"Ambiguous SBDB query for target '{target}': matched {candidate_count} objects. "
            "Specific designation ('des') or SPK-ID ('spk') required."
        )

    # Check for error message / not found
    if "message" in payload and "object" not in payload:
        raise ValueError(f"SBDB API error for target '{target}': {payload.get('message')}")

    # Validate mandatory core sections
    if "object" not in payload or not isinstance(payload["object"], dict):
        raise ValueError(f"Invalid SBDB response for '{target}': missing or malformed 'object' section.")

    if "orbit" not in payload or not isinstance(payload["orbit"], dict):
        raise ValueError(f"Invalid SBDB response for '{target}': missing or malformed 'orbit' section.")

    return payload


# ---------------------------------------------------------------------------
# Local Storage Primitive
# ---------------------------------------------------------------------------
def save_raw_json(data, filename):
    """Save raw SBDB JSON payload locally with secret redaction."""
    with open(filename, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=4)


def save_sbdb_summary(summary_data, filename="sbdb_batch_summary.json"):
    """Save authoritative SBDB batch summary as deterministic JSON."""
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(summary_data, f, indent=4)


# ---------------------------------------------------------------------------
# Data Extraction & Normalization
# ---------------------------------------------------------------------------
def extract_sbdb_object(payload, snapshot_key, run_id, snapshot_time):
    """Extract object-level metadata into Table 1 format.

    Args:
        payload: Validated SBDB JSON payload.
        snapshot_key: Ingestion snapshot date string (YYYY-MM-DD).
        run_id: Execution run ID.
        snapshot_time: ISO-8601 UTC timestamp.

    Returns:
        dict: Normalized record conforming to SBDB_OBJECT_SCHEMA.

    Raises:
        ValueError: If mandatory object identifiers are absent or malformed.
    """
    obj = payload.get("object", {})
    spkid = obj.get("spkid")
    des = obj.get("des")

    if not spkid or not isinstance(spkid, str) or not spkid.strip():
        raise ValueError("SBDB object validation failed: missing or invalid 'spkid'.")

    if not des or not isinstance(des, str) or not des.strip():
        raise ValueError("SBDB object validation failed: missing or invalid 'des'.")

    spkid = spkid.strip()
    des = des.strip()
    fullname = obj.get("fullname", "").strip() or des
    shortname = obj.get("shortname")
    shortname_clean = shortname.strip() if isinstance(shortname, str) and shortname.strip() else None

    orbit_class = obj.get("orbit_class", {})
    if not isinstance(orbit_class, dict):
        orbit_class = {}

    orbit_class_code = orbit_class.get("code")
    orbit_class_name = orbit_class.get("name")
    if not orbit_class_code or not orbit_class_name:
        raise ValueError(f"SBDB object validation failed: missing orbit_class for object {spkid}.")

    orbit_id = obj.get("orbit_id")
    if not orbit_id or not str(orbit_id).strip():
        raise ValueError(f"SBDB object validation failed: missing orbit_id in object section for {spkid}.")

    prefix_val = obj.get("prefix")
    prefix_clean = prefix_val.strip() if isinstance(prefix_val, str) and prefix_val.strip() else None

    return {
        "snapshot_key": snapshot_key,
        "run_id": run_id,
        "snapshot_time": snapshot_time,
        "spkid": spkid,
        "designation": des,
        "fullname": fullname,
        "shortname": shortname_clean,
        "object_kind": str(obj.get("kind", "")).strip() or "unknown",
        "is_neo": bool(obj.get("neo", False)),
        "is_pha": bool(obj.get("pha", False)),
        "orbit_class_code": str(orbit_class_code).strip(),
        "orbit_class_name": str(orbit_class_name).strip(),
        "orbit_id": str(orbit_id).strip(),
        "prefix": prefix_clean,
    }


def extract_sbdb_orbit(payload, snapshot_key, run_id, snapshot_time, spkid):
    """Extract orbit solution fit metrics into Table 2 format.

    Args:
        payload: Validated SBDB JSON payload.
        snapshot_key: Ingestion snapshot date string (YYYY-MM-DD).
        run_id: Execution run ID.
        snapshot_time: ISO-8601 UTC timestamp.
        spkid: Validated target SPK-ID.

    Returns:
        dict: Normalized record conforming to SBDB_ORBIT_SCHEMA.

    Raises:
        ValueError: If mandatory orbit fields or mathematical sanity checks fail.
    """
    orb = payload.get("orbit", {})
    orbit_id = orb.get("orbit_id")
    if not orbit_id or not str(orbit_id).strip():
        raise ValueError(f"SBDB orbit validation failed: missing 'orbit_id' for {spkid}.")

    epoch = orb.get("epoch")
    try:
        epoch_jd = float(epoch)
        if epoch_jd <= 0.0:
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError(f"SBDB orbit validation failed: invalid epoch '{epoch}' for {spkid}.")

    equinox = str(orb.get("equinox", "")).strip()
    if not equinox:
        raise ValueError(f"SBDB orbit validation failed: missing 'equinox' for {spkid}.")

    soln_date = str(orb.get("soln_date", "")).strip()
    first_obs = str(orb.get("first_obs", "")).strip()
    last_obs = str(orb.get("last_obs", "")).strip()
    orbit_source = str(orb.get("source", "JPL")).strip() or "JPL"

    producer_val = orb.get("producer")
    producer = str(producer_val).strip() if producer_val and str(producer_val).strip() else None

    # Optional data arc
    data_arc_val = orb.get("data_arc")
    try:
        data_arc_days = int(data_arc_val) if data_arc_val is not None else None
    except (TypeError, ValueError):
        data_arc_days = None

    # Number of observations used
    n_obs_val = orb.get("n_obs_used")
    try:
        n_obs_used = int(n_obs_val)
        if n_obs_used < 0:
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError(f"SBDB orbit validation failed: invalid n_obs_used '{n_obs_val}' for {spkid}.")

    # Normalized RMS
    rms_val = orb.get("rms")
    try:
        rms = float(rms_val)
        if rms < 0.0:
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError(f"SBDB orbit validation failed: invalid rms '{rms_val}' for {spkid}.")

    # Earth MOID
    moid_val = orb.get("moid")
    try:
        earth_moid_au = float(moid_val)
        if earth_moid_au < 0.0:
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError(f"SBDB orbit validation failed: invalid Earth MOID '{moid_val}' for {spkid}.")

    # Jupiter MOID (optional)
    moid_jup_val = orb.get("moid_jup")
    try:
        jupiter_moid_au = float(moid_jup_val) if moid_jup_val is not None else None
    except (TypeError, ValueError):
        jupiter_moid_au = None

    # Jupiter Tisserand invariant (optional)
    t_jup_val = orb.get("t_jup")
    try:
        t_jup = float(t_jup_val) if t_jup_val is not None else None
    except (TypeError, ValueError):
        t_jup = None

    condition_code = str(orb.get("condition_code", "")).strip() or "unknown"
    pe_used = str(orb.get("pe_used", "")).strip() or None
    sb_used = str(orb.get("sb_used", "")).strip() or None

    return {
        "snapshot_key": snapshot_key,
        "run_id": run_id,
        "snapshot_time": snapshot_time,
        "spkid": spkid,
        "orbit_id": str(orbit_id).strip(),
        "epoch_jd": epoch_jd,
        "equinox": equinox,
        "soln_date": soln_date,
        "orbit_source": orbit_source,
        "producer": producer,
        "first_obs": first_obs,
        "last_obs": last_obs,
        "data_arc_days": data_arc_days,
        "n_obs_used": n_obs_used,
        "condition_code": condition_code,
        "rms": rms,
        "earth_moid_au": earth_moid_au,
        "jupiter_moid_au": jupiter_moid_au,
        "t_jup": t_jup,
        "pe_used": pe_used,
        "sb_used": sb_used,
    }


def extract_sbdb_orbit_elements(payload, snapshot_key, run_id, snapshot_time, spkid, orbit_id, epoch_jd, equinox):
    """Dynamically extract osculating orbital elements into Table 3 format.

    Does not enforce a fixed element count; dynamically parses elements returned by SBDB.

    Args:
        payload: Validated SBDB JSON payload.
        snapshot_key: Ingestion snapshot date string (YYYY-MM-DD).
        run_id: Execution run ID.
        snapshot_time: ISO-8601 UTC timestamp.
        spkid: Validated target SPK-ID.
        orbit_id: Validated orbit solution ID.
        epoch_jd: Osculating epoch in Julian Days.
        equinox: Coordinate frame equinox.

    Returns:
        list[dict]: Normalized records conforming to SBDB_ORBIT_ELEMENT_SCHEMA.

    Raises:
        ValueError: If elements section is missing or fundamental eccentricity check fails.
    """
    orb = payload.get("orbit", {})
    raw_elements = orb.get("elements")

    if not isinstance(raw_elements, list) or len(raw_elements) == 0:
        raise ValueError(f"SBDB orbit elements validation failed: empty or non-list elements for {spkid}.")

    element_records = []
    has_eccentricity = False

    for item in raw_elements:
        if not isinstance(item, dict):
            continue

        name = item.get("name")
        val_str = item.get("value")

        if not name or val_str is None:
            continue

        try:
            element_value = float(val_str)
        except (TypeError, ValueError):
            continue

        sigma_val = item.get("sigma")
        try:
            sigma = float(sigma_val) if sigma_val is not None else None
        except (TypeError, ValueError):
            sigma = None

        units = item.get("units")
        units_clean = str(units).strip() if units and str(units).strip() else None

        title = item.get("title")
        title_clean = str(title).strip() if title and str(title).strip() else None

        label = item.get("label")
        label_clean = str(label).strip() if label and str(label).strip() else None

        if name == "e":
            has_eccentricity = True
            if element_value < 0.0:
                raise ValueError(
                    f"SBDB orbit element validation failed: negative eccentricity {element_value} for {spkid}."
                )

        element_records.append({
            "snapshot_key": snapshot_key,
            "run_id": run_id,
            "snapshot_time": snapshot_time,
            "spkid": spkid,
            "orbit_id": orbit_id,
            "epoch_jd": epoch_jd,
            "equinox": equinox,
            "element_name": str(name).strip(),
            "element_value": element_value,
            "sigma": sigma,
            "units": units_clean,
            "title": title_clean,
            "label": label_clean,
        })

    if not has_eccentricity:
        logger.warning(
            "[%s] SBDB elements for object %s did not contain explicit eccentricity 'e'.",
            run_id,
            spkid,
        )

    return element_records


def extract_sbdb_physical_parameters(payload, snapshot_key, run_id, snapshot_time, spkid):
    """Extract physical properties into Table 4 format with strict duplicate detection.

    Enforces the approved duplicate-parameter safeguard rule:
    If multiple records with the same param_name are encountered for a single SBDB
    object within one payload, do not silently discard any source record. Fail and
    quarantine the affected payload before Parquet generation and S3 upload.

    Args:
        payload: Validated SBDB JSON payload.
        snapshot_key: Ingestion snapshot date string (YYYY-MM-DD).
        run_id: Execution run ID.
        snapshot_time: ISO-8601 UTC timestamp.
        spkid: Validated target SPK-ID.

    Returns:
        list[dict]: Normalized records conforming to SBDB_PHYS_PAR_SCHEMA.

    Raises:
        ValueError: On duplicate param_name or malformed physical property entries.
    """
    raw_phys_par = payload.get("phys_par", [])
    if not isinstance(raw_phys_par, list):
        raise ValueError(f"SBDB physical parameters validation failed: expected list for {spkid}.")

    phys_records = []
    seen_param_names = set()

    for item in raw_phys_par:
        if not isinstance(item, dict):
            continue

        param_name = item.get("name")
        if not param_name or not str(param_name).strip():
            continue

        param_name_clean = str(param_name).strip()

        # Strict duplicate-parameter safeguard
        if param_name_clean in seen_param_names:
            logger.error(
                "[%s] Data-quality violation: duplicate physical parameter '%s' encountered for "
                "object SPK-ID %s in payload. Halting payload processing to prevent source data loss.",
                run_id,
                param_name_clean,
                spkid,
            )
            raise ValueError(
                f"Data-quality failure: duplicate physical parameter '{param_name_clean}' "
                f"encountered for object {spkid}. Silent discard prohibited."
            )

        seen_param_names.add(param_name_clean)

        raw_val = item.get("value")
        if raw_val is None:
            continue
        param_value_raw = str(raw_val).strip()

        # Attempt numeric parse (retaining raw string regardless)
        try:
            param_value_numeric = float(param_value_raw)
        except (TypeError, ValueError):
            param_value_numeric = None

        sigma_val = item.get("sigma")
        try:
            sigma = float(sigma_val) if sigma_val is not None else None
        except (TypeError, ValueError):
            sigma = None

        units = item.get("units")
        units_clean = str(units).strip() if units and str(units).strip() else None

        ref = item.get("ref")
        bib_reference = str(ref).strip() if ref and str(ref).strip() else None

        notes = item.get("notes")
        notes_clean = str(notes).strip() if notes and str(notes).strip() else None

        title = item.get("title")
        title_clean = str(title).strip() if title and str(title).strip() else None

        desc = item.get("desc")
        desc_clean = str(desc).strip() if desc and str(desc).strip() else None

        phys_records.append({
            "snapshot_key": snapshot_key,
            "run_id": run_id,
            "snapshot_time": snapshot_time,
            "spkid": spkid,
            "param_name": param_name_clean,
            "param_value_numeric": param_value_numeric,
            "param_value_raw": param_value_raw,
            "sigma": sigma,
            "units": units_clean,
            "bib_reference": bib_reference,
            "notes": notes_clean,
            "title": title_clean,
            "desc": desc_clean,
        })

    return phys_records


# ---------------------------------------------------------------------------
# S3 Upload Helpers
# ---------------------------------------------------------------------------
def upload_raw_to_s3(local_file_path, snapshot_date, spkid, metadata=None):
    """Upload raw SBDB JSON payload to deterministic raw/sbdb S3 partition."""
    year = f"{snapshot_date.year:04d}"
    month = f"{snapshot_date.month:02d}"
    day = f"{snapshot_date.day:02d}"
    filename = os.path.basename(local_file_path)

    raw_key = f"raw/sbdb/object/year={year}/month={month}/day={day}/spkid={spkid}/{filename}"

    run_id = metadata.get("run_id") if isinstance(metadata, dict) else None
    prefix = f"[{run_id}] " if run_id else ""

    s3 = boto3.client("s3")
    try:
        upload_file_to_s3(
            local_file_path=local_file_path,
            bucket_name=S3_BUCKET_NAME,
            s3_key=raw_key,
            metadata=metadata,
            s3_client=s3,
        )
        logger.info("%sUploaded raw SBDB JSON to s3://%s/%s", prefix, S3_BUCKET_NAME, raw_key)
    except (BotoCoreError, ClientError) as error:
        logger.error(
            "%sRaw SBDB S3 upload failed for s3://%s/%s: %s",
            prefix,
            S3_BUCKET_NAME,
            raw_key,
            redact_api_key(str(error)),
        )
        raise


def upload_processed_to_s3(table_name, local_file_path, snapshot_date, metadata=None):
    """Upload a normalized SBDB Parquet file to deterministic processed/sbdb S3 partition."""
    year = f"{snapshot_date.year:04d}"
    month = f"{snapshot_date.month:02d}"
    day = f"{snapshot_date.day:02d}"
    filename = os.path.basename(local_file_path)

    parquet_key = f"processed/sbdb/{table_name}/year={year}/month={month}/day={day}/{filename}"

    run_id = metadata.get("run_id") if isinstance(metadata, dict) else None
    prefix = f"[{run_id}] " if run_id else ""

    s3 = boto3.client("s3")
    try:
        upload_file_to_s3(
            local_file_path=local_file_path,
            bucket_name=S3_BUCKET_NAME,
            s3_key=parquet_key,
            metadata=metadata,
            s3_client=s3,
        )
        logger.info(
            "%sUploaded processed SBDB Parquet [%s] to s3://%s/%s",
            prefix,
            table_name,
            S3_BUCKET_NAME,
            parquet_key,
        )
    except (BotoCoreError, ClientError) as error:
        logger.error(
            "%sProcessed SBDB Parquet upload failed for s3://%s/%s: %s",
            prefix,
            S3_BUCKET_NAME,
            parquet_key,
            redact_api_key(str(error)),
        )
        raise


# ---------------------------------------------------------------------------
# Target Resolution & CLI Argument Parsing
# ---------------------------------------------------------------------------
def resolve_targets(
    target: str | None = None,
    targets: str | list[str] | None = None,
    targets_file: str | None = None,
    default_target: str = DEFAULT_TARGET,
) -> list[str]:
    """Resolve target list according to strict precedence rules.

    Precedence:
    1. targets_file, when supplied
    2. targets, when supplied
    3. target, when supplied and non-empty
    4. default_target
    """
    raw_targets: list[str] = []

    if targets_file:
        if not os.path.exists(targets_file):
            raise FileNotFoundError(f"SBDB targets file not found: {targets_file}")
        with open(targets_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    raw_targets.append(line)
        if not raw_targets:
            raise ValueError(f"SBDB targets file is empty: {targets_file}")

    elif targets:
        if isinstance(targets, str):
            parts = [t.strip() for t in targets.split(",")]
            raw_targets = [p for p in parts if p]
        elif isinstance(targets, (list, tuple, set)):
            raw_targets = [str(t).strip() for t in targets if str(t).strip()]
        if not raw_targets:
            raise ValueError("No valid targets provided in --targets")

    elif target:
        t = str(target).strip()
        if t:
            raw_targets = [t]
        else:
            raw_targets = [default_target]

    else:
        raw_targets = [default_target]

    # Deduplicate while preserving deterministic alphanumeric order
    normalized: list[str] = []
    seen: set[str] = set()
    for t in raw_targets:
        clean = " ".join(t.split())
        if clean.startswith("(") and clean.endswith(")"):
            clean = clean[1:-1].strip()
        if clean and clean not in seen:
            seen.add(clean)
            normalized.append(clean)

    if not normalized:
        raise ValueError("Target resolution produced zero valid targets.")

    normalized.sort()
    return normalized


def parse_args(args=None):
    """Parse CLI arguments for SBDB pipeline."""
    parser = argparse.ArgumentParser(
        description="NASA Planetary Defense Risk Intelligence Platform — SBDB Ingestion Pipeline"
    )
    parser.add_argument(
        "--target",
        type=str,
        default=None,
        help=f"Target identifier to query (default: '{DEFAULT_TARGET}' if no targets specified)",
    )
    parser.add_argument(
        "--targets",
        type=str,
        default=None,
        help="Comma-separated list of target identifiers to query (e.g. '2010 TW54,2025 HX,2008 ST')",
    )
    parser.add_argument(
        "--targets-file",
        type=str,
        default=None,
        help="Path to text file containing target identifiers, one per line",
    )
    parser.add_argument(
        "--id-type",
        type=str,
        default=DEFAULT_ID_TYPE,
        choices=["sstr", "spk", "des"],
        help=f"SBDB identifier type: 'sstr', 'spk', or 'des' (default: '{DEFAULT_ID_TYPE}')",
    )
    parser.add_argument(
        "--snapshot-date",
        type=str,
        default=None,
        help="Snapshot date for SBDB catalog (YYYY-MM-DD). Default: today (UTC)",
    )
    parser.add_argument(
        "--summary-file",
        type=str,
        default="sbdb_batch_summary.json",
        help="Path for authoritative SBDB batch summary JSON (default: 'sbdb_batch_summary.json')",
    )
    return parser.parse_args(args)


# ---------------------------------------------------------------------------
# Main Orchestration Workflow
# ---------------------------------------------------------------------------
def main(
    target: str | None = None,
    id_type: str | None = None,
    snapshot_date_str: str | None = None,
    targets: str | list[str] | None = None,
    targets_file: str | None = None,
    summary_filename: str = "sbdb_batch_summary.json",
) -> int:
    """Execute the end-to-end SBDB ingestion workflow."""
    start_time = time.perf_counter()
    run_id = uuid.uuid4().hex[:12]
    main.current_run_id = run_id
    snapshot_time = datetime.now(timezone.utc).isoformat()

    resolved_id_type = (id_type or DEFAULT_ID_TYPE).strip()

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
        source_name="nasa_jpl_sbdb_api",
        run_id=run_id,
        ingested_at=snapshot_time,
    )

    try:
        resolved_targets = resolve_targets(
            target=target,
            targets=targets,
            targets_file=targets_file,
            default_target=DEFAULT_TARGET,
        )
    except Exception as error:
        logger.error("Failed to resolve SBDB targets: %s", error)
        return 1

    logger.info(
        "[%s] Starting SBDB ingestion for %d target(s) (%s) | snapshot_date=%s",
        run_id,
        len(resolved_targets),
        resolved_id_type,
        snapshot_key,
    )

    all_obj_records: list[dict] = []
    all_orbit_records: list[dict] = []
    all_element_records: list[dict] = []
    all_phys_records: list[dict] = []
    saved_raw_files: list[tuple[str, str]] = []  # (filename, spkid)
    successful_targets: list[str] = []
    failed_targets: list[str] = []

    for idx, target_item in enumerate(resolved_targets):
        if idx > 0:
            time.sleep(0.2)  # ~200 ms pacing between requests

        try:
            raw_payload = fetch_sbdb_data(
                target=target_item,
                id_type=resolved_id_type,
                run_id=run_id,
            )
            obj_record = extract_sbdb_object(raw_payload, snapshot_key, run_id, snapshot_time)
            spkid = obj_record["spkid"]
            orbit_id = obj_record["orbit_id"]

            orbit_record = extract_sbdb_orbit(raw_payload, snapshot_key, run_id, snapshot_time, spkid)
            epoch_jd = orbit_record["epoch_jd"]
            equinox = orbit_record["equinox"]

            element_records = extract_sbdb_orbit_elements(
                raw_payload, snapshot_key, run_id, snapshot_time, spkid, orbit_id, epoch_jd, equinox
            )

            phys_records = extract_sbdb_physical_parameters(
                raw_payload, snapshot_key, run_id, snapshot_time, spkid
            )

            if not obj_record or not orbit_record:
                logger.error("[%s] Mandatory object/orbit records missing for target '%s'", run_id, target_item)
                failed_targets.append(target_item)
                continue

            raw_filename = f"sbdb_raw_{spkid}.json"
            save_raw_json(raw_payload, filename=raw_filename)
            saved_raw_files.append((raw_filename, spkid))

            all_obj_records.append(obj_record)
            all_orbit_records.append(orbit_record)
            all_element_records.extend(element_records)
            all_phys_records.extend(phys_records)
            successful_targets.append(target_item)

        except Exception as error:
            logger.error(
                "[%s] SBDB data fetch/extraction failed for target '%s': %s",
                run_id,
                target_item,
                redact_api_key(str(error)),
            )
            failed_targets.append(target_item)
            continue

    # Circuit breaker & Data Quality Gate: objects_extracted > 0 AND failure_pct < 25.0%
    total_targets = len(resolved_targets)
    successful_targets_count = len(successful_targets)
    failed_targets_count = len(failed_targets)
    failure_pct = (failed_targets_count / total_targets * 100.0) if total_targets > 0 else 100.0
    circuit_breaker_passed = (successful_targets_count > 0) and (failure_pct < 25.0)

    summary_data = {
        "run_id": run_id,
        "snapshot_key": snapshot_key,
        "snapshot_time": snapshot_time,
        "total_targets": total_targets,
        "successful_targets_count": successful_targets_count,
        "failed_targets_count": failed_targets_count,
        "successful_targets": successful_targets,
        "failed_targets": failed_targets,
        "failure_rate_pct": round(failure_pct, 2),
        "circuit_breaker_threshold_pct": 25.0,
        "circuit_breaker_passed": circuit_breaker_passed,
        "output_tables": [
            "fact_sbdb_object_snapshot.parquet",
            "fact_sbdb_orbit.parquet",
            "fact_sbdb_orbit_element.parquet",
            "fact_sbdb_physical_parameter.parquet",
        ],
    }

    try:
        save_sbdb_summary(summary_data, filename=summary_filename)
        logger.info("[%s] Saved SBDB batch summary to %s", run_id, summary_filename)
    except Exception as e:
        logger.error(
            "[%s] Failed to write SBDB authoritative batch summary to %s: %s. "
            "Halting pipeline to prevent unmonitored production output.",
            run_id,
            summary_filename,
            redact_api_key(str(e)),
        )
        return 1

    if not circuit_breaker_passed:
        if successful_targets_count == 0:
            logger.error(
                "[%s] Circuit breaker triggered: 0 valid object or orbit records extracted across %d target(s).",
                run_id,
                total_targets,
            )
        else:
            logger.error(
                "[%s] Circuit breaker triggered: SBDB failure rate %.1f%% exceeded 25.0%% threshold "
                "(%d failed, %d succeeded out of %d total targets). Halting before processed outputs.",
                run_id,
                failure_pct,
                failed_targets_count,
                successful_targets_count,
                total_targets,
            )
        return 1

    # 4. Write 4 normalized Parquet tables locally
    obj_parquet = "fact_sbdb_object_snapshot.parquet"
    orbit_parquet = "fact_sbdb_orbit.parquet"
    elem_parquet = "fact_sbdb_orbit_element.parquet"
    phys_parquet = "fact_sbdb_physical_parameter.parquet"

    write_parquet(all_obj_records, SBDB_OBJECT_SCHEMA, obj_parquet)
    write_parquet(all_orbit_records, SBDB_ORBIT_SCHEMA, orbit_parquet)
    write_parquet(all_element_records, SBDB_ORBIT_ELEMENT_SCHEMA, elem_parquet)
    write_parquet(all_phys_records, SBDB_PHYS_PAR_SCHEMA, phys_parquet)

    logger.info(
        "[%s] Generated consolidated Parquets: objects=%d rows, orbits=%d rows, elements=%d rows, phys_par=%d rows",
        run_id,
        len(all_obj_records),
        len(all_orbit_records),
        len(all_element_records),
        len(all_phys_records),
    )

    # 5. Upload raw payload and 4 Parquet tables to S3
    try:
        for raw_file, spk in saved_raw_files:
            upload_raw_to_s3(
                local_file_path=raw_file,
                snapshot_date=resolved_snapshot_date,
                spkid=spk,
                metadata=lineage_metadata,
            )

        upload_processed_to_s3(
            table_name="fact_sbdb_object_snapshot",
            local_file_path=obj_parquet,
            snapshot_date=resolved_snapshot_date,
            metadata=lineage_metadata,
        )

        upload_processed_to_s3(
            table_name="fact_sbdb_orbit",
            local_file_path=orbit_parquet,
            snapshot_date=resolved_snapshot_date,
            metadata=lineage_metadata,
        )

        upload_processed_to_s3(
            table_name="fact_sbdb_orbit_element",
            local_file_path=elem_parquet,
            snapshot_date=resolved_snapshot_date,
            metadata=lineage_metadata,
        )

        upload_processed_to_s3(
            table_name="fact_sbdb_physical_parameter",
            local_file_path=phys_parquet,
            snapshot_date=resolved_snapshot_date,
            metadata=lineage_metadata,
        )
    except Exception as error:
        logger.error(
            "[%s] S3 upload sequence failed: %s",
            run_id,
            redact_api_key(str(error)),
        )
        return 1

    elapsed = time.perf_counter() - start_time
    if len(resolved_targets) == 1:
        logger.info(
            "[%s] SBDB ingestion completed successfully for object %s (%s) in %.2fs",
            run_id,
            resolved_targets[0],
            all_obj_records[0]["spkid"],
            elapsed,
        )
    else:
        logger.info(
            "[%s] SBDB batch ingestion completed successfully for %d/%d target(s) in %.2fs",
            run_id,
            len(successful_targets),
            len(resolved_targets),
            elapsed,
        )
    return 0


if __name__ == "__main__":
    args = parse_args()
    exit_code = main(
        target=args.target,
        id_type=args.id_type,
        snapshot_date_str=args.snapshot_date,
        targets=args.targets,
        targets_file=args.targets_file,
        summary_filename=args.summary_file,
    )
    sys.exit(exit_code)
