"""Regenerate samples/sample.glb (+ sample.manifest.json) from the demo run:
/workspace/openradioss/venv/bin/python post/samples/make_sample.py [run_dir] [max_frames]   (run from sim-server/)"""
import json, os, shutil, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from post import process_run  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
run = sys.argv[1] if len(sys.argv) > 1 else "/workspace/openradioss/results/demo"
mf = int(sys.argv[2]) if len(sys.argv) > 2 else None
with tempfile.TemporaryDirectory() as td:
    m = process_run(run, td, video=False, gltf_max_frames=mf)
    shutil.copy(os.path.join(td, m["glb"]), os.path.join(HERE, "sample.glb"))
    man = json.load(open(os.path.join(td, "gltf", "manifest.json")))
    man["glb"] = "sample.glb"; man["source_run"] = run
    json.dump(man, open(os.path.join(HERE, "sample.manifest.json"), "w"), indent=2)
print(os.path.join(HERE, "sample.glb"), os.path.getsize(os.path.join(HERE, "sample.glb")), "bytes")
