"""Tests for shared ingestion foundation utilities in pipeline_utils.py."""
import json
import logging
import os
from unittest.mock import MagicMock, patch

from botocore.exceptions import BotoCoreError, ClientError
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import pipeline_utils


def test_get_http_session_default_configuration():
    session = pipeline_utils.get_http_session()
    adapter_https = session.adapters.get("https://")
    adapter_http = session.adapters.get("http://")

    assert adapter_https is not None
    assert adapter_http is not None

    retries = adapter_https.max_retries
    assert retries.total == 3
    assert retries.backoff_factor == 1
    assert set(retries.status_forcelist) == {429, 500, 502, 503, 504}
    assert retries.allowed_methods == ["GET"]


def test_get_http_session_custom_configuration():
    session = pipeline_utils.get_http_session(total_retries=5, backoff_factor=2)
    adapter = session.adapters.get("https://")
    assert adapter.max_retries.total == 5
    assert adapter.max_retries.backoff_factor == 2


def test_redact_api_key():
    url_single = "http://api.nasa.gov/test?api_key=SECRET_TOKEN"
    assert (
        pipeline_utils.redact_api_key(url_single)
        == "http://api.nasa.gov/test?api_key=REDACTED"
    )

    url_multi = "http://api.nasa.gov/test?param1=val&api_key=SECRET_TOKEN&param2=123"
    assert (
        pipeline_utils.redact_api_key(url_multi)
        == "http://api.nasa.gov/test?param1=val&api_key=REDACTED&param2=123"
    )

    url_clean = "http://api.nasa.gov/test?param1=val"
    assert pipeline_utils.redact_api_key(url_clean) == url_clean

    assert pipeline_utils.redact_api_key(None) is None
    assert pipeline_utils.redact_api_key(42) == 42


def test_build_lineage_metadata():
    metadata = pipeline_utils.build_lineage_metadata(
        source_name="test_source",
        run_id="abc123def456",
        ingested_at="2026-09-25T01:00:00+00:00"
    )
    assert metadata == {
        "run_id": "abc123def456",
        "ingested_at": "2026-09-25T01:00:00+00:00",
        "source": "test_source"
    }


def test_upload_file_to_s3_success():
    mock_s3 = MagicMock()
    metadata = {"run_id": "run-001"}

    with patch("boto3.client", return_value=mock_s3) as mock_boto:
        pipeline_utils.upload_file_to_s3(
            local_file_path="sample.json",
            bucket_name="test-bucket",
            s3_key="raw/sample.json",
            metadata=metadata
        )

        mock_boto.assert_called_once_with("s3")
        mock_s3.upload_file.assert_called_once_with(
            "sample.json",
            "test-bucket",
            "raw/sample.json",
            ExtraArgs={"Metadata": metadata}
        )


def test_upload_file_to_s3_with_existing_client():
    mock_s3 = MagicMock()
    pipeline_utils.upload_file_to_s3(
        local_file_path="sample.parquet",
        bucket_name="test-bucket",
        s3_key="processed/sample.parquet",
        s3_client=mock_s3
    )

    mock_s3.upload_file.assert_called_once_with(
        "sample.parquet",
        "test-bucket",
        "processed/sample.parquet"
    )


def test_upload_file_to_s3_handles_client_error(caplog):
    mock_s3 = MagicMock()
    client_error = ClientError({"Error": {"Code": "403", "Message": "Forbidden"}}, "PutObject")
    mock_s3.upload_file.side_effect = client_error

    with caplog.at_level(logging.ERROR):
        with pytest.raises(ClientError) as exc_info:
            pipeline_utils.upload_file_to_s3(
                local_file_path="sample.json",
                bucket_name="test-bucket",
                s3_key="raw/sample.json",
                s3_client=mock_s3
            )

    assert exc_info.value == client_error
    assert "S3 upload failed for sample.json" in caplog.text


def test_upload_file_to_s3_handles_botocore_error(caplog):
    mock_s3 = MagicMock()
    boto_error = BotoCoreError()
    mock_s3.upload_file.side_effect = boto_error

    with caplog.at_level(logging.ERROR):
        with pytest.raises(BotoCoreError) as exc_info:
            pipeline_utils.upload_file_to_s3(
                local_file_path="sample.json",
                bucket_name="test-bucket",
                s3_key="raw/sample.json",
                s3_client=mock_s3
            )

    assert exc_info.value == boto_error
    assert "S3 upload failed for sample.json" in caplog.text


