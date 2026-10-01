import datetime
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
from datetime import date
from unittest.mock import MagicMock, patch

import pyarrow.parquet as pq
import pytest
import requests
from botocore.exceptions import ClientError

import database
import nasa_asteroids

REPO_DIR = os.path.dirname(os.path.abspath(__file__))


@pytest.fixture(autouse=True)
def _isolate_cwd(tmp_path, monkeypatch):
    """Run every test from a temp dir.

    main() writes cwd-relative default outputs (neows_summary.json, raw JSON,
    CSV, Parquet, SQLite); without this, tests overwrite real repo-root files.
    """
    monkeypatch.chdir(tmp_path)


def test_main_default_summary_path_does_not_touch_repo_root(tmp_path, monkeypatch):
    repo_summary = os.path.join(REPO_DIR, "neows_summary.json")
    before = open(repo_summary, "rb").read() if os.path.exists(repo_summary) else None

    monkeypatch.setattr(nasa_asteroids, "API_KEY", "TEST_KEY")
    with patch("nasa_asteroids.fetch_data", return_value={"near_earth_objects": {}}), \
         patch("nasa_asteroids.save_raw_json"):
        assert nasa_asteroids.main() == 1

    assert (tmp_path / "neows_summary.json").exists()
    after = open(repo_summary, "rb").read() if os.path.exists(repo_summary) else None
    assert after == before


def test_extract_asteroids():
    fake_data = {
        "near_earth_objects": {
            "2026-09-20": [
                {   
                    "id": "123456",
                    "name": "Test Asteroid",
                    "is_potentially_hazardous_asteroid": False,
                    "close_approach_data": [
                        {
                            "close_approach_date": "2026-09-20",
                            "miss_distance": {
                                "kilometers": "123456.78"
                            }
                        }
                    ]
                }
            ]
        }
    }

    asteroid_data, skipped_records, records_received = (
        nasa_asteroids.extract_asteroids(fake_data)
    )

    assert len(asteroid_data) == 1
    assert skipped_records == 0
    assert records_received == 1

def test_skip_asteroid_without_name():
    fake_data = {
        "near_earth_objects": {
            "2026-09-20": [
                {    
                    "id": "123456",
                    "name": "",
                    "is_potentially_hazardous_asteroid": False,
                    "close_approach_data": [
                        {
                            "close_approach_date": "2026-09-20",
                            "miss_distance": {
                                "kilometers": "123456.78"
                            }
                        }
                    ]
                }
            ]
        }
    }

    asteroid_data, skipped_records, records_received = (
        nasa_asteroids.extract_asteroids(fake_data)
    )

    assert len(asteroid_data) == 0
    assert skipped_records == 1
    assert records_received == 1

def test_skip_asteroid_without_close_approach_data():
    fake_data = {
        "near_earth_objects": {
            "2026-09-20": [
                {   
                    "id": "123456",
                    "name": "Test Asteroid",
                    "is_potentially_hazardous_asteroid": False,
                    "close_approach_data": []
                }
            ]
        }
    }

    asteroid_data, skipped_records, records_received = (
        nasa_asteroids.extract_asteroids(fake_data)
    )

    assert len(asteroid_data) == 0
    assert skipped_records == 1
    assert records_received == 1

def test_skip_asteroid_without_miss_distance():
    fake_data = {
        "near_earth_objects": {
            "2026-09-20": [
                {    
                    "id": "123456",
                    "name": "Test Asteroid",
                    "is_potentially_hazardous_asteroid": False,
                    "close_approach_data": [
                        {
                            "close_approach_date": "2026-09-20",
                            "miss_distance": {}
                        }
                    ]
                }
            ]
        }
    }

    asteroid_data, skipped_records, records_received = (
        nasa_asteroids.extract_asteroids(fake_data)
    )

    assert len(asteroid_data) == 0
    assert skipped_records == 1
    assert records_received == 1

def test_skip_asteroid_without_kilometers():
    fake_data = {
        "near_earth_objects": {
            "2026-09-20": [
                {    
                    "id": "123456",
                    "name": "Test Asteroid",
                    "is_potentially_hazardous_asteroid": False,
                    "close_approach_data": [
                        {
                            "close_approach_date": "2026-09-20",
                            "miss_distance": {
                                "kilometers": ""
                            }
                        }
                    ]
                }
            ]
        }
    }

    asteroid_data, skipped_records, records_received = (
        nasa_asteroids.extract_asteroids(fake_data)
    )

    assert len(asteroid_data) == 0
    assert skipped_records == 1
    assert records_received == 1

def test_extracted_asteroid_fields():
    fake_data = {
        "near_earth_objects": {
            "2026-09-20": [
                {    
                    "id": "123456",
                    "name": "Test Asteroid",
                    "is_potentially_hazardous_asteroid": True,
                    "close_approach_data": [
                        {
                            "close_approach_date": "2026-09-20",
                            "miss_distance": {
                                "kilometers": "123456.78"
                            }
                        }
                    ]
                }
            ]
        }
    }

    asteroid_data, skipped_records, records_received = (
        nasa_asteroids.extract_asteroids(fake_data)
    )

    assert asteroid_data[0]["name"] == "Test Asteroid"
    assert asteroid_data[0]["closest_approach_date"] == "2026-09-20"
    assert asteroid_data[0]["miss_distance_km"] == 123456.78
    assert asteroid_data[0]["hazardous"] is True

def test_extract_multiple_asteroids():
    fake_data = {
        "near_earth_objects": {
            "2026-09-20": [
                {
                    "id": "123456",
                    "name": "Asteroid One",
                    "is_potentially_hazardous_asteroid": False,
                    "close_approach_data": [
                        {
                            "close_approach_date": "2026-09-20",
                            "miss_distance": {
                                "kilometers": "100000"
                            }
                        }
                    ]
                },
                {
                    "id": "789012",
                    "name": "Asteroid Two",
                    "is_potentially_hazardous_asteroid": True,
                    "close_approach_data": [
                        {
                            "close_approach_date": "2026-09-20",
                            "miss_distance": {
                                "kilometers": "200000"
                            }
                        }
                    ]
                }
            ]
        }
    }

    asteroid_data, skipped_records, records_received = (
        nasa_asteroids.extract_asteroids(fake_data)
    )

    assert len(asteroid_data) == 2
    assert skipped_records == 0
    assert records_received == 2


def test_save_to_parquet(tmp_path):
    test_file = tmp_path / "test_asteroids.parquet"
    test_data = [
        {
            "id": "12345",
            "name": "Parquet Test",
            "closest_approach_date": "2026-09-22",
            "miss_distance_km": 150000.5,
            "hazardous": False
        }
    ]
    nasa_asteroids.save_to_parquet(test_data, filename=str(test_file))
    assert test_file.exists()

    table = pq.read_table(str(test_file))
    assert table.num_rows == 1
    assert table.column("name")[0].as_py() == "Parquet Test"
    assert table.column("miss_distance_km")[0].as_py() == 150000.5


def test_fetch_data_mocked():
    mock_payload = {
        "element_count": 1,
        "near_earth_objects": {}
    }
    with patch("nasa_asteroids.get_http_session") as mock_get_session:
        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.json.return_value = mock_payload
        mock_response.raise_for_status.return_value = None
        mock_session.get.return_value = mock_response
        mock_get_session.return_value = mock_session

        data = nasa_asteroids.fetch_data(start="2026-09-01", end="2026-09-07", key="TEST_KEY")
        assert data == mock_payload
        mock_session.get.assert_called_once()


