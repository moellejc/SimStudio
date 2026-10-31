# post (owner: SimStudio post-processing)

Turns an OpenRadioss/OpenCourant run folder (`<name>A###[.gz]`, `<name>T01`, `*_0000.rad`, `*.out`) into VTK frames,
an animated glTF (`.glb`, primary 3D output), heat-map renders, an MP4/GIF, metrics for Postgres, a list of S3 artifacts, multi-run comparisons and a standalone 3D viewer.
Run folders are only read. Output always goes to a separate directory.

| Task | Function | CLI (run from `sim-server/`) |
|---|---|---|
| One run | `process_run(run_dir, out_dir, *, ramp_duration=None, cmap="jet", ...)` → metrics dict | `python -m post process RUN --out DIR [--ramp-duration 5] [--no-video] [--no-gltf] [--gltf-max-frames N] [--sim-id ID]` |
| Several runs | `compare_runs(run_dirs, labels, out_dir, *, ramp_duration=None, cmap="jet", mode="auto", video=True, ...)` → comparison dict | `python -m post compare RUN1 RUN2 --labels "Steel,Al" --out DIR` |
| 3D viewer | `build_viewer(run_dirs, labels, out_html, *, ramp_duration=None, comparison=None, max_mb=30)` → path | `python -m post viewer RUN1 RUN2 --out v.html [--comparison DIR/comparison.json]` |
| DB rows | `metrics_to_rows(metrics, sim_id)` → `[{sim_id, name, value, unit}]` | `process ... --sim-id ID` prints them |
| S3 upload list | `artifact_manifest(out_dir)` → `[{path, s3_key_suffix, content_type}]` | |
| Buckling only | `detect_buckling(F_N, u, reaction_N=None, *, kfrac=0.15, track_tol=0.2, sustain=3, f_start=0.1, smooth=5)` | |
| GLB only | `frames_to_glb(vtk_files, out_glb, *, times, load_times, loads, max_frames=None, length_to_m=1.0)` → manifest dict; `read_glb(path)` → (json, accessors) | |
| TH only | `analyze_time_history(df, deck, mode="auto")` for a th_to_csv DataFrame and a `compare.parse_deck` dict | |

## process_run(run_dir, out_dir) writes
- `vtk/<name>_NNN.vtk`: the anim_to_vtk frames with every stress array (`von_mises|vonm|stress`) converted to Pa.
- `vtk/frames.json`: `{stress_unit: "Pa", time_unit, load_kind, frames: [{file, time, load_time, load_N}]}`. `time` is solver time.
  `load_time` is the equivalent load time in s (time × `ramp_duration`/ramp end) when `ramp_duration` is given, else solver time in s.
  `load_N` is the applied load for force control (/CLOAD) or the floor reaction for velocity control. This is the generator-export
  format that `compare.py`/`make_viewer.py` already read, so an out_dir can be passed to `build_viewer`.
- `frames/frame_NNN.png`, `<name>.mp4`, `<name>.gif`: offscreen heat maps (jet: blue = low, red = high). Pass `video=False` to skip the MP4/GIF. The PNGs are always written.
- `gltf/<name>.glb` + `gltf/manifest.json`: the animated GLB described below (`gltf=False` skips it, `gltf_max_frames=N` keeps N evenly spaced frames). `metrics["glb"]` is its relative path.
- `th/<name>T01.csv`, `plots/*.png` (energies, time step/added mass, per-entity TH).
- `summary.json`: the full postprocess.py-style summary. `metrics.json`: the dict returned by the function:

| key | meaning |
|---|---|
| `mode` | `force` (deck has /CLOAD or /PLOAD) or `velocity` |
| `peak_force_N` (+`peak_force_kind`) | max applied load (force) or max floor reaction (velocity). `peak_reaction_N`: max floor reaction |
| `buckling_force_N`, `disp_at_buckling_mm`, `buckling_time_s`, `buckling_load_time_s`, `buckling_criterion` | null when nothing is detected (`buckling_reason` says why) |
| `max_disp_mm` | loaded-end displacement along the load (force) or imposed displacement (velocity) |
| `max_von_mises_Pa`, `max_plastic_strain` | maxima over all frames and cells |
| `fracture_onset` | null or `{source, time_solver, time_s, load_time_s, load_N, disp_mm, eroded_elements}` |
| `energy_balance_ok` | (Etot − Etot0 − Wext)/max < 5 % (postprocess.py check). `hourglass_warning`: hourglass > 10 % of IE |
| `ke_ie_ratio` | max KE/IE while IE > 5 % of its max (quasi-static check) |
| `added_mass_pct` | max PERCENTAGE ADDED MASS |
| `ended_early` (+`ended_early_reason`) | TH ends before 99 % of /RUN end, or the engine listing lacks NORMAL TERMINATION (sensor stop, crash) |