def test_upload_file_to_s3_redacts_api_key_in_error_log(caplog):
    mock_s3 = MagicMock()
    client_error = ClientError(
        {"Error": {"Code": "403", "Message": "AccessDenied url https://s3.amazonaws.com?api_key=SECRET_AWS_KEY"}},
        "PutObject"
    )
    mock_s3.upload_file.side_effect = client_error

    with caplog.at_level(logging.ERROR):
        with pytest.raises(ClientError):
            pipeline_utils.upload_file_to_s3(
                local_file_path="sample.json",
                bucket_name="test-bucket",
                s3_key="raw/sample.json",
                s3_client=mock_s3
            )

    assert "SECRET_AWS_KEY" not in caplog.text
    assert "api_key=REDACTED" in caplog.text


def test_write_parquet_success(tmp_path):
    output_path = tmp_path / "test.parquet"
    test_schema = pa.schema([
        ("id", pa.string()),
        ("value", pa.float64())
    ])
    records = [
        {"id": "rec1", "value": 12.34},
        {"id": "rec2", "value": 56.78}
    ]

    pipeline_utils.write_parquet(records, schema=test_schema, output_path=str(output_path))

    assert output_path.exists()
    table = pq.read_table(str(output_path))
    assert table.num_rows == 2
    assert table.column("id").to_pylist() == ["rec1", "rec2"]
    assert table.column("value").to_pylist() == [12.34, 56.78]


def test_build_manifest_s3_key_distinct_runs_no_overwrite():
    run_date = "2026-09-28"
    key1 = pipeline_utils.build_manifest_s3_key(run_date, "pipe_001")
    key2 = pipeline_utils.build_manifest_s3_key(run_date, "pipe_002")

    assert key1 == "metadata/pipeline_runs/year=2026/month=09/day=28/run_manifest_pipe_001.json"
    assert key2 == "metadata/pipeline_runs/year=2026/month=09/day=28/run_manifest_pipe_002.json"
    assert key1 != key2

    # Supports datetime and date objects
    from datetime import date, datetime
    key_date = pipeline_utils.build_manifest_s3_key(date(2026, 10, 5), "run_abc")
    assert key_date == "metadata/pipeline_runs/year=2026/month=10/day=05/run_manifest_run_abc.json"

    key_dt = pipeline_utils.build_manifest_s3_key(datetime(2026, 12, 1, 15, 30), "run_xyz")
    assert key_dt == "metadata/pipeline_runs/year=2026/month=12/day=01/run_manifest_run_xyz.json"

    with pytest.raises(ValueError):
        pipeline_utils.build_manifest_s3_key(run_date, "")

    with pytest.raises(ValueError):
        pipeline_utils.build_manifest_s3_key(12345, "pipe_001")


