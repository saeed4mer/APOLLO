"""Comprehensive unit and integration test suite for NASA/JPL SBDB ingestion pipeline.

Verifies the locked SBDB ingestion contract for nasa_sbdb.py across all 4 normalized
datasets, API query options, payload validations, dynamic element extraction,
physical-parameter EAV modeling, duplicate safeguard, and S3 partitioning.
"""

from datetime import date
import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

from botocore.exceptions import BotoCoreError, ClientError
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import requests

import nasa_sbdb

REPO_DIR = Path(__file__).resolve().parent.parent.parent


@pytest.fixture(autouse=True)
def _isolate_cwd(tmp_path, monkeypatch):
    """Run every test from a temp dir.

    main() writes cwd-relative default outputs (sbdb_batch_summary.json, raw
    JSON, Parquet); without this, tests overwrite real repo-root files.
    """
    monkeypatch.chdir(tmp_path)


def test_main_default_summary_path_does_not_touch_repo_root(tmp_path):
    report_file = REPO_DIR / "data" / "reports" / "sbdb_batch_summary.json"
    repo_summary = report_file if report_file.exists() else (REPO_DIR / "sbdb_batch_summary.json")
    before = repo_summary.read_bytes() if repo_summary.exists() else None

    with patch("nasa_sbdb.fetch_sbdb_data", side_effect=requests.exceptions.HTTPError("404")), \
         patch("nasa_sbdb.save_raw_json"), \
         patch("nasa_sbdb.write_parquet"), \
         patch("nasa_sbdb.upload_raw_to_s3"), \
         patch("nasa_sbdb.upload_processed_to_s3"), \
         patch("time.sleep"):
        assert nasa_sbdb.main(targets="T1") == 1

    assert (tmp_path / "sbdb_batch_summary.json").exists()
    after = repo_summary.read_bytes() if repo_summary.exists() else None
    assert after == before


# ---------------------------------------------------------------------------
# Test Fixtures & Payloads
# ---------------------------------------------------------------------------
SAMPLE_SBDB_2025_HX_PAYLOAD = {
    "signature": {"version": "1.3", "source": "NASA/JPL Small-Body Database (SBDB) API"},
    "object": {
        "kind": "au",
        "orbit_id": "4",
        "orbit_class": {"name": "Apollo", "code": "APO"},
        "pha": False,
        "neo": True,
        "fullname": "(2025 HX)",
        "spkid": "54527277",
        "des": "2025 HX",
        "prefix": None,
        "shortname": None,
    },
    "orbit": {
        "cov_epoch": "2460788.5",
        "source": "JPL",
        "producer": "Otto Matic",
        "equinox": "J2000",
        "sb_used": "SB441-N16",
        "moid": "0.000393",
        "rms": "0.35",
        "n_del_obs_used": None,
        "epoch": "2461200.5",
        "soln_date": "2025-04-25 08:35:30",
        "not_valid_before": None,
        "n_dop_obs_used": None,
        "elements": [
            {"units": None, "value": "0.395", "label": "e", "title": "eccentricity", "sigma": "0.00044", "name": "e"},
            {"name": "a", "title": "semi-major axis", "sigma": "0.00052", "label": "a", "value": "1.2", "units": "au"},
            {"name": "q", "sigma": "0.00021", "title": "perihelion distance", "label": "q", "units": "au", "value": "0.724"},
            {"label": "i", "value": "7.66", "units": "deg", "name": "i", "title": "inclination; angle with respect to x-y ecliptic plane", "sigma": "0.0085"},
            {"name": "om", "title": "longitude of the ascending node", "sigma": "0.00028", "label": "node", "units": "deg", "value": "31.7"},
            {"name": "w", "title": "argument of perihelion", "sigma": "0.0022", "label": "peri", "units": "deg", "value": "272"},
            {"units": "deg", "value": "263", "label": "M", "title": "mean anomaly", "sigma": "0.15", "name": "ma"},
            {"sigma": "0.29", "title": "time of perihelion passage", "name": "tp", "units": "TDB", "value": "2461329.257", "label": "tp"},
            {"sigma": "0.31", "title": "sidereal orbital period", "name": "per", "value": "478", "units": "d", "label": "period"},
            {"label": "n", "units": "deg/d", "value": "0.753", "name": "n", "sigma": "0.00049", "title": "mean motion"},
            {"label": "Q", "value": "1.67", "units": "au", "name": "ad", "sigma": "0.00072", "title": "aphelion distance"},
        ],
        "model_pars": [],
        "first_obs": "2025-04-21",
        "orbit_id": "4",
        "t_jup": "5.223",
        "pe_used": "DE441",
        "moid_jup": "3.59",
        "n_obs_used": 36,
        "comment": None,
        "not_valid_after": None,
        "last_obs": "2025-04-24",
        "condition_code": "7",
        "data_arc": "3",
        "two_body": None,
    },
    "phys_par": [
        {
            "ref": "MPO913959",
            "notes": "autocmod 3.0f",
            "name": "H",
            "title": "absolute magnitude",
            "sigma": ".29621",
            "value": "27.55",
            "units": None,
            "desc": "absolute magnitude (magnitude at 1 au from Sun and observer)",
        }
    ],
}

SAMPLE_SBDB_EROS_PAYLOAD = {
    "signature": {"version": "1.3", "source": "NASA/JPL Small-Body Database (SBDB) API"},
    "object": {
        "neo": True,
        "prefix": None,
        "pha": False,
        "orbit_id": "659",
        "shortname": "433 Eros",
        "fullname": "433 Eros (A898 PA)",
        "spkid": "2000433",
        "des": "433",
        "orbit_class": {"name": "Amor", "code": "AMO"},
        "kind": "an",
    },
    "orbit": {
        "first_obs": "1893-10-29",
        "pe_used": "DE441",
        "elements": [
            {"name": "e", "label": "e", "title": "eccentricity", "sigma": "9.4e-09", "units": None, "value": "0.223"},
            {"name": "a", "title": "semi-major axis", "label": "a", "units": "au", "value": "1.46", "sigma": "1.5e-10"},
            {"name": "q", "label": "q", "title": "perihelion distance", "units": "au", "value": "1.13", "sigma": "1.4e-08"},
            {"sigma": "1.2e-06", "units": "deg", "value": "10.8", "name": "i", "title": "inclination", "label": "i"},
            {"units": "deg", "value": "304", "sigma": "3.6e-06", "name": "om", "label": "node", "title": "longitude of ascending node"},
            {"sigma": "4e-06", "value": "179", "units": "deg", "label": "peri", "title": "argument of perihelion", "name": "w"},
        ],
        "n_dop_obs_used": 2,
        "n_obs_used": 9130,
        "model_pars": [],
        "moid_jup": "3.3",
        "source": "JPL",
        "t_jup": "4.582",
        "equinox": "J2000",
        "data_arc": "46582",
        "comment": None,
        "last_obs": "2021-05-13",
        "two_body": None,
        "not_valid_after": None,
        "condition_code": "0",
        "orbit_id": "659",
        "sb_used": "SB441-N16",
        "cov_epoch": "2453311.5",
        "epoch": "2461200.5",
        "soln_date": "2021-05-24 17:55:05",
        "n_del_obs_used": 4,
        "not_valid_before": None,
        "rms": "0.3",
        "moid": "0.149",
        "producer": "Giorgini",
    },
    "phys_par": [
        {"name": "H", "value": "10.40", "sigma": None, "units": None, "ref": "E2026L10", "notes": None, "title": "absolute magnitude", "desc": "absolute magnitude"},
        {"name": "diameter", "value": "16.84", "sigma": "0.06", "units": "km", "ref": "Yeomans et al.", "notes": "sphere", "title": "diameter", "desc": "effective body diameter"},
        {"name": "extent", "value": "34.4x11.2x11.2", "sigma": None, "units": "km", "ref": "Veverka et al.", "notes": None, "title": "extent", "desc": "triaxial body dimensions"},
        {"name": "albedo", "value": "0.25", "sigma": "0.06", "units": None, "ref": "Veverka et al.", "notes": "average", "title": "geometric albedo", "desc": "geometric albedo"},
        {"name": "rot_per", "value": "5.27", "sigma": None, "units": "h", "ref": "LCDB", "notes": "ref list", "title": "rotation period", "desc": "body rotation period"},
    ],
}


