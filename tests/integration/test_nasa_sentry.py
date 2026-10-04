"""Unit and integration tests for the Sentry Mode S risk ingestion pipeline."""
from datetime import date, datetime, timezone
import logging
from unittest.mock import MagicMock, patch

import pyarrow.parquet as pq
import pytest
import requests
from botocore.exceptions import ClientError

import nasa_sentry

SAMPLE_MODE_S_PAYLOAD = {
    "signature": {"version": "2.0", "source": "NASA/JPL Sentry Data API"},
    "count": 2,
    "data": [
        {
            "des": "1979 XB",
            "diameter": "0.66",
            "fullname": "(1979 XB)",
            "h": "18.56",
            "id": "bJ79X00B",
            "ip": "8.89646e-07",
            "last_obs": "1979-12-15",
            "last_obs_jd": "2444222.5",
            "n_imp": "5",
            "ps_cum": "-2.75",
            "ps_max": "-3.12",
            "range": "2056-2113",
            "ts_max": "0",
            "v_inf": "23.7204"
        },
        {
            "des": "2000 SB45",
            "diameter": "0.046",
            "fullname": "(2000 SB45)",
            "h": "24.34",
            "id": "bK00S45B",
            "ip": "0.000159",
            "last_obs": "2000-09-29",
            "last_obs_jd": "2451816.5",
            "n_imp": 227,
            "ps_cum": "-3.75",
            "ps_max": "-4.22",
            "range": "2068-2118",
            "ts_max": "0",
            "v_inf": "7.5307"
        }
    ]
}


def make_valid_sentry_item(**overrides):
    """Generate a valid Mode S data record dictionary with optional field overrides."""
    base = {
        "id": "bJ79X00B",
        "des": "1979 XB",
        "fullname": "(1979 XB)",
        "h": "18.56",
        "diameter": "0.66",
        "ip": "8.89646e-07",
        "n_imp": "5",
        "ps_cum": "-2.75",
        "ps_max": "-3.12",
        "ts_max": "0",
        "v_inf": "23.72",
        "range": "2056-2113",
        "last_obs": "1979-12-15",
        "last_obs_jd": "2444222.5"
    }
    base.update(overrides)
    return base


def test_extract_sentry_valid_records():
    records, skipped, received = nasa_sentry.extract_sentry_risk_snapshot(
        data=SAMPLE_MODE_S_PAYLOAD,
        snapshot_key="2026-09-25",
        run_id="run_test_123",
        snapshot_time="2026-09-25T12:00:00Z"
    )

    assert received == 2
    assert skipped == 0
    assert len(records) == 2

    first = records[0]
    assert first["snapshot_key"] == "2026-09-25"
    assert first["run_id"] == "run_test_123"
    assert first["snapshot_time"] == "2026-09-25T12:00:00Z"
    assert first["sentry_id"] == "bJ79X00B"
    assert first["designation"] == "1979 XB"
    assert first["fullname"] == "(1979 XB)"
    assert first["absolute_magnitude"] == 18.56
    assert first["estimated_diameter_km"] == 0.66
    assert first["impact_probability"] == 8.89646e-07
    assert first["potential_impacts_count"] == 5
    assert first["palermo_scale_cum"] == -2.75
    assert first["palermo_scale_max"] == -3.12
    assert first["torino_scale_max"] == 0
    assert first["v_infinity_km_s"] == 23.7204
    assert first["impact_year_range"] == "2056-2113"
    assert first["last_obs_date"] == "1979-12-15"
    assert first["last_obs_jd"] == 2444222.5

    second = records[1]
    assert second["sentry_id"] == "bK00S45B"
    assert second["potential_impacts_count"] == 227


def test_extract_sentry_skips_missing_or_empty_id():
    items = [
        make_valid_sentry_item(id=None),
        make_valid_sentry_item(id=""),
        make_valid_sentry_item(id="   "),
    ]
    payload = {"data": items}
    records, skipped, received = nasa_sentry.extract_sentry_risk_snapshot(
        payload, "2026-09-25", "r1", "t1"
    )
    assert len(records) == 0
    assert skipped == 3
    assert received == 3


