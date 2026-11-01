#!/usr/bin/env python3
"""Serve the public dashboard and start one background benchmark run."""

import json
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pipeline


PUBLIC = Path(__file__).resolve().parent / "public"
RESULTS = PUBLIC / "data" / "results.json"
RUN_LOCK = threading.Lock()


def idle_results():
    return {
        "status": "idle",
        "models": {
            "claimant": pipeline.CLAIMANT_MODEL,
            "adjudicator": pipeline.ADJUDICATOR_MODEL,
            "image": pipeline.IMAGE_MODEL,
            "voice": pipeline.VOICE_MODEL,
        },
        "tasks": [],
        "benchmark": {"completed": 0, "total": len(json.loads(pipeline.SCENARIOS.read_text())), "passed": 0, "accuracy": 0},
    }


def save_worker_error(error):
    try:
        data = json.loads(RESULTS.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = idle_results()
    message = str(error)[:800]
    data["status"] = "error"
    for task in data.get("tasks", []):
        if task.get("status") == "running":
            task["status"] = "error"
            task["error"] = message
    pipeline.update_benchmark(data)
    pipeline.write_results(data)


def run_worker(lock_file):
    try:
        pipeline.run_pipeline(lock_file)
    except Exception as exc:
        save_worker_error(exc)
    finally:
        pipeline.release_run_lock(lock_file)
        RUN_LOCK.release()


class DashboardHandler(SimpleHTTPRequestHandler):
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map, ".vtt": "text/vtt; charset=utf-8"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(PUBLIC), **kwargs)

    def send_json(self, status, value):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if urlsplit(self.path).path == "/api/results":
            try:
                data = json.loads(RESULTS.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = idle_results()
            self.send_json(200, data)
            return
        super().do_GET()

    def do_POST(self):
        if urlsplit(self.path).path != "/api/run":
            self.send_error(404)
            return
        origin = self.headers.get("Origin")
        if origin:
            parsed_origin = urlsplit(origin)
            parsed_host = urlsplit("//" + self.headers.get("Host", ""))
            if (
                parsed_origin.scheme != "http"
                or parsed_origin.hostname not in {"localhost", "127.0.0.1"}
                or parsed_origin.port != 8765
                or parsed_origin.hostname != parsed_host.hostname
                or parsed_origin.port != parsed_host.port
            ):
                self.send_json(403, {"status": "error", "error": "Cross-origin run requests are not allowed."})
                return
        if not RUN_LOCK.acquire(blocking=False):
            self.send_json(409, {"status": "running", "error": "A benchmark run is already active."})
            return
        try:
            lock_file = pipeline.acquire_run_lock()
            worker = threading.Thread(target=run_worker, args=(lock_file,), name="insurance-benchmark", daemon=True)
            worker.start()
        except pipeline.RunAlreadyActive:
            RUN_LOCK.release()
            self.send_json(409, {"status": "running", "error": "A benchmark run is already active."})
            return
        except Exception:
            if "lock_file" in locals():
                pipeline.release_run_lock(lock_file)
            RUN_LOCK.release()
            raise
        self.send_json(202, {"status": "running"})


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 8765), DashboardHandler)
    print("Insurance demo at http://127.0.0.1:8765", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopping server", flush=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