def test_generate_run_manifest_current_production_success():
    stages = {
        "neows": {
            "status": "SUCCESS",
            "records_received": 142,
            "records_valid": 140,
            "skipped_records": 2,
            "rejection_rate_pct": 1.4,
            "dq_gate_passed": True,
            "s3_keys": ["raw/year=2026/month=09/day=28/asteroids_raw.json", "processed/year=2026/month=09/day=28/asteroids.parquet"],
        },
        "sentry": {
            "status": "SUCCESS",
            "records_received": 50,
            "records_valid": 50,
            "skipped_records": 0,
            "rejection_rate_pct": 0.0,
            "dq_gate_passed": True,
            "s3_key": "processed/sentry/risk_snapshot/year=2026/month=09/day=28/fact_sentry_risk_snapshot.parquet",
        },
        "sbdb": {
            "status": "SUCCESS",
            "total_targets": 15,
            "successful_targets": 14,
            "failed_targets": 1,
            "failure_rate_pct": 6.7,
            "failure_gate_passed": True,
        },
        "entity_resolution": {
            "status": "SUCCESS",
            "resolution_run_id": "res_12345",
            "total_evaluated": 200,
            "resolved_count": 190,
            "resolution_rate_pct": 95.0,
            "crosswalk_gate_passed": True,
        },
        "crosswalk_publish": {
            "status": "SUCCESS",
            "bridge_s3_key": "reference/asteroid_crosswalk/bridge_asteroid_identifier/year=2026/month=09/day=28/bridge_asteroid_identifier.parquet",
            "audit_s3_key": "reference/asteroid_crosswalk/fact_entity_resolution/year=2026/month=09/day=28/fact_entity_resolution.parquet",
        },
        "verification": {
            "status": "SUCCESS",
            "checks_passed": True,
        },
    }

    manifest = pipeline_utils.generate_run_manifest(
        pipeline_run_id="pipe_20260928_prod",
        execution_mode="CURRENT_PRODUCTION",
        started_at="2026-09-28T06:00:00Z",
        completed_at="2026-09-28T06:04:15Z",
        run_date="2026-09-28",
        stages=stages,
        workflow_metadata={"run_number": 42, "trigger": "schedule"},
    )

    assert manifest["schema_version"] == "1.0.0"
    assert manifest["pipeline_run_id"] == "pipe_20260928_prod"
    assert manifest["execution_mode"] == "CURRENT_PRODUCTION"
    assert manifest["overall_status"] == "SUCCESS"
    assert manifest["duration_seconds"] == 255.0
    assert manifest["stages"]["neows"]["records_valid"] == 140
    assert manifest["stages"]["sentry"]["status"] == "SUCCESS"
    assert manifest["stages"]["sbdb"]["failure_rate_pct"] == 6.7
    assert manifest["stages"]["entity_resolution"]["resolved_count"] == 190
    assert manifest["manifest_s3_key"] == "metadata/pipeline_runs/year=2026/month=09/day=28/run_manifest_pipe_20260928_prod.json"
    assert "published_s3_uris" not in manifest
    assert "published_s3_keys" in manifest
    assert len(manifest["published_s3_keys"]) == 5
    assert manifest["published_s3_keys"] == sorted(manifest["published_s3_keys"])
    assert "processed/year=2026/month=09/day=28/asteroids.parquet" in manifest["published_s3_keys"]


def test_generate_run_manifest_historical_backfill_no_sentry():
    stages = {
        "neows": {
            "status": "SUCCESS",
            "records_valid": 85,
            "dq_gate_passed": True,
        },
        "sbdb": {
            "status": "SUCCESS",
            "successful_targets": 10,
            "failure_gate_passed": True,
        },
        "entity_resolution": {
            "status": "SUCCESS",
            "resolved_count": 80,
            "crosswalk_gate_passed": True,
        },
    }

    manifest = pipeline_utils.generate_run_manifest(
        pipeline_run_id="pipe_backfill_001",
        execution_mode="HISTORICAL_BACKFILL",
        started_at="2026-09-28T10:00:00+00:00",
        completed_at="2026-09-28T10:02:30+00:00",
        stages=stages,
    )

    assert manifest["execution_mode"] == "HISTORICAL_BACKFILL"
    assert manifest["overall_status"] == "SUCCESS"
    assert "sentry" not in manifest["stages"] or manifest["stages"]["sentry"] is None
    assert manifest["duration_seconds"] == 150.0
    assert "published_s3_uris" not in manifest
    assert "published_s3_keys" in manifest


def test_generate_run_manifest_failed_stage_preserves_prior_stages():
    stages = {
        "neows": {
            "status": "SUCCESS",
            "records_received": 100,
            "records_valid": 98,
            "dq_gate_passed": True,
            "s3_key": "processed/year=2026/month=09/day=28/asteroids.parquet",
        },
        "sbdb": {
            "status": "FAILED",
            "total_targets": 10,
            "successful_targets": 2,
            "failed_targets": 8,
            "failure_rate_pct": 80.0,
            "failure_gate_passed": False,
        },
    }

    manifest = pipeline_utils.generate_run_manifest(
        pipeline_run_id="pipe_failed_001",
        execution_mode="CURRENT_PRODUCTION",
        started_at="2026-09-28T12:00:00Z",
        completed_at="2026-09-28T12:01:00Z",
        stages=stages,
    )

    # Inferred overall status must be FAILED
    assert manifest["overall_status"] == "FAILED"
    # But NeoWs metrics remain preserved
    assert manifest["stages"]["neows"]["status"] == "SUCCESS"
    assert manifest["stages"]["neows"]["records_valid"] == 98
    assert manifest["stages"]["sbdb"]["status"] == "FAILED"
    assert manifest["stages"]["sbdb"]["failure_rate_pct"] == 80.0
    assert "published_s3_uris" not in manifest
    assert "published_s3_keys" in manifest
    assert "processed/year=2026/month=09/day=28/asteroids.parquet" in manifest["published_s3_keys"]


