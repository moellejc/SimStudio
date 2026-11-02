"""Redis status hash, log list, and pub/sub for live updates."""

from __future__ import annotations

import json
import time


class StatusBus:
    def __init__(self, redis_client):
        self.redis = redis_client

    def _key(self, simulation_id: str) -> str:
        return f"sim:{simulation_id}"

    def _channel(self, simulation_id: str) -> str:
        return f"sim:{simulation_id}:events"

    def update(self, simulation_id: str, state: str, progress: float, message: str, error: str = "") -> None:
        payload = {
            "state": state,
            "progress": str(progress),
            "message": message,
            "error": error,
            "updated_at_ms": str(int(time.time() * 1000)),
        }
        self.redis.hset(self._key(simulation_id), mapping=payload)
        self.redis.publish(
            self._channel(simulation_id),
            json.dumps({"type": "status", "state": state, "progress": progress, "message": message, "error": error}),
        )

    def log(self, simulation_id: str, stream: str, line: str) -> None:
        item = json.dumps({"stream": stream, "line": line, "ts": int(time.time() * 1000)})
        key = f"sim:{simulation_id}:logs"
        self.redis.rpush(key, item)
        self.redis.ltrim(key, -2000, -1)
        self.redis.publish(self._channel(simulation_id), json.dumps({"type": "log", "stream": stream, "line": line}))

    def request_cancel(self, simulation_id: str) -> None:
        self.redis.set(f"sim:{simulation_id}:cancel", "1")

    def is_cancelled(self, simulation_id: str) -> bool:
        return self.redis.get(f"sim:{simulation_id}:cancel") == "1"

    def snapshot(self, simulation_id: str) -> dict:
        data = self.redis.hgetall(self._key(simulation_id)) or {}
        return {
            "state": data.get("state", ""),
            "progress": float(data.get("progress", 0) or 0),
            "message": data.get("message", ""),
            "error": data.get("error", ""),
            "updated_at_ms": int(data.get("updated_at_ms", 0) or 0),
        }

    def logs(self, simulation_id: str) -> list[dict]:
        raw = self.redis.lrange(f"sim:{simulation_id}:logs", 0, -1) or []
        out = []
        for item in raw:
            try:
                out.append(json.loads(item))
            except json.JSONDecodeError:
                out.append({"stream": "log", "line": item})
        return out
