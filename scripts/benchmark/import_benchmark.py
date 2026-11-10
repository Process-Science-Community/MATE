"""Import + precompute benchmark against a running `make up` stack.

For each event log it:

1. stages and imports the file through the normal endpoints
   (`POST /api/v1/event-logs/stage` -> `POST /api/v1/event-logs`),
2. samples `docker stats` for the api container while the import job runs
   (peak memory),
3. reads events / cases / variants from `GET /api/v1/event-logs/{id}`,
4. waits for the eager precompute(s) the import triggered, then asks every
   remaining module that precomputes for the log to run (`POST .../module-runs/{id}`),
   one module at a time so runs never overlap,
5. reads each job's `started_at` / `finished_at` straight from the `jobs` table.

Raw results go to a JSON file; `render_benchmark.py` turns that into Markdown.

Requires DEMO_MODE=true in .env (the demo bearer token authenticates the calls).

Usage (repo root, stack up):
    uv run python scripts/benchmark/import_benchmark.py \\
        --log sepsis=path/Sepsis.xes.gz --log bpic2017=path/BPI2017.xes.gz \\
        --roles 'bpic2017={"activity": "activity"}' -o bench.json

`--roles key=JSON` pins column roles for one log, exactly like the import wizard's
role picker (`column_roles` on `POST /event-logs`); unpinned roles still auto-resolve.
"""

from __future__ import annotations

import argparse
import json
import platform
import re
import subprocess
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

API = "http://localhost:8000/api/v1"
TOKEN = "demo-access-token"
API_CONTAINER = "mate-api"
DB_CONTAINER = "mate-app-db"
TERMINAL_JOB = {"completed", "failed", "cancelled"}
TERMINAL_RUN = {"ready", "failed", "cancelled", "unavailable", "stale"}

