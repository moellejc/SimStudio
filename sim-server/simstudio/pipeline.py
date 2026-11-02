"""One simulation: mesh, decks, OpenMP engine (or mock), post, upload."""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from geometry import build_fork
from post import artifact_manifest, metrics_to_rows, process_run
from runner import (
    DeckSpec,
    engine_binary_name,
    generate_decks,
    launch_engine,
    resolve_binary,
    solver_environment,
)
from simstudio.config import Settings
from simstudio.deck_mesh import mesh_from_deck, skip_missing_includes

log = logging.getLogger(__name__)


@dataclass
class Job:
    simulation_id: str
    user_id: str
    name: str
    dry_run: bool
    template_id: str = "fork_load"
    material: str = "steel"
    end_time_ms: float = 20.0
    load_kN: float = 0.4413
    stop_displacement_mm: float = 40.0
    push_velocity_mm_ms: float = 0.25
    ramp_ms: float = 100.0
    threads: int = 1
    friction: float = 0.3
    dx_mm: float = 8.0
    dy_mm: float = 3.0
    nz: int = 2
    starter_text: str = ""
    engine_text: str = ""
    run_name: str = ""

    def to_json(self) -> str:
        return json.dumps(self.__dict__)

    @classmethod
    def from_json(cls, raw: str) -> "Job":
        return cls(**json.loads(raw))


def solver_available() -> bool:
    env = solver_environment(1)
    binary = resolve_binary(env, engine_binary_name())
    return bool(binary) and "ompi" not in Path(binary).name


def should_mock(job: Job, settings: Settings) -> bool:
    mode = settings.sim_mode.lower()
    if mode == "mock" or job.dry_run:
        return True
    if not solver_available():
        if mode == "auto":
            return True
        raise RuntimeError("OpenMP engine engine_linux64_gf is not installed")
    return False