def test_upload_raw_to_s3_mocked():
    with patch("boto3.client") as mock_boto:
        mock_s3 = MagicMock()
        mock_boto.return_value = mock_s3

        nasa_asteroids.upload_raw_to_s3()

        mock_boto.assert_called_once_with("s3")
        mock_s3.upload_file.assert_called_once()
        call_args = mock_s3.upload_file.call_args[0]
        assert call_args[0] == "asteroids_raw.json"
        assert "raw/year=" in call_args[2]
        assert call_args[2].endswith("/asteroids_raw.json")


def test_upload_processed_to_s3_mocked():
    with patch("boto3.client") as mock_boto:
        mock_s3 = MagicMock()
        mock_boto.return_value = mock_s3

        nasa_asteroids.upload_processed_to_s3()

        mock_boto.assert_called_once_with("s3")
        assert mock_s3.upload_file.call_count == 2
        uploaded_files = [call[0][0] for call in mock_s3.upload_file.call_args_list]
        assert "asteroids.parquet" in uploaded_files
        assert "asteroids.csv" in uploaded_files
        uploaded_keys = [call[0][2] for call in mock_s3.upload_file.call_args_list]
        assert uploaded_keys[0].endswith("/asteroids.parquet")
        assert uploaded_keys[1].endswith("/asteroids.csv")


def test_database_load_data_end_to_end(tmp_path):
    db_file = str(tmp_path / "test_asteroids.db")
    test_records = [
        {
            "id": "999",
            "name": "Integration Asteroid",
            "closest_approach_date": "2026-09-22",
            "miss_distance_km": 500000.0,
            "hazardous": True
        }
    ]

    database.load_data(test_records, db_path=db_file)

    with sqlite3.connect(db_file) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT asteroid_id, name, hazardous FROM asteroids WHERE asteroid_id = '999'")
        row = cursor.fetchone()
        assert row == ("999", "Integration Asteroid", 1)

        cursor.execute(
            "SELECT asteroid_id, approach_date, miss_distance_km FROM close_approaches WHERE asteroid_id = '999'"
        )
        approach = cursor.fetchone()
        assert approach == ("999", "2026-09-22", 500000.0)


def test_database_load_data_is_idempotent(tmp_path):
    db_file = str(tmp_path / "test_idempotent.db")
    test_records = [
        {
            "id": "1001",
            "name": "Idempotent Asteroid",
            "closest_approach_date": "2026-09-22",
            "miss_distance_km": 300000.0,
            "hazardous": True
        }
    ]

    # First load
    database.load_data(test_records, db_path=db_file)

    # Second load with identical records
    database.load_data(test_records, db_path=db_file)

    with sqlite3.connect(db_file) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM asteroids WHERE asteroid_id = '1001'")
        assert cursor.fetchone()[0] == 1

        cursor.execute(
            "SELECT COUNT(*) FROM close_approaches WHERE asteroid_id = '1001' AND approach_date = '2026-09-22'"
        )
        assert cursor.fetchone()[0] == 1


def test_redact_api_key_query_params():
    # Only api_key parameter
    url_single = "http://api.nasa.gov/neo/rest/v1/feed?api_key=SECRET_API_KEY"
    assert (
        nasa_asteroids.redact_api_key(url_single)
        == "http://api.nasa.gov/neo/rest/v1/feed?api_key=REDACTED"
    )

    # api_key between other query parameters
    url_multi = (
        "http://api.nasa.gov/neo/rest/v1/feed"
        "?start_date=2026-09-01&api_key=SECRET_API_KEY&end_date=2026-09-07"
    )
    assert (
        nasa_asteroids.redact_api_key(url_multi)
        == "http://api.nasa.gov/neo/rest/v1/feed?start_date=2026-09-01&api_key=REDACTED&end_date=2026-09-07"
    )

    # api_key at start of query string with subsequent parameter
    url_leading = (
        "http://api.nasa.gov/neo/rest/v1/feed?api_key=SECRET_API_KEY&detailed=false"
    )
    assert (
        nasa_asteroids.redact_api_key(url_leading)
        == "http://api.nasa.gov/neo/rest/v1/feed?api_key=REDACTED&detailed=false"
    )

    # No api_key parameter present
    url_safe = "http://api.nasa.gov/neo/rest/v1/feed?start_date=2026-09-01"
    assert nasa_asteroids.redact_api_key(url_safe) == url_safe

    # Non-string input handled gracefully
    assert nasa_asteroids.redact_api_key(None) is None
    assert nasa_asteroids.redact_api_key(12345) == 12345


def test_sanitize_raw_data_top_level_and_asteroid_links():
    raw_payload = {
        "links": {
            "next": "http://api.nasa.gov/neo/rest/v1/feed?start_date=2026-09-28&api_key=SECRET_KEY_123",
            "previous": "http://api.nasa.gov/neo/rest/v1/feed?start_date=2026-09-14&api_key=SECRET_KEY_123",
            "self": "http://api.nasa.gov/neo/rest/v1/feed?start_date=2026-09-21&api_key=SECRET_KEY_123"
        },
        "element_count": 1,
        "near_earth_objects": {
            "2026-09-21": [
                {
                    "links": {
                        "self": "http://api.nasa.gov/neo/rest/v1/neo/3183853?api_key=SECRET_KEY_123"
                    },
                    "id": "3183853",
                    "name": "(2004 LA6)",
                    "nasa_jpl_url": "https://ssd.jpl.nasa.gov/tools/sbdb_lookup.html#/?sstr=3183853",
                    "is_potentially_hazardous_asteroid": False,
                    "close_approach_data": [
                        {
                            "close_approach_date": "2026-09-22",
                            "miss_distance": {
                                "kilometers": "54500983.17"
                            }
                        }
                    ]
                }
            ]
        }
    }

    sanitized = nasa_asteroids.sanitize_raw_data(raw_payload)

    # Verify secret is completely removed
    sanitized_str = json.dumps(sanitized)
    assert "SECRET_KEY_123" not in sanitized_str

    # Verify links have REDACTED
    assert (
        sanitized["links"]["next"]
        == "http://api.nasa.gov/neo/rest/v1/feed?start_date=2026-09-28&api_key=REDACTED"
    )
    assert (
        sanitized["links"]["previous"]
        == "http://api.nasa.gov/neo/rest/v1/feed?start_date=2026-09-14&api_key=REDACTED"
    )
    assert (
        sanitized["links"]["self"]
        == "http://api.nasa.gov/neo/rest/v1/feed?start_date=2026-09-21&api_key=REDACTED"
    )

    asteroid_link = sanitized["near_earth_objects"]["2026-09-21"][0]["links"]["self"]
    assert (
        asteroid_link
        == "http://api.nasa.gov/neo/rest/v1/neo/3183853?api_key=REDACTED"
    )

    # Verify astronomical / science data is preserved unchanged
    asteroid = sanitized["near_earth_objects"]["2026-09-21"][0]
    assert asteroid["id"] == "3183853"
    assert asteroid["name"] == "(2004 LA6)"
    assert asteroid["nasa_jpl_url"] == "https://ssd.jpl.nasa.gov/tools/sbdb_lookup.html#/?sstr=3183853"
    assert asteroid["is_potentially_hazardous_asteroid"] is False
    assert asteroid["close_approach_data"][0]["close_approach_date"] == "2026-09-22"
    assert asteroid["close_approach_data"][0]["miss_distance"]["kilometers"] == "54500983.17"

    # Verify original payload was not mutated
    assert "SECRET_KEY_123" in raw_payload["links"]["self"]


