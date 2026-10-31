# OpenRadioss / OpenCourant toolchain. Source this before starter or engine.
# The engine on PATH is the OpenMP build (engine_linux64_gf). Do not export the
# MPI binary engine_linux64_gf_ompi; that build fails here.
if [ -z "${OPENRADIOSS_PATH:-}" ]; then
  if [ -n "${SOLVER_ROOT:-}" ] && [ -d "${SOLVER_ROOT}" ]; then
    export OPENRADIOSS_PATH="${SOLVER_ROOT}"
  elif [ -d /opt/solver ]; then
    export OPENRADIOSS_PATH=/opt/solver
  elif [ -d /opt/OpenCourant ]; then
    export OPENRADIOSS_PATH=/opt/OpenCourant
  fi
fi
if [ -n "${OPENRADIOSS_PATH:-}" ]; then
  export RAD_CFG_PATH="${RAD_CFG_PATH:-$OPENRADIOSS_PATH/hm_cfg_files}"
  export RAD_H3D_PATH="${RAD_H3D_PATH:-$OPENRADIOSS_PATH/extlib/h3d/lib/linux64}"
  export LD_LIBRARY_PATH="$OPENRADIOSS_PATH/extlib/hm_reader/linux64:$OPENRADIOSS_PATH/extlib/h3d/lib/linux64:${LD_LIBRARY_PATH:-}"
  export PATH="$OPENRADIOSS_PATH/exec:${PATH}"
fi
export OMP_STACKSIZE="${OMP_STACKSIZE:-400m}"
ulimit -s unlimited 2>/dev/null || true