# ---------------------------------------------------------------------------
# 1. API Fetch & HTTP Tests
# ---------------------------------------------------------------------------
def test_fetch_sbdb_success_2025_hx():
    """Verify fetch_sbdb_data issues correct GET request with full-prec=1, phys-par=1 and alt-des=1."""
    with patch("nasa_sbdb.get_http_session") as mock_get_session:
        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.json.return_value = SAMPLE_SBDB_2025_HX_PAYLOAD
        mock_response.raise_for_status.return_value = None
        mock_session.get.return_value = mock_response
        mock_get_session.return_value = mock_session

        payload = nasa_sbdb.fetch_sbdb_data(target="2025 HX", id_type="sstr", run_id="run_test")

        assert payload == SAMPLE_SBDB_2025_HX_PAYLOAD
        mock_session.get.assert_called_once_with(
            "https://ssd-api.jpl.nasa.gov/sbdb.api",
            params={"sstr": "2025 HX", "phys-par": "1", "full-prec": "1", "alt-des": "1"},
            headers={"User-Agent": "NASA-Planetary-Defense-Platform/1.0"},
            timeout=15,
        )
        mock_response.raise_for_status.assert_called_once()


def test_fetch_sbdb_identifier_handling():
    """Verify sstr, spk, and des identifier types are correctly mapped to query parameters."""
    with patch("nasa_sbdb.get_http_session") as mock_get_session:
        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.json.return_value = SAMPLE_SBDB_2025_HX_PAYLOAD
        mock_response.raise_for_status.return_value = None
        mock_session.get.return_value = mock_response
        mock_get_session.return_value = mock_session

        # 1. SPK-ID lookup
        nasa_sbdb.fetch_sbdb_data(target="54527277", id_type="spk")
        assert mock_session.get.call_args[1]["params"]["spk"] == "54527277"
        assert "sstr" not in mock_session.get.call_args[1]["params"]

        # 2. Designation lookup
        nasa_sbdb.fetch_sbdb_data(target="2025 HX", id_type="des")
        assert mock_session.get.call_args[1]["params"]["des"] == "2025 HX"

        # 3. Invalid id_type
        with pytest.raises(ValueError, match="Invalid id_type"):
            nasa_sbdb.fetch_sbdb_data(target="2025 HX", id_type="invalid_type")


def test_fetch_sbdb_handles_ambiguous_300_query():
    """Verify ambiguous query response (code 300 / list) raises explicit ValueError."""
    ambiguous_payload = {
        "signature": {"version": "1.3"},
        "code": 300,
        "message": "specified query matched more than one object",
        "count": 2,
        "list": [
            {"pdes": "141P", "name": "141P/Machholz 2"},
            {"pdes": "141P-A", "name": "141P/Machholz 2-A"},
        ],
    }
    with patch("nasa_sbdb.get_http_session") as mock_get_session:
        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.json.return_value = ambiguous_payload
        mock_response.raise_for_status.return_value = None
        mock_session.get.return_value = mock_response
        mock_get_session.return_value = mock_session

        with pytest.raises(ValueError, match="Ambiguous SBDB query for target '141P'"):
            nasa_sbdb.fetch_sbdb_data(target="141P", id_type="sstr")


def test_fetch_sbdb_handles_404_or_object_not_found():
    """Verify object not found message raises descriptive ValueError."""
    not_found_payload = {
        "message": "specified object was not found",
        "code": 404,
    }
    with patch("nasa_sbdb.get_http_session") as mock_get_session:
        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.json.return_value = not_found_payload
        mock_response.raise_for_status.return_value = None
        mock_session.get.return_value = mock_response
        mock_get_session.return_value = mock_session

        with pytest.raises(ValueError, match="SBDB API error for target 'UNKNOWN_ASTEROID'"):
            nasa_sbdb.fetch_sbdb_data(target="UNKNOWN_ASTEROID")


def test_fetch_sbdb_handles_malformed_json_and_missing_sections():
    """Verify non-dict payload, missing object, or missing orbit raises ValueError."""
    with patch("nasa_sbdb.get_http_session") as mock_get_session:
        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_session.get.return_value = mock_response
        mock_get_session.return_value = mock_session

        # 1. Non-dict root
        mock_response.json.return_value = ["not", "a", "dict"]
        with pytest.raises(ValueError, match="expected JSON object root"):
            nasa_sbdb.fetch_sbdb_data()

        # 2. Missing object section
        mock_response.json.return_value = {"orbit": {}}
        with pytest.raises(ValueError, match="missing or malformed 'object' section"):
            nasa_sbdb.fetch_sbdb_data()

        # 3. Missing orbit section
        mock_response.json.return_value = {"object": {}}
        with pytest.raises(ValueError, match="missing or malformed 'orbit' section"):
            nasa_sbdb.fetch_sbdb_data()


def test_fetch_sbdb_missing_signature_emits_warning(caplog):
    """Verify missing signature emits warning log but does not halt."""
    payload_without_sig = {
        "object": SAMPLE_SBDB_2025_HX_PAYLOAD["object"],
        "orbit": SAMPLE_SBDB_2025_HX_PAYLOAD["orbit"],
    }
    with patch("nasa_sbdb.get_http_session") as mock_get_session:
        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.json.return_value = payload_without_sig
        mock_response.raise_for_status.return_value = None
        mock_session.get.return_value = mock_response
        mock_get_session.return_value = mock_session

        with caplog.at_level(logging.WARNING):
            data = nasa_sbdb.fetch_sbdb_data(run_id="run_sig")

        assert data == payload_without_sig
        assert any("missing 'signature' block" in record.message for record in caplog.records)


# ---------------------------------------------------------------------------
# 2. Extraction & Normalization Tests
# ---------------------------------------------------------------------------
def test_extract_sbdb_object_fields_2025_hx():
    """Verify extract_sbdb_object extracts all 14 fields conforming to SBDB_OBJECT_SCHEMA."""
    record = nasa_sbdb.extract_sbdb_object(
        payload=SAMPLE_SBDB_2025_HX_PAYLOAD,
        snapshot_key="2026-09-26",
        run_id="r1",
        snapshot_time="2026-09-26T00:00:00Z",
    )

    assert record["snapshot_key"] == "2026-09-26"
    assert record["run_id"] == "r1"
    assert record["snapshot_time"] == "2026-09-26T00:00:00Z"
    assert record["spkid"] == "54527277"
    assert record["designation"] == "2025 HX"
    assert record["fullname"] == "(2025 HX)"
    assert record["shortname"] is None
    assert record["object_kind"] == "au"
    assert record["is_neo"] is True
    assert record["is_pha"] is False
    assert record["orbit_class_code"] == "APO"
    assert record["orbit_class_name"] == "Apollo"
    assert record["orbit_id"] == "4"
    assert record["prefix"] is None
    assert record["alternate_designations"] is None  # payload carries no des_alt: not captured