def test_extract_sentry_skips_missing_or_empty_des():
    items = [
        make_valid_sentry_item(des=None),
        make_valid_sentry_item(des=""),
        make_valid_sentry_item(des="   "),
    ]
    payload = {"data": items}
    records, skipped, received = nasa_sentry.extract_sentry_risk_snapshot(
        payload, "2026-09-25", "r1", "t1"
    )
    assert len(records) == 0
    assert skipped == 3
    assert received == 3


def test_extract_sentry_skips_invalid_impact_probability():
    items = [
        make_valid_sentry_item(id="1", ip="not_a_float"),
        make_valid_sentry_item(id="2", ip="-0.01"),
        make_valid_sentry_item(id="3", ip="0.0"),
        make_valid_sentry_item(id="4", ip="1.05"),
        make_valid_sentry_item(id="5", ip=None),
    ]
    payload = {"data": items}
    records, skipped, received = nasa_sentry.extract_sentry_risk_snapshot(
        payload, "2026-09-25", "r1", "t1"
    )
    assert len(records) == 0
    assert skipped == 5
    assert received == 5


def test_extract_sentry_accepts_valid_boundary_impact_probabilities():
    items = [
        make_valid_sentry_item(id="1", ip="1.0"),
        make_valid_sentry_item(id="2", ip="1e-9"),
    ]
    payload = {"data": items}
    records, skipped, received = nasa_sentry.extract_sentry_risk_snapshot(
        payload, "2026-09-25", "r1", "t1"
    )
    assert len(records) == 2
    assert skipped == 0
    assert records[0]["impact_probability"] == 1.0
    assert records[1]["impact_probability"] == 1e-9


def test_extract_sentry_skips_invalid_n_imp():
    items = [
        make_valid_sentry_item(id="1", n_imp="abc"),
        make_valid_sentry_item(id="2", n_imp="0"),
        make_valid_sentry_item(id="3", n_imp="-5"),
        make_valid_sentry_item(id="4", n_imp=None),
    ]
    payload = {"data": items}
    records, skipped, received = nasa_sentry.extract_sentry_risk_snapshot(
        payload, "2026-09-25", "r1", "t1"
    )
    assert len(records) == 0
    assert skipped == 4
    assert received == 4


def test_extract_sentry_skips_invalid_palermo():
    items = [
        make_valid_sentry_item(id="1", ps_cum="invalid"),
        make_valid_sentry_item(id="2", ps_max="invalid"),
        make_valid_sentry_item(id="3", ps_cum=None),
        make_valid_sentry_item(id="4", ps_max=None),
    ]
    payload = {"data": items}
    records, skipped, received = nasa_sentry.extract_sentry_risk_snapshot(
        payload, "2026-09-25", "r1", "t1"
    )
    assert len(records) == 0
    assert skipped == 4


def test_extract_sentry_skips_invalid_torino():
    items = [
        make_valid_sentry_item(id="1", ts_max="not_int"),
        make_valid_sentry_item(id="2", ts_max="-1"),
        make_valid_sentry_item(id="3", ts_max=None),
    ]
    payload = {"data": items}
    records, skipped, received = nasa_sentry.extract_sentry_risk_snapshot(
        payload, "2026-09-25", "r1", "t1"
    )
    assert len(records) == 0
    assert skipped == 3


def test_extract_sentry_skips_invalid_velocity():
    items = [
        make_valid_sentry_item(id="1", v_inf="invalid"),
        make_valid_sentry_item(id="2", v_inf="-1.5"),
        make_valid_sentry_item(id="3", v_inf=None),
    ]
    payload = {"data": items}
    records, skipped, received = nasa_sentry.extract_sentry_risk_snapshot(
        payload, "2026-09-25", "r1", "t1"
    )
    assert len(records) == 0
    assert skipped == 3


