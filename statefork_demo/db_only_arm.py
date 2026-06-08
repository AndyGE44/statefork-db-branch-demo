from __future__ import annotations

import csv
import io
import shutil
import subprocess
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

from . import redis_wire
from .logic import CACHE_KEY, build_status, read_config, write_config


class DemoError(RuntimeError):
    pass


class DoltDbOnlyArm:
    def __init__(self, project_root: Path, redis_port: int = 6396) -> None:
        self.project_root = Path(project_root)
        self.run_dir = self.project_root / "runs" / "db_only"
        self.repo_dir = self.run_dir / "dolt_checkout"
        self.state_dir = self.run_dir / "state"
        self.redis_port = redis_port
        self.phase = "idle"
        self.expected_unit = "dollars"
        self.snapshot_branch = "warm_snapshot"

    def cleanup(self) -> None:
        redis_wire.shutdown(self.redis_port)
        time.sleep(0.1)
        if self.run_dir.exists():
            shutil.rmtree(self.run_dir)
        self.phase = "idle"
        self.expected_unit = "dollars"

    def setup(self) -> dict[str, Any]:
        self.cleanup()
        self.repo_dir.mkdir(parents=True, exist_ok=True)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._run(["dolt", "init", "--name", "StateFork Demo", "--email", "statefork-demo@example.com"])
        self._sql(
            """
            CREATE TABLE products (
                id INT PRIMARY KEY,
                name VARCHAR(100) NOT NULL,
                price DECIMAL(10,2) NOT NULL
            );
            CREATE TABLE carts (id INT PRIMARY KEY);
            CREATE TABLE cart_items (
                cart_id INT NOT NULL,
                product_id INT NOT NULL,
                quantity INT NOT NULL,
                PRIMARY KEY (cart_id, product_id)
            );
            INSERT INTO products VALUES (1, 'Widget', 19.99);
            INSERT INTO carts VALUES (1);
            INSERT INTO cart_items VALUES (1, 1, 2);
            """
        )
        write_config(self.state_dir, "dollars")
        self._start_redis()
        redis_wire.flushdb(self.redis_port)
        self._run(["dolt", "add", "."])
        self._run(["dolt", "commit", "-m", "seed dollars checkout"])
        self.phase = "setup"
        self.expected_unit = "dollars"
        return self.state()

    def warm(self) -> dict[str, Any]:
        self._require_repo()
        self._write_cache_from_db()
        self.phase = "warm"
        self.expected_unit = "dollars"
        return self.state()

    def snapshot(self) -> dict[str, Any]:
        self._require_repo()
        branches = self._run(["dolt", "branch"], check=True).stdout
        names = {line.strip().lstrip("* ").strip() for line in branches.splitlines()}
        if self.snapshot_branch in names:
            self._run(["dolt", "branch", "-D", self.snapshot_branch])
        self._run(["dolt", "branch", self.snapshot_branch])
        self.phase = "snapshot"
        self.expected_unit = "dollars"
        return self.state()

    def fix_a(self) -> dict[str, Any]:
        self._require_repo()
        current = self._run(["dolt", "branch", "--show-current"]).stdout.strip()
        if current != "main":
            self._run(["dolt", "checkout", "main"])
        self._sql("UPDATE products SET price = ROUND(price * 100, 0);")
        self._run(["dolt", "add", "."])
        self._run(["dolt", "commit", "-m", "fix_a migrate prices to cents"])
        write_config(self.state_dir, "cents")
        self._write_cache_from_db()
        self.phase = "fix_a"
        self.expected_unit = "cents"
        return self.state()

    def rollback(self) -> dict[str, Any]:
        self._require_repo()
        self._run(["dolt", "checkout", self.snapshot_branch])
        self.phase = "rollback"
        self.expected_unit = "dollars"
        return self.state()

    def checkout(self) -> dict[str, Any]:
        return self.state()

    def state(self) -> dict[str, Any]:
        self._require_repo()
        raw = self._cart_raw_values()
        cached = redis_wire.get(self.redis_port, CACHE_KEY) if redis_wire.ping(self.redis_port) else None
        return build_status(
            arm="db",
            backend="Dolt branch rollback only",
            phase=self.phase,
            expected_unit=self.expected_unit,
            db_raw_total=raw["db_raw_total"],
            product_raw_price=raw["product_raw_price"],
            config_unit=read_config(self.state_dir),
            cached_raw=cached,
            snapshot_id=self.snapshot_branch if self.phase in {"snapshot", "fix_a", "rollback"} else None,
            note="Dolt restored the database branch; Redis and schema_version.json are intentionally outside that branch.",
        )

    def _require_repo(self) -> None:
        if not (self.repo_dir / ".dolt").exists():
            raise DemoError("DB-only arm is not set up. Click setup first.")

    def _run(self, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                args,
                cwd=self.repo_dir,
                check=check,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise DemoError(f"Missing command: {args[0]}") from exc
        except subprocess.CalledProcessError as exc:
            output = (exc.stderr or exc.stdout or str(exc)).strip()
            raise DemoError(f"Command failed: {' '.join(args)}\n{output}") from exc

    def _sql(self, query: str) -> None:
        self._run(["dolt", "sql", "-q", " ".join(query.split())])

    def _sql_csv(self, query: str) -> list[dict[str, str]]:
        completed = self._run(["dolt", "sql", "-r", "csv", "-q", " ".join(query.split())])
        return list(csv.DictReader(io.StringIO(completed.stdout)))

    def _cart_raw_values(self) -> dict[str, Decimal]:
        rows = self._sql_csv(
            """
            SELECT SUM(products.price * cart_items.quantity) AS db_raw_total,
                   MAX(products.price) AS product_raw_price
            FROM cart_items
            JOIN products ON products.id = cart_items.product_id
            WHERE cart_items.cart_id = 1;
            """
        )
        if not rows:
            raise DemoError("cart query returned no rows")
        return {
            "db_raw_total": Decimal(rows[0]["db_raw_total"]),
            "product_raw_price": Decimal(rows[0]["product_raw_price"]),
        }

    def _write_cache_from_db(self) -> None:
        raw = self._cart_raw_values()["db_raw_total"]
        value = str(raw.quantize(Decimal("1"))) if raw == raw.to_integral() else format(raw.normalize(), "f")
        redis_wire.set(self.redis_port, CACHE_KEY, value)

    def _start_redis(self) -> None:
        if redis_wire.ping(self.redis_port):
            redis_wire.flushdb(self.redis_port)
            return
        self.run_dir.mkdir(parents=True, exist_ok=True)
        log_path = self.run_dir / "redis.log"
        process = subprocess.Popen(
            [
                "redis-server",
                "--port",
                str(self.redis_port),
                "--bind",
                "127.0.0.1",
                "--save",
                "",
                "--appendonly",
                "no",
                "--dir",
                str(self.run_dir),
                "--logfile",
                str(log_path),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        (self.run_dir / "redis.pid").write_text(str(process.pid))
        redis_wire.wait_until_ready(self.redis_port)
