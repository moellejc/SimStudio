# OpenMP OpenRadioss/OpenCourant runtime. The public solver line is OpenCourant
# (starter_linux64_gf and engine_linux64_gf). Do not install or invoke
# engine_linux64_gf_ompi — that MPI build fails in this environment.
FROM python:3.12-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl unzip libgomp1 libgfortran5 \
        libgl1 libglib2.0-0 libegl1 libosmesa6 libxrender1 \
    && rm -rf /var/lib/apt/lists/*

ARG INSTALL_SOLVER=false
ARG SOLVER_TAG=latest-20261011
ARG SOLVER_URL=https://github.com/OpenCourant/OpenCourant/releases/download/${SOLVER_TAG}/OpenCourant_linux64.zip

RUN if [ "$INSTALL_SOLVER" = "true" ]; then \
      curl -fL "$SOLVER_URL" -o /tmp/solver.zip && \
      unzip -q /tmp/solver.zip -d /tmp/solver && \
      bin=$(find /tmp/solver -type f -name 'engine_linux64_gf' ! -name '*ompi*' | head -1) && \
      root=$(dirname "$(dirname "$bin")") && \
      mv "$root" /opt/solver && \
      rm -rf /tmp/solver && \
      chmod +x /opt/solver/exec/starter_linux64_gf /opt/solver/exec/engine_linux64_gf /opt/solver/exec/*_linux64_gf || true ; \
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
