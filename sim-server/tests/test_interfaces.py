"""Contract tests for the geometry, runner, and post interfaces."""

from pathlib import Path

from geometry import MeshRequest, build_hex_mesh
from post import (
    STRESS_GPA_TO_PA,
    postprocess_run,
    stress_gpa_to_pa,
    wall_force_from_impulse,
)
from runner import OPENMP_ENGINE, MPI_ENGINE, DeckSpec, engine_binary_name, generate_decks, solver_environment
from runner.mock_run import write_mock_outputs


def test_mock_box_mesh_is_radioss_hex():
    mesh = build_hex_mesh(MeshRequest(kind="box", mock=True))
    assert mesh.include_text.startswith("#")
    assert "/NODE" in mesh.include_text
    assert "/BRICK/1" in mesh.include_text
    assert len(mesh.bricks) == 2
    ids = [n[0] for n in mesh.nodes]
    assert ids == list(range(1, len(ids) + 1))


def test_fork_mesh_coarse_has_bricks_and_stl():
    mesh = build_hex_mesh(MeshRequest(kind="fork", dx_mm=12, dy_mm=4, nz=1, length_mm=190))
    assert mesh.meta["implementation"] == "make_fork"
    assert mesh.meta["bricks"] == len(mesh.bricks) > 0
    assert mesh.include_text.count("/NODE") == 1
    assert mesh.stl_bytes and mesh.stl_bytes.startswith(b"fork")
    z = [n[3] for n in mesh.nodes]
    assert min(z) < 40 and max(z) > 150


def test_engine_name_is_openmp_not_mpi():
    assert engine_binary_name() == OPENMP_ENGINE
    assert MPI_ENGINE not in engine_binary_name()
    env = solver_environment(4)
    assert env["OMP_NUM_THREADS"] == "4"
    assert env["OPENRADIOSS_ENGINE"] == OPENMP_ENGINE
    assert env["OMP_STACKSIZE"]


def test_generate_load_deck_roundtrip(tmp_path: Path):
    mesh = build_hex_mesh(MeshRequest(kind="fork", dx_mm=16, dy_mm=6, nz=1, length_mm=190))
    inc = tmp_path / "fork_mesh.inc"
    inc.write_text(mesh.include_text)
    spec = DeckSpec(
        name="fork45_steel",
        material="steel",
        mode="load",
        out_dir=str(tmp_path / "runs"),
        mesh_path=str(inc),
        end_time_ms=5.0,
        ramp_ms=4.0,
        load_kN=0.4413,
        stop_displacement_mm=40,
        nanim=4,
        nth=10,
    )
    decks = generate_decks(spec)
    starter = Path(decks.starter_path).read_text()
    engine = Path(decks.engine_path).read_text()
    assert "/BEGIN" in starter and "fork45_steel" in starter
    assert "/MAT/PLAS_JOHNS/1" in starter
    assert "/CLOAD/1" in starter
    assert "/RWALL/PLANE/1" in starter
    assert "/BRICK/1" in starter
    assert f"/RUN/fork45_steel/1" in engine
    assert "engine_linux64_gf_ompi" not in engine


def test_generate_displacement_deck(tmp_path: Path):
    mesh = build_hex_mesh(MeshRequest(kind="box", mock=True))
    # Displacement deck expects a fork-like include with a Z span. Use the coarse fork.
    mesh = build_hex_mesh(MeshRequest(kind="fork", dx_mm=16, dy_mm=6, nz=1))
    inc = tmp_path / "fork_mesh.inc"
    inc.write_text(mesh.include_text)
    spec = DeckSpec(
        name="fork_aluminum",
        material="aluminum",
        mode="displacement",
        out_dir=str(tmp_path / "runs"),
        mesh_path=str(inc),
        velocity_mm_ms=0.25,
        end_time_ms=2.0,
        ramp_ms=0.5,
    )
    decks = generate_decks(spec)
    text = Path(decks.starter_path).read_text()
    assert "/IMPVEL/1" in text
    assert "/CLOAD/1" not in text


def test_impulse_derivative_and_stress_units():
    # impulse grows 0.001 kN*ms per ms -> slope 0.001 kN = 1 N, negated by the helper
    force = wall_force_from_impulse([0, 10, 20], [0, 0.01, 0.02])
    assert abs(force[1] - (-1.0)) < 1e-9
    assert stress_gpa_to_pa(0.00352) == 0.00352 * STRESS_GPA_TO_PA
    assert stress_gpa_to_pa(1.0) == 1.0e9


def test_mock_run_summary_has_postgres_scalars(tmp_path: Path):
    write_mock_outputs(str(tmp_path), "fork45_steel", threads=2, stop_displacement_mm=40)
    summary = postprocess_run(str(tmp_path), "fork45_steel")
    assert summary["peak_force_n"] > 0
    assert summary["max_von_mises_pa"] == 0.00352 * 1e9
    assert summary["max_plastic_strain"] == 0.004
    assert summary["energy_balance_error_pct"] is not None
    assert (tmp_path / "post" / "summary.json").is_file()
    assert (tmp_path / "start.ts").is_file() and (tmp_path / "end.ts").is_file()
