#!/usr/bin/env python3
"""Register existing fork45 glTF results as finished SimStudio studies.

Each model becomes one `simulations` row (status succeeded, source_type "import")
owned by an existing user, with its GLB and manifests uploaded to S3 under
sims/<id>/post/gltf/. The simulation id is a uuid5 of "<set>/<model>", so
re-running for a model replaces that study's row, metrics, artifacts, and S3
objects in place (idempotent per model).

    python sim-server/scripts/import_forks.py plastic             # fork45v2 plastic
    python sim-server/scripts/import_forks.py plastic aluminum
    python sim-server/scripts/import_forks.py --set fork45 all    # uniform 45 kg runs

A GLB whose size changes during a short wait, or that does not parse as a
simstudio-morph-v1 binary glTF, is skipped (it may still be being written).

Env: DATABASE_URL, S3_ENDPOINT_URL, S3_BUCKET, AWS_ACCESS_KEY_ID,
AWS_SECRET_ACCESS_KEY, AWS_DEFAULT_REGION (defaults match docker-compose with
docker-compose.lowmem.yml, which publishes Postgres/LocalStack on 127.0.0.1).
Requires: boto3, psycopg[binary].
"""

from __future__ import annotations

import argparse
import json
import os
import struct
import sys
import time
import uuid
from pathlib import Path

import boto3
import psycopg
from botocore.client import Config
from psycopg.types.json import Jsonb

SETS = {
    "fork45v2": {
        "dir": "/workspace/openradioss/results/fork45v2_viewer/gltf",
        "title": "Fork 45 v2",
    },
    "fork45": {
        "dir": "/workspace/openradioss/results/fork45_viewer/gltf",
        "title": "Fork 45 (uniform 45 kg)",
    },
}
NAMESPACE = uuid.UUID("5b0c8f1e-6a51-4d1c-9a43-6f726b343500")


def parse_glb(path: Path) -> dict:
    """Return mesh[0].extras of a complete simstudio-morph-v1 GLB, or raise."""
    size = path.stat().st_size
    with path.open("rb") as fh:
        head = fh.read(20)
        if len(head) < 20:
            raise ValueError("truncated header")
        magic, version, total = struct.unpack("<III", head[:12])
        if magic != 0x46546C67 or version != 2:
            raise ValueError("not a glTF 2 binary")
        if total != size:
            raise ValueError(f"header length {total} != file size {size} (still being written?)")
        jlen, jtype = struct.unpack("<II", head[12:20])
        if jtype != 0x4E4F534A:
            raise ValueError("first chunk is not JSON")
        doc = json.loads(fh.read(jlen))
        fh.seek(20 + jlen)
        bhead = fh.read(8)
        if len(bhead) == 8:
            blen, _ = struct.unpack("<II", bhead)
            if 28 + jlen + blen != size:
                raise ValueError("BIN chunk length does not match file size")
    extras = (doc.get("meshes") or [{}])[0].get("extras") or {}
    if extras.get("format") != "simstudio-morph-v1":
        raise ValueError("mesh extras format is not simstudio-morph-v1")
    return extras


def stable(path: Path, wait_s: float) -> bool:
    a = path.stat()
    time.sleep(wait_s)
    b = path.stat()
    return (a.st_size, a.st_mtime_ns) == (b.st_size, b.st_mtime_ns)


