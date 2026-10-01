"""Pydantic v2 response and entity schemas for M6 API.

Adheres strictly to the locked M6.2 contract:
- Root envelope with 'meta' and 'data'
- Strict field validation (extra='forbid')
- Deterministic UTC timestamps
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class MetaEnvelope(BaseModel):
    """Standard metadata envelope present on every API response."""

    model_config = ConfigDict(extra="forbid")

    api_version: str = Field(
        default="1.0.0",
        description="Authoritative API contract version",
    )
    execution_mode: str = Field(
        ...,
        description="Active backend execution mode reported by provider",
    )
    timestamp: str = Field(
        ...,
        description="Timezone-aware UTC ISO-8601 timestamp of response generation",
    )


class HealthChecks(BaseModel):
    """Readiness status of required subsystems."""

    model_config = ConfigDict(extra="forbid")

    lakehouse_storage: bool = Field(
        ...,
        description="True if all required core Parquet assets exist on disk",
    )
    query_engine: bool = Field(
        ...,
        description="True if DuckDB query engine can execute basic queries",
    )


class HealthData(BaseModel):
    """Payload for readiness probe /health."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["healthy", "unavailable"] = Field(
        ...,
        description="Overall readiness: 'healthy' (HTTP 200) or 'unavailable' (HTTP 503)",
    )
    checks: HealthChecks = Field(
        ...,
        description="Detailed subsystem check results",
    )


class HealthResponse(BaseModel):
    """Standard response model for GET /health."""

    model_config = ConfigDict(extra="forbid")

    meta: MetaEnvelope = Field(
        ...,
        description="Response metadata envelope",
    )
    data: HealthData = Field(
        ...,
        description="Health readiness data payload",
    )


class PaginationEnvelope(BaseModel):
    """Pagination metadata envelope."""

    model_config = ConfigDict(extra="forbid")

    total: int = Field(
        ...,
        ge=0,
        description="Total matching records before pagination",
    )
    limit: int = Field(
        ...,
        ge=1,
        le=500,
        description="Requested page limit",
    )
    offset: int = Field(
        ...,
        ge=0,
        description="Requested page offset",
    )
    returned: int = Field(
        ...,
        ge=0,
        description="Count of records returned in this page",
    )


class WatchlistAsteroid(BaseModel):
    """Encounter event row from the close-approach threat watchlist.

    Grain: (closest_approach_date, neows_id).
    Strict 21-column schema preserving genuine scientific nulls.
    """

    model_config = ConfigDict(extra="forbid")

    closest_approach_date: str = Field(..., description="Date of closest approach (YYYY-MM-DD)")
    neows_id: str = Field(..., description="NeoWs asteroid identifier")
    name: str = Field(..., description="Primary asteroid name or designation")
    miss_distance_km: float = Field(..., description="Miss distance in kilometers")
    miss_distance_lunar: float = Field(..., description="Miss distance in lunar distances (LD)")
    hazardous: bool = Field(..., description="Potentially hazardous asteroid flag")
    asteroid_key: str | None = Field(default=None, description="Global UUID5 identifier if resolved")
    match_state: str = Field(..., description="Entity resolution match state")
    is_sentry_monitored: bool = Field(..., description="True if actively monitored by JPL Sentry")
    is_sentry_ambiguous: bool = Field(..., description="True if reverse resolution to Sentry is ambiguous")
    sentry_id: str | None = Field(default=None, description="Sentry object designation if resolved")
    sentry_impact_probability: float | None = Field(default=None, description="Cumulative impact probability")
    sentry_palermo_scale_max: float | None = Field(default=None, description="Maximum Palermo Technical Scale value")
    sentry_torino_scale_max: int | None = Field(default=None, description="Maximum Torino Scale value")
    sentry_potential_impacts_count: int | None = Field(default=None, description="Number of potential impact solutions")
    sentry_impact_year_range: str | None = Field(default=None, description="Impact year span (e.g. 2088-2122)")
    has_sbdb_characterization: bool = Field(..., description="True if characterized by JPL SBDB")
    sbdb_spkid: str | None = Field(default=None, description="SBDB SPK-ID if linked")
    sbdb_designation: str | None = Field(default=None, description="SBDB official designation")
    sbdb_fullname: str | None = Field(default=None, description="SBDB full name string")
    sbdb_orbit_class_name: str | None = Field(default=None, description="SBDB orbit class name")


class WatchlistQueryParams(BaseModel):
    """Validated query parameters for GET /asteroids."""

    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=50, ge=1, le=500, description="Maximum number of records to return")
    offset: int = Field(default=0, ge=0, description="Offset index for pagination")
    hazardous: bool | None = Field(default=None, description="Filter by hazardous status")
    sentry_monitored: bool | None = Field(default=None, description="Filter by Sentry monitoring status")
    horizon_mkm: float | None = Field(default=None, gt=0, description="Filter by maximum miss distance in millions of km")


