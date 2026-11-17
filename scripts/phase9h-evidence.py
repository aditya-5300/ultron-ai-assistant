"""Export sanitized observations without modifying a prior run or frozen evidence."""

import argparse
import csv
from hashlib import sha256
import json
from pathlib import Path
import platform
import subprocess
from importlib.metadata import version


ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "docs/phase9h-reference"


def checksum(file):
    return sha256(file.read_bytes()).hexdigest()


def load(file):
    return json.loads(file.read_text(encoding="utf-8-sig"))


def frozen():
    manifests = sorted(ROOT.glob("ml/configs/*reference_sha256.json")) + sorted(ROOT.glob("docs/phase*reference/*sha256.json"))
    snapshots = [*ROOT.glob("docs/phase8c-reference/**/*.txt"), *ROOT.glob("docs/phase8d-reference/**/*.txt")]
    counts = {"historical": 0, "phase8_outputs": 0, "phase9_results": 0}

    def verify(entries, root, historical=False):
        for relative, expected in entries.items():
            file = root / relative
            if checksum(file) == expected:
                continue
            candidates = (snap for snap in snapshots if snap.name in {f"{file.name}.txt", f"phase8a-{file.name}.txt"})
            if not historical or not any(checksum(snap) == expected for snap in candidates):
                raise ValueError(f"Frozen hash mismatch: {relative}")
        return len(entries)

    for manifest in manifests:
        counts["historical"] += verify(load(manifest), ROOT, historical=True)
    for phase in ("phase8", "phase8b", "phase8c", "phase8d", "phase9"):
        results = ROOT / "docs" / (phase + "-reference") / "results"
        group = "phase9_results" if phase == "phase9" else "phase8_outputs"
        counts[group] += verify(load(results / "reproducibility_manifest.json")["output_sha256"], results)
    if counts != {"historical": 583, "phase8_outputs": 39, "phase9_results": 13}:
        raise ValueError("Unexpected frozen reference counts")
    return counts


def original(file, config):
    text = file.read_text(encoding="utf-8-sig")
    if ("not ok 2 - authenticated HTTP accepts bounded and exact-size requests, and rejects oversized requests" not in text
            or "error: 'read ECONNRESET'" not in text or "code: 'ECONNRESET'" not in text or "# fail 1" not in text):
        raise ValueError("The original failed TAP was not supplied")
    if checksum(file) != "884e01613f99ff8c4375d424c2de85f9a02aad21f6f2ecf51115a126f2dce7b9":
        raise ValueError("Original TAP is not byte-identical to the preserved copy")
    tracked = ("scripts/phase8b-operational.test.cjs", "scripts/phase8b-fixture.cjs",
               "docs/phase8b-reference/phase8b_config.json", "lib/health/shadow/client.cjs",
               "ml/src/ultron_health_ml/service/app.py", "ml/src/ultron_health_ml/service/__main__.py",
               "ml/src/ultron_health_ml/service/schema.py")
    return {
        "id": "HTTP_BOUNDARY_ECONNRESET",
        "status": "FAIL",
        "baseline": config["baseline"],
        "original_tap_sha256": checksum(file),
        "archive": "Byte-identical original-regressions.tap retained locally outside Git. This record omits private paths and unrelated fixture output.",
        "excerpt": ["not ok 2 - authenticated HTTP accepts bounded and exact-size requests, and rejects oversized requests",
                    "duration_ms: 10162.7474", "error: 'read ECONNRESET'", "code: 'ECONNRESET'", "# pass 5", "# fail 1"],
        "request_id": None,
        "runtime_api": "1.0.0",
        "policy": "phase6-approved-v1",
        "original_limits": {key: config[key] for key in ("body_bytes", "response_bytes", "max_records",
            "max_history_seconds", "node_connect_ms", "node_total_ms", "python_body_ms", "python_inference_ms",
            "python_keep_alive_ms", "fixture_network_idle_ms")},
        "baseline_file_sha256": {path: sha256(subprocess.check_output(
            ("git", "show", f"{config['baseline']}:{path}"), cwd=ROOT)).hexdigest() for path in tracked},
    }


def table(path, rows):
    columns = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def rounded(value):
    return round(value, 3) if value is not None else None


