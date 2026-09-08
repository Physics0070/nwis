"""Volve WITSML -> normalised drilling telemetry.

Stages: raw XML -> parsed channels -> unit-normalised -> quality-flagged -> resampled.

Design notes:

* Channel names come from ``volve_pipeline.channel_map`` and are resolved against what
  each log actually contains. A missing mnemonic yields a null column, never a crash.
* Units are converted once, here, from the strict SI that WITSML publishes into units a
  driller reads (kN, bar, rpm, L/min, m/h, degC).
* Zero is a legitimate operational state in this dataset (pumps off, string static), so
  zeros are never treated as nulls. An ``operations_active`` mask is derived instead.
* Implausible values are counted and flagged, not deleted.

Outputs:
    data/processed/volve/telemetry.parquet    normalised, resampled telemetry
    data/processed/volve/trajectory.parquet   directional surveys
    data/processed/volve/messages.parquet     operational remarks with recovered depth
    data/processed/volve/wells.parquet        well headers
    data/processed/volve/quality_report.json  every cleaning decision, counted

Usage:
    python -m data_pipeline.volve.prepare
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
import pandas as pd

from data_pipeline.common.units import convert_series
from data_pipeline.volve import witsml
from nwis_common import citation_path, get_config, get_logger
from nwis_common.paths import ensure_dir

log = get_logger("nwis.volve.prepare")


def resolve_channels(available: set[str], channel_map: dict[str, list[str]]) -> dict[str, str]:
    """Map canonical channel -> the first configured mnemonic actually present."""
    resolved: dict[str, str] = {}
    for canonical, candidates in channel_map.items():
        for mnemonic in candidates:
            if mnemonic in available:
                resolved[canonical] = mnemonic
                break
    return resolved


def read_well_telemetry(well_dir: Path, config) -> tuple[pd.DataFrame, dict]:
    """Read and normalise every drilling log under one well directory."""
    channel_map = config.section("volve_pipeline.channel_map")
    preferred = list(config.get("volve_pipeline.preferred_log_names"))
    index_type = config.get("volve_pipeline.index_type")
    sentinels = [float(v) for v in config.get("data_quality.null_sentinels")]
    convert = bool(config.get("volve_pipeline.unit_conversions.enabled"))

    frames: list[pd.DataFrame] = []
    report = {
        "logs_read": 0,
        "logs_skipped_by_name": 0,
        "rows_raw": 0,
        "channels_resolved": {},
        "units_converted": {},
        "missing_channels": [],
    }

    for log_path in witsml.find_objects(well_dir, "log"):
        log_object = witsml.read_log(log_path)
        if log_object is None or log_object.data.empty:
            continue
        if log_object.name not in preferred:
            report["logs_skipped_by_name"] += 1
            continue
        if log_object.index_type != index_type:
            continue

        data = log_object.data
        available = set(data.columns)
        resolved = resolve_channels(available, channel_map)
        if not resolved:
            continue

        index_curve = log_object.index_curve or "Time"
        if index_curve not in data.columns:
            continue

        units = {c.mnemonic: c.unit for c in log_object.channels}
        out = pd.DataFrame(
            {"timestamp": pd.to_datetime(data[index_curve], errors="coerce", utc=True)}
        )

        for canonical, mnemonic in resolved.items():
            values = pd.to_numeric(data[mnemonic], errors="coerce")
            if log_object.null_value:
                try:
                    values = values.replace(float(log_object.null_value), np.nan)
                except ValueError:
                    pass
            for sentinel in sentinels:
                values = values.replace(sentinel, np.nan)

            unit = units.get(mnemonic)
            if convert:
                values, canonical_unit, applied = convert_series(values, unit)
                if applied:
                    report["units_converted"][canonical] = f"{unit} -> {canonical_unit}"
            out[canonical] = values

        out["source_log"] = log_object.name
        out["source_file"] = citation_path(log_path)
        frames.append(out)
        report["logs_read"] += 1
        report["rows_raw"] += len(out)
        report["channels_resolved"] = {
            **report["channels_resolved"],
            **{k: v for k, v in resolved.items()},
        }

    if not frames:
        return pd.DataFrame(), report

    telemetry = pd.concat(frames, ignore_index=True)
    telemetry = telemetry.dropna(subset=["timestamp"]).sort_values("timestamp")
    report["missing_channels"] = sorted(set(channel_map) - set(report["channels_resolved"]))
    return telemetry, report


def flag_quality(frame: pd.DataFrame, config) -> tuple[pd.DataFrame, dict]:
    """Flag physically implausible values. Counts them; deletes nothing."""
    ranges = config.section("volve_pipeline.plausible_ranges")
    flags: dict[str, int] = {}
    for channel, (low, high) in ranges.items():
        if channel not in frame.columns:
            continue
        outside = frame[channel].notna() & (
            (frame[channel] < float(low)) | (frame[channel] > float(high))
        )
        count = int(outside.sum())
        if count:
            flags[channel] = count
            frame.loc[outside, f"{channel}_implausible"] = True
            # Excluded from modelling but retained in the record for audit.
            frame.loc[outside, channel] = np.nan
    return frame, {"implausible_values_flagged": flags}


def find_constant_channels(frame: pd.DataFrame, tolerance: float) -> list[str]:
    """Channels whose values never vary carry no information and must be reported.

    In this mirror the `Depth` channel is a constant equal to wellbore total depth, so
    anything derived from it (an on-bottom test, for instance) would be meaningless.
    """
    constant = []
    for column in frame.select_dtypes(include=[np.number]).columns:
        values = frame[column].dropna()
        if len(values) > 1 and float(values.max() - values.min()) <= tolerance:
            constant.append(column)
    return constant


def derive_activity(frame: pd.DataFrame, config) -> tuple[pd.DataFrame, dict]:
    """Derive rig-state flags from signals that genuinely vary.

    Zero flow or zero RPM is a valid operational state, not missing data, so activity is
    expressed as explicit flags rather than by discarding rows.

    An on-bottom test is deliberately NOT derived: `hole_depth_m` is a constant in this
    dataset, so any such flag would be an artefact rather than a measurement.
    """
    cfg = config.section("volve_pipeline.activity")
    min_flow = float(cfg["min_flow_in_lpm"])
    min_rpm = float(cfg["min_bit_rpm"])
    min_trip_speed = float(cfg["min_trip_speed_m_per_min"])
    tolerance = float(cfg["constant_channel_tolerance"])

    constant_channels = find_constant_channels(frame, tolerance)

    frame["circulating"] = (
        frame["flow_in_lpm"] > min_flow
        if "flow_in_lpm" in frame.columns
        else pd.Series(False, index=frame.index)
    )
    frame["rotating"] = (
        frame["bit_rpm"] > min_rpm
        if "bit_rpm" in frame.columns
        else pd.Series(False, index=frame.index)
    )

    # String movement, from the rate of change of bit depth in metres per minute.
    if "bit_depth_m" in frame.columns and "timestamp" in frame.columns:
        elapsed_min = frame["timestamp"].diff().dt.total_seconds() / 60.0
        speed = (frame["bit_depth_m"].diff() / elapsed_min.replace(0.0, np.nan)).abs()
        frame["trip_speed_m_per_min"] = speed
        frame["tripping"] = speed > min_trip_speed
    else:
        frame["tripping"] = pd.Series(False, index=frame.index)

    active_states = list(cfg["active_states"])
    mask = pd.Series(False, index=frame.index)
    for flag in active_states:
        if flag in frame.columns:
            mask |= frame[flag].fillna(False)
    frame["operations_active"] = mask

    return frame, {
        "constant_channels": constant_channels,
        "active_states": active_states,
        "state_counts": {
            state: int(frame[state].sum())
            for state in ("circulating", "rotating", "tripping")
            if state in frame.columns
        },
    }


def resample(frame: pd.DataFrame, config) -> pd.DataFrame:
    """Resample to a fixed cadence so every well shares one time base."""
    seconds = int(config.get("volve_pipeline.resample_seconds"))
    numeric = frame.select_dtypes(include=[np.number]).columns.tolist()
    boolean = [c for c in ("circulating", "rotating", "tripping", "operations_active")
               if c in frame.columns]

    indexed = frame.set_index("timestamp").sort_index()
    aggregated = indexed[numeric].resample(f"{seconds}s").mean()
    if boolean:
        # A bin counts as active only if the rig was active for most of it.
        flags = indexed[boolean].astype(float).resample(f"{seconds}s").mean() >= 0.5
        aggregated = aggregated.join(flags)

    aggregated = aggregated.dropna(how="all")
    return aggregated.reset_index()


def recover_message_depths(
    messages: pd.DataFrame, telemetry: pd.DataFrame
) -> pd.DataFrame:
    """Attach a real depth to each operational remark.

    The WITSML message objects carry the wellbore total depth on every record rather than
    the depth at which the remark was made, so message depth is recovered by matching the
    message timestamp to the nearest telemetry sample. Rows keep both the original value
    and the recovered one, so the substitution is auditable.
    """
    if messages.empty or telemetry.empty:
        return messages

    messages = messages.copy()
    messages["timestamp"] = pd.to_datetime(messages["timestamp"], errors="coerce", utc=True)
    messages = messages.dropna(subset=["timestamp"]).sort_values("timestamp")
    messages = messages.rename(columns={"md_m": "md_reported_m"})

    depth_columns = [c for c in ("bit_depth_m", "hole_depth_m") if c in telemetry.columns]
    if not depth_columns:
        return messages

    reference = telemetry[["timestamp", *depth_columns]].dropna(subset=depth_columns, how="all")
    reference = reference.sort_values("timestamp")

    merged = pd.merge_asof(
        messages,
        reference,
        on="timestamp",
        direction="nearest",
        tolerance=pd.Timedelta("10min"),
    )
    merged["md_recovered_m"] = merged.get("bit_depth_m")
    if "hole_depth_m" in merged.columns:
        merged["md_recovered_m"] = merged["md_recovered_m"].fillna(merged["hole_depth_m"])
    merged["depth_source"] = np.where(
        merged["md_recovered_m"].notna(), "telemetry_time_join", "unresolved"
    )
    return merged


def main() -> int:
    config = get_config()
    raw_dir = config.path("datasets.volve.raw_dir")
    witsml_root = raw_dir / config.get("datasets.volve.witsml_subdir")
    out_dir = ensure_dir(Path(config.get("paths.data_processed")) / "volve")

    if not witsml_root.is_dir():
        log.error("volve_missing", path=str(witsml_root))
        return 1

    telemetry_frames, trajectory_frames, message_frames, well_records = [], [], [], []
    quality: dict = {"wells": {}}

    for well_dir in witsml.iter_well_directories(witsml_root):
        reference = witsml.decode_directory_name(well_dir.name)

        headers = witsml.find_objects(well_dir, "_wellInfo")
        header = witsml.read_well_header(headers[0]) if headers else None
        well_name = header.name if header else reference

        telemetry, report = read_well_telemetry(well_dir, config)
        if telemetry.empty:
            log.warning("no_telemetry", well=well_name)
            continue

        telemetry, flag_report = flag_quality(telemetry, config)
        rows_before = len(telemetry)
        # Resample BEFORE deriving activity. Overlapping log objects repeat timestamps,
        # and a derivative taken across a duplicated timestamp produces nonsense trip
        # speeds. Binning to a regular cadence merges duplicates first.
        telemetry = resample(telemetry, config)
        telemetry, activity_report = derive_activity(telemetry, config)
        telemetry.insert(0, "well_name", well_name)

        # Trajectory
        trajectories = [witsml.read_trajectory(p) for p in witsml.find_objects(well_dir, "trajectory")]
        trajectory = pd.concat([t for t in trajectories if not t.empty], ignore_index=True) \
            if any(not t.empty for t in trajectories) else pd.DataFrame()

        # Messages, with depth recovered by time join
        raw_messages = [witsml.read_messages(p) for p in witsml.find_objects(well_dir, "message")]
        messages = pd.concat([m for m in raw_messages if not m.empty], ignore_index=True) \
            if any(not m.empty for m in raw_messages) else pd.DataFrame()
        messages = recover_message_depths(messages, telemetry)

        telemetry_frames.append(telemetry)
        if not trajectory.empty:
            trajectory_frames.append(trajectory)
        if not messages.empty:
            message_frames.append(messages)

        well_records.append(
            {
                "well_name": well_name,
                "well_reference": reference,
                "field": header.field_name if header else None,
                "country": header.country if header else None,
                "region": header.region if header else None,
                "operator": header.operator if header else None,
                "water_depth_m": header.water_depth_m if header else None,
                "kb_elevation_m": (header.datums or {}).get("KB") if header else None,
                "latitude": header.latitude if header else None,
                "longitude": header.longitude if header else None,
                "telemetry_rows": int(len(telemetry)),
                "telemetry_start": str(telemetry["timestamp"].min()),
                "telemetry_end": str(telemetry["timestamp"].max()),
                "max_bit_depth_m": float(telemetry["bit_depth_m"].max())
                if "bit_depth_m" in telemetry else None,
                "operations_active_rows": int(telemetry["operations_active"].sum()),
            }
        )

        quality["wells"][well_name] = {
            **report,
            **flag_report,
            **activity_report,
            "rows_before_resample": rows_before,
            "rows_after_resample": int(len(telemetry)),
            "operations_active_rows": int(telemetry["operations_active"].sum()),
        }

        log.info(
            "well_prepared",
            well=well_name,
            logs=report["logs_read"],
            raw_rows=report["rows_raw"],
            resampled_rows=len(telemetry),
            operations_active=int(telemetry["operations_active"].sum()),
            states=activity_report["state_counts"],
            constant_channels=activity_report["constant_channels"] or None,
            channels=len(report["channels_resolved"]),
            missing=report["missing_channels"] or None,
        )

    if not telemetry_frames:
        log.error("no_telemetry_produced")
        return 1

    telemetry_all = pd.concat(telemetry_frames, ignore_index=True)
    telemetry_all.to_parquet(out_dir / "telemetry.parquet", index=False)
    pd.DataFrame(well_records).to_parquet(out_dir / "wells.parquet", index=False)

    if trajectory_frames:
        pd.concat(trajectory_frames, ignore_index=True).to_parquet(
            out_dir / "trajectory.parquet", index=False
        )
    if message_frames:
        pd.concat(message_frames, ignore_index=True).to_parquet(
            out_dir / "messages.parquet", index=False
        )

    quality["generated_at"] = datetime.now(timezone.utc).isoformat()
    quality["total_rows"] = int(len(telemetry_all))
    (out_dir / "quality_report.json").write_text(
        json.dumps(quality, indent=2, default=str), encoding="utf-8"
    )

    log.info(
        "volve_prepare_complete",
        wells=len(well_records),
        telemetry_rows=len(telemetry_all),
        operations_active_rows=int(telemetry_all["operations_active"].sum()),
        output=str(out_dir),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