class WatchlistResponse(BaseModel):
    """Authoritative response model for GET /asteroids."""

    model_config = ConfigDict(extra="forbid")

    meta: MetaEnvelope = Field(..., description="Standard response metadata envelope")
    data: list[WatchlistAsteroid] = Field(..., description="List of encounter event records")
    pagination: PaginationEnvelope = Field(..., description="Pagination metadata")


class AsteroidDetail(BaseModel):
    """Primary encounter and metadata for a single NeoWs asteroid.

    Grain: (neows_id).
    Primary encounter selected via CLOSEST_OBSERVED_APPROACH.
    """

    model_config = ConfigDict(extra="forbid")

    neows_id: str = Field(..., description="NeoWs asteroid identifier")
    name: str = Field(..., description="Primary asteroid name or designation")
    closest_approach_date: str = Field(..., description="Date of closest observed approach (YYYY-MM-DD)")
    miss_distance_km: float = Field(..., description="Miss distance in kilometers for closest approach")
    miss_distance_lunar: float = Field(..., description="Miss distance in lunar distances (LD)")
    hazardous: bool = Field(..., description="Potentially hazardous asteroid flag")
    approaches_recorded_count: int = Field(..., ge=1, description="Number of observed close approach encounters recorded")
    selection_rule: str = Field(default="CLOSEST_OBSERVED_APPROACH", description="Rule used to select the primary encounter")
    asteroid_key: str | None = Field(default=None, description="Global UUID5 identifier if resolved")
    match_state: str = Field(..., description="Entity resolution match state")
    is_sentry_monitored: bool = Field(..., description="True if actively monitored by JPL Sentry")
    is_sentry_ambiguous: bool = Field(..., description="True if reverse resolution to Sentry is ambiguous")
    sentry_id: str | None = Field(default=None, description="Sentry object designation if resolved")
    has_sbdb_characterization: bool = Field(..., description="True if characterized by JPL SBDB")
    sbdb_spkid: str | None = Field(default=None, description="SBDB SPK-ID if linked")
    sbdb_designation: str | None = Field(default=None, description="SBDB official designation")
    sbdb_fullname: str | None = Field(default=None, description="SBDB full name string")
    sbdb_orbit_class_name: str | None = Field(default=None, description="SBDB orbit class name")


class ResolutionEnvelope(BaseModel):
    """Entity resolution status and evidence block."""

    model_config = ConfigDict(extra="forbid")

    match_state: str = Field(..., description="Identity resolution state: RESOLVED, UNRESOLVED, AMBIGUOUS")
    asteroid_key: str | None = Field(default=None, description="Global UUID5 identifier if resolved")
    match_rule: str | None = Field(default=None, description="Resolution rule applied by M3 entity resolution")
    evidence: str | None = Field(default=None, description="Supporting resolution evidence")
    resolved_at: str | None = Field(default=None, description="Timestamp of resolution execution")


class AsteroidDetailResponse(BaseModel):
    """Authoritative response envelope for GET /asteroids/{neows_id}."""

    model_config = ConfigDict(extra="forbid")

    meta: MetaEnvelope = Field(..., description="Standard response metadata envelope")
    data: AsteroidDetail = Field(..., description="Primary encounter object dossier")
    resolution: ResolutionEnvelope = Field(..., description="Entity resolution state and evidence")


