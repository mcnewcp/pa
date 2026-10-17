# LadybugDB feasibility spike (#35)

Run on 2026-10-11 against the devbox and `pa-home`, with throwaway builds of a graph shaped like #34's schema: a hand-made Argus-flavoured graph (7 people, 10 statements) and a synthetic one at the scale corpus's expected size (738 episodes, 2,537 statements, 350 people, 4 FTS indexes and one HNSW index, 25 MB). Builds were written on the devbox and read in the VM through a read-only virtiofs share at `/srv/pa/graph`, attached the way `mount-store.sh` attaches L0.

## Pinned version

- **Python:** `ladybug==0.21.2` (PyPI name `ladybug`; `real_ladybug` is the old name, stuck at 0.15.3). Pinned in `adapters/home/pyproject.toml`. Wheels for cp312–cp314; the devbox built with 3.12 and the VM read with 3.14.
- **Extensions:** 0.21.x loads extensions built for `v0.21.0`, from `https://extension.ladybugdb.com/v0.21.0/linux_amd64/<name>/lib<name>.lbug_extension`:

  | Extension | SHA-256 | Bytes |
  |---|---|---|
  | `fts` | `742f2756c2f80bcd1886b6ee0446483038cff0ecf6cf47f698bc6fe855cbaef6` | 2,238,216 |
  | `vector` | `571726cc2ea0d202df1909ae5a3b0528c9dea3ea8d40be6c16b310ec14aa8821` | 1,168,352 |

- **Explorer:** `@ladybugdb/core@0.21.2` (npm, with its `@ladybugdb/core-linux-x64` prebuilt), on LadybugDB/explorer at commit `cc507f0cbbeec7691044c0d65a2bf5a87b3a26c7`.

## Results

| # | Check | Result |
|---|---|---|
| 1 | A checkpointed, closed build opens read-only in the VM from a read-only virtiofs mount | **Pass** |
| 2 | A checkpointed, closed build is one file, with no WAL or sidecar | **Pass**, with two rules for the builder |
| 3 | A long-running reader follows an atomic `current` swap without restarting, and the old build can then be deleted | **Pass** |
| 4 | Explorer runs on the host against a build, read-only, with `/api/db` and `/api/gpt` disabled | **Pass**, but it doesn't follow `current` |
| 5 | FTS and vector indexes built at build time, queried read-only, extensions from a pinned local path; 4 domain queries and 2 open-ended questions | **Pass**, with an engine bug to work around |