def test_save_raw_json_produces_file_without_secret(tmp_path):
    test_file = tmp_path / "test_raw.json"
    raw_payload = {
        "links": {
            "self": "http://api.nasa.gov/neo/rest/v1/feed?start_date=2026-09-21&api_key=SUPER_SECRET_KEY"
        },
        "element_count": 1,
        "near_earth_objects": {
            "2026-09-21": [
                {
                    "links": {
                        "self": "http://api.nasa.gov/neo/rest/v1/neo/999?api_key=SUPER_SECRET_KEY"
                    },
                    "id": "999",
                    "name": "Secret Test Asteroid",
                    "is_potentially_hazardous_asteroid": True,
                    "close_approach_data": [
                        {
                            "close_approach_date": "2026-09-21",
                            "miss_distance": {"kilometers": "100000"}
                        }
                    ]
                }
            ]
        }
    }

    nasa_asteroids.save_raw_json(raw_payload, filename=str(test_file))
    assert test_file.exists()

    file_content = test_file.read_text(encoding="utf-8")
    assert "SUPER_SECRET_KEY" not in file_content
    assert "api_key=REDACTED" in file_content

    loaded_json = json.loads(file_content)
    assert loaded_json["element_count"] == 1
    assert loaded_json["near_earth_objects"]["2026-09-21"][0]["name"] == "Secret Test Asteroid"


def test_http_error_redaction():
    error_url = (
        "https://api.nasa.gov/neo/rest/v1/feed"
        "?start_date=2026-09-01&api_key=VERY_SECRET_KEY&end_date=2026-09-07"
    )
    http_error = requests.exceptions.HTTPError(
        f"403 Client Error: Forbidden for url: {error_url}"
    )

    redacted_message = nasa_asteroids.redact_api_key(str(http_error))

    assert "VERY_SECRET_KEY" not in redacted_message
    assert "api_key=REDACTED" in redacted_message
    assert "start_date=2026-09-01" in redacted_message
    assert "end_date=2026-09-07" in redacted_message
    assert "403 Client Error: Forbidden for url:" in redacted_message


def test_main_returns_1_when_api_key_missing(monkeypatch):
    monkeypatch.setattr(nasa_asteroids, "API_KEY", None)
    exit_code = nasa_asteroids.main()
    assert exit_code == 1


def test_main_returns_1_for_invalid_date_input(monkeypatch):
    monkeypatch.setattr(nasa_asteroids, "API_KEY", "TEST_KEY")
    assert nasa_asteroids.main(start_date_str="2026-99-99") == 1
    assert nasa_asteroids.main(start_date_str="2026-09-01", end_date_str="invalid-date") == 1
    assert nasa_asteroids.main(start_date_str="2026-02-30") == 1
    assert nasa_asteroids.main(start_date_str="2026-09-01", end_date_str="2026-02-30") == 1


def test_main_accepts_dates_with_whitespace_and_carriage_return(monkeypatch):
    fake_data = {
        "near_earth_objects": {
            "2026-09-01": [
                {
                    "id": "123456",
                    "name": "Test Asteroid",
                    "is_potentially_hazardous_asteroid": False,
                    "close_approach_data": [
                        {
                            "close_approach_date": "2026-09-01",
                            "miss_distance": {
                                "kilometers": "123456.78"
                            }
                        }
                    ]
                }
            ]
        }
    }
    monkeypatch.setattr(nasa_asteroids, "API_KEY", "TEST_KEY")
    with patch("nasa_asteroids.fetch_data", return_value=fake_data) as mock_fetch, \
         patch("nasa_asteroids.save_raw_json"), \
         patch("nasa_asteroids.upload_raw_to_s3"), \
         patch("nasa_asteroids.save_to_csv"), \
         patch("nasa_asteroids.save_to_parquet"), \
         patch("nasa_asteroids.upload_processed_to_s3"), \
         patch("nasa_asteroids.load_data"):

        exit_code = nasa_asteroids.main(
            start_date_str=" 2026-09-01 ",
            end_date_str="2026-09-07\r"
        )
        assert exit_code == 0
        assert mock_fetch.call_args.kwargs["start"] == date(2026, 9, 1)
        assert mock_fetch.call_args.kwargs["end"] == date(2026, 9, 7)


def test_main_returns_1_when_end_date_precedes_start_date(monkeypatch):
    monkeypatch.setattr(nasa_asteroids, "API_KEY", "TEST_KEY")
    exit_code = nasa_asteroids.main(start_date_str="2026-09-10", end_date_str="2026-09-01")
    assert exit_code == 1


def test_cli_exits_code_1_when_fetch_data_raises_http_error(tmp_path):
    repo_dir = os.path.dirname(os.path.abspath(__file__))
    target_script = os.path.join(repo_dir, "nasa_asteroids.py")
    env = os.environ.copy()
    env["PYTHONPATH"] = repo_dir
    env["NASA_API_KEY"] = "TEST_KEY"

    script = (
        "from unittest.mock import patch\n"
        "import requests, runpy\n"
        "with patch('requests.Session.get', side_effect=requests.exceptions.HTTPError('403 Forbidden')):\n"
        "    with patch('nasa_asteroids.API_KEY', 'TEST_KEY'):\n"
        f"        runpy.run_path(r'{target_script}', run_name='__main__')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True
    )
    assert result.returncode == 1
    assert "NASA API returned an HTTP error" in result.stderr


def test_cli_exits_code_1_and_logs_response_body_on_http_error(tmp_path):
    repo_dir = os.path.dirname(os.path.abspath(__file__))
    target_script = os.path.join(repo_dir, "nasa_asteroids.py")
    env = os.environ.copy()
    env["PYTHONPATH"] = repo_dir
    env["NASA_API_KEY"] = "TEST_KEY"

    script = (
        "from unittest.mock import MagicMock, patch\n"
        "import requests, runpy\n"
        "mock_resp = MagicMock()\n"
        "mock_resp.text = '{\"error\": {\"code\": \"API_KEY_INVALID\", \"url\": \"https://api.nasa.gov/?api_key=SECRET_BODY_KEY\"}}'\n"
        "err = requests.exceptions.HTTPError('403 Forbidden', response=mock_resp)\n"
        "with patch('requests.Session.get', side_effect=err):\n"
        "    with patch('nasa_asteroids.API_KEY', 'TEST_KEY'):\n"
        f"        runpy.run_path(r'{target_script}', run_name='__main__')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True
    )
    assert result.returncode == 1
    assert "NASA API returned an HTTP error" in result.stderr
    assert "HTTP response body:" in result.stderr
    assert "API_KEY_INVALID" in result.stderr
    assert "SECRET_BODY_KEY" not in result.stderr
    assert "api_key=REDACTED" in result.stderr


def test_cli_exits_code_1_when_fetch_data_raises_request_exception(tmp_path):
    repo_dir = os.path.dirname(os.path.abspath(__file__))
    target_script = os.path.join(repo_dir, "nasa_asteroids.py")
    env = os.environ.copy()
    env["PYTHONPATH"] = repo_dir
    env["NASA_API_KEY"] = "TEST_KEY"

    script = (
        "from unittest.mock import patch\n"
        "import requests, runpy\n"
        "conn_err = requests.exceptions.ConnectionError('Connection refused: https://api.nasa.gov/neo/rest/v1/feed?api_key=SECRET_NETWORK_KEY')\n"
        "with patch('requests.Session.get', side_effect=conn_err):\n"
        "    with patch('nasa_asteroids.API_KEY', 'TEST_KEY'):\n"
        f"        runpy.run_path(r'{target_script}', run_name='__main__')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True
    )
    assert result.returncode == 1
    assert "Network error:" in result.stderr
    assert "SECRET_NETWORK_KEY" not in result.stderr
    assert "api_key=REDACTED" in result.stderr