def test_generate_run_manifest_duration_calculation():
    # Calculation from timestamps
    m1 = pipeline_utils.generate_run_manifest(
        pipeline_run_id="pipe_dur_1",
        execution_mode="CURRENT_PRODUCTION",
        started_at="2026-09-28T00:00:00Z",
        completed_at="2026-09-28T00:01:30Z",
    )
    assert m1["duration_seconds"] == 90.0

    # Explicit override
    m2 = pipeline_utils.generate_run_manifest(
        pipeline_run_id="pipe_dur_2",
        execution_mode="CURRENT_PRODUCTION",
        started_at="2026-09-28T00:00:00Z",
        completed_at="2026-09-28T00:01:30Z",
        duration_seconds=125.456,
    )
    assert m2["duration_seconds"] == 125.46


def test_generate_run_manifest_secret_exclusion():
    dirty_metadata = {
        "actor": "octocat",
        "api_key": "SUPER_SECRET_API_KEY",
        "NASA_API_KEY": "ANOTHER_SECRET",
        "aws_secret_access_key": "AWS_SECRET_VAL",
        "nested": {
            "token": "BEARER_TOKEN",
            "clean_field": "clean_value",
            "url_with_key": "https://api.nasa.gov/feed?api_key=TOP_SECRET_123&other=val",
        },
    }

    manifest = pipeline_utils.generate_run_manifest(
        pipeline_run_id="pipe_sec_001",
        execution_mode="CURRENT_PRODUCTION",
        started_at="2026-09-28T00:00:00Z",
        workflow_metadata=dirty_metadata,
        published_s3_keys=["data/raw/feed.parquet?api_key=SECRET_PARAM"],
    )

    clean_meta = manifest["workflow_metadata"]
    assert "api_key" not in clean_meta
    assert "NASA_API_KEY" not in clean_meta
    assert "aws_secret_access_key" not in clean_meta
    assert "token" not in clean_meta["nested"]
    assert clean_meta["nested"]["clean_field"] == "clean_value"
    assert clean_meta["nested"]["url_with_key"] == "https://api.nasa.gov/feed?api_key=REDACTED&other=val"
    assert "published_s3_uris" not in manifest
    assert manifest["published_s3_keys"] == ["data/raw/feed.parquet?api_key=REDACTED"]


def test_generate_run_manifest_invalid_inputs():
    with pytest.raises(ValueError):
        pipeline_utils.generate_run_manifest("", "CURRENT_PRODUCTION", "2026-09-28T00:00:00Z")

    with pytest.raises(ValueError):
        pipeline_utils.generate_run_manifest("pipe_01", "INVALID_MODE", "2026-09-28T00:00:00Z")

    with pytest.raises(ValueError):
        pipeline_utils.generate_run_manifest(
            "pipe_01", "CURRENT_PRODUCTION", "2026-09-28T00:00:00Z", overall_status="INVALID_STATUS"
        )


def test_save_and_upload_run_manifest(tmp_path):
    manifest = pipeline_utils.generate_run_manifest(
        pipeline_run_id="pipe_save_001",
        execution_mode="CURRENT_PRODUCTION",
        started_at="2026-09-28T00:00:00Z",
        run_date="2026-09-28",
    )

    out_file = tmp_path / "subdir" / "manifest.json"
    saved_path = pipeline_utils.save_run_manifest(manifest, local_path=str(out_file))
    assert os.path.exists(saved_path)

    with open(saved_path, "r", encoding="utf-8") as f:
        loaded = json.load(f)
    assert loaded["pipeline_run_id"] == "pipe_save_001"

    # Test upload with mocked upload_file_to_s3
    with patch("pipeline_utils.upload_file_to_s3") as mock_upload:
        uploaded_key = pipeline_utils.upload_run_manifest_to_s3(
            manifest,
            local_file_path=str(out_file),
            bucket_name="custom-bucket"
        )
        assert uploaded_key == "metadata/pipeline_runs/year=2026/month=09/day=28/run_manifest_pipe_save_001.json"
        mock_upload.assert_called_once()
        call_kwargs = mock_upload.call_args[1]
        assert call_kwargs["bucket_name"] == "custom-bucket"
        assert call_kwargs["s3_key"] == uploaded_key
        assert call_kwargs["metadata"]["run_id"] == "pipe_save_001"
