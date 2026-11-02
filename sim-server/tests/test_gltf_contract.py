"""Results .glb contract from post/samples/sample.glb.

process_run writes gltf/<name>.glb. The sample is that file for the ball demo:
one mesh, a morph target per frame, STEP weights, COLOR_0 for the last frame,
and per-frame _VON_MISES_####. Erosion collapses faces; there is no visibility
attribute. Coordinates match geometry model.glb (meters, Y-up).
"""

import json
import struct
from pathlib import Path

from post.gltf import read_glb
from simstudio.pipeline import attach_sample_surface

SAMPLE = Path(__file__).resolve().parents[1] / "post" / "samples" / "sample.glb"


def test_sample_glb_is_the_morph_contract():
    gltf, _arrays = read_glb(SAMPLE)
    mesh = gltf["meshes"][0]
    primitive = mesh["primitives"][0]
    extras = mesh["extras"]
    assert gltf["asset"]["version"] == "2.0"
    assert extras["format"] == "simstudio-morph-v1"
    assert extras["n_frames"] == 26
    assert extras["n_vertices"] == 128
    assert len(primitive["targets"]) == 26
    assert gltf["animations"][0]["samplers"][0]["interpolation"] == "STEP"
    assert extras["length_unit"] == "m"
    assert extras["up_axis"] == "Y"
    assert extras["source_to_gltf"] == "(x,z,-y)*scale"
    assert extras["scale"] == 1.0
    assert extras["von_mises_attributes"][0] == "_VON_MISES_0000"
    assert len(extras["von_mises_attributes"]) == 26
    assert "_VON_MISES_0000" in primitive["attributes"]
    assert "COLOR_0" in primitive["attributes"]
    assert "_EROSION" not in primitive["attributes"]
    assert "centroid" in extras["erosion"]
    manifest = json.loads(SAMPLE.with_name("sample.manifest.json").read_text())
    assert manifest["n_frames"] == 26
    assert manifest["up_axis"] == "Y"
    assert manifest["source_to_gltf"] == "(x,z,-y)*scale"
    assert manifest["glb"] == "sample.glb"
    magic = SAMPLE.read_bytes()[:4]
    assert magic == b"glTF"
    _length = struct.unpack_from("<I", SAMPLE.read_bytes(), 8)[0]
    assert _length == SAMPLE.stat().st_size


def test_pipeline_keeps_a_real_glb_and_falls_back_to_the_sample(tmp_path: Path):
    written = {"glb": "gltf/run.glb"}
    (tmp_path / "gltf").mkdir()
    (tmp_path / "gltf" / "run.glb").write_bytes(b"glTF-real")
    attach_sample_surface(written, tmp_path)
    assert written["glb"] == "gltf/run.glb"
    assert not (tmp_path / "gltf" / "sample.glb").exists()

    empty: dict = {"notes": []}
    attach_sample_surface(empty, tmp_path / "fallback")
    assert empty["glb"] == "gltf/sample.glb"
    data = (tmp_path / "fallback" / "gltf" / "sample.glb").read_bytes()
    assert data[:4] == b"glTF"
    assert len(data) == SAMPLE.stat().st_size