def test_cli_exits_code_1_when_unexpected_pipeline_exception_occurs(tmp_path):
    repo_dir = os.path.dirname(os.path.abspath(__file__))
    target_script = os.path.join(repo_dir, "nasa_asteroids.py")
    env = os.environ.copy()
    env["PYTHONPATH"] = repo_dir
    env["DB_PATH"] = str(tmp_path / "subprocess.db")
    env["NASA_API_KEY"] = "TEST_KEY"

    script = (
        "from unittest.mock import patch\n"
        "import os, runpy\n"
        "with patch('requests.Session.get') as mock_get:\n"
        "    valid_data = {'near_earth_objects': {'2026-09-20': [{'id': '123', 'name': 'A', 'close_approach_data': [{'close_approach_date': '2026-09-20', 'miss_distance': {'kilometers': '100000'}}], 'is_potentially_hazardous_asteroid': False}]}}\n"
        "    mock_get.return_value.json.return_value = valid_data\n"
        "    mock_get.return_value.raise_for_status.return_value = None\n"
        "    with patch('boto3.client', side_effect=RuntimeError('Unexpected AWS error')):\n"
        "        with patch('nasa_asteroids.API_KEY', 'TEST_KEY'):\n"
        f"            runpy.run_path(r'{target_script}', run_name='__main__')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True
    )
    assert result.returncode == 1
    assert "Pipeline failure: Unexpected AWS error" in result.stderr


def test_main_success_returns_0(monkeypatch):
    fake_data = {
        "near_earth_objects": {
            "2026-09-20": [
                {
                    "id": "123456",
                    "name": "Test Asteroid",
                    "is_potentially_hazardous_asteroid": False,
                    "close_approach_data": [
                        {
                            "close_approach_date": "2026-09-20",
                            "miss_distance": {
                                "kilometers": "123456.78"
                            }
                        }
                    ]
                }
            ]
        }
    }

    monkeypatch.setattr(nasa_asteroids, "API_KEY", "TEST_KEY")
    with patch("nasa_asteroids.fetch_data", return_value=fake_data) as mock_fetch, \
         patch("nasa_asteroids.save_raw_json") as mock_save_raw, \
         patch("nasa_asteroids.upload_raw_to_s3") as mock_upload_raw, \
         patch("nasa_asteroids.save_to_csv") as mock_save_csv, \
         patch("nasa_asteroids.save_to_parquet") as mock_save_parquet, \
         patch("nasa_asteroids.upload_processed_to_s3") as mock_upload_proc, \
         patch("nasa_asteroids.load_data") as mock_load_data:

        exit_code = nasa_asteroids.main(start_date_str="2026-09-20", end_date_str="2026-09-26")

        assert exit_code == 0
        mock_fetch.assert_called_once()
        mock_save_raw.assert_called_once()
        mock_upload_raw.assert_called_once()
        mock_save_csv.assert_called_once()
        mock_save_parquet.assert_called_once()
        mock_upload_proc.assert_called_once()
        mock_load_data.assert_called_once()


def test_upload_raw_to_s3_handles_client_error():
    client_error = ClientError({"Error": {"Code": "403", "Message": "AccessDenied"}}, "PutObject")
    with patch("boto3.client") as mock_boto:
        mock_s3 = MagicMock()
        mock_s3.upload_file.side_effect = client_error
        mock_boto.return_value = mock_s3

        with pytest.raises(ClientError) as exc_info:
            nasa_asteroids.upload_raw_to_s3()

        assert exc_info.value == client_error
        mock_s3.upload_file.assert_called_once()


def test_upload_processed_to_s3_handles_client_error():
    client_error = ClientError({"Error": {"Code": "500", "Message": "InternalError"}}, "PutObject")
    with patch("boto3.client") as mock_boto:
        mock_s3 = MagicMock()
        mock_s3.upload_file.side_effect = client_error
        mock_boto.return_value = mock_s3

        with pytest.raises(ClientError) as exc_info:
            nasa_asteroids.upload_processed_to_s3()

        assert exc_info.value == client_error
        assert mock_s3.upload_file.call_count == 1
        assert mock_s3.upload_file.call_args[0][0] == "asteroids.parquet"


def test_upload_processed_to_s3_handles_partial_failure():
    client_error = ClientError({"Error": {"Code": "404", "Message": "NoSuchBucket"}}, "PutObject")
    with patch("boto3.client") as mock_boto:
        mock_s3 = MagicMock()
        # Parquet succeeds (None), CSV fails (ClientError)
        mock_s3.upload_file.side_effect = [None, client_error]
        mock_boto.return_value = mock_s3

        with pytest.raises(ClientError) as exc_info:
            nasa_asteroids.upload_processed_to_s3()

        assert exc_info.value == client_error
        assert mock_s3.upload_file.call_count == 2
        calls = [c[0][0] for c in mock_s3.upload_file.call_args_list]
        assert calls == ["asteroids.parquet", "asteroids.csv"]


def test_main_exits_code_1_on_s3_failure(tmp_path):
    repo_dir = os.path.dirname(os.path.abspath(__file__))
    target_script = os.path.join(repo_dir, "nasa_asteroids.py")
    env = os.environ.copy()
    env["PYTHONPATH"] = repo_dir
    env["NASA_API_KEY"] = "TEST_KEY"

    script = (
        "from unittest.mock import patch, MagicMock\n"
        "import os, runpy\n"
        "from botocore.exceptions import ClientError\n"
        "err = ClientError({'Error': {'Code': '403', 'Message': 'AccessDenied'}}, 'PutObject')\n"
        "with patch('requests.Session.get') as mock_get:\n"
        "    valid_data = {'near_earth_objects': {'2026-09-20': [{'id': '123', 'name': 'A', 'close_approach_data': [{'close_approach_date': '2026-09-20', 'miss_distance': {'kilometers': '100000'}}], 'is_potentially_hazardous_asteroid': False}]}}\n"
        "    mock_get.return_value.json.return_value = valid_data\n"
        "    mock_get.return_value.raise_for_status.return_value = None\n"
        "    with patch('boto3.client') as mock_boto:\n"
        "        mock_s3 = MagicMock()\n"
        "        mock_s3.upload_file.side_effect = err\n"
        "        mock_boto.return_value = mock_s3\n"
        "        with patch('nasa_asteroids.API_KEY', 'TEST_KEY'):\n"
        f"            runpy.run_path(r'{target_script}', run_name='__main__')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True
    )
    assert result.returncode == 1
    assert "Raw S3 upload failed" in result.stderr or "Pipeline failure" in result.stderr


def test_main_executes_local_pipeline_before_s3_failure(monkeypatch):
    fake_data = {"near_earth_objects": {}}
    call_order = []

    monkeypatch.setattr(nasa_asteroids, "API_KEY", "TEST_KEY")
    client_error = ClientError({"Error": {"Code": "500", "Message": "S3Unavailable"}}, "PutObject")

    valid_record = [{"id": "1", "name": "A", "closest_approach_date": "2026-09-20", "miss_distance_km": 100.0, "hazardous": False}]

    with patch("nasa_asteroids.fetch_data", return_value=fake_data), \
         patch("nasa_asteroids.save_raw_json", side_effect=lambda *a, **kw: call_order.append("save_raw")), \
         patch("nasa_asteroids.extract_asteroids", return_value=(valid_record, 0, 1)), \
         patch("nasa_asteroids.save_to_csv", side_effect=lambda *a, **kw: call_order.append("save_csv")), \
         patch("nasa_asteroids.save_to_parquet", side_effect=lambda *a, **kw: call_order.append("save_parquet")), \
         patch("nasa_asteroids.load_data", side_effect=lambda *a, **kw: call_order.append("load_data")), \
         patch("nasa_asteroids.upload_raw_to_s3", side_effect=client_error), \
         patch("nasa_asteroids.upload_processed_to_s3") as mock_upload_proc:

        with pytest.raises(ClientError):
            nasa_asteroids.main()

        assert call_order == ["save_raw", "save_csv", "save_parquet", "load_data"]
        mock_upload_proc.assert_not_called()


