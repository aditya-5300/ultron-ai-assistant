"""Supported local launcher; do not deploy behind a proxy."""

import argparse
import os

import uvicorn

from ultron_health_ml.service.app import create_app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("port must be between 1024 and 65535")
    try:
        app = create_app(os.environ.get("ULTRON_INFERENCE_TOKEN"))
    except ValueError:
        parser.error("set ULTRON_INFERENCE_TOKEN to a generated per-installation secret")
    uvicorn.run(app, host="127.0.0.1", port=args.port, workers=1, proxy_headers=False,
                server_header=False, access_log=False, limit_concurrency=8, timeout_keep_alive=2,
                h11_max_incomplete_event_size=16384, http="h11", ws="none")


if __name__ == "__main__":
    main()
