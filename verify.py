from __future__ import annotations

import os
import sys
from pathlib import Path

from statefork_demo.db_only_arm import DoltDbOnlyArm
from statefork_demo.statefork_arm import StateForkArm


def ensure_root() -> None:
    if hasattr(os, "geteuid") and os.geteuid() != 0 and os.environ.get("STATEFORK_DEMO_SKIP_SUDO") != "1":
        os.execvp("sudo", ["sudo", "-E", sys.executable, *sys.argv])


def run_db(project_root: Path) -> dict:
    arm = DoltDbOnlyArm(project_root)
    arm.setup()
    arm.warm()
    arm.snapshot()
    after_fix = arm.fix_a()
    final = arm.rollback()
    final = arm.checkout()
    assert after_fix["verdict"] == "PASS", after_fix
    assert final["verdict"] == "FAIL", final
    assert final["db_unit"] == "dollars", final
    assert final["config_unit"] == "cents", final
    assert final["cache_unit"] == "cents", final
    assert final["display_total_money"] == "$0.40", final
    return final


def run_statefork(project_root: Path) -> dict:
    arm = StateForkArm(project_root)
    try:
        arm.setup()
        arm.warm()
        arm.snapshot()
        after_fix = arm.fix_a()
        final = arm.rollback()
        final = arm.checkout()
        assert after_fix["verdict"] == "PASS", after_fix
        assert final["verdict"] == "PASS", final
        assert final["db_unit"] == "dollars", final
        assert final["config_unit"] == "dollars", final
        assert final["cache_unit"] == "dollars", final
        assert final["display_total_money"] == "$39.98", final
        return final
    finally:
        arm.cleanup()


def main() -> None:
    ensure_root()
    project_root = Path(__file__).resolve().parent
    os.environ.setdefault("PYTHONPATH", str(project_root))
    os.environ.setdefault("DEMO_STATEFORK_ROOT", "/users/alexxjk/Andy_StateFork")
    os.environ.setdefault("CHECKPOINT_SESSIONS_DIR", "/tmp/checkpoint-sessions-db-branch-demo-verify")
    os.environ.setdefault("WAYPOINT_SESSIONS_DIR", os.environ["CHECKPOINT_SESSIONS_DIR"])
    os.environ.setdefault("WAYPOINT_PRESERVE_SESSION_ON_CLEANUP", "true")

    db = run_db(project_root)
    print("DB-only arm:", db["verdict"], db["display_total_money"], f"db={db['db_unit']}", f"config={db['config_unit']}", f"cache={db['cache_unit']}")
    sf = run_statefork(project_root)
    print("StateFork arm:", sf["verdict"], sf["display_total_money"], f"db={sf['db_unit']}", f"config={sf['config_unit']}", f"cache={sf['cache_unit']}")
    print("Verification reproduced the intended contrast: DB-only FAIL, StateFork PASS.")


if __name__ == "__main__":
    main()
