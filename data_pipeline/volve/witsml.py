"""WITSML 1.4.1.1 readers for the Volve drilling dataset.

Parses the object types actually present in the mirror:

    _wellInfo      well header, field, operator, datums, water depth
    _wellboreInfo  wellbore header
    log            time-indexed and depth-indexed channel data
    trajectory     directional survey stations
    message        timestamped, depth-tagged operational remarks
    bhaRun         bottom-hole assembly runs
    wbGeometry     wellbore geometry sections

Every reader returns plain dataclasses / DataFrames. Nothing about which curves exist
is assumed: channels are discovered from ``logCurveInfo`` and carried with their
declared units.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator
from xml.etree import ElementTree

import numpy as np
import pandas as pd

from nwis_common import citation_path

WITSML_NS = "{http://www.witsml.org/schemas/1series}"

# The mirror escapes '/' in directory names as '$47$' (its ASCII code).
_DIR_ESCAPE = re.compile(r"\$(\d+)\$")


def decode_directory_name(name: str) -> str:
    """Turn 'Norway-Statoil-NO 15_$47$_9-F-4' into a readable well reference."""
    decoded = _DIR_ESCAPE.sub(lambda m: chr(int(m.group(1))), name)
    return decoded.replace("_/_", "/")


def _tag(element: ElementTree.Element) -> str:
    return element.tag.replace(WITSML_NS, "")


def _text(parent: ElementTree.Element | None, path: str) -> str | None:
    if parent is None:
        return None
    node = parent.find(f"{WITSML_NS}{path}")
    return node.text.strip() if node is not None and node.text else None


def _float(parent: ElementTree.Element | None, path: str) -> float | None:
    raw = _text(parent, path)
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _uom(parent: ElementTree.Element | None, path: str) -> str | None:
    if parent is None:
        return None
    node = parent.find(f"{WITSML_NS}{path}")
    return node.get("uom") if node is not None else None


def _parse(path: Path) -> ElementTree.Element | None:
    try:
        return ElementTree.parse(path).getroot()
    except ElementTree.ParseError:
        return None


# --------------------------------------------------------------------------- headers


@dataclass
class WellHeader:
    uid: str
    name: str
    field_name: str | None
    country: str | None
    region: str | None
    operator: str | None
    water_depth_m: float | None
    datums: dict[str, float] = field(default_factory=dict)
    latitude: float | None = None
    longitude: float | None = None
    source_file: str = ""


def read_well_header(path: Path) -> WellHeader | None:
    root = _parse(path)
    if root is None:
        return None
    well = root.find(f"{WITSML_NS}well")
    if well is None:
        return None

    datums: dict[str, float] = {}
    for datum in well.findall(f"{WITSML_NS}wellDatum"):
        code = _text(datum, "code") or _text(datum, "name") or datum.get("uid") or "?"
        elevation = _float(datum, "elevation")
        if elevation is not None:
            datums[code] = elevation

    location = well.find(f"{WITSML_NS}wellLocation")
    latitude = _float(location, "latitude") if location is not None else None
    longitude = _float(location, "longitude") if location is not None else None
    # The mirror stores 0/0 placeholders rather than real positions. Treat those as
    # absent so no well is ever plotted off the coast of Africa.
    if latitude in (0.0, None) and longitude in (0.0, None):
        latitude = longitude = None

    return WellHeader(
        uid=well.get("uid", ""),
        name=_text(well, "name") or "",
        field_name=_text(well, "field"),
        country=_text(well, "country"),
        region=_text(well, "region"),
        operator=_text(well, "operator"),
        water_depth_m=_float(well, "waterDepth"),
        datums=datums,
        latitude=latitude,
        longitude=longitude,
        source_file=citation_path(path),
    )


@dataclass
class WellboreHeader:
    uid: str
    uid_well: str
    name: str
    well_name: str
    status: str | None
    md_planned_m: float | None
    kickoff_time: str | None
    source_file: str = ""


def read_wellbore_header(path: Path) -> WellboreHeader | None:
    root = _parse(path)
    if root is None:
        return None
    wellbore = root.find(f"{WITSML_NS}wellbore")
    if wellbore is None:
        return None
    return WellboreHeader(
        uid=wellbore.get("uid", ""),
        uid_well=wellbore.get("uidWell", ""),
        name=_text(wellbore, "name") or "",
        well_name=_text(wellbore, "nameWell") or "",
        status=_text(wellbore, "statusWellbore"),
        md_planned_m=_float(wellbore, "mdPlanned"),
        kickoff_time=_text(wellbore, "dTimKickoff"),
        source_file=citation_path(path),
    )


# ------------------------------------------------------------------------------ logs


@dataclass
class LogChannel:
    mnemonic: str
    unit: str | None
    description: str | None
    witsml_class: str | None


@dataclass
class LogObject:
    uid: str
    name: str
    well_name: str
    wellbore_name: str
    index_type: str | None
    index_curve: str | None
    null_value: str | None
    start_index: str | None
    end_index: str | None
    service_company: str | None
    channels: list[LogChannel]
    data: pd.DataFrame
    source_file: str = ""

    @property
    def row_count(self) -> int:
        return len(self.data)


def read_log(path: Path) -> LogObject | None:
    """Read one WITSML log file into a DataFrame of its declared channels."""
    root = _parse(path)
    if root is None:
        return None
    log_element = root.find(f"{WITSML_NS}log")
    if log_element is None:
        return None

    channels = [
        LogChannel(
            mnemonic=_text(info, "mnemonic") or "",
            unit=_text(info, "unit"),
            description=_text(info, "curveDescription"),
            witsml_class=_text(info, "classWitsml"),
        )
        for info in log_element.findall(f"{WITSML_NS}logCurveInfo")
    ]

    log_data = log_element.find(f"{WITSML_NS}logData")
    frame = pd.DataFrame()
    if log_data is not None:
        mnemonics = (_text(log_data, "mnemonicList") or "").split(",")
        units = (_text(log_data, "unitList") or "").split(",")
        rows = [
            node.text.split(",")
            for node in log_data.findall(f"{WITSML_NS}data")
            if node.text
        ]
        if rows and mnemonics:
            frame = pd.DataFrame(rows, columns=mnemonics)
            # Units declared inline on logData win over the header when both exist.
            if len(units) == len(mnemonics):
                declared = dict(zip(mnemonics, units))
                by_mnemonic = {c.mnemonic: c for c in channels}
                for mnemonic, unit in declared.items():
                    if mnemonic in by_mnemonic and not by_mnemonic[mnemonic].unit:
                        by_mnemonic[mnemonic].unit = unit
                    elif mnemonic not in by_mnemonic:
                        channels.append(LogChannel(mnemonic, unit, None, None))

    return LogObject(
        uid=log_element.get("uid", ""),
        name=_text(log_element, "name") or "",
        well_name=_text(log_element, "nameWell") or "",
        wellbore_name=_text(log_element, "nameWellbore") or "",
        index_type=_text(log_element, "indexType"),
        index_curve=_text(log_element, "indexCurve"),
        null_value=_text(log_element, "nullValue"),
        start_index=_text(log_element, "startDateTimeIndex")
        or _text(log_element, "startIndex"),
        end_index=_text(log_element, "endDateTimeIndex") or _text(log_element, "endIndex"),
        service_company=_text(log_element, "serviceCompany"),
        channels=channels,
        data=frame,
        source_file=citation_path(path),
    )


# ------------------------------------------------------------------------ trajectory


def read_trajectory(path: Path) -> pd.DataFrame:
    """Directional survey stations. Angles are converted from radians to degrees."""
    root = _parse(path)
    if root is None:
        return pd.DataFrame()

    records: list[dict[str, Any]] = []
    for trajectory in root.findall(f"{WITSML_NS}trajectory"):
        well_name = _text(trajectory, "nameWell") or ""
        wellbore_name = _text(trajectory, "nameWellbore") or ""
        traj_name = _text(trajectory, "name") or ""
        for station in trajectory.findall(f"{WITSML_NS}trajectoryStation"):
            incl = _float(station, "incl")
            azi = _float(station, "azi")
            dls = _float(station, "dls")
            incl_uom = _uom(station, "incl")
            azi_uom = _uom(station, "azi")
            records.append(
                {
                    "well_name": well_name,
                    "wellbore_name": wellbore_name,
                    "trajectory_name": traj_name,
                    "station_uid": station.get("uid"),
                    "station_type": _text(station, "typeTrajStation"),
                    "timestamp": _text(station, "dTimStn"),
                    "md_m": _float(station, "md"),
                    "tvd_m": _float(station, "tvd"),
                    "inclination_deg": np.degrees(incl)
                    if (incl is not None and incl_uom == "rad")
                    else incl,
                    "azimuth_deg": np.degrees(azi)
                    if (azi is not None and azi_uom == "rad")
                    else azi,
                    "north_offset_m": _float(station, "dispNs"),
                    "east_offset_m": _float(station, "dispEw"),
                    "vertical_section_m": _float(station, "vertSect"),
                    "dogleg_severity_deg_per_m": np.degrees(dls)
                    if (dls is not None and _uom(station, "dls") == "rad/m")
                    else dls,
                    "source_file": citation_path(path),
                }
            )
    return pd.DataFrame(records)


# --------------------------------------------------------------------------- messages


def read_messages(path: Path) -> pd.DataFrame:
    """Operational remarks: the raw material for historical event intelligence."""
    root = _parse(path)
    if root is None:
        return pd.DataFrame()

    records: list[dict[str, Any]] = []
    for message in root.findall(f"{WITSML_NS}message"):
        common = message.find(f"{WITSML_NS}commonData")
        records.append(
            {
                "message_uid": message.get("uid"),
                "well_name": _text(message, "nameWell") or "",
                "wellbore_name": _text(message, "nameWellbore") or "",
                "timestamp": _text(message, "dTim"),
                "md_m": _float(message, "md"),
                "tvd_m": _float(message, "tvd"),
                "message_type": _text(message, "typeMessage"),
                "activity_code": _text(message, "activityCode"),
                "severity": _text(message, "severity"),
                "text": _text(message, "messageText") or _text(message, "name") or "",
                "source_name": _text(common, "sourceName") if common is not None else None,
                "source_file": citation_path(path),
            }
        )
    return pd.DataFrame(records)


# ---------------------------------------------------------------------------- bhaRun


def read_bha_runs(path: Path) -> pd.DataFrame:
    root = _parse(path)
    if root is None:
        return pd.DataFrame()

    records: list[dict[str, Any]] = []
    for run in root.findall(f"{WITSML_NS}bhaRun"):
        records.append(
            {
                "run_uid": run.get("uid"),
                "well_name": _text(run, "nameWell") or "",
                "wellbore_name": _text(run, "nameWellbore") or "",
                "name": _text(run, "name"),
                "run_number": _text(run, "numStringRun"),
                "start_time": _text(run, "dTimStart"),
                "end_time": _text(run, "dTimStop"),
                "md_start_m": _float(run, "tubular"),
                "drilling_hours": _float(run, "tubular"),
                "source_file": citation_path(path),
            }
        )
    return pd.DataFrame(records)


# ------------------------------------------------------------------------- discovery


def iter_well_directories(witsml_root: Path) -> Iterator[Path]:
    for path in sorted(witsml_root.iterdir()):
        if path.is_dir():
            yield path


def find_objects(well_dir: Path, object_type: str) -> list[Path]:
    """All XML files for one WITSML object type under a well directory."""
    matches: list[Path] = []
    for path in well_dir.rglob("*.xml"):
        if object_type in path.parts:
            matches.append(path)
    return sorted(matches)
