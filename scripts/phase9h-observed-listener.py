"""Run the Phase 6 launcher with fixture-only ASGI and processing observations."""

from datetime import datetime, timezone
import json
from time import perf_counter

from ultron_health_ml.service import __main__ as launcher
from ultron_health_ml.service.app import create_app, process


def observed(secret):
    def processor(payload, policy, deadline, clock):
        started = perf_counter()
        try:
            return process(payload, policy, deadline, clock)
        finally:
            print(json.dumps({"kind": "processing", "ms": (perf_counter() - started) * 1000}), flush=True)

    app = create_app(secret, processor=processor)

    async def observe(scope, receive, send):
        if scope["type"] != "http":
            return await app(scope, receive, send)
        started = perf_counter()
        row = {"kind": "wire", "route": scope["path"], "peer": scope["client"][1],
               "start_utc": datetime.now(timezone.utc).isoformat(), "received_bytes": 0,
               "upload_done_ms": None, "status": None, "headers_ms": None,
               "response_done_ms": None, "disconnected": False}

        async def incoming():
            message = await receive()
            if message["type"] == "http.request":
                row["received_bytes"] += len(message.get("body", b""))
                if not message.get("more_body", False):
                    row["upload_done_ms"] = (perf_counter() - started) * 1000
            elif message["type"] == "http.disconnect":
                row["disconnected"] = True
            return message

        async def outgoing(message):
            if message["type"] == "http.response.start":
                row["status"] = message["status"]
                row["headers_ms"] = (perf_counter() - started) * 1000
            await send(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                row["response_done_ms"] = (perf_counter() - started) * 1000

        try:
            await app(scope, incoming, outgoing)
        finally:
            print(json.dumps(row), flush=True)

    return observe


if __name__ == "__main__":
    launcher.create_app = observed
    launcher.main()