def test_extract_sentry_skips_invalid_last_obs():
    items = [
        make_valid_sentry_item(id="1", last_obs="not-a-date"),
        make_valid_sentry_item(id="2", last_obs="2026-02-30"),
        make_valid_sentry_item(id="3", last_obs="2026-99-99"),
        make_valid_sentry_item(id="4", last_obs=None),
        make_valid_sentry_item(id="5", last_obs=""),
    ]
    payload = {"data": items}
    records, skipped, received = nasa_sentry.extract_sentry_risk_snapshot(
        payload, "2026-09-25", "r1", "t1"
    )
    assert len(records) == 0
    assert skipped == 5


def test_extract_sentry_optional_fields_null_and_empty():
    item = make_valid_sentry_item(
        id="1",
        h="",
        diameter=None,
        last_obs_jd="  ",
        fullname=None
    )
    payload = {"data": [item]}
    records, skipped, received = nasa_sentry.extract_sentry_risk_snapshot(
        payload, "2026-09-25", "r1", "t1"
    )
    assert len(records) == 1
    assert skipped == 0
    rec = records[0]
    assert rec["absolute_magnitude"] is None
    assert rec["estimated_diameter_km"] is None
    assert rec["last_obs_jd"] is None
    assert rec["fullname"] == "1979 XB"  # Fallback to designation


def test_extract_sentry_skips_malformed_optional_fields():
    items = [
        make_valid_sentry_item(id="1", h="not_a_number"),
        make_valid_sentry_item(id="2", diameter="bad_dia"),
        make_valid_sentry_item(id="3", last_obs_jd="bad_jd"),
    ]
    payload = {"data": items}
    records, skipped, received = nasa_sentry.extract_sentry_risk_snapshot(
        payload, "2026-09-25", "r1", "t1"
    )
    assert len(records) == 0
    assert skipped == 3


def test_extract_sentry_deduplicates_by_id():
    items = [
        make_valid_sentry_item(id="DUP_ID", des="OBJ1"),
        make_valid_sentry_item(id="DUP_ID", des="OBJ2"),
    ]
    payload = {"data": items}
    records, skipped, received = nasa_sentry.extract_sentry_risk_snapshot(
        payload, "2026-09-25", "r1", "t1"
    )
    assert received == 2
    assert len(records) == 1
    assert skipped == 1
    assert records[0]["sentry_id"] == "DUP_ID"


def test_save_sentry_parquet(tmp_path):
    target_parquet = tmp_path / "test_sentry.parquet"
    records, _, _ = nasa_sentry.extract_sentry_risk_snapshot(
        SAMPLE_MODE_S_PAYLOAD, "2026-09-25", "r1", "t1"
    )

    nasa_sentry.save_to_parquet(records, filename=str(target_parquet))
    assert target_parquet.exists()

    table = pq.read_table(str(target_parquet))
    assert table.schema == nasa_sentry.SENTRY_RISK_SNAPSHOT_SCHEMA
    assert table.num_rows == 2
    assert table.column("sentry_id").to_pylist() == ["bJ79X00B", "bK00S45B"]
    assert table.column("potential_impacts_count").to_pylist() == [5, 227]


def test_fetch_sentry_data_success():
    mock_payload = {
        "signature": {"version": "2.0", "source": "NASA/JPL Sentry Data API"},
        "data": []
    }
    with patch("nasa_sentry.get_http_session") as mock_get_session:
        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.json.return_value = mock_payload
        mock_response.raise_for_status.return_value = None
        mock_session.get.return_value = mock_response
        mock_get_session.return_value = mock_session

        data = nasa_sentry.fetch_sentry_data(run_id="run_abc")

        assert data == mock_payload
        mock_session.get.assert_called_once_with(
            "https://ssd-api.jpl.nasa.gov/sentry.api",
            timeout=15
        )
        mock_response.raise_for_status.assert_called_once()


def test_fetch_sentry_data_handles_http_error():
    with patch("nasa_sentry.get_http_session") as mock_get_session:
        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError("500 Server Error")
        mock_session.get.return_value = mock_response
        mock_get_session.return_value = mock_session

        with pytest.raises(requests.exceptions.HTTPError):
            nasa_sentry.fetch_sentry_data()


