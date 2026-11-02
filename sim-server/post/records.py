"""Rows for Postgres (sim_metrics) and the S3 upload manifest."""
import mimetypes
import os

# metric name -> unit (scalar metrics stored one row each)
METRIC_UNITS = {
    "peak_force_N": "N", "peak_reaction_N": "N", "buckling_force_N": "N", "disp_at_buckling_mm": "mm",
    "buckling_time_s": "s", "buckling_load_time_s": "s", "max_disp_mm": "mm", "max_von_mises_Pa": "Pa",
    "max_plastic_strain": "-", "energy_balance_ok": "bool", "hourglass_warning": "bool", "ke_ie_ratio": "-",
    "added_mass_pct": "%", "ended_early": "bool", "normal_termination": "bool", "frame_count": "count",
}
FRACTURE_UNITS = {"time_s": "s", "load_time_s": "s", "load_N": "N", "disp_mm": "mm", "eroded_elements": "count"}

CONTENT_TYPES = {".json": "application/json", ".csv": "text/csv", ".md": "text/markdown", ".html": "text/html",
                 ".png": "image/png", ".gif": "image/gif", ".mp4": "video/mp4", ".vtk": "application/octet-stream",
                 ".vtu": "application/xml", ".txt": "text/plain",
                 ".glb": "model/gltf-binary", ".gltf": "model/gltf+json"}


def _num(v):
    if v is None:
        return None
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    return float(v)


def metrics_to_rows(metrics, sim_id):
    """Flatten a process_run metrics dict into [{sim_id, name, value, unit}] (value float or None; bools 1/0).
    Includes fracture_detected plus fracture_onset_<field> rows; strings/notes are not emitted."""
    rows = [{"sim_id": sim_id, "name": k, "value": _num(metrics.get(k)), "unit": u} for k, u in METRIC_UNITS.items()]
    fo = metrics.get("fracture_onset") or {}
    rows.append({"sim_id": sim_id, "name": "fracture_detected", "value": 1.0 if fo else 0.0, "unit": "bool"})
    for k, u in FRACTURE_UNITS.items():
        rows.append({"sim_id": sim_id, "name": f"fracture_onset_{k}", "value": _num(fo.get(k)), "unit": u})
    return rows


def artifact_manifest(out_dir):
    """Every file under out_dir as {path, s3_key_suffix, content_type}, sorted by key. s3_key_suffix is the
    POSIX path relative to out_dir (prefix it with e.g. sims/<sim_id>/post/)."""
    root = os.path.abspath(out_dir)
    items = []
    for d, _, files in os.walk(root):
        for fn in files:
            p = os.path.join(d, fn)
            rel = os.path.relpath(p, root).replace(os.sep, "/")
            ext = os.path.splitext(fn)[1].lower()
            ct = CONTENT_TYPES.get(ext) or mimetypes.guess_type(fn)[0] or "application/octet-stream"
            items.append({"path": p, "s3_key_suffix": rel, "content_type": ct})
    return sorted(items, key=lambda x: x["s3_key_suffix"])
