"""gRPC server plus Redis job workers."""

from __future__ import annotations

import json
import logging
import signal
import sys
import threading
from concurrent import futures
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import grpc
import redis

_GEN = Path(__file__).resolve().parent / "gen"
if str(_GEN) not in sys.path:
    sys.path.insert(0, str(_GEN))

import simstudio_pb2_grpc as pbg  # noqa: E402

from runner import engine_binary_name, resolve_binary, solver_environment  # noqa: E402
from simstudio.config import get_settings  # noqa: E402
from simstudio.db import PostgresSink, make_session_factory, wait_for_db  # noqa: E402
from simstudio.pipeline import Job, run_job  # noqa: E402
from simstudio.servicer import SimulationServicer  # noqa: E402
from simstudio.status import StatusBus  # noqa: E402
from simstudio.storage import S3Storage  # noqa: E402

log = logging.getLogger("simstudio")


def _worker(settings, stop: threading.Event) -> None:
    client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    _, factory = make_session_factory(settings.sqlalchemy_url())
    sink = PostgresSink(factory)
    bus = StatusBus(client)
    storage = S3Storage(settings)
    while not stop.is_set():
        item = client.brpop(settings.queue_key, timeout=2)
        if not item:
            continue
        _, raw = item
        try:
            job = Job.from_json(raw)
            run_job(job, settings, bus, sink, storage)
        except Exception:
            log.exception("worker failed on %s", raw[:200])


def _serve_health(settings, httpd: ThreadingHTTPServer) -> None:
    httpd.serve_forever(poll_interval=0.5)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = get_settings()
    if settings.solver_root:
        import os

        os.environ["SOLVER_ROOT"] = settings.solver_root
        os.environ.setdefault("OPENRADIOSS_PATH", settings.openradioss_path or settings.solver_root)
    elif settings.openradioss_path:
        import os

        os.environ["OPENRADIOSS_PATH"] = settings.openradioss_path
    engine, factory = make_session_factory(settings.sqlalchemy_url())
    wait_for_db(engine)
    client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    storage = S3Storage(settings)
    for _ in range(30):
        try:
            storage.ensure_bucket()
            break
        except Exception:
            import time

            time.sleep(1)
    else:
        log.warning("could not create bucket %s yet", settings.s3_bucket)

    sink = PostgresSink(factory)
    bus = StatusBus(client)
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=8),
        options=[
            ("grpc.max_send_message_length", 32 * 1024 * 1024),
            ("grpc.max_receive_message_length", 32 * 1024 * 1024),
        ],
    )
    pbg.add_SimStudioServicer_to_server(SimulationServicer(client, sink, bus, settings.queue_key), server)
    server.add_insecure_port(f"0.0.0.0:{settings.grpc_port}")
    server.start()
    env = solver_environment(1)
    binary = resolve_binary(env, engine_binary_name())

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path.split("?")[0] != "/health":
                self.send_response(404)
                self.end_headers()
                return
            body = json.dumps(
                {
                    "status": "ok",
                    "simMode": settings.sim_mode,
                    "engine": engine_binary_name(),
                    "solverAvailable": bool(binary),
                    "solverPath": binary or "",
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            return

    httpd = ThreadingHTTPServer(("0.0.0.0", settings.health_port), Handler)
    threading.Thread(target=_serve_health, args=(settings, httpd), daemon=True).start()
    stop = threading.Event()
    threads = []
    for i in range(max(1, settings.worker_concurrency)):
        thread = threading.Thread(target=_worker, args=(settings, stop), name=f"worker-{i}", daemon=True)
        thread.start()
        threads.append(thread)
    log.info("grpc :%s health :%s engine %s", settings.grpc_port, settings.health_port, binary or "missing")

    def _stop(signum, _frame):
        stop.set()
        httpd.shutdown()
        server.stop(3)

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    server.wait_for_termination()


if __name__ == "__main__":
    main()
