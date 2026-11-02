import glob, json, os, subprocess, sys
import numpy as np
import pandas as pd
import pytest
from post import (process_run, detect_buckling, analyze_time_history, compare_runs, build_viewer,
                  metrics_to_rows, artifact_manifest)
from post.process import _to_pa
from post.gltf import frames_to_glb, read_glb, jet, to_gltf_coords
from post._vendor import compare as cmp, postprocess as pp

DEMO = "/workspace/openradioss/results/demo"   # ball hitting a rigid wall, kg/m/s, velocity-controlled
SIM_SERVER = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def snapshot(d):
    return {f: (os.path.getsize(f), os.path.getmtime(f)) for f in glob.glob(os.path.join(d, "**"), recursive=True)}


@pytest.fixture(scope="session")
def demo(tmp_path_factory):
    if not os.path.isdir(DEMO):
        pytest.skip("ball-hits-wall demo is not on this machine")
    before = snapshot(DEMO)
    out = tmp_path_factory.mktemp("demo_post")
    m = process_run(DEMO, str(out))
    assert snapshot(DEMO) == before, "run folder must stay read-only"
    return out, m


# ------------------------------------------------------------------ demo fixture
def test_demo_outputs(demo):
    out, m = demo
    fj = json.load(open(out / "vtk" / "frames.json"))
    assert fj["stress_unit"] == "Pa" and len(fj["frames"]) == 26 == m["frame_count"]
    f0 = fj["frames"][1]
    assert set(f0) == {"file", "time", "load_time", "load_N"} and (out / "vtk" / f0["file"]).exists()
    times = [f["time"] for f in fj["frames"]]
    assert times == sorted(times) and times[-1] == pytest.approx(0.125, abs=1e-3)
    assert len(glob.glob(str(out / "frames" / "frame_*.png"))) == 26
    for ext in ("mp4", "gif"):
        assert os.path.getsize(out / f"BOULE1V4.{ext}") > 10_000
    assert json.load(open(out / "metrics.json")) == m


def test_demo_metrics(demo):
    out, m = demo
    import pyvista as pv
    vm = max(float(np.max(pv.read(str(f)).cell_data["2DELEM_Von_Mises"])) for f in (out / "vtk").glob("*.vtk"))
    assert m["max_von_mises_Pa"] == pytest.approx(vm) and m["max_von_mises_Pa"] == pytest.approx(377.94, rel=1e-3)
    assert m["max_plastic_strain"] == pytest.approx(0.7734, rel=1e-3)
    assert m["mode"] == "velocity" and m["buckling_force_N"] is None and m["disp_at_buckling_mm"] is None
    assert m["peak_force_kind"] == "floor reaction" and 1000 < m["peak_force_N"] < 20000
    assert m["ended_early"] is False and m["normal_termination"] is True
    assert m["energy_balance_ok"] is False and m["hourglass_warning"] is True   # demo has 12% hourglass energy
    assert 0 < m["ke_ie_ratio"] < 1 and m["added_mass_pct"] < 1e-6
    assert m["fracture_onset"] is None
    assert m["max_disp_mm"] == pytest.approx(1824.7, rel=1e-3)   # metres in the deck -> mm


def test_reaction_from_impulse(demo):
    out, m = demo
    df = pd.read_csv(glob.glob(str(out / "th" / "*.csv"))[0]); df.columns = [c.strip() for c in df.columns]
    t = df["time"].values
    imp = df[[c for c in df.columns if c.startswith("Rigid walls")][:3]].values
    raw = np.linalg.norm(np.column_stack([np.gradient(imp[:, j], t) for j in range(3)]), axis=1)
    assert m["peak_reaction_N"] == pytest.approx(cmp.smooth(raw, 5).max())
    assert m["peak_reaction_N"] < raw.max()   # impulse is differentiated, not read as force