def build_summary(run: dict, model_manifest: dict, extras: dict) -> dict:
    markers = run.get("buckling_markers") or model_manifest.get("buckling_markers") or {}
    pvm = run.get("peak_von_mises") or model_manifest.get("peak_von_mises") or {}
    pps = run.get("peak_plastic_strain") or model_manifest.get("peak_plastic_strain") or {}
    lb = model_manifest.get("lower_bound") or extras.get("lower_bound") or {}
    vm_lb = bool(pvm.get("peak_is_lower_bound") or model_manifest.get("peak_is_lower_bound"))
    ps_lb = bool(pps.get("peak_is_lower_bound") or model_manifest.get("peak_is_lower_bound"))
    notes = []
    if pvm.get("label"):
        notes.append(pvm["label"])
    if vm_lb or ps_lb:
        notes.append("Peak values are lower bounds: " + (lb.get("note") or "the run ended before the next frame."))
    if run.get("unphysical_frames") or model_manifest.get("unphysical_frames"):
        notes.append(
            f"Frames {run.get('unphysical_frames') or model_manifest.get('unphysical_frames')} are past the last "
            f"physical frame ({run.get('physical_until_s') or model_manifest.get('physical_until_s')} s)."
        )
    if markers.get("buckling_load_N") is not None:
        notes.append(f"Buckling at {markers['buckling_load_N']:.1f} N (t = {markers.get('buckling_time_real_s', 0):.2f} s).")
    if markers.get("plastic_fracture_load_N") is not None:
        notes.append(f"Plastic fracture load {markers['plastic_fracture_load_N']:.1f} N.")
    frame_loads = [f.get("load_N") for f in (model_manifest.get("frames") or extras.get("frames") or []) if f.get("load_N") is not None]
    if markers.get("max_load_N") is not None:
        notes.append(f"Target load {markers['max_load_N']:.1f} N; peak force is the largest applied load over the exported frames.")
    notes.append(f"Imported from {model_manifest.get('source_run') or 'existing results'} by import_forks.py.")
    return {
        "peak_force_N": max(frame_loads) if frame_loads else markers.get("max_load_N"),
        "target_load_N": markers.get("max_load_N"),
        "buckling_force_N": markers.get("buckling_load_N"),
        "buckling_reason": "buckling marker from the fork45 viewer manifest" if markers.get("buckling_load_N") is not None else None,
        "max_von_mises_Pa": pvm.get("value_Pa", markers.get("max_von_mises_Pa")),
        "max_von_mises_lower_bound": vm_lb,
        "max_plastic_strain": pps.get("value"),
        "max_plastic_strain_lower_bound": ps_lb,
        "lower_bound_note": (lb.get("note") or "").removeprefix("lower bound: ") or None if (vm_lb or ps_lb) else None,
        "fracture_onset": {"load_N": markers["plastic_fracture_load_N"]} if markers.get("plastic_fracture_load_N") is not None else None,
        "energy_balance_ok": None,
        "glb": f"gltf/{run['glb']}",
        "n_frames": extras.get("n_frames"),
        "physical_until_s": run.get("physical_until_s") or model_manifest.get("physical_until_s"),
        "unphysical_frames": run.get("unphysical_frames") or model_manifest.get("unphysical_frames"),
        "notes": notes,
        "imported": True,
    }