def test_extract_sbdb_object_missing_identifiers_raises_value_error():
    """Verify missing spkid, des, orbit_class, or orbit_id raises ValueError."""
    bad_payload = {"object": {"des": "2025 HX"}}
    with pytest.raises(ValueError, match="missing or invalid 'spkid'"):
        nasa_sbdb.extract_sbdb_object(bad_payload, "2026-09-26", "r1", "t1")

    bad_payload2 = {"object": {"spkid": "54527277"}}
    with pytest.raises(ValueError, match="missing or invalid 'des'"):
        nasa_sbdb.extract_sbdb_object(bad_payload2, "2026-09-26", "r1", "t1")


def test_extract_sbdb_orbit_fields_2025_hx():
    """Verify extract_sbdb_orbit extracts all 21 fields conforming to SBDB_ORBIT_SCHEMA."""
    record = nasa_sbdb.extract_sbdb_orbit(
        payload=SAMPLE_SBDB_2025_HX_PAYLOAD,
        snapshot_key="2026-09-26",
        run_id="r1",
        snapshot_time="2026-09-26T00:00:00Z",
        spkid="54527277",
    )

    assert record["spkid"] == "54527277"
    assert record["orbit_id"] == "4"
    assert record["epoch_jd"] == 2461200.5
    assert record["equinox"] == "J2000"
    assert record["soln_date"] == "2025-04-25 08:35:30"
    assert record["orbit_source"] == "JPL"
    assert record["producer"] == "Otto Matic"
    assert record["first_obs"] == "2025-04-21"
    assert record["last_obs"] == "2025-04-24"
    assert record["data_arc_days"] == 3
    assert record["n_obs_used"] == 36
    assert record["condition_code"] == "7"
    assert record["rms"] == 0.35
    assert record["earth_moid_au"] == 0.000393
    assert record["jupiter_moid_au"] == 3.59
    assert record["t_jup"] == 5.223
    assert record["pe_used"] == "DE441"
    assert record["sb_used"] == "SB441-N16"


def test_extract_sbdb_orbit_invalid_epoch_and_metrics_raises():
    """Verify invalid epoch, negative RMS, or negative MOID raises ValueError."""
    bad_orbit = {"orbit": {"orbit_id": "1", "epoch": "-100", "equinox": "J2000"}}
    with pytest.raises(ValueError, match="invalid epoch"):
        nasa_sbdb.extract_sbdb_orbit(bad_orbit, "k", "r", "t", "spk1")

    bad_orbit2 = {
        "orbit": {
            "orbit_id": "1", "epoch": "2450000.5", "equinox": "J2000",
            "rms": "-0.5", "moid": "0.1", "n_obs_used": 10,
        }
    }
    with pytest.raises(ValueError, match="invalid rms"):
        nasa_sbdb.extract_sbdb_orbit(bad_orbit2, "k", "r", "t", "spk1")

    bad_orbit3 = {
        "orbit": {
            "orbit_id": "1", "epoch": "2450000.5", "equinox": "J2000",
            "rms": "0.5", "moid": "-0.01", "n_obs_used": 10,
        }
    }
    with pytest.raises(ValueError, match="invalid Earth MOID"):
        nasa_sbdb.extract_sbdb_orbit(bad_orbit3, "k", "r", "t", "spk1")


def test_extract_sbdb_orbit_elements_dynamic_extraction():
    """Verify dynamic orbital element extraction preserves all elements without assuming 11."""
    records = nasa_sbdb.extract_sbdb_orbit_elements(
        payload=SAMPLE_SBDB_2025_HX_PAYLOAD,
        snapshot_key="2026-09-26",
        run_id="r1",
        snapshot_time="2026-09-26T00:00:00Z",
        spkid="54527277",
        orbit_id="4",
        epoch_jd=2461200.5,
        equinox="J2000",
    )

    assert len(records) == 11
    e_rec = next(r for r in records if r["element_name"] == "e")
    assert e_rec["element_value"] == 0.395
    assert e_rec["sigma"] == 0.00044
    assert e_rec["label"] == "e"
    assert e_rec["title"] == "eccentricity"
    assert e_rec["epoch_jd"] == 2461200.5
    assert e_rec["equinox"] == "J2000"
    assert e_rec["orbit_id"] == "4"

    a_rec = next(r for r in records if r["element_name"] == "a")
    assert a_rec["element_value"] == 1.2
    assert a_rec["units"] == "au"


def test_extract_sbdb_orbit_elements_reduced_count():
    """Verify payload with only 2 or 6 elements extracts successfully without requiring 11."""
    payload_reduced = {
        "orbit": {
            "elements": [
                {"name": "e", "value": "0.1", "sigma": "0.001", "units": None},
                {"name": "q", "value": "1.05", "sigma": "0.002", "units": "au"},
            ]
        }
    }
    records = nasa_sbdb.extract_sbdb_orbit_elements(
        payload=payload_reduced,
        snapshot_key="k",
        run_id="r",
        snapshot_time="t",
        spkid="123",
        orbit_id="1",
        epoch_jd=2450000.5,
        equinox="J2000",
    )
    assert len(records) == 2
    assert records[0]["element_name"] == "e"
    assert records[1]["element_name"] == "q"


def test_extract_sbdb_orbit_elements_negative_eccentricity_raises():
    """Verify negative eccentricity raises ValueError."""
    bad_elements = {
        "orbit": {
            "elements": [{"name": "e", "value": "-0.05"}]
        }
    }
    with pytest.raises(ValueError, match="negative eccentricity"):
        nasa_sbdb.extract_sbdb_orbit_elements(bad_elements, "k", "r", "t", "123", "1", 2450000.5, "J2000")


def test_extract_sbdb_physical_parameters_eav_extraction():
    """Verify physical properties extraction into Table 4 EAV schema with numeric and string types."""
    records = nasa_sbdb.extract_sbdb_physical_parameters(
        payload=SAMPLE_SBDB_EROS_PAYLOAD,
        snapshot_key="2026-09-26",
        run_id="r1",
        snapshot_time="2026-09-26T00:00:00Z",
        spkid="2000433",
    )

    assert len(records) == 5

    h_rec = next(r for r in records if r["param_name"] == "H")
    assert h_rec["param_value_numeric"] == 10.40
    assert h_rec["param_value_raw"] == "10.40"
    assert h_rec["bib_reference"] == "E2026L10"
    assert h_rec["sigma"] is None

    diam_rec = next(r for r in records if r["param_name"] == "diameter")
    assert diam_rec["param_value_numeric"] == 16.84
    assert diam_rec["sigma"] == 0.06
    assert diam_rec["units"] == "km"

    # Non-numeric range string preservation
    extent_rec = next(r for r in records if r["param_name"] == "extent")
    assert extent_rec["param_value_numeric"] is None
    assert extent_rec["param_value_raw"] == "34.4x11.2x11.2"
    assert extent_rec["units"] == "km"


def test_extract_sbdb_physical_parameters_duplicate_param_name_fails_dq():
    """Verify duplicate param_name strictly raises ValueError and logs error before Parquet/S3."""
    dup_payload = {
        "phys_par": [
            {"name": "H", "value": "27.55"},
            {"name": "H", "value": "28.10"},
        ]
    }
    with pytest.raises(ValueError, match="duplicate physical parameter 'H'"):
        nasa_sbdb.extract_sbdb_physical_parameters(
            dup_payload, "2026-09-26", "r1", "2026-09-26T00:00:00Z", "54527277"
        )


