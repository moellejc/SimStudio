# geometry (owner: Image23D)

Turns a photo, an STL, or a parametric template into an OpenRadioss hex mesh. Units are mm.

| Source | Function | CLI |
|---|---|---|
| Photo (outline + assumed thickness) | `image_to_mesh(path, length_mm, thickness_mm=2.0, cell_mm=None, nz=3, flip=False, upright=True)` | `python -m geometry image photo.jpg --length-mm 190 --out out/` |
| STL (watertight) | `stl_to_mesh(path, voxel_mm=1.0, scale=1.0, place_on_floor=False)` | `python -m geometry stl part.stl --voxel-mm 0.8 --on-floor --out out/` |
| Fork template | `build_fork(profile="realistic"\|"uniform", nz=5)` | `python -m geometry fork --profile realistic --out out/` |

Every builder returns a `HexMesh`. `mesh.export_all(out_dir)` writes:
- `model.inc`: `/NODE` + `/BRICK/<part_id>` (Radioss fixed format, I10/F20). `#include` it in the starter deck.
- `model.stl`: the boundary surface, for the web viewer.
- `model.vtk`: the volume mesh, for post-processing and preview.
- `model.glb`: the outer surface as binary glTF for the web 3D preview. It is in meters and Y-up, mapped as (x, y, z) in mm to (x, z, −y)/1000.
- `model.json`: node and brick counts, volume, bbox, inverted-brick count (always 0) and source metadata. Store it in Postgres.

Conventions for the runner: an upright part has its long axis along +Z with its lowest point 1 mm above a floor at Z=0, so a push goes along −Z. Bricks use VTK/Radioss node order with positive volume. Node and element IDs start at 1; use `write_radioss_inc(path, node_offset, elem_offset)` to combine parts.

Limits: a single photo gives only the outline. Thickness is a user input, perspective distorts the shape, and gaps narrower than about two cells (for example fork tines from an angled shot) merge. Use a template or an STL when accuracy matters.

Tests: `pytest geometry/tests` (9 tests, about 2 s).