def test_fetch_sentry_data_validates_payload_structure():
    with patch("nasa_sentry.get_http_session") as mock_get_session:
        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_session.get.return_value = mock_response
        mock_get_session.return_value = mock_session

        # Non-dict payload
        mock_response.json.return_value = ["not", "a", "dict"]
        with pytest.raises(ValueError, match="expected JSON object root"):
            nasa_sentry.fetch_sentry_data()

        # Missing 'data' key
        mock_response.json.return_value = {"signature": {}}
        with pytest.raises(ValueError, match="missing 'data' array"):
            nasa_sentry.fetch_sentry_data()

        # 'data' is not a list
        mock_response.json.return_value = {"signature": {}, "data": "not_a_list"}
        with pytest.raises(ValueError, match="missing 'data' array"):
            nasa_sentry.fetch_sentry_data()


def test_fetch_sentry_data_missing_signature_emits_warning(caplog):
    mock_payload = {"data": []}
    with patch("nasa_sentry.get_http_session") as mock_get_session:
        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.json.return_value = mock_payload
        mock_response.raise_for_status.return_value = None
        mock_session.get.return_value = mock_response
        mock_get_session.return_value = mock_session

        with caplog.at_level(logging.WARNING):
            data = nasa_sentry.fetch_sentry_data(run_id="run_sig")

        assert data == mock_payload
        assert any("missing 'signature' block" in record.message for record in caplog.records)


def test_upload_raw_to_s3_mocked():
    test_date = date(2026, 9, 25)
    metadata = {"source": "nasa_sentry_api", "run_id": "r1", "ingested_at": "t1"}

    with patch("boto3.client") as mock_boto:
        mock_s3 = MagicMock()
        mock_boto.return_value = mock_s3

        nasa_sentry.upload_raw_to_s3(snapshot_date=test_date, metadata=metadata)

        mock_boto.assert_called_once_with("s3")
        mock_s3.upload_file.assert_called_once_with(
            "sentry_risk_snapshot_raw.json",
            "nasa-asteroid-intelligence",
            "raw/sentry/risk_snapshot/year=2026/month=09/day=25/sentry_risk_snapshot_raw.json",
            ExtraArgs={"Metadata": metadata}
        )


def test_upload_processed_to_s3_mocked():
    test_date = date(2026, 9, 25)
    metadata = {"source": "nasa_sentry_api", "run_id": "r1", "ingested_at": "t1"}

    with patch("boto3.client") as mock_boto:
        mock_s3 = MagicMock()
        mock_boto.return_value = mock_s3

        nasa_sentry.upload_processed_to_s3(snapshot_date=test_date, metadata=metadata)

        mock_boto.assert_called_once_with("s3")
        mock_s3.upload_file.assert_called_once_with(
            "fact_sentry_risk_snapshot.parquet",
            "nasa-asteroid-intelligence",
            "processed/sentry/risk_snapshot/year=2026/month=09/day=25/fact_sentry_risk_snapshot.parquet",
            ExtraArgs={"Metadata": metadata}
        )


def test_sentry_circuit_breaker_halts_on_zero_valid():
    empty_payload = {"signature": {}, "data": []}
    with patch("nasa_sentry.fetch_sentry_data", return_value=empty_payload), \
         patch("nasa_sentry.save_raw_json") as mock_save_raw, \
         patch("nasa_sentry.save_to_parquet") as mock_save_parquet, \
         patch("nasa_sentry.upload_raw_to_s3") as mock_upload_raw, \
         patch("nasa_sentry.upload_processed_to_s3") as mock_upload_proc:

        exit_code = nasa_sentry.main(snapshot_date_str="2026-09-25")

        assert exit_code == 1
        mock_save_raw.assert_called_once()
        mock_save_parquet.assert_not_called()
        mock_upload_raw.assert_not_called()
        mock_upload_proc.assert_not_called()


def test_sentry_main_success_returns_0():
    with patch("nasa_sentry.fetch_sentry_data", return_value=SAMPLE_MODE_S_PAYLOAD) as mock_fetch, \
         patch("nasa_sentry.save_raw_json") as mock_save_raw, \
         patch("nasa_sentry.save_to_parquet") as mock_save_parquet, \
         patch("nasa_sentry.upload_raw_to_s3") as mock_upload_raw, \
         patch("nasa_sentry.upload_processed_to_s3") as mock_upload_proc:

        exit_code = nasa_sentry.main(snapshot_date_str="2026-09-25")

        assert exit_code == 0
        mock_fetch.assert_called_once()
        mock_save_raw.assert_called_once()
        mock_save_parquet.assert_called_once()
        mock_upload_raw.assert_called_once()
        mock_upload_proc.assert_called_once()