def test_extract_sbdb_nullable_optional_fields():
    """Verify optional astronomical fields are gracefully stored as None when absent/null."""
    minimal_payload = {
        "object": {
            "spkid": "1001",
            "des": "1001 Test",
            "fullname": "1001 Test",
            "shortname": None,
            "kind": "au",
            "neo": True,
            "pha": False,
            "orbit_class": {"code": "APO", "name": "Apollo"},
            "orbit_id": "1",
            "prefix": None,
        },
        "orbit": {
            "orbit_id": "1",
            "epoch": "2460000.5",
            "equinox": "J2000",
            "soln_date": "2026-01-01",
            "first_obs": "2025-01-01",
            "last_obs": "2026-01-01",
            "n_obs_used": 50,
            "rms": "0.4",
            "moid": "0.01",
            "producer": None,
            "moid_jup": None,
            "t_jup": None,
            "data_arc": None,
            "pe_used": None,
            "sb_used": None,
            "elements": [{"name": "e", "value": "0.2"}],
        },
        "phys_par": [
            {"name": "H", "value": "20.0", "sigma": None, "units": None, "ref": None, "notes": None}
        ],
    }

    obj = nasa_sbdb.extract_sbdb_object(minimal_payload, "k", "r", "t")
    assert obj["shortname"] is None
    assert obj["prefix"] is None

    orb = nasa_sbdb.extract_sbdb_orbit(minimal_payload, "k", "r", "t", "1001")
    assert orb["producer"] is None
    assert orb["jupiter_moid_au"] is None
    assert orb["t_jup"] is None
    assert orb["data_arc_days"] is None

    phys = nasa_sbdb.extract_sbdb_physical_parameters(minimal_payload, "k", "r", "t", "1001")
    assert phys[0]["sigma"] is None
    assert phys[0]["units"] is None
    assert phys[0]["bib_reference"] is None
    assert phys[0]["notes"] is None


# ---------------------------------------------------------------------------
# 3. Schema & Parquet Generation Tests
# ---------------------------------------------------------------------------
def test_exact_pyarrow_schemas_match_contract():
    """Verify all 4 PyArrow schemas conform to the approved contract."""
    # 1. Object: 15 fields (alternate_designations added for identity resolution)
    assert len(nasa_sbdb.SBDB_OBJECT_SCHEMA) == 15
    assert nasa_sbdb.SBDB_OBJECT_SCHEMA.field("alternate_designations").type == pa.list_(pa.string())
    assert nasa_sbdb.SBDB_OBJECT_SCHEMA.field("alternate_designations").nullable
    assert nasa_sbdb.SBDB_OBJECT_SCHEMA.field("snapshot_key").type == pa.string()
    assert nasa_sbdb.SBDB_OBJECT_SCHEMA.field("spkid").type == pa.string()
    assert nasa_sbdb.SBDB_OBJECT_SCHEMA.field("is_neo").type == pa.bool_()
    assert nasa_sbdb.SBDB_OBJECT_SCHEMA.field("is_pha").type == pa.bool_()

    # 2. Orbit: 21 fields
    assert len(nasa_sbdb.SBDB_ORBIT_SCHEMA) == 21
    assert nasa_sbdb.SBDB_ORBIT_SCHEMA.field("epoch_jd").type == pa.float64()
    assert nasa_sbdb.SBDB_ORBIT_SCHEMA.field("rms").type == pa.float64()
    assert nasa_sbdb.SBDB_ORBIT_SCHEMA.field("earth_moid_au").type == pa.float64()

    # 3. Elements: 13 fields
    assert len(nasa_sbdb.SBDB_ORBIT_ELEMENT_SCHEMA) == 13
    assert nasa_sbdb.SBDB_ORBIT_ELEMENT_SCHEMA.field("element_value").type == pa.float64()

    # 4. Physical: 13 fields
    assert len(nasa_sbdb.SBDB_PHYS_PAR_SCHEMA) == 13
    assert nasa_sbdb.SBDB_PHYS_PAR_SCHEMA.field("param_value_numeric").type == pa.float64()
    assert nasa_sbdb.SBDB_PHYS_PAR_SCHEMA.field("param_value_raw").type == pa.string()


def test_snappy_parquet_generation(tmp_path):
    """Verify Parquet generation creates valid Snappy-compressed files matching schemas."""
    obj_rec = nasa_sbdb.extract_sbdb_object(SAMPLE_SBDB_2025_HX_PAYLOAD, "2026-09-26", "r1", "t1")
    orb_rec = nasa_sbdb.extract_sbdb_orbit(SAMPLE_SBDB_2025_HX_PAYLOAD, "2026-09-26", "r1", "t1", "54527277")
    elem_recs = nasa_sbdb.extract_sbdb_orbit_elements(
        SAMPLE_SBDB_2025_HX_PAYLOAD, "2026-09-26", "r1", "t1", "54527277", "4", 2461200.5, "J2000"
    )
    phys_recs = nasa_sbdb.extract_sbdb_physical_parameters(SAMPLE_SBDB_2025_HX_PAYLOAD, "2026-09-26", "r1", "t1", "54527277")

    out_obj = str(tmp_path / "fact_sbdb_object_snapshot.parquet")
    out_orb = str(tmp_path / "fact_sbdb_orbit.parquet")
    out_elem = str(tmp_path / "fact_sbdb_orbit_element.parquet")
    out_phys = str(tmp_path / "fact_sbdb_physical_parameter.parquet")

    nasa_sbdb.write_parquet([obj_rec], nasa_sbdb.SBDB_OBJECT_SCHEMA, out_obj)
    nasa_sbdb.write_parquet([orb_rec], nasa_sbdb.SBDB_ORBIT_SCHEMA, out_orb)
    nasa_sbdb.write_parquet(elem_recs, nasa_sbdb.SBDB_ORBIT_ELEMENT_SCHEMA, out_elem)
    nasa_sbdb.write_parquet(phys_recs, nasa_sbdb.SBDB_PHYS_PAR_SCHEMA, out_phys)

    # Validate read-back with PyArrow
    f_obj = pq.ParquetFile(out_obj)
    assert f_obj.metadata.num_rows == 1
    assert f_obj.metadata.row_group(0).column(0).compression.lower() == "snappy"

    f_elem = pq.ParquetFile(out_elem)
    assert f_elem.metadata.num_rows == 11
    assert f_elem.metadata.row_group(0).column(0).compression.lower() == "snappy"

    f_phys = pq.ParquetFile(out_phys)
    assert f_phys.metadata.num_rows == 1
    assert f_phys.metadata.row_group(0).column(0).compression.lower() == "snappy"


def test_save_raw_json(tmp_path):
    """Verify save_raw_json writes formatted JSON payload."""
    raw_path = str(tmp_path / "sbdb_raw_test.json")
    nasa_sbdb.save_raw_json(SAMPLE_SBDB_2025_HX_PAYLOAD, filename=raw_path)

    with open(raw_path, encoding="utf-8") as f:
        data = json.load(f)
    assert data["object"]["spkid"] == "54527277"


# ---------------------------------------------------------------------------
# 4. S3 Upload & Key Generation Tests
# ---------------------------------------------------------------------------
def test_upload_raw_to_s3_exact_path_and_metadata():
    """Verify raw S3 upload path matches raw/sbdb/object/... specification."""
    test_date = date(2026, 9, 26)
    metadata = {"source": "nasa_jpl_sbdb_api", "run_id": "r1", "ingested_at": "t1"}

    with patch("boto3.client") as mock_boto:
        mock_s3 = MagicMock()
        mock_boto.return_value = mock_s3

        nasa_sbdb.upload_raw_to_s3(
            local_file_path="sbdb_raw_54527277.json",
            snapshot_date=test_date,
            spkid="54527277",
            metadata=metadata,
        )

        mock_boto.assert_called_once_with("s3")
        mock_s3.upload_file.assert_called_once_with(
            "sbdb_raw_54527277.json",
            "nasa-asteroid-intelligence",
            "raw/sbdb/object/year=2026/month=09/day=26/spkid=54527277/sbdb_raw_54527277.json",
            ExtraArgs={"Metadata": metadata},
        )