def run_job(job: Job, settings: Settings, bus, sink, storage) -> None:
    work = Path(settings.work_root) / job.simulation_id
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    sim = job.simulation_id

    def phase(state: str, progress: float, message: str) -> None:
        if bus.is_cancelled(sim) and state not in {"cancelled", "failed"}:
            raise Cancelled()
        bus.update(sim, state, progress, message)
        sink.update(sim, status=state, progress=progress, message=message, error_message=None)
        if settings.mock_pace_s:
            time.sleep(settings.mock_pace_s)

    try:
        geometry_doc: dict = {}
        if job.starter_text and job.engine_text:
            phase("meshing", 8, "Using the uploaded starter and engine decks")
            mesh = _mesh_from_deck(job.starter_text)
            if mesh is None:
                raise RuntimeError("Uploaded starter has no /NODE mesh with /BRICK, /SHELL or /SH3N elements to view")
            geometry_doc = mesh.export_all(str(work / "geometry"), title=job.name)
            include = Path(geometry_doc["files"]["radioss_inc"])
            run_name = job.run_name or "RUN"
            run_dir = work / run_name
            run_dir.mkdir(parents=True)
            starter_path = run_dir / f"{run_name}_0000.rad"
            engine_path = run_dir / f"{run_name}_0001.rad"
            starter_path.write_text(job.starter_text)
            engine_path.write_text(job.engine_text)
            for deck_path in (starter_path, engine_path):
                skip_missing_includes(deck_path, log=lambda line: bus.log(sim, "deck", line))
            spec = DeckSpec(
                name=run_name,
                material=job.material if job.material in {"steel", "aluminum", "plastic"} else "steel",
                mode="load",
                out_dir=str(work),
                mesh_path=str(include),
                threads=max(1, job.threads),
                end_time_ms=job.end_time_ms or 1.0,
            )
            from runner import DeckFiles

            decks = DeckFiles(str(starter_path), str(engine_path), str(run_dir), {"mode": "upload"})
            bus.log(sim, "deck", f"uploaded {starter_path.name}")
        else:
            phase("meshing", 8, "Meshing the fork template (tines down, 1 mm above the floor)")
            mesh = build_fork("realistic", nz=max(1, int(job.nz)), dx=float(job.dx_mm), dy=float(job.dy_mm))
            geometry_doc = mesh.export_all(str(work / "geometry"), title=job.name)
            include = Path(geometry_doc["files"]["radioss_inc"])
            bus.log(
                sim,
                "geometry",
                f"{geometry_doc['bricks']} hexes, {geometry_doc['nodes']} nodes, volume {geometry_doc['volume_mm3']:.0f} mm³",
            )

            mode = "displacement" if job.template_id == "fork_displacement" else "load"
            spec = DeckSpec(
                name=_safe_name(job.name, job.material),
                material=job.material,
                mode=mode,
                out_dir=str(work),
                mesh_path=str(include),
                load_kN=job.load_kN,
                stop_displacement_mm=job.stop_displacement_mm,
                end_time_ms=job.end_time_ms,
                ramp_ms=job.ramp_ms,
                velocity_mm_ms=job.push_velocity_mm_ms,
                friction=job.friction,
                threads=max(1, job.threads),
                nanim=8,
                nth=40,
            )
            phase("running_starter", 22, "Writing starter and engine decks")
            decks = generate_decks(spec)
            bus.log(sim, "deck", f"starter {Path(decks.starter_path).name}")

        mock = should_mock(job, settings)
        if mock:
            why = "dry run" if job.dry_run or settings.sim_mode == "mock" else "solver binary not mounted; auto-mock"
            phase("running_engine", 48, f"Mock OpenMP engine ({why})")
            bus.log(sim, "engine", f"engine binary would be {engine_binary_name()} with OMP_NUM_THREADS={spec.threads}")
        else:
            phase("running_engine", 48, f"Running {engine_binary_name()} with OMP_NUM_THREADS={spec.threads}")

        def emit(stream: str, line: str) -> None:
            bus.log(sim, stream, line)

        result = launch_engine(
            decks.run_dir,
            spec.name,
            spec.threads,
            spec.stop_displacement_mm,
            mock=mock,
            log=None if mock else emit,
            cancel_flag=lambda: bus.is_cancelled(sim),
        )
        if result.cancelled:
            phase("cancelled", 100, "Cancelled")
            return
        if not result.normal_termination and not mock:
            raise RuntimeError(result.log_tail or f"engine exit {result.returncode}")
        for line in (result.log_tail or "").splitlines()[-8:]:
            bus.log(sim, "listing", line)

        phase("postprocessing", 90, "Converting animation to VTK (Pa), frames, and metrics")
        post_dir = work / "post_out"
        ramp = 5.0 if job.template_id == "fork_load" else None
        metrics = process_run(decks.run_dir, str(post_dir), ramp_duration=ramp, video=not mock)
        metrics["geometry"] = {key: value for key, value in geometry_doc.items() if key != "files"}
        metrics["parameters"] = {
            "template_id": job.template_id,
            "material": job.material,
            "ramp_ms": job.ramp_ms,
            "end_time_ms": job.end_time_ms,
            "load_kN": job.load_kN,
            "dry_run": mock,
            "engine": engine_binary_name(),
            "threads": spec.threads,
        }
        attach_sample_surface(metrics, post_dir)
        rows = metrics_to_rows(metrics, sim)
        prefix = f"sims/{sim}/post"
        artifacts = _upload_post(storage, prefix, post_dir)
        artifacts.extend(_upload_inputs(storage, f"sims/{sim}/input", decks, include, geometry_doc))
        sink.save_results(sim, metrics, artifacts, prefix, rows)
        peak = metrics.get("peak_force_N")
        vm = metrics.get("max_von_mises_Pa")
        if peak is not None and vm is not None:
            message = f"Peak force {peak:.2f} N, max von Mises {vm:.3e} Pa"
        elif peak is not None:
            message = f"Peak force {peak:.2f} N"
        else:
            message = "Finished"
        phase("succeeded", 100, message)
    except Cancelled:
        bus.update(sim, "cancelled", 100, "Cancelled")
        sink.update(sim, status="cancelled", progress=100, message="Cancelled")
    except Exception as exc:  # noqa: BLE001
        log.exception("job %s failed", sim)
        message = str(exc).strip() or exc.__class__.__name__
        bus.update(sim, "failed", 100, "Failed", message[:500])
        sink.update(sim, status="failed", progress=100, message="Failed", error_message=message[:2000])
    finally:
        if not settings.keep_workdir:
            shutil.rmtree(work, ignore_errors=True)


