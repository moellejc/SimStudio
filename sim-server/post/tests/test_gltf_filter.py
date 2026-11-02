"""frames_to_glb keeps only cells with ELEMENT_ID > 0 (drops rigid-wall lines, floor quads, helper cells)."""
import numpy as np
import pytest

pv = pytest.importorskip("pyvista")
pytest.importorskip("vtk")

from post.gltf import frames_to_glb, read_glb, jet


def _from_gltf(p, scale=1.0):
    p = np.asarray(p, float) / scale
    return np.stack([p[:, 0], -p[:, 2], p[:, 1]], 1)


def _frames(tmp_path, n_frames=2):
    """Two valid shells (x 0..2, z=0), a rigid-wall line (id 0, out to x=10), a floor quad (id -1, z=-5)
    and a helper quad (id 0, z=+5). The invalid cells carry extreme stress so leaking them would move the range."""
    pts = np.array([
        [0, 0, 0], [1, 0, 0], [2, 0, 0], [0, 1, 0], [1, 1, 0], [2, 1, 0],          # 0-5 shells
        [0, 0, 0.5], [10, 0, 0.5],                                                  # 6-7 line
        [-5, -5, -5], [5, -5, -5], [5, 5, -5], [-5, 5, -5],                         # 8-11 floor
        [0, 0, 5], [1, 0, 5], [1, 1, 5], [0, 1, 5],                                 # 12-15 helper
    ], float)
    cells = np.hstack([[4, 0, 1, 4, 3], [4, 1, 2, 5, 4], [2, 6, 7], [4, 8, 9, 10, 11], [4, 12, 13, 14, 15]])
    types = np.array([pv.CellType.QUAD, pv.CellType.QUAD, pv.CellType.LINE, pv.CellType.QUAD, pv.CellType.QUAD],
                     np.uint8)
    files = []
    for f in range(n_frames):
        g = pv.UnstructuredGrid(cells, types, pts)
        g.cell_data["2DELEM_Von_Mises"] = np.array([1.0 + f, 3.0 + f, 100.0, 50.0, 1e-3], np.float32) * 1e6
        g.cell_data["EROSION_STATUS"] = np.ones(5, np.float32)
        g.cell_data["ELEMENT_ID"] = np.array([11, 12, 0, -1, 0], np.int32)
        files.append(str(tmp_path / f"s_{f:03d}.vtk")); g.save(files[-1], binary=False)
    return files


def test_glb_keeps_only_positive_element_ids(tmp_path):
    files = _frames(tmp_path)
    out = str(tmp_path / "g" / "f.glb")
    man = frames_to_glb(files, out, times=[0.0, 1e-3])
    gl, acc = read_glb(out)
    p = gl["meshes"][0]["primitives"][0]

    # geometry: 2 quads -> 4 triangles, 8 vertices (unique per cell/point), nothing else
    assert man["n_structural_cells"] == 2
    tri = acc[p["indices"]].reshape(-1, 3)
    pos = acc[p["attributes"]["POSITION"]]
    assert len(tri) == 4 and man["n_triangles"] == 4
    assert len(pos) == 8 and man["n_vertices"] == 8
    assert tri.max() < len(pos)
    xyz = _from_gltf(pos)
    np.testing.assert_allclose(xyz.min(0), [0, 0, 0], atol=1e-6)
    np.testing.assert_allclose(xyz.max(0), [2, 1, 0], atol=1e-6)
    acc_pos = gl["accessors"][p["attributes"]["POSITION"]]
    np.testing.assert_allclose(_from_gltf(np.array([acc_pos["min"], acc_pos["max"]])).min(0), [0, 0, 0], atol=1e-6)

    # value range: point-averaged over valid shells only. Last frame: shells 2e6 / 4e6, shared edge 3e6.
    vr = man["von_mises_range"]
    assert vr == pytest.approx([1e6, 4e6], rel=1e-6)
    for k in range(2):
        v = acc[p["attributes"][f"_VON_MISES_{k:04d}"]]
        assert v.min() >= vr[0] * (1 - 1e-6) and v.max() <= vr[1] * (1 + 1e-6)
    vl = acc[p["attributes"]["_VON_MISES"]]
    assert sorted(set(np.round(vl / 1e6, 4))) == [2.0, 3.0, 4.0]
    col = acc[p["attributes"]["COLOR_0"]]
    np.testing.assert_allclose(col, jet((vl - vr[0]) / (vr[1] - vr[0])), atol=1e-6)


def test_all_cells_filtered_raises(tmp_path):
    g = pv.UnstructuredGrid(np.array([4, 0, 1, 2, 3]), np.array([pv.CellType.QUAD], np.uint8),
                            np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], float))
    g.cell_data["2DELEM_Von_Mises"] = np.array([1e6], np.float32)
    g.cell_data["ELEMENT_ID"] = np.array([0], np.int32)
    f = str(tmp_path / "z.vtk"); g.save(f, binary=False)
    with pytest.raises(ValueError, match="no structural cells"):
        frames_to_glb([f], str(tmp_path / "z.glb"))
