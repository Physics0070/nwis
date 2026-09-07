"""Load processed datasets into the NWIS database.

This is the step that turns the offline pipelines into a running product. Everything the
API serves originates here; nothing is generated in the frontend.

Sources:
    FORCE  -> wells, formation intervals, decimated log samples, lithology predictions
    Volve  -> wells, trajectory stations, telemetry samples, drilling events
    NPD    -> real surface coordinates (Volve WITSML has none)
    registry -> model versions and their measured metrics

Coordinates are transformed from projected UTM to WGS84. A well whose position cannot be
established keeps NULL coordinates and is reported as unmappable — never placed at a guess.

Usage:
    python -m data_pipeline.load_database
    python -m data_pipeline.load_database --reset
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
from sqlalchemy import delete, select

from backend.app.core.database import get_capabilities, get_engine, session_scope
from backend.app.models import (
    Base,
    DrillingEvent,
    FormationInterval,
    ModelVersion,
    TelemetrySample,
    TrajectoryStation,
    Well,
    WellLogSample,
)
from backend.app.models.types import point_wkt
from nwis_common import get_config, get_logger

log = get_logger("nwis.load")


# ------------------------------------------------------------------- coordinates


def utm_to_wgs84(easting: pd.Series, northing: pd.Series, source_crs: str, target_crs: str):
    """Transform projected coordinates to latitude/longitude."""
    from pyproj import Transformer

    transformer = Transformer.from_crs(source_crs, target_crs, always_xy=True)
    longitude, latitude = transformer.transform(easting.to_numpy(), northing.to_numpy())
    return pd.Series(latitude, index=easting.index), pd.Series(longitude, index=easting.index)


def load_npd_positions(config) -> pd.DataFrame:
    """Official wellbore surface positions, transformed to WGS84."""
    raw_dir = config.path("datasets.npd.raw_dir")
    name_col = config.get("datasets.npd.name_column")
    north_col = config.get("datasets.npd.northing_column")
    east_col = config.get("datasets.npd.easting_column")
    source_crs = config.get("datasets.npd.source_crs")
    target_crs = config.get("force_pipeline.target_crs")

    frames = []
    for filename in ("wellbore_development_all.csv", "wellbore_exploration_all.csv"):
        path = raw_dir / filename
        if path.exists():
            frames.append(pd.read_csv(path, low_memory=False))
    if not frames:
        log.warning("npd_positions_unavailable",
                    impact="Volve wells will have no surface position")
        return pd.DataFrame()

    frame = pd.concat(frames, ignore_index=True)
    frame = frame.dropna(subset=[name_col, north_col, east_col])
    latitude, longitude = utm_to_wgs84(frame[east_col], frame[north_col], source_crs, target_crs)
    result = pd.DataFrame(
        {
            "wellbore_name": frame[name_col].astype(str).str.strip(),
            "latitude": latitude,
            "longitude": longitude,
            "field": frame.get("wlbField"),
            "operator": frame.get("wlbDrillingOperator"),
            "kb_elevation_m": frame.get("wlbKellyBushElevation"),
            "total_depth_md_m": frame.get("wlbTotalDepth"),
        }
    ).drop_duplicates(subset=["wellbore_name"])
    log.info("npd_positions_loaded", wellbores=len(result))
    return result


# ------------------------------------------------------------------------ FORCE


def _interval_runs(frame: pd.DataFrame, depth_col: str, value_cols: list[str]):
    """Collapse per-sample stratigraphy into contiguous intervals."""
    working = frame[[depth_col, *value_cols]].copy()
    key = working[value_cols].astype(str).agg("|".join, axis=1)
    change = (key != key.shift()).cumsum()
    grouped = working.groupby(change)
    for _, block in grouped:
        first = block.iloc[0]
        yield {
            "depth_top_m": float(block[depth_col].min()),
            "depth_base_m": float(block[depth_col].max()),
            "sample_count": int(len(block)),
            **{col: (None if pd.isna(first[col]) else str(first[col])) for col in value_cols},
        }


def ingest_force(session, config, npd: pd.DataFrame) -> dict:
    processed = config.path("paths.data_processed") / "force"
    features_path = processed / "features.parquet"
    if not features_path.exists():
        log.warning("force_features_missing", path=str(features_path))
        return {"wells": 0}

    well_col = config.get("force_pipeline.well_column")
    depth_col = config.get("force_pipeline.depth_column")
    group_col = config.get("force_pipeline.group_column")
    formation_col = config.get("force_pipeline.formation_column")
    target_col = config.get("force_pipeline.target_column")
    source_crs = config.get("force_pipeline.source_crs")
    target_crs = config.get("force_pipeline.target_crs")
    depth_step = float(config.get("ingestion.log_depth_step_m"))
    log_curves = list(config.get("ingestion.log_curves"))

    columns = [well_col, depth_col, target_col, "X_LOC", "Y_LOC", "Z_LOC",
               group_col, formation_col, *log_curves]
    frame = pd.read_parquet(features_path, columns=[c for c in columns])
    log.info("force_loaded", rows=len(frame))

    stats = {"wells": 0, "formation_intervals": 0, "log_samples": 0, "unmapped": []}

    for well_name, block in frame.groupby(well_col, sort=True):
        block = block.sort_values(depth_col)

        # Surface position: median of the well's own coordinates, transformed to WGS84.
        latitude = longitude = None
        position_source = None
        coords = block[["X_LOC", "Y_LOC"]].dropna()
        if not coords.empty:
            lat_series, lon_series = utm_to_wgs84(
                pd.Series([coords["X_LOC"].median()]),
                pd.Series([coords["Y_LOC"].median()]),
                source_crs,
                target_crs,
            )
            latitude, longitude = float(lat_series.iloc[0]), float(lon_series.iloc[0])
            position_source = f"FORCE X_LOC/Y_LOC ({source_crs} -> {target_crs})"
        else:
            stats["unmapped"].append(str(well_name))

        well = Well(
            name=str(well_name),
            reference=str(well_name),
            field_name=None,
            country="Norway",
            region="North Sea",
            latitude=latitude,
            longitude=longitude,
            location=point_wkt(longitude, latitude) if latitude is not None else None,
            location_source=position_source,
            total_depth_md_m=float(block[depth_col].max()),
            has_logs=True,
            has_telemetry=False,
            has_trajectory=False,
            is_active=False,
            status="historical",
            source_dataset="FORCE 2020",
            source_reference=str(features_path.name),
        )
        session.add(well)
        session.flush()
        stats["wells"] += 1

        # Stratigraphic intervals
        strat = block[[depth_col, group_col, formation_col]].dropna(
            subset=[group_col, formation_col], how="all"
        )
        if not strat.empty:
            for run in _interval_runs(strat, depth_col, [group_col, formation_col]):
                session.add(
                    FormationInterval(
                        well_id=well.id,
                        group_name=run.get(group_col),
                        formation_name=run.get(formation_col),
                        depth_top_m=run["depth_top_m"],
                        depth_base_m=run["depth_base_m"],
                        sample_count=run["sample_count"],
                        source_dataset="FORCE 2020",
                    )
                )
                stats["formation_intervals"] += 1

        # Decimated log samples. Full resolution stays in parquet for training.
        decimated = block[(block[depth_col] % depth_step).abs() < 0.08]
        for _, row in decimated.iterrows():
            curves = {
                curve: (None if pd.isna(row[curve]) else round(float(row[curve]), 4))
                for curve in log_curves
                if curve in row.index
            }
            session.add(
                WellLogSample(
                    well_id=well.id,
                    depth_md_m=float(row[depth_col]),
                    tvd_m=None if pd.isna(row.get("Z_LOC")) else abs(float(row["Z_LOC"])),
                    curves=curves,
                )
            )
            stats["log_samples"] += 1

        session.flush()

    log.info("force_ingested", **{k: v if not isinstance(v, list) else len(v)
                                  for k, v in stats.items()})
    return stats


# ------------------------------------------------------------------------ Volve


def ingest_volve(session, config, npd: pd.DataFrame) -> dict:
    processed = config.path("paths.data_processed") / "volve"
    wells_path = processed / "wells.parquet"
    if not wells_path.exists():
        log.warning("volve_wells_missing", path=str(wells_path))
        return {"wells": 0}

    wells = pd.read_parquet(wells_path)
    telemetry = pd.read_parquet(processed / "telemetry.parquet") \
        if (processed / "telemetry.parquet").exists() else pd.DataFrame()
    trajectory = pd.read_parquet(processed / "trajectory.parquet") \
        if (processed / "trajectory.parquet").exists() else pd.DataFrame()
    messages = pd.read_parquet(processed / "messages.parquet") \
        if (processed / "messages.parquet").exists() else pd.DataFrame()

    channel_columns = [
        c for c in telemetry.columns
        if c not in {"well_name", "timestamp", "bit_depth_m", "hole_depth_m",
                     "circulating", "rotating", "tripping", "operations_active"}
        and pd.api.types.is_numeric_dtype(telemetry[c])
    ] if not telemetry.empty else []

    positions = (
        npd.set_index("wellbore_name") if not npd.empty else pd.DataFrame()
    )
    stats = {"wells": 0, "telemetry": 0, "trajectory": 0, "events": 0, "unmapped": []}

    for _, record in wells.iterrows():
        well_name = str(record["well_name"])
        lookup = well_name.replace("NO ", "").strip()

        latitude = longitude = None
        position_source = None
        if not positions.empty and lookup in positions.index:
            row = positions.loc[lookup]
            latitude = float(row["latitude"])
            longitude = float(row["longitude"])
            position_source = "NPD/Sodir factpages (EPSG:23031 -> EPSG:4326)"
        else:
            stats["unmapped"].append(well_name)

        well = Well(
            name=well_name,
            reference=lookup,
            field_name=record.get("field"),
            country=record.get("country"),
            region=record.get("region"),
            operator=record.get("operator"),
            latitude=latitude,
            longitude=longitude,
            location=point_wkt(longitude, latitude) if latitude is not None else None,
            location_source=position_source,
            water_depth_m=_maybe_float(record.get("water_depth_m")),
            kb_elevation_m=_maybe_float(record.get("kb_elevation_m")),
            total_depth_md_m=_maybe_float(record.get("max_bit_depth_m")),
            has_logs=False,
            has_telemetry=True,
            has_trajectory=not trajectory.empty,
            is_active=False,
            status="historical",
            source_dataset="Volve WITSML",
            source_reference=str(record.get("well_reference")),
        )
        session.add(well)
        session.flush()
        stats["wells"] += 1

        if not trajectory.empty:
            stations = trajectory[trajectory["well_name"] == well_name]
            seen: set[float] = set()
            for _, station in stations.iterrows():
                md = _maybe_float(station.get("md_m"))
                if md is None or md in seen:
                    continue
                seen.add(md)
                session.add(
                    TrajectoryStation(
                        well_id=well.id,
                        md_m=md,
                        tvd_m=_maybe_float(station.get("tvd_m")),
                        inclination_deg=_maybe_float(station.get("inclination_deg")),
                        azimuth_deg=_maybe_float(station.get("azimuth_deg")),
                        north_offset_m=_maybe_float(station.get("north_offset_m")),
                        east_offset_m=_maybe_float(station.get("east_offset_m")),
                        dogleg_severity_deg_per_m=_maybe_float(
                            station.get("dogleg_severity_deg_per_m")
                        ),
                        station_type=station.get("station_type"),
                    )
                )
                stats["trajectory"] += 1

        if not telemetry.empty:
            samples = telemetry[telemetry["well_name"] == well_name]
            rows = []
            for record_tuple in samples.itertuples(index=False):
                values = record_tuple._asdict()
                channels = {
                    name: _round_or_none(values.get(name)) for name in channel_columns
                }
                rows.append(
                    {
                        "well_id": well.id,
                        "recorded_at": values["timestamp"].to_pydatetime(),
                        "bit_depth_m": _round_or_none(values.get("bit_depth_m")),
                        "hole_depth_m": _round_or_none(values.get("hole_depth_m")),
                        "channels": {k: v for k, v in channels.items() if v is not None},
                        "circulating": bool(values.get("circulating", False)),
                        "rotating": bool(values.get("rotating", False)),
                        "tripping": bool(values.get("tripping", False)),
                        "operations_active": bool(values.get("operations_active", False)),
                    }
                )
            if rows:
                session.bulk_insert_mappings(TelemetrySample, rows)
                stats["telemetry"] += len(rows)

        if not messages.empty:
            well_messages = messages[messages["well_name"] == well_name]
            for _, message in well_messages.iterrows():
                depth = _maybe_float(message.get("md_recovered_m"))
                session.add(
                    DrillingEvent(
                        well_id=well.id,
                        occurred_at=_maybe_datetime(message.get("timestamp")),
                        event_type="operational_remark",
                        category=None,
                        depth_start_m=depth,
                        depth_end_m=depth,
                        depth_source=message.get("depth_source"),
                        description=str(message.get("text") or "").strip(),
                        raw_text=str(message.get("text") or "").strip(),
                        extraction_method="witsml_message",
                        source_dataset="Volve WITSML",
                        source_reference=str(message.get("source_file")),
                    )
                )
                stats["events"] += 1

        session.flush()

    log.info("volve_ingested", **{k: v if not isinstance(v, list) else len(v)
                                  for k, v in stats.items()})
    return stats


# ------------------------------------------------------------------ model registry


def ingest_models(session, config) -> dict:
    from ml.common.registry import ModelRegistry

    registry = ModelRegistry(config.get("paths.models"))
    records = registry.list_models()
    count = 0
    for record in records:
        session.add(
            ModelVersion(
                name=record["name"],
                version=record["version"],
                task=record["task"],
                algorithm=record["algorithm"],
                dataset=record.get("dataset"),
                dataset_rows=record.get("dataset_rows"),
                trained_at=_maybe_datetime(record.get("trained_at")),
                training_seconds=record.get("training_seconds"),
                is_active=True,
                feature_columns=record.get("feature_columns", []),
                hyperparameters=record.get("hyperparameters", {}),
                metrics=record.get("metrics", {}),
                split_summary=record.get("split_summary", {}),
                limitations=record.get("limitations", []),
                artifact_path=record.get("artifact_path"),
            )
        )
        count += 1
    log.info("models_ingested", count=count)
    return {"models": count}


# ---------------------------------------------------------------------- helpers


def _maybe_float(value):
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return None if np.isnan(result) else result


def _round_or_none(value, digits: int = 4):
    result = _maybe_float(value)
    return None if result is None else round(result, digits)


def _maybe_datetime(value):
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    if isinstance(value, datetime):
        return value
    try:
        parsed = pd.to_datetime(value, utc=True, errors="coerce")
    except Exception:
        return None
    return None if pd.isna(parsed) else parsed.to_pydatetime()


def main() -> int:
    parser = argparse.ArgumentParser(description="Load NWIS datasets into the database")
    parser.add_argument("--reset", action="store_true",
                        help="drop and recreate all tables before loading")
    args = parser.parse_args()

    config = get_config()
    engine = get_engine()
    capabilities = get_capabilities()
    log.info("database_target", **capabilities.as_dict())

    if args.reset:
        log.warning("schema_reset", note="dropping all NWIS tables")
        Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    npd = load_npd_positions(config)
    summary: dict = {}

    with session_scope() as session:
        existing = session.execute(select(Well)).scalars().first()
        if existing is not None and not args.reset:
            log.error("database_not_empty",
                      hint="run with --reset to reload from scratch")
            return 1

        summary["force"] = ingest_force(session, config, npd)
        summary["volve"] = ingest_volve(session, config, npd)
        summary["models"] = ingest_models(session, config)
        session.commit()

    unmapped = summary["force"].get("unmapped", []) + summary["volve"].get("unmapped", [])
    log.info(
        "load_complete",
        wells=summary["force"]["wells"] + summary["volve"]["wells"],
        formation_intervals=summary["force"].get("formation_intervals"),
        log_samples=summary["force"].get("log_samples"),
        telemetry_samples=summary["volve"].get("telemetry"),
        trajectory_stations=summary["volve"].get("trajectory"),
        events=summary["volve"].get("events"),
        models=summary["models"]["models"],
        wells_without_position=len(unmapped),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