def test_s3_daily_deterministic_key_generation():
    test_date = date(2026, 9, 23)

    with patch("boto3.client") as mock_boto:
        mock_s3 = MagicMock()
        mock_boto.return_value = mock_s3

        nasa_asteroids.upload_raw_to_s3(start_date=test_date)
        raw_key = mock_s3.upload_file.call_args[0][2]
        assert raw_key == "raw/year=2026/month=09/day=23/asteroids_raw.json"

    with patch("boto3.client") as mock_boto:
        mock_s3 = MagicMock()
        mock_boto.return_value = mock_s3

        nasa_asteroids.upload_processed_to_s3(start_date=test_date)
        parquet_key = mock_s3.upload_file.call_args_list[0][0][2]
        csv_key = mock_s3.upload_file.call_args_list[1][0][2]
        assert parquet_key == "processed/year=2026/month=09/day=23/asteroids.parquet"
        assert csv_key == "processed_csv/year=2026/month=09/day=23/asteroids.csv"


def test_s3_custom_date_range_key_generation():
    custom_date = date(2026, 1, 1)

    with patch("boto3.client") as mock_boto:
        mock_s3 = MagicMock()
        mock_boto.return_value = mock_s3

        nasa_asteroids.upload_raw_to_s3(start_date=custom_date)
        raw_key = mock_s3.upload_file.call_args[0][2]
        assert raw_key == "raw/year=2026/month=01/day=01/asteroids_raw.json"

    with patch("boto3.client") as mock_boto:
        mock_s3 = MagicMock()
        mock_boto.return_value = mock_s3

        nasa_asteroids.upload_processed_to_s3(start_date=custom_date)
        parquet_key = mock_s3.upload_file.call_args_list[0][0][2]
        csv_key = mock_s3.upload_file.call_args_list[1][0][2]
        assert parquet_key == "processed/year=2026/month=01/day=01/asteroids.parquet"
        assert csv_key == "processed_csv/year=2026/month=01/day=01/asteroids.csv"


def test_s3_same_start_date_produces_identical_keys():
    start_date = date(2026, 9, 23)

    with patch("boto3.client") as mock_boto:
        mock_s3 = MagicMock()
        mock_boto.return_value = mock_s3

        nasa_asteroids.upload_raw_to_s3(start_date=start_date)
        nasa_asteroids.upload_processed_to_s3(start_date=start_date)
        keys_first_call = [call[0][2] for call in mock_s3.upload_file.call_args_list]

    with patch("boto3.client") as mock_boto:
        mock_s3 = MagicMock()
        mock_boto.return_value = mock_s3

        nasa_asteroids.upload_raw_to_s3(start_date=start_date)
        nasa_asteroids.upload_processed_to_s3(start_date=start_date)
        keys_second_call = [call[0][2] for call in mock_s3.upload_file.call_args_list]

    assert keys_first_call == keys_second_call
    assert len(keys_first_call) == 3


def test_s3_different_start_dates_produce_different_keys():
    date_jan = date(2026, 1, 1)
    date_feb = date(2026, 2, 1)

    with patch("boto3.client") as mock_boto:
        mock_s3 = MagicMock()
        mock_boto.return_value = mock_s3

        nasa_asteroids.upload_processed_to_s3(start_date=date_jan)
        key_jan = mock_s3.upload_file.call_args_list[0][0][2]

    with patch("boto3.client") as mock_boto:
        mock_s3 = MagicMock()
        mock_boto.return_value = mock_s3

        nasa_asteroids.upload_processed_to_s3(start_date=date_feb)
        key_feb = mock_s3.upload_file.call_args_list[0][0][2]

    assert key_jan != key_feb
    assert "year=2026/month=01/day=01" in key_jan
    assert "year=2026/month=02/day=01" in key_feb


def test_s3_object_names_contain_no_timestamps():
    test_date = date(2026, 9, 23)

    with patch("boto3.client") as mock_boto:
        mock_s3 = MagicMock()
        mock_boto.return_value = mock_s3

        nasa_asteroids.upload_raw_to_s3(start_date=test_date)
        nasa_asteroids.upload_processed_to_s3(start_date=test_date)
        keys = [call[0][2] for call in mock_s3.upload_file.call_args_list]

    for key in keys:
        filename = key.split("/")[-1]
        assert filename in ["asteroids_raw.json", "asteroids.parquet", "asteroids.csv"]
        # Ensure no timestamp suffix like _14-00-00 exists
        assert not any(char.isdigit() for char in filename)


def test_fetch_data_dynamic_today_defaults(monkeypatch):
    class MockDate(datetime.date):
        @classmethod
        def today(cls):
            return datetime.date(2026, 10, 15)

    monkeypatch.setattr(nasa_asteroids, "date", MockDate)

    with patch("nasa_asteroids.get_http_session") as mock_get_session:
        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.json.return_value = {"near_earth_objects": {}}
        mock_response.raise_for_status.return_value = None
        mock_session.get.return_value = mock_response
        mock_get_session.return_value = mock_session

        nasa_asteroids.fetch_data(key="TEST_KEY")

        mock_session.get.assert_called_once()
        params = mock_session.get.call_args[1]["params"]
        assert params["start_date"] == "2026-10-15"
        assert params["end_date"] == "2026-10-21"
        assert params["api_key"] == "TEST_KEY"


def test_fetch_data_start_only_calculates_end_date():
    with patch("nasa_asteroids.get_http_session") as mock_get_session:
        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.json.return_value = {"near_earth_objects": {}}
        mock_response.raise_for_status.return_value = None
        mock_session.get.return_value = mock_response
        mock_get_session.return_value = mock_session

        nasa_asteroids.fetch_data(start="2026-05-01", end=None, key="TEST_KEY")

        mock_session.get.assert_called_once()
        params = mock_session.get.call_args[1]["params"]
        assert params["start_date"] == "2026-05-01"
        assert params["end_date"] == "2026-05-07"
        assert params["api_key"] == "TEST_KEY"


def test_main_dynamic_date_evaluation_and_propagation(monkeypatch):
    class MockDate(datetime.date):
        @classmethod
        def today(cls):
            return datetime.date(2026, 11, 10)

    monkeypatch.setattr(nasa_asteroids, "date", MockDate)
    monkeypatch.setattr(nasa_asteroids, "API_KEY", "TEST_KEY")

    valid_record = [{"id": "1", "name": "A", "closest_approach_date": "2026-11-10", "miss_distance_km": 100.0, "hazardous": False}]

    with (
        patch("nasa_asteroids.fetch_data", return_value={"near_earth_objects": {}}) as mock_fetch,
        patch("nasa_asteroids.save_raw_json"),
        patch("nasa_asteroids.extract_asteroids", return_value=(valid_record, 0, 1)),
        patch("nasa_asteroids.save_to_csv"),
        patch("nasa_asteroids.save_to_parquet"),
        patch("nasa_asteroids.load_data"),
        patch("nasa_asteroids.upload_raw_to_s3") as mock_upload_raw,
        patch("nasa_asteroids.upload_processed_to_s3") as mock_upload_proc,
    ):
        exit_code = nasa_asteroids.main()

        assert exit_code == 0
        expected_start = datetime.date(2026, 11, 10)
        expected_end = datetime.date(2026, 11, 16)

        assert mock_fetch.call_args[1]["start"] == expected_start
        assert mock_fetch.call_args[1]["end"] == expected_end
        assert mock_fetch.call_args[1]["key"] == "TEST_KEY"
        assert mock_upload_raw.call_args[1]["start_date"] == expected_start
        assert mock_upload_proc.call_args[1]["start_date"] == expected_start


def test_old_module_level_date_variables_do_not_exist():
    assert not hasattr(nasa_asteroids, "run_year")
    assert not hasattr(nasa_asteroids, "run_month")
    assert not hasattr(nasa_asteroids, "run_day")
    assert not hasattr(nasa_asteroids, "run_time")
    assert not hasattr(nasa_asteroids, "start_date")
    assert not hasattr(nasa_asteroids, "end_date")

