"""Contract tests for the runner and post interfaces, using the geometry package."""

from pathlib import Path

from geometry import build_fork
from post import artifact_manifest, metrics_to_rows, process_run
from runner import MPI_ENGINE, OPENMP_ENGINE, DeckSpec, engine_binary_name, generate_decks, solver_environment
from runner.mock_run import write_mock_outputs


def _coarse_include(tmp_path: Path) -> str:
    mesh = build_fork("uniform", nz=2, dx=8.0, dy=3.0)
    summary = mesh.export_all(str(tmp_path / "geom"), title="fork")
    assert summary["inverted_bricks"] == 0
    assert summary["bbox_min_mm"][2] == 1.0
    assert (tmp_path / "geom" / "model.inc").is_file()
    assert (tmp_path / "geom" / "model.stl").is_file()
    assert (tmp_path / "geom" / "model.vtk").is_file()
    assert (tmp_path / "geom" / "model.json").is_file()
    glb = tmp_path / "geom" / "model.glb"
    assert glb.is_file() and glb.read_bytes()[:4] == b"glTF"
    assert summary["files"]["glb"].endswith("model.glb")
    return summary["files"]["radioss_inc"]


def test_engine_name_is_openmp_not_mpi():
    assert engine_binary_name() == OPENMP_ENGINE
    assert MPI_ENGINE not in engine_binary_name()
    env = solver_environment(4)
    assert env["OMP_NUM_THREADS"] == "4"
    assert env["OPENRADIOSS_ENGINE"] == OPENMP_ENGINE
    assert env["OMP_STACKSIZE"]


def test_generate_load_deck_roundtrip(tmp_path: Path):
    inc = _coarse_include(tmp_path)
    spec = DeckSpec(
        name="fork45_steel",
        material="steel",
        mode="load",
        out_dir=str(tmp_path / "runs"),
        mesh_path=inc,
        end_time_ms=5.0,
        ramp_ms=4.0,
        load_kN=0.4413,
        stop_displacement_mm=40,
        nanim=4,
        nth=10,
        zshift=0.0,
    )
    decks = generate_decks(spec)
    starter = Path(decks.starter_path).read_text()
    engine = Path(decks.engine_path).read_text()
    assert "/BEGIN" in starter and "fork45_steel" in starter
    assert "/MAT/PLAS_JOHNS/1" in starter
    assert "/CLOAD/1" in starter
    assert "/RWALL/PLANE/1" in starter
    assert "/BRICK/1" in starter
    assert "/RUN/fork45_steel/1" in engine
    assert "engine_linux64_gf_ompi" not in engine


def test_generate_displacement_deck(tmp_path: Path):
    inc = _coarse_include(tmp_path)
    spec = DeckSpec(
        name="fork_aluminum",
        material="aluminum",
        mode="displacement",
        out_dir=str(tmp_path / "runs"),
        mesh_path=inc,
        velocity_mm_ms=0.25,
        end_time_ms=2.0,
        ramp_ms=0.5,
    )
    decks = generate_decks(spec)
    text = Path(decks.starter_path).read_text()
    assert "/IMPVEL/1" in text
    assert "/CLOAD/1" not in text


def test_mock_run_uses_process_run_contract(tmp_path: Path):
    run = tmp_path / "run"
    write_mock_outputs(str(run), "fork45_steel", threads=2, stop_displacement_mm=40)
    metrics = process_run(str(run), str(tmp_path / "out"), video=False)
    assert "peak_force_N" in metrics and "max_von_mises_Pa" in metrics
    assert "max_plastic_strain" in metrics and "energy_balance_ok" in metrics
    rows = metrics_to_rows(metrics, "sim-1")
    assert any(row["name"] == "peak_force_N" and row["unit"] == "N" for row in rows)
    manifest = artifact_manifest(str(tmp_path / "out"))
    keys = {item["s3_key_suffix"] for item in manifest}
    assert "metrics.json" in keys and "summary.json" in keys