class ErrorDetail(BaseModel):
    """Machine-readable and human-readable error payload."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(..., description="Machine-readable error code")
    message: str = Field(..., description="Human-readable error description")


class ErrorResponse(BaseModel):
    """Standard error response envelope."""

    model_config = ConfigDict(extra="forbid")

    meta: MetaEnvelope = Field(..., description="Standard response metadata envelope")
    error: ErrorDetail = Field(..., description="Error payload")


class SbdbProfile(BaseModel):
    """Authoritative JPL SBDB physical and orbital characterization.

    Grain: (spkid) resolved via canonical asteroid_key.
    Preserves descriptive quality tier and genuine scientific nulls.
    """

    model_config = ConfigDict(extra="forbid")

    spkid: str = Field(..., description="JPL SBDB SPK-ID")
    asteroid_key: str = Field(..., description="Canonical internal entity UUID5 identifier")
    designation: str | None = Field(default=None, description="Official small-body designation")
    fullname: str | None = Field(default=None, description="Full object name string from SBDB")
    shortname: str | None = Field(default=None, description="Short name or abbreviation")
    object_kind: str | None = Field(default=None, description="Small-body kind code (e.g. au for asteroid)")
    is_neo: bool | None = Field(..., description="SBDB NEO flag as published; null when the source snapshot does not report it")
    is_pha: bool | None = Field(..., description="SBDB PHA flag as published; null when the source snapshot does not report it")
    orbit_class_code: str | None = Field(default=None, description="Three-letter orbit class code (e.g. APO, AMO)")
    orbit_class_name: str | None = Field(default=None, description="Full orbit class name")
    snapshot_key: str | None = Field(default=None, description="SBDB snapshot partition key (YYYY-MM-DD) every field in this profile was read from")
    run_id: str | None = Field(default=None, description="SBDB ingestion run ID every field in this profile was read from")
    snapshot_time: str | None = Field(default=None, description="UTC ISO timestamp of the selected SBDB snapshot run")
    orbit_id: str | None = Field(default=None, description="Orbit solution identifier")
    epoch_jd: float | None = Field(default=None, description="Orbit epoch in Julian Days (TDB)")
    equinox: str | None = Field(default=None, description="Reference frame equinox of the orbital elements (e.g. J2000)")
    soln_date: str | None = Field(default=None, description="Orbit solution calculation timestamp")
    orbit_source: str | None = Field(default=None, description="Orbit solution provider/source")
    producer: str | None = Field(default=None, description="Orbit computer / producer name")
    first_obs: str | None = Field(default=None, description="Date of first observation used in orbit fit")
    last_obs: str | None = Field(default=None, description="Date of last observation used in orbit fit")
    data_arc_days: int | None = Field(default=None, description="Observation arc length in days")
    n_obs_used: int | None = Field(default=None, description="Number of observations used in orbit determination")
    condition_code: str | None = Field(default=None, description="Orbit condition code / U parameter")
    rms: float | None = Field(default=None, description="Normalized RMS residual of orbit fit")
    earth_moid_au: float | None = Field(default=None, description="Earth Minimum Orbit Intersection Distance in AU")
    jupiter_moid_au: float | None = Field(default=None, description="Jupiter Minimum Orbit Intersection Distance in AU")
    t_jup: float | None = Field(default=None, description="Tisserand parameter with respect to Jupiter")
    eccentricity: float | None = Field(default=None, description="Orbital eccentricity (e)")
    semi_major_axis_au: float | None = Field(default=None, description="Semi-major axis in AU (a)")
    perihelion_distance_au: float | None = Field(default=None, description="Perihelion distance in AU (q)")
    aphelion_distance_au: float | None = Field(default=None, description="Aphelion distance in AU (SBDB 'ad', label Q)")
    inclination_deg: float | None = Field(default=None, description="Orbital inclination in degrees (i)")
    ascending_node_longitude_deg: float | None = Field(default=None, description="Longitude of the ascending node in degrees (SBDB 'om', Ω)")
    argument_of_perihelion_deg: float | None = Field(default=None, description="Argument of perihelion in degrees (SBDB 'w', ω)")
    mean_anomaly_deg: float | None = Field(default=None, description="Mean anomaly at epoch in degrees (SBDB 'ma', M)")
    mean_motion_deg_per_day: float | None = Field(default=None, description="Mean motion in degrees per day (SBDB 'n')")
    orbital_period_days: float | None = Field(default=None, description="Sidereal orbital period in days, as published by SBDB ('per')")
    time_of_perihelion_jd_tdb: float | None = Field(default=None, description="Time of perihelion passage as a Julian Date in TDB (SBDB 'tp')")
    orbital_period_yr: float | None = Field(
        default=None,
        deprecated="Derived value; use orbital_period_days (SBDB source).",
        description="DEPRECATED, DERIVED: orbital_period_days / 365.25 (Julian years). Not a source value; retained for compatibility.",
    )
    estimated_diameter_km: float | None = Field(default=None, description="Estimated physical diameter in kilometers")
    absolute_magnitude: float | None = Field(default=None, description="Absolute visual magnitude (H)")
    albedo: float | None = Field(default=None, description="Geometric albedo")
    rotational_period_hr: float | None = Field(default=None, description="Rotational period in hours")
    astrometric_data_quality_tier: str = Field(..., description="Astrometric data quality tier classification")


class SbdbResponse(BaseModel):
    """Authoritative response envelope for GET /asteroids/{neows_id}/sbdb."""

    model_config = ConfigDict(extra="forbid")

    meta: MetaEnvelope = Field(..., description="Standard response metadata envelope")
    data: SbdbProfile | None = Field(default=None, description="SBDB physical and orbital profile payload")
    resolution: ResolutionEnvelope = Field(..., description="Entity resolution state and evidence")


class SentryProfile(BaseModel):
    """Authoritative NASA/JPL Sentry Mode S impact risk monitoring profile.

    Grain: (asteroid_key) with reverse-cardinality defense.
    All fields reflect factual provider attributes; zero synthetic risk scores.
    """

    model_config = ConfigDict(extra="forbid")

    asteroid_key: str = Field(..., description="Canonical internal entity UUID5 identifier")
    has_sentry_monitoring: bool = Field(..., description="True if asteroid has Sentry monitoring records")
    is_sentry_ambiguous: bool = Field(..., description="True if multiple Sentry IDs map to this entity")
    sentry_identifier_count: int = Field(..., description="Count of distinct Sentry IDs linked to this entity")
    sentry_id: str | None = Field(default=None, description="NASA/JPL Sentry object identifier")
    designation: str | None = Field(default=None, description="Official small-body designation in Sentry")
    fullname: str | None = Field(default=None, description="Full object name string in Sentry")
    latest_impact_probability: float | None = Field(default=None, description="Latest computed cumulative impact probability")
    latest_palermo_scale_max: float | None = Field(default=None, description="Latest maximum Palermo technical scale value")
    latest_palermo_scale_cum: float | None = Field(default=None, description="Latest cumulative Palermo technical scale value")
    latest_torino_scale_max: int | None = Field(default=None, description="Latest maximum Torino scale integer value (0-10)")
    latest_potential_impacts_count: int | None = Field(default=None, description="Count of potential future Earth impact solutions")
    v_infinity_km_s: float | None = Field(default=None, description="Relative velocity at infinity in km/s")
    impact_year_range: str | None = Field(default=None, description="Calendar year range of potential impact solutions (e.g. 2088-2122)")
    last_obs_date: str | None = Field(default=None, description="Observation date of last data used in Sentry solution")
    latest_snapshot_key: str | None = Field(default=None, description="Snapshot partition date key of latest record")
    total_snapshots_observed: int = Field(..., description="Total count of distinct Sentry snapshots recorded")
    all_time_max_impact_probability: float | None = Field(default=None, description="All-time maximum impact probability across snapshots")
    all_time_max_palermo_scale_max: float | None = Field(default=None, description="All-time maximum Palermo scale across snapshots")
    all_time_max_torino_scale_max: int | None = Field(default=None, description="All-time maximum Torino scale across snapshots")
    is_currently_active: bool = Field(..., description="True if object appears in latest global Sentry snapshot")


class SentryResponse(BaseModel):
    """Authoritative response envelope for GET /asteroids/{neows_id}/sentry."""

    model_config = ConfigDict(extra="forbid")

    meta: MetaEnvelope = Field(..., description="Standard response metadata envelope")
    data: SentryProfile | None = Field(default=None, description="Sentry impact risk monitoring profile payload")
    resolution: ResolutionEnvelope = Field(..., description="Entity resolution state and evidence")


class SentryHistoryRecord(BaseModel):
    """Authoritative historical risk snapshot record for a JPL Sentry object.

    Grain: (snapshot_key, sentry_id).
    Strict 16-field schema preserving non-causal change flags and genuine scientific nulls.
    Zero synthetic risk, threat, or safety scores.
    """

    model_config = ConfigDict(extra="forbid")

    snapshot_key: str = Field(..., description="Snapshot partition date key (YYYY-MM-DD)")
    snapshot_time: str = Field(..., description="UTC ISO timestamp of the catalog snapshot run")
    sentry_id: str = Field(..., description="NASA/JPL Sentry object identifier")
    designation: str | None = Field(default=None, description="Official small-body designation in Sentry")
    impact_probability: float | None = Field(default=None, description="Computed cumulative impact probability")
    palermo_scale_max: float | None = Field(default=None, description="Maximum Palermo Technical Scale value")
    palermo_scale_cum: float | None = Field(default=None, description="Cumulative Palermo Technical Scale value")
    torino_scale_max: int | None = Field(default=None, description="Maximum Torino Scale integer value (0-10)")
    potential_impacts_count: int | None = Field(default=None, description="Count of potential future Earth impact solutions")
    v_infinity_km_s: float | None = Field(default=None, description="Relative velocity at infinity in km/s")
    estimated_diameter_km: float | None = Field(default=None, description="Estimated physical diameter in kilometers")
    absolute_magnitude: float | None = Field(default=None, description="Absolute visual magnitude (H)")
    impact_year_range: str | None = Field(default=None, description="Calendar year span of potential impacts (e.g. 2088-2122)")
    last_obs_date: str | None = Field(default=None, description="Date of last astrometric observation used in orbit fit")
    is_impact_probability_changed: bool = Field(..., description="Non-causal flag indicating if impact probability changed from previous snapshot")
    is_palermo_scale_max_changed: bool = Field(..., description="Non-causal flag indicating if maximum Palermo scale changed from previous snapshot")


class HistoryResolutionEnvelope(ResolutionEnvelope):
    """Entity resolution status and ambiguity metadata for GET /asteroids/{neows_id}/history."""

    model_config = ConfigDict(extra="forbid")

    is_sentry_ambiguous: bool | None = Field(default=None, description="True if reverse resolution to Sentry is ambiguous")
    warning: str | None = Field(default=None, description="Warning note regarding resolution or linkage ambiguity")
    notes: str | None = Field(default=None, description="Operational notes regarding resolution or linkage ambiguity")


class SentryHistoryResponse(BaseModel):
    """Authoritative response envelope for GET /asteroids/{neows_id}/history."""

    model_config = ConfigDict(extra="forbid")

    meta: MetaEnvelope = Field(..., description="Standard response metadata envelope")
    data: list[SentryHistoryRecord] | None = Field(default=None, description="List of historical risk snapshot records or null")
    resolution: HistoryResolutionEnvelope = Field(..., description="Entity resolution state and ambiguity metadata")


class CrosswalkRecord(BaseModel):
    """Authoritative identifier mapping record from bridge_asteroid_identifier.

    Grain: (asteroid_key, source_system, identifier_name, identifier_value).
    All fields reflect factual multi-source mappings; zero synthetic identities.
    """

    model_config = ConfigDict(extra="forbid")

    asteroid_key: str = Field(..., description="Canonical internal entity UUID5 identifier")
    source_system: str = Field(..., description="Origin source system (neows, sbdb, sentry)")
    identifier_name: str = Field(..., description="System identifier attribute name (id, spkid, des, sentry_id)")
    identifier_value: str = Field(..., description="Actual identifier value string in the source system")
    is_primary_pivot: bool | None = Field(default=None, description="True if this identifier served as primary resolution anchor")
    created_at: str | None = Field(default=None, description="UTC ISO timestamp of mapping creation")
    updated_at: str | None = Field(default=None, description="UTC ISO timestamp of mapping update")


class CrosswalkResponse(BaseModel):
    """Authoritative response envelope for GET /asteroids/{neows_id}/crosswalk."""

    model_config = ConfigDict(extra="forbid")

    meta: MetaEnvelope = Field(..., description="Standard response metadata envelope")
    data: list[CrosswalkRecord] = Field(default_factory=list, description="List of multi-source identifier mapping records")
    resolution: ResolutionEnvelope = Field(..., description="Entity resolution state and evidence")


# ============================================================================
# WORLD SNAPSHOT CONTRACT — GET /asteroids/world
# ============================================================================


class WorldEncounter(BaseModel):
    """NeoWs close-approach facts for the selected encounter. Source: NASA NeoWs only."""

    model_config = ConfigDict(extra="forbid")

    source: Literal["nasa_neows"] = Field(default="nasa_neows", description="Source system for every field in this block")
    closest_approach_date: str = Field(..., description="Date of closest observed approach (YYYY-MM-DD)")
    miss_distance_km: float = Field(..., description="Real NeoWs miss distance in kilometers (source value, not normalized)")
    is_potentially_hazardous: bool | None = Field(
        ..., description="NeoWs potentially-hazardous flag; null if not reported. Independent of Sentry availability."
    )


class WorldResolution(BaseModel):
    """Cross-source identity resolution state (same rules as GET /asteroids/{neows_id})."""

    model_config = ConfigDict(extra="forbid")

    match_state: Literal["RESOLVED", "UNRESOLVED", "AMBIGUOUS", "INVALID"] = Field(..., description="Identity resolution state")
    match_rule: str | None = Field(default=None, description="Resolution rule applied")
    resolved_at: str | None = Field(default=None, description="Timestamp of the resolution run, if recorded")


class WorldSbdbAvailability(BaseModel):
    """Whether a JPL SBDB snapshot exists for this identity, with its provenance."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["available", "not_resolved", "not_present"] = Field(
        ...,
        description=(
            "available: an SBDB snapshot exists; not_resolved: identity not resolved, so SBDB cannot be linked; "
            "not_present: identity resolved but no SBDB snapshot is stored"
        ),
    )
    spkid: str | None = Field(default=None, description="SBDB SPK-ID linked through the crosswalk")
    snapshot_key: str | None = Field(default=None, description="Snapshot key of the SBDB snapshot served by /sbdb")
    run_id: str | None = Field(default=None, description="Ingestion run ID of that SBDB snapshot")