_UNITS = {
    "b": 1,
    "kib": 2**10,
    "mib": 2**20,
    "gib": 2**30,
    "tib": 2**40,
    "kb": 10**3,
    "mb": 10**6,
    "gb": 10**9,
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _bytes(text: str) -> int:
    m = re.match(r"\s*([\d.]+)\s*([A-Za-z]+)", text)
    if not m:
        return 0
    return int(float(m.group(1)) * _UNITS.get(m.group(2).lower(), 1))


def _epoch(ts: str | None) -> float | None:
    # `jobs` timestamps are naive UTC (models._utcnow).
    if not ts:
        return None
    return datetime.fromisoformat(ts).replace(tzinfo=UTC).timestamp()


class MemSampler:
    """Streams `docker stats` MemUsage for one container as (unix time, bytes) samples.

    The stream is started a little before the job and stopped a little after it,
    so peaks are taken with `peak_between(job.started_at, job.finished_at)`, never
    over the whole capture: eager precompute starts within ~0.2 s of an import
    finishing and would otherwise leak into the import's figure.
    """

    def __init__(self, container: str) -> None:
        self.container = container
        self.series: list[tuple[float, int]] = []
        self._proc: subprocess.Popen[str] | None = None
        self._thread: threading.Thread | None = None

    def __enter__(self) -> MemSampler:
        self._proc = subprocess.Popen(
            ["docker", "stats", "--format", "{{.MemUsage}}", self.container],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        self._thread = threading.Thread(target=self._read, daemon=True)
        self._thread.start()
        return self

    def _read(self) -> None:
        assert self._proc and self._proc.stdout
        for line in self._proc.stdout:
            # The stream redraws with ANSI clear codes; keep the usage part.
            clean = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", line)
            used = clean.split("/")[0]
            if used.strip():
                self.series.append((time.time(), _bytes(used)))

    def peak_between(self, start: float | None, end: float | None) -> tuple[int | None, int]:
        """Peak bytes over samples taken in [start, end], and how many there were.

        A job shorter than the stream interval can contain no sample; then the
        first sample after `start` stands in (count reported as 0).
        """
        if start is None or end is None:
            return None, 0
        inside = [b for t, b in self.series if start <= t <= end]
        if inside:
            return max(inside), len(inside)
        after = [b for t, b in self.series if t >= start]
        return (after[0] if after else None), 0

    def __exit__(self, *_exc: object) -> None:
        # One more stats tick so a short job still gets a sample after it ends.
        time.sleep(2.5)
        if self._proc:
            self._proc.terminate()
            self._proc.wait(timeout=10)
        if self._thread:
            self._thread.join(timeout=5)


def jobs_from_db(job_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not job_ids:
        return {}
    ids = ",".join(f"'{j}'" for j in job_ids if re.fullmatch(r"[0-9a-f-]{36}", j))
    sql = (
        "select row_to_json(t) from (select id, type, module_id, status, error, "
        "created_at, started_at, finished_at, "
        "extract(epoch from finished_at - started_at) as wall_s, "
        "extract(epoch from started_at - created_at) as queue_s "
        f"from jobs where id in ({ids})) t"
    )
    out = subprocess.run(
        ["docker", "exec", DB_CONTAINER, "psql", "-U", "mate", "-d", "mate", "-tAc", sql],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    rows = [json.loads(line) for line in out.splitlines() if line.strip()]
    return {r["id"]: r for r in rows}


def machine_spec() -> dict[str, Any]:
    def sh(cmd: str) -> str:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout.strip()

    return {
        "cpu_model": sh("lscpu | sed -n 's/^Model name:[[:space:]]*//p' | head -1"),
        "cpus": sh("nproc"),
        "cpu_topology": sh(
            "lscpu | awk -F: '/^(Socket\\(s\\)|Core\\(s\\) per socket|Thread\\(s\\) per core)/"
            ' {gsub(/^ +/, "", $2); printf "%s=%s; ", $1, $2}\''
        ),
        "ram": sh("free -h | awk '/^Mem:/ {print $2}'"),
        "kernel": platform.release(),
        "os": sh(". /etc/os-release && echo $PRETTY_NAME"),
        "docker_server": sh("docker version --format '{{.Server.Version}}'"),
        "docker_compose": sh("docker compose version --short"),
        "cgroup": sh("docker info --format '{{.CgroupVersion}}'"),
        "git_commit": sh("git rev-parse --short HEAD"),
    }


class Client:
    def __init__(self) -> None:
        self.http = httpx.Client(
            base_url=API, headers={"Authorization": f"Bearer {TOKEN}"}, timeout=600
        )

    def get(self, path: str) -> Any:
        r = self.http.get(path)
        r.raise_for_status()
        return r.json()

    def post(self, path: str, **kw: Any) -> Any:
        r = self.http.post(path, **kw)
        r.raise_for_status()
        return r.json()

    def wait_job(self, job_id: str, timeout: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            job = self.get(f"/jobs/{job_id}")
            if job["status"] in TERMINAL_JOB:
                return job
            if time.monotonic() > deadline:
                raise TimeoutError(job_id)
            time.sleep(1)

    def runs(self, log_id: str) -> dict[str, dict[str, Any]]:
        return {s["module_id"]: s for s in self.get(f"/event-logs/{log_id}/module-runs")}

    def wait_run(self, log_id: str, module_id: str, timeout: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            state = self.runs(log_id).get(module_id, {"status": "idle"})
            if state["status"] in TERMINAL_RUN:
                return state
            if time.monotonic() > deadline:
                if state.get("job_id"):
                    self.http.post(f"/jobs/{state['job_id']}/cancel")
                return {**state, "status": "timeout"}
            time.sleep(2)


def bench_log(
    c: Client, key: str, path: Path, module_timeout: float, roles: dict[str, str] | None
) -> dict[str, Any]:
    print(f"[{key}] staging {path.name}", flush=True)
    with path.open("rb") as fh:
        staged = c.post("/event-logs/stage", files={"file": (path.name, fh)})
    with MemSampler(API_CONTAINER) as mem:
        time.sleep(1.5)  # let the stats stream start before the job does
        form = {"staging_token": staged["staging_token"], "name": key}
        if roles:
            form["column_roles"] = json.dumps(roles)
        created = c.post("/event-logs", data=form)
        job = c.wait_job(created["job_id"], timeout=4 * 3600)
    log_id = created["log_id"]
    detail = c.get(f"/event-logs/{log_id}")
    print(f"[{key}] import {job['status']}", flush=True)

    result: dict[str, Any] = {
        "key": key,
        "file": path.name,
        "file_bytes": path.stat().st_size,
        "log_id": log_id,
        "import_job_id": created["job_id"],
        "import_status": job["status"],
        "events": detail.get("events_count"),
        "cases": detail.get("cases_count"),
        "variants": detail.get("variants_count"),
        "roles_pinned": roles or {},
        "column_roles": detail.get("column_roles"),
        "mem_samples_captured": len(mem.series),
        "modules": {},
    }
    if job["status"] != "completed":
        result["import_error"] = job.get("error")
        return result

    # Eager precompute (fired by the import itself) first, then every other module
    # that precomputes, one at a time.
    states = c.runs(log_id)
    samplers: dict[str, MemSampler] = {}
    order = sorted(states, key=lambda m: (states[m].get("precompute") != "eager", m))
    for module_id in order:
        before = c.runs(log_id).get(module_id, {})
        t0 = time.monotonic()
        # Sampler first: the stream's first line takes ~0.5-1 s to arrive, and a
        # short job can otherwise finish before it.
        with MemSampler(API_CONTAINER) as mmem:
            time.sleep(1.5)
            if before.get("status") in {"idle", "stale"}:
                c.post(f"/event-logs/{log_id}/module-runs/{module_id}", json={})
            final = c.wait_run(log_id, module_id, module_timeout)
        samplers[module_id] = mmem
        print(
            f"[{key}] {module_id}: {final['status']} ({time.monotonic() - t0:.1f}s polled)",
            flush=True,
        )
        result["modules"][module_id] = {
            "precompute": states[module_id].get("precompute"),
            "status": final["status"],
            "job_id": final.get("job_id"),
            "error": final.get("error"),
        }

    db = jobs_from_db(
        [created["job_id"]] + [m["job_id"] for m in result["modules"].values() if m.get("job_id")]
    )
    imp = db.get(created["job_id"], {})
    result["import_wall_s"] = imp.get("wall_s")
    result["import_queue_s"] = imp.get("queue_s")
    peak, n = mem.peak_between(_epoch(imp.get("started_at")), _epoch(imp.get("finished_at")))
    result["peak_mem_bytes_import"], result["mem_samples_import"] = peak, n
    print(
        f"[{key}] peak api mem during import job {(peak or 0) / 2**20:.0f} MiB ({n} samples)",
        flush=True,
    )
    result["import_started_at"] = imp.get("started_at")
    result["import_finished_at"] = imp.get("finished_at")
    for mid, m in result["modules"].items():
        row = db.get(m.get("job_id") or "", {})
        m["wall_s"] = row.get("wall_s")
        m["queue_s"] = row.get("queue_s")
        m["job_status"] = row.get("status")
        m["started_at"] = row.get("started_at")
        m["finished_at"] = row.get("finished_at")
        sampler = samplers.get(mid)
        if sampler is not None:
            m["peak_mem_bytes"], m["mem_samples"] = sampler.peak_between(
                _epoch(row.get("started_at")), _epoch(row.get("finished_at"))
            )
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--log", action="append", required=True, help="key=path (repeatable)")
    ap.add_argument(
        "--roles",
        action="append",
        default=[],
        help='key={"role": "column"} pins column roles for that log (repeatable)',
    )
    ap.add_argument("-o", "--output", type=Path, required=True)
    ap.add_argument(
        "--module-timeout",
        type=float,
        default=3600,
        help="seconds before a module run is cancelled and recorded as timeout",
    )
    args = ap.parse_args()

    pinned = {k: json.loads(v) for k, _, v in (r.partition("=") for r in args.roles)}
    c = Client()
    # A fresh account owns no modules until onboarding seeds the bundled defaults;
    # this is the same idempotent call the UI's "Restore defaults" makes.
    restored = c.post("/modules/restore-defaults")["restored"]
    print(f"restored default modules: {restored}", flush=True)
    with MemSampler(API_CONTAINER) as idle:
        time.sleep(10)
    idle_bytes = sorted(b for _, b in idle.series)
    idle_median = idle_bytes[len(idle_bytes) // 2] if idle_bytes else None
    out: dict[str, Any] = {
        "started_at": _now(),
        "machine": machine_spec(),
        "modules_restored": restored,
        "idle_mem_bytes": idle_median,
        "logs": [],
    }
    for spec in args.log:
        key, _, p = spec.partition("=")
        out["logs"].append(bench_log(c, key, Path(p), args.module_timeout, pinned.get(key)))
        args.output.write_text(json.dumps(out, indent=2, default=str))
    out["finished_at"] = _now()
    args.output.write_text(json.dumps(out, indent=2, default=str))
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
