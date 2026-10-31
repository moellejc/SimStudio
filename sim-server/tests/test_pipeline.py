"""Dry-run pipeline: mesh, decks, mock engine, summary scalars, viewer payload."""

from simstudio.config import Settings
from simstudio.pipeline import Job, run_job
from simstudio.status import StatusBus


class Sink:
    def __init__(self):
        self.fields = {}
        self.summary = None
        self.artifacts = None

    def update(self, simulation_id, **fields):
        self.fields.update(fields)

    def save_results(self, simulation_id, summary, artifacts, prefix, metric_rows=None):
        self.summary = summary
        self.artifacts = artifacts
        self.rows = metric_rows


class Store:
    def __init__(self):
        self.objects = {}

    def put_bytes(self, key, data, content_type):
        self.objects[key] = data
        return len(data)


def test_dry_run_job_writes_summary_and_viewer(tmp_path, monkeypatch):
    import fakeredis

    monkeypatch.setenv("SIM_MODE", "mock")
    settings = Settings(
        sim_mode="mock",
        work_root=str(tmp_path / "work"),
        keep_workdir=False,
        mock_pace_s=0,
        database_url="postgresql+psycopg://radlab:radlab@localhost:5432/radlab",
        redis_url="redis://localhost:6379/0",
    )
    bus = StatusBus(fakeredis.FakeRedis(decode_responses=True))
    sink = Sink()
    store = Store()
    job = Job(
        simulation_id="11111111-1111-1111-1111-111111111111",
        user_id="22222222-2222-2222-2222-222222222222",
        name="steel fork",
        dry_run=True,
        template_id="fork_load",
        material="steel",
        dx_mm=8.0,
        dy_mm=3.0,
        nz=2,
        end_time_ms=5,
        ramp_ms=4,
    )
    run_job(job, settings, bus, sink, store)
    assert sink.fields["status"] == "succeeded"
    assert "peak_force_N" in sink.summary
    assert sink.summary["geometry"]["bricks"] > 0
    assert any(row["name"] == "max_von_mises_Pa" for row in sink.rows)
    assert sink.summary.get("glb", "").endswith(".glb")
    assert any(key.endswith("sample.glb") or "/gltf/" in key and key.endswith(".glb") for key in store.objects)
    assert any(key.endswith("model.glb") for key in store.objects)
    assert any(key.endswith("metrics.json") for key in store.objects)
    assert any(key.endswith("model.json") for key in store.objects)
    snap = bus.snapshot(job.simulation_id)
    assert snap["state"] == "succeeded"
