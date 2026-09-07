"""Build the entire NWIS knowledge base from a clean checkout.

Runs every pipeline stage in dependency order, skipping work that is already done unless
told otherwise. Safe to re-run.

    python scripts/bootstrap.py                 # everything not already present
    python scripts/bootstrap.py --force         # rebuild from scratch
    python scripts/bootstrap.py --skip-documents --skip-training
    python scripts/bootstrap.py --dry-run       # show the plan without running it

Each stage reports how long it took, so a slow step is visible rather than mysterious.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from nwis_common import get_config, get_logger  # noqa: E402

log = get_logger("nwis.bootstrap")


@dataclass
class Stage:
    name: str
    command: list[str]
    description: str
    # Returns True when the stage output already exists.
    is_satisfied: Callable[[], bool]
    optional: bool = False


def _exists(*relative: str) -> Callable[[], bool]:
    def check() -> bool:
        return all((REPO_ROOT / part).exists() for part in relative)

    return check


def _database_populated() -> bool:
    try:
        from sqlalchemy import func, select

        from backend.app.core.database import session_scope
        from backend.app.models import Well

        session = session_scope()
        try:
            return bool(session.scalar(select(func.count()).select_from(Well)))
        finally:
            session.close()
    except Exception:
        return False


def _embeddings_loaded() -> bool:
    try:
        from sqlalchemy import func, select

        from backend.app.core.database import session_scope
        from backend.app.models import WellEmbedding

        session = session_scope()
        try:
            return bool(session.scalar(select(func.count()).select_from(WellEmbedding)))
        finally:
            session.close()
    except Exception:
        return False


def _predictions_loaded() -> bool:
    try:
        from sqlalchemy import func, select

        from backend.app.core.database import session_scope
        from backend.app.models import LithologyPrediction

        session = session_scope()
        try:
            return bool(
                session.scalar(select(func.count()).select_from(LithologyPrediction))
            )
        finally:
            session.close()
    except Exception:
        return False


def build_stages(config, args) -> list[Stage]:
    processed = Path(config.get("paths.data_processed"))
    models = Path(config.get("paths.models"))

    stages: list[Stage] = [
        Stage(
            "download",
            [sys.executable, "scripts/download_datasets.py"],
            "Acquire FORCE 2020, Volve WITSML and NPD wellbore tables (~325 MB)",
            _exists("data/raw/force2020/train.csv", "data/raw/volve/witsml"),
        ),
        Stage(
            "profile-force",
            [sys.executable, "-m", "data_pipeline.force.profile"],
            "Measure the FORCE schema: curves, coverage, class balance, duplicates",
            _exists("artifacts/profiling/force_train_profile.json"),
        ),
        Stage(
            "prepare-force",
            [sys.executable, "-m", "data_pipeline.force.prepare"],
            "Clean, engineer features and build the well-level split",
            _exists(str(processed / "force" / "features.parquet")),
        ),
        Stage(
            "profile-volve",
            [sys.executable, "-m", "data_pipeline.volve.profile"],
            "Inventory the WITSML objects, channels and units actually present",
            _exists("artifacts/profiling/volve_witsml_profile.json"),
        ),
        Stage(
            "prepare-volve",
            [sys.executable, "-m", "data_pipeline.volve.prepare"],
            "Parse WITSML into normalised telemetry, trajectories and events",
            _exists(str(processed / "volve" / "telemetry.parquet")),
        ),
    ]

    if not args.skip_training:
        stages += [
            Stage(
                "train-lithology",
                [sys.executable, "-m", "ml.lithology.train"],
                "Train and evaluate the lithology classifier on unseen wells",
                _exists(str(models / "lithology" / "selected.json")),
            ),
            Stage(
                "train-anomaly",
                [sys.executable, "-m", "ml.anomaly.train"],
                "Train the drilling-operations anomaly detector",
                _exists(str(models / "anomaly" / "isolation_forest.joblib")),
            ),
            Stage(
                "build-embeddings",
                [sys.executable, "-m", "ml.similarity.build_embeddings"],
                "Build depth-segment embeddings for the analogue engine",
                _exists(str(models / "analogue_embedding" / "embeddings.npy")),
            ),
        ]

    stages += [
        Stage(
            "load-database",
            [sys.executable, "-m", "data_pipeline.load_database", "--reset"],
            "Load wells, geology, telemetry, events and the model registry",
            _database_populated,
        ),
        Stage(
            "load-embeddings",
            [sys.executable, "-m", "data_pipeline.load_embeddings"],
            "Load segment embeddings into the vector store",
            _embeddings_loaded,
        ),
        Stage(
            "load-anomaly-scores",
            [sys.executable, "-m", "data_pipeline.load_anomaly_scores"],
            "Store anomaly scores so the risk engine can resolve them",
            lambda: False,  # cheap, and must follow any reload of the database
        ),
        Stage(
            "predict-lithology",
            [sys.executable, "-m", "ml.lithology.predict"],
            "Run the selected model over every well and store predictions",
            _predictions_loaded,
        ),
    ]

    if not args.skip_documents:
        stages.append(
            Stage(
                "ingest-documents",
                [
                    sys.executable, "-m", "data_pipeline.documents.ingest",
                    "--well", "15/9-13", "--well", "15/9-17", "--limit", "2",
                ],
                "OCR well reports and extract stratigraphy and events (slow)",
                _exists("data/interim/document_text"),
                optional=True,
            )
        )

    return stages


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the NWIS knowledge base")
    parser.add_argument("--force", action="store_true",
                        help="re-run every stage even if its output exists")
    parser.add_argument("--skip-training", action="store_true",
                        help="use existing models instead of retraining")
    parser.add_argument("--skip-documents", action="store_true",
                        help="skip OCR document ingestion, which is the slowest stage")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the plan without running anything")
    args = parser.parse_args()

    config = get_config()
    stages = build_stages(config, args)

    log.info("bootstrap_plan", stages=[s.name for s in stages], force=args.force)
    if args.dry_run:
        for stage in stages:
            status = "run" if args.force or not stage.is_satisfied() else "skip"
            print(f"  [{status:>4}] {stage.name:<22} {stage.description}")
        return 0

    started = time.perf_counter()
    completed, skipped, failed = [], [], []

    for stage in stages:
        if not args.force and stage.is_satisfied():
            log.info("stage_skipped", stage=stage.name, reason="output already present")
            skipped.append(stage.name)
            continue

        log.info("stage_start", stage=stage.name, description=stage.description)
        stage_started = time.perf_counter()
        result = subprocess.run(stage.command, cwd=REPO_ROOT)
        elapsed = round(time.perf_counter() - stage_started, 1)

        if result.returncode != 0:
            if stage.optional:
                log.warning("optional_stage_failed", stage=stage.name,
                            exit_code=result.returncode, seconds=elapsed)
                failed.append(stage.name)
                continue
            log.error("stage_failed", stage=stage.name,
                      exit_code=result.returncode, seconds=elapsed)
            return result.returncode

        log.info("stage_complete", stage=stage.name, seconds=elapsed)
        completed.append(stage.name)

    log.info(
        "bootstrap_complete",
        total_seconds=round(time.perf_counter() - started, 1),
        completed=completed,
        skipped=skipped,
        optional_failures=failed or None,
    )
    print("\nNWIS is ready. Start the services with:")
    print("  uvicorn backend.app.main:app --reload")
    print("  cd frontend && npm run dev")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