def test_rows_and_manifest(demo):
    out, m = demo
    rows = metrics_to_rows(m, "sim-123")
    names = [r["name"] for r in rows]
    assert len(names) == len(set(names)) and all(r["sim_id"] == "sim-123" for r in rows)
    by = {r["name"]: r for r in rows}
    assert by["max_von_mises_Pa"]["unit"] == "Pa" and by["buckling_force_N"]["value"] is None
    assert by["ended_early"]["value"] == 0.0 and by["fracture_detected"]["value"] == 0.0
    assert all(r["value"] is None or isinstance(r["value"], float) for r in rows)
    man = artifact_manifest(out)
    keys = {x["s3_key_suffix"]: x for x in man}
    assert keys["metrics.json"]["content_type"] == "application/json"
    assert keys["BOULE1V4.mp4"]["content_type"] == "video/mp4"
    assert keys["vtk/frames.json"] and keys["vtk/BOULE1V4_001.vtk"]["content_type"] == "application/octet-stream"
    assert keys["frames/frame_001.png"]["content_type"] == "image/png"
    assert all(os.path.isfile(x["path"]) and not x["s3_key_suffix"].startswith("/") for x in man)


def test_viewer_from_processed_and_cli(demo, tmp_path):
    out, _ = demo
    html = build_viewer([str(out)], ["demo ball"], str(tmp_path / "v.html"))
    s = open(html).read()
    assert "REVISION" in s and "OrbitControls" in s and "<script src" not in s and '"label":"demo ball"' in s
    r = subprocess.run([sys.executable, "-m", "post", "viewer", DEMO, "--out", str(tmp_path / "cli.html")],
                       cwd=SIM_SERVER, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert os.path.getsize(tmp_path / "cli.html") > 600_000 and not os.path.exists(os.path.join(DEMO, "runs"))


def test_compare_demo(tmp_path):
    if not os.path.isdir(DEMO):
        pytest.skip("ball-hits-wall demo is not on this machine")
    before = snapshot(DEMO)
    c = compare_runs([DEMO], ["ball"], str(tmp_path), video=False)
    assert snapshot(DEMO) == before
    r = c["runs"][0]
    assert r["label"] == "ball" and r["mode"] == "velocity"
    assert r["table_row"]["max_von_mises_Pa"] == pytest.approx(377.94, rel=1e-3)
    for f in ("comparison.md", "comparison.csv", "force_displacement.png"):
        assert (tmp_path / f).exists()
    with pytest.raises(ValueError):
        compare_runs([DEMO], ["a,b"], str(tmp_path))


# ------------------------------------------------------------------ units
def test_to_pa_scales_only_stress(demo, tmp_path):
    out, _ = demo
    import pyvista as pv
    src = str(out / "vtk" / "BOULE1V4_026.vtk"); dst = str(tmp_path / "x.vtk")
    t = _to_pa(src, dst, 1e9)
    a, b = pv.read(src), pv.read(dst)
    assert t == pytest.approx(0.125012)
    assert np.allclose(b.cell_data["2DELEM_Von_Mises"], 1e9 * a.cell_data["2DELEM_Von_Mises"], rtol=1e-6)
    assert np.array_equal(b.cell_data["2DELEM_Plastic_Strain"], a.cell_data["2DELEM_Plastic_Strain"])


DECK = """#RADIOSS STARTER
/BEGIN
synthetic
      2026         0
                  kg                  mm                  ms
                  kg                  mm                  ms
/FUNCT/1
ramp
#                  X                   Y
                   0                   0
                  10                   1
                 100                   1
/GRNOD/NODE/12
loaded
         1         2
/CLOAD/1
load
#funct_IDT       Dir   skew_ID sensor_ID  grnod_ID                       Ascalex             Fscaley
         1         Z         0         0        12                             1                -0.5
/TH/RWALL/1
rigid wall force
#     var1
DEF
         1
/TH/NODE/2
pushed end node
#     var1
DEF
         1         0
/END
"""


def synth_run(tmp_path, fc_N=600.0, soft=20.0, reaction_drop=False, n=501):
    """Load ramps 0 -> 1000 N over 10 ms (2 nodes x 0.5 kN). Elastic k = 100 N/mm until fc_N, then soft x softer."""
    (tmp_path / "S_0000.rad").write_text(DECK)
    deck = cmp.parse_deck(str(tmp_path / "S_0000.rad"), str(tmp_path), "S")
    t = np.linspace(0, 10, n)
    F = 100.0 * t                                                  # N
    u = np.where(F < fc_N, F / 100.0, fc_N / 100.0 + soft * (F - fc_N) / 100.0)   # mm
    R = F.copy()
    if reaction_drop:
        R = np.where(F < fc_N, F, fc_N * 0.5)
    imp = np.concatenate([[0.0], np.cumsum(0.5 * (R[1:] + R[:-1]) * np.diff(t))]) / 1000.0   # kN*ms
    cols = {"time": t, "INTERNAL ENERGY": 1 + t, "KINETIC ENERGY": 0.01 * (1 + t), "PERCENTAGE ADDED MASS": 0.5 * t / 10}
    rw = "rigid wall force                         1 floor                                  var "
    for j, v in enumerate([np.zeros(n), np.zeros(n), -imp, np.zeros(n), np.zeros(n), np.zeros(n)]):
        cols[rw + str(42 + j)] = v
    nd = "pushed end node                          1                                        var "
    for j, v in enumerate([np.zeros(n), np.zeros(n), -u, np.zeros(n), np.zeros(n), np.zeros(n)]):
        cols[nd + str(48 + j)] = v
    cols["FORK                                     ERODED "] = np.where(t < 8, 4.0, 9.0)   # 4 rigid-cap cells at t=0
    return deck, pd.DataFrame(cols), t, F, u, R


def test_deck_units_mm_ms_kg(tmp_path):
    deck, *_ = synth_run(tmp_path)
    assert deck["force_to_N"] == pytest.approx(1000.0) and deck["stress_to_Pa"] == pytest.approx(1e9)
    assert cmp.curve_full_load(deck) == pytest.approx(1000.0) and cmp.ramp_end_time(deck) == pytest.approx(10.0)


# ------------------------------------------------------------------ buckling (synthetic)
def test_elastic_no_buckling():
    F = np.linspace(0, 1000, 400); u = F / 100.0
    r = detect_buckling(F, u, F.copy())
    assert r["detected"] is False and r["force_N"] is None and r["k0_N_per_length_unit"] == pytest.approx(100, rel=1e-3)


def test_stiffness_loss():
    F = np.linspace(0, 1000, 400); u = np.where(F < 600, F / 100, 6 + 20 * (F - 600) / 100)
    r = detect_buckling(F, u)
    assert r["detected"] and r["criterion"] == "stiffness_loss"
    assert r["force_N"] == pytest.approx(600, rel=0.03) and r["disp"] == pytest.approx(6.0, rel=0.05)


def test_mild_softening_below_kfrac_threshold_not_flagged():
    F = np.linspace(0, 1000, 400); u = np.where(F < 600, F / 100, 6 + 5 * (F - 600) / 100)   # k drops to 20% > 15%
    assert detect_buckling(F, u)["detected"] is False
    assert detect_buckling(F, u, kfrac=0.25)["detected"] is True


def test_reaction_stops_tracking():
    F = np.linspace(0, 1000, 400); u = F / 100; R = np.where(F < 700, F, 300.0)
    r = detect_buckling(F, u, R)
    assert r["detected"] and r["criterion"] == "reaction_stops_tracking"
    assert r["force_N"] == pytest.approx(700, rel=0.03)


def test_short_glitch_needs_sustain():
    F = np.linspace(0, 1000, 400); u = F / 100; R = F.copy(); R[250:252] = 0.0   # 2 bad samples
    assert detect_buckling(F, u, R, smooth=1)["detected"] is False
    assert detect_buckling(F, u, R, smooth=1, sustain=2)["detected"] is True


def test_analyze_time_history_force_control(tmp_path):
    deck, df, t, F, u, R = synth_run(tmp_path)
    a = analyze_time_history(df, deck)
    assert a["mode"] == "force"
    assert np.allclose(a["applied_N"], F)                               # kN -> N, |vector sum| of /CLOAD
    assert np.allclose(a["disp"], u)                                    # -DZ along the -Z load
    mid = slice(20, -20)
    assert np.allclose(a["reaction_N"][mid], F[mid], rtol=0.02, atol=2)   # d(impulse)/dt, kN -> N
    inst = a["instability"]
    assert inst["detected"] and inst["criterion"] == "stiffness_loss" and inst["force_N"] == pytest.approx(600, rel=0.03)
    assert a["fracture_th"]["time_solver"] == pytest.approx(8.0, abs=0.03)   # not t=0 (rigid cap baseline)
    assert a["fracture_th"]["eroded_elements"] == 5
    assert a["added_mass_pct"] == pytest.approx(0.5) and a["ke_ie_ratio"] == pytest.approx(0.01)


def test_analyze_time_history_reaction_criterion(tmp_path):
    deck, df, *_ = synth_run(tmp_path, fc_N=2000.0, reaction_drop=False)
    assert analyze_time_history(df, deck)["instability"]["detected"] is False
    deck, df, *_ = synth_run(tmp_path, fc_N=650.0, soft=1.0, reaction_drop=True)
    inst = analyze_time_history(df, deck)["instability"]
    assert inst["detected"] and inst["criterion"] == "reaction_stops_tracking"
    assert inst["force_N"] == pytest.approx(650, rel=0.03)


def test_ramp_hold_plateau_is_not_buckling():
    # cubic ramp to 441 N at t=300 then hold; displacement lags (dynamic creep) once the load holds
    t = np.linspace(0, 400, 8000)
    F = 441.0 * np.minimum(t / 300, 1) ** 3
    u = F / 4000 + np.where(t > 300, 0.004 * (1 - np.exp(-(t - 300) / 5)), 0)
    r = detect_buckling(F, u, F.copy())
    assert r["detected"] is False
    assert detect_buckling(F, u, plateau_frac=None)["detected"] is True   # plain compare.py behaviour


# ---------------------------------------------------------------- animated GLB (morph targets)
def _prim(gl):
    return gl["meshes"][0]["primitives"][0]


def test_demo_glb_morph_animation(demo):
    out, m = demo
    glb = os.path.join(out, m["glb"])
    assert m["glb"] == f"gltf/{m['name']}.glb" and os.path.isfile(glb)
    gl, acc = read_glb(glb)
    p, ex = _prim(gl), gl["meshes"][0]["extras"]
    n = m["frame_count"]
    assert len(p["targets"]) == n == ex["n_frames"] == len(ex["frames"]) == len(gl["meshes"][0]["weights"])
    assert "_VON_MISES" in p["attributes"] and "_PLASTIC_STRAIN" in p["attributes"]
    for k in range(n):
        assert f"_VON_MISES_{k:04d}" in p["attributes"] and f"_PLASTIC_STRAIN_{k:04d}" in p["attributes"]
    np.testing.assert_array_equal(acc[p["attributes"]["_VON_MISES"]], acc[p["attributes"][f"_VON_MISES_{n-1:04d}"]])
    assert acc[p["attributes"]["_VON_MISES"]].dtype == np.float32
    # step animation over load_time (s), one-hot weights
    s = gl["animations"][0]["samplers"][0]
    assert s["interpolation"] == "STEP" and gl["animations"][0]["channels"][0]["target"]["path"] == "weights"
    t, w = acc[s["input"]], acc[s["output"]].reshape(n, n)
    assert np.all(np.diff(t) > 0) and ex["time_axis"] == "load_time (s)"
    np.testing.assert_allclose(t, [f["load_time"] for f in ex["frames"]], rtol=1e-6)
    np.testing.assert_array_equal(w, np.eye(n))
    assert np.abs(acc[p["targets"][0]["POSITION"]]).max() == 0 and np.abs(acc[p["targets"][-1]["POSITION"]]).max() > 0
    # one surface: indices refer to frame-1 vertices only; vertices duplicated per element
    assert ex["n_vertices"] == len(acc[p["attributes"]["POSITION"]]) and ex["n_structural_cells"] == 32
    # metadata
    assert ex["stress_unit"] == "Pa" and ex["colormap"] == "jet" and ex["color_frame"] == n - 1
    assert ex["frames"][-1]["load_N"] > 0
    man = json.load(open(os.path.join(out, "gltf", "manifest.json")))
    assert man["glb"] == os.path.basename(glb) and man["von_mises_range"] == ex["von_mises_range"]
    assert {x["s3_key_suffix"]: x["content_type"] for x in artifact_manifest(out)}[m["glb"]] == "model/gltf-binary"


def test_demo_glb_colors_red_max_blue_min(demo):
    out, m = demo
    gl, acc = read_glb(os.path.join(out, m["glb"]))
    p, ex = _prim(gl), gl["meshes"][0]["extras"]
    col, vm = acc[p["attributes"]["COLOR_0"]], acc[p["attributes"]["_VON_MISES"]]
    vmin, vmax = ex["von_mises_range"]
    all_vm = np.concatenate([acc[p["attributes"][a]] for a in ex["von_mises_attributes"]])
    assert vmin == all_vm.min() and vmax == all_vm.max()          # one global range over all frames
    np.testing.assert_allclose(col, jet((vm - vmin) / (vmax - vmin)), atol=1e-6)   # COLOR_0 = last frame
    np.testing.assert_allclose(jet(1.0), [0.5, 0, 0]); np.testing.assert_allclose(jet(0.0), [0, 0, 0.5])
    hot = col[np.argmax(vm)]
    assert hot[0] > 0.5 and hot[2] == 0                            # red end at the max-stress vertex
    # global minimum (frame 1, unstressed) is pure blue
    v0 = acc[p["attributes"]["_VON_MISES_0000"]]
    c0 = jet((v0 - vmin) / (vmax - vmin))[np.argmin(v0)]
    assert c0[2] >= 0.5 and c0[0] == 0 and c0[1] == 0


def _hex_frames(tmp_path, n_frames=3, erode_at=2):
    """3 hexes in a row (x = 0..3) + a rigid cap quad inactive from t=0. Hex 1 (middle) erodes at frame erode_at."""
    import pyvista as pv
    xs = np.arange(4.0)
    pts = np.array([[x, y, z] for z in (0.0, 1.0) for y in (0.0, 1.0) for x in xs])
    pid = lambda i, j, k: k * 8 + j * 4 + i
    hexes = [[pid(i, 0, 0), pid(i + 1, 0, 0), pid(i + 1, 1, 0), pid(i, 1, 0),
              pid(i, 0, 1), pid(i + 1, 0, 1), pid(i + 1, 1, 1), pid(i, 1, 1)] for i in range(3)]
    cap_pts = np.array([[0, 0, 2.0], [3, 0, 2.0], [3, 1, 2.0], [0, 1, 2.0]])
    allp = np.vstack([pts, cap_pts])
    cells = np.hstack([np.r_[8, h] for h in hexes] + [np.r_[4, 16, 17, 18, 19]])
    types = np.array([pv.CellType.HEXAHEDRON] * 3 + [pv.CellType.QUAD], np.uint8)
    files = []
    for f in range(n_frames):
        g = pv.UnstructuredGrid(cells, types, allp + np.array([0, 0, -0.1 * f]))
        g.cell_data["3DELEM_Von_Mises"] = np.array([1.0 + f, 5.0 + f, 2.0 + f, 9.0], np.float32) * 1e6
        es = np.array([1, 0 if f >= erode_at else 1, 1, 0], np.float32)   # cap: 0 from t=0 (rigid/inactive)
        g.cell_data["EROSION_STATUS"] = es
        files.append(str(tmp_path / f"h_{f:03d}.vtk")); g.save(files[-1], binary=False)
    return files


def _from_gltf(p, scale):
    """Inverse of (x, z, -y) * scale, back to model coordinates."""
    p = np.asarray(p, float) / scale
    return np.stack([p[:, 0], -p[:, 2], p[:, 1]], 1)


def test_glb_eroded_faces_collapse(tmp_path):
    files = _hex_frames(tmp_path)
    man = frames_to_glb(files, str(tmp_path / "g" / "e.glb"), times=[0, 1e-3, 2e-3], loads=[0, 10, 20], length_to_m=1e-3)
    gl, acc = read_glb(str(tmp_path / "g" / "e.glb"))
    p = _prim(gl)
    assert man["n_structural_cells"] == 3 and man["inactive_at_t0_skipped"] == 1   # cap excluded
    assert man["time_axis"] == "solver time" and [f["eroded"] for f in man["frames"]] == [0, 0, 1]
    base = _from_gltf(acc[p["attributes"]["POSITION"]], 1e-3)
    assert base[:, 2].max() <= 1.0 + 1e-6                # no cap vertices (z=2) in the surface
    tri = acc[p["indices"]].reshape(-1, 3)
    # outer surface of the 3-hex bar: 14 quads = 28 triangles
    assert len(tri) == 28 and len(base) == 24             # 8 surface nodes per hex, duplicated per element
    pos = [_from_gltf(acc[p["attributes"]["POSITION"]] + acc[t["POSITION"]], 1e-3) for t in p["targets"]]
    mid = (base[:, 0] >= 1 - 1e-6) & (base[:, 0] <= 2 + 1e-6)
    for k in (0, 1):   # alive: rigid translation only
        np.testing.assert_allclose(pos[k] - base, np.tile([0, 0, -0.1 * k], (len(base), 1)), atol=1e-4)
    last = pos[2]
    moved = np.abs(last - (base + [0, 0, -0.2])).max(1) > 1e-4
    collapsed = last[moved]
    assert moved.sum() >= 4
    np.testing.assert_allclose(collapsed, np.tile(collapsed.mean(0), (len(collapsed), 1)), atol=1e-4)  # one point
    np.testing.assert_allclose(collapsed.mean(0), [1.5, 0.5, 0.3], atol=1e-4)   # the middle hex's centroid
    # its triangles are degenerate; the neighbours' triangles (which share nodes x=1,x=2) are intact
    area = lambda P: 0.5 * np.linalg.norm(np.cross(P[tri[:, 1]] - P[tri[:, 0]], P[tri[:, 2]] - P[tri[:, 0]]), axis=1)
    a = area(last)
    dead_tri = moved[tri].all(1)
    assert dead_tri.sum() == 8 and np.all(a[dead_tri] < 1e-6) and np.all(a[~dead_tri] > 0.4)
    assert np.all(mid[moved])
    # VM averaged to nodes over live cells only: after erosion the middle hex no longer contributes
    vm2 = acc[p["attributes"]["_VON_MISES_0002"]]
    assert vm2[~moved].max() <= 4e6 + 1 and acc[p["attributes"]["_VON_MISES_0001"]].max() > 4e6
    assert "_PLASTIC_STRAIN" not in p["attributes"]


def test_glb_coordinates_metres_y_up(tmp_path):
    files = _hex_frames(tmp_path)          # model in mm, Z-up; frames push along -Z by 0.1 mm per frame
    man = frames_to_glb(files, str(tmp_path / "c.glb"), length_to_m=1e-3)
    gl, acc = read_glb(str(tmp_path / "c.glb"))
    p, ex = _prim(gl), gl["meshes"][0]["extras"]
    assert (ex["length_unit"], ex["up_axis"], ex["source_to_gltf"], ex["scale"]) == ("m", "Y", "(x,z,-y)*scale", 1e-3)
    assert man["scale"] == 1e-3 and man["length_unit"] == "m"
    P = acc[p["attributes"]["POSITION"]]
    # known node: model (3, 1, 1) mm -> glTF (0.003, 0.001, -0.001) m; model (3, 0, 0) -> (0.003, 0, 0)
    for src, dst in (((3, 1, 1), (0.003, 0.001, -0.001)), ((3, 0, 0), (0.003, 0, 0)), ((0, 1, 0), (0, 0, -0.001))):
        assert np.min(np.linalg.norm(P - np.array(dst), axis=1)) < 1e-9, (src, dst)
    np.testing.assert_allclose(to_gltf_coords([[3, 1, 1]], 1e-3), [[0.003, 0.001, -0.001]])
    # solver -Z push -> three.js -Y; no x/z drift
    d = acc[p["targets"][1]["POSITION"]]          # frame 1: no erosion yet
    np.testing.assert_allclose(d, np.tile([0, -0.1e-3, 0], (len(d), 1)), atol=1e-9)
    # proper rotation: outward normals of the closed surface still point outwards (positive signed volume)
    tri = acc[p["indices"]].reshape(-1, 3).astype(int)
    vol = np.einsum("ij,ij->i", P[tri[:, 0]], np.cross(P[tri[:, 1]], P[tri[:, 2]])).sum() / 6
    frames_to_glb(files, str(tmp_path / "raw.glb"), manifest=False)   # same mesh at scale 1 for the sign
    gl2, acc2 = read_glb(str(tmp_path / "raw.glb"))
    P2 = acc2[_prim(gl2)["attributes"]["POSITION"]]
    vol2 = np.einsum("ij,ij->i", P2[tri[:, 0]], np.cross(P2[tri[:, 1]], P2[tri[:, 2]])).sum() / 6
    assert np.sign(vol) == np.sign(vol2) != 0 and abs(abs(vol) - 3e-9) < 1e-12   # 3 mm^3 = 3e-9 m^3


def test_glb_overlaps_geometry_model_glb(tmp_path):
    """A geometry/ fork mesh run through frames_to_glb (as if it were a solver frame in mm) lands on geometry's model.glb."""
    from geometry.fork import build_fork
    fm = build_fork("realistic", nz=2, dx=2.0, dy=1.0)
    fm.write_glb(str(tmp_path / "model.glb"))
    fm.write_vtk(str(tmp_path / "fork_000.vtk"), cell_data={"3DELEM_Von_Mises": np.ones(len(fm.elems))})
    frames_to_glb([str(tmp_path / "fork_000.vtk")], str(tmp_path / "res.glb"), length_to_m=1e-3)
    ga, aa = read_glb(str(tmp_path / "model.glb"))
    gb, ab = read_glb(str(tmp_path / "res.glb"))
    pa = ga["accessors"][ga["meshes"][0]["primitives"][0]["attributes"]["POSITION"]]
    pb = gb["accessors"][_prim(gb)["attributes"]["POSITION"]]
    np.testing.assert_allclose(pb["min"], pa["min"], atol=1e-6)
    np.testing.assert_allclose(pb["max"], pa["max"], atol=1e-6)
    assert pa["min"][1] > 0 and pa["max"][1] > 0.15        # standing on the floor, length along +Y (up)

SAMPLES = os.path.join(SIM_SERVER, "post", "samples")


def test_sample_glb_for_web_team():
    gl, acc = read_glb(os.path.join(SAMPLES, "sample.glb"))
    ex = gl["meshes"][0]["extras"]
    assert len(_prim(gl)["targets"]) == ex["n_frames"] > 1 and os.path.getsize(os.path.join(SAMPLES, "sample.glb")) < 2e6


@pytest.mark.skipif(not __import__("shutil").which("google-chrome"), reason="needs google-chrome for the three.js check")
def test_sample_animates_and_recolors_in_threejs():
    sys.path.insert(0, SAMPLES)
    import verify_headless
    first, mid, last = verify_headless.run()
    assert first["active"] == 0 and last["active"] == last["n_frames"] - 1
    assert first["color_at_max"] == [0, 0, 0.5] and last["color_at_max"][0] > 0.5   # recoloured per frame
