"""Uploaded-deck meshes: /BRICK stays a HexMesh; /SHELL and /SH3N decks build a surface mesh and exports."""
import json
import os

import numpy as np
import pytest

from simstudio.deck_mesh import DeckMesh, mesh_from_deck, skip_missing_includes

SHELL_DECK = """#RADIOSS STARTER
/BEGIN
shells
      2019         0
                  kg                  mm                  ms
                  kg                  mm                  ms
/NODE
1 0 0 0
2 10 0 0
3 10 10 0
4 0 10 0
5 20 0 0
6 20 10 5
/SHELL/1
1 1 2 3 4
2 2 5 6 3
#  a degenerate quad (n3 == n4) is a triangle
3 2 5 6 6
/SH3N/1
4 3 6 4
/PART/1
plate
1 1
/END
"""

FIXED_DECK = (
    "/NODE\n"
    + "".join(f"{i:10d}{x:20.6f}{y:20.6f}{0.0:20.6f}\n" for i, (x, y) in enumerate([(0, 0), (1, 0), (1, 1)], 1))
    + "/SH3N/2\n"
    + f"{7:10d}{1:10d}{2:10d}{3:10d}\n"
)

BRICK_DECK = "/NODE\n" + "".join(
    f"{i + 1} {x} {y} {z}\n"
    for i, (x, y, z) in enumerate([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)])
) + "/BRICK/1\n1 1 2 3 4 5 6 7 8\n"


def test_shell_and_sh3n_deck():
    mesh = mesh_from_deck(SHELL_DECK)
    assert isinstance(mesh, DeckMesh)
    assert len(mesh.nodes) == 6
    assert mesh.quads.shape == (2, 4)
    assert mesh.tris.shape == (2, 3)
    stats = mesh.stats()
    assert stats["shells"] == 4 and stats["quad_shells"] == 2 and stats["tri_shells"] == 2
    assert stats["bricks"] == 0
    np.testing.assert_allclose(stats["bbox_max_mm"], [20, 10, 5])
    # 2 quads -> 4 triangles, plus 2 triangles
    assert len(mesh.surface_tris()) == 6


def test_meter_deck_is_converted_to_mm():
    deck = SHELL_DECK.replace("                  kg                  mm                  ms\n                  kg                  mm                  ms",
                              "                  kg                   m                   s\n                  kg                   m                   s")
    assert deck != SHELL_DECK
    mesh = mesh_from_deck(deck)
    np.testing.assert_allclose(mesh.stats()["bbox_max_mm"], [20000, 10000, 5000])
    assert mesh.meta["deck_length_unit"] == "m"


def test_fixed_width_sh3n():
    mesh = mesh_from_deck(FIXED_DECK)
    assert isinstance(mesh, DeckMesh)
    assert mesh.tris.shape == (1, 3) and len(mesh.nodes) == 3


def test_brick_deck_is_unchanged_hexmesh():
    mesh = mesh_from_deck(BRICK_DECK)
    assert mesh is not None and not isinstance(mesh, DeckMesh)
    assert type(mesh).__name__ == "HexMesh"


def test_no_elements_returns_none():
    assert mesh_from_deck("/NODE\n1 0 0 0\n") is None


def test_shell_exports(tmp_path):
    mesh = mesh_from_deck(SHELL_DECK)
    summary = mesh.export_all(str(tmp_path), stem="model", title="shells")
    assert summary["shells"] == 4
    for path in summary["files"].values():
        assert os.path.getsize(path) > 0
    glb = summary["files"]["glb"]
    data = open(glb, "rb").read()
    assert data[:4] == b"glTF"
    n = int.from_bytes(data[12:16], "little")
    gl = json.loads(data[20:20 + n])
    pos = gl["accessors"][gl["meshes"][0]["primitives"][0]["attributes"]["POSITION"]]
    # meters, Y-up: source (x, y, z) mm -> (x, z, -y) / 1000
    np.testing.assert_allclose(pos["max"], [0.02, 0.005, 0.0], atol=1e-9)
    np.testing.assert_allclose(pos["min"], [0.0, 0.0, -0.01], atol=1e-9)


def test_skip_missing_includes(tmp_path):
    (tmp_path / "here.inc").write_text("# ok\n")
    deck = tmp_path / "A_0000.rad"
    deck.write_text("/BEGIN\n#include here.inc\n#include missing.inc\n/END\n")
    lines = []
    skipped = skip_missing_includes(str(deck), lines.append)
    text = deck.read_text()
    assert skipped == ["missing.inc"]
    assert "#include here.inc" in text
    assert "#include missing.inc" not in text.replace("skipped: missing.inc", "")
    assert lines


pv = pytest.importorskip("pyvista")


def test_gltf_export_mixed_quad_and_tri_shells(tmp_path):
    """post/gltf.frames_to_glb accepts SH3N triangles alongside SHELL quads (ELEMENT_ID > 0)."""
    from post.gltf import frames_to_glb, read_glb

    pts = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [2, 0, 0]], float)
    cells = np.hstack([[4, 0, 1, 2, 3], [3, 1, 4, 2]])
    types = np.array([pv.CellType.QUAD, pv.CellType.TRIANGLE], np.uint8)
    files = []
    for f in range(2):
        g = pv.UnstructuredGrid(cells, types, pts + [0, 0, 0.1 * f])
        g.cell_data["2DELEM_Von_Mises"] = np.array([1.0 + f, 2.0 + f], np.float32) * 1e6
        g.cell_data["EROSION_STATUS"] = np.ones(2, np.float32)
        g.cell_data["ELEMENT_ID"] = np.array([1, 2], np.int32)
        files.append(str(tmp_path / f"s_{f:03d}.vtk"))
        g.save(files[-1], binary=False)
    out = str(tmp_path / "f.glb")
    man = frames_to_glb(files, out, times=[0.0, 1e-3])
    gl, acc = read_glb(out)
    prim = gl["meshes"][0]["primitives"][0]
    assert man["n_structural_cells"] == 2
    assert len(acc[prim["indices"]].reshape(-1, 3)) == 3  # quad -> 2 tris, + 1 tri
    assert len(prim["targets"]) == 2
