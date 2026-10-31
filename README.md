# SimStudio

Workbench for OpenRadioss explicit simulations (the maintained solver line is [OpenCourant](https://github.com/OpenCourant/OpenCourant): same starter/engine decks, OpenMP binary `engine_linux64_gf`).

## Layout

| Path | Role |
| --- | --- |
| `proto/simstudio.proto` | gRPC contract: `SubmitSim`, `GetStatus`, `StreamStatus`, `ListSims`, `GetResults`, `CancelSim` |
| `web/` | Next.js UI and web backend. **Schema owner** (`web/prisma`) |
| `sim-server/geometry/` | STL or photo → Radioss `/NODE` + `/BRICK` |
| `sim-server/runner/` | Deck generation and OpenMP starter/engine |
| `sim-server/post/` | Animation and time history → `summary.json` |
| `infra/terraform/` | AWS deployment (validate only; do not apply from CI) |
| `docker-compose.yml` | Local stack |

Postgres schema and migrations live only in `web/prisma`. The sim server writes the same tables and does not migrate them.

## Module interfaces

```python
geometry.build_hex_mesh(request: MeshRequest) -> HexMesh
runner.generate_decks(spec: DeckSpec) -> DeckFiles
runner.solver_environment(threads: int) -> dict
runner.launch_engine(run_dir, name, threads, stop_displacement_mm=40) -> LaunchResult
runner.engine_binary_name() -> "engine_linux64_gf"   # never the MPI build
post.postprocess_run(run_dir, name=None) -> dict     # writes post/summary.json
post.compare_runs(run_dirs, out_dir, labels=None) -> dict
post.wall_force_from_impulse(time_ms, impulse_kN_ms) -> list[float]
post.stress_gpa_to_pa(value) -> float
```

`summary.json` is stored on `simulations.summary`. Scalars mirrored into columns: `peak_force_n`, `max_von_mises_pa`, `max_plastic_strain`, `energy_balance_error_pct`.

Fork decks use kg / mm / ms. Stress × 1e9 is pascals. Force × 1000 is newtons. Rigid-wall time history stores cumulative impulse and is differentiated before it is reported.

```bash
cd sim-server && python -m pytest
```
