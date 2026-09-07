"""Profile the Volve WITSML mirror as it actually exists on disk.

Reports, per well: which WITSML object types are present, which log objects exist,
which channels each carries with declared units, time and depth coverage, and how much
of each channel is actually populated.

Usage:
    python -m data_pipeline.volve.profile
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
import pandas as pd

from data_pipeline.common.units import canonical_unit, is_known
from data_pipeline.volve import witsml
from nwis_common import get_config, get_logger
from nwis_common.paths import ensure_dir

log = get_logger("nwis.volve.profile")


def _numeric(series: pd.Series, null_value: str | None) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce")
    if null_value:
        try:
            values = values.replace(float(null_value), np.nan)
        except ValueError:
            pass
    return values


def profile_well(well_dir: Path, config) -> dict:
    """Profile every WITSML object under one well directory."""
    well_reference = witsml.decode_directory_name(well_dir.name)
    sentinels = [float(v) for v in config.get("data_quality.null_sentinels")]

    object_types = Counter()
    for path in well_dir.rglob("*.xml"):
        for part in path.parts:
            if part in {
                "log", "trajectory", "message", "bhaRun", "wbGeometry",
                "tubular", "rig", "_wellInfo", "_wellboreInfo",
            }:
                object_types[part] += 1
                break

    # --- header ---------------------------------------------------------------
    header_files = witsml.find_objects(well_dir, "_wellInfo")
    header = witsml.read_well_header(header_files[0]) if header_files else None

    # --- logs -----------------------------------------------------------------
    logs_summary: list[dict] = []
    channel_stats: dict[str, dict] = defaultdict(
        lambda: {"logs": 0, "rows": 0, "populated": 0, "units": set(), "description": None}
    )
    total_rows = 0

    for log_path in witsml.find_objects(well_dir, "log"):
        log_object = witsml.read_log(log_path)
        if log_object is None or log_object.data.empty:
            continue
        total_rows += log_object.row_count

        populated: dict[str, float] = {}
        for channel in log_object.channels:
            if channel.mnemonic not in log_object.data.columns:
                continue
            values = _numeric(log_object.data[channel.mnemonic], log_object.null_value)
            for sentinel in sentinels:
                values = values.replace(sentinel, np.nan)
            non_null = int(values.notna().sum())
            populated[channel.mnemonic] = round(non_null / max(len(values), 1), 4)

            stats = channel_stats[channel.mnemonic]
            stats["logs"] += 1
            stats["rows"] += len(values)
            stats["populated"] += non_null
            if channel.unit:
                stats["units"].add(channel.unit)
            stats["description"] = stats["description"] or channel.description

        logs_summary.append(
            {
                "name": log_object.name,
                "uid": log_object.uid,
                "index_type": log_object.index_type,
                "index_curve": log_object.index_curve,
                "rows": log_object.row_count,
                "channels": len(log_object.channels),
                "start_index": log_object.start_index,
                "end_index": log_object.end_index,
                "service_company": log_object.service_company,
                "null_value": log_object.null_value,
                "file": str(log_path.relative_to(well_dir)),
            }
        )

    # --- trajectory -----------------------------------------------------------
    trajectory_frames = [
        witsml.read_trajectory(p) for p in witsml.find_objects(well_dir, "trajectory")
    ]
    trajectory = pd.concat([f for f in trajectory_frames if not f.empty], ignore_index=True) \
        if any(not f.empty for f in trajectory_frames) else pd.DataFrame()

    trajectory_summary = {}
    if not trajectory.empty:
        trajectory_summary = {
            "stations": int(len(trajectory)),
            "md_min_m": round(float(trajectory["md_m"].min()), 2),
            "md_max_m": round(float(trajectory["md_m"].max()), 2),
            "tvd_max_m": round(float(trajectory["tvd_m"].max()), 2)
            if trajectory["tvd_m"].notna().any() else None,
            "max_inclination_deg": round(float(trajectory["inclination_deg"].max()), 2)
            if trajectory["inclination_deg"].notna().any() else None,
            "station_types": trajectory["station_type"].value_counts().to_dict(),
        }

    # --- messages -------------------------------------------------------------
    message_frames = [
        witsml.read_messages(p) for p in witsml.find_objects(well_dir, "message")
    ]
    messages = pd.concat([f for f in message_frames if not f.empty], ignore_index=True) \
        if any(not f.empty for f in message_frames) else pd.DataFrame()

    message_summary = {}
    if not messages.empty:
        message_summary = {
            "count": int(len(messages)),
            "with_depth": int(messages["md_m"].notna().sum()),
            "depth_range_m": [
                round(float(messages["md_m"].min()), 2),
                round(float(messages["md_m"].max()), 2),
            ] if messages["md_m"].notna().any() else None,
            "time_range": [
                str(messages["timestamp"].min()),
                str(messages["timestamp"].max()),
            ],
            "types": messages["message_type"].value_counts().to_dict(),
            "median_text_length": int(messages["text"].str.len().median()),
        }

    channels_out = {
        name: {
            "logs_containing": stats["logs"],
            "total_rows": stats["rows"],
            "populated_fraction": round(stats["populated"] / max(stats["rows"], 1), 4),
            "declared_units": sorted(stats["units"]),
            "canonical_unit": canonical_unit(next(iter(stats["units"]), None)),
            "unit_recognised": all(is_known(u) for u in stats["units"]) if stats["units"] else False,
            "description": stats["description"],
        }
        for name, stats in sorted(channel_stats.items())
    }

    return {
        "well_directory": well_dir.name,
        "well_reference": well_reference,
        "header": {
            "name": header.name if header else None,
            "field": header.field_name if header else None,
            "country": header.country if header else None,
            "region": header.region if header else None,
            "operator": header.operator if header else None,
            "water_depth_m": header.water_depth_m if header else None,
            "datums": header.datums if header else {},
            "latitude": header.latitude if header else None,
            "longitude": header.longitude if header else None,
        },
        "object_type_file_counts": dict(object_types),
        "logs": logs_summary,
        "log_row_total": total_rows,
        "channels": channels_out,
        "trajectory": trajectory_summary,
        "messages": message_summary,
    }


def main() -> int:
    config = get_config()
    raw_dir = config.path("datasets.volve.raw_dir")
    witsml_root = raw_dir / config.get("datasets.volve.witsml_subdir")
    out_dir = ensure_dir("artifacts/profiling")

    if not witsml_root.is_dir():
        log.error("volve_missing", path=str(witsml_root),
                  hint="run scripts/download_datasets.py --only volve")
        return 1

    wells = [profile_well(d, config) for d in witsml.iter_well_directories(witsml_root)]

    unmapped_units = sorted(
        {
            unit
            for well in wells
            for channel in well["channels"].values()
            for unit in channel["declared_units"]
            if not is_known(unit)
        }
    )
    missing_coordinates = [
        well["well_reference"] for well in wells if well["header"]["latitude"] is None
    ]

    report = {
        "dataset": "Volve WITSML (public mirror)",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_root": str(witsml_root),
        "well_count": len(wells),
        "total_log_rows": sum(w["log_row_total"] for w in wells),
        "wells": wells,
        "notes": [
            f"Units without a conversion rule: {unmapped_units}" if unmapped_units
            else "All declared units are recognised.",
            f"Wells without usable surface coordinates in WITSML: {missing_coordinates}. "
            "Positions must come from the NPD factpages; none are invented."
            if missing_coordinates else "All wells carry surface coordinates.",
        ],
    }

    path = out_dir / "volve_witsml_profile.json"
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    log.info(
        "volve_profile_written",
        path=str(path),
        wells=len(wells),
        log_rows=report["total_log_rows"],
        unmapped_units=unmapped_units or None,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