def test_upload_processed_to_s3_exact_paths():
    """Verify processed S3 uploads generate exact isolated namespaces for all 4 tables."""
    test_date = date(2026, 9, 26)
    metadata = {"source": "nasa_jpl_sbdb_api", "run_id": "r1"}

    tables = [
        "fact_sbdb_object_snapshot",
        "fact_sbdb_orbit",
        "fact_sbdb_orbit_element",
        "fact_sbdb_physical_parameter",
    ]

    with patch("boto3.client") as mock_boto:
        mock_s3 = MagicMock()
        mock_boto.return_value = mock_s3

        for tbl in tables:
            local_name = f"{tbl}.parquet"
            nasa_sbdb.upload_processed_to_s3(
                table_name=tbl,
                local_file_path=local_name,
                snapshot_date=test_date,
                metadata=metadata,
            )

        assert mock_s3.upload_file.call_count == 4
        uploaded_keys = [c[0][2] for c in mock_s3.upload_file.call_args_list]

        for tbl in tables:
            expected_key = f"processed/sbdb/{tbl}/year=2026/month=09/day=26/{tbl}.parquet"
            assert expected_key in uploaded_keys


def test_upload_raw_to_s3_handles_client_error():
    """Verify ClientError during S3 upload is logged with redaction and re-raised."""
    client_error = ClientError({"Error": {"Code": "403", "Message": "AccessDenied"}}, "PutObject")
    with patch("boto3.client") as mock_boto:
        mock_s3 = MagicMock()
        mock_s3.upload_file.side_effect = client_error
        mock_boto.return_value = mock_s3

        with pytest.raises(ClientError):
            nasa_sbdb.upload_raw_to_s3(
                local_file_path="sbdb_raw.json",
                snapshot_date=date(2026, 9, 26),
                spkid="123",
            )


def test_upload_processed_to_s3_handles_boto_error():
    """Verify BotoCoreError during processed upload is logged with redaction and re-raised."""
    boto_err = BotoCoreError()
    with patch("boto3.client") as mock_boto:
        mock_s3 = MagicMock()
        mock_s3.upload_file.side_effect = boto_err
        mock_boto.return_value = mock_s3

        with pytest.raises(BotoCoreError):
            nasa_sbdb.upload_processed_to_s3(
                table_name="fact_sbdb_orbit",
                local_file_path="fact_sbdb_orbit.parquet",
                snapshot_date=date(2026, 9, 26),
            )


# ---------------------------------------------------------------------------
# 5. Pipeline Orchestration, CLI, & Circuit Breaker Tests
# ---------------------------------------------------------------------------
def test_cli_target_and_id_type_override():
    """Verify CLI target and id-type arguments propagate to fetch_sbdb_data."""
    with patch("nasa_sbdb.fetch_sbdb_data", return_value=SAMPLE_SBDB_EROS_PAYLOAD) as mock_fetch, \
         patch("nasa_sbdb.save_raw_json"), \
         patch("nasa_sbdb.write_parquet"), \
         patch("nasa_sbdb.upload_raw_to_s3"), \
         patch("nasa_sbdb.upload_processed_to_s3"):

        exit_code = nasa_sbdb.main(target="433", id_type="des", snapshot_date_str="2026-09-26")

        assert exit_code == 0
        mock_fetch.assert_called_once_with(target="433", id_type="des", run_id=mock_fetch.call_args[1]["run_id"])


def test_cli_snapshot_date_propagation():
    """Verify snapshot-date CLI override propagates to S3 uploads."""
    with patch("nasa_sbdb.fetch_sbdb_data", return_value=SAMPLE_SBDB_2025_HX_PAYLOAD), \
         patch("nasa_sbdb.save_raw_json"), \
         patch("nasa_sbdb.write_parquet"), \
         patch("nasa_sbdb.upload_raw_to_s3") as mock_upload_raw, \
         patch("nasa_sbdb.upload_processed_to_s3") as mock_upload_proc:

        exit_code = nasa_sbdb.main(snapshot_date_str="2026-11-15")

        assert exit_code == 0
        assert mock_upload_raw.call_args[1]["snapshot_date"] == date(2026, 11, 15)
        assert mock_upload_proc.call_args[1]["snapshot_date"] == date(2026, 11, 15)


def test_cli_invalid_date_returns_1():
    """Verify invalid snapshot date string logs error and returns exit code 1."""
    assert nasa_sbdb.main(snapshot_date_str="not-a-date") == 1
    assert nasa_sbdb.main(snapshot_date_str="2026-02-30") == 1


def test_circuit_breaker_halts_before_parquet_and_s3_on_fetch_failure():
    """Verify fetch failure halts pipeline immediately before raw/parquet/S3 writes."""
    with patch("nasa_sbdb.fetch_sbdb_data", side_effect=requests.exceptions.HTTPError("500 Server Error")), \
         patch("nasa_sbdb.save_raw_json") as mock_raw, \
         patch("nasa_sbdb.write_parquet") as mock_pq, \
         patch("nasa_sbdb.upload_raw_to_s3") as mock_up_raw, \
         patch("nasa_sbdb.upload_processed_to_s3") as mock_up_proc:

        exit_code = nasa_sbdb.main()

        assert exit_code == 1
        mock_raw.assert_not_called()
        mock_pq.assert_not_called()
        mock_up_raw.assert_not_called()
        mock_up_proc.assert_not_called()


def test_circuit_breaker_halts_before_parquet_and_s3_on_duplicate_dq_failure():
    """Verify duplicate physical parameter DQ failure halts before Parquet/S3 writes."""
    dup_payload = {
        "object": SAMPLE_SBDB_2025_HX_PAYLOAD["object"],
        "orbit": SAMPLE_SBDB_2025_HX_PAYLOAD["orbit"],
        "phys_par": [
            {"name": "H", "value": "27.55"},
            {"name": "H", "value": "28.00"},
        ],
    }

    with patch("nasa_sbdb.fetch_sbdb_data", return_value=dup_payload), \
         patch("nasa_sbdb.save_raw_json") as mock_raw, \
         patch("nasa_sbdb.write_parquet") as mock_pq, \
         patch("nasa_sbdb.upload_raw_to_s3") as mock_up_raw, \
         patch("nasa_sbdb.upload_processed_to_s3") as mock_up_proc:

        exit_code = nasa_sbdb.main()

        assert exit_code == 1
        mock_raw.assert_not_called()
        mock_pq.assert_not_called()
        mock_up_raw.assert_not_called()
        mock_up_proc.assert_not_called()


def test_main_success_returns_0_and_lineage_metadata():
    """Verify full end-to-end execution succeeds with code 0 and exact lineage metadata."""
    with patch("nasa_sbdb.fetch_sbdb_data", return_value=SAMPLE_SBDB_2025_HX_PAYLOAD), \
         patch("nasa_sbdb.save_raw_json") as mock_raw, \
         patch("nasa_sbdb.write_parquet") as mock_pq, \
         patch("nasa_sbdb.upload_raw_to_s3") as mock_up_raw, \
         patch("nasa_sbdb.upload_processed_to_s3") as mock_up_proc:

        exit_code = nasa_sbdb.main()

        assert exit_code == 0
        mock_raw.assert_called_once()
        assert mock_pq.call_count == 4
        mock_up_raw.assert_called_once()
        assert mock_up_proc.call_count == 4

        # Verify lineage metadata attached to uploads
        raw_meta = mock_up_raw.call_args[1]["metadata"]
        assert raw_meta["source"] == "nasa_jpl_sbdb_api"
        assert len(raw_meta["run_id"]) == 12
        assert "ingested_at" in raw_meta