def test_extract_asteroids_skips_malformed_close_approach_date():
    fake_data = {
        "near_earth_objects": {
            "2026-09-20": [
                {
                    "id": "1",
                    "name": "Asteroid Bad Date",
                    "is_potentially_hazardous_asteroid": False,
                    "close_approach_data": [
                        {
                            "close_approach_date": "not-a-valid-date",
                            "miss_distance": {"kilometers": "100000"}
                        }
                    ]
                },
                {
                    "id": "2",
                    "name": "Asteroid Impossible Date",
                    "is_potentially_hazardous_asteroid": False,
                    "close_approach_data": [
                        {
                            "close_approach_date": "2026-99-99",
                            "miss_distance": {"kilometers": "100000"}
                        }
                    ]
                }
            ]
        }
    }
    asteroid_data, skipped_records, records_received = nasa_asteroids.extract_asteroids(fake_data)
    assert len(asteroid_data) == 0
    assert skipped_records == 2
    assert records_received == 2


def test_extract_asteroids_skips_non_numeric_miss_distance():
    fake_data = {
        "near_earth_objects": {
            "2026-09-20": [
                {
                    "id": "1",
                    "name": "Asteroid Non Numeric",
                    "is_potentially_hazardous_asteroid": False,
                    "close_approach_data": [
                        {
                            "close_approach_date": "2026-09-20",
                            "miss_distance": {"kilometers": "invalid_number"}
                        }
                    ]
                }
            ]
        }
    }
    asteroid_data, skipped_records, records_received = nasa_asteroids.extract_asteroids(fake_data)
    assert len(asteroid_data) == 0
    assert skipped_records == 1
    assert records_received == 1


def test_extract_asteroids_skips_zero_and_negative_miss_distance():
    fake_data = {
        "near_earth_objects": {
            "2026-09-20": [
                {
                    "id": "1",
                    "name": "Asteroid Zero Dist",
                    "is_potentially_hazardous_asteroid": False,
                    "close_approach_data": [
                        {
                            "close_approach_date": "2026-09-20",
                            "miss_distance": {"kilometers": "0"}
                        }
                    ]
                },
                {
                    "id": "2",
                    "name": "Asteroid Neg Dist",
                    "is_potentially_hazardous_asteroid": False,
                    "close_approach_data": [
                        {
                            "close_approach_date": "2026-09-20",
                            "miss_distance": {"kilometers": "-500.5"}
                        }
                    ]
                }
            ]
        }
    }
    asteroid_data, skipped_records, records_received = nasa_asteroids.extract_asteroids(fake_data)
    assert len(asteroid_data) == 0
    assert skipped_records == 2
    assert records_received == 2


def test_extract_asteroids_skips_non_boolean_hazardous():
    fake_data = {
        "near_earth_objects": {
            "2026-09-20": [
                {
                    "id": "1",
                    "name": "Asteroid String Haz",
                    "is_potentially_hazardous_asteroid": "True",
                    "close_approach_data": [
                        {
                            "close_approach_date": "2026-09-20",
                            "miss_distance": {"kilometers": "100000"}
                        }
                    ]
                },
                {
                    "id": "2",
                    "name": "Asteroid None Haz",
                    "is_potentially_hazardous_asteroid": None,
                    "close_approach_data": [
                        {
                            "close_approach_date": "2026-09-20",
                            "miss_distance": {"kilometers": "100000"}
                        }
                    ]
                }
            ]
        }
    }
    asteroid_data, skipped_records, records_received = nasa_asteroids.extract_asteroids(fake_data)
    assert len(asteroid_data) == 0
    assert skipped_records == 2
    assert records_received == 2


def test_extract_asteroids_deduplicates_identical_approaches():
    fake_data = {
        "near_earth_objects": {
            "2026-09-20": [
                {
                    "id": "123",
                    "name": "Asteroid Dup",
                    "is_potentially_hazardous_asteroid": False,
                    "close_approach_data": [
                        {
                            "close_approach_date": "2026-09-20",
                            "miss_distance": {"kilometers": "100000"}
                        }
                    ]
                },
                {
                    "id": "123",
                    "name": "Asteroid Dup",
                    "is_potentially_hazardous_asteroid": False,
                    "close_approach_data": [
                        {
                            "close_approach_date": "2026-09-20",
                            "miss_distance": {"kilometers": "100000"}
                        }
                    ]
                }
            ]
        }
    }
    asteroid_data, skipped_records, records_received = nasa_asteroids.extract_asteroids(fake_data)
    assert len(asteroid_data) == 1
    assert skipped_records == 1
    assert records_received == 2


def test_main_zero_valid_records_returns_code_1(monkeypatch):
    monkeypatch.setattr(nasa_asteroids, "API_KEY", "TEST_KEY")
    invalid_data = {
        "near_earth_objects": {
            "2026-09-20": [
                {
                    "id": "1",
                    "name": "Bad Asteroid",
                    "is_potentially_hazardous_asteroid": "invalid_bool",
                    "close_approach_data": [
                        {
                            "close_approach_date": "2026-09-20",
                            "miss_distance": {"kilometers": "100000"}
                        }
                    ]
                }
            ]
        }
    }
    with patch("nasa_asteroids.fetch_data", return_value=invalid_data), \
         patch("nasa_asteroids.save_raw_json"):
        exit_code = nasa_asteroids.main()
        assert exit_code == 1


def test_main_empty_nasa_response_triggers_circuit_breaker(monkeypatch):
    monkeypatch.setattr(nasa_asteroids, "API_KEY", "TEST_KEY")
    empty_data = {"near_earth_objects": {}}

    with patch("nasa_asteroids.fetch_data", return_value=empty_data), \
         patch("nasa_asteroids.save_raw_json"):
        exit_code = nasa_asteroids.main()
        assert exit_code == 1


def test_circuit_breaker_prevents_processed_files_and_s3_uploads(monkeypatch):
    monkeypatch.setattr(nasa_asteroids, "API_KEY", "TEST_KEY")

    with patch("nasa_asteroids.fetch_data", return_value={"near_earth_objects": {}}), \
         patch("nasa_asteroids.save_raw_json") as mock_save_raw, \
         patch("nasa_asteroids.save_to_csv") as mock_save_csv, \
         patch("nasa_asteroids.save_to_parquet") as mock_save_parquet, \
         patch("nasa_asteroids.load_data") as mock_load_data, \
         patch("nasa_asteroids.upload_raw_to_s3") as mock_upload_raw, \
         patch("nasa_asteroids.upload_processed_to_s3") as mock_upload_proc:

        exit_code = nasa_asteroids.main()

        assert exit_code == 1
        mock_save_raw.assert_called_once()
        mock_save_csv.assert_not_called()
        mock_save_parquet.assert_not_called()
        mock_load_data.assert_not_called()
        mock_upload_raw.assert_not_called()
        mock_upload_proc.assert_not_called()


def test_s3_upload_functions_receive_expected_lineage_metadata():
    metadata = {
        "run_id": "test_run_123",
        "ingested_at": "2026-09-23T12:00:00+00:00",
        "source": "nasa_neows_api"
    }

    with patch("boto3.client") as mock_boto:
        mock_s3 = MagicMock()
        mock_boto.return_value = mock_s3

        nasa_asteroids.upload_raw_to_s3(metadata=metadata)
        assert mock_s3.upload_file.call_args[1]["ExtraArgs"] == {"Metadata": metadata}

    with patch("boto3.client") as mock_boto:
        mock_s3 = MagicMock()
        mock_boto.return_value = mock_s3

        nasa_asteroids.upload_processed_to_s3(metadata=metadata)
        assert mock_s3.upload_file.call_args_list[0][1]["ExtraArgs"] == {"Metadata": metadata}
        assert mock_s3.upload_file.call_args_list[1][1]["ExtraArgs"] == {"Metadata": metadata}


