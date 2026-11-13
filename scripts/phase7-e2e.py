"""Run the Node shadow suite against a temporary, restartable Phase 6 listener."""

import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx


def main():
    repo = Path(__file__).resolve().parents[1]
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    secret = secrets.token_urlsafe(32)
    environment = {**os.environ, "ULTRON_INFERENCE_TOKEN": secret, "ULTRON_PHASE7_FIXTURE_PORT": str(port)}
    python = None

    def stop():
        if python is not None:
            python.terminate()
            logs = python.communicate(timeout=12)[0]
            if secret.encode() in logs:
                raise RuntimeError("token present in service logs")

    def start():
        nonlocal python
        python = subprocess.Popen([sys.executable, "-m", "ultron_health_ml.service", "--port", str(port)],
                                  cwd=repo, env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=2) as client:
            for _ in range(100):
                try:
                    assert client.get("/healthz").json() == {"status": "ok"}
                    return
                except httpx.ConnectError:
                    if python.poll() is not None:
                        raise RuntimeError("fixture service exited")
                    time.sleep(.1)
        raise RuntimeError("fixture service not ready")

    # The child asks the parent to restart the exact listener between requests.
    try:
        start()
        with subprocess.Popen(["node", "--test", "--test-reporter=tap", "scripts/phase7-shadow.test.cjs"], cwd=repo,
                              env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, encoding="utf-8", errors="replace") as test:
            for line in test.stdout:
                if "PHASE7_RESTART_LISTENER" in line:
                    stop()
                    python = None
                    start()
                    Path(environment["ULTRON_PHASE7_RESTART_ACK"]).touch()
                print(line, end="")
            return test.wait()
    finally:
        stop()


if __name__ == "__main__":
    from tempfile import TemporaryDirectory
    with TemporaryDirectory(prefix="ultron-phase7-restart-") as directory:
        os.environ["ULTRON_PHASE7_RESTART_ACK"] = str(Path(directory) / "restarted")
        raise SystemExit(main())
