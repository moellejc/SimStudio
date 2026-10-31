"""Headless three.js r147 check of an animated GLB: python samples/verify_headless.py [glb] [chrome]
Serves post/ on localhost, renders verify.html at the first/middle/last frame in headless Chrome (SwiftShader),
asserts the active morph target, shape change and recolouring, and writes samples/verify.png (contact sheet)."""
import functools, http.server, json, os, re, subprocess, sys, threading
import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def run(glb="sample.glb", chrome=os.environ.get("CHROME", "google-chrome")):
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    h = functools.partial(Quiet, directory=ROOT)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), h)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    n = json.load(open(os.path.join(HERE, "sample.manifest.json")))["n_frames"] if glb == "sample.glb" else None
    frames = [0, (n - 1) // 2 if n else 12, -1]
    base = [chrome, "--headless=new", "--no-sandbox", "--use-angle=swiftshader", "--enable-unsafe-swiftshader",
            "--window-size=800,600", "--virtual-time-budget=8000"]
    res, shots = [], []
    try:
        for f in frames:
            url = f"http://127.0.0.1:{srv.server_port}/samples/verify.html?glb={glb}&frame={f}"
            png = os.path.join(HERE, f"_frame_{f}.png")
            # headless virtual time does not always wait for the GLB fetch -> retry until the page has rendered
            for _ in range(8):
                subprocess.run(base + [f"--screenshot={png}", url], capture_output=True, timeout=120, check=True)
                im = np.asarray(Image.open(png).convert("RGB")).astype(int)
                if ((im.max(2) - im.min(2)) > 60).sum() > 500:     # coloured (jet) pixels present
                    break
            else:
                raise AssertionError("screenshot never showed the model")
            for _ in range(8):
                dom = subprocess.run(base + ["--dump-dom", url], capture_output=True, timeout=120, text=True).stdout
                m = re.search(r'id="result"[^>]*>(.*?)</pre>', dom, re.S)
                if m and m.group(1).strip():
                    break
            else:
                raise AssertionError("page did not report (load error?)")
            res.append(json.loads(m.group(1))); shots.append(png)
    finally:
        srv.shutdown()
    first, mid, last = res
    assert first["morph_targets"] == first["n_frames"]
    assert [r["active"] for r in res] == [r["frame"] for r in res], "animation did not select the frame's target"
    assert first["bbox_min"] != last["bbox_min"] or first["bbox_max"] != last["bbox_max"], "shape did not change"
    assert last["color_at_max"][0] > 0.5 and last["color_at_max"][2] == 0, "max not red"
    imgs = [np.asarray(Image.open(p).convert("RGB")) for p in shots]
    assert np.abs(imgs[0].astype(int) - imgs[2].astype(int)).sum() > 0, "screenshots identical"
    Image.fromarray(np.concatenate(imgs, 1)).save(os.path.join(HERE, "verify.png"))
    for p in shots:
        os.remove(p)
    return res


if __name__ == "__main__":
    for r in run(*sys.argv[1:]):
        print(json.dumps({k: r[k] for k in ("frame", "active", "anim_time", "load_N", "vm_max", "bbox_min", "bbox_max",
                                            "color_at_max", "color_at_min")}))
    print("OK ->", os.path.join(HERE, "verify.png"))
