"""gRPC servicer. Generated stubs are imported from simstudio.gen."""

from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path

import grpc

_GEN = Path(__file__).resolve().parent / "gen"
if str(_GEN) not in sys.path:
    sys.path.insert(0, str(_GEN))

import simstudio_pb2 as pb  # noqa: E402
import simstudio_pb2_grpc as pbg  # noqa: E402

from simstudio.pipeline import Job  # noqa: E402

_STATE = {
    "": pb.SIM_STATE_UNSPECIFIED,
    "queued": pb.QUEUED,
    "meshing": pb.MESHING,
    "running_starter": pb.RUNNING_STARTER,
    "running_engine": pb.RUNNING_ENGINE,
    "postprocessing": pb.POSTPROCESSING,
    "succeeded": pb.SUCCEEDED,
    "failed": pb.FAILED,
    "cancelled": pb.CANCELLED,
}


def _state(name: str) -> int:
    return _STATE.get(name or "", pb.SIM_STATE_UNSPECIFIED)


class SimulationServicer(pbg.SimStudioServicer):
    def __init__(self, redis_client, sink, bus, queue_key: str):
        self.redis = redis_client
        self.sink = sink
        self.bus = bus
        self.queue_key = queue_key

    def SubmitSim(self, request, context):
        try:
            job = _job_from_request(request)
        except ValueError as exc:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(exc))
        row = self.sink.get(job.simulation_id, job.user_id)
        if row is None:
            context.abort(grpc.StatusCode.NOT_FOUND, "simulation was not created for this user")
        if row.status not in {"queued", "failed"}:
            snap = self.bus.snapshot(job.simulation_id)
            state = snap["state"] or row.status
            return pb.SubmitSimResponse(simulation_id=job.simulation_id, state=_state(state), message=row.message or "already submitted")
        self.redis.lpush(self.queue_key, job.to_json())
        self.bus.update(job.simulation_id, "queued", 0, "Queued")
        self.sink.update(job.simulation_id, status="queued", progress=0, message="Queued", error_message=None)
        return pb.SubmitSimResponse(simulation_id=job.simulation_id, state=pb.QUEUED, message="Queued")

    def GetStatus(self, request, context):
        row = self.sink.get(request.simulation_id, request.user_id)
        if row is None:
            context.abort(grpc.StatusCode.NOT_FOUND, "simulation not found")
        snap = self.bus.snapshot(request.simulation_id)
        state = snap["state"] or row.status
        return pb.SimStatus(
            simulation_id=request.simulation_id,
            state=_state(state),
            progress=snap["progress"] or row.progress or 0,
            message=snap["message"] or row.message or "",
            error_message=snap["error"] or (row.error_message or ""),
            updated_at_ms=snap["updated_at_ms"] or int(time.time() * 1000),
        )

    def StreamStatus(self, request, context):
        row = self.sink.get(request.simulation_id, request.user_id)
        if row is None:
            context.abort(grpc.StatusCode.NOT_FOUND, "simulation not found")
        snap = self.bus.snapshot(request.simulation_id)
        state = snap["state"] or row.status
        yield pb.SimStatus(
            simulation_id=request.simulation_id,
            state=_state(state),
            progress=snap["progress"] or row.progress or 0,
            message=snap["message"] or row.message or "",
            error_message=snap["error"] or (row.error_message or ""),
            updated_at_ms=snap["updated_at_ms"],
        )
        if state in {"succeeded", "failed", "cancelled"}:
            return
        pubsub = self.redis.pubsub()
        pubsub.subscribe(f"sim:{request.simulation_id}:events")
        started = time.time()
        try:
            while context.is_active() and time.time() - started < 3600:
                msg = pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if not msg or msg.get("type") != "message":
                    continue
                data = json.loads(msg["data"])
                if data.get("type") != "status":
                    continue
                yield pb.SimStatus(
                    simulation_id=request.simulation_id,
                    state=_state(data.get("state", "")),
                    progress=float(data.get("progress") or 0),
                    message=data.get("message") or "",
                    error_message=data.get("error") or "",
                    updated_at_ms=int(time.time() * 1000),
                )
                if data.get("state") in {"succeeded", "failed", "cancelled"}:
                    return
        finally:
            pubsub.close()

    def ListSims(self, request, context):
        rows = self.sink.list_for_user(request.user_id, request.limit or 50)
        sims = []
        for row in rows:
            sims.append(
                pb.SimSummary(
                    simulation_id=str(row.id),
                    name=row.name,
                    state=_state(row.status),
                    progress=row.progress or 0,
                    message=row.message or "",
                    template_id=row.template_id or "",
                    material=row.material or "",
                    dry_run=bool(row.dry_run),
                    created_at=row.created_at.isoformat() if row.created_at else "",
                    peak_force_n=row.peak_force_n or 0,
                    max_von_mises_pa=row.max_von_mises_pa or 0,
                    max_plastic_strain=row.max_plastic_strain or 0,
                )
            )
        return pb.ListSimsResponse(simulations=sims)

    def GetResults(self, request, context):
        row = self.sink.get(request.simulation_id, request.user_id)
        if row is None:
            context.abort(grpc.StatusCode.NOT_FOUND, "simulation not found")
        summary = row.summary or {}
        return pb.SimResults(
            simulation_id=request.simulation_id,
            state=_state(row.status),
            summary_json=json.dumps(summary),
            peak_force_n=row.peak_force_n or 0,
            max_von_mises_pa=row.max_von_mises_pa or 0,
            max_plastic_strain=row.max_plastic_strain or 0,
            energy_balance_error_pct=row.energy_balance_error_pct or 0,
            error_message=row.error_message or "",
        )

    def CancelSim(self, request, context):
        row = self.sink.get(request.simulation_id, request.user_id)
        if row is None:
            context.abort(grpc.StatusCode.NOT_FOUND, "simulation not found")
        self.bus.request_cancel(request.simulation_id)
        if row.status in {"queued", "meshing", "running_starter", "running_engine", "postprocessing"}:
            self.bus.update(request.simulation_id, "cancelled", row.progress or 0, "Cancellation requested")
            self.sink.update(request.simulation_id, status="cancelled", message="Cancellation requested")
        return pb.CancelSimResponse(simulation_id=request.simulation_id, state=pb.CANCELLED, message="Cancellation requested")


