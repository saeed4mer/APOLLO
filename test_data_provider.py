"""Tests for the data access layer behind the FastAPI serving layer (dashboard_data.py).

The fixtures below are also the clean-room lakehouse used by test_api.py.

Validates:
A. Watchlist contract & event grain (closest_approach_date, neows_id)
B. Identifier separation (NeoWs ID != SBDB SPK-ID, UUID5 format, source namespaces)
C. Entity resolution states (RESOLVED, UNRESOLVED, AMBIGUOUS, INVALID)
D. Null safety across uncharacterized and unmonitored targets
E. Sentry reverse cardinality defense (ambiguity flags, scalar metric suppression)
F. Historical risk grain and snapshot sequencing
G. Scientific safety (zero danger scores, non-causal reporting)
H. No data fabrication (no synthetic velocity, no invented physical parameters)
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from entity_resolution import (
    BRIDGE_ASTEROID_IDENTIFIER_SCHEMA,
    FACT_ENTITY_RESOLUTION_SCHEMA,
)
from nasa_asteroids import ASTEROID_SCHEMA
from nasa_sbdb import (
    SBDB_OBJECT_SCHEMA,
    SBDB_ORBIT_ELEMENT_SCHEMA,
    SBDB_ORBIT_SCHEMA,
    SBDB_PHYS_PAR_SCHEMA,
)
from nasa_sentry import SENTRY_RISK_SNAPSHOT_SCHEMA

from dashboard_data import UUID5_PATTERN, DashboardDataProvider, LocalDuckDBDataProvider

PROJ_DIR = Path(__file__).resolve().parent


_FIXTURE_ASTEROIDS = [
    {
        "id": "2138971",
        "name": "138971 (2001 CB21)",
        "closest_approach_date": "2026-09-29",
        "miss_distance_km": 8096701.943856126,
        "hazardous": True,
    },
    {
        "id": "3448110",
        "name": "(2009 DC12)",
        "closest_approach_date": "2026-09-29",
        "miss_distance_km": 64533048.981356874,
        "hazardous": False,
    },
    {
        "id": "3648745",
        "name": "(2013 TL)",
        "closest_approach_date": "2026-09-29",
        "miss_distance_km": 47966664.318208545,
        "hazardous": False,
    },
    {
        "id": "3666677",
        "name": "(2014 FR37)",
        "closest_approach_date": "2026-09-29",
        "miss_distance_km": 39845554.075641945,
        "hazardous": False,
    },
    {
        "id": "3132507",
        "name": "(2002 PN6)",
        "closest_approach_date": "2026-09-30",
        "miss_distance_km": 62349988.91125866,
        "hazardous": False,
    },
    {
        "id": "3548666",
        "name": "(2010 TW54)",
        "closest_approach_date": "2026-09-30",
        "miss_distance_km": 17457205.45180993,
        "hazardous": False,
    },
    {
        "id": "3616702",
        "name": "(2012 VT6)",
        "closest_approach_date": "2026-09-30",
        "miss_distance_km": 22754832.15433697,
        "hazardous": False,
    },
    {
        "id": "3682467",
        "name": "(2014 QJ33)",
        "closest_approach_date": "2026-09-30",
        "miss_distance_km": 16885636.660463914,
        "hazardous": False,
    },
    {
        "id": "3830890",
        "name": "(2018 SP2)",
        "closest_approach_date": "2026-09-30",
        "miss_distance_km": 1927097.426378009,
        "hazardous": False,
    },
    {
        "id": "2376848",
        "name": "376848 (2001 RY47)",
        "closest_approach_date": "2026-09-26",
        "miss_distance_km": 19724592.195650574,
        "hazardous": False,
    },
    {
        "id": "3092330",
        "name": "(2001 SY169)",
        "closest_approach_date": "2026-09-26",
        "miss_distance_km": 70322607.8143903,
        "hazardous": False,
    },
    {
        "id": "3291224",
        "name": "(2005 SP9)",
        "closest_approach_date": "2026-09-26",
        "miss_distance_km": 56699114.96312618,
        "hazardous": False,
    },
    {
        "id": "3427460",
        "name": "(2008 ST)",
        "closest_approach_date": "2026-09-26",
        "miss_distance_km": 14878930.159093497,
        "hazardous": False,
    },
    {
        "id": "3629117",
        "name": "(2013 ET)",
        "closest_approach_date": "2026-09-26",
        "miss_distance_km": 70330127.72532052,
        "hazardous": False,
    },
    {
        "id": "3717039",
        "name": "(2015 HA10)",
        "closest_approach_date": "2026-09-26",
        "miss_distance_km": 38850386.1696524,
        "hazardous": False,
    },
    {
        "id": "3830851",
        "name": "(2018 SD1)",
        "closest_approach_date": "2026-09-26",
        "miss_distance_km": 35828309.58721446,
        "hazardous": False,
    },
    {
        "id": "3843612",
        "name": "(2019 QY1)",
        "closest_approach_date": "2026-09-26",
        "miss_distance_km": 74134162.95081787,
        "hazardous": True,
    },
    {
        "id": "3753791",
        "name": "(2016 LY8)",
        "closest_approach_date": "2026-09-25",
        "miss_distance_km": 13142326.651150573,
        "hazardous": False,
    },
    {
        "id": "3803909",
        "name": "(2018 HN)",
        "closest_approach_date": "2026-09-25",
        "miss_distance_km": 16612223.948800756,
        "hazardous": False,
    },
    {
        "id": "3824978",
        "name": "(2018 KS)",
        "closest_approach_date": "2026-09-25",
        "miss_distance_km": 34180550.04021606,
        "hazardous": False,
    },
    {
        "id": "2523934",
        "name": "523934 (1998 FF14)",
        "closest_approach_date": "2026-09-28",
        "miss_distance_km": 15393500.935266983,
        "hazardous": True,
    },
    {
        "id": "3256319",
        "name": "(2004 TB10)",
        "closest_approach_date": "2026-09-28",
        "miss_distance_km": 35666461.52163483,
        "hazardous": True,
    },
    {
        "id": "3691093",
        "name": "(2014 SQ260)",
        "closest_approach_date": "2026-09-28",
        "miss_distance_km": 39365623.793291196,
        "hazardous": False,
    },
    {
        "id": "3713318",
        "name": "(2015 EQ7)",
        "closest_approach_date": "2026-09-28",
        "miss_distance_km": 29487898.560108572,
        "hazardous": False,
    },
    {
        "id": "3748418",
        "name": "(2016 FL12)",
        "closest_approach_date": "2026-09-28",
        "miss_distance_km": 63605339.886157274,
        "hazardous": False,
    },
    {
        "id": "3773752",
        "name": "(2017 GK4)",
        "closest_approach_date": "2026-09-28",
        "miss_distance_km": 18423686.14113296,
        "hazardous": True,
    },
    {
        "id": "2499496",
        "name": "499496 (2010 MR87)",
        "closest_approach_date": "2026-10-01",
        "miss_distance_km": 28345941.169972755,
        "hazardous": False,
    },
    {
        "id": "3430304",
        "name": "(2008 TX3)",
        "closest_approach_date": "2026-10-01",
        "miss_distance_km": 65905705.79459494,
        "hazardous": False,
    },
    {
        "id": "3689018",
        "name": "(2014 RX22)",
        "closest_approach_date": "2026-10-01",
        "miss_distance_km": 37754643.5707825,
        "hazardous": False,
    },
    {
        "id": "3747498",
        "name": "(2016 FB)",
        "closest_approach_date": "2026-10-01",
        "miss_distance_km": 64412686.73885502,
        "hazardous": False,
    },
    {
        "id": "3825100",
        "name": "(2018 LC1)",
        "closest_approach_date": "2026-10-01",
        "miss_distance_km": 71893233.35323587,
        "hazardous": False,
    },
    {
        "id": "3869353",
        "name": "(2019 SD7)",
        "closest_approach_date": "2026-10-01",
        "miss_distance_km": 43473504.48498495,
        "hazardous": False,
    },
    {
        "id": "3692560",
        "name": "(2014 TU)",
        "closest_approach_date": "2026-09-27",
        "miss_distance_km": 40650768.30598433,
        "hazardous": False,
    },
    {
        "id": "3838859",
        "name": "(2019 DJ1)",
        "closest_approach_date": "2026-09-27",
        "miss_distance_km": 42105242.557971746,
        "hazardous": False,
    },
    {
        "id": "3872621",
        "name": "(2019 SH7)",
        "closest_approach_date": "2026-09-27",
        "miss_distance_km": 32446692.879890166,
        "hazardous": False,
    },
]

_FIXTURE_BRIDGE = [
    {
        "asteroid_key": "ast_8520aaac-9c77-5e8f-9a88-b4e501749e26",
        "source_system": "neows",
        "identifier_name": "id",
        "identifier_value": "3427460",
        "is_primary_pivot": False,
        "created_at": "2026-09-28T01:15:26.842071+00:00",
        "updated_at": "2026-09-28T01:15:26.842071+00:00",
    },
    {
        "asteroid_key": "ast_8520aaac-9c77-5e8f-9a88-b4e501749e26",
        "source_system": "sbdb",
        "identifier_name": "des",
        "identifier_value": "2008 ST",
        "is_primary_pivot": False,
        "created_at": "2026-09-28T01:15:26.842071+00:00",
        "updated_at": "2026-09-28T01:15:26.842071+00:00",
    },
    {
        "asteroid_key": "ast_8520aaac-9c77-5e8f-9a88-b4e501749e26",
        "source_system": "sbdb",
        "identifier_name": "spkid",
        "identifier_value": "50427483",
        "is_primary_pivot": True,
        "created_at": "2026-09-28T01:15:26.842071+00:00",
        "updated_at": "2026-09-28T01:15:26.842071+00:00",
    },
    {
        "asteroid_key": "ast_8520aaac-9c77-5e8f-9a88-b4e501749e26",
        "source_system": "sentry",
        "identifier_name": "des",
        "identifier_value": "2008 ST",
        "is_primary_pivot": False,
        "created_at": "2026-09-28T01:15:26.842071+00:00",
        "updated_at": "2026-09-28T01:15:26.842071+00:00",
    },
    {
        "asteroid_key": "ast_8520aaac-9c77-5e8f-9a88-b4e501749e26",
        "source_system": "sentry",
        "identifier_name": "sentry_id",
        "identifier_value": "bK08S00T",
        "is_primary_pivot": False,
        "created_at": "2026-09-28T01:15:26.842071+00:00",
        "updated_at": "2026-09-28T01:15:26.842071+00:00",
    },
    {
        "asteroid_key": "ast_b8259bf1-e6e5-5059-853e-9434274cdf2c",
        "source_system": "neows",
        "identifier_name": "id",
        "identifier_value": "3548666",
        "is_primary_pivot": False,
        "created_at": "2026-09-28T01:15:26.842071+00:00",
        "updated_at": "2026-09-28T01:15:26.842071+00:00",
    },
    {
        "asteroid_key": "ast_b8259bf1-e6e5-5059-853e-9434274cdf2c",
        "source_system": "sbdb",
        "identifier_name": "des",
        "identifier_value": "2010 TW54",
        "is_primary_pivot": False,
        "created_at": "2026-09-28T01:15:26.842071+00:00",
        "updated_at": "2026-09-28T01:15:26.842071+00:00",
    },
    {
        "asteroid_key": "ast_b8259bf1-e6e5-5059-853e-9434274cdf2c",
        "source_system": "sbdb",
        "identifier_name": "spkid",
        "identifier_value": "50548689",
        "is_primary_pivot": True,
        "created_at": "2026-09-28T01:15:26.842071+00:00",
        "updated_at": "2026-09-28T01:15:26.842071+00:00",
    },
    {
        "asteroid_key": "ast_b8259bf1-e6e5-5059-853e-9434274cdf2c",
        "source_system": "sentry",
        "identifier_name": "des",
        "identifier_value": "2010 TW54",
        "is_primary_pivot": False,
        "created_at": "2026-09-28T01:15:26.842071+00:00",
        "updated_at": "2026-09-28T01:15:26.842071+00:00",
    },
    {
        "asteroid_key": "ast_b8259bf1-e6e5-5059-853e-9434274cdf2c",
        "source_system": "sentry",
        "identifier_name": "sentry_id",
        "identifier_value": "bK10T54W",
        "is_primary_pivot": False,
        "created_at": "2026-09-28T01:15:26.842071+00:00",
        "updated_at": "2026-09-28T01:15:26.842071+00:00",
    },
]

_FIXTURE_RESOLUTION = [
    {
        "resolution_run_id": "9d0721990813",
        "resolved_at": "2026-09-28T01:15:26.842071+00:00",
        "source_system": "neows",
        "identifier_name": "id",
        "source_identifier_value": "2523934",
        "matched_target_system": None,
        "matched_target_identifier_name": None,
        "matched_target_identifier_value": None,
        "assigned_asteroid_key": None,
        "match_state": "UNRESOLVED",
        "match_rule": "NO_CROSS_SOURCE_MATCH",
        "evidence_json": '{"name": "523934 (1998 FF14)", "normalized_designation": "523934 (1998 FF14)", "reason": "NeoWs ID and designation not found in SBDB catalog"}',
    },
    {
        "resolution_run_id": "9d0721990813",
        "resolved_at": "2026-09-28T01:15:26.842071+00:00",
        "source_system": "neows",
        "identifier_name": "id",
        "source_identifier_value": "3427460",
        "matched_target_system": "sbdb",
        "matched_target_identifier_name": "des",
        "matched_target_identifier_value": "2008 ST",
        "assigned_asteroid_key": "ast_8520aaac-9c77-5e8f-9a88-b4e501749e26",
        "match_state": "RESOLVED",
        "match_rule": "EXACT_DESIGNATION_MATCH",
        "evidence_json": '{"matched_sbdb_des": "2008 ST", "matched_spkid": "50427483", "name": "(2008 ST)", "normalized_designation": "2008 ST", "resolution_rule": "EXACT_DESIGNATION_MATCH"}',
    },
    {
        "resolution_run_id": "9d0721990813",
        "resolved_at": "2026-09-28T01:15:26.842071+00:00",
        "source_system": "neows",
        "identifier_name": "id",
        "source_identifier_value": "3548666",
        "matched_target_system": "sbdb",
        "matched_target_identifier_name": "des",
        "matched_target_identifier_value": "2010 TW54",
        "assigned_asteroid_key": "ast_b8259bf1-e6e5-5059-853e-9434274cdf2c",
        "match_state": "RESOLVED",
        "match_rule": "EXACT_DESIGNATION_MATCH",
        "evidence_json": '{"matched_sbdb_des": "2010 TW54", "matched_spkid": "50548689", "name": "(2010 TW54)", "normalized_designation": "2010 TW54", "resolution_rule": "EXACT_DESIGNATION_MATCH"}',
    },
]

_FIXTURE_SBDB_OBJ = [
    {
        "snapshot_key": "2026-09-26",
        "run_id": "26b0c1ef0bba",
        "snapshot_time": "2026-09-26T20:27:39.451638+00:00",
        "spkid": "50548689",
        "designation": "2010 TW54",
        "fullname": "(2010 TW54)",
        "shortname": None,
        "object_kind": "au",
        "is_neo": True,
        "is_pha": False,
        "orbit_class_code": "APO",
        "orbit_class_name": "Apollo",
        "orbit_id": "14",
        "prefix": None,
    },
    {
        "snapshot_key": "2026-09-26",
        "run_id": "3a217b3b1657",
        "snapshot_time": "2026-09-26T20:34:56.545486+00:00",
        "spkid": "50427483",
        "designation": "2008 ST",
        "fullname": "(2008 ST)",
        "shortname": None,
        "object_kind": "au",
        "is_neo": True,
        "is_pha": False,
        "orbit_class_code": "ATE",
        "orbit_class_name": "Aten",
        "orbit_id": "14",
        "prefix": None,
    },
]

_FIXTURE_SBDB_ORB = [
    {
        "snapshot_key": "2026-09-26",
        "run_id": "26b0c1ef0bba",
        "snapshot_time": "2026-09-26T20:27:39.451638+00:00",
        "spkid": "50548689",
        "orbit_id": "14",
        "epoch_jd": 2461200.5,
        "equinox": "J2000",
        "soln_date": "2021-04-15 04:16:11",
        "orbit_source": "JPL",
        "producer": "Otto Matic",
        "first_obs": "2010-10-11",
        "last_obs": "2010-10-16",
        "data_arc_days": 5,
        "n_obs_used": 70,
        "condition_code": "6",
        "rms": 0.47283,
        "earth_moid_au": 0.000607628,
        "jupiter_moid_au": 3.81749,
        "t_jup": 5.859,
        "pe_used": "DE441",
        "sb_used": "SB441-N16",
    },
    {
        "snapshot_key": "2026-09-26",
        "run_id": "3a217b3b1657",
        "snapshot_time": "2026-09-26T20:34:56.545486+00:00",
        "spkid": "50427483",
        "orbit_id": "14",
        "epoch_jd": 2461200.5,
        "equinox": "J2000",
        "soln_date": "2021-04-15 01:51:10",
        "orbit_source": "JPL",
        "producer": "Otto Matic",
        "first_obs": "2008-09-22",
        "last_obs": "2008-09-29",
        "data_arc_days": 7,
        "n_obs_used": 49,
        "condition_code": "5",
        "rms": 0.45709,
        "earth_moid_au": 0.00192604,
        "jupiter_moid_au": 3.94437,
        "t_jup": 6.255,
        "pe_used": "DE441",
        "sb_used": "SB441-N16",
    },
]

_FIXTURE_SBDB_ELEM = [
    {
        "snapshot_key": "2026-09-26",
        "run_id": "26b0c1ef0bba",
        "snapshot_time": "2026-09-26T20:27:39.451638+00:00",
        "spkid": "50548689",
        "orbit_id": "14",
        "epoch_jd": 2461200.5,
        "equinox": "J2000",
        "element_name": "e",
        "element_value": 0.2340036536591127,
        "sigma": 0.00010316,
        "units": None,
        "title": "eccentricity",
        "label": "e",
    },
    {
        "snapshot_key": "2026-09-26",
        "run_id": "26b0c1ef0bba",
        "snapshot_time": "2026-09-26T20:27:39.451638+00:00",
        "spkid": "50548689",
        "orbit_id": "14",
        "epoch_jd": 2461200.5,
        "equinox": "J2000",
        "element_name": "a",
        "element_value": 1.042699543697046,
        "sigma": 2.2936e-05,
        "units": "au",
        "title": "semi-major axis",
        "label": "a",
    },
    {
        "snapshot_key": "2026-09-26",
        "run_id": "26b0c1ef0bba",
        "snapshot_time": "2026-09-26T20:27:39.451638+00:00",
        "spkid": "50548689",
        "orbit_id": "14",
        "epoch_jd": 2461200.5,
        "equinox": "J2000",
        "element_name": "q",
        "element_value": 0.7987040408032474,
        "sigma": 0.00012512,
        "units": "au",
        "title": "perihelion distance",
        "label": "q",
    },
    {
        "snapshot_key": "2026-09-26",
        "run_id": "26b0c1ef0bba",
        "snapshot_time": "2026-09-26T20:27:39.451638+00:00",
        "spkid": "50548689",
        "orbit_id": "14",
        "epoch_jd": 2461200.5,
        "equinox": "J2000",
        "element_name": "i",
        "element_value": 3.847985990811305,
        "sigma": 0.0021682,
        "units": "deg",
        "title": "inclination; angle with respect to x-y ecliptic plane",
        "label": "i",
    },
    {
        "snapshot_key": "2026-09-26",
        "run_id": "26b0c1ef0bba",
        "snapshot_time": "2026-09-26T20:27:39.451638+00:00",
        "spkid": "50548689",
        "orbit_id": "14",
        "epoch_jd": 2461200.5,
        "equinox": "J2000",
        "element_name": "om",
        "element_value": 196.2035517490737,
        "sigma": 0.0014158,
        "units": "deg",
        "title": "longitude of the ascending node",
        "label": "node",
    },
    {
        "snapshot_key": "2026-09-26",
        "run_id": "26b0c1ef0bba",
        "snapshot_time": "2026-09-26T20:27:39.451638+00:00",
        "spkid": "50548689",
        "orbit_id": "14",
        "epoch_jd": 2461200.5,
        "equinox": "J2000",
        "element_name": "w",
        "element_value": 86.09566594834301,
        "sigma": 0.014773,
        "units": "deg",
        "title": "argument of perihelion",
        "label": "peri",
    },
    {
        "snapshot_key": "2026-09-26",
        "run_id": "26b0c1ef0bba",
        "snapshot_time": "2026-09-26T20:27:39.451638+00:00",
        "spkid": "50548689",
        "orbit_id": "14",
        "epoch_jd": 2461200.5,
        "equinox": "J2000",
        "element_name": "ma",
        "element_value": 319.6240400428038,
        "sigma": 0.34447,
        "units": "deg",
        "title": "mean anomaly",
        "label": "M",
    },
    {
        "snapshot_key": "2026-09-26",
        "run_id": "26b0c1ef0bba",
        "snapshot_time": "2026-09-26T20:27:39.451638+00:00",
        "spkid": "50548689",
        "orbit_id": "14",
        "epoch_jd": 2461200.5,
        "equinox": "J2000",
        "element_name": "tp",
        "element_value": 2461244.117177873,
        "sigma": 0.37069,
        "units": "TDB",
        "title": "time of perihelion passage",
        "label": "tp",
    },
    {
        "snapshot_key": "2026-09-26",
        "run_id": "26b0c1ef0bba",
        "snapshot_time": "2026-09-26T20:27:39.451638+00:00",
        "spkid": "50548689",
        "orbit_id": "14",
        "epoch_jd": 2461200.5,
        "equinox": "J2000",
        "element_name": "per",
        "element_value": 388.8993364112806,
        "sigma": 0.012832,
        "units": "d",
        "title": "sidereal orbital period",
        "label": "period",
    },
    {
        "snapshot_key": "2026-09-26",
        "run_id": "26b0c1ef0bba",
        "snapshot_time": "2026-09-26T20:27:39.451638+00:00",
        "spkid": "50548689",
        "orbit_id": "14",
        "epoch_jd": 2461200.5,
        "equinox": "J2000",
        "element_name": "n",
        "element_value": 0.9256894170147976,
        "sigma": 3.0543e-05,
        "units": "deg/d",
        "title": "mean motion",
        "label": "n",
    },
    {
        "snapshot_key": "2026-09-26",
        "run_id": "26b0c1ef0bba",
        "snapshot_time": "2026-09-26T20:27:39.451638+00:00",
        "spkid": "50548689",
        "orbit_id": "14",
        "epoch_jd": 2461200.5,
        "equinox": "J2000",
        "element_name": "ad",
        "element_value": 1.286695046590844,
        "sigma": 2.8303e-05,
        "units": "au",
        "title": "aphelion distance",
        "label": "Q",
    },
    {
        "snapshot_key": "2026-09-26",
        "run_id": "3a217b3b1657",
        "snapshot_time": "2026-09-26T20:34:56.545486+00:00",
        "spkid": "50427483",
        "orbit_id": "14",
        "epoch_jd": 2461200.5,
        "equinox": "J2000",
        "element_name": "e",
        "element_value": 0.1261923500852797,
        "sigma": 7.6202e-05,
        "units": None,
        "title": "eccentricity",
        "label": "e",
    },
    {
        "snapshot_key": "2026-09-26",
        "run_id": "3a217b3b1657",
        "snapshot_time": "2026-09-26T20:34:56.545486+00:00",
        "spkid": "50427483",
        "orbit_id": "14",
        "epoch_jd": 2461200.5,
        "equinox": "J2000",
        "element_name": "a",
        "element_value": 0.9632117464463521,
        "sigma": 0.00013109,
        "units": "au",
        "title": "semi-major axis",
        "label": "a",
    },
    {
        "snapshot_key": "2026-09-26",
        "run_id": "3a217b3b1657",
        "snapshot_time": "2026-09-26T20:34:56.545486+00:00",
        "spkid": "50427483",
        "orbit_id": "14",
        "epoch_jd": 2461200.5,
        "equinox": "J2000",
        "element_name": "q",
        "element_value": 0.8416617925325404,
        "sigma": 0.00018792,
        "units": "au",
        "title": "perihelion distance",
        "label": "q",
    },
    {
        "snapshot_key": "2026-09-26",
        "run_id": "3a217b3b1657",
        "snapshot_time": "2026-09-26T20:34:56.545486+00:00",
        "spkid": "50427483",
        "orbit_id": "14",
        "epoch_jd": 2461200.5,
        "equinox": "J2000",
        "element_name": "i",
        "element_value": 1.868007353600377,
        "sigma": 0.0095851,
        "units": "deg",
        "title": "inclination; angle with respect to x-y ecliptic plane",
        "label": "i",
    },
    {
        "snapshot_key": "2026-09-26",
        "run_id": "3a217b3b1657",
        "snapshot_time": "2026-09-26T20:34:56.545486+00:00",
        "spkid": "50427483",
        "orbit_id": "14",
        "epoch_jd": 2461200.5,
        "equinox": "J2000",
        "element_name": "om",
        "element_value": 187.3926350471293,
        "sigma": 0.29358,
        "units": "deg",
        "title": "longitude of the ascending node",
        "label": "node",
    },
    {
        "snapshot_key": "2026-09-26",
        "run_id": "3a217b3b1657",
        "snapshot_time": "2026-09-26T20:34:56.545486+00:00",
        "spkid": "50427483",
        "orbit_id": "14",
        "epoch_jd": 2461200.5,
        "equinox": "J2000",
        "element_name": "w",
        "element_value": 292.32400927,
        "sigma": 0.33933,
        "units": "deg",
        "title": "argument of perihelion",
        "label": "peri",
    },
    {
        "snapshot_key": "2026-09-26",
        "run_id": "3a217b3b1657",
        "snapshot_time": "2026-09-26T20:34:56.545486+00:00",
        "spkid": "50427483",
        "orbit_id": "14",
        "epoch_jd": 2461200.5,
        "equinox": "J2000",
        "element_name": "ma",
        "element_value": 148.799540786337,
        "sigma": 0.4409,
        "units": "deg",
        "title": "mean anomaly",
        "label": "M",
    },
    {
        "snapshot_key": "2026-09-26",
        "run_id": "3a217b3b1657",
        "snapshot_time": "2026-09-26T20:34:56.545486+00:00",
        "spkid": "50427483",
        "orbit_id": "14",
        "epoch_jd": 2461200.5,
        "equinox": "J2000",
        "element_name": "tp",
        "element_value": 2461057.781532928,
        "sigma": 0.39375,
        "units": "TDB",
        "title": "time of perihelion passage",
        "label": "tp",
    },
    {
        "snapshot_key": "2026-09-26",
        "run_id": "3a217b3b1657",
        "snapshot_time": "2026-09-26T20:34:56.545486+00:00",
        "spkid": "50427483",
        "orbit_id": "14",
        "epoch_jd": 2461200.5,
        "equinox": "J2000",
        "element_name": "per",
        "element_value": 345.2876794809105,
        "sigma": 0.070487,
        "units": "d",
        "title": "sidereal orbital period",
        "label": "period",
    },
    {
        "snapshot_key": "2026-09-26",
        "run_id": "3a217b3b1657",
        "snapshot_time": "2026-09-26T20:34:56.545486+00:00",
        "spkid": "50427483",
        "orbit_id": "14",
        "epoch_jd": 2461200.5,
        "equinox": "J2000",
        "element_name": "n",
        "element_value": 1.042608877736986,
        "sigma": 0.00021284,
        "units": "deg/d",
        "title": "mean motion",
        "label": "n",
    },
    {
        "snapshot_key": "2026-09-26",
        "run_id": "3a217b3b1657",
        "snapshot_time": "2026-09-26T20:34:56.545486+00:00",
        "spkid": "50427483",
        "orbit_id": "14",
        "epoch_jd": 2461200.5,
        "equinox": "J2000",
        "element_name": "ad",
        "element_value": 1.084761700360164,
        "sigma": 0.00014763,
        "units": "au",
        "title": "aphelion distance",
        "label": "Q",
    },
]

_FIXTURE_SBDB_PHYS = [
    {
        "snapshot_key": "2026-09-26",
        "run_id": "26b0c1ef0bba",
        "snapshot_time": "2026-09-26T20:27:39.451638+00:00",
        "spkid": "50548689",
        "param_name": "H",
        "param_value_numeric": 27.6,
        "param_value_raw": "27.6",
        "sigma": None,
        "units": None,
        "bib_reference": "MPO186394",
        "notes": None,
        "title": "absolute magnitude",
        "desc": "absolute magnitude (magnitude at 1 au from Sun and observer)",
    },
    {
        "snapshot_key": "2026-09-26",
        "run_id": "3a217b3b1657",
        "snapshot_time": "2026-09-26T20:34:56.545486+00:00",
        "spkid": "50427483",
        "param_name": "H",
        "param_value_numeric": 27.1,
        "param_value_raw": "27.1",
        "sigma": None,
        "units": None,
        "bib_reference": "MPO147478",
        "notes": None,
        "title": "absolute magnitude",
        "desc": "absolute magnitude (magnitude at 1 au from Sun and observer)",
    },
]

_FIXTURE_SENTRY = [
    {
        "snapshot_key": "2026-09-26",
        "run_id": "7d446dc65b5a",
        "snapshot_time": "2026-09-26T20:15:16.035354+00:00",
        "sentry_id": "bK10T54W",
        "designation": "2010 TW54",
        "fullname": "(2010 TW54)",
        "absolute_magnitude": 27.55,
        "estimated_diameter_km": 0.01,
        "impact_probability": 6.594578e-05,
        "potential_impacts_count": 16,
        "palermo_scale_cum": -5.76,
        "palermo_scale_max": -6.12,
        "torino_scale_max": 0,
        "v_infinity_km_s": 7.76209523793638,
        "impact_year_range": "2088-2122",
        "last_obs_date": "2010-10-16",
        "last_obs_jd": 2455485.5,
    },
    {
        "snapshot_key": "2026-09-26",
        "run_id": "7d446dc65b5a",
        "snapshot_time": "2026-09-26T20:15:16.035354+00:00",
        "sentry_id": "bK08S00T",
        "designation": "2008 ST",
        "fullname": "(2008 ST)",
        "absolute_magnitude": 27.07,
        "estimated_diameter_km": 0.013,
        "impact_probability": 0.00013679128,
        "potential_impacts_count": 64,
        "palermo_scale_cum": -5.27,
        "palermo_scale_max": -5.68,
        "torino_scale_max": 0,
        "v_infinity_km_s": 3.24499536659062,
        "impact_year_range": "2048-2122",
        "last_obs_date": "2008-09-29",
        "last_obs_jd": 2454738.5,
    },
]

_DETERMINISTIC_LAKEHOUSE_SPECS = [
    ("asteroids.parquet", _FIXTURE_ASTEROIDS, ASTEROID_SCHEMA),
    (
        "bridge_asteroid_identifier.parquet",
        _FIXTURE_BRIDGE,
        BRIDGE_ASTEROID_IDENTIFIER_SCHEMA,
    ),
    (
        "fact_entity_resolution.parquet",
        _FIXTURE_RESOLUTION,
        FACT_ENTITY_RESOLUTION_SCHEMA,
    ),
    ("fact_sbdb_object_snapshot.parquet", _FIXTURE_SBDB_OBJ, SBDB_OBJECT_SCHEMA),
    ("fact_sbdb_orbit.parquet", _FIXTURE_SBDB_ORB, SBDB_ORBIT_SCHEMA),
    ("fact_sbdb_orbit_element.parquet", _FIXTURE_SBDB_ELEM, SBDB_ORBIT_ELEMENT_SCHEMA),
    ("fact_sbdb_physical_parameter.parquet", _FIXTURE_SBDB_PHYS, SBDB_PHYS_PAR_SCHEMA),
    ("fact_sentry_risk_snapshot.parquet", _FIXTURE_SENTRY, SENTRY_RISK_SNAPSHOT_SCHEMA),
]


@pytest.fixture(scope="session", autouse=True)
def ensure_deterministic_lakehouse_fixtures():
    """Ensure minimal deterministic lakehouse Parquet files exist in PROJ_DIR.

    In clean checkouts (such as CI), the local lakehouse Parquet files are absent.
    This fixture creates the exact deterministic records required by the dashboard
    contract and regression tests, then cleans up newly created files on teardown.
    """
    created_files: list[Path] = []
    for filename, records, schema in _DETERMINISTIC_LAKEHOUSE_SPECS:
        target_path = PROJ_DIR / filename
        if not target_path.exists():
            table = pa.Table.from_pylist(records, schema=schema)
            pq.write_table(table, target_path, compression="snappy")
            created_files.append(target_path)
    try:
        yield
    finally:
        for created_path in created_files:
            if created_path.exists():
                try:
                    created_path.unlink()
                except OSError:
                    pass


@pytest.fixture
def provider() -> DashboardDataProvider:
    """Default local provider pointing to actual lakehouse Parquet files."""
    return DashboardDataProvider(base_dir=PROJ_DIR, execution_mode="LOCAL")


# ============================================================================
# A. WATCHLIST CONTRACT & GRAIN TESTS
# ============================================================================


def test_watchlist_contract_and_columns(provider: DashboardDataProvider):
    """Verify threat watchlist returns all required operational columns."""
    df = provider.get_threat_watchlist()
    assert isinstance(df, pd.DataFrame)
    assert not df.empty, "Watchlist should contain active close-approach rows."

    expected_cols = [
        "closest_approach_date",
        "neows_id",
        "name",
        "miss_distance_km",
        "miss_distance_lunar",
        "hazardous",
        "asteroid_key",
        "match_state",
        "is_sentry_monitored",
        "is_sentry_ambiguous",
        "sentry_id",
        "sentry_impact_probability",
        "sentry_palermo_scale_max",
        "sentry_torino_scale_max",
        "sentry_potential_impacts_count",
        "sentry_impact_year_range",
        "has_sbdb_characterization",
        "sbdb_spkid",
        "sbdb_designation",
        "sbdb_fullname",
        "sbdb_orbit_class_name",
    ]
    for col in expected_cols:
        assert col in df.columns, f"Column '{col}' missing from threat watchlist."


def test_watchlist_grain_uniqueness(provider: DashboardDataProvider):
    """Verify grain is strictly (closest_approach_date, neows_id) with no duplicates."""
    df = provider.get_threat_watchlist()
    grain_keys = list(zip(df["closest_approach_date"], df["neows_id"]))
    assert len(grain_keys) == len(set(grain_keys)), "Duplicate (closest_approach_date, neows_id) in watchlist."


def test_watchlist_no_synthetic_velocity(provider: DashboardDataProvider):
    """Verify watchlist does not fabricate relative velocity when absent from NeoWs data."""
    df = provider.get_threat_watchlist()
    # asteroids.parquet has no velocity column; verify it is not manufactured in watchlist
    assert "relative_velocity_km_s" not in df.columns
    assert "velocity_km_s" not in df.columns


# ============================================================================
# B. IDENTIFIER SEPARATION & NAMESPACE INTEGRITY
# ============================================================================


def test_identifier_separation_neows_vs_spkid(provider: DashboardDataProvider):
    """Verify NeoWs ID is strictly isolated from SBDB SPK-ID."""
    # (2010 TW54) has NeoWs ID '3548666' and SBDB SPK-ID '50548689'
    df = provider.get_threat_watchlist()
    tw54_row = df[df["name"].str.contains("2010 TW54", na=False)]
    if not tw54_row.empty:
        neows_id = tw54_row.iloc[0]["neows_id"]
        assert neows_id == "3548666"
        assert neows_id != "50548689", "NeoWs ID must not be conflated with SBDB SPK-ID!"


def test_asteroid_key_uuid5_format(provider: DashboardDataProvider):
    """Verify resolved asteroid_key adheres strictly to platform UUID5 format."""
    # ast_8520aaac-9c77-5e8f-9a88-b4e501749e26
    profile = provider.get_sbdb_profile("ast_8520aaac-9c77-5e8f-9a88-b4e501749e26")
    assert profile is not None
    key = profile["asteroid_key"]
    assert UUID5_PATTERN.match(key), f"asteroid_key '{key}' does not match UUID5 pattern."


def test_crosswalk_namespace_separation(provider: DashboardDataProvider):
    """Verify crosswalk table separates source systems into distinct namespaces."""
    df = provider.get_crosswalk("ast_8520aaac-9c77-5e8f-9a88-b4e501749e26")
    assert not df.empty
    assert "source_system" in df.columns
    assert "identifier_name" in df.columns
    assert "identifier_value" in df.columns

    systems = set(df["source_system"].unique())
    assert "sbdb" in systems
    assert "sentry" in systems

    # SBDB SPKID is marked as primary pivot
    spkid_row = df[(df["source_system"] == "sbdb") & (df["identifier_name"] == "spkid")]
    assert not spkid_row.empty
    assert bool(spkid_row.iloc[0]["is_primary_pivot"]) is True


# ============================================================================
# C. ENTITY RESOLUTION STATES (RESOLVED, UNRESOLVED, AMBIGUOUS, INVALID)
# ============================================================================


def test_resolution_state_unresolved(provider: DashboardDataProvider):
    """Verify unmapped NeoWs targets return UNRESOLVED state with None key."""
    res = provider.get_resolution_state("9999999")
    assert res["match_state"] == "UNRESOLVED"
    assert res["asteroid_key"] is None
    assert "neows_id" in res
    assert res["neows_id"] == "9999999"


def test_resolution_state_resolved_2010_tw54(provider: DashboardDataProvider):
    """Verify resolved NeoWs target 3548666 (2010 TW54) returns RESOLVED with canonical key."""
    res = provider.get_resolution_state("3548666")
    assert res["match_state"] == "RESOLVED"
    assert res["asteroid_key"] == "ast_b8259bf1-e6e5-5059-853e-9434274cdf2c"
    assert res["neows_id"] == "3548666"
    assert res["match_rule"] == "EXACT_DESIGNATION_MATCH"


def test_resolution_state_resolved_2008_st(provider: DashboardDataProvider):
    """Verify resolved NeoWs target 3427460 (2008 ST) returns RESOLVED with canonical key."""
    res = provider.get_resolution_state("3427460")
    assert res["match_state"] == "RESOLVED"
    assert res["asteroid_key"] == "ast_8520aaac-9c77-5e8f-9a88-b4e501749e26"
    assert res["neows_id"] == "3427460"
    assert res["match_rule"] == "EXACT_DESIGNATION_MATCH"


def test_resolution_state_invalid_inputs(provider: DashboardDataProvider):
    """Verify invalid or malformed NeoWs IDs return INVALID state."""
    # None
    res1 = provider.get_resolution_state(None)
    assert res1["match_state"] == "INVALID"
    assert res1["asteroid_key"] is None

    # Empty string
    res2 = provider.get_resolution_state("   ")
    assert res2["match_state"] == "INVALID"
    assert res2["asteroid_key"] is None

    # Non-numeric string
    res3 = provider.get_resolution_state("invalid_id_123")
    assert res3["match_state"] == "INVALID"
    assert res3["asteroid_key"] is None


def test_resolution_state_ambiguous_synthetic(tmp_path: Path):
    """Verify ambiguous multiple-key bridge mappings return AMBIGUOUS with None key."""
    # Create synthetic bridge fixture with 2 conflicting keys for same NeoWs ID
    bridge_df = pd.DataFrame(
        [
            {
                "asteroid_key": "ast_11111111-1111-5111-8111-111111111111",
                "source_system": "neows",
                "identifier_name": "id",
                "identifier_value": "9999999",
                "is_primary_pivot": False,
                "created_at": "2026-09-27T00:00:00Z",
                "updated_at": "2026-09-27T00:00:00Z",
            },
            {
                "asteroid_key": "ast_22222222-2222-5222-8222-222222222222",
                "source_system": "neows",
                "identifier_name": "id",
                "identifier_value": "9999999",
                "is_primary_pivot": False,
                "created_at": "2026-09-27T00:00:00Z",
                "updated_at": "2026-09-27T00:00:00Z",
            },
        ]
    )
    bridge_path = tmp_path / "bridge_asteroid_identifier.parquet"
    bridge_df.to_parquet(bridge_path)

    # Instantiate local provider pointed at tmp_path
    local_p = LocalDuckDBDataProvider(base_dir=tmp_path)
    res = local_p.get_resolution_state("9999999")
    assert res["match_state"] == "AMBIGUOUS"
    assert res["asteroid_key"] is None, "Ambiguous resolution must not arbitrarily choose an asteroid_key!"


def test_resolution_state_resolved_synthetic(tmp_path: Path):
    """Verify deterministic single-key bridge mapping returns RESOLVED."""
    bridge_df = pd.DataFrame(
        [
            {
                "asteroid_key": "ast_33333333-3333-5333-8333-333333333333",
                "source_system": "neows",
                "identifier_name": "id",
                "identifier_value": "8888888",
                "is_primary_pivot": False,
                "created_at": "2026-09-27T00:00:00Z",
                "updated_at": "2026-09-27T00:00:00Z",
            }
        ]
    )
    bridge_df.to_parquet(tmp_path / "bridge_asteroid_identifier.parquet")

    local_p = LocalDuckDBDataProvider(base_dir=tmp_path)
    res = local_p.get_resolution_state("8888888")
    assert res["match_state"] == "RESOLVED"
    assert res["asteroid_key"] == "ast_33333333-3333-5333-8333-333333333333"


def test_resolution_state_authoritative_rule_priority_over_bridge(tmp_path: Path):
    """Regression test: verify fact_entity_resolution rule takes priority over bridge fallback."""
    # Create fact_entity_resolution fixture
    res_df = pd.DataFrame(
        [
            {
                "resolution_run_id": "run_1",
                "resolved_at": "2026-09-28T01:15:26.842071+00:00",
                "source_system": "neows",
                "identifier_name": "id",
                "source_identifier_value": "7777777",
                "matched_target_system": "sbdb",
                "matched_target_identifier_name": "des",
                "matched_target_identifier_value": "2026 TEST",
                "assigned_asteroid_key": "ast_77777777-7777-5777-8777-777777777777",
                "match_state": "RESOLVED",
                "match_rule": "EXACT_DESIGNATION_MATCH",
                "evidence_json": '{"rule": "EXACT_DESIGNATION_MATCH"}',
            }
        ]
    )
    res_df.to_parquet(tmp_path / "fact_entity_resolution.parquet")

    # Create bridge fixture mapping same identifier
    bridge_df = pd.DataFrame(
        [
            {
                "asteroid_key": "ast_77777777-7777-5777-8777-777777777777",
                "source_system": "neows",
                "identifier_name": "id",
                "identifier_value": "7777777",
                "is_primary_pivot": False,
                "created_at": "2026-09-28T01:15:26.842071+00:00",
                "updated_at": "2026-09-28T01:15:26.842071+00:00",
            }
        ]
    )
    bridge_df.to_parquet(tmp_path / "bridge_asteroid_identifier.parquet")

    local_p = LocalDuckDBDataProvider(base_dir=tmp_path)
    res = local_p.get_resolution_state("7777777")
    assert res["match_state"] == "RESOLVED"
    assert res["asteroid_key"] == "ast_77777777-7777-5777-8777-777777777777"
    # MUST return authoritative audit rule, NEVER synthetic BRIDGE_EXACT_NEOWS_ID
    assert res["match_rule"] == "EXACT_DESIGNATION_MATCH"
    assert res["match_rule"] != "BRIDGE_EXACT_NEOWS_ID"
    assert "EXACT_DESIGNATION_MATCH" in res["evidence"]


# ============================================================================
# D. NULL SAFETY & MISSINGNESS
# ============================================================================


def test_sbdb_profile_null_key(provider: DashboardDataProvider):
    """Verify get_sbdb_profile returns None when key is missing or unmapped."""
    assert provider.get_sbdb_profile(None) is None
    assert provider.get_sbdb_profile("") is None
    assert provider.get_sbdb_profile("non_existent_key") is None


def test_sbdb_profile_physical_null_preservation(provider: DashboardDataProvider):
    """Verify physical parameters preserve None when not present in source catalog."""
    # 2008 ST has H magnitude, but diameter and albedo are null in source snapshot
    profile = provider.get_sbdb_profile("ast_8520aaac-9c77-5e8f-9a88-b4e501749e26")
    assert profile is not None
    assert profile["absolute_magnitude"] == 27.1
    assert profile["estimated_diameter_km"] is None, "Missing diameter must be None, not 0.0!"
    assert profile["albedo"] is None, "Missing albedo must be None, not 0.0!"


def test_sentry_profile_unmonitored_key(provider: DashboardDataProvider):
    """Verify unmonitored entity returns clean inactive structure without raising."""
    assert provider.get_sentry_profile(None) is None
    assert provider.get_sentry_profile("") is None

    # Unmonitored key returns inactive structure
    profile = provider.get_sentry_profile("non_existent_key")
    assert profile is not None
    assert profile["has_sentry_monitoring"] is False
    assert profile["is_sentry_ambiguous"] is False
    assert profile["sentry_id"] is None
    assert profile["latest_impact_probability"] is None


def test_historical_risk_empty_id(provider: DashboardDataProvider):
    """Verify get_historical_risk returns empty DataFrame for None or missing sentry_id."""
    df1 = provider.get_historical_risk(None)
    assert isinstance(df1, pd.DataFrame)
    assert df1.empty
    assert "impact_probability" in df1.columns

    df2 = provider.get_historical_risk("non_existent_sentry_id_xyz")
    assert isinstance(df2, pd.DataFrame)
    assert df2.empty


# ============================================================================
# E. SENTRY CARDINALITY & AMBIGUITY DEFENSE
# ============================================================================


def test_sentry_ambiguity_defense(tmp_path: Path):
    """Verify multiple Sentry IDs linked to 1 asteroid_key suppress scalar metrics."""
    # Create synthetic bridge with 2 Sentry IDs for 1 asteroid key
    bridge_df = pd.DataFrame(
        [
            {
                "asteroid_key": "ast_44444444-4444-5444-8444-444444444444",
                "source_system": "sentry",
                "identifier_name": "sentry_id",
                "identifier_value": "sentry_obj_1",
                "is_primary_pivot": False,
                "created_at": "2026-09-27T00:00:00Z",
                "updated_at": "2026-09-27T00:00:00Z",
            },
            {
                "asteroid_key": "ast_44444444-4444-5444-8444-444444444444",
                "source_system": "sentry",
                "identifier_name": "sentry_id",
                "identifier_value": "sentry_obj_2",
                "is_primary_pivot": False,
                "created_at": "2026-09-27T00:00:00Z",
                "updated_at": "2026-09-27T00:00:00Z",
            },
        ]
    )
    bridge_df.to_parquet(tmp_path / "bridge_asteroid_identifier.parquet")

    local_p = LocalDuckDBDataProvider(base_dir=tmp_path)
    profile = local_p.get_sentry_profile("ast_44444444-4444-5444-8444-444444444444")
    assert profile is not None
    assert profile["has_sentry_monitoring"] is True
    assert profile["is_sentry_ambiguous"] is True
    assert profile["sentry_identifier_count"] == 2
    # All scalar risk metrics MUST be None when ambiguous!
    assert profile["sentry_id"] is None
    assert profile["latest_impact_probability"] is None
    assert profile["latest_palermo_scale_max"] is None


def test_sentry_single_id_resolution(provider: DashboardDataProvider):
    """Verify exactly one Sentry ID populates verified scalar risk metrics."""
    # ast_8520aaac-9c77-5e8f-9a88-b4e501749e26 maps to exactly 1 Sentry ID: bK08S00T
    profile = provider.get_sentry_profile("ast_8520aaac-9c77-5e8f-9a88-b4e501749e26")
    assert profile is not None
    assert profile["has_sentry_monitoring"] is True
    assert profile["is_sentry_ambiguous"] is False
    assert profile["sentry_identifier_count"] == 1
    assert profile["sentry_id"] == "bK08S00T"
    assert profile["latest_impact_probability"] is not None
    assert profile["latest_impact_probability"] > 0
    assert profile["latest_palermo_scale_max"] is not None
    assert profile["v_infinity_km_s"] is not None


# ============================================================================
# F. HISTORICAL RISK GRAIN & SNAPSHOT SEQUENCING
# ============================================================================


def test_historical_risk_grain_and_change_flags(tmp_path: Path):
    """Verify historical risk grain (snapshot_key, sentry_id) and non-causal change flags."""
    # Create multi-snapshot risk dataset for 1 sentry_id
    snap_df = pd.DataFrame(
        [
            {
                "snapshot_key": "2026-09-10",
                "run_id": "r1",
                "snapshot_time": "2026-09-10T00:00:00Z",
                "sentry_id": "test_sentry_obj",
                "designation": "2026 TS1",
                "fullname": "(2026 TS1)",
                "absolute_magnitude": 22.1,
                "estimated_diameter_km": 0.12,
                "impact_probability": 1.0e-4,
                "potential_impacts_count": 5,
                "palermo_scale_cum": -2.8,
                "palermo_scale_max": -3.1,
                "torino_scale_max": 0,
                "v_infinity_km_s": 14.5,
                "impact_year_range": "2045-2080",
                "last_obs_date": "2026-09-09",
                "last_obs_jd": 2461000.5,
            },
            {
                "snapshot_key": "2026-09-18",
                "run_id": "r2",
                "snapshot_time": "2026-09-18T00:00:00Z",
                "sentry_id": "test_sentry_obj",
                "designation": "2026 TS1",
                "fullname": "(2026 TS1)",
                "absolute_magnitude": 22.1,
                "estimated_diameter_km": 0.12,
                "impact_probability": 2.5e-5,  # Changed
                "potential_impacts_count": 3,
                "palermo_scale_cum": -3.2,
                "palermo_scale_max": -3.1,  # Unchanged
                "torino_scale_max": 0,
                "v_infinity_km_s": 14.5,
                "impact_year_range": "2045-2080",
                "last_obs_date": "2026-09-17",
                "last_obs_jd": 2461008.5,
            },
        ]
    )
    snap_df.to_parquet(tmp_path / "fact_sentry_risk_snapshot.parquet")

    local_p = LocalDuckDBDataProvider(base_dir=tmp_path)
    hist = local_p.get_historical_risk("test_sentry_obj")
    assert len(hist) == 2

    # First row has no previous row -> change flags False
    assert bool(hist.iloc[0]["is_impact_probability_changed"]) is False
    assert bool(hist.iloc[0]["is_palermo_scale_max_changed"]) is False

    # Second row has changed IP -> is_impact_probability_changed is True
    assert bool(hist.iloc[1]["is_impact_probability_changed"]) is True
    # Second row has identical palermo -> is_palermo_scale_max_changed is False
    assert bool(hist.iloc[1]["is_palermo_scale_max_changed"]) is False


# ============================================================================
# G. SCIENTIFIC SAFETY & PROHIBITED TERMS
# ============================================================================


def test_scientific_safety_no_prohibited_terms():
    """Verify data access layer contains zero synthetic risk score terms."""
    src_path = PROJ_DIR / "dashboard_data.py"
    with open(src_path, encoding="utf-8") as f:
        code = f.read().lower()

    prohibited = [
        "danger_score",
        "danger score",
        "threat_index",
        "threat index",
        "lethality",
        "became safer",
        "became more dangerous",
        "orbit refinement",  # Must not claim causal mechanisms in code
    ]
    for term in prohibited:
        assert term not in code, f"Prohibited unscientific term '{term}' found in dashboard_data.py!"


# ============================================================================
# H. ATHENA PROVIDER NOTIMPLEMENTED GUARD
# ============================================================================


def test_athena_provider_offline_guard():
    """Verify Athena provider cleanly raises NotImplementedError during offline step."""
    athena_p = DashboardDataProvider(execution_mode="ATHENA")
    assert "ATHENA" in athena_p.get_execution_mode()

    with pytest.raises(NotImplementedError):
        athena_p.get_threat_watchlist()

    with pytest.raises(NotImplementedError):
        athena_p.get_resolution_state("3548666")