def export(result, output, regression):
    config = load(REFERENCE / "phase9h_config.json")
    original_reference = original(result / "original-regressions.tap", config)
    counts = frozen()
    rows, lifecycle, timings, recovery, repeats, reproduced_resets = [], [], [], [], [], []
    unexpected_legacy_transfer = False
    valid_payloads_complete = True
    lines = (result / "phase9h.tap").read_text(encoding="utf-8-sig").splitlines()
    prefix = "# PHASE9H "
    runs = [json.loads(line.removeprefix(prefix)) for line in lines if line.startswith(prefix)]
    if [run["index"] for run in runs] != list(range(1, config["repetitions"] + 1)):
        raise ValueError("Every predeclared repetition must be present in the TAP")
    for run in runs:
        index = run["index"]
        wire = {entry["peer"]: entry for entry in run["traces"] if entry["kind"] == "wire"}
        cases = run["cases"]
        main = [case for case in cases if case["id"] != "above_cap_raw_full_upload"]
        legacy = next((case for case in cases if case["id"] == "above_cap_raw_full_upload"), {})
        for case in cases:
            request = next((item for item in run["requests"]
                            if item["caseId"] == case["id"] and item["route"] == "/v1/infer"), {})
            server = wire.get(request.get("peer"), {})
            key = {"repetition": index, "request_sequence": request.get("request")}
            rows.append({**key, **{key: value for key, value in case.items() if not key.startswith("recovery")}})
            if case["status"] == 200:
                valid_payloads_complete &= (server.get("received_bytes") == case["bytes"]
                                            and server.get("upload_done_ms") is not None and case["complete"])
            if case["id"] == "above_cap_raw_full_upload" and case["category"] != "PASS":
                if (case["error"] == "ECONNRESET" and case["status"] == server.get("status") == 413
                        and request["responseAborted"] and not request["timeout"] and not case["complete"]
                        and server.get("received_bytes") == 0 and run["aliveBeforeStop"] and case.get("recovery")):
                    reproduced_resets.append(key)
                else:
                    unexpected_legacy_transfer = True
            if "recovery" in case:
                recovery.append({"repetition": index, "case": case["id"], "validated": case["recovery"],
                                 "error": case["recoveryError"], "actual_notifications": run["actualNotifications"]})
        repeats.append(dict(repetition=index, observed_cases=len(cases), passed=len([row for row in main if row["category"] == "PASS"]),
                            required_cases=len(config["cases"]) - 1, legacy_full_upload=legacy.get("category"),
                            legacy_error=legacy.get("error"), ready_ms=rounded(run["readyMs"]),
                            alive_before_planned_stop=run["aliveBeforeStop"], stop_ms=rounded(run["stopMs"]),
                            post_stop_exit_code=run["exitCode"], post_stop_exit_signal=run["exitSignal"],
                            harness_error=run["failure"]))
        for request in run["requests"]:
            server = wire.get(request.get("peer"), {})
            key = {"repetition": index, "request_sequence": request["request"], "case": request["caseId"], "route": request["route"]}
            lifecycle.append({**key, "start_utc": request["startUtc"], "declared_bytes": request["declaredBytes"],
                              "records": request["records"], "history_seconds": request["historySeconds"],
                              "server_status": server.get("status"), "client_status": request["status"],
                              "connection": request.get("connection"), "reused_socket": request["reusedSocket"],
                              "complete": request["complete"], "error": request["error"], "timeout": request["timeout"],
                              "response_aborted": request["responseAborted"], "server_disconnected": server.get("disconnected"),
                              "server_read_bytes": server.get("received_bytes"), "listener_exit": request["listenerExit"],
                              "listener_signal": request["listenerSignal"]})
            ready, sent = server.get("headers_ms"), server.get("upload_done_ms")
            headers, complete = request.get("headersMs"), request.get("responseMs")
            timings.append({**key, "connect_ms": rounded(request.get("connectMs")),
                            "client_upload_handoff_ms": rounded(request.get("uploadHandoffMs")),
                            "server_upload_done_ms": rounded(sent),
                            "server_processing_to_headers_ms": rounded(ready - sent if ready is not None and sent is not None else None),
                            "server_headers_ms": rounded(ready), "server_asgi_response_done_ms": rounded(server.get("response_done_ms")),
                            "client_headers_ms": rounded(headers),
                            "client_response_transfer_ms": rounded(complete - headers if complete is not None and headers is not None else None),
                            "client_total_ms": rounded(complete), "client_socket_close_ms": rounded(request.get("closeMs")),
                            "timeout": request["timeout"]})
    phase9h_pass = "# pass 1" in lines and "# fail 0" in lines and all(
        row["passed"] == row["required_cases"] and row["alive_before_planned_stop"] for row in repeats
    )
    all_recoveries_pass = bool(recovery) and all(row["validated"] for row in recovery)
    verified = (phase9h_pass and all_recoveries_pass and valid_payloads_complete and not unexpected_legacy_transfer
                and all(regression.values()))
    gates = {"instrumented_matrix_pass": phase9h_pass,
             "all_recoveries_pass": all_recoveries_pass,
             "valid_payloads_complete": valid_payloads_complete,
             "unexpected_legacy_transfer": unexpected_legacy_transfer,
             "legacy_probe_reproduced_resets": reproduced_resets,
             "original_failure_preserved": True, "frozen_reference_counts": counts,
             "regression": regression,
             "decision": "HTTP RELIABILITY VERIFIED" if verified else "REPEAT HTTP INVESTIGATION"}
    output.mkdir()
    for name, data in (("http_request_matrix", rows), ("connection_lifecycle_evaluation", lifecycle),
                       ("timeout_evaluation", timings), ("service_recovery_evaluation", recovery),
                       ("repetition_results", repeats)):
        table(output / (name + ".csv"), data)

    def document(name, data):
        with (output / (name + ".json")).open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(data, stream, indent=2)
            stream.write("\n")

    document("original_failure_reference", original_reference)
    document("acceptance_gates", gates)
    logical = {key: val for key, val in gates.items() if key != "regression"}
    logical["case_outcomes"] = rows
    document("reproducibility_manifest", {
        "version": config["version"], "config_sha256": checksum(REFERENCE / "phase9h_config.json"),
        "matrix_tap_sha256": checksum(result / "phase9h.tap"),
        "logical_decision_sha256": sha256(json.dumps(logical, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "environment": {"python": platform.python_version(),
                        "node": subprocess.check_output(("node", "--version"), text=True).strip(),
                        "fastapi": version("fastapi"), "uvicorn": version("uvicorn"), "h11": version("h11"),
                        "os": platform.system()},
        "output_sha256": {file.name: checksum(file) for file in sorted(output.iterdir())},
    })


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("result", type=Path, nargs="?")
    parser.add_argument("output", type=Path, nargs="?")
    parser.add_argument("regression", type=Path, nargs="?")
    options = parser.parse_args()
    if options.result is None:
        print(json.dumps(frozen(), sort_keys=True))
    else:
        export(options.result, options.output, load(options.regression))