class WorldSentryAvailability(BaseModel):
    """Whether a Sentry record exists via crosswalk membership. Never inferred from the PHA flag."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["available", "not_resolved", "not_present", "ambiguous", "linked_no_record"] = Field(
        ...,
        description=(
            "available: one linked Sentry ID with stored snapshots; not_resolved: identity not resolved; "
            "not_present: resolved but no Sentry link; ambiguous: multiple Sentry IDs linked; "
            "linked_no_record: one Sentry ID linked but no stored snapshot"
        ),
    )
    sentry_id: str | None = Field(default=None, description="Sentry object identifier when exactly one is linked")
    latest_snapshot_key: str | None = Field(default=None, description="Snapshot key of the latest stored Sentry record")
    run_id: str | None = Field(default=None, description="Ingestion run ID of that Sentry record")
    in_latest_catalog: bool | None = Field(
        default=None,
        description="True if that record is in the latest stored Sentry catalog snapshot; null when no record",
    )


class IllustrativeDirection(BaseModel):
    """Deterministic ILLUSTRATIVE unit vector for placing the object around Earth.

    Not an astronomical direction: NeoWs publishes no 3D direction. Derived only from neows_id.
    """

    model_config = ConfigDict(extra="forbid")

    x: float = Field(..., ge=-1.0, le=1.0, description="Unit vector x component (illustrative)")
    y: float = Field(..., ge=-1.0, le=1.0, description="Unit vector y component (illustrative)")
    z: float = Field(..., ge=-1.0, le=1.0, description="Unit vector z component (illustrative)")


class WorldAsteroid(BaseModel):
    """One NeoWs object in the world snapshot. Grain: (neows_id)."""

    model_config = ConfigDict(extra="forbid")

    neows_id: str = Field(..., description="NeoWs asteroid identifier")
    name: str = Field(..., description="NeoWs name / designation")
    asteroid_key: str | None = Field(default=None, description="Canonical UUID5 key; null unless resolved")
    encounter: WorldEncounter
    resolution: WorldResolution
    sbdb: WorldSbdbAvailability
    sentry: WorldSentryAvailability
    illustrative_direction: IllustrativeDirection


class WorldSpatialModel(BaseModel):
    """Declares how the world's spatial values may be interpreted."""

    model_config = ConfigDict(extra="forbid")

    direction_semantics: Literal["illustrative"] = Field(
        default="illustrative", description="Directions are illustrative, not astronomical"
    )
    direction_algorithm: str = Field(..., description="Versioned identifier of the deterministic direction algorithm")
    direction_seed_field: Literal["neows_id"] = Field(default="neows_id", description="Only input to the direction algorithm")
    distance_field: Literal["encounter.miss_distance_km"] = Field(
        default="encounter.miss_distance_km", description="Real source distance; any visual scaling is the renderer's"
    )
    note: str = Field(..., description="Human-readable interpretation note")