def test_idempotent_same_snapshot_key_behavior():
    """Verify executing twice on same snapshot date targets identical deterministic S3 keys."""
    test_date = date(2026, 9, 26)
    with patch("nasa_sbdb.upload_file_to_s3") as mock_upload, \
         patch("boto3.client"):

        nasa_sbdb.upload_raw_to_s3("raw.json", test_date, "54527277")
        key_run1 = mock_upload.call_args[1]["s3_key"]

        nasa_sbdb.upload_raw_to_s3("raw.json", test_date, "54527277")
        key_run2 = mock_upload.call_args[1]["s3_key"]

        assert key_run1 == key_run2
        assert key_run1 == "raw/sbdb/object/year=2026/month=09/day=26/spkid=54527277/raw.json"


# ---------------------------------------------------------------------------
# 6. Step 3A: Native Batch Support & Target Resolution Tests
# ---------------------------------------------------------------------------
def test_single_target_argument_parsing():
    """Verify existing single-target CLI argument parsing remains fully functional."""
    args = nasa_sbdb.parse_args(["--target", "2010 TW54"])
    assert args.target == "2010 TW54"
    assert args.targets is None
    assert args.targets_file is None
    assert args.id_type == "sstr"

    # Default invocation without flags
    args_default = nasa_sbdb.parse_args([])
    assert args_default.target is None
    assert args_default.targets is None
    assert args_default.targets_file is None


def test_targets_comma_separated_parsing():
    """Verify --targets parsing extracts list of targets."""
    args = nasa_sbdb.parse_args(["--targets", "2010 TW54,2025 HX,2008 ST"])
    assert args.targets == "2010 TW54,2025 HX,2008 ST"
    assert args.target is None
    assert args.targets_file is None


def test_targets_file_parsing(tmp_path):
    """Verify --targets-file parsing accepts file path."""
    target_file = tmp_path / "targets.txt"
    target_file.write_text("2010 TW54\n2025 HX\n", encoding="utf-8")

    args = nasa_sbdb.parse_args(["--targets-file", str(target_file)])
    assert args.targets_file == str(target_file)
    assert args.target is None
    assert args.targets is None


def test_target_resolution_precedence(tmp_path):
    """Verify target resolution precedence: file > targets > target > default."""
    target_file = tmp_path / "precedence_targets.txt"
    target_file.write_text("FileTarget1\nFileTarget2\n", encoding="utf-8")

    # 1. targets_file wins over targets and target
    res1 = nasa_sbdb.resolve_targets(
        target="SingleTarget",
        targets="ListTarget1,ListTarget2",
        targets_file=str(target_file),
    )
    assert res1 == ["FileTarget1", "FileTarget2"]

    # 2. targets wins over target
    res2 = nasa_sbdb.resolve_targets(
        target="SingleTarget",
        targets="ListTarget2,ListTarget1",
    )
    assert res2 == ["ListTarget1", "ListTarget2"]

    # 3. target wins over default
    res3 = nasa_sbdb.resolve_targets(
        target="SingleTarget",
    )
    assert res3 == ["SingleTarget"]

    # 4. default target when all are None
    res4 = nasa_sbdb.resolve_targets()
    assert res4 == [nasa_sbdb.DEFAULT_TARGET]


def test_target_deduplication_and_normalization():
    """Verify deduplication, whitespace normalization, and deterministic alphanumeric sorting."""
    raw_input = "  2025 HX , (2010 TW54) , 2008 ST , 2025 HX ,   (2008 ST)   "
    res = nasa_sbdb.resolve_targets(targets=raw_input)

    assert res == ["2008 ST", "2010 TW54", "2025 HX"]


def test_target_resolution_errors(tmp_path):
    """Verify invalid target inputs raise proper exceptions or return code 1."""
    # Non-existent file
    with pytest.raises(FileNotFoundError):
        nasa_sbdb.resolve_targets(targets_file="nonexistent_targets_file.txt")

    # Empty file
    empty_file = tmp_path / "empty_targets.txt"
    empty_file.write_text("# only comments\n   \n", encoding="utf-8")
    with pytest.raises(ValueError):
        nasa_sbdb.resolve_targets(targets_file=str(empty_file))

    # Empty comma string
    with pytest.raises(ValueError):
        nasa_sbdb.resolve_targets(targets="  ,  ,  ")

    # Main returns 1 on target resolution failure
    assert nasa_sbdb.main(targets_file="nonexistent_targets_file.txt") == 1


def test_batch_execution_multi_target_aggregation():
    """Verify multi-target batch ingestion consolidates records into single Parquet files."""
    def mock_fetch(target, id_type="sstr", run_id=None):
        if target == "2025 HX":
            return SAMPLE_SBDB_2025_HX_PAYLOAD
        elif target == "433":
            return SAMPLE_SBDB_EROS_PAYLOAD
        raise ValueError(f"Unexpected target {target}")

    with patch("nasa_sbdb.fetch_sbdb_data", side_effect=mock_fetch), \
         patch("nasa_sbdb.save_raw_json") as mock_raw, \
         patch("nasa_sbdb.write_parquet") as mock_pq, \
         patch("nasa_sbdb.upload_raw_to_s3") as mock_up_raw, \
         patch("nasa_sbdb.upload_processed_to_s3") as mock_up_proc, \
         patch("time.sleep") as mock_sleep:

        exit_code = nasa_sbdb.main(targets="2025 HX, 433")

        assert exit_code == 0
        mock_sleep.assert_called_once_with(0.2)

        # 2 raw JSON saves and uploads (one per object)
        assert mock_raw.call_count == 2
        assert mock_up_raw.call_count == 2

        # 4 consolidated Parquets written locally
        assert mock_pq.call_count == 4

        # Inspect the calls to write_parquet
        # Call 1: fact_sbdb_object_snapshot.parquet should have 2 records
        obj_call_records = mock_pq.call_args_list[0][0][0]
        assert len(obj_call_records) == 2
        spkids = {r["spkid"] for r in obj_call_records}
        assert spkids == {"54527277", "2000433"}

        # Call 2: fact_sbdb_orbit.parquet should have 2 records
        orbit_call_records = mock_pq.call_args_list[1][0][0]
        assert len(orbit_call_records) == 2

        # Call 3: fact_sbdb_orbit_element.parquet should have elements from both objects
        elem_call_records = mock_pq.call_args_list[2][0][0]
        assert len(elem_call_records) > 10
        elem_spkids = {r["spkid"] for r in elem_call_records}
        assert elem_spkids == {"54527277", "2000433"}

        # Call 4: fact_sbdb_physical_parameter.parquet should have physical records
        phys_call_records = mock_pq.call_args_list[3][0][0]
        assert len(phys_call_records) >= 1

        # 4 consolidated processed Parquets uploaded to S3
        assert mock_up_proc.call_count == 4
        uploaded_tables = [c[1]["table_name"] for c in mock_up_proc.call_args_list]
        assert uploaded_tables == [
            "fact_sbdb_object_snapshot",
            "fact_sbdb_orbit",
            "fact_sbdb_orbit_element",
            "fact_sbdb_physical_parameter",
        ]


def make_mock_payload(spkid, des):
    """Helper to generate mock SBDB payload with custom spkid and designation."""
    payload = json.loads(json.dumps(SAMPLE_SBDB_2025_HX_PAYLOAD))
    payload["object"]["spkid"] = str(spkid)
    payload["object"]["des"] = str(des)
    payload["object"]["fullname"] = f"({des})"
    return payload


