#!/usr/bin/env python3
"""NASA Planetary Defense Risk Intelligence Platform — Entity Resolution Engine.

Implements M5 Phase 6 multi-source entity resolution, establishing deterministic
crosswalk linkages across NeoWs close-approach records, Small-Body Database (SBDB)
astronomical snapshots, and Sentry Mode S risk assessments.

Grains:
- bridge_asteroid_identifier:
    (asteroid_key, source_system, identifier_name, identifier_value)
- fact_entity_resolution:
    (resolution_run_id, source_system, identifier_name, source_identifier_value)

Key Invariant:
    (source_system, identifier_name, identifier_value) -> at most one asteroid_key
"""

import argparse
from collections import defaultdict
from datetime import date, datetime, timezone
import json
import logging
import os
import re
import sys
import time
import uuid

import boto3
from botocore.exceptions import BotoCoreError, ClientError
import pyarrow as pa
import pyarrow.parquet as pq

from pipeline_utils import (
    build_lineage_metadata,
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
# Fixed platform namespace UUID for deterministic canonical asteroid_key generation
NAMESPACE_PLANETARY_DEFENSE = uuid.UUID("e7b8c9d0-1234-5678-9abc-def012345678")
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME", "nasa-asteroid-intelligence")
REFERENCE_BASE_KEY = "reference/asteroid_crosswalk"

# Resolution States (Locked Contract)
STATE_RESOLVED = "RESOLVED"
STATE_UNRESOLVED = "UNRESOLVED"
STATE_AMBIGUOUS = "AMBIGUOUS"
STATE_INVALID = "INVALID"

# Deterministic Matching Rules (Locked Contract)
RULE_CANONICAL_EXTERNAL_PIVOT = "CANONICAL_EXTERNAL_PIVOT"
RULE_CANONICAL_ASSOCIATED_ID = "CANONICAL_ASSOCIATED_IDENTIFIER"
RULE_EXACT_SPKID = "EXACT_SPKID_MATCH"
RULE_EXACT_DESIGNATION = "EXACT_DESIGNATION_MATCH"
RULE_EXACT_FULLNAME = "EXACT_FULLNAME_MATCH"
RULE_ALTERNATE_DESIGNATION = "ALTERNATE_DESIGNATION_MATCH"
RULE_ASSOCIATED_NEOWS_NAME = "ASSOCIATED_NAME_VIA_SPKID"
RULE_ASSOCIATED_SENTRY_ID = "ASSOCIATED_SENTRY_ID_VIA_DESIGNATION"
RULE_ASSOCIATED_SENTRY_NAME = "ASSOCIATED_NAME_VIA_DESIGNATION"
RULE_NO_MATCH = "NO_CROSS_SOURCE_MATCH"
RULE_COLLISION_QUARANTINE = "DUPLICATE_COLLISION_QUARANTINE"
RULE_CONTRADICTORY_CANDIDATE = "CONTRADICTORY_CANDIDATE_QUARANTINE"
RULE_INVALID_IDENTIFIER = "INVALID_IDENTIFIER"

# ---------------------------------------------------------------------------
# PyArrow Schemas (Locked Contract)
# ---------------------------------------------------------------------------

# Table 1: Crosswalk Bridge
# Grain: (asteroid_key, source_system, identifier_name, identifier_value)
BRIDGE_ASTEROID_IDENTIFIER_SCHEMA = pa.schema([
    ("asteroid_key", pa.string()),
    ("source_system", pa.string()),
    ("identifier_name", pa.string()),
    ("identifier_value", pa.string()),
    ("is_primary_pivot", pa.bool_()),
    ("created_at", pa.string()),
    ("updated_at", pa.string()),
])

# Table 2: Resolution Audit Trail
# Grain: (resolution_run_id, source_system, identifier_name, source_identifier_value)
FACT_ENTITY_RESOLUTION_SCHEMA = pa.schema([
    ("resolution_run_id", pa.string()),
    ("resolved_at", pa.string()),
    ("source_system", pa.string()),
    ("identifier_name", pa.string()),
    ("source_identifier_value", pa.string()),
    ("matched_target_system", pa.string()),
    ("matched_target_identifier_name", pa.string()),
    ("matched_target_identifier_value", pa.string()),
    ("assigned_asteroid_key", pa.string()),
    ("match_state", pa.string()),
    ("match_rule", pa.string()),
    ("evidence_json", pa.string()),
])


# ---------------------------------------------------------------------------
# Deterministic Normalization & Key Generation
# ---------------------------------------------------------------------------

def normalize_whitespace(text: str) -> str:
    """Trim surrounding whitespace and collapse internal whitespace."""
    if not isinstance(text, str):
        return ""
    return re.sub(r"\s+", " ", text.strip())


def normalize_designation(text: str) -> str:
    """Deterministic designation normalization for secondary cross-source matching.

    1. Trim surrounding whitespace and collapse internal whitespace.
    2. Strip outer enclosing parentheses if the entire string is wrapped.
    3. Convert to uppercase.

    Normalization is strictly deterministic and does NOT modify stored raw values.
    """
    if not isinstance(text, str):
        return ""
    cleaned = normalize_whitespace(text)
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = cleaned[1:-1].strip()
        cleaned = normalize_whitespace(cleaned)
    return cleaned.upper()


def generate_asteroid_key(canonical_spkid: str) -> str:
    """Generate a deterministic, collision-resistant canonical asteroid key.

    Uses standard UUIDv5 (SHA-1 hashing over fixed platform namespace)
    producing a full UUID5 representation providing a large deterministic,
    collision-resistant identifier space.
    """
    clean_spkid = str(canonical_spkid).strip()
    return f"ast_{uuid.uuid5(NAMESPACE_PLANETARY_DEFENSE, f'spk:{clean_spkid}')}"


# ---------------------------------------------------------------------------
# Core Entity Resolution Algorithm
# ---------------------------------------------------------------------------

def resolve_entities(
    neows_records: list[dict] | None = None,
    sbdb_records: list[dict] | None = None,
    sentry_records: list[dict] | None = None,
    run_id: str | None = None,
    resolved_at: str | None = None,
) -> tuple[list[dict], list[dict], dict]:
    """Execute deterministic multi-source entity resolution.

    Resolves NeoWs and Sentry records to canonical SBDB asteroid entities using:
    1. Primary match rule: NeoWs.id == SBDB.spkid (EXACT_SPKID_MATCH)
    2. Secondary match rule: NeoWs.name == SBDB.des (EXACT_DESIGNATION_MATCH)
    3. Fallback, only when rule 2 finds no SBDB entity, using SBDB's own identity fields:
       NeoWs.name == SBDB.fullname (EXACT_FULLNAME_MATCH; e.g. numbered "138971 (2001 CB21)")
       or NeoWs.name in SBDB.alternate_designations (ALTERNATE_DESIGNATION_MATCH; e.g. a later
       provisional designation SBDB links to an earlier one). Candidates that point to more than
       one SPK-ID are quarantined as AMBIGUOUS, exactly like rule 2.
    4. Secondary match rule: Sentry.des == SBDB.des (EXACT_DESIGNATION_MATCH)
    5. Global DQ invariant: (source_system, identifier_name, identifier_value) -> at most one asteroid_key

    Returns:
        (bridge_records, fact_resolution_records, metrics_dict)
    """
    start_time = time.perf_counter()
    run_id = run_id or uuid.uuid4().hex[:12]
    resolved_at = resolved_at or datetime.now(timezone.utc).isoformat()

    neows_list = neows_records or []
    sbdb_list = sbdb_records or []
    sentry_list = sentry_records or []

    logger.info(
        "[%s] Starting entity resolution | inputs: NeoWs=%d, SBDB=%d, Sentry=%d",
        run_id,
        len(neows_list),
        len(sbdb_list),
        len(sentry_list),
    )

    candidate_bridge_rows: list[dict] = []
    audit_records: list[dict] = []

    # Indexes for the canonical hub (SBDB)
    sbdb_by_spkid: dict[str, dict] = {}
    sbdb_by_norm_des: dict[str, list[dict]] = defaultdict(list)
    # Fallback identity indexes (exact, normalized): SBDB full name and SBDB alternate designations.
    sbdb_by_norm_fullname: dict[str, list[dict]] = defaultdict(list)
    sbdb_by_norm_alt_des: dict[str, list[dict]] = defaultdict(list)
    conflicting_sbdb_spkids: set[str] = set()

    # -----------------------------------------------------------------------
    # Step 1: Ingest & Index SBDB Object Snapshots (Canonical Hub)
    # -----------------------------------------------------------------------
    # Deduplicate input SBDB records by (spkid, designation, fullname)
    unique_sbdb_input: dict[tuple, dict] = {}
    for r in sbdb_list:
        if not isinstance(r, dict):
            continue
        key_tuple = (r.get("spkid"), r.get("designation"), r.get("fullname"))
        if key_tuple not in unique_sbdb_input:
            unique_sbdb_input[key_tuple] = r

    for record in unique_sbdb_input.values():
        raw_spkid = record.get("spkid")
        if raw_spkid is None or not str(raw_spkid).strip():
            audit_records.append({
                "resolution_run_id": run_id,
                "resolved_at": resolved_at,
                "source_system": "sbdb",
                "identifier_name": "spkid",
                "source_identifier_value": str(raw_spkid or ""),
                "matched_target_system": None,
                "matched_target_identifier_name": None,
                "matched_target_identifier_value": None,
                "assigned_asteroid_key": None,
                "match_state": STATE_INVALID,
                "match_rule": RULE_INVALID_IDENTIFIER,
                "evidence_json": json.dumps({"error": "Missing or empty SBDB SPK-ID"}, sort_keys=True),
            })
            continue

        clean_spkid = str(raw_spkid).strip()
        canonical_key = generate_asteroid_key(clean_spkid)

        raw_des = record.get("designation")
        clean_des = str(raw_des).strip() if raw_des is not None and str(raw_des).strip() else None
        norm_des = normalize_designation(clean_des) if clean_des else None

        raw_fullname = record.get("fullname")
        clean_fullname = str(raw_fullname).strip() if raw_fullname is not None and str(raw_fullname).strip() else None

        # Check for SBDB internal collisions on spkid with conflicting data
        if clean_spkid in sbdb_by_spkid:
            existing = sbdb_by_spkid[clean_spkid]
            if existing["designation"] != clean_des:
                logger.error(
                    "[%s] Collision in SBDB input: SPK-ID %s associated with multiple designations: '%s' vs '%s'",
                    run_id,
                    clean_spkid,
                    existing["designation"],
                    clean_des,
                )
                conflicting_sbdb_spkids.add(clean_spkid)

        entity = {
            "spkid": clean_spkid,
            "asteroid_key": canonical_key,
            "designation": clean_des,
            "norm_des": norm_des,
            "fullname": clean_fullname,
        }
        sbdb_by_spkid[clean_spkid] = entity
        if norm_des:
            sbdb_by_norm_des[norm_des].append(entity)
        if clean_fullname:
            sbdb_by_norm_fullname[normalize_designation(clean_fullname)].append(entity)
        for alt_des in record.get("alternate_designations") or []:
            norm_alt = normalize_designation(alt_des) if isinstance(alt_des, str) else ""
            if norm_alt and norm_alt != norm_des:
                sbdb_by_norm_alt_des[norm_alt].append(entity)

        # Primary pivot audit & bridge
        spkid_evidence = {"pivot": "canonical_external_pivot", "spkid": clean_spkid}
        if clean_fullname:
            spkid_evidence["fullname"] = clean_fullname

        audit_records.append({
            "resolution_run_id": run_id,
            "resolved_at": resolved_at,
            "source_system": "sbdb",
            "identifier_name": "spkid",
            "source_identifier_value": clean_spkid,
            "matched_target_system": "sbdb",
            "matched_target_identifier_name": "spkid",
            "matched_target_identifier_value": clean_spkid,
            "assigned_asteroid_key": canonical_key,
            "match_state": STATE_RESOLVED,
            "match_rule": RULE_CANONICAL_EXTERNAL_PIVOT,
            "evidence_json": json.dumps(spkid_evidence, sort_keys=True),
        })
        candidate_bridge_rows.append({
            "asteroid_key": canonical_key,
            "source_system": "sbdb",
            "identifier_name": "spkid",
            "identifier_value": clean_spkid,
            "is_primary_pivot": True,
            "created_at": resolved_at,
            "updated_at": resolved_at,
        })

        # Associated designation audit & bridge
        if clean_des:
            des_evidence = {"associated_with_spkid": clean_spkid}
            if clean_fullname:
                des_evidence["fullname"] = clean_fullname

            audit_records.append({
                "resolution_run_id": run_id,
                "resolved_at": resolved_at,
                "source_system": "sbdb",
                "identifier_name": "des",
                "source_identifier_value": clean_des,
                "matched_target_system": "sbdb",
                "matched_target_identifier_name": "spkid",
                "matched_target_identifier_value": clean_spkid,
                "assigned_asteroid_key": canonical_key,
                "match_state": STATE_RESOLVED,
                "match_rule": RULE_CANONICAL_ASSOCIATED_ID,
                "evidence_json": json.dumps(des_evidence, sort_keys=True),
            })
            candidate_bridge_rows.append({
                "asteroid_key": canonical_key,
                "source_system": "sbdb",
                "identifier_name": "des",
                "identifier_value": clean_des,
                "is_primary_pivot": False,
                "created_at": resolved_at,
                "updated_at": resolved_at,
            })

    # -----------------------------------------------------------------------
    # Step 2: Resolve NeoWs Entities via Primary Bridge (NeoWs.id == SBDB.spkid)
    # -----------------------------------------------------------------------
    # Deduplicate NeoWs records by (id, name)
    unique_neows_input: dict[tuple, dict] = {}
    for r in neows_list:
        if not isinstance(r, dict):
            continue
        key_tuple = (r.get("id"), r.get("name"))
        if key_tuple not in unique_neows_input:
            unique_neows_input[key_tuple] = r

    for record in unique_neows_input.values():
        raw_id = record.get("id")
        if raw_id is None or not str(raw_id).strip():
            audit_records.append({
                "resolution_run_id": run_id,
                "resolved_at": resolved_at,
                "source_system": "neows",
                "identifier_name": "id",
                "source_identifier_value": str(raw_id or ""),
                "matched_target_system": None,
                "matched_target_identifier_name": None,
                "matched_target_identifier_value": None,
                "assigned_asteroid_key": None,
                "match_state": STATE_INVALID,
                "match_rule": RULE_INVALID_IDENTIFIER,
                "evidence_json": json.dumps({"error": "Missing or empty NeoWs asteroid ID"}, sort_keys=True),
            })
            continue

        clean_id = str(raw_id).strip()
        raw_name = record.get("name")
        clean_name = str(raw_name).strip() if raw_name is not None and str(raw_name).strip() else None

        # Check if ID exists in canonical SBDB hub
        if clean_id in sbdb_by_spkid and clean_id not in conflicting_sbdb_spkids:
            matched_entity = sbdb_by_spkid[clean_id]
            target_key = matched_entity["asteroid_key"]
            target_spkid = matched_entity["spkid"]

            # Contradiction check (Conflict Scenario C): Does NeoWs name match a DIFFERENT SBDB object?
            has_contradiction = False
            contradicting_spkids: list[str] = []
            if clean_name:
                norm_name = normalize_designation(clean_name)
                if norm_name in sbdb_by_norm_des:
                    cand_entities = sbdb_by_norm_des[norm_name]
                    diff_entities = [c for c in cand_entities if c["spkid"] != clean_id]
                    if diff_entities:
                        has_contradiction = True
                        contradicting_spkids = [c["spkid"] for c in diff_entities]

            if has_contradiction:
                # Contradictory candidates: mark AMBIGUOUS, quarantine, write zero bridge rows
                neows_contra_evidence = {
                    "error": "NeoWs ID and designation point to contradictory SBDB objects",
                    "id_spkid": clean_id,
                    "contradicting_spkids": contradicting_spkids,
                }
                if clean_name:
                    neows_contra_evidence["name"] = clean_name

                audit_records.append({
                    "resolution_run_id": run_id,
                    "resolved_at": resolved_at,
                    "source_system": "neows",
                    "identifier_name": "id",
                    "source_identifier_value": clean_id,
                    "matched_target_system": "sbdb",
                    "matched_target_identifier_name": "spkid",
                    "matched_target_identifier_value": clean_id,
                    "assigned_asteroid_key": None,
                    "match_state": STATE_AMBIGUOUS,
                    "match_rule": RULE_CONTRADICTORY_CANDIDATE,
                    "evidence_json": json.dumps(neows_contra_evidence, sort_keys=True),
                })
            else:
                # Match succeeded
                neows_evidence = {"matched_spkid": target_spkid, "pivot_match": "exact_spkid"}
                if clean_name:
                    neows_evidence["name"] = clean_name

                audit_records.append({
                    "resolution_run_id": run_id,
                    "resolved_at": resolved_at,
                    "source_system": "neows",
                    "identifier_name": "id",
                    "source_identifier_value": clean_id,
                    "matched_target_system": "sbdb",
                    "matched_target_identifier_name": "spkid",
                    "matched_target_identifier_value": target_spkid,
                    "assigned_asteroid_key": target_key,
                    "match_state": STATE_RESOLVED,
                    "match_rule": RULE_EXACT_SPKID,
                    "evidence_json": json.dumps(neows_evidence, sort_keys=True),
                })
                candidate_bridge_rows.append({
                    "asteroid_key": target_key,
                    "source_system": "neows",
                    "identifier_name": "id",
                    "identifier_value": clean_id,
                    "is_primary_pivot": False,
                    "created_at": resolved_at,
                    "updated_at": resolved_at,
                })

        else:
            # Clean ID is not in SBDB SPK-ID index: attempt secondary exact designation match
            norm_name = normalize_designation(clean_name) if clean_name else None
            candidates = sbdb_by_norm_des.get(norm_name, []) if norm_name else []
            secondary_rule = RULE_EXACT_DESIGNATION
            matched_field = "des"

            if not candidates and norm_name:
                # Fallback: SBDB full name, then SBDB alternate designations (exact, normalized).
                by_fullname = sbdb_by_norm_fullname.get(norm_name, [])
                by_alt = sbdb_by_norm_alt_des.get(norm_name, [])
                distinct = {c["spkid"]: c for c in [*by_fullname, *by_alt]}
                candidates = list(distinct.values())
                if by_fullname:
                    secondary_rule, matched_field = RULE_EXACT_FULLNAME, "fullname"
                elif by_alt:
                    secondary_rule, matched_field = RULE_ALTERNATE_DESIGNATION, "alternate_designation"

            if len(candidates) == 1:
                # Case A: Exactly one SBDB entity matches
                matched_entity = candidates[0]
                target_key = matched_entity["asteroid_key"]
                target_spkid = matched_entity["spkid"]
                target_sbdb_des = matched_entity["designation"]

                neows_des_evidence = {
                    "matched_sbdb_des": target_sbdb_des,
                    "matched_spkid": target_spkid,
                    "name": clean_name,
                    "normalized_designation": norm_name,
                    "resolution_rule": secondary_rule,
                }
                if matched_field != "des":
                    neows_des_evidence["matched_sbdb_field"] = matched_field

                audit_records.append({
                    "resolution_run_id": run_id,
                    "resolved_at": resolved_at,
                    "source_system": "neows",
                    "identifier_name": "id",
                    "source_identifier_value": clean_id,
                    "matched_target_system": "sbdb",
                    "matched_target_identifier_name": "des",
                    "matched_target_identifier_value": target_sbdb_des,
                    "assigned_asteroid_key": target_key,
                    "match_state": STATE_RESOLVED,
                    "match_rule": secondary_rule,
                    "evidence_json": json.dumps(neows_des_evidence, sort_keys=True),
                })
                candidate_bridge_rows.append({
                    "asteroid_key": target_key,
                    "source_system": "neows",
                    "identifier_name": "id",
                    "identifier_value": clean_id,
                    "is_primary_pivot": False,
                    "created_at": resolved_at,
                    "updated_at": resolved_at,
                })

            elif len(candidates) > 1:
                # Case B: Multiple SBDB entities match normalized designation: AMBIGUOUS collision quarantine
                cand_spkids = [c["spkid"] for c in candidates]
                cand_keys = [c["asteroid_key"] for c in candidates]
                logger.warning(
                    "[%s] NeoWs designation '%s' (%s) matches multiple SBDB entities: SPK-IDs=%s",
                    run_id,
                    clean_name,
                    norm_name,
                    cand_spkids,
                )
                amb_neows_ev = {
                    "conflicting_keys": cand_keys,
                    "conflicting_spkids": cand_spkids,
                    "error": "NeoWs designation matches multiple SBDB entities",
                    "name": clean_name,
                    "normalized_designation": norm_name,
                }
                if matched_field != "des":
                    amb_neows_ev["matched_sbdb_field"] = matched_field

                audit_records.append({
                    "resolution_run_id": run_id,
                    "resolved_at": resolved_at,
                    "source_system": "neows",
                    "identifier_name": "id",
                    "source_identifier_value": clean_id,
                    "matched_target_system": "sbdb",
                    "matched_target_identifier_name": "des",
                    "matched_target_identifier_value": norm_name,
                    "assigned_asteroid_key": None,
                    "match_state": STATE_AMBIGUOUS,
                    "match_rule": RULE_COLLISION_QUARANTINE,
                    "evidence_json": json.dumps(amb_neows_ev, sort_keys=True),
                })

            else:
                # Case C: Unresolved in SBDB (neither SPK-ID nor designation matched)
                neows_unres_evidence = {"reason": "NeoWs ID and designation not found in SBDB catalog"}
                if clean_name:
                    neows_unres_evidence["name"] = clean_name
                if norm_name:
                    neows_unres_evidence["normalized_designation"] = norm_name

                audit_records.append({
                    "resolution_run_id": run_id,
                    "resolved_at": resolved_at,
                    "source_system": "neows",
                    "identifier_name": "id",
                    "source_identifier_value": clean_id,
                    "matched_target_system": None,
                    "matched_target_identifier_name": None,
                    "matched_target_identifier_value": None,
                    "assigned_asteroid_key": None,
                    "match_state": STATE_UNRESOLVED,
                    "match_rule": RULE_NO_MATCH,
                    "evidence_json": json.dumps(neows_unres_evidence, sort_keys=True),
                })

    # -----------------------------------------------------------------------
    # Step 3: Resolve Sentry Entities via Secondary Bridge (Sentry.des == SBDB.des)
    # -----------------------------------------------------------------------
    # Deduplicate Sentry records by (sentry_id, designation, fullname)
    unique_sentry_input: dict[tuple, dict] = {}
    for r in sentry_list:
        if not isinstance(r, dict):
            continue
        key_tuple = (r.get("sentry_id"), r.get("designation"), r.get("fullname"))
        if key_tuple not in unique_sentry_input:
            unique_sentry_input[key_tuple] = r

    for record in unique_sentry_input.values():
        raw_sentry_id = record.get("sentry_id")
        raw_des = record.get("designation")
        raw_fullname = record.get("fullname")
        clean_fullname = str(raw_fullname).strip() if raw_fullname is not None and str(raw_fullname).strip() else None

        if raw_sentry_id is None or not str(raw_sentry_id).strip():
            audit_records.append({
                "resolution_run_id": run_id,
                "resolved_at": resolved_at,
                "source_system": "sentry",
                "identifier_name": "sentry_id",
                "source_identifier_value": str(raw_sentry_id or ""),
                "matched_target_system": None,
                "matched_target_identifier_name": None,
                "matched_target_identifier_value": None,
                "assigned_asteroid_key": None,
                "match_state": STATE_INVALID,
                "match_rule": RULE_INVALID_IDENTIFIER,
                "evidence_json": json.dumps({"error": "Missing or empty Sentry ID"}, sort_keys=True),
            })
            continue

        if raw_des is None or not str(raw_des).strip():
            audit_records.append({
                "resolution_run_id": run_id,
                "resolved_at": resolved_at,
                "source_system": "sentry",
                "identifier_name": "des",
                "source_identifier_value": str(raw_des or ""),
                "matched_target_system": None,
                "matched_target_identifier_name": None,
                "matched_target_identifier_value": None,
                "assigned_asteroid_key": None,
                "match_state": STATE_INVALID,
                "match_rule": RULE_INVALID_IDENTIFIER,
                "evidence_json": json.dumps({"error": "Missing or empty Sentry designation"}, sort_keys=True),
            })
            continue

        clean_sentry_id = str(raw_sentry_id).strip()
        clean_des = str(raw_des).strip()
        norm_sentry_des = normalize_designation(clean_des)

        # Look up in normalized SBDB designation index
        if norm_sentry_des in sbdb_by_norm_des:
            candidates = sbdb_by_norm_des[norm_sentry_des]

            if len(candidates) == 1:
                # Exactly one match: deterministic resolution succeeded
                matched_entity = candidates[0]
                target_key = matched_entity["asteroid_key"]
                target_spkid = matched_entity["spkid"]
                target_sbdb_des = matched_entity["designation"] or clean_des

                # Audit & bridge for designation
                sentry_des_ev = {
                    "matched_sbdb_des": target_sbdb_des,
                    "matched_spkid": target_spkid,
                    "normalized_designation": norm_sentry_des,
                }
                if clean_fullname:
                    sentry_des_ev["fullname"] = clean_fullname

                audit_records.append({
                    "resolution_run_id": run_id,
                    "resolved_at": resolved_at,
                    "source_system": "sentry",
                    "identifier_name": "des",
                    "source_identifier_value": clean_des,
                    "matched_target_system": "sbdb",
                    "matched_target_identifier_name": "des",
                    "matched_target_identifier_value": target_sbdb_des,
                    "assigned_asteroid_key": target_key,
                    "match_state": STATE_RESOLVED,
                    "match_rule": RULE_EXACT_DESIGNATION,
                    "evidence_json": json.dumps(sentry_des_ev, sort_keys=True),
                })
                candidate_bridge_rows.append({
                    "asteroid_key": target_key,
                    "source_system": "sentry",
                    "identifier_name": "des",
                    "identifier_value": clean_des,
                    "is_primary_pivot": False,
                    "created_at": resolved_at,
                    "updated_at": resolved_at,
                })

                # Audit & bridge for sentry_id
                sentry_id_ev = {
                    "associated_via_sentry_des": clean_des,
                    "matched_spkid": target_spkid,
                }
                if clean_fullname:
                    sentry_id_ev["fullname"] = clean_fullname

                audit_records.append({
                    "resolution_run_id": run_id,
                    "resolved_at": resolved_at,
                    "source_system": "sentry",
                    "identifier_name": "sentry_id",
                    "source_identifier_value": clean_sentry_id,
                    "matched_target_system": "sbdb",
                    "matched_target_identifier_name": "des",
                    "matched_target_identifier_value": target_sbdb_des,
                    "assigned_asteroid_key": target_key,
                    "match_state": STATE_RESOLVED,
                    "match_rule": RULE_ASSOCIATED_SENTRY_ID,
                    "evidence_json": json.dumps(sentry_id_ev, sort_keys=True),
                })
                candidate_bridge_rows.append({
                    "asteroid_key": target_key,
                    "source_system": "sentry",
                    "identifier_name": "sentry_id",
                    "identifier_value": clean_sentry_id,
                    "is_primary_pivot": False,
                    "created_at": resolved_at,
                    "updated_at": resolved_at,
                })

            else:
                # Multiple candidates found in SBDB: AMBIGUOUS collision quarantine
                cand_spkids = [c["spkid"] for c in candidates]
                cand_keys = [c["asteroid_key"] for c in candidates]
                logger.warning(
                    "[%s] Sentry designation '%s' matches multiple SBDB entities: SPK-IDs=%s",
                    run_id,
                    clean_des,
                    cand_spkids,
                )
                amb_des_ev = {
                    "error": "Sentry designation matches multiple SBDB entities",
                    "conflicting_spkids": cand_spkids,
                    "conflicting_keys": cand_keys,
                }
                if clean_fullname:
                    amb_des_ev["fullname"] = clean_fullname

                audit_records.append({
                    "resolution_run_id": run_id,
                    "resolved_at": resolved_at,
                    "source_system": "sentry",
                    "identifier_name": "des",
                    "source_identifier_value": clean_des,
                    "matched_target_system": "sbdb",
                    "matched_target_identifier_name": "des",
                    "matched_target_identifier_value": clean_des,
                    "assigned_asteroid_key": None,
                    "match_state": STATE_AMBIGUOUS,
                    "match_rule": RULE_COLLISION_QUARANTINE,
                    "evidence_json": json.dumps(amb_des_ev, sort_keys=True),
                })

                amb_id_ev = {
                    "error": "Sentry ID associated with ambiguous designation",
                    "conflicting_spkids": cand_spkids,
                }
                if clean_fullname:
                    amb_id_ev["fullname"] = clean_fullname

                audit_records.append({
                    "resolution_run_id": run_id,
                    "resolved_at": resolved_at,
                    "source_system": "sentry",
                    "identifier_name": "sentry_id",
                    "source_identifier_value": clean_sentry_id,
                    "matched_target_system": "sbdb",
                    "matched_target_identifier_name": "des",
                    "matched_target_identifier_value": clean_des,
                    "assigned_asteroid_key": None,
                    "match_state": STATE_AMBIGUOUS,
                    "match_rule": RULE_COLLISION_QUARANTINE,
                    "evidence_json": json.dumps(amb_id_ev, sort_keys=True),
                })

        else:
            # Unresolved in SBDB
            unres_des_ev = {"reason": "Designation not found in SBDB catalog", "normalized": norm_sentry_des}
            if clean_fullname:
                unres_des_ev["fullname"] = clean_fullname

            audit_records.append({
                "resolution_run_id": run_id,
                "resolved_at": resolved_at,
                "source_system": "sentry",
                "identifier_name": "des",
                "source_identifier_value": clean_des,
                "matched_target_system": None,
                "matched_target_identifier_name": None,
                "matched_target_identifier_value": None,
                "assigned_asteroid_key": None,
                "match_state": STATE_UNRESOLVED,
                "match_rule": RULE_NO_MATCH,
                "evidence_json": json.dumps(unres_des_ev, sort_keys=True),
            })

            unres_id_ev = {"reason": "Associated with unresolved Sentry designation", "designation": clean_des}
            if clean_fullname:
                unres_id_ev["fullname"] = clean_fullname

            audit_records.append({
                "resolution_run_id": run_id,
                "resolved_at": resolved_at,
                "source_system": "sentry",
                "identifier_name": "sentry_id",
                "source_identifier_value": clean_sentry_id,
                "matched_target_system": None,
                "matched_target_identifier_name": None,
                "matched_target_identifier_value": None,
                "assigned_asteroid_key": None,
                "match_state": STATE_UNRESOLVED,
                "match_rule": RULE_NO_MATCH,
                "evidence_json": json.dumps(unres_id_ev, sort_keys=True),
            })

    # -----------------------------------------------------------------------
    # Step 4: Enforce Critical Bridge Data Quality Invariant
    # (source_system, identifier_name, identifier_value) -> at most one asteroid_key
    # -----------------------------------------------------------------------
    id_to_assigned_keys: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for row in candidate_bridge_rows:
        key_tuple = (row["source_system"], row["identifier_name"], row["identifier_value"])
        id_to_assigned_keys[key_tuple].add(row["asteroid_key"])

    violating_identifiers: set[tuple[str, str, str]] = {
        k for k, keys in id_to_assigned_keys.items() if len(keys) > 1
    }

    if violating_identifiers:
        for src_sys, id_name, id_val in violating_identifiers:
            competing_keys = sorted(list(id_to_assigned_keys[(src_sys, id_name, id_val)]))
            logger.error(
                "[%s] DQ Invariant Violation: (%s, %s, '%s') maps to multiple asteroid_keys: %s. Quarantining.",
                run_id,
                src_sys,
                id_name,
                id_val,
                competing_keys,
            )

        # 1. Quarantine candidate bridge rows for violating identifiers
        candidate_bridge_rows = [
            row for row in candidate_bridge_rows
            if (row["source_system"], row["identifier_name"], row["identifier_value"]) not in violating_identifiers
        ]

        # 2. Update audit records for violating identifiers to AMBIGUOUS and null key
        for audit in audit_records:
            audit_id_tuple = (audit["source_system"], audit["identifier_name"], audit["source_identifier_value"])
            if audit_id_tuple in violating_identifiers:
                competing_keys = sorted(list(id_to_assigned_keys[audit_id_tuple]))
                audit["match_state"] = STATE_AMBIGUOUS
                audit["match_rule"] = RULE_COLLISION_QUARANTINE
                audit["assigned_asteroid_key"] = None
                audit["evidence_json"] = json.dumps({
                    "error": "Source identifier mapped to multiple competing asteroid_keys",
                    "competing_keys": competing_keys,
                }, sort_keys=True)

    # -----------------------------------------------------------------------
    # Step 5: Bridge Deduplication & Authoritative Grain Enforcement
    # Grain: (asteroid_key, source_system, identifier_name, identifier_value)
    # -----------------------------------------------------------------------
    unique_bridge: dict[tuple, dict] = {}
    for row in candidate_bridge_rows:
        bridge_grain_key = (
            row["asteroid_key"],
            row["source_system"],
            row["identifier_name"],
            row["identifier_value"],
        )
        if bridge_grain_key not in unique_bridge:
            unique_bridge[bridge_grain_key] = row

    # Sort deterministically
    final_bridge_records = sorted(
        unique_bridge.values(),
        key=lambda r: (
            r["asteroid_key"],
            r["source_system"],
            r["identifier_name"],
            r["identifier_value"],
        ),
    )

    # -----------------------------------------------------------------------
    # Step 6: Audit Deduplication & Authoritative Grain Enforcement
    # Grain: (resolution_run_id, source_system, identifier_name, source_identifier_value)
    # -----------------------------------------------------------------------
    unique_audit: dict[tuple, dict] = {}
    state_severity = {STATE_AMBIGUOUS: 4, STATE_INVALID: 3, STATE_UNRESOLVED: 2, STATE_RESOLVED: 1}

    for audit in audit_records:
        audit_grain_key = (
            audit["resolution_run_id"],
            audit["source_system"],
            audit["identifier_name"],
            audit["source_identifier_value"],
        )
        if audit_grain_key not in unique_audit:
            unique_audit[audit_grain_key] = audit
        else:
            # If seen multiple times, keep the most conservative state
            prev_severity = state_severity.get(unique_audit[audit_grain_key]["match_state"], 0)
            curr_severity = state_severity.get(audit["match_state"], 0)
            if curr_severity > prev_severity:
                unique_audit[audit_grain_key] = audit

    # Sort deterministically
    final_audit_records = sorted(
        unique_audit.values(),
        key=lambda r: (
            r["source_system"],
            r["identifier_name"],
            r["source_identifier_value"],
        ),
    )

    # -----------------------------------------------------------------------
    # Step 7: Metrics & Summary Logging
    # -----------------------------------------------------------------------
    total_eval = len(final_audit_records)
    by_source = defaultdict(int)
    by_state = defaultdict(int)
    by_rule = defaultdict(int)

    for r in final_audit_records:
        by_source[r["source_system"]] += 1
        by_state[r["match_state"]] += 1
        by_rule[r["match_rule"]] += 1

    resolved_count = by_state[STATE_RESOLVED]
    resolution_rate = (resolved_count / total_eval * 100.0) if total_eval > 0 else 0.0

    metrics = {
        "run_id": run_id,
        "resolved_at": resolved_at,
        "elapsed_seconds": round(time.perf_counter() - start_time, 4),
        "total_evaluated": total_eval,
        "by_source": dict(by_source),
        "by_state": dict(by_state),
        "by_rule": dict(by_rule),
        "resolved_count": resolved_count,
        "unresolved_count": by_state[STATE_UNRESOLVED],
        "ambiguous_count": by_state[STATE_AMBIGUOUS],
        "invalid_count": by_state[STATE_INVALID],
        "resolution_rate_pct": round(resolution_rate, 2),
        "bridge_records_emitted": len(final_bridge_records),
    }

    logger.info(
        "[%s] Entity resolution complete in %.2fs | Total=%d (Resolved=%d [%.1f%%], Unresolved=%d, Ambiguous=%d, Invalid=%d) | Bridge emitted=%d",
        run_id,
        metrics["elapsed_seconds"],
        total_eval,
        resolved_count,
        resolution_rate,
        metrics["unresolved_count"],
        metrics["ambiguous_count"],
        metrics["invalid_count"],
        len(final_bridge_records),
    )

    return final_bridge_records, final_audit_records, metrics


# ---------------------------------------------------------------------------
# File I/O & Storage Primitives
# ---------------------------------------------------------------------------

def load_parquet_records(file_path: str) -> list[dict]:
    """Load records from a local Parquet file into a list of dicts."""
    if not file_path or not os.path.exists(file_path):
        logger.warning("Input Parquet path not found or empty: %s", file_path)
        return []
    table = pq.read_table(file_path)
    return table.to_pylist()


def save_resolution_outputs(
    bridge_records: list[dict],
    resolution_records: list[dict],
    output_dir: str = ".",
    bridge_filename: str = "bridge_asteroid_identifier.parquet",
    resolution_filename: str = "fact_entity_resolution.parquet",
) -> tuple[str, str]:
    """Write bridge and audit Parquet tables locally with locked schemas."""
    os.makedirs(output_dir, exist_ok=True)
    bridge_path = os.path.join(output_dir, bridge_filename)
    resolution_path = os.path.join(output_dir, resolution_filename)

    write_parquet(bridge_records, BRIDGE_ASTEROID_IDENTIFIER_SCHEMA, bridge_path, compression="snappy")
    write_parquet(resolution_records, FACT_ENTITY_RESOLUTION_SCHEMA, resolution_path, compression="snappy")

    logger.info("Saved local bridge Parquet (%d rows) to %s", len(bridge_records), bridge_path)
    logger.info("Saved local audit Parquet (%d rows) to %s", len(resolution_records), resolution_path)

    return bridge_path, resolution_path


def upload_crosswalk_to_s3(
    bridge_local_path: str,
    resolution_local_path: str,
    resolution_date: date,
    bucket_name: str | None = None,
    metadata: dict | None = None,
    s3_client=None,
) -> tuple[str, str]:
    """Upload generated crosswalk datasets to dedicated reference/ partition in S3."""
    bucket = bucket_name or S3_BUCKET_NAME
    year = f"{resolution_date.year:04d}"
    month = f"{resolution_date.month:02d}"
    day = f"{resolution_date.day:02d}"

    bridge_s3_key = (
        f"{REFERENCE_BASE_KEY}/bridge_asteroid_identifier/"
        f"year={year}/month={month}/day={day}/bridge_asteroid_identifier.parquet"
    )
    resolution_s3_key = (
        f"{REFERENCE_BASE_KEY}/fact_entity_resolution/"
        f"year={year}/month={month}/day={day}/fact_entity_resolution.parquet"
    )

    s3 = s3_client or boto3.client("s3")
    try:
        upload_file_to_s3(bridge_local_path, bucket, bridge_s3_key, metadata=metadata, s3_client=s3)
        upload_file_to_s3(resolution_local_path, bucket, resolution_s3_key, metadata=metadata, s3_client=s3)
    except (BotoCoreError, ClientError) as error:
        logger.error("Failed to upload crosswalk to S3: %s", redact_api_key(str(error)))
        raise

    return bridge_s3_key, resolution_s3_key


# ---------------------------------------------------------------------------
# CLI Argument Parsing & Orchestration
# ---------------------------------------------------------------------------

def parse_args():
    """Parse CLI arguments for entity resolution pipeline."""
    parser = argparse.ArgumentParser(
        description="NASA Planetary Defense Risk Intelligence Platform — Entity Resolution Engine"
    )
    parser.add_argument("--neows-path", type=str, default=None, help="Path to processed NeoWs Parquet file")
    parser.add_argument("--sbdb-path", type=str, default=None, help="Path to processed SBDB object snapshot Parquet file")
    parser.add_argument("--sentry-path", type=str, default=None, help="Path to processed Sentry risk snapshot Parquet file")
    parser.add_argument("--resolution-date", type=str, default=None, help="Resolution partition date (YYYY-MM-DD). Default: today UTC")
    parser.add_argument("--output-dir", type=str, default=".", help="Local directory to output Parquet files")
    parser.add_argument("--upload-s3", action="store_true", help="Upload generated reference Parquet files to S3")
    parser.add_argument("--s3-bucket", type=str, default=S3_BUCKET_NAME, help="S3 bucket name")
    return parser.parse_args()


def main(
    neows_records: list[dict] | None = None,
    sbdb_records: list[dict] | None = None,
    sentry_records: list[dict] | None = None,
    resolution_date_str: str | None = None,
    output_dir: str = ".",
    upload_s3: bool = False,
    bucket_name: str | None = None,
) -> int:
    """Execute end-to-end entity resolution workflow."""
    run_id = uuid.uuid4().hex[:12]
    main.current_run_id = run_id
    resolved_time = datetime.now(timezone.utc).isoformat()

    if resolution_date_str:
        try:
            res_date = datetime.strptime(resolution_date_str.strip(), "%Y-%m-%d").date()
        except ValueError:
            logger.error("Invalid --resolution-date format: %s. Expected YYYY-MM-DD.", resolution_date_str)
            return 1
    else:
        res_date = datetime.now(timezone.utc).date()

    lineage_metadata = build_lineage_metadata(
        source_name="nasa_entity_resolution_engine",
        run_id=run_id,
        ingested_at=resolved_time,
    )

    try:
        bridge_records, audit_records, metrics = resolve_entities(
            neows_records=neows_records,
            sbdb_records=sbdb_records,
            sentry_records=sentry_records,
            run_id=run_id,
            resolved_at=resolved_time,
        )

        bridge_path, resolution_path = save_resolution_outputs(
            bridge_records=bridge_records,
            resolution_records=audit_records,
            output_dir=output_dir,
        )

        if upload_s3:
            upload_crosswalk_to_s3(
                bridge_local_path=bridge_path,
                resolution_local_path=resolution_path,
                resolution_date=res_date,
                bucket_name=bucket_name,
                metadata=lineage_metadata,
            )

        return 0
    except Exception as error:
        logger.error("[%s] Entity resolution workflow failed: %s", run_id, redact_api_key(str(error)))
        return 1


if __name__ == "__main__":
    args = parse_args()

    # Load from file paths if provided
    neows_data = load_parquet_records(args.neows_path) if args.neows_path else None
    sbdb_data = load_parquet_records(args.sbdb_path) if args.sbdb_path else None
    sentry_data = load_parquet_records(args.sentry_path) if args.sentry_path else None

    exit_code = main(
        neows_records=neows_data,
        sbdb_records=sbdb_data,
        sentry_records=sentry_data,
        resolution_date_str=args.resolution_date,
        output_dir=args.output_dir,
        upload_s3=args.upload_s3,
        bucket_name=args.s3_bucket,
    )
    sys.exit(exit_code)