def test_main_propagates_same_run_id_and_ingested_at_to_both_s3_uploads(monkeypatch):
    monkeypatch.setattr(nasa_asteroids, "API_KEY", "TEST_KEY")
    valid_record = [{"id": "1", "name": "A", "closest_approach_date": "2026-09-20", "miss_distance_km": 100.0, "hazardous": False}]

    with patch("nasa_asteroids.fetch_data", return_value={"near_earth_objects": {}}), \
         patch("nasa_asteroids.save_raw_json"), \
         patch("nasa_asteroids.extract_asteroids", return_value=(valid_record, 0, 1)), \
         patch("nasa_asteroids.save_to_csv"), \
         patch("nasa_asteroids.save_to_parquet"), \
         patch("nasa_asteroids.load_data"), \
         patch("nasa_asteroids.upload_raw_to_s3") as mock_upload_raw, \
         patch("nasa_asteroids.upload_processed_to_s3") as mock_upload_proc:

        exit_code = nasa_asteroids.main()

        assert exit_code == 0
        raw_metadata = mock_upload_raw.call_args[1]["metadata"]
        proc_metadata = mock_upload_proc.call_args[1]["metadata"]

        assert raw_metadata == proc_metadata
        assert "run_id" in raw_metadata
        assert len(raw_metadata["run_id"]) == 12
        assert "ingested_at" in raw_metadata
        assert raw_metadata["source"] == "nasa_neows_api"


def test_rejection_percentage_warning_emitted_when_over_20_percent(monkeypatch, caplog):
    monkeypatch.setattr(nasa_asteroids, "API_KEY", "TEST_KEY")

    valid_records = [
        {"id": str(i), "name": f"A{i}", "closest_approach_date": "2026-09-20", "miss_distance_km": 100.0, "hazardous": False}
        for i in range(4)
    ]

    with patch("nasa_asteroids.fetch_data", return_value={"near_earth_objects": {}}), \
         patch("nasa_asteroids.save_raw_json"), \
         patch("nasa_asteroids.extract_asteroids", return_value=(valid_records, 2, 6)), \
         patch("nasa_asteroids.save_to_csv"), \
         patch("nasa_asteroids.save_to_parquet"), \
         patch("nasa_asteroids.load_data"), \
         patch("nasa_asteroids.upload_raw_to_s3"), \
         patch("nasa_asteroids.upload_processed_to_s3"):

        with caplog.at_level(logging.WARNING):
            exit_code = nasa_asteroids.main()

        assert exit_code == 1
        assert any("High rejection rate" in record.message for record in caplog.records)


def test_main_logs_run_id_and_ingested_at_at_startup(monkeypatch, caplog):
    monkeypatch.setattr(nasa_asteroids, "API_KEY", "TEST_KEY")
    valid_record = [{"id": "1", "name": "A", "closest_approach_date": "2026-09-20", "miss_distance_km": 100.0, "hazardous": False}]

    with (
        patch("nasa_asteroids.fetch_data", return_value={"near_earth_objects": {}}),
        patch("nasa_asteroids.save_raw_json"),
        patch("nasa_asteroids.extract_asteroids", return_value=(valid_record, 0, 1)),
        patch("nasa_asteroids.save_to_csv"),
        patch("nasa_asteroids.save_to_parquet"),
        patch("nasa_asteroids.load_data"),
        patch("nasa_asteroids.upload_raw_to_s3"),
        patch("nasa_asteroids.upload_processed_to_s3"),
    ):
        with caplog.at_level(logging.INFO):
            exit_code = nasa_asteroids.main()

        assert exit_code == 0
        startup_records = [r for r in caplog.records if "Starting NASA asteroid pipeline" in r.message]
        assert len(startup_records) == 1
        msg = startup_records[0].message
        assert "run_id:" in msg
        assert "ingested_at:" in msg


def test_main_logs_elapsed_duration_at_completion(monkeypatch, caplog):
    monkeypatch.setattr(nasa_asteroids, "API_KEY", "TEST_KEY")
    valid_record = [{"id": "1", "name": "A", "closest_approach_date": "2026-09-20", "miss_distance_km": 100.0, "hazardous": False}]

    with (
        patch("nasa_asteroids.fetch_data", return_value={"near_earth_objects": {}}),
        patch("nasa_asteroids.save_raw_json"),
        patch("nasa_asteroids.extract_asteroids", return_value=(valid_record, 0, 1)),
        patch("nasa_asteroids.save_to_csv"),
        patch("nasa_asteroids.save_to_parquet"),
        patch("nasa_asteroids.load_data"),
        patch("nasa_asteroids.upload_raw_to_s3"),
        patch("nasa_asteroids.upload_processed_to_s3"),
    ):
        with caplog.at_level(logging.INFO):
            exit_code = nasa_asteroids.main()

        assert exit_code == 0
        completion_records = [r for r in caplog.records if "completed successfully in" in r.message]
        assert len(completion_records) == 1
        assert re.search(r"completed successfully in \d+\.\d{2}s", completion_records[0].message)


def test_main_logs_sqlite_loading(monkeypatch, caplog):
    monkeypatch.setattr(nasa_asteroids, "API_KEY", "TEST_KEY")
    valid_record = [{"id": "1", "name": "A", "closest_approach_date": "2026-09-20", "miss_distance_km": 100.0, "hazardous": False}]

    with (
        patch("nasa_asteroids.fetch_data", return_value={"near_earth_objects": {}}),
        patch("nasa_asteroids.save_raw_json"),
        patch("nasa_asteroids.extract_asteroids", return_value=(valid_record, 0, 1)),
        patch("nasa_asteroids.save_to_csv"),
        patch("nasa_asteroids.save_to_parquet"),
        patch("nasa_asteroids.load_data"),
        patch("nasa_asteroids.upload_raw_to_s3"),
        patch("nasa_asteroids.upload_processed_to_s3"),
    ):
        with caplog.at_level(logging.INFO):
            exit_code = nasa_asteroids.main()

        assert exit_code == 0
        sqlite_records = [r for r in caplog.records if "Loaded 1 valid records into SQLite database" in r.message]
        assert len(sqlite_records) == 1


def test_main_logs_api_success_immediately_after_fetch(monkeypatch, caplog):
    monkeypatch.setattr(nasa_asteroids, "API_KEY", "TEST_KEY")
    valid_record = [{"id": "1", "name": "A", "closest_approach_date": "2026-09-20", "miss_distance_km": 100.0, "hazardous": False}]

    with (
        patch("nasa_asteroids.fetch_data", return_value={"near_earth_objects": {}}),
        patch("nasa_asteroids.save_raw_json"),
        patch("nasa_asteroids.extract_asteroids", return_value=(valid_record, 0, 1)),
        patch("nasa_asteroids.save_to_csv"),
        patch("nasa_asteroids.save_to_parquet"),
        patch("nasa_asteroids.load_data"),
        patch("nasa_asteroids.upload_raw_to_s3"),
        patch("nasa_asteroids.upload_processed_to_s3"),
    ):
        with caplog.at_level(logging.INFO):
            exit_code = nasa_asteroids.main()

        assert exit_code == 0
        messages = [r.message for r in caplog.records]
        api_success_idx = next(i for i, m in enumerate(messages) if "API request successful" in m)
        save_raw_idx = next(i for i, m in enumerate(messages) if "Saving raw NASA response" in m)
        assert api_success_idx < save_raw_idx