def test_batch_failure_gate_zero_success_fails():
    """Verify 0/N success halts pipeline with exit code 1 and no processed outputs."""
    with patch("nasa_sbdb.fetch_sbdb_data", side_effect=requests.exceptions.HTTPError("500 Server Error")), \
         patch("nasa_sbdb.save_raw_json") as mock_raw, \
         patch("nasa_sbdb.write_parquet") as mock_pq, \
         patch("nasa_sbdb.upload_raw_to_s3") as mock_up_raw, \
         patch("nasa_sbdb.upload_processed_to_s3") as mock_up_proc, \
         patch("time.sleep"):

        exit_code = nasa_sbdb.main(targets="OBJ1, OBJ2, OBJ3")

        assert exit_code == 1
        mock_raw.assert_not_called()
        mock_pq.assert_not_called()
        mock_up_raw.assert_not_called()
        mock_up_proc.assert_not_called()


def test_batch_failure_gate_exactly_25_percent_fails():
    """Verify exactly 25.0% failure (1 of 4 failed) triggers circuit breaker and exits 1."""
    def mock_fetch(target, id_type="sstr", run_id=None):
        if target in ("T1", "T2", "T3"):
            return make_mock_payload(spkid=f"100{target}", des=target)
        elif target == "T4":
            raise requests.exceptions.HTTPError("404 Not Found")
        raise ValueError(f"Unexpected target {target}")

    with patch("nasa_sbdb.fetch_sbdb_data", side_effect=mock_fetch), \
         patch("nasa_sbdb.save_raw_json") as mock_raw, \
         patch("nasa_sbdb.write_parquet") as mock_pq, \
         patch("nasa_sbdb.upload_raw_to_s3") as mock_up_raw, \
         patch("nasa_sbdb.upload_processed_to_s3") as mock_up_proc, \
         patch("time.sleep"):

        exit_code = nasa_sbdb.main(targets="T1, T2, T3, T4")

        # 1/4 = 25.0% failure -> fails
        assert exit_code == 1
        # Raw payloads for the 3 succeeded targets were captured locally
        assert mock_raw.call_count == 3
        # Consolidated processed outputs MUST NOT be written or uploaded to S3
        mock_pq.assert_not_called()
        mock_up_raw.assert_not_called()
        mock_up_proc.assert_not_called()


def test_batch_failure_gate_just_below_25_percent_succeeds():
    """Verify failure rate just below 25% (1 of 5 failed = 20.0%) succeeds and publishes outputs."""
    def mock_fetch(target, id_type="sstr", run_id=None):
        if target in ("T1", "T2", "T3", "T4"):
            return make_mock_payload(spkid=f"100{target}", des=target)
        elif target == "T5":
            raise requests.exceptions.HTTPError("404 Not Found")
        raise ValueError(f"Unexpected target {target}")

    with patch("nasa_sbdb.fetch_sbdb_data", side_effect=mock_fetch), \
         patch("nasa_sbdb.save_raw_json") as mock_raw, \
         patch("nasa_sbdb.write_parquet") as mock_pq, \
         patch("nasa_sbdb.upload_raw_to_s3") as mock_up_raw, \
         patch("nasa_sbdb.upload_processed_to_s3") as mock_up_proc, \
         patch("time.sleep"):

        exit_code = nasa_sbdb.main(targets="T1, T2, T3, T4, T5")

        # 1/5 = 20.0% failure -> succeeds!
        assert exit_code == 0
        assert mock_raw.call_count == 4
        assert mock_up_raw.call_count == 4
        assert mock_pq.call_count == 4
        assert mock_up_proc.call_count == 4

        # Consolidated object table has 4 records
        obj_records = mock_pq.call_args_list[0][0][0]
        assert len(obj_records) == 4


def test_batch_failure_gate_above_25_percent_fails():
    """Verify failure rate above 25% (2 of 4 failed = 50.0%) halts pipeline and exits 1."""
    def mock_fetch(target, id_type="sstr", run_id=None):
        if target in ("T1", "T2"):
            return make_mock_payload(spkid=f"100{target}", des=target)
        elif target in ("T3", "T4"):
            raise requests.exceptions.HTTPError("500 Server Error")
        raise ValueError(f"Unexpected target {target}")

    with patch("nasa_sbdb.fetch_sbdb_data", side_effect=mock_fetch), \
         patch("nasa_sbdb.save_raw_json") as mock_raw, \
         patch("nasa_sbdb.write_parquet") as mock_pq, \
         patch("nasa_sbdb.upload_raw_to_s3") as mock_up_raw, \
         patch("nasa_sbdb.upload_processed_to_s3") as mock_up_proc, \
         patch("time.sleep"):

        exit_code = nasa_sbdb.main(targets="T1, T2, T3, T4")

        # 2/4 = 50.0% failure -> fails
        assert exit_code == 1
        assert mock_raw.call_count == 2
        mock_pq.assert_not_called()
        mock_up_raw.assert_not_called()
        mock_up_proc.assert_not_called()


def test_sbdb_batch_summary_emission_success(tmp_path):
    summary_file = tmp_path / "custom_sbdb_summary.json"
    with patch("nasa_sbdb.fetch_sbdb_data", return_value=SAMPLE_SBDB_2025_HX_PAYLOAD), \
         patch("nasa_sbdb.save_raw_json"), \
         patch("nasa_sbdb.write_parquet"), \
         patch("nasa_sbdb.upload_raw_to_s3"), \
         patch("nasa_sbdb.upload_processed_to_s3"):

        exit_code = nasa_sbdb.main(
            target="2025 HX",
            snapshot_date_str="2026-09-28",
            summary_filename=str(summary_file)
        )
        assert exit_code == 0
        assert summary_file.exists()

        with open(summary_file, "r", encoding="utf-8") as f:
            summary = json.load(f)

        assert summary["total_targets"] == 1
        assert summary["successful_targets_count"] == 1
        assert summary["failed_targets_count"] == 0
        assert summary["failure_rate_pct"] == 0.0
        assert summary["circuit_breaker_passed"] is True
        assert summary["successful_targets"] == ["2025 HX"]
        assert summary["snapshot_key"] == "2026-09-28"
        assert len(summary["output_tables"]) == 4


def test_sbdb_batch_summary_emission_failure(tmp_path):
    summary_file = tmp_path / "failed_sbdb_summary.json"
    def mock_fetch(target, id_type="sstr", run_id=None):
        if target == "T1":
            return make_mock_payload(spkid="1001", des="T1")
        raise requests.exceptions.HTTPError("500 Server Error")

    with patch("nasa_sbdb.fetch_sbdb_data", side_effect=mock_fetch), \
         patch("nasa_sbdb.save_raw_json"), \
         patch("nasa_sbdb.write_parquet") as mock_pq, \
         patch("nasa_sbdb.upload_raw_to_s3") as mock_up_raw, \
         patch("nasa_sbdb.upload_processed_to_s3") as mock_up_proc, \
         patch("time.sleep"):

        # 1 success (T1), 1 failure (T2) -> 50% failure rate >= 25% threshold
        exit_code = nasa_sbdb.main(
            targets="T1, T2",
            snapshot_date_str="2026-09-28",
            summary_filename=str(summary_file)
        )
        assert exit_code == 1
        assert summary_file.exists()

        with open(summary_file, "r", encoding="utf-8") as f:
            summary = json.load(f)

        assert summary["total_targets"] == 2
        assert summary["successful_targets_count"] == 1
        assert summary["failed_targets_count"] == 1
        assert summary["failure_rate_pct"] == 50.0
        assert summary["circuit_breaker_passed"] is False
        assert summary["successful_targets"] == ["T1"]
        assert summary["failed_targets"] == ["T2"]

        # Ensure Parquet and S3 uploads were suppressed
        mock_pq.assert_not_called()
        mock_up_raw.assert_not_called()
        mock_up_proc.assert_not_called()


