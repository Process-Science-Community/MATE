"""Metadata-database → S3 snapshot for durability in S3 mode (S3_OFFLOAD.md).

In S3 mode user data (parquet, caches) lives in the bucket, but the SQLite
metadata DB - the authoritative index mapping every object key to a user/log -
is VM-local. Lose the VM and the bucket is orphaned. This ships a consistent
online snapshot of ``metadata.db`` to ``{prefix}/_system/metadata.db``
periodically and on shutdown, so S3 holds a complete restorable picture.

**Backup** depends on the engine. On SQLite it uses the online-backup API
(``Connection.backup``), safe against the live WAL database. On PostgreSQL it
shells out to ``pg_dump --format=custom``, which likewise runs against the live
server in a single transaction snapshot; the key is ``_system/metadata.dump``
so the two formats can never be mistaken for one another.

``pg_dump`` must be on PATH and at least the server's major version - the tool
refuses to dump a newer server. The api image installs ``postgresql-client-16``
from PGDG for exactly this.

**Restore is operator-invoked, NOT automatic.** ``alembic upgrade head`` runs in
the entrypoint before the app starts, so by the time the lifespan could restore,
a fresh (empty) schema DB already exists. Recovery on a new VM is a pre-boot
step, before alembic::

    STORAGE_MODE=s3 STORAGE_S3_BUCKET=... STORAGE_S3_ENDPOINT=... \\
      python -m mate.api.storage.db_backup restore

It restores only into an empty database - an absent file on SQLite, a schema
with no tables on PostgreSQL - so it never clobbers a populated one; the
subsequent ``alembic upgrade head`` then migrates the restored DB to head. The storage backend is configured purely via ``STORAGE_*`` env vars
(``storage.config``), so the CLI sees exactly the same bucket as the app - no
DB needed to bootstrap.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path

import structlog
from sqlalchemy.engine import make_url

from mate.api.config import get_settings
from mate.api.storage import s3
from mate.api.storage.config import get_storage_settings, is_s3

log = structlog.get_logger(__name__)


# pg_dump/pg_restore ceiling. The metadata DB is ~51 MB in production, so this
# is a runaway guard rather than a real bound.
_PG_TOOL_TIMEOUT_SECONDS = 900


def _url():  # type: ignore[no-untyped-def]
    return make_url(get_settings().database_url)


def is_postgres() -> bool:
    return _url().get_backend_name() == "postgresql"


def _pg_command(tool: str, *extra: str) -> tuple[list[str], dict[str, str]]:
    """Build a libpq CLI invocation plus its environment.

    The password goes in the environment, never in argv - anything on the
    command line is visible to every process on the box via `ps`.
    """
    url = _url()
    cmd = [tool]
    if url.host:
        cmd += ["--host", url.host]
    if url.port:
        cmd += ["--port", str(url.port)]
    if url.username:
        cmd += ["--username", url.username]
    cmd += ["--dbname", url.database or ""]
    cmd += list(extra)
    env = os.environ.copy()
    if url.password:
        env["PGPASSWORD"] = str(url.password)
    return cmd, env


def _run_pg_tool(tool: str, *extra: str) -> None:
    """Run a libpq CLI tool, raising with its stderr on failure."""
    if shutil.which(tool) is None:
        raise RuntimeError(
            f"{tool} not found on PATH - the image needs postgresql-client "
            "at or above the server's major version"
        )
    cmd, env = _pg_command(tool, *extra)
    proc = subprocess.run(
        cmd, env=env, capture_output=True, text=True, timeout=_PG_TOOL_TIMEOUT_SECONDS, check=False
    )
    if proc.returncode != 0:
        raise RuntimeError(f"{tool} exited {proc.returncode}: {proc.stderr.strip()[-400:]}")


def _tool_major(tool: str) -> int | None:
    """Major version of a libpq CLI tool, or None if it cannot be determined."""
    try:
        out = subprocess.run(
            [tool, "--version"], capture_output=True, text=True, timeout=10, check=False
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"(\d+)", out)
    return int(m.group(1)) if m else None


def _server_major() -> int | None:
    from sqlalchemy import create_engine, text

    sync_url = get_settings().database_url.replace("+asyncpg", "+psycopg")
    engine = create_engine(sync_url, future=True, connect_args={"connect_timeout": 5})
    try:
        with engine.connect() as conn:
            num = int(conn.execute(text("SHOW server_version_num")).scalar_one())
        return num // 10000
    except Exception:
        return None
    finally:
        engine.dispose()


def _assert_tool_matches_server(tool: str) -> None:
    """Refuse to run when the client major version differs from the server's.

    An older client is rejected by pg_dump itself. A *newer* client is the
    dangerous case: it happily dumps an older server, but emits directives that
    server cannot execute - pg_dump 18 writes `SET transaction_timeout`, which
    PostgreSQL 16 rejects - so the upload succeeds and the restore fails on the
    day it is needed. Refusing loudly beats a backup that cannot be restored.
    """
    tool_major, server_major = _tool_major(tool), _server_major()
    if tool_major is None or server_major is None:
        return  # cannot compare; the tool's own checks still apply
    if tool_major != server_major:
        raise RuntimeError(
            f"{tool} is version {tool_major} but the server is {server_major}; "
            f"install postgresql-client-{server_major}. A dump taken by a newer "
            "client cannot be restored into an older server."
        )


def dump_postgres(dest: Path) -> None:
    """Write a custom-format pg_dump of the metadata database to ``dest``."""
    _assert_tool_matches_server("pg_dump")
    _run_pg_tool(
        "pg_dump", "--format=custom", "--no-owner", "--no-privileges", "--file", str(dest)
    )


def _postgres_is_empty() -> bool:
    """True when the target database has no tables in the public schema."""
    from sqlalchemy import create_engine, text

    sync_url = get_settings().database_url.replace("+asyncpg", "+psycopg")
    engine = create_engine(sync_url, future=True, connect_args={"connect_timeout": 5})
    try:
        with engine.connect() as conn:
            count = conn.execute(
                text("SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")
            ).scalar_one()
        return int(count) == 0
    finally:
        engine.dispose()


def _db_path() -> Path | None:
    """Filesystem path of the SQLite metadata DB, or None for a non-SQLite URL."""
    database = make_url(get_settings().database_url).database
    return Path(database) if database else None


def _backup_key() -> str:
    name = "metadata.dump" if is_postgres() else "metadata.db"
    prefix = get_storage_settings().prefix.strip("/")
    return f"{prefix}/_system/{name}" if prefix else f"_system/{name}"


def backup_sync() -> bool:
    """Snapshot metadata.db to S3. Returns True on a successful upload.

    No-op (False) in local mode or when the DB file is missing. Best-effort:
    any failure is logged, never raised - a backup hiccup must not break a
    request or shutdown.
    """
    if not is_s3():
        return False
    if is_postgres():
        work = Path(tempfile.mkdtemp(prefix="ff-dbbak-"))
        dump = work / "metadata.dump"
        try:
            dump_postgres(dump)
            s3.upload_object(dump, _backup_key())
            log.info(
                "storage.db_backup.uploaded", key=_backup_key(), bytes=dump.stat().st_size
            )
            return True
        except Exception:
            log.warning("storage.db_backup.failed", exc_info=True)
            return False
        finally:
            shutil.rmtree(work, ignore_errors=True)
    path = _db_path()
    if path is None or not path.exists():
        return False
    work = Path(tempfile.mkdtemp(prefix="ff-dbbak-"))
    snapshot = work / "metadata.db"
    try:
        src = sqlite3.connect(str(path))
        try:
            dst = sqlite3.connect(str(snapshot))
            try:
                src.backup(dst)  # online backup - consistent even under WAL writes
            finally:
                dst.close()
        finally:
            src.close()
        s3.upload_object(snapshot, _backup_key())
        log.info("storage.db_backup.uploaded", key=_backup_key(), bytes=snapshot.stat().st_size)
        return True
    except Exception:
        log.warning("storage.db_backup.failed", exc_info=True)
        return False
    finally:
        shutil.rmtree(work, ignore_errors=True)


def restore_sync() -> bool:
    """Download the S3 snapshot to the local DB path IFF the local DB is absent.

    Returns True when a snapshot was restored. Never overwrites a populated DB
    (size > 0) - so it's safe to run unconditionally before boot; on an existing
    VM it no-ops. Reads the bucket from the ``STORAGE_*`` env vars (the DB
    itself may not exist yet).
    """
    if not is_s3():
        log.info("storage.db_restore.not_s3")
        return False
    if is_postgres():
        return _restore_postgres()
    path = _db_path()
    if path is None:
        return False
    if path.exists() and path.stat().st_size > 0:
        log.info("storage.db_restore.skip_local_present", path=str(path))
        return False
    key = _backup_key()
    try:
        if not s3.object_exists(key):
            log.info("storage.db_restore.no_snapshot", key=key)
            return False
        s3.download_object(key, path)
        log.info("storage.db_restore.restored", key=key, path=str(path))
        return True
    except s3.StorageError:
        log.warning("storage.db_restore.failed", key=key, exc_info=True)
        return False


def _restore_postgres() -> bool:
    """Restore the pg_dump snapshot, but only into an empty database.

    Mirrors the SQLite rule: never clobber populated data. `pg_restore` is
    given the dump's own schema, so this runs *before* alembic - the subsequent
    `alembic upgrade head` then brings the restored schema to head.
    """
    key = _backup_key()
    try:
        if not _postgres_is_empty():
            log.info("storage.db_restore.skip_db_populated")
            return False
    except Exception:
        log.warning("storage.db_restore.target_unreachable", exc_info=True)
        return False
    work = Path(tempfile.mkdtemp(prefix="ff-dbrestore-"))
    dump = work / "metadata.dump"
    try:
        if not s3.object_exists(key):
            log.info("storage.db_restore.no_snapshot", key=key)
            return False
        s3.download_object(key, dump)
        _run_pg_tool(
            "pg_restore", "--no-owner", "--no-privileges", "--single-transaction", str(dump)
        )
        log.info("storage.db_restore.restored", key=key, bytes=dump.stat().st_size)
        return True
    except Exception:
        log.warning("storage.db_restore.failed", key=key, exc_info=True)
        return False
    finally:
        shutil.rmtree(work, ignore_errors=True)


async def backup() -> None:
    await asyncio.to_thread(backup_sync)


# How often the periodic backup loop wakes (seconds). Snapshots are cheap and
# idempotent; hourly keeps the S3 copy fresh without churn.
_BACKUP_INTERVAL_SECONDS = 60 * 60


async def db_backup_loop() -> None:
    """Periodically snapshot metadata.db to S3 (no-op in local mode). Errors are
    swallowed so a transient S3 hiccup never tears the loop down."""
    while True:
        try:
            await asyncio.sleep(_BACKUP_INTERVAL_SECONDS)
            if is_s3():
                await backup()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.warning("storage.db_backup.loop_failed", exc_info=True)


def main(argv: list[str] | None = None) -> int:
    """CLI: ``python -m mate.api.storage.db_backup [restore|backup]``."""
    import sys

    args = argv if argv is not None else sys.argv[1:]
    cmd = args[0] if args else "restore"
    if cmd == "restore":
        return 0 if restore_sync() else 1
    if cmd == "backup":
        return 0 if backup_sync() else 1
    print("usage: python -m mate.api.storage.db_backup [restore|backup]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