## Animated GLB (`gltf/<name>.glb`) — contract for the web viewer
One file per run, one mesh, one primitive (triangles). Load with three.js `GLTFLoader` and play `gltf.animations[0]`.
- **Surface**: the outer surface of the structural elements (stressed in some frame, active at t=0, shells/solids) is built ONCE
  from frame 1. Rigid caps/bodies inactive from t=0 are left out. Vertices are duplicated per (element, node), so
  `n_vertices` is larger than the node count.
- **Coordinates** (same as geometry's `model.glb`, `HexMesh.write_glb`): metres, Y-up, `gltf = (x, z, -y)_model * scale`.
  `scale` is the deck's /BEGIN length unit converted to metres (1e-3 for mm/ms/kg decks, 1 for the m-based demo; `postprocess.LEN_SI`).
  The mapping is a proper rotation (−90° about x), so winding and handedness are unchanged. The solver's −Z push or gravity points to −Y in three.js, and
  a run lines up with its geometry `model.glb` in one scene. Both POSITION and every morph delta are converted. If the length unit is not recognised,
  `scale` is 1 and `length_unit` is "model units". Extras: `length_unit: "m"`, `up_axis: "Y"`, `source_to_gltf: "(x,z,-y)*scale"`, `scale`.
  `to_gltf_coords(p, scale)` applies the same mapping, e.g. for markers.
- **Frames = morph targets**: `POSITION` holds the frame-1 coordinates and morph target k holds the POSITION delta of frame k
  (target 0 is zero). Animation `frames`: a `weights` channel, `STEP` interpolation, key k at `extras.times[k]`
  (load_time in s when `ramp_duration`/frames.json give it, else solver time), one-hot weights. Play it with `LoopOnce` +
  `clampWhenFinished`, or set `mixer.setTime(times[k])`. With LoopRepeat, the time of the last key wraps to frame 0.
  The demo clip is 0.125 s long, so scale it with `action.timeScale`.
- **Erosion**: in frame k the vertices of every element eroded in that frame (`EROSION_STATUS` 0, active at t=0) are morphed onto
  that element's centroid, so its faces have zero area. Neighbours keep their own (duplicated) vertices and do not move.
- **Colours**: `COLOR_0` = jet (blue = min, red = max; same formula as the HTML viewer) of von Mises for the LAST frame, on one fixed global
  range `extras.von_mises_range` over all frames. glTF cannot animate vertex colours, so for per-frame colours recolour from
  the per-frame attributes (see `samples/verify.html`, function `setFrame`).
- **Custom attributes** (float32 SCALAR per vertex): `_VON_MISES` (last frame, Pa) and `_VON_MISES_0000` … `_VON_MISES_NNNN` (one per morph target),
  plus `_PLASTIC_STRAIN` / `_PLASTIC_STRAIN_####` when the run has plastic strain. The element values are averaged onto the nodes over adjacent live
  elements, so the node maxima are a little below the cell maxima in `metrics.max_von_mises_Pa`. **three.js lower-cases the names**:
  `geometry.attributes._von_mises_0003`.
- **Metadata**: `mesh.extras` (`object.userData` in three.js) and the sidecar `gltf/manifest.json` both hold `format`
  ("simstudio-morph-v1"), `n_frames`, `frames: [{index, source_frame, time, load_time, anim_time, load_N, eroded}]`, `times`, `time_axis`,
  `length_unit`, `up_axis`, `source_to_gltf`, `scale`, `stress_unit` ("Pa"), `colormap`, `color_frame`, `von_mises_range`, `plastic_strain_range`, the attribute name lists, `n_vertices`, `n_triangles`,
  and `targetNames`.
- There is no NORMAL attribute, so three.js uses flat shading, which stays correct while the mesh morphs. The material is double-sided and non-metallic.
- Sample for the web team: `samples/sample.glb` (demo run, 26 frames, 128 vertices, 97 kB) and its manifest `sample.manifest.json`.
  `samples/make_sample.py` regenerates it. `samples/verify.html?glb=sample.glb[&frame=K]` is a reference three.js r147 loader that plays the animation
  and recolours each frame. `samples/verify_headless.py` renders frames 1/13/26 in headless Chrome, checks the active target, the shape change and the colours,
  and writes `samples/verify.png`. `assets/three/GLTFLoader.js` (r147, non-module) is vendored for it.

## Physics conventions
- Units come from the `/BEGIN` input-units line. For mm/ms/kg decks, stress is in GPa (×1e9 to get Pa) and force is in kN (×1000 to get N). Unknown units leave values in model units and add a note.
- The floor force is |d(impulse)/dt| of `/TH/RWALL` FN, because T01 stores cumulative impulse. It is smoothed with a 5-sample moving average.
- Applied load = |vector sum| of /CLOAD × /FUNCT × nodes in the /GRNOD. Displacement comes from a `/TH/NODE` (DEF) node in the loaded group, taken along the load.
- Buckling (force control only) is the first sample after F > 0.1 Fmax that meets either criterion for `sustain`=3 consecutive samples:
  1. Tangent stiffness dF/du < `kfrac`(0.15) × initial stiffness while F still rises. Initial stiffness is the median over 10–30 % of Fmax.
  2. |R − F|/F > `track_tol`(0.2).
  The stiffness criterion is only evaluated until F first reaches `plateau_frac`(0.98) × Fmax. Without that limit, compare.py's forward
  difference flags the end of a ramp-and-hold load as buckling, which happened on fork45_steel. Pass `plateau_frac=None` to get plain compare.py behaviour.
  Velocity-controlled runs report `buckling_force_N: null`. compare.py's velocity path still applies its own ">10 % load drop" rule, so `compare_runs` can show a value for them.
- Fracture (erosion) onset comes first from a part TH `ERODED` count rising above its t=0 value (fine resolution). If there is none, it comes from the animation `EROSION_STATUS`, at frame resolution. Elements already inactive at t=0, such as rigid caps or rigid bodies, never count as fracture. They are also left out of the viewer and drawn as translucent context in the renders.

## Runner integration
- `rows = metrics_to_rows(m, sim_id)`: one row per scalar metric (`value` is a float or None; bools are 1/0; `fracture_detected` and `fracture_onset_*` are included). This maps to `sim_metrics(sim_id, name, value double precision NULL, unit text)`.
- `artifact_manifest(out_dir)`: every file with `s3_key_suffix` relative to out_dir (for example `vtk/frames.json` or `BOULE1V4.mp4`). Prefix it with `sims/<id>/post/` when uploading.
- Converters: `anim_to_vtk_linux64_gf` / `th_to_csv_linux64_gf` from `$OPENRADIOSS_EXEC` (default `/workspace/openradioss/tools/OpenCourant/exec`). The package puts that directory on PATH, so `env.sh` is not needed. Rendering is headless (VTK EGL).

## Layout
`process.py` (process_run, detect_buckling, analyze_time_history), `multi.py` (compare_runs, build_viewer), `gltf.py` (frames_to_glb, read_glb), `records.py` (rows, manifest),
`_env.py` (tool lookup), `_vendor/` (copies of openradioss/post `postprocess.py`, `compare.py`, `make_viewer.py`; logic unchanged,
only `main(argv=None)` and the three.js path edited), `assets/three/` (three.js r147 + OrbitControls + GLTFLoader, MIT), `samples/` (sample.glb + three.js check).

Limits:
- `compare_runs` still uses compare.py's detector unchanged, so it can mark the end of the ramp as buckling where `process_run` does not.
- Rendering needs EGL/OSMesa-capable VTK.
- `compare_runs` re-runs postprocess.py for each run into `post_root` (default `out_dir/runs`). It does not reuse a `process_run` out_dir, whose VTK is already in Pa and would be scaled twice.
- Labels must not contain commas.
- The viewer needs a browser with WebGL and DecompressionStream (`extra_args=["--no-gzip"]` otherwise).
- GLB: the surface is fixed at frame 1. When an element erodes, the interior faces it uncovers are not added, so holes show through.
  Elements that are not on the frame-1 surface are not drawn at all. Each frame costs 12 B per vertex (plus 4 B for each per-frame scalar), so
  use `gltf_max_frames` on large models. Only jet is baked into COLOR_0. Morph targets above 8 need WebGL2 (three.js morph textures, r133+).
- Only `/TH/RWALL` (or `/TH/INTER`) floor forces and `/TH/NODE` DEF displacements are recognised.

Tests: `pytest post/tests` with `/workspace/openradioss/venv/bin/python` (23 tests, about 65 s; the three.js check is skipped without `google-chrome`). They use the ball-hits-wall demo run at `/workspace/openradioss/results/demo` plus synthetic force curves, a synthetic mm/ms/kg deck and a synthetic 3-hex erosion model, and a coarse geometry/ fork checked against its model.glb bounding box.