class WorldSnapshotInfo(BaseModel):
    """Snapshot-level metadata shared by every world record."""

    model_config = ConfigDict(extra="forbid")

    object_count: int = Field(..., ge=0, description="Number of NeoWs objects in this world snapshot")
    encounter_selection_rule: Literal["CLOSEST_OBSERVED_APPROACH"] = Field(
        default="CLOSEST_OBSERVED_APPROACH", description="Rule used to pick one encounter per NeoWs object"
    )
    neows_run_id: str | None = Field(
        default=None, description="NeoWs ingestion run ID from dataset metadata; null if the dataset does not record one"
    )
    sentry_latest_catalog_snapshot_key: str | None = Field(
        default=None, description="Latest stored Sentry catalog snapshot key; null if no Sentry data is stored"
    )
    spatial_model: WorldSpatialModel


class WorldResponse(BaseModel):
    """Authoritative response envelope for GET /asteroids/world."""

    model_config = ConfigDict(extra="forbid")

    meta: MetaEnvelope = Field(..., description="Standard response metadata envelope")
    world: WorldSnapshotInfo = Field(..., description="World snapshot metadata")
    data: list[WorldAsteroid] = Field(..., description="One record per NeoWs object")


# ============================================================================
# ASTEROID PROFILE CONTRACT — GET /asteroids/{neows_id}/profile
# ============================================================================