def test_top_level_error_logging_includes_run_id(tmp_path):
    repo_dir = os.path.dirname(os.path.abspath(__file__))
    target_script = os.path.join(repo_dir, "nasa_asteroids.py")
    env = os.environ.copy()
    env["PYTHONPATH"] = repo_dir
    env["DB_PATH"] = str(tmp_path / "subprocess.db")
    env["NASA_API_KEY"] = "TEST_KEY"

    script = (
        "from unittest.mock import patch\n"
        "import os, runpy\n"
        "with patch('requests.Session.get') as mock_get:\n"
        "    valid_data = {'near_earth_objects': {'2026-09-20': [{'id': '123', 'name': 'A', 'close_approach_data': [{'close_approach_date': '2026-09-20', 'miss_distance': {'kilometers': '100000'}}], 'is_potentially_hazardous_asteroid': False}]}}\n"
        "    mock_get.return_value.json.return_value = valid_data\n"
        "    mock_get.return_value.raise_for_status.return_value = None\n"
        "    with patch('boto3.client', side_effect=RuntimeError('Unexpected AWS error')):\n"
        "        with patch('nasa_asteroids.API_KEY', 'TEST_KEY'):\n"
        f"            runpy.run_path(r'{target_script}', run_name='__main__')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True
    )
    assert result.returncode == 1
    assert re.search(r"\[[0-9a-f]{12}\] Pipeline failure: Unexpected AWS error", result.stderr)


def test_neows_summary_emission_on_success(tmp_path, monkeypatch):
    monkeypatch.setattr(nasa_asteroids, "API_KEY", "TEST_KEY")
    valid_data = {
        "near_earth_objects": {
            "2026-09-28": [
                {
                    "id": "12345",
                    "name": "Asteroid A",
                    "close_approach_data": [{
                        "close_approach_date": "2026-09-28",
                        "miss_distance": {"kilometers": "100000"}
                    }],
                    "is_potentially_hazardous_asteroid": False
                }
            ]
        }
    }
    summary_file = tmp_path / "custom_neows_summary.json"

    with patch("nasa_asteroids.fetch_data", return_value=valid_data), \
         patch("nasa_asteroids.save_raw_json"), \
         patch("nasa_asteroids.save_to_csv"), \
         patch("nasa_asteroids.save_to_parquet"), \
         patch("nasa_asteroids.load_data"), \
         patch("nasa_asteroids.upload_raw_to_s3"), \
         patch("nasa_asteroids.upload_processed_to_s3"):

        exit_code = nasa_asteroids.main(
            start_date_str="2026-09-28",
            end_date_str="2026-10-04",
            summary_filename=str(summary_file)
        )
        assert exit_code == 0
        assert summary_file.exists()

        with open(summary_file, "r", encoding="utf-8") as f:
            summary = json.load(f)

        assert summary["records_received"] == 1
        assert summary["valid_records"] == 1
        assert summary["skipped_records"] == 0
        assert summary["rejection_pct"] == 0.0
        assert summary["gate_passed"] is True
        assert summary["start_date"] == "2026-09-28"
        assert summary["end_date"] == "2026-10-04"
        assert summary["run_id"] == nasa_asteroids.main.current_run_id


def test_neows_summary_emission_on_high_rejection_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(nasa_asteroids, "API_KEY", "TEST_KEY")
    # 1 valid, 1 skipped -> 50% rejection rate >= 20%
    data = {
        "near_earth_objects": {
            "2026-09-28": [
                {
                    "id": "12345",
                    "name": "Valid Asteroid",
                    "close_approach_data": [{
                        "close_approach_date": "2026-09-28",
                        "miss_distance": {"kilometers": "100000"}
                    }],
                    "is_potentially_hazardous_asteroid": False
                },
                {
                    "id": "99999",
                    "name": "",  # invalid: missing name
                    "close_approach_data": [{
                        "close_approach_date": "2026-09-28",
                        "miss_distance": {"kilometers": "100000"}
                    }],
                    "is_potentially_hazardous_asteroid": False
                }
            ]
        }
    }
    summary_file = tmp_path / "failed_neows_summary.json"

    with patch("nasa_asteroids.fetch_data", return_value=data), \
         patch("nasa_asteroids.save_raw_json"), \
         patch("nasa_asteroids.save_to_csv") as mock_csv, \
         patch("nasa_asteroids.save_to_parquet") as mock_parquet, \
         patch("nasa_asteroids.load_data") as mock_load, \
         patch("nasa_asteroids.upload_raw_to_s3") as mock_raw_s3, \
         patch("nasa_asteroids.upload_processed_to_s3") as mock_proc_s3:

        exit_code = nasa_asteroids.main(
            start_date_str="2026-09-28",
            end_date_str="2026-10-04",
            summary_filename=str(summary_file)
        )
        assert exit_code == 1
        assert summary_file.exists()

        with open(summary_file, "r", encoding="utf-8") as f:
            summary = json.load(f)

        assert summary["records_received"] == 2
        assert summary["valid_records"] == 1
        assert summary["skipped_records"] == 1
        assert summary["rejection_pct"] == 50.0
        assert summary["gate_passed"] is False

        # Verify production outputs were suppressed
        mock_csv.assert_not_called()
        mock_parquet.assert_not_called()
        mock_load.assert_not_called()
        mock_raw_s3.assert_not_called()
        mock_proc_s3.assert_not_called()


def test_neows_parquet_metadata_embeds_run_id(tmp_path):
    records = [
        {
            "id": "12345",
            "name": "Asteroid A",
            "closest_approach_date": "2026-09-28",
            "miss_distance_km": 150000.0,
            "hazardous": False
        }
    ]
    parquet_path = tmp_path / "asteroids_meta.parquet"
    nasa_asteroids.save_to_parquet(records, filename=str(parquet_path), run_id="run_abc_123")

    table = pq.read_table(str(parquet_path))
    assert table.num_rows == 1
    assert table.schema.metadata is not None
    assert b"run_id" in table.schema.metadata
    assert table.schema.metadata[b"run_id"].decode("utf-8") == "run_abc_123"


def test_neows_summary_write_failure_halts_pipeline(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(nasa_asteroids, "API_KEY", "TEST_KEY")
    valid_data = {
        "near_earth_objects": {
            "2026-09-28": [
                {
                    "id": "12345",
                    "name": "Asteroid A",
                    "close_approach_data": [{
                        "close_approach_date": "2026-09-28",
                        "miss_distance": {"kilometers": "100000"}
                    }],
                    "is_potentially_hazardous_asteroid": False
                }
            ]
        }
    }
    summary_file = tmp_path / "summary_write_fail.json"

    with patch("nasa_asteroids.fetch_data", return_value=valid_data), \
         patch("nasa_asteroids.save_raw_json") as mock_raw, \
         patch("nasa_asteroids.save_neows_summary", side_effect=IOError("Disk write error")), \
         patch("nasa_asteroids.save_to_csv") as mock_csv, \
         patch("nasa_asteroids.save_to_parquet") as mock_parquet, \
         patch("nasa_asteroids.load_data") as mock_load, \
         patch("nasa_asteroids.upload_raw_to_s3") as mock_raw_s3, \
         patch("nasa_asteroids.upload_processed_to_s3") as mock_proc_s3:

        with caplog.at_level(logging.ERROR):
            exit_code = nasa_asteroids.main(
                start_date_str="2026-09-28",
                end_date_str="2026-10-04",
                summary_filename=str(summary_file)
            )

        assert exit_code == 1
        # Raw forensic JSON was preserved prior to summary write
        assert mock_raw.called
        # Production outputs must be suppressed
        mock_csv.assert_not_called()
        mock_parquet.assert_not_called()
        mock_load.assert_not_called()
        mock_raw_s3.assert_not_called()
        mock_proc_s3.assert_not_called()

        # Must log ERROR, not WARNING
        assert any("Failed to write NeoWs authoritative summary" in record.message and record.levelname == "ERROR"
                   for record in caplog.records)
