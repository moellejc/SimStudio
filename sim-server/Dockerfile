# OpenCourant is the community continuation of OpenRadioss. The old
# OpenRadioss GitHub repository 404s. Pin the Linux x86_64 release
# latest-20261011. The engine this image runs is engine_linux64_gf
# (OpenMP). Do not invoke engine_linux64_gf_ompi; that MPI build fails.
# OMP_NUM_THREADS defaults to 2 and is overridden at runtime
# (compose env, or the study's thread count when the engine is spawned).
FROM python:3.12-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl unzip libgomp1 libgfortran5 \
        libgl1 libglib2.0-0 libegl1 libosmesa6 libxrender1 \
    && rm -rf /var/lib/apt/lists/*

# GitHub release asset digest for OpenCourant_linux64.zip (tag latest-20261011).
ARG INSTALL_SOLVER=false
ARG SOLVER_TAG=latest-20261011
ARG SOLVER_URL=https://github.com/OpenCourant/OpenCourant/releases/download/${SOLVER_TAG}/OpenCourant_linux64.zip
ARG SOLVER_SHA256=6cb419677ca36d73acb96218cc0cf5e4f7d50494d9c7812ba27935e3c0f8ec94

RUN if [ "$INSTALL_SOLVER" = "true" ]; then \
      curl -fL "$SOLVER_URL" -o /tmp/solver.zip && \
      echo "${SOLVER_SHA256}  /tmp/solver.zip" | sha256sum -c - && \
      unzip -q /tmp/solver.zip -d /tmp/solver && \
      bin=$(find /tmp/solver -type f -name 'engine_linux64_gf' ! -name '*ompi*' | head -1) && \
      test -n "$bin" && test "$(basename "$bin")" = "engine_linux64_gf" && \
      root=$(dirname "$(dirname "$bin")") && \
      mv "$root" /opt/solver && \
      rm -rf /tmp/solver /tmp/solver.zip && \
      chmod +x /opt/solver/exec/starter_linux64_gf /opt/solver/exec/engine_linux64_gf \
               /opt/solver/exec/anim_to_vtk_linux64_gf /opt/solver/exec/th_to_csv_linux64_gf && \
      test -x /opt/solver/exec/engine_linux64_gf ; \
    fi

ENV OPENRADIOSS_PATH=/opt/solver \
    SOLVER_ROOT=/opt/solver \
    OPENRADIOSS_EXEC=/opt/solver/exec \
    OMP_NUM_THREADS=2 \
    OMP_STACKSIZE=400m \
    SIM_MODE=auto \
    PYTHONPATH=/srv \
    PYTHONUNBUFFERED=1 \
    VTK_DEFAULT_OPENGL_WINDOW=vtkEGLRenderWindow \
    PYVISTA_OFF_SCREEN=true

WORKDIR /srv
COPY sim-server /srv
COPY proto /proto
RUN pip install --no-cache-dir . \
    && mkdir -p /srv/simstudio/gen \
    && python -m grpc_tools.protoc -I /proto \
        --python_out=/srv/simstudio/gen \
        --grpc_python_out=/srv/simstudio/gen \
        /proto/simstudio.proto \
    && touch /srv/simstudio/gen/__init__.py

EXPOSE 50051 8081
CMD ["python", "-m", "simstudio.main"]
