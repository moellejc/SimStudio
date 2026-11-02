#!/bin/bash
# usage: launch.sh <rundir> <name> <threads> [stop_displacement_mm]
# Runs starter (if the restart file is missing) and the OpenMP engine only.
set -u
ROOT="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "$ROOT/env.sh"
cd "$1" || exit 1
NAME="$2"
THREADS="${3:-1}"
STOPD="${4:-40}"
export OMP_NUM_THREADS="$THREADS"
date +%s > start.ts
if [ ! -f "${NAME}_0000_0001.rst" ] && [ ! -f "${NAME}_0001_0000.rst" ]; then
  starter_linux64_gf -i "${NAME}_0000.rad" -np 1 > starter.log 2>&1 || {
    date +%s > end.ts
    exit 1
  }
fi
# OpenMP engine. Never engine_linux64_gf_ompi.
engine_linux64_gf -i "${NAME}_0001.rad" > engine.log 2>&1 &
EP=$!
python "$ROOT/watch.py" "$1" "$NAME" "$EP" "$STOPD" &
wait "$EP"
RC=$?
date +%s > end.ts
exit "$RC"
