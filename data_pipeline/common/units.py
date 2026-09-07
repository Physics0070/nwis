"""Unit conversion registry.

Volve WITSML is published in strict SI: hookload in newtons, pressure in pascals,
temperature in kelvin, rotation in cycles per second, ROP in metres per second.
Those are unreadable on a drilling dashboard, so telemetry is normalised once at
ingestion into the units a driller actually uses.

These are physical constants, not tunable parameters, so they live in code rather than
in ``config/`` — but the conversion is switchable via ``volve_pipeline.unit_conversions``
and every converted channel records both its source and its canonical unit, so the
transformation is always auditable.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class Conversion:
    """A conversion from a WITSML source unit to the canonical display unit."""

    source_unit: str
    canonical_unit: str
    apply: Callable[[float], float]
    description: str


def _scale(factor: float) -> Callable[[float], float]:
    return lambda value: value * factor


# Keyed by the unit string as it appears in WITSML ``<unit>`` elements.
CONVERSIONS: dict[str, Conversion] = {
    "N": Conversion("N", "kN", _scale(1e-3), "force: newtons to kilonewtons"),
    "N.m": Conversion("N.m", "kN.m", _scale(1e-3), "torque: newton-metres to kilonewton-metres"),
    "Pa": Conversion("Pa", "bar", _scale(1e-5), "pressure: pascals to bar"),
    "m3/s": Conversion("m3/s", "L/min", _scale(60_000.0), "flow rate: cubic metres per second to litres per minute"),
    "c/s": Conversion("c/s", "rpm", _scale(60.0), "rotation: cycles per second to revolutions per minute"),
    "Hz": Conversion("Hz", "spm", _scale(60.0), "pump stroke rate: hertz to strokes per minute"),
    "K": Conversion("K", "degC", lambda v: v - 273.15, "temperature: kelvin to degrees Celsius"),
    "kg/m3": Conversion("kg/m3", "s.g.", _scale(1e-3), "density: kilograms per cubic metre to specific gravity"),
    "m/s": Conversion("m/s", "m/h", _scale(3600.0), "rate of penetration: metres per second to metres per hour"),
    "J": Conversion("J", "kJ", _scale(1e-3), "energy: joules to kilojoules"),
}

# Units that are already sensible, or carry no dimension.
PASSTHROUGH_UNITS = {"m", "s", "unitless", "ohm.m", "m3", "A", "Euc", "%", ""}


def convert_series(values, unit: str | None):
    """Convert a pandas Series given its WITSML unit.

    Returns ``(converted_values, canonical_unit, applied)``. Unknown units are passed
    through untouched and reported, never guessed at.
    """
    if not unit:
        return values, unit, False
    conversion = CONVERSIONS.get(unit)
    if conversion is None:
        return values, unit, False
    return conversion.apply(values), conversion.canonical_unit, True


def canonical_unit(unit: str | None) -> str | None:
    conversion = CONVERSIONS.get(unit or "")
    return conversion.canonical_unit if conversion else unit


def is_known(unit: str | None) -> bool:
    return (unit or "") in CONVERSIONS or (unit or "") in PASSTHROUGH_UNITS
