"""Reproducible dataset acquisition for NWIS.

Downloads only what the configuration declares. Nothing about the datasets is
hardcoded here — sources, destinations and file lists all come from
``config/default.yaml`` under ``datasets``.

Usage:
    python scripts/download_datasets.py                  # all sources
    python scripts/download_datasets.py --only force
    python scripts/download_datasets.py --only volve --volve-source <existing clone>
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from nwis_common import get_config, get_logger
from nwis_common.paths import ensure_dir, resolve_path

log = get_logger("nwis.download")

_CHUNK = 1 << 20  # 1 MiB streaming chunks


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download(url: str, dest: Path, *, force: bool = False) -> Path:
    """Stream a URL to disk, skipping the transfer when the file already exists."""
    if dest.exists() and not force:
        log.info("download_skipped", file=dest.name, reason="already present",
                 size_bytes=dest.stat().st_size)
        return dest

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    log.info("download_start", url=url, dest=str(dest))
    with httpx.stream("GET", url, follow_redirects=True, timeout=120.0) as response:
        response.raise_for_status()
        total = int(response.headers.get("content-length", 0))
        written = 0
        with tmp.open("wb") as handle:
            for chunk in response.iter_bytes(_CHUNK):
                handle.write(chunk)
                written += len(chunk)
    tmp.replace(dest)
    log.info("download_complete", file=dest.name, bytes=written,
             expected_bytes=total or None, sha256=_sha256(dest)[:16])
    return dest


def fetch_force(config, *, force: bool = False) -> None:
    """FORCE 2020 lithology competition data (well logs + lithofacies labels)."""
    raw_dir = ensure_dir(config.get("datasets.force.raw_dir"))
    files = config.section("datasets.force.files")

    for key, url in files.items():
        filename = url.rsplit("/", 1)[-1]
        dest = raw_dir / filename
        _download(url, dest, force=force)

        if dest.suffix == ".zip":
            marker = raw_dir / f".{dest.stem}.extracted"
            if marker.exists() and not force:
                log.info("extract_skipped", archive=dest.name)
                continue
            log.info("extract_start", archive=dest.name)
            with zipfile.ZipFile(dest) as archive:
                names = archive.namelist()
                archive.extractall(raw_dir)
            marker.write_text("\n".join(names), encoding="utf-8")
            log.info("extract_complete", archive=dest.name, members=len(names))


def fetch_volve(config, *, source: str | None = None, force: bool = False) -> None:
    """Volve WITSML drilling data (public Equinor open-data mirror)."""
    raw_dir = ensure_dir(config.get("datasets.volve.raw_dir"))
    witsml_dir = raw_dir / config.get("datasets.volve.witsml_subdir")

    if witsml_dir.exists() and any(witsml_dir.iterdir()) and not force:
        wells = sum(1 for p in witsml_dir.iterdir() if p.is_dir())
        log.info("volve_skipped", reason="already present", well_dirs=wells)
        return

    if source:
        src = resolve_path(source)
        src_witsml = src / "witsml" if (src / "witsml").is_dir() else src
        if not src_witsml.is_dir():
            raise FileNotFoundError(f"No WITSML directory under {src}")
        log.info("volve_copy_start", source=str(src_witsml))
        shutil.copytree(src_witsml, witsml_dir, dirs_exist_ok=True)
    else:
        repo_url = config.get("datasets.volve.source_repo")
        clone_dir = raw_dir / "_clone"
        if clone_dir.exists():
            shutil.rmtree(clone_dir, ignore_errors=True)
        log.info("volve_clone_start", repo=repo_url)
        subprocess.run(
            ["git", "clone", "--depth", "1", "--quiet", f"{repo_url}.git", str(clone_dir)],
            check=True,
        )
        shutil.copytree(clone_dir / "witsml", witsml_dir, dirs_exist_ok=True)
        shutil.rmtree(clone_dir, ignore_errors=True)

    well_dirs = sorted(p.name for p in witsml_dir.iterdir() if p.is_dir())
    xml_count = sum(1 for _ in witsml_dir.rglob("*.xml"))
    log.info("volve_ready", well_dirs=len(well_dirs), xml_files=xml_count, wells=well_dirs)


def fetch_npd(config, *, force: bool = False) -> None:
    """Public wellbore coordinates (Norwegian Offshore Directorate factpages).

    Volve WITSML files carry zeroed surface coordinates, so geographic position is
    sourced here rather than invented.
    """
    raw_dir = ensure_dir(config.get("datasets.npd.raw_dir"))
    for key, filename in (
        ("wellbore_development_csv", "wellbore_development_all.csv"),
        ("wellbore_exploration_csv", "wellbore_exploration_all.csv"),
    ):
        url = config.get(f"datasets.npd.{key}")
        try:
            _download(url, raw_dir / filename, force=force)
        except Exception as exc:  # source availability is not fatal to the build
            log.warning("npd_download_failed", file=filename, error=str(exc),
                        impact="affected wells stay unmapped rather than being placed "
                               "at an invented position")


def main() -> int:
    parser = argparse.ArgumentParser(description="Download NWIS source datasets")
    parser.add_argument("--only", choices=["force", "volve", "npd"], action="append",
                        help="restrict to specific sources (repeatable)")
    parser.add_argument("--volve-source", default=None,
                        help="path to an existing volve-drilling clone to copy from")
    parser.add_argument("--force", action="store_true", help="re-download even if present")
    args = parser.parse_args()

    config = get_config()
    selected = set(args.only or ["force", "volve", "npd"])
    log.info("acquisition_start", sources=sorted(selected))

    if "force" in selected:
        fetch_force(config, force=args.force)
    if "volve" in selected:
        fetch_volve(config, source=args.volve_source, force=args.force)
    if "npd" in selected:
        fetch_npd(config, force=args.force)

    log.info("acquisition_complete", sources=sorted(selected))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
