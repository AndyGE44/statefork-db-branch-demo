from __future__ import annotations

import json
import os
import shlex
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from .logic import EXPECTED_TOTAL, FIX_A_EXPECTED_TOTAL, PROBE_PRODUCT_ID, build_status
from . import redis_wire, search_index


class StateForkDemoError(RuntimeError):
    pass


class StateForkArm:
    def __init__(
        self,
        project_root: Path,
        redis_port: int = 6397,
        statefork_root: Path | None = None,
        sessions_dir: Path | None = None,
    ) -> None:
        self.project_root = Path(project_root)
        self.redis_port = redis_port
        self.statefork_root = Path(os.getenv("DEMO_STATEFORK_ROOT", str(statefork_root or Path.home() / "Andy_StateFork")))
        self.sessions_dir = Path(os.getenv("CHECKPOINT_SESSIONS_DIR", str(sessions_dir or Path("/tmp/checkpoint-sessions-db-branch-demo"))))
        self.manager: Any | None = None
        self.snapshot_id: str | None = None
        self.phase = "idle"
        self.expected_unit = "dollars"
        self.expected_total = EXPECTED_TOTAL
        self.expected_total = EXPECTED_TOTAL

    def cleanup(self) -> None:
        if self.manager is not None:
            try:
                self._call_statefork(self.manager.cleanup)
            except Exception:
                pass
        self.manager = None
        self.snapshot_id = None
        self.phase = "idle"
        self.expected_unit = "dollars"

    def setup(self) -> dict[str, Any]:
        self.cleanup()
        redis_wire.shutdown(self.redis_port)
        self.manager = self._create_manager()
        self._start_session_redis()
        self._session_op("setup")
        self.phase = "setup"
        self.expected_unit = "dollars"
        self.expected_total = EXPECTED_TOTAL
        return self.state()

    def warm(self) -> dict[str, Any]:
        self._require_manager()
        self._session_op("warm")
        self.phase = "warm"
        self.expected_unit = "dollars"
        self.expected_total = EXPECTED_TOTAL
        return self.state()

    def snapshot(self) -> dict[str, Any]:
        manager = self._require_manager()
        snapshot_id = self._call_statefork(manager.snapshot)
        if not snapshot_id:
            raise StateForkDemoError("StateFork snapshot failed")
        self.snapshot_id = str(snapshot_id)
        self.phase = "snapshot"
        self.expected_unit = "dollars"
        self.expected_total = EXPECTED_TOTAL
        return self.state()

    def fix_a(self) -> dict[str, Any]:
        self._require_manager()
        self._session_op("fix_a")
        self.phase = "fix_a"
        self.expected_unit = "cents"
        self.expected_total = FIX_A_EXPECTED_TOTAL
        return self.state()

    def rollback(self) -> dict[str, Any]:
        manager = self._require_manager()
        if not self.snapshot_id:
            raise StateForkDemoError("StateFork snapshot has not been created")
        ok = self._call_statefork(lambda: manager.restore(self.snapshot_id))
        if ok is False:
            raise StateForkDemoError(f"StateFork restore failed for {self.snapshot_id}")
        self.phase = "rollback"
        self.expected_unit = "dollars"
        self.expected_total = EXPECTED_TOTAL
        return self.state()

    def checkout(self) -> dict[str, Any]:
        return self.state()

    def state(self) -> dict[str, Any]:
        self._require_manager()
        raw = self._session_op("state")
        return build_status(
            arm="statefork",
            backend="StateFork + Waypoint whole-session restore (SQLite DB + FTS index)",
            phase=self.phase,
            expected_unit=self.expected_unit,
            expected_total=self.expected_total,
            db_raw_total=raw.get("db_raw_total"),
            product_raw_price=raw.get("product_raw_price"),
            product_name=raw.get("product_name"),
            config_unit=raw.get("config_unit", "missing"),
            cached_raw=raw.get("cached_raw"),
            indexed_product=raw.get("indexed_product"),
            snapshot_id=self.snapshot_id,
            note="The StateFork arm uses SQLite because Dolt daemon checkpointing was not assumed; Waypoint restores DB file, schema file, Redis, and the FTS search index together.",
        )

    def search(self, query: str = search_index.PROBE_QUERY) -> dict[str, Any]:
        payload = self._session_op("search", query=query)
        return {"arm": "statefork", **payload}

    def product(self, product_id: int = PROBE_PRODUCT_ID) -> dict[str, Any]:
        payload = self._session_op("product", product_id=product_id)
        return {"arm": "statefork", **payload}

    def _require_manager(self):
        if self.manager is None:
            raise StateForkDemoError("StateFork arm is not set up. Click setup first.")
        return self.manager

    def _create_manager(self):
        if not self.statefork_root.exists():
            raise StateForkDemoError(f"StateFork root does not exist: {self.statefork_root}")
        os.environ.setdefault("CHECKPOINT_SESSIONS_DIR", str(self.sessions_dir))
        os.environ.setdefault("WAYPOINT_SESSIONS_DIR", os.environ["CHECKPOINT_SESSIONS_DIR"])
        os.environ.setdefault("WAYPOINT_PRESERVE_SESSION_ON_CLEANUP", "true")
        root = str(self.statefork_root)
        if root not in sys.path:
            sys.path.insert(0, root)
        try:
            from controller import create_env_manager
        except Exception as exc:
            raise StateForkDemoError(f"Could not import StateFork controller: {exc}") from exc
        return self._call_statefork(
            lambda: create_env_manager(
                "ckpt_build",
                dockerfile_dir=str(self.project_root),
                build=True,
            )
        )

    def _session_op(
        self,
        name: str,
        *,
        query: str | None = None,
        product_id: int | None = None,
    ) -> dict[str, Any]:
        manager = self._require_manager()
        extra = ""
        if query is not None:
            extra += f" --query {shlex.quote(query)}"
        if product_id is not None:
            extra += f" --product-id {int(product_id)}"
        cmd = (
            "cd /demo && "
            f"python3 -m statefork_demo.session_ops {shlex.quote(name)} "
            f"--port {int(self.redis_port)}{extra}"
        )
        rc, stdout, stderr = self._call_statefork(lambda: manager.exec_command(cmd, timeout=20))
        if rc != 0:
            raise StateForkDemoError(f"session op {name!r} failed: {stderr or stdout}")
        lines = [line for line in stdout.splitlines() if line.strip()]
        if not lines:
            raise StateForkDemoError(f"session op {name!r} returned no JSON")
        for line in reversed(lines):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
        raise StateForkDemoError(f"session op {name!r} returned no JSON object: {stdout!r}")

    def _start_session_redis(self) -> None:
        manager = self._require_manager()
        port = int(self.redis_port)
        pid_file = f"/tmp/statefork-demo/redis-{port}.pid"
        log_file = f"/tmp/statefork-demo/redis-{port}.log"
        command = (
            "mkdir -p /demo/session_state /tmp/statefork-demo; "
            f"if [ -f {pid_file} ]; then oldpid=$(cat {pid_file} || true); "
            "if [ -n \"$oldpid\" ]; then kill \"$oldpid\" 2>/dev/null || true; fi; fi; "
            f"redis-server --port {port} --bind 127.0.0.1 --save '' --appendonly no --dir /tmp --logfile {log_file} "
            f"</dev/null >/tmp/statefork-demo/redis-{port}.stdout 2>&1 & "
            f"echo $! > {pid_file}; ready=0; "
            f"for i in 1 2 3 4 5 6 7 8 9 10; do "
            f"if redis-cli -p {port} ping >/dev/null 2>&1; then ready=1; break; fi; "
            "sleep 0.2; done; echo REDIS_READY=$ready; test \"$ready\" = 1"
        )
        rc, stdout, stderr = self._call_statefork(lambda: manager.exec_command(command, timeout=10))
        if rc != 0 or "REDIS_READY=1" not in stdout:
            raise StateForkDemoError(f"could not start session redis: {stderr or stdout}")

    def _call_statefork(self, fn: Callable[[], Any]) -> Any:
        with self._statefork_cwd():
            return fn()

    @contextmanager
    def _statefork_cwd(self) -> Iterator[None]:
        previous = Path.cwd()
        os.chdir(self.statefork_root)
        try:
            yield
        finally:
            os.chdir(previous)
