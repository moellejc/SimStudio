import json, os
import numpy as np
import pytest
from PIL import Image, ImageDraw
from scipy import ndimage
from geometry import HexMesh, extrude_columns, build_fork, image_to_mesh, stl_to_mesh

FORK_PHOTO = "/home/box/agent-data/agents/de9f1c97-95c9-4372-a06f-fb8f3841fabc/attachments/9a5f75d403856ddac48d70225391cae7469d8aa1b8930aeec9fb69f8cb629b51.jpg"


def unit_cube():
    n = np.array([[0,0,0],[1,0,0],[1,1,0],[0,1,0],[0,0,1],[1,0,1],[1,1,1],[0,1,1]], float)
    return HexMesh(n, np.arange(8)[None])


def connected(m):
    # union-find over shared nodes
    parent = np.arange(len(m.nodes))
    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]; a = parent[a]
        return a
    for e in m.elems:
        r = find(e[0])
        for v in e[1:]:
            parent[find(v)] = r
    return len({find(e[0]) for e in m.elems}) == 1


def test_cube_volume_and_flip():
    m = unit_cube()
    assert m.element_volumes()[0] == pytest.approx(1.0)
    m.elems = m.elems[:, [4,5,6,7,0,1,2,3]]
    assert m.element_volumes()[0] < 0 and m.fix_orientation() == 1
    assert m.element_volumes()[0] == pytest.approx(1.0)
    assert len(m.boundary_quads()) == 6


def test_extrude_block_volume():
    mask = np.ones((10, 4), bool)
    m = extrude_columns(mask, np.linspace(0, 10, 11), np.linspace(0, 4, 5), lambda X, Y: 0*X, lambda X, Y: 0*X + 2, 3)
    assert m.stats()["volume_mm3"] == pytest.approx(80.0)
    assert m.stats()["inverted_bricks"] == 0 and len(m.boundary_quads()) == 2*(30+12+40)


@pytest.mark.parametrize("profile", ["realistic", "uniform"])
def test_fork(profile):
    m = build_fork(profile, nz=3)
    s = m.stats()
    assert s["inverted_bricks"] == 0 and connected(m)
    assert s["bbox_min_mm"][2] == pytest.approx(1.0) and s["bbox_max_mm"][2] == pytest.approx(191.0)
    assert 5500 < s["volume_mm3"] < 7500  # ~45-60 g in steel


def test_radioss_inc_format(tmp_path):
    m = unit_cube(); p = tmp_path / "c.inc"; m.write_radioss_inc(str(p))
    lines = p.read_text().splitlines()
    assert lines[1] == "/NODE" and lines[10] == "/BRICK/1"
    assert len(lines[2]) == 70 and len(lines[11]) == 90
    assert lines[11].split() == ["1"] + [str(i) for i in range(1, 9)]


def test_image_synthetic_rectangle(tmp_path):
    img = Image.new("RGB", (400, 200), "white"); ImageDraw.Draw(img).rectangle([50, 80, 349, 119], fill="black")
    p = tmp_path / "bar.png"; img.save(p)
    m = image_to_mesh(str(p), length_mm=150, thickness_mm=2.0, cell_mm=1.0, nz=2, upright=False)
    s = m.stats()
    # 300x40 px bar -> 150 x 20 mm x 2 mm = 6000 mm3
    assert s["volume_mm3"] == pytest.approx(6000, rel=0.06)
    assert s["inverted_bricks"] == 0 and connected(m)


@pytest.mark.skipif(not os.path.exists(FORK_PHOTO), reason="fork photo not on this machine")
def test_image_fork_photo(tmp_path):
    m = image_to_mesh(FORK_PHOTO, length_mm=190, thickness_mm=lambda s: 1.5 + 1.0*s, nz=3)
    s = m.stats()
    assert s["inverted_bricks"] == 0 and connected(m)
    assert s["bbox_max_mm"][2] - s["bbox_min_mm"][2] == pytest.approx(190, abs=2)
    out = m.export_all(str(tmp_path), title="fork from photo")
    assert all(os.path.exists(v) for v in out["files"].values())


def test_stl_roundtrip(tmp_path):
    mask = np.ones((20, 10), bool)
    box = extrude_columns(mask, np.linspace(0, 20, 21), np.linspace(0, 10, 11), lambda X, Y: 0*X, lambda X, Y: 0*X + 5, 5)
    p = tmp_path / "box.stl"; box.write_stl(str(p))
    m = stl_to_mesh(str(p), voxel_mm=1.0, place_on_floor=True)
    assert m.stats()["volume_mm3"] == pytest.approx(1000, rel=0.02)
    assert m.stats()["bbox_min_mm"][2] == pytest.approx(1.0) and connected(m)


def test_glb(tmp_path):
    import struct, json
    m = build_fork("realistic", nz=2); p = tmp_path / "f.glb"; m.write_glb(str(p))
    b = p.read_bytes()
    magic, ver, length = struct.unpack("<III", b[:12])
    assert magic == 0x46546C67 and ver == 2 and length == len(b)
    jl = struct.unpack("<I", b[12:16])[0]; gl = json.loads(b[20:20 + jl])
    acc = gl["accessors"]
    assert acc[2]["count"] % 3 == 0 and acc[0]["count"] > 1000
    assert abs(acc[0]["max"][1] - 0.191) < 1e-3  # 191 mm tall, Y-up, meters