class Cancelled(Exception):
    pass


def _mesh_from_deck(text: str):
    """Best-effort /NODE + /BRICK, /SHELL, /SH3N parse so an uploaded deck can still be viewed.

    Brick-only decks return a geometry.HexMesh (unchanged exports); decks with shells a DeckMesh."""
    return mesh_from_deck(text)


def _safe_name(name: str, material: str) -> str:
    raw = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name.strip())[:40] or "fork"
    if material not in raw:
        raw = f"{raw}_{material}"
    return raw[:48]


def attach_sample_surface(metrics: dict, post_dir: Path) -> None:
    """Use the glTF that process_run wrote.

    Converters are optional in mock mode. When they do not produce
    gltf/<name>.glb, copy post/samples/sample.glb so the viewer still has a
    morph-target surface in the same meters / Y-up frame as model.glb.
    """
    rel = metrics.get("glb")
    if rel and (post_dir / rel).is_file():
        return
    sample = Path(__file__).resolve().parents[1] / "post" / "samples" / "sample.glb"
    dest = post_dir / "gltf"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy(sample, dest / "sample.glb")
    manifest = sample.with_name("sample.manifest.json")
    if manifest.is_file():
        shutil.copy(manifest, dest / "manifest.json")
    metrics["glb"] = "gltf/sample.glb"
    notes = list(metrics.get("notes") or [])
    notes.append("No solver surface; attached post/samples/sample.glb (meters, Y-up).")
    metrics["notes"] = notes


def _upload_post(storage, prefix: str, post_dir: Path) -> list[dict]:
    """Upload every file artifact_manifest reports. Keys are sims/<id>/post/<suffix>."""
    artifacts = []
    for item in artifact_manifest(str(post_dir)):
        path = Path(item["path"])
        key = f"{prefix}/{item['s3_key_suffix']}"
        data = path.read_bytes()
        content_type = "model/gltf-binary" if path.suffix == ".glb" else item["content_type"]
        storage.put_bytes(key, data, content_type)
        artifacts.append(
            {
                "kind": item["s3_key_suffix"],
                "filename": path.name,
                "s3_key": key,
                "content_type": content_type,
                "size_bytes": len(data),
            }
        )
    return artifacts


def _upload_inputs(storage, prefix: str, decks, include: Path, geometry_doc: dict | None) -> list[dict]:
    files = [
        (Path(decks.starter_path), "text/plain"),
        (Path(decks.engine_path), "text/plain"),
        (include, "text/plain"),
        (Path((geometry_doc or {}).get("files", {}).get("stl", "")), "model/stl"),
        (Path((geometry_doc or {}).get("files", {}).get("vtk", "")), "application/octet-stream"),
        (Path((geometry_doc or {}).get("files", {}).get("glb", "")), "model/gltf-binary"),
        (Path((geometry_doc or {}).get("files", {}).get("summary", "")), "application/json"),
    ]
    artifacts = []
    for path, content_type in files:
        if not path.is_file():
            continue
        data = path.read_bytes()
        key = f"{prefix}/{path.name}"
        storage.put_bytes(key, data, content_type)
        artifacts.append(
            {"kind": f"input/{path.name}", "filename": path.name, "s3_key": key, "content_type": content_type, "size_bytes": len(data)}
        )
    return artifacts


# Imported by tests that poke the queue without redis.
os.environ.setdefault("SIM_MODE", os.environ.get("SIM_MODE", "auto"))