UnavailableReason = Literal["not_resolved", "not_in_source", "not_in_current_contract", "ambiguous_linkage"]


class SectionAvailability(BaseModel):
    """Machine-readable availability of a profile section. Unavailable fields are null, never defaulted."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["available", "partial", "unavailable"] = Field(
        ..., description="available: every field has a source value; partial: some are null; unavailable: all are null"
    )
    unavailable: dict[str, UnavailableReason] = Field(
        default_factory=dict,
        description=(
            "Null fields and why: not_resolved (identity not established, so the source cannot be linked), "
            "not_in_source (linked source has no value), not_in_current_contract (not ingested by the current pipeline), "
            "ambiguous_linkage (several source records are linked, so none is attributed)"
        ),
    )


class ProfileIdentity(BaseModel):
    """Who this object is across sources. Resolution follows the canonical serving-layer rules."""

    model_config = ConfigDict(extra="forbid")

    neows_id: str = Field(..., description="NeoWs asteroid identifier")
    name: str = Field(..., description="NeoWs name / designation")
    match_state: Literal["RESOLVED", "UNRESOLVED", "AMBIGUOUS", "INVALID"] = Field(..., description="Identity resolution state")
    asteroid_key: str | None = Field(default=None, description="Canonical UUID5 key; null unless resolved")
    sbdb_spkid: str | None = Field(default=None, description="JPL SBDB SPK-ID linked through the crosswalk")
    sbdb_designation: str | None = Field(default=None, description="Designation as published by SBDB")
    sbdb_fullname: str | None = Field(default=None, description="Full name as published by SBDB")
    sentry_id: str | None = Field(default=None, description="Sentry object ID when exactly one is linked")
    crosswalk: list[CrosswalkRecord] = Field(default_factory=list, description="All identifiers mapped to asteroid_key")
    availability: SectionAvailability


class ProfileOrbit(BaseModel):
    """Osculating orbit from ONE coherent SBDB snapshot (see provenance.sbdb). Source: JPL SBDB."""

    model_config = ConfigDict(extra="forbid")

    source: Literal["jpl_sbdb"] = "jpl_sbdb"
    orbit_class_code: str | None = Field(default=None, description="SBDB orbit class code (e.g. APO)")
    orbit_class_name: str | None = Field(default=None, description="SBDB orbit class name")
    is_neo: bool | None = Field(default=None, description="SBDB NEO flag; null when not reported")
    is_pha: bool | None = Field(default=None, description="SBDB PHA flag; null when not reported. Distinct from the NeoWs flag")
    orbit_id: str | None = Field(default=None, description="Orbit solution identifier")
    epoch_jd: float | None = Field(default=None, description="Osculating epoch, Julian Date (TDB)")
    equinox: str | None = Field(default=None, description="Reference frame equinox (e.g. J2000)")
    semi_major_axis_au: float | None = Field(default=None, description="a, au")
    eccentricity: float | None = Field(default=None, description="e, unitless")
    perihelion_distance_au: float | None = Field(default=None, description="q, au")
    aphelion_distance_au: float | None = Field(default=None, description="Q (SBDB 'ad'), au")
    inclination_deg: float | None = Field(default=None, description="i, deg")
    ascending_node_longitude_deg: float | None = Field(default=None, description="Ω (SBDB 'om'), deg")
    argument_of_perihelion_deg: float | None = Field(default=None, description="ω (SBDB 'w'), deg")
    mean_anomaly_deg: float | None = Field(default=None, description="M (SBDB 'ma'), deg")
    mean_motion_deg_per_day: float | None = Field(default=None, description="n, deg/d")
    orbital_period_days: float | None = Field(default=None, description="Sidereal period as published by SBDB ('per'), days")
    time_of_perihelion_jd_tdb: float | None = Field(default=None, description="tp, Julian Date (TDB)")
    soln_date: str | None = Field(default=None, description="Orbit solution date")
    first_obs: str | None = Field(default=None, description="First observation used in the fit")
    last_obs: str | None = Field(default=None, description="Last observation used in the fit")
    data_arc_days: int | None = Field(default=None, description="Observation arc, days")
    n_obs_used: int | None = Field(default=None, description="Observations used in the fit")
    condition_code: str | None = Field(default=None, description="Orbit condition code (U)")
    rms: float | None = Field(default=None, description="Normalized RMS residual of the fit")
    earth_moid_au: float | None = Field(default=None, description="Earth MOID, au")
    jupiter_moid_au: float | None = Field(default=None, description="Jupiter MOID, au")
    t_jup: float | None = Field(default=None, description="Tisserand parameter with respect to Jupiter")
    availability: SectionAvailability


class ProfilePhysical(BaseModel):
    """Physical parameters from the same SBDB snapshot as the orbit. Source: JPL SBDB."""

    model_config = ConfigDict(extra="forbid")

    source: Literal["jpl_sbdb"] = "jpl_sbdb"
    absolute_magnitude: float | None = Field(default=None, description="H")
    estimated_diameter_km: float | None = Field(default=None, description="SBDB 'diameter', km")
    albedo: float | None = Field(default=None, description="SBDB geometric albedo")
    rotational_period_hr: float | None = Field(default=None, description="SBDB 'rot_per', hours")
    availability: SectionAvailability


class ProfileEncounter(BaseModel):
    """Close-approach facts for the selected encounter. Source: NASA NeoWs only."""

    model_config = ConfigDict(extra="forbid")

    source: Literal["nasa_neows"] = "nasa_neows"
    selection_rule: Literal["CLOSEST_OBSERVED_APPROACH"] = "CLOSEST_OBSERVED_APPROACH"
    closest_approach_date: str = Field(..., description="Date of closest observed approach (YYYY-MM-DD)")
    miss_distance_km: float = Field(..., description="Miss distance, km (source value)")
    is_potentially_hazardous: bool | None = Field(
        default=None, description="NeoWs PHA flag; null when not reported. Independent of Sentry linkage"
    )
    availability: SectionAvailability


class SentryAssessment(BaseModel):
    """Values JPL Sentry published for this object, from ONE Mode S summary record (see provenance.sentry).

    Mode S is the per-object catalog summary. It carries no individual impact solutions,
    impact dates or impact energies (those are Mode O, which is not ingested). Nothing here is
    computed by this platform: there is no derived risk or danger score.
    """

    model_config = ConfigDict(extra="forbid")

    designation: str | None = Field(default=None, description="Designation as published by Sentry ('des')")
    fullname: str | None = Field(default=None, description="Full name as published by Sentry ('fullname')")
    impact_probability: float | None = Field(
        default=None, description="Cumulative impact probability over all listed potential impacts ('ip')"
    )
    potential_impacts_count: int | None = Field(default=None, description="Number of potential impacts listed ('n_imp')")
    impact_year_range: str | None = Field(default=None, description="Year range of the listed potential impacts ('range')")
    palermo_scale_cum: float | None = Field(default=None, description="Cumulative Palermo Technical Scale ('ps_cum')")
    palermo_scale_max: float | None = Field(default=None, description="Maximum Palermo Technical Scale ('ps_max')")
    torino_scale_max: int | None = Field(default=None, description="Maximum Torino Scale ('ts_max')")
    v_infinity_km_s: float | None = Field(default=None, description="Velocity relative to Earth at infinity, km/s ('v_inf')")
    absolute_magnitude: float | None = Field(
        default=None, description="H used by Sentry ('h'); Sentry's value, distinct from physical.absolute_magnitude (SBDB)"
    )
    estimated_diameter_km: float | None = Field(
        default=None, description="Diameter estimate used by Sentry, km ('diameter'); not an SBDB measurement"
    )
    last_obs_date: str | None = Field(default=None, description="Date of the last observation used ('last_obs')")
    last_obs_jd: float | None = Field(default=None, description="Julian Date of the last observation used ('last_obs_jd')")
    availability: SectionAvailability


class ProfileSentryLinkage(BaseModel):
    """Sentry linkage via the crosswalk, plus the published Mode S assessment when exactly one record is linked."""

    model_config = ConfigDict(extra="forbid")

    source: Literal["jpl_sentry"] = "jpl_sentry"
    source_contract: Literal["sentry_mode_s_summary"] = Field(
        default="sentry_mode_s_summary", description="Sentry API mode ingested: per-object summary only (no Mode O detail)"
    )
    status: Literal["available", "not_resolved", "not_present", "ambiguous", "linked_no_record"] = Field(
        ..., description="Same statuses as the world snapshot's sentry block"
    )
    sentry_id: str | None = Field(default=None, description="Sentry object ID when exactly one is linked")
    in_latest_catalog: bool | None = Field(
        default=None,
        description=(
            "True if the assessment's record is in the latest stored Sentry catalog snapshot. False means the "
            "object is absent from that catalog and the assessment is its last stored record. Null when no record"
        ),
    )
    assessment: SentryAssessment
    assessment_endpoint: str | None = Field(
        default=None, description="Route serving the legacy Sentry profile (incl. all-time aggregates), when linked"
    )


class NeowsProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["nasa_neows"] = "nasa_neows"
    dataset_run_id: str | None = Field(default=None, description="NeoWs ingestion run ID from dataset metadata; null if not recorded")


class ResolutionProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["entity_resolution"] = "entity_resolution"
    match_rule: str | None = Field(default=None, description="Resolution rule applied")
    resolved_at: str | None = Field(default=None, description="Timestamp of the resolution run, if recorded")


class SbdbProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["jpl_sbdb"] = "jpl_sbdb"
    spkid: str | None = Field(default=None, description="SPK-ID the orbit and physical sections were read for")
    snapshot_key: str | None = Field(default=None, description="The single SBDB snapshot both sections came from")
    run_id: str | None = Field(default=None, description="Ingestion run of that snapshot")
    snapshot_time: str | None = Field(default=None, description="UTC timestamp of that snapshot run")


class SentryProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["jpl_sentry"] = "jpl_sentry"
    sentry_id: str | None = Field(default=None, description="Linked Sentry object ID")
    latest_snapshot_key: str | None = Field(default=None, description="Snapshot key of the record the assessment came from")
    run_id: str | None = Field(default=None, description="Ingestion run of that record")
    snapshot_time: str | None = Field(default=None, description="UTC timestamp of that record's snapshot run")
    latest_catalog_snapshot_key: str | None = Field(
        default=None, description="Latest stored Sentry catalog snapshot key overall; null if no Sentry data is stored"
    )


class ProfileProvenance(BaseModel):
    """Where every section came from, with snapshot/run metadata where the data records it."""

    model_config = ConfigDict(extra="forbid")

    neows: NeowsProvenance
    resolution: ResolutionProvenance
    sbdb: SbdbProvenance
    sentry: SentryProvenance


class AsteroidProfile(BaseModel):
    """Single coherent cross-source profile for one NeoWs object. Grain: (neows_id)."""

    model_config = ConfigDict(extra="forbid")

    neows_id: str = Field(..., description="NeoWs asteroid identifier")
    identity: ProfileIdentity
    orbit: ProfileOrbit
    physical: ProfilePhysical
    encounter: ProfileEncounter
    sentry: ProfileSentryLinkage
    provenance: ProfileProvenance


class AsteroidProfileResponse(BaseModel):
    """Authoritative response envelope for GET /asteroids/{neows_id}/profile."""

    model_config = ConfigDict(extra="forbid")

    meta: MetaEnvelope = Field(..., description="Standard response metadata envelope")
    data: AsteroidProfile = Field(..., description="Cross-source asteroid profile")
