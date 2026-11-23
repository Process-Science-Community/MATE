#!/usr/bin/env python3
"""Database side of ``reset-reviewer-accounts.sh`` (stdlib only, runs on the VM host).

Talks to the app database only through ``psql`` (default: inside the
``mate-app-db`` container, override with ``REVIEWER_RESET_PSQL``). Every query is
restricted to the account ids listed in the sample-content manifest, and the ids
are checked against a strict UUID pattern before they reach SQL.

Commands (the manifest path is the host path of SAMPLE_CONTENT_MANIFEST):

    report  <manifest>   what differs from the freshly seeded state (no writes)
    reset   <manifest>   delete everything the seeder does not create (one transaction)
    wait    <manifest>   wait until seeding and the warm-up precompute have finished
    verify  <manifest>   check every account against the seeded state; exit 1 on a failure
    ids     <manifest>   print "<username> <id>" per account

Never prints a setting's value except the non-secret onboarding flags: ``ai.config``
holds API keys.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
DEFAULT_PSQL = "docker exec -i mate-app-db psql -U mate -d mate"

SEEDED_KEY = "sample_content.seeded"
# Written by the seeder (the first three) or by the first request of a reviewer
# (auth.sandboxed). analytics.exempt and auth.sandboxed are kept on reset: they
# are safety flags, and the seeder/login would write them again anyway.
SEED_SETTING_KEYS = {"analytics.exempt", "modules_defaults_seeded", SEEDED_KEY, "auth.sandboxed"}
KEEP_SETTING_KEYS = ("analytics.exempt", "auth.sandboxed")

ANALYTICS_TABLES = (
    "analytics_event_objects",
    "analytics_events",
    "analytics_sessions",
    "analytics_objects",
    "analytics_object_relations",
)
# Rows the seeded state has none of (beyond analytics).
EMPTY_TABLES = (
    "process_folders",
    "watched_folders",
    "module_configs",
    "module_layouts",
    "event_edits",
    "api_tokens",
)
# Delete order for reset. All are keyed by user_id; FK children first.
RESET_DELETES: tuple[tuple[str, str], ...] = (
    ("analytics_event_objects", "user_id"),
    ("analytics_events", "user_id"),
    ("analytics_sessions", "user_id"),
    ("analytics_objects", "user_id"),
    ("analytics_object_relations", "user_id"),
    ("event_edits", "user_id"),
    ("module_layouts", "user_id"),
    ("log_module_runs", "user_id"),
    # Shares the reviewer created. Shares *to* a reviewer belong to someone
    # else and are only reported.
    ("dashboard_shares", "created_by"),
    ("dashboards", "user_id"),
    ("watched_folders", "user_id"),
    ("jobs", "user_id"),
    # Hard delete, soft-deleted rows included: the seeder reuses a same-named log.
    ("process_logs", "user_id"),
    ("process_folders", "user_id"),
    ("module_configs", "user_id"),
    # With modules_defaults_seeded gone too, the seeder grants every default again
    # (a module the reviewer uninstalled would otherwise never come back).
    ("module_installs", "user_id"),
    ("api_tokens", "user_id"),
)

ACTIVE = ("queued", "running")


# ── plumbing ────────────────────────────────────────────────────────────────


def psql_cmd() -> list[str]:
    return [
        *shlex.split(os.environ.get("REVIEWER_RESET_PSQL", DEFAULT_PSQL)),
        "-X",
        "-q",
        "-A",
        "-t",
        "-v",
        "ON_ERROR_STOP=1",
    ]


def run_sql(sql: str) -> str:
    proc = subprocess.run(psql_cmd(), input=sql, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        raise SystemExit(f"✗ psql failed (exit {proc.returncode})")
    return proc.stdout


def q(sql: str) -> list[dict[str, Any]]:
    """Rows of ``sql`` as dicts (one JSON document per call)."""
    out = run_sql(f"SELECT coalesce(json_agg(t), '[]'::json) FROM ({sql}) t;").strip()
    return json.loads(out or "[]")


def lit(value: str) -> str:
    """A string literal for an id that already passed UUID_RE / a plain key."""
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", value):
        raise SystemExit(f"✗ refusing to put {value!r} into SQL")
    return "'" + value + "'"


def load_manifest(path: Path) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    users = manifest.get("users") or []
    if not users:
        raise SystemExit(f"✗ {path} lists no users")
    for user in users:
        if not UUID_RE.fullmatch(str(user.get("id", ""))):
            raise SystemExit(f"✗ {path}: user id {user.get('id')!r} is not a Keycloak UUID")
    return manifest


def users_of(manifest: dict[str, Any]) -> list[tuple[str, str]]:
    return [(str(u.get("username") or u["id"]), str(u["id"])) for u in manifest["users"]]


def id_list(manifest: dict[str, Any]) -> str:
    return ", ".join(lit(uid) for _, uid in users_of(manifest))


def expected_log_names(manifest: dict[str, Any]) -> list[str]:
    return [str(entry["name"]) for entry in manifest.get("logs") or []]


def warm_list(manifest: dict[str, Any]) -> list[str]:
    return [str(m) for m in manifest.get("warm_modules") or []]


def required_modules() -> list[str]:
    raw = os.environ.get("REQUIRED_MODULES", "cv4cdd performance_over_time")
    return [m for m in raw.replace(",", " ").split() if m]


def counts(table: str, column: str, ids: str) -> dict[str, int]:
    rows = q(
        f"SELECT {column} AS uid, count(*) AS n FROM {table} WHERE {column} IN ({ids}) GROUP BY 1"
    )
    return {r["uid"]: int(r["n"]) for r in rows}


class Snapshot:
    """Everything the report and the verify step look at, for every account at once."""

    def __init__(self, manifest: dict[str, Any]) -> None:
        ids = id_list(manifest)
        self.users = {
            r["id"]: r
            for r in q(
                f"SELECT id, preferred_username, last_seen_at FROM users WHERE id IN ({ids})"
            )
        }
        self.settings = q(
            f"SELECT user_id, key, updated_at, CASE WHEN key IN ('onboarding', {lit(SEEDED_KEY)}, "
            f"'modules_defaults_seeded', 'analytics.exempt', 'auth.sandboxed') THEN value_json "
            f"ELSE NULL END AS value FROM user_settings WHERE user_id IN ({ids}) ORDER BY key"
        )
        self.logs = q(
            "SELECT id, user_id, name, status, deleted_at, created_at, data_revision, folder_id, "
            "dataset_revision_id "
            f"FROM process_logs WHERE user_id IN ({ids}) ORDER BY created_at"
        )
        self.dashboards = q(
            "SELECT id, user_id, name, event_log_id, created_at, updated_at, "
            "extract(epoch FROM updated_at - created_at) AS edited_after_s "
            f"FROM dashboards WHERE user_id IN ({ids}) ORDER BY created_at"
        )
        self.jobs = q(
            "SELECT user_id, type, module_id, status, count(*) AS n, min(created_at) AS first, "
            "max(created_at) AS last "
            f"FROM jobs WHERE user_id IN ({ids}) GROUP BY 1, 2, 3, 4 ORDER BY 1, 2, 3, 4"
        )
        self.failed_jobs = q(
            "SELECT user_id, id, type, module_id, payload_json->>'log_id' AS log_id, "
            "left(coalesce(error, ''), 160) AS error, created_at "
            f"FROM jobs WHERE user_id IN ({ids}) AND status IN ('failed', 'cancelled') "
            "ORDER BY created_at"
        )
        self.runs = q(
            "SELECT r.user_id, r.log_id, r.module_id, r.status, r.job_id, "
            "left(coalesce(r.error, ''), 160) AS error, "
            "(r.data_revision <> l.data_revision) AS stale, l.name AS log_name, "
            "(l.deleted_at IS NOT NULL) AS log_deleted, r.module_version "
            "FROM log_module_runs r JOIN process_logs l ON l.id = r.log_id "
            f"WHERE r.user_id IN ({ids}) ORDER BY l.name, r.module_id"
        )
        self.installs = q(
            f"SELECT user_id, module_id, source FROM module_installs WHERE user_id IN ({ids}) "
            "ORDER BY module_id"
        )
        self.configs = q(
            f"SELECT user_id, module_id, enabled, updated_at FROM module_configs WHERE user_id IN ({ids})"
        )
        self.analytics = {t: counts(t, "user_id", ids) for t in ANALYTICS_TABLES}
        self.empty = {t: counts(t, "user_id", ids) for t in EMPTY_TABLES}
        self.shares_out = counts("dashboard_shares", "created_by", ids)
        self.shares_in = q(
            "SELECT target_user_id AS uid, count(*) AS n FROM dashboard_shares "
            f"WHERE target_user_id IN ({ids}) AND created_by NOT IN ({ids}) GROUP BY 1"
        )
        self.teams = counts("team_members", "user_id", ids)
        self.hub = q(
            "SELECT created_by AS uid, count(*) AS n FROM hub_datasets "
            f"WHERE created_by IN ({ids}) GROUP BY 1"
        )

        # Stale like processing._derive: data moved on, or the deployed module
        # version differs from the one the result was computed with.
        versions = json.loads(os.environ.get("MODULE_VERSIONS") or "{}")
        for row in self.runs:
            want = versions.get(row["module_id"])
            if want and row["module_version"] and row["module_version"] != want:
                row["stale"] = True

    def of(
        self, rows: list[dict[str, Any]], uid: str, key: str = "user_id"
    ) -> list[dict[str, Any]]:
        return [r for r in rows if r.get(key) == uid]

    def setting(self, uid: str, key: str) -> dict[str, Any] | None:
        return next((s for s in self.of(self.settings, uid) if s["key"] == key), None)


def seeded_state(snap: Snapshot, uid: str) -> tuple[str | None, list[str]]:
    row = snap.setting(uid, SEEDED_KEY)
    value = (row or {}).get("value") or {}
    return value.get("at"), [str(x) for x in value.get("logs") or []]


# ── report (dry run) ────────────────────────────────────────────────────────


def cmd_report(manifest: dict[str, Any]) -> int:
    snap = Snapshot(manifest)
    names = expected_log_names(manifest)
    board = manifest.get("dashboard") or {}
    for username, uid in users_of(manifest):
        print(f"\n━━ {username}  ({uid})")
        user = snap.users.get(uid)
        if user is None:
            print("  users row: absent (never seeded, never logged in); reset just seeds it")
        else:
            print(f"  users row: last_seen_at={user['last_seen_at']} (kept)")
        seeded_at, seeded_logs = seeded_state(snap, uid)
        print(f"  seeded:    {seeded_at or 'NO (sample_content.seeded missing)'}")

        print("  logs:")
        live_names = []
        for row in snap.of(snap.logs, uid):
            tags = []
            if row["id"] not in seeded_logs:
                tags.append("EXTRA (not seeded)")
            if row["deleted_at"]:
                tags.append(f"DELETED {row['deleted_at']}")
            else:
                live_names.append(row["name"])
            if row["status"] != "ready":
                tags.append(f"status={row['status']}")
            if int(row["data_revision"] or 0) > 1:
                tags.append(f"data changed (revision {row['data_revision']}: filter/edit/reimport)")
            if row["folder_id"]:
                tags.append("moved into a folder")
            print(
                f"    {'!' if tags else '='} {row['name']!r} {row['id']} {'; '.join(tags) or 'seeded, unchanged'}"
            )
        for name in names:
            if name not in live_names:
                print(f"    ! MISSING seeded log {name!r}")
        if not snap.of(snap.logs, uid):
            print("    (none)")

        print("  dashboards:")
        boards = snap.of(snap.dashboards, uid)
        for row in boards:
            tags = []
            if row["name"] != board.get("name"):
                tags.append("EXTRA (not seeded)")
            if float(row["edited_after_s"] or 0) > 5:
                tags.append(f"CHANGED (updated {row['updated_at']})")
            if row["event_log_id"] and seeded_logs and board and row["name"] == board.get("name"):
                idx = int(board.get("log", 0))
                if idx < len(seeded_logs) and row["event_log_id"] != seeded_logs[idx]:
                    tags.append("bound to a different log")
            print(
                f"    {'!' if tags else '='} {row['name']!r} {row['id']} {'; '.join(tags) or 'seeded, unchanged'}"
            )
        if board and not any(r["name"] == board.get("name") for r in boards):
            print(f"    ! MISSING {board.get('name')!r}")

        print("  user_settings:")
        for row in snap.of(snap.settings, uid):
            key = row["key"]
            if key in SEED_SETTING_KEYS:
                note = "seed/login flag" + (
                    " (kept)" if key in KEEP_SETTING_KEYS else " (rewritten by the seeder)"
                )
            elif key == "onboarding":
                v = row["value"] or {}
                note = (
                    f"TESTING: completed={v.get('completed')} tour_completed={v.get('tour_completed')} "
                    "-> deleted, wizard + tour show again"
                )
            else:
                note = "TESTING -> deleted (value not shown)"
            print(
                f"    {'=' if key in SEED_SETTING_KEYS else '!'} {key:<26} {row['updated_at']}  {note}"
            )

        print("  jobs (type / module / status: count, newest):")
        for row in snap.of(snap.jobs, uid):
            # Both ISO 8601 ("YYYY-MM-DDTHH:MM:SS…", UTC): "+" = created after seeding.
            after = seeded_at and str(row["last"])[:19] > seeded_at[:19]
            mark = "!" if row["status"] in ("failed", "cancelled") else ("+" if after else "=")
            print(
                f"    {mark} {row['type']} / {row['module_id'] or '-'} / {row['status']}: {row['n']}  last {row['last']}"
            )
        for row in snap.of(snap.failed_jobs, uid):
            print(
                f"      ✗ {row['type']} {row['module_id'] or ''} log={row['log_id']}: {row['error']}"
            )

        print("  module results (log_module_runs):")
        by_module: dict[str, list[str]] = {}
        for row in snap.of(snap.runs, uid):
            state = row["status"] + ("/stale" if row["stale"] else "")
            state += "/deleted-log" if row["log_deleted"] else ""
            by_module.setdefault(row["module_id"], []).append(state)
        for module_id in sorted(by_module):
            states = by_module[module_id]
            bad = any(s != "ready" for s in states)
            print(f"    {'!' if bad else '='} {module_id:<26} {', '.join(sorted(states))}")
        for module_id in required_modules():
            if module_id not in by_module:
                print(f"    ! {module_id:<26} never ran (not in the seeder's warm-up)")

        installed = sorted(r["module_id"] for r in snap.of(snap.installs, uid))
        defaults_row = snap.setting(uid, "modules_defaults_seeded")
        offered = sorted((defaults_row or {}).get("value") or [])
        missing = sorted(set(offered) - set(installed))
        extra = sorted(set(installed) - set(offered))
        print(
            f"  modules:   {len(installed)} installed"
            + (f"; UNINSTALLED {missing}" if missing else "")
            + (f"; added {extra}" if extra else "")
        )
        not_warm = sorted(set(installed) - set(warm_list(manifest)))
        print(f"             not in warm_modules (no precompute or computed on open): {not_warm}")
        cfgs = snap.of(snap.configs, uid)
        if cfgs:
            print(f"  ! module settings changed: {sorted(c['module_id'] for c in cfgs)}")

        analytics = {t: snap.analytics[t].get(uid, 0) for t in ANALYTICS_TABLES}
        total = sum(analytics.values())
        print(f"  analytics: {'0 events' if total == 0 else '! ' + json.dumps(analytics)}")
        other = {t: snap.empty[t].get(uid, 0) for t in EMPTY_TABLES}
        other["dashboard_shares (own)"] = snap.shares_out.get(uid, 0)
        nonzero = {k: v for k, v in other.items() if v}
        print(f"  other:     {'none' if not nonzero else '! ' + json.dumps(nonzero)}")
        foreign = [r for r in snap.shares_in if r["uid"] == uid]
        if foreign or snap.teams.get(uid) or [r for r in snap.hub if r["uid"] == uid]:
            print(
                "  ! NOT TOUCHED (belongs to others / admin): "
                f"shares to it={sum(r['n'] for r in foreign)}, team memberships={snap.teams.get(uid, 0)}, "
                f"hub datasets={sum(r['n'] for r in snap.hub if r['uid'] == uid)}"
            )
    return 0


# ── reset ───────────────────────────────────────────────────────────────────


def cmd_reset(manifest: dict[str, Any]) -> int:
    ids = id_list(manifest)
    keep = ", ".join(lit(k) for k in KEEP_SETTING_KEYS)
    parts = ["BEGIN;"]
    for table, column in RESET_DELETES:
        parts.append(
            f"WITH d AS (DELETE FROM {table} WHERE {column} IN ({ids}) RETURNING {column} AS uid) "
            f"SELECT '{table}', uid, count(*) FROM d GROUP BY uid;"
        )
    parts.append(
        f"WITH d AS (DELETE FROM user_settings WHERE user_id IN ({ids}) AND key NOT IN ({keep}) "
        "RETURNING user_id AS uid, key) SELECT 'user_settings', uid, string_agg(key, ',' ORDER BY key) "
        "FROM d GROUP BY uid;"
    )
    parts.append("COMMIT;")
    out = run_sql("\n".join(parts))
    names = {uid: name for name, uid in users_of(manifest)}
    deleted: dict[str, list[str]] = {}
    for line in out.splitlines():
        if not line.strip():
            continue
        table, uid, n = line.split("|", 2)
        deleted.setdefault(uid, []).append(f"{table}={n}")
    for _, uid in users_of(manifest):
        print(f"  {names[uid]}: deleted {', '.join(deleted.get(uid, [])) or 'nothing'}")
    return 0


# ── wait ────────────────────────────────────────────────────────────────────


def cmd_wait(manifest: dict[str, Any]) -> int:
    ids = id_list(manifest)
    timeout = float(os.environ.get("WAIT_TIMEOUT_SECONDS", str(3 * 3600)))
    poll = float(os.environ.get("WAIT_POLL_SECONDS", "15"))
    quiet_needed = int(os.environ.get("WAIT_QUIET_POLLS", "8"))
    n_users = len(users_of(manifest))
    deadline = time.monotonic() + timeout
    quiet = 0
    last = ""
    while True:
        seeded = q(
            f"SELECT count(*) AS n FROM user_settings WHERE user_id IN ({ids}) AND key = {lit(SEEDED_KEY)}"
        )[0]["n"]
        active_jobs = q(
            f"SELECT count(*) AS n FROM jobs WHERE user_id IN ({ids}) AND status IN ('queued', 'running')"
        )[0]["n"]
        active_runs = q(
            f"SELECT count(*) AS n FROM log_module_runs WHERE user_id IN ({ids}) AND status IN ('queued', 'running')"
        )[0]["n"]
        done_runs = q(
            f"SELECT count(*) AS n FROM log_module_runs WHERE user_id IN ({ids}) AND status NOT IN ('queued', 'running')"
        )[0]["n"]
        status = f"seeded {seeded}/{n_users}, jobs active {active_jobs}, module runs active {active_runs} / finished {done_runs}"
        if status != last:
            print(f"  {time.strftime('%H:%M:%S')} {status}", flush=True)
            last = status
        # The warm-up is queued only after every account is seeded, so "nothing
        # active" must hold for a while (WAIT_QUIET_POLLS x WAIT_POLL_SECONDS).
        if seeded == n_users and active_jobs == 0 and active_runs == 0 and done_runs > 0:
            quiet += 1
            if quiet >= quiet_needed:
                print("✓ Seeding and precompute finished.")
                return 0
        else:
            quiet = 0
        abort_cmd = os.environ.get("WAIT_ABORT_CMD")
        if abort_cmd and seeded < n_users:
            hit = subprocess.run(abort_cmd, shell=True, capture_output=True, text=True, check=False)
            if hit.returncode == 0 and hit.stdout.strip():
                print("✗ the seeder failed for an account:", file=sys.stderr)
                print(hit.stdout.strip()[-2000:], file=sys.stderr)
                return 1
        if time.monotonic() > deadline:
            print(f"✗ timed out after {timeout:.0f}s: {status}", file=sys.stderr)
            return 1
        time.sleep(poll)


# ── verify ──────────────────────────────────────────────────────────────────


def cmd_verify(manifest: dict[str, Any]) -> int:
    snap = Snapshot(manifest)
    names = expected_log_names(manifest)
    board = manifest.get("dashboard") or {}
    warm = set(warm_list(manifest))
    failures = 0
    for username, uid in users_of(manifest):
        problems: list[str] = []
        notes: list[str] = []
        logs = snap.of(snap.logs, uid)
        live = [r for r in logs if not r["deleted_at"]]
        if sorted(r["name"] for r in live) != sorted(names) or len(logs) != len(live):
            problems.append(f"logs {sorted(r['name'] for r in logs)} != manifest {sorted(names)}")
        if any(r["status"] != "ready" for r in live):
            problems.append("a log is not ready")
        if any(int(r["data_revision"] or 0) != 1 for r in live):
            problems.append("a log's data_revision is not 1 (changed after import)")
        by_name = {r["name"]: r["id"] for r in live}
        seeded_at, seeded_logs = seeded_state(snap, uid)
        if seeded_at is None:
            problems.append("not seeded (sample_content.seeded missing)")
        elif sorted(seeded_logs) != sorted(by_name.values()):
            problems.append("sample_content.seeded lists other log ids")

        boards = snap.of(snap.dashboards, uid)
        if board:
            want_log = by_name.get(names[int(board.get("log", 0))]) if names else None
            if len(boards) != 1 or boards[0]["name"] != board.get("name"):
                problems.append(
                    f"dashboards {[b['name'] for b in boards]} != [{board.get('name')!r}]"
                )
            elif boards[0]["event_log_id"] != want_log:
                problems.append("dashboard is bound to the wrong log")
        elif boards:
            problems.append("unexpected dashboards")

        keys = sorted(s["key"] for s in snap.of(snap.settings, uid))
        stray = [k for k in keys if k not in SEED_SETTING_KEYS]
        if stray:
            problems.append(f"user_settings has {stray} (onboarding/tour would not show)")
        if "analytics.exempt" not in keys:
            problems.append("analytics.exempt flag missing")

        jobs = snap.of(snap.jobs, uid)
        bad_jobs = sum(int(j["n"]) for j in jobs if j["status"] in ("failed", "cancelled"))
        active_jobs = sum(int(j["n"]) for j in jobs if j["status"] in ACTIVE)
        if bad_jobs:
            problems.append(f"{bad_jobs} failed/cancelled jobs")
            for row in snap.of(snap.failed_jobs, uid):
                notes.append(f"✗ job {row['type']} {row['module_id'] or ''}: {row['error']}")
        if active_jobs:
            problems.append(f"{active_jobs} jobs still queued/running")

        runs = snap.of(snap.runs, uid)
        ready: dict[str, int] = {}
        for row in runs:
            if row["status"] == "ready" and not row["stale"]:
                ready[row["module_id"]] = ready.get(row["module_id"], 0) + 1
            else:
                problems.append(
                    f"{row['module_id']} on {row['log_name']!r}: {row['status']}{' (stale)' if row['stale'] else ''} {row['error']}".rstrip()
                )
        installed = {r["module_id"] for r in snap.of(snap.installs, uid)}
        for module_id in required_modules():
            if ready.get(module_id, 0) != len(names):
                problems.append(
                    f"{module_id}: ready on {ready.get(module_id, 0)}/{len(names)} logs"
                )
        for module_id in sorted((warm & installed) - set(required_modules())):
            if ready.get(module_id, 0) != len(names):
                notes.append(
                    f"{module_id}: ready on {ready.get(module_id, 0)}/{len(names)} logs "
                    "(no precompute, or unavailable for these logs)"
                )
        defaults_row = snap.setting(uid, "modules_defaults_seeded")
        offered = set((defaults_row or {}).get("value") or [])
        if offered - installed:
            problems.append(f"default modules not installed: {sorted(offered - installed)}")

        analytics = {t: snap.analytics[t].get(uid, 0) for t in ANALYTICS_TABLES}
        if any(analytics.values()):
            problems.append(f"analytics rows: {analytics}")
        other = {t: snap.empty[t].get(uid, 0) for t in EMPTY_TABLES}
        other["dashboard_shares"] = snap.shares_out.get(uid, 0)
        if any(other.values()):
            problems.append(f"non-seeded rows: { {k: v for k, v in other.items() if v} }")

        modules_ok = ", ".join(f"{m} {ready.get(m, 0)}/{len(names)}" for m in sorted(ready))
        print(f"\n{'✓' if not problems else '✗'} {username} ({uid})")
        print(f"  logs: {len(live)} ({', '.join(sorted(by_name))})")
        print(f"  dashboard: {', '.join(b['name'] for b in boards) or '-'}")
        print(f"  user_settings: {', '.join(keys)}  (no onboarding row -> wizard + tour show)")
        print(
            f"  jobs: {sum(int(j['n']) for j in jobs)} total, {bad_jobs} failed/cancelled, {active_jobs} active"
        )
        print(f"  module results ready: {modules_ok or '-'}")
        print(f"  analytics/interaction rows: {sum(analytics.values())}")
        for note in notes:
            print(f"  · {note}")
        for problem in problems:
            print(f"  ✗ {problem}")
        failures += bool(problems)
    print()
    if failures:
        print(f"✗ {failures} account(s) differ from the seeded state.")
        return 1
    print("✓ All accounts match the seeded state.")
    return 0


def cmd_ids(manifest: dict[str, Any]) -> int:
    for username, uid in users_of(manifest):
        print(f"{username} {uid}")
    return 0


def cmd_logids(manifest: dict[str, Any]) -> int:
    """``<user id> <log id>`` per live log (the on-disk check compares against it)."""
    for row in q(
        f"SELECT user_id, id FROM process_logs WHERE user_id IN ({id_list(manifest)}) "
        "AND deleted_at IS NULL ORDER BY 1, 2"
    ):
        print(f"{row['user_id']} {row['id']}")
    return 0


def cmd_busy(manifest: dict[str, Any]) -> int:
    """Queued/running jobs of OTHER accounts: an API restart fails their running ones."""
    rows = q(
        "SELECT status, count(*) AS n FROM jobs WHERE status IN ('queued', 'running') "
        f"AND user_id NOT IN ({id_list(manifest)}) GROUP BY 1"
    )
    print(" ".join(f"{r['status']}={r['n']}" for r in rows) or "none")
    return 0


COMMANDS = {
    "report": cmd_report,
    "reset": cmd_reset,
    "wait": cmd_wait,
    "verify": cmd_verify,
    "ids": cmd_ids,
    "logids": cmd_logids,
    "busy": cmd_busy,
}


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] not in COMMANDS:
        print(__doc__, file=sys.stderr)
        return 2
    return COMMANDS[argv[1]](load_manifest(Path(argv[2])))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