def test_sbdb_batch_summary_write_failure_halts_pipeline(tmp_path, caplog):
    summary_file = tmp_path / "summary_write_fail.json"

    with patch("nasa_sbdb.fetch_sbdb_data", return_value=SAMPLE_SBDB_2025_HX_PAYLOAD), \
         patch("nasa_sbdb.save_raw_json") as mock_raw, \
         patch("nasa_sbdb.save_sbdb_summary", side_effect=IOError("Disk write error")), \
         patch("nasa_sbdb.write_parquet") as mock_pq, \
         patch("nasa_sbdb.upload_raw_to_s3") as mock_up_raw, \
         patch("nasa_sbdb.upload_processed_to_s3") as mock_up_proc, \
         patch("time.sleep"):

        with caplog.at_level(logging.ERROR):
            exit_code = nasa_sbdb.main(
                target="2025 HX",
                snapshot_date_str="2026-09-28",
                summary_filename=str(summary_file)
            )

        assert exit_code == 1
        # Raw payload was saved locally before summary write
        assert mock_raw.called
        # Production Parquets and S3 uploads must be suppressed
        mock_pq.assert_not_called()
        mock_up_raw.assert_not_called()
        mock_up_proc.assert_not_called()

        # Must log ERROR, not WARNING
        assert any("Failed to write SBDB authoritative batch summary" in record.message and record.levelname == "ERROR"
                   for record in caplog.records)


# ---------------------------------------------------------------------------
# Phase 1 Step 3b — NEO/PHA flag tri-state survives ingestion
# ---------------------------------------------------------------------------
_MISSING = object()


def _object_payload_with_flags(neo, pha):
    obj = {k: v for k, v in SAMPLE_SBDB_2025_HX_PAYLOAD["object"].items() if k not in ("neo", "pha")}
    if neo is not _MISSING:
        obj["neo"] = neo
    if pha is not _MISSING:
        obj["pha"] = pha
    return {**SAMPLE_SBDB_2025_HX_PAYLOAD, "object": obj}


@pytest.mark.parametrize(
    "raw,expected",
    [(True, True), (False, False), (None, None), (_MISSING, None), ("false", None)],
    ids=["explicit_true", "explicit_false", "json_null", "key_missing", "non_boolean"],
)
def test_extract_sbdb_object_preserves_flag_tri_state(raw, expected):
    """Explicit booleans pass through; missing/null/non-boolean flags are unknown (None), never False."""
    record = nasa_sbdb.extract_sbdb_object(
        payload=_object_payload_with_flags(raw, raw),
        snapshot_key="2026-09-26",
        run_id="r1",
        snapshot_time="2026-09-26T00:00:00Z",
    )
    assert record["is_neo"] is expected
    assert record["is_pha"] is expected


def test_sbdb_object_flag_tri_state_survives_parquet_round_trip(tmp_path):
    """True, False and None are all written and read back distinctly through the real schema/writer."""
    from pipeline_utils import write_parquet

    records = [
        nasa_sbdb.extract_sbdb_object(_object_payload_with_flags(raw, raw), "2026-09-26", f"r{i}", "t")
        for i, raw in enumerate([True, False, _MISSING])
    ]
    out = tmp_path / "fact_sbdb_object_snapshot.parquet"
    write_parquet(records, nasa_sbdb.SBDB_OBJECT_SCHEMA, str(out))

    table = pq.read_table(out)
    assert table.column("is_neo").to_pylist() == [True, False, None]
    assert table.column("is_pha").to_pylist() == [True, False, None]


# ---------------------------------------------------------------------------
# M8: Alternate designations, same-object de-duplication, local backfill without S3
# ---------------------------------------------------------------------------
def _payload_with_alt(spkid, des, fullname, des_alt):
    payload = make_mock_payload(spkid, des)
    payload["object"]["fullname"] = fullname
    if des_alt is not ...:
        payload["object"]["des_alt"] = des_alt
    return payload


@pytest.mark.parametrize(
    ("des_alt", "expected"),
    [
        (..., None),  # key absent: not captured, never claimed empty
        ([], []),  # SBDB lists none
        ([{"des": "2013 ET"}], ["2013 ET"]),  # a later provisional designation linked to the object
        ([{"pri": "2001 CB21"}], ["2001 CB21"]),  # principal provisional designation of a numbered object
        ([{"des": "2001 SY169"}, {"des": " 2013  ET "}, {"des": "2013 ET"}], ["2013 ET"]),  # primary excluded, whitespace normalized, de-duplicated
    ],
)
def test_extract_sbdb_object_alternate_designations(des_alt, expected):
    payload = _payload_with_alt("50092353", "2001 SY169", "(2001 SY169)", des_alt)
    record = nasa_sbdb.extract_sbdb_object(payload, "2026-10-02", "r1", "t1")
    assert record["alternate_designations"] == expected


def test_batch_same_object_targets_written_once_and_summarized(tmp_path):
    """Two targets SBDB resolves to the same SPK-ID (e.g. NeoWs 3092330 and 3629117 -> 2001 SY169)
    produce ONE object row per (snapshot_key, spkid); the second is recorded as a duplicate,
    neither a success row nor a failure, so DQ lineage and the failure rate stay exact."""
    same = _payload_with_alt("50092353", "2001 SY169", "(2001 SY169)", [{"des": "2013 ET"}])
    other = make_mock_payload("50548689", "2010 TW54")

    def mock_fetch(target, id_type="sstr", run_id=None):
        return {"3092330": same, "3629117": same, "3548666": other}[target]

    summary_file = tmp_path / "sbdb_batch_summary.json"
    with patch("nasa_sbdb.fetch_sbdb_data", side_effect=mock_fetch),          patch("nasa_sbdb.save_raw_json") as mock_raw,          patch("nasa_sbdb.write_parquet") as mock_pq,          patch("nasa_sbdb.upload_raw_to_s3"),          patch("nasa_sbdb.upload_processed_to_s3"),          patch("time.sleep"):
        exit_code = nasa_sbdb.main(targets="3092330,3629117,3548666", summary_filename=str(summary_file))

    assert exit_code == 0
    obj_rows = mock_pq.call_args_list[0][0][0]
    assert sorted(r["spkid"] for r in obj_rows) == ["50092353", "50548689"]
    assert mock_raw.call_count == 2
    summary = json.loads(summary_file.read_text(encoding="utf-8"))
    assert summary["total_targets"] == 3
    assert summary["successful_targets_count"] == 2
    assert summary["failed_targets_count"] == 0
    assert summary["duplicate_targets_count"] == 1
    assert summary["duplicate_targets"] == [{"target": "3629117", "spkid": "50092353"}]
    assert summary["failure_rate_pct"] == 0.0
    assert summary["circuit_breaker_passed"] is True


def test_skip_s3_upload_writes_local_outputs_only(tmp_path):
    summary_file = tmp_path / "sbdb_batch_summary.json"
    with patch("nasa_sbdb.fetch_sbdb_data", return_value=SAMPLE_SBDB_2025_HX_PAYLOAD),          patch("nasa_sbdb.save_raw_json"),          patch("nasa_sbdb.write_parquet") as mock_pq,          patch("nasa_sbdb.upload_raw_to_s3") as mock_up_raw,          patch("nasa_sbdb.upload_processed_to_s3") as mock_up_proc:
        exit_code = nasa_sbdb.main(target="2025 HX", summary_filename=str(summary_file), skip_s3_upload=True)

    assert exit_code == 0
    assert mock_pq.call_count == 4
    mock_up_raw.assert_not_called()
    mock_up_proc.assert_not_called()
    assert nasa_sbdb.parse_args(["--skip-s3-upload"]).skip_s3_upload is True
    assert nasa_sbdb.parse_args([]).skip_s3_upload is False  # production default: upload