def _job_from_request(request) -> Job:
    which = request.WhichOneof("source")
    if which == "deck":
        return _job_from_deck(request)
    if which != "template":
        raise ValueError("submit a fork template or a starter/engine deck pair")
    tmpl = request.template
    if tmpl.material not in {"steel", "aluminum", "plastic"}:
        raise ValueError("material must be steel, aluminum, or plastic")
    if tmpl.template_id not in {"fork_load", "fork_displacement"}:
        raise ValueError("template_id must be fork_load or fork_displacement")
    try:
        uuid.UUID(request.simulation_id)
        uuid.UUID(request.user_id)
    except ValueError as exc:
        raise ValueError("simulation_id and user_id must be uuids") from exc
    # Dry runs use a coarser pitch of the same fork template so the viewer stays light.
    # A solver run uses the package defaults (0.8 mm × 0.35 mm, 5 layers).
    dx = 2.0 if request.dry_run else 0.8
    dy = 0.9 if request.dry_run else 0.35
    nz = 3 if request.dry_run else 5
    return Job(
        simulation_id=request.simulation_id,
        user_id=request.user_id,
        name=request.name or "fork",
        dry_run=bool(request.dry_run),
        template_id=tmpl.template_id,
        material=tmpl.material,
        end_time_ms=tmpl.end_time_ms or (20.0 if request.dry_run else 40.0),
        load_kN=tmpl.load_kN or 0.4413,
        stop_displacement_mm=tmpl.stop_displacement_mm or 40.0,
        push_velocity_mm_ms=tmpl.push_velocity_mm_ms or 0.25,
        ramp_ms=tmpl.ramp_ms or 100.0,
        threads=tmpl.threads or 1,
        friction=tmpl.friction or 0.3,
        dx_mm=dx,
        dy_mm=dy,
        nz=nz,
    )


def _job_from_deck(request) -> Job:
    deck = request.deck
    starter = deck.starter_deck
    engine = deck.engine_deck
    if isinstance(starter, str):
        starter = starter.encode()
    if isinstance(engine, str):
        engine = engine.encode()
    if len(starter) > 8 * 1024 * 1024 or len(engine) > 8 * 1024 * 1024:
        raise ValueError("decks must be under 8 MB each")
    run = _run_name(deck.starter_filename or "", deck.engine_filename or "")
    try:
        uuid.UUID(request.simulation_id)
        uuid.UUID(request.user_id)
    except ValueError as exc:
        raise ValueError("simulation_id and user_id must be uuids") from exc
    return Job(
        simulation_id=request.simulation_id,
        user_id=request.user_id,
        name=request.name or run,
        dry_run=bool(request.dry_run),
        template_id="upload",
        material="steel",
        threads=_upload_threads(deck.threads),
        units=_upload_units(getattr(deck, "units", "")),
        starter_text=starter.decode("utf-8", errors="replace"),
        engine_text=engine.decode("utf-8", errors="replace"),
        run_name=run,
    )


def _upload_units(requested: str) -> str:
    """'' for /BEGIN (auto), else one of sanity.UPLOAD_UNIT_SYSTEMS."""
    from simstudio.sanity import UPLOAD_UNIT_SYSTEMS

    value = (requested or "").strip().lower()
    if value in ("", "auto"):
        return ""
    if value not in UPLOAD_UNIT_SYSTEMS:
        raise ValueError(f"units must be auto or one of {', '.join(UPLOAD_UNIT_SYSTEMS)}")
    return value


def _upload_threads(requested: int) -> int:
    """Study thread count for uploaded decks: the request's value, else OMP_NUM_THREADS, clamped to 1..16."""
    import os

    value = int(requested or 0)
    if value <= 0:
        try:
            value = int(os.environ.get("OMP_NUM_THREADS", "1"))
        except ValueError:
            value = 1
    return max(1, min(16, value))


def _run_name(starter_filename: str, engine_filename: str) -> str:
    import re

    def stem(name: str) -> str:
        match = re.search(r"([A-Za-z0-9_-]+)_000[01]\.rad$", name.replace("\\", "/").split("/")[-1], re.I)
        if not match:
            raise ValueError("filenames must look like RunName_0000.rad and RunName_0001.rad")
        return match.group(1)

    left, right = stem(starter_filename), stem(engine_filename)
    if left != right:
        raise ValueError("starter and engine decks must share a run name")
    return left
