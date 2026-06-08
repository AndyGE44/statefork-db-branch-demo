# StateFork vs. DB-Only Branch Rollback Demo

This is a runnable VM demo for the scope argument in `statefork-demo-plan.md`,
now extended with the search/read-model index layer from
`statefork-demo-plan-2-search-index.md`. It is deliberately small: a checkout
service and storefront have four local stateful layers that must move together.

| Layer | Warm state | Fix A state |
| --- | --- | --- |
| Database | `Wireless Mouse`, price stored as dollars: `19.99` | `Wireless Mouse Pro`, price stored as cents: `2499` |
| Filesystem artifact | `state/schema_version.json` says `dollars` | it says `cents` |
| Cache process | Redis key `cart:1:total` is `39.98` | Redis key is `4998` |
| Search/read-model index | separate SQLite FTS file shows `Wireless Mouse $19.99` | index is rebuilt and shows `Wireless Mouse Pro $24.99` |

Dolt and Neon-style database branches are not being portrayed as buggy. They do
what their boundary promises: they restore database state. The demo shows what
happens when an agent retry depends on state outside that boundary. After Fix A,
rolling back only Dolt returns the DB to dollars, but the config file, Redis
cache, and external search index still reflect Fix A. The checkout code reads
the stale config and silently computes `$0.40`; the storefront search listing
also shows `$24.99` while the DB product detail is back to `$19.99`.

The StateFork arm uses `Andy_StateFork` with the Waypoint/checkpoint-lite backend
on this VM. Per Andy's note, it uses SQLite rather than Dolt inside the
StateFork-managed session, because the question being demonstrated is whole
session restore, not Dolt daemon checkpointing. Redis is still a real child
process inside that managed shell/session, and the SQLite FTS index file lives
inside the session so Waypoint can restore it.

## Why Add The Search Index

The Redis cache already shows warm derived state drifting. The search index is
the production-standard version of the same problem: a storefront listing often
reads a separate derived index kept in sync by CDC, an outbox, a cron job, or a
batch reindex. It is intentionally not the source-of-truth database.

Two precision rules matter:

1. The index is a separate datastore, not a table inside Dolt. In the DB-only arm
   it lives at `runs/db_only/index/product_search.db`, outside the Dolt checkout.
   If the FTS table lived inside Dolt, Dolt rollback would restore it too, and
   the demo would prove nothing.
2. DB rollback does not reindex. A database branch operation should not be
   expected to run a search pipeline backward. An operator could manually
   reindex later, but that is extra recovery work outside the DB-branch
   primitive. StateFork/Waypoint restores the index file atomically with the
   rest of the local session.

Out of scope: if the index lived in a remote managed service such as OpenSearch,
neither a DB branch nor a local whole-environment snapshot would automatically
rewind it. That is the non-local-state boundary; this demo uses a local index so
StateFork can legitimately capture and restore it.

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

## Stop The Web Demo

If `./run_demo.sh` is running in the foreground, press `Ctrl-C` in that VM
terminal.

If the demo is running in the VM tmux session used for this repo, stop it with:

```bash
tmux kill-session -t statefork-db-demo
```

If the service was started another way and is still listening on port `8015`,
stop the listener on the VM with:

```bash
sudo lsof -tiTCP:8015 -sTCP:LISTEN | xargs -r sudo kill
```

If you created the SSH forwarding tunnel from your laptop, stop that local
`ssh -N -L 8015:127.0.0.1:8015 sf-exp` command with `Ctrl-C` in the tunnel
terminal.

Click through:

1. Setup & Warm
2. Snapshot
3. Apply Fix A
4. Roll Back
5. Check Fix B Baseline

Expected final result:

- Left column, DB-only rollback: red `FAIL`, checkout `$0.40`, DB=dollars,
  config=cents, cache=cents, search index=`Wireless Mouse Pro $24.99`, DB
  product detail=`Wireless Mouse $19.99`.
- Right column, StateFork + Waypoint: green `PASS`, checkout `$39.98`, all local
  layers restored to dollars, search index=`Wireless Mouse $19.99`, DB product
  detail=`Wireless Mouse $19.99`.

## Headless Verification

```bash
cd ~/statefork-db-branch-demo
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
sudo -E .venv/bin/python verify.py
```

The script exits non-zero if the intended contrast is not reproduced, including
the search-index assertion:

```text
DB-only arm: FAIL $0.40 ... index=$24.99 vs source=$19.99
StateFork arm: PASS $39.98 ... index=$19.99 vs source=$19.99
```

## What Is Real Here

- The DB-only arm uses real Dolt branch/checkout operations.
- The cache layer uses a real Redis server process.
- The filesystem artifact is a real JSON file read at checkout time.
- The search/read-model layer is a real, separate SQLite FTS5 database file.
- The StateFork arm creates a real `ckpt_build` manager from `Andy_StateFork`,
  starts Redis inside the managed shell, snapshots the session, mutates SQLite +
  Redis + JSON + FTS index, and restores via Waypoint/checkpoint-lite.

## Honesty Notes

The StateFork arm uses SQLite because the user asked to avoid assuming StateFork
can control Dolt. That keeps the demonstration focused on the branch boundary:
DB-only rollback covers the database layer, while StateFork/Waypoint covers the
local process, filesystem, cache, index, and shell/session state together.

If Waypoint cannot checkpoint a live Redis process or restore the SQLite FTS
file on a particular VM/kernel, `verify.py` will fail on the StateFork arm rather
than silently marking both arms successful. That is intentional: the demo should
not claim whole-session restore unless the real backend reproduces it.