def test_sentry_cli_snapshot_date_propagation():
    with patch("nasa_sentry.fetch_sentry_data", return_value=SAMPLE_MODE_S_PAYLOAD), \
         patch("nasa_sentry.save_raw_json"), \
         patch("nasa_sentry.save_to_parquet"), \
         patch("nasa_sentry.upload_raw_to_s3") as mock_upload_raw, \
         patch("nasa_sentry.upload_processed_to_s3") as mock_upload_proc:

        exit_code = nasa_sentry.main(snapshot_date_str=" 2026-11-10 \r")

        assert exit_code == 0
        expected_date = date(2026, 11, 10)
        assert mock_upload_raw.call_args[1]["snapshot_date"] == expected_date
        assert mock_upload_proc.call_args[1]["snapshot_date"] == expected_date


def test_sentry_cli_invalid_date_returns_1():
    assert nasa_sentry.main(snapshot_date_str="invalid-date") == 1
    assert nasa_sentry.main(snapshot_date_str="2026-02-30") == 1


def test_sentry_omitted_date_defaults_to_utc_today():
    today_utc = datetime.now(timezone.utc).date()

    with patch("nasa_sentry.fetch_sentry_data", return_value=SAMPLE_MODE_S_PAYLOAD), \
         patch("nasa_sentry.save_raw_json"), \
         patch("nasa_sentry.save_to_parquet"), \
         patch("nasa_sentry.upload_raw_to_s3") as mock_upload_raw, \
         patch("nasa_sentry.upload_processed_to_s3") as mock_upload_proc:

        exit_code = nasa_sentry.main()

        assert exit_code == 0
        assert mock_upload_raw.call_args[1]["snapshot_date"] == today_utc
        assert mock_upload_proc.call_args[1]["snapshot_date"] == today_utc


def test_historical_snapshot_independence():
    """Verify that multiple historical snapshots record the same sentry_id independently."""
    single_item_payload = {"data": [make_valid_sentry_item(id="PERSISTENT_ASTEROID")]}

    records_day1, _, _ = nasa_sentry.extract_sentry_risk_snapshot(
        single_item_payload, "2026-09-01", "run_1", "2026-09-01T00:00:00Z"
    )
    records_day2, _, _ = nasa_sentry.extract_sentry_risk_snapshot(
        single_item_payload, "2026-09-02", "run_2", "2026-09-02T00:00:00Z"
    )

    assert len(records_day1) == 1
    assert len(records_day2) == 1
    assert records_day1[0]["sentry_id"] == "PERSISTENT_ASTEROID"
    assert records_day2[0]["sentry_id"] == "PERSISTENT_ASTEROID"
    assert records_day1[0]["snapshot_key"] == "2026-09-01"
    assert records_day2[0]["snapshot_key"] == "2026-09-02"


def test_upload_raw_to_s3_handles_client_error():
    client_error = ClientError({"Error": {"Code": "403", "Message": "AccessDenied"}}, "PutObject")
    with patch("boto3.client") as mock_boto:
        mock_s3 = MagicMock()
        mock_s3.upload_file.side_effect = client_error
        mock_boto.return_value = mock_s3

        with pytest.raises(ClientError):
            nasa_sentry.upload_raw_to_s3(snapshot_date=date(2026, 9, 25))


def test_upload_processed_to_s3_handles_client_error():
    client_error = ClientError({"Error": {"Code": "500", "Message": "InternalError"}}, "PutObject")
    with patch("boto3.client") as mock_boto:
        mock_s3 = MagicMock()
        mock_s3.upload_file.side_effect = client_error
        mock_boto.return_value = mock_s3

        with pytest.raises(ClientError):
            nasa_sentry.upload_processed_to_s3(snapshot_date=date(2026, 9, 25))