1. **Read-only open from the mount.** `Database(path, read_only=True)` opened both builds in the VM straight from `/srv/pa/graph/current/graph.lbdb`, in 0.2 s (hand-made) and 0.6 s (scale), and every query below ran. Writes were refused by the engine (`Cannot execute write operations in a read-only database!`). `CHECKPOINT` was refused by the mount itself (`…graph.lbdb.checkpoint.intent.lock: Read-only file system`), even with the build writable on the devbox. Two processes held the same build open read-only at once. No copy is needed. For the record, copying the 25 MB scale build to the VM's disk took 0.17 s cold and 0.06 s warm.
2. **No sidecar.** While building there is a `graph.lbdb.wal` (2 MB after 20k inserts). `CHECKPOINT` folds it in and removes it, and `close()` leaves only `graph.lbdb`. A build killed before `close()` leaves its `.wal`, and **a read-only open silently replays a stale WAL in memory**: the reader saw the write that was never checkpointed. Also, **`CHECKPOINT` on a read-only database creates `graph.lbdb.wal` and `graph.lbdb.shadow`** when the directory is writable (the main file is unchanged). So the builder must (a) refuse to publish unless the build directory holds exactly `graph.lbdb`, and (b) make the build read-only (`chmod a-w` on the file and its directory) before swapping. With the directory read-only, the same `CHECKPOINT` fails and creates nothing.
3. **Picking up builds.** A stand-in server in the VM resolved `current` (a relative symlink, `current -> builds/<id>`, replaced by `rename`) before each call. When it changed, it opened the new build read-only and closed the old one. Publishing the scale build was picked up on the next call (0.6 s to open), with no restart and no errors. The old build was then deleted on the devbox, and the reader carried on. A second reader that never switched also kept answering after its build was deleted, but only because the small build was cached, so keep the last few builds as #34 says.
4. **Explorer.** Built our own image (below). It opened the build read-only from a `:ro` bind mount and served the UI, schema, reads and a shortest path. `CREATE` and `CHECKPOINT` were refused, and `INSTALL` failed on the read-only root filesystem. `/api/db`, `/api/gpt`, `/api/reset` and `/api/import` returned 404. **It opens `current/graph.lbdb` once at start and keeps the old build after a swap.** It showed the new build only after a restart.
5. **Indexes and queries.** `CALL CREATE_FTS_INDEX` (people, organizations and topics on a flattened `search_text`; statements on `summary` and `quote`) and `CALL CREATE_VECTOR_INDEX` (statements, `FLOAT[8]`, cosine) ran at build time. `QUERY_FTS_INDEX` and `QUERY_VECTOR_INDEX` ran read-only in the VM, with both extensions loaded by path (`LOAD EXTENSION '<path>'`) from the VM's disk, and also from the read-only share. All six questions ran in Cypher: statements by a person, a topic's timeline (statements, calendar versions, loop openings and closings), open loops owed by someone, calendar events with every version, how Hannah connects to the owner (`[* SHORTEST 1..8]` and `[* ALL SHORTEST 1..8]`), and who is involved in both the kitchen and the wedding (`EXISTS { … }` twice). Each took 1–50 ms on the mount.
   - **Bug: a second `$`-parameterized `QUERY_FTS_INDEX` on the same connection segfaults the process** ([LadybugDB/ladybug#1105](https://github.com/LadybugDB/ladybug/issues/1105)). The first call works. The fix (LadybugDB/extensions#94, 2026-10-07) is merged but not released; the `v0.21.0` extension on the server predates it. A fresh `Connection` per call (20 calls in a row) and a literal query string both avoid it. Vector queries and other parameterized queries re-ran fine on one connection.

## Runaway queries

On the scale build in the VM (4 vCPU, 5.8 GiB), each run in a cgroup scope as a backstop:

| Query and limits | What happened |
|---|---|
| `MATCH p = (a:Person)-[*]-(b:Person) RETURN count(*)`, defaults | RSS grew about 40 MB/s (0.6 GB at 10 s, 1.8 GB at 40 s) until the 2 GiB cgroup killed it at about 45 s. The default buffer pool is 80% of RAM, so uncapped it would take about 4.6 GiB of the VM. `[*]` is bounded only by `var_length_extend_max_depth` (default 30). |
| same, `conn.set_query_timeout(3000)` | `RuntimeError: Interrupted.` at 3.0 s, peak RSS 0.3 GB, and the next query on the connection worked |
| same, `conn.interrupt()` from a thread at 3 s | the same |
| same, `buffer_pool_size` 256 MB, 512 MB or 1 GB | `Buffer manager exception: Unable to allocate memory! The buffer pool is full…` within 0.1–0.4 s, RSS under 135 MB, and the next query worked |
| `[* ALL SHORTEST 1..30]` over all people, timeout 3 s | Interrupted at 3.0 s |
| A cross product of 2,537³ rows with `count(*)` | 0.0 s (factorized): a big row count isn't the same as a slow query |
| Real path queries (`SHORTEST`, `ALL SHORTEST 1..8`, `*1..4`, two `EXISTS`) at 256 MB | All under 50 ms |
| `CALL timeout=0; <slow query>` after `set_query_timeout(2000)` | **Ran for 6.7 s.** One string carries several statements, and a config `CALL` undoes the API timeout. |

So a small explicit `buffer_pool_size` with `set_query_timeout` makes runaways fail fast and cleanly in-process. A wall-clock kill of the worker is still the backstop, since the Cypher text can lift the timeout unless it is rejected first.

## Statements a read-only database still runs

All of these were run against a read-only database in the VM:

- `COPY (…) TO '<file>'` **wrote the file.**
- `INSTALL json` downloaded the extension into `~/.lbdb/`.
- `ATTACH '<db>' AS g2 (dbtype lbug)` attached.
- `LOAD FROM '<file>' (file_format=…)` reads local files, and the Python API's `LOAD FROM <name>` scans Python objects in the caller's frames.
- `CALL <option>=<value>` (`timeout`, `threads`, `var_length_extend_max_depth`, `home_directory`, `file_search_path`, …) changes connection settings.
- `EXPORT DATABASE` failed here only because a rel table has several FROM/TO pairs.

## Explorer image

The upstream image is `ghcr.io/ladybugdb/explorer`, on `@ladybugdb/core` 0.19.1, and it can't read 0.21 builds. Ours is built from the explorer's source at a pinned commit:

- `@ladybugdb/core` and `@ladybugdb/wasm-core` are set to the pinned version, and `openai` is removed.
- The `/gpt` and `/db` routers are deleted from `src/server/API.js`, so those routes don't exist, whatever the proxy does. In `READ_ONLY` mode `/reset` and `/import` aren't mounted either.
- It's built with `npm install` and `npm run build`, on `node:20-trixie-slim` (`sha256:abfbe12cc943141a0c9e8c0a57d710df1dadd95d35e8662cc02958b284d1f35b`). It is 2 GB as one stage.
- It runs with:
  - `MODE=READ_ONLY`;
  - the graph directory mounted `:ro` at `/database`, with `LBUG_FILE=current/graph.lbdb`;
  - `LBUG_BUFFER_POOL_SIZE` and `LBUG_QUERY_TIMEOUT` set;
  - `--read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges --user <uid>`;
  - published on `127.0.0.1` only.

For #46: commit a `package-lock.json` and use `npm ci`, use a multi-stage build, and restart the explorer after each publish. Whether `tailscale serve` can refuse a path wasn't tested. With the routes removed it doesn't need to.

## Changes to #34

1. **Publish mechanics: the read-only mount, no VM-local copy.** The copy-on-start fallback isn't needed.
2. **The builder** refuses to publish unless the build directory holds only `graph.lbdb`, and makes the build read-only before the swap (check 2). Nothing ever opens a published build read-write.
3. **The PA MCP server uses a fresh `Connection` per call** until a release carries the FTS fix (#1105). Since an engine segfault ends the whole process, consider running the domain queries and `find` in the guarded worker too, not only `query`. Today #34 has them in the server process.
4. **The `query` tool's allowlist also rejects:**
   - more than one statement per call;
   - standalone `CALL <option>=…`, which can lift the timeout;
   - `COPY … TO`, `ATTACH`, `INSTALL`, `LOAD`, `UPDATE`, `UNINSTALL`, `LOAD FROM`, `EXPORT` and `IMPORT`, on top of #34's write list. A read-only database alone stops none of these.
   - The worker also sets an explicit `buffer_pool_size` (256–512 MB was enough for real path queries) and `set_query_timeout`, and is killed on a wall clock.
5. **Explorer:** it must be restarted after each publish, because it doesn't follow `current`. The routes are disabled by removing them in our image.
6. **Extensions are needed in the VM as well as on the devbox,** because eval builds run the librarian in the VM (#34 user story 81). Fetch the two files above from the `v0.21.0` path, check their SHA-256, and put them at a fixed local path on each machine (for example `~/.local/share/pa/lbug-extensions/0.21.0/linux_amd64/`). Load them with `LOAD EXTENSION '<path>'` once per `Database`; that covers every connection to it. Never `INSTALL`. Loading from the read-only graph share also works.
7. **Full-text lookup:**
   - FTS indexes only `STRING` properties, so names, aliases and addresses need a flattened text column.
   - The default tokenizer drops digits, and a term found in every document scores 0 and returns every document.
   - So `find` should match exact addresses and aliases in plain Cypher first, and drop FTS hits with score 0.

None of these reopens ADR-0005: no fallback was needed.
