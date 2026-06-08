# StateFork vs. DB-Only Branch Rollback Demo

This is a runnable VM demo for the scope argument in `statefork-demo-plan.md`.
It is deliberately small: a checkout service has three stateful layers that must
move together.

| Layer | Warm state | Fix A state |
| --- | --- | --- |
| Database | price is stored as dollars: `19.99` | price is stored as cents: `1999` |
| Filesystem artifact | `state/schema_version.json` says `dollars` | it says `cents` |
| Cache process | Redis key `cart:1:total` is `39.98` | Redis key is `3998` |

Dolt and Neon-style database branches are not being portrayed as buggy. They do
what their boundary promises: they restore database state. The demo shows what
happens when an agent retry depends on state outside that boundary. After Fix A,
rolling back only Dolt returns the DB to dollars, but the config file and Redis
cache still say cents. The checkout code reads the config at request time and
silently computes `$0.40` instead of `$39.98`.

The StateFork arm uses `Andy_StateFork` with the Waypoint/checkpoint-lite backend
on this VM. Per Andy's note, it uses SQLite rather than Dolt inside the
StateFork-managed session, because the question being demonstrated is whole
session restore, not Dolt daemon checkpointing. Redis is still a real child
process inside that managed shell/session.

## Location

The project lives on the VM only:

```bash
/users/alexxjk/statefork-db-branch-demo
```

## Prerequisites On A Fresh Ubuntu VM

```bash
sudo apt-get update
sudo apt-get install -y python3.12-venv redis-server redis-tools lsof curl tar

# Dolt branchable DB representative
tmpdir=$(mktemp -d)
cd "$tmpdir"
curl -L --fail --show-error -o dolt.tar.gz \
  https://github.com/dolthub/dolt/releases/latest/download/dolt-linux-amd64.tar.gz
tar -xzf dolt.tar.gz
sudo install -m 0755 dolt-linux-amd64/bin/dolt /usr/local/bin/dolt
dolt version
```

The StateFork root expected by the scripts is:

```bash
/users/alexxjk/Andy_StateFork
```

That folder should contain symlinks to:

```bash
/users/alexxjk/Andy_checkpoint-lite/checkpoint-lite
/users/alexxjk/Andy_checkpoint-lite/bash_init
```

## Run The Web Demo

```bash
cd ~/statefork-db-branch-demo
./run_demo.sh
```

Open the printed URL on the VM, or forward it to your laptop:

```bash
ssh -N -L 8015:127.0.0.1:8015 sf-exp
```

Then open `http://127.0.0.1:8015` locally.

Click through:

1. Setup & Warm
2. Snapshot
3. Apply Fix A
4. Roll Back
5. Re-run Checkout

Expected final result:

- Left column, DB-only rollback: red `FAIL`, checkout `$0.40`, DB=dollars,
  config=cents, cache=cents.
- Right column, StateFork + Waypoint: green `PASS`, checkout `$39.98`, all three
  layers=dollars.

## Headless Verification

```bash
cd ~/statefork-db-branch-demo
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
sudo -E .venv/bin/python verify.py
```

The script exits non-zero if the intended contrast is not reproduced.

## What Is Real Here

- The DB-only arm uses real Dolt branch/checkout operations.
- The cache layer uses a real Redis server process.
- The filesystem artifact is a real JSON file read at checkout time.
- The StateFork arm creates a real `ckpt_build` manager from `Andy_StateFork`,
  starts Redis inside the managed shell, snapshots the session, mutates SQLite +
  Redis + JSON, and restores via Waypoint/checkpoint-lite.

## Honesty Notes

The StateFork arm uses SQLite because the user asked to avoid assuming StateFork
can control Dolt. That keeps the demonstration focused on the branch boundary:
DB-only rollback covers one layer, while StateFork/Waypoint covers the process,
filesystem, and shell/session state together.

If Waypoint cannot checkpoint a live Redis process on a particular VM/kernel,
`verify.py` will fail on the StateFork arm rather than silently marking both arms
successful. That is intentional: the demo should not claim whole-session restore
unless the real backend reproduces it.