def metric_rows(summary: dict) -> list[tuple[str, float | None, str]]:
    def f(v):
        return None if v is None else float(v)

    return [
        ("peak_force_N", f(summary["peak_force_N"]), "N"),
        ("buckling_force_N", f(summary["buckling_force_N"]), "N"),
        ("max_von_mises_Pa", f(summary["max_von_mises_Pa"]), "Pa"),
        ("max_von_mises_is_lower_bound", 1.0 if summary["max_von_mises_lower_bound"] else 0.0, "bool"),
        ("max_plastic_strain", f(summary["max_plastic_strain"]), "1"),
        ("max_plastic_strain_is_lower_bound", 1.0 if summary["max_plastic_strain_lower_bound"] else 0.0, "bool"),
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("models", nargs="+", help="material names in the set's manifest.json (e.g. plastic aluminum), or 'all'")
    ap.add_argument("--set", default="fork45v2", choices=sorted(SETS))
    ap.add_argument("--dir", help="override the set's gltf directory")
    ap.add_argument("--email", default=os.environ.get("IMPORT_USER_EMAIL", "ada@simstudio.test"))
    ap.add_argument("--wait", type=float, default=3.0, help="seconds to confirm the GLB size is stable")
    args = ap.parse_args()

    gdir = Path(args.dir or SETS[args.set]["dir"])
    manifest = json.loads((gdir / "manifest.json").read_text())
    runs = {r["material"]: r for r in manifest["runs"]}
    wanted = sorted(runs) if args.models == ["all"] else args.models

    db_url = os.environ.get("DATABASE_URL", "postgresql://radlab:radlab@127.0.0.1:5432/radlab").replace("postgresql+psycopg://", "postgresql://")
    bucket = os.environ.get("S3_BUCKET", "radlab-artifacts")
    s3 = boto3.client(
        "s3",
        endpoint_url=os.environ.get("S3_ENDPOINT_URL", "http://127.0.0.1:4566"),
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID", "test"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY", "test"),
        region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        config=Config(s3={"addressing_style": "path"}),
    )

    failures = 0
    with psycopg.connect(db_url) as conn:
        row = conn.execute("SELECT id FROM users WHERE email = %s", (args.email,)).fetchone()
        if not row:
            print(f"no user {args.email}; register one through /api/auth/register first", file=sys.stderr)
            return 2
        user_id = row[0]
        for model in wanted:
            run = runs.get(model)
            if not run:
                print(f"[skip] {model}: not in {gdir}/manifest.json (have {sorted(runs)})")
                failures += 1
                continue
            glb = gdir / run["glb"]
            mpath = gdir / (run.get("manifest") or f"{model}.manifest.json")
            try:
                if not stable(glb, args.wait):
                    raise ValueError("size/mtime changed while waiting (still being written?)")
                extras = parse_glb(glb)
                model_manifest = json.loads(mpath.read_text()) if mpath.is_file() else {}
            except Exception as exc:  # noqa: BLE001
                print(f"[skip] {model}: {glb}: {exc}")
                failures += 1
                continue

            sim_id = uuid.uuid5(NAMESPACE, f"{args.set}/{model}")
            prefix = f"sims/{sim_id}"
            uploads = [
                (glb, f"post/gltf/{glb.name}", "model/gltf-binary", f"gltf/{glb.name}"),
                (mpath, f"post/gltf/{mpath.name}", "application/json", f"gltf/{mpath.name}"),
                (gdir / "manifest.json", "post/gltf/manifest.json", "application/json", "gltf/manifest.json"),
            ]
            for png in sorted(gdir.glob(f"verify_{model}_*.png")):
                uploads.append((png, f"post/gltf/{png.name}", "image/png", f"gltf/{png.name}"))

            old = s3.list_objects_v2(Bucket=bucket, Prefix=prefix + "/").get("Contents", [])
            for obj in old:
                s3.delete_object(Bucket=bucket, Key=obj["Key"])
            artifacts = []
            for path, suffix, ctype, kind in uploads:
                if not path.is_file():
                    continue
                key = f"{prefix}/{suffix}"
                s3.upload_file(str(path), bucket, key, ExtraArgs={"ContentType": ctype})
                artifacts.append((kind, path.name, key, ctype, path.stat().st_size))

            summary = build_summary(run, model_manifest, extras)
            name = f"{SETS[args.set]['title']} · {run.get('label') or model}"
            params = {"import_set": args.set, "model": model, "source_dir": str(gdir), "glb_bytes": glb.stat().st_size}
            with conn.transaction():
                conn.execute("DELETE FROM simulations WHERE id = %s", (sim_id,))
                conn.execute(
                    """INSERT INTO simulations (id, user_id, name, status, progress, message, source_type, template_id,
                           material, dry_run, parameters, summary, peak_force_n, max_von_mises_pa, max_plastic_strain,
                           s3_prefix, created_at, started_at, finished_at)
                       VALUES (%s,%s,%s,'succeeded',100,%s,'import',%s,%s,false,%s,%s,%s,%s,%s,%s,now(),now(),now())""",
                    (
                        sim_id, user_id, name, "Imported existing OpenRadioss result", args.set, model,
                        Jsonb(params), Jsonb(summary), summary["peak_force_N"], summary["max_von_mises_Pa"],
                        summary["max_plastic_strain"], prefix,
                    ),
                )
                for mname, value, unit in metric_rows(summary):
                    conn.execute(
                        "INSERT INTO sim_metrics (id, sim_id, name, value, unit) VALUES (%s,%s,%s,%s,%s)",
                        (uuid.uuid4(), sim_id, mname, value, unit),
                    )
                for kind, fname, key, ctype, size in artifacts:
                    conn.execute(
                        """INSERT INTO artifacts (id, simulation_id, kind, filename, s3_key, content_type, size_bytes)
                           VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                        (uuid.uuid4(), sim_id, kind, fname, key, ctype, size),
                    )
            lb = " (peak is a lower bound)" if summary["max_von_mises_lower_bound"] else ""
            print(f"[ok] {args.set}/{model} -> /simulations/{sim_id}  {len(artifacts)} artifacts, "
                  f"{glb.stat().st_size} B glb, max vM {summary['max_von_mises_Pa']:.3g} Pa{lb}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
