"""CLI: python -m geometry {fork,image,stl} ... --out DIR  -> DIR/model.{inc,stl,vtk,json}"""
import argparse, json
from . import build_fork, image_to_mesh, stl_to_mesh

def main(argv=None):
    p = argparse.ArgumentParser(prog="geometry")
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fork"); f.add_argument("--profile", default="realistic", choices=["realistic", "uniform"]); f.add_argument("--nz", type=int, default=5)
    i = sub.add_parser("image"); i.add_argument("path"); i.add_argument("--length-mm", type=float, required=True)
    i.add_argument("--thickness-mm", type=float, default=2.0); i.add_argument("--cell-mm", type=float); i.add_argument("--nz", type=int, default=3)
    i.add_argument("--flip", action="store_true"); i.add_argument("--flat", action="store_true", help="keep lying flat instead of standing on end")
    s = sub.add_parser("stl"); s.add_argument("path"); s.add_argument("--voxel-mm", type=float, default=1.0); s.add_argument("--scale", type=float, default=1.0)
    s.add_argument("--on-floor", action="store_true")
    for sp in (f, i, s):
        sp.add_argument("--out", required=True); sp.add_argument("--part-id", type=int, default=1)
    a = p.parse_args(argv)
    if a.cmd == "fork":
        m = build_fork(a.profile, nz=a.nz)
    elif a.cmd == "image":
        m = image_to_mesh(a.path, a.length_mm, a.thickness_mm, a.cell_mm, a.nz, flip=a.flip, upright=not a.flat)
    else:
        m = stl_to_mesh(a.path, a.voxel_mm, a.scale, place_on_floor=a.on_floor)
    m.part_id = a.part_id
    print(json.dumps(m.export_all(a.out, title=f"simstudio {a.cmd}"), indent=2))

if __name__ == "__main__":
    main()
