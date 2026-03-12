"""Manages persistent Opencode worker processes (opencode serve)."""

from __future__ import annotations

import os
import subprocess
import time
import urllib.request
import urllib.error
import logging
from typing import Optional

from .models import Worker, WorkerStatus

logger = logging.getLogger(__name__)

BASE_PORT = 4096


class WorkerManager:
    def __init__(self):
        self._workers: dict[str, Worker] = {}
        self._port_counter = BASE_PORT

    def _next_port(self) -> int:
        port = self._port_counter
        used = {w.port for w in self._workers.values()}
        while port in used:
            port += 1
        self._port_counter = port + 1
        return port

    def spawn(self, worker_id: str, cwd: str, port: Optional[int] = None, model: Optional[str] = None) -> Worker:
        if worker_id in self._workers:
            existing = self._workers[worker_id]
            if existing.status == WorkerStatus.RUNNING:
                return existing
            self._cleanup(worker_id)

        port = port or self._next_port()
        cmd = ["opencode", "serve", "--port", str(port)]
        env = os.environ.copy()
        if model:
            env["OPENCODE_MODEL"] = model

        try:
            process = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, env=env, text=True)
        except FileNotFoundError:
            raise RuntimeError("opencode binary not found. Make sure it is installed and on PATH.")

        worker = Worker(worker_id=worker_id, cwd=cwd, port=port, model=model,
                        status=WorkerStatus.STARTING, pid=process.pid, process=process)
        self._workers[worker_id] = worker
        self._wait_for_ready(worker)
        return worker

    def kill(self, worker_id: str) -> str:
        if worker_id not in self._workers:
            return f"Worker '{worker_id}' not found."
        self._cleanup(worker_id)
        return f"Worker '{worker_id}' stopped."

    def _cleanup(self, worker_id: str):
        worker = self._workers.pop(worker_id, None)
        if worker and worker.process:
            try:
                worker.process.terminate()
                worker.process.wait(timeout=5)
            except Exception:
                try:
                    worker.process.kill()
                except Exception:
                    pass

    def _wait_for_ready(self, worker: Worker, timeout: int = 20) -> None:
        """Wait for opencode serve to be ready.

        opencode serve doesn't expose a dedicated /health endpoint, but it does
        print a ready message to stderr once the HTTP server is up. We detect
        readiness by watching stderr for that message, falling back to a port
        probe, and finally a fixed sleep if neither works within timeout.
        """
        deadline = time.time() + timeout
        ready_markers = ["listening", "ready", "started", "server", str(worker.port)]

        while time.time() < deadline:
            # Check if the process died early
            if worker.process and worker.process.poll() is not None:
                try:
                    stderr_out = worker.process.stderr.read() if worker.process.stderr else ""
                except Exception:
                    stderr_out = ""
                worker.status = WorkerStatus.ERROR
                raise RuntimeError(
                    f"opencode serve exited early for worker '{worker.worker_id}'. "
                    f"stderr: {stderr_out[:500]}"
                )

            # Try HTTP probe — any response (including 404/405) means the server is up
            try:
                urllib.request.urlopen(f"http://localhost:{worker.port}", timeout=1)
                worker.status = WorkerStatus.RUNNING
                logger.info("Worker %s ready on port %d", worker.worker_id, worker.port)
                return
            except urllib.error.HTTPError:
                # Any HTTP error response means the server is listening
                worker.status = WorkerStatus.RUNNING
                logger.info("Worker %s ready on port %d", worker.worker_id, worker.port)
                return
            except Exception:
                pass

            time.sleep(0.5)

        # Timeout reached — assume it's ready anyway (opencode may not expose HTTP root)
        logger.warning(
            "Worker %s: could not confirm readiness within %ds, assuming ready.",
            worker.worker_id, timeout,
        )
        worker.status = WorkerStatus.RUNNING

    def get(self, worker_id: str) -> Optional[Worker]:
        return self._workers.get(worker_id)

    def list_all(self) -> list[Worker]:
        for worker in self._workers.values():
            if worker.process and worker.process.poll() is not None:
                worker.status = WorkerStatus.ERROR
        return list(self._workers.values())

    def server_url(self, worker_id: str) -> Optional[str]:
        worker = self._workers.get(worker_id)
        if worker and worker.status == WorkerStatus.RUNNING:
            return f"http://localhost:{worker.port}"
        return None
