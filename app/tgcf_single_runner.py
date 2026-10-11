#!/usr/bin/env python3

import re
import fcntl
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, "/opt/my-tgcf/vendor")
from tgcf.state_store import locked, atomic_json, read_json

ROOT = Path("/opt/my-tgcf")
MASTER = ROOT / "tgcf.config.json"
ACCOUNTS = ROOT / "tgcf.accounts.json"
LOCKFILE = ROOT / ".tgcf_config.lock"
TMPROOT = ROOT / ".tgcf_multi"
OFFSET_REQUESTS = ROOT / ".tgcf_offset_requests.json"

# Persistent pause state.
PAUSE_FILE = ROOT / ".tgcf_paused.json"
USER_PAUSE_FILE = ROOT / ".tgcf_paused_users.json"

# Current worker state shown by the Backup Control GUI.
RUNTIME_FILE = ROOT / ".tgcf_runtime_status.json"

# One connection may run for at most 5 hours before the runner
# saves its current offset and gives the next connection a turn.
MAX_CONNECTION_RUNTIME = 5 * 60 * 60

# CHILD -> MASTER synchronization interval.
SYNC_INTERVAL = 2

# When a 5-hour time slice expires, give TGCF this long to
# terminate normally before forcing it to stop.
TERMINATE_GRACE = 30

PROCESS = None
STOP = False
JOB = json.loads(os.environ.get('TGCF_JOB', '{}'))


def load_json(path, attempts=20, delay=0.1):
    """
    TGCF sometimes rewrites its JSON config non-atomically.
    During that tiny window the file can temporarily be empty
    or incomplete.

    Retry instead of crashing the runner.
    """
    last_error = None

    for attempt in range(attempts):
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()

            if not text.strip():
                raise json.JSONDecodeError(
                    "Temporary empty JSON file",
                    text,
                    0,
                )

            return json.loads(text)

        except (json.JSONDecodeError, OSError) as exc:
            last_error = exc

            if attempt < attempts - 1:
                time.sleep(delay)

    raise last_error


def save_json(path, data):
    with locked(ROOT):
        atomic_json(path, data)

def master_lock():
    return locked(ROOT)


def connection_name(fwd, index):
    return fwd["connection_id"]


def get_login(account, master, accounts):
    """
    Return Telegram login configuration for user1/user2/user3.
    User2/User3 may reuse User1 API ID/API Hash while keeping
    their own Telegram session.
    """

    if account == "user1":
        return dict(master["login"])

    user = accounts.get(account)

    if not user:
        raise RuntimeError(
            f"{account} configuration does not exist."
        )

    label = user.get(
        "label",
        account,
    )

    if not user.get(
        "session_string"
    ):
        raise RuntimeError(
            f"{label} is not logged in."
        )

    if user.get(
        "reuse_user1_api",
        True,
    ):
        api_id = master["login"]["API_ID"]
        api_hash = master["login"]["API_HASH"]

    else:
        api_id = int(
            user.get("api_id")
            or 0
        )

        api_hash = user.get(
            "api_hash",
            "",
        )

    if not api_id or not api_hash:
        raise RuntimeError(
            f"{label} API ID/API Hash are missing."
        )

    return {
        "API_ID": api_id,
        "API_HASH": api_hash,
        "user_type": 1,
        "phone_no": 0,
        "USERNAME": "",
        "SESSION_STRING": user["session_string"],
        "BOT_TOKEN": "",
    }

def get_forwards(master, accounts, account):
    """
    Return enabled connections belonging to this Telegram account.
    """
    result = []
    assignments = accounts.get("assignments", {})

    for index, forward in enumerate(master.get("forwards", [])):
        name = connection_name(forward, index)

        if assignments.get(name, "unassigned") != account:
            continue

        if not forward.get("use_this", True) and JOB.get("scope") != "all":
            continue

        item = dict(forward)
        item["dest"] = list(forward.get("dest", []))
        item["use_this"] = True

        result.append(item)

    return result



def load_optional_json(path, default=None):
    """
    Safely read optional state JSON.

    Pause/runtime files do not exist until first used.
    """
    if default is None:
        default = {}

    if not path.exists():
        return default

    return load_json(path)


def load_paused_connections():
    data = load_optional_json(
        PAUSE_FILE,
        {},
    )

    return data if isinstance(data, dict) else {}


def is_connection_paused(name):
    """
    Persistent pause for one backup connection.
    """
    return bool(
        load_paused_connections().get(
            name,
            False,
        )
    )


def load_paused_users():
    data = load_optional_json(
        USER_PAUSE_FILE,
        {},
    )

    return data if isinstance(data, dict) else {}


def is_user_paused(account_name):
    """
    Persistent pause for an entire Telegram account.
    """
    return bool(
        load_paused_users().get(
            account_name,
            False,
        )
    )


def update_runtime(
    state,
    connection=None,
    start_offset=None,
    message=None,
):
    """
    Publish this worker's current state for the web GUI.

    User1 and User2 each maintain their own entry.
    """
    with master_lock():

        runtime = load_optional_json(
            RUNTIME_FILE,
            {},
        )

        runtime[account] = {
            "state": state,
            "connection_id": connection,
            "connection": next((f.get('con_name') or 'Unnamed connection' for f in load_json(MASTER).get('forwards', []) if f.get('connection_id') == connection), connection),
            "pid": os.getpid(),
            "start_offset": start_offset,
            "message": message,
            "updated_at": time.time(),
        }

        save_json(
            RUNTIME_FILE,
            runtime,
        )
        if connection:
            history = load_optional_json(ROOT / '.tgcf_connection_status.json', {})
            history[connection] = dict(runtime[account], account=account)
            save_json(ROOT / '.tgcf_connection_status.json', history)



def load_offset_requests():
    if not OFFSET_REQUESTS.exists():
        return {}

    try:
        return load_json(OFFSET_REQUESTS)
    except Exception:
        return {}


def save_offset_requests(data):
    save_json(OFFSET_REQUESTS, data)


def assigned_account(forward, index, accounts):
    name = connection_name(forward, index)

    return accounts.get(
        "assignments",
        {},
    ).get(
        name,
        "unassigned",
    )


def apply_pending_offset_requests(accounts, account):
    """
    Apply GUI offset requests to MASTER.

    Examples:
        Reset offset to 0
        Start from latest message

    Requests remain queued until a CHILD config containing the
    requested value has actually been created.
    """
    applied = {}

    with master_lock():
        requests = load_offset_requests()

        if not requests:
            return applied

        master = load_json(MASTER)
        changed = False

        for index, forward in enumerate(
            master.get("forwards", [])
        ):
            name = connection_name(
                forward,
                index,
            )

            if name not in requests:
                continue

            if assigned_account(
                forward,
                index,
                accounts,
            ) != account:
                continue

            value = int(requests[name])

            forward["offset"] = value
            applied[name] = value
            changed = True

            print(
                f"{account}: applying queued offset "
                f"{name} -> {value}",
                flush=True,
            )

        if changed:
            save_json(
                MASTER,
                master,
            )

    return applied


def clear_applied_offset_request(name, expected_value):
    """
    Clear ONE GUI request only after that connection's CHILD
    config has been created.

    If a newer GUI request appeared meanwhile, leave it alone.
    """
    if expected_value is None:
        return

    with master_lock():
        requests = load_offset_requests()

        if requests.get(name) != expected_value:
            return

        del requests[name]
        save_offset_requests(requests)


def sync_offsets(config_path):
    """
    Copy offsets written by TGCF in CHILD back into MASTER.
    """
    if not config_path.exists():
        return

    try:
        child = load_json(config_path)

    except (json.JSONDecodeError, OSError) as exc:
        print(
            f"{account}: temporary config read error; "
            f"will retry on next sync: {exc}",
            flush=True,
        )
        return

    with master_lock():
        schedule_path = ROOT / ('.tgcf_schedule_' + account + '.json')
        schedule_state = read_json(schedule_path, {})
        pending = schedule_state.get('pending')
        if pending and any(f.get('connection_id') == pending['key'] for f in child.get('forwards', [])) and 'started_at' in globals():
            pending['elapsed'] = max(0, time.monotonic() - started_at)
            atomic_json(schedule_path, schedule_state)
        runtime = load_optional_json(RUNTIME_FILE, {})
        if account in runtime:
            runtime[account]['updated_at'] = time.time()
            runtime[account]['pid'] = os.getpid()
            runtime[account]['offsets'] = {f['connection_id']: f.get('offset', 0) for f in child.get('forwards', [])}
            save_json(RUNTIME_FILE, runtime)

        master = load_json(MASTER)
        pending_offsets = load_offset_requests()

        by_name = {
            connection_name(fwd, i): fwd
            for i, fwd in enumerate(
                master.get("forwards", [])
            )
        }

        changed = False

        for i, fwd in enumerate(
            child.get("forwards", [])
        ):
            name = connection_name(fwd, i)
            target = by_name.get(name)

            if target is None:
                continue

            # A newly queued GUI offset change must always win
            # over the currently running CHILD offset.
            if name in pending_offsets:
                continue

            new_offset = fwd.get("offset", 0)

            if target.get("offset") != new_offset:
                target["offset"] = new_offset
                changed = True

        if changed:
            save_json(
                MASTER,
                master,
            )


def get_current_connection(
    connection_to_find,
    master,
    accounts,
    account,
):
    """
    Re-read a connection from the latest MASTER configuration.

    This means a connection receives the newest persisted offset
    when its turn starts rather than an old snapshot taken hours ago.
    """
    assignments = accounts.get(
        "assignments",
        {},
    )

    for index, forward in enumerate(
        master.get("forwards", [])
    ):
        name = connection_name(
            forward,
            index,
        )

        if name != connection_to_find:
            continue

        if assignments.get(
            name,
            "unassigned",
        ) != account:
            return None

        if not forward.get("use_this", True) and JOB.get("scope") != "all":
            return None

        item = dict(forward)
        item["dest"] = list(
            forward.get(
                "dest",
                [],
            )
        )
        item["use_this"] = True

        return item

    return None


def stop_process_for_rotation():
    """
    Stop the currently running TGCF process after its time slice.

    First request normal termination. If TGCF does not stop within
    TERMINATE_GRACE seconds, force-kill it.
    """
    global PROCESS

    if PROCESS is None:
        return

    if PROCESS.poll() is not None:
        return

    PROCESS.terminate()

    try:
        PROCESS.wait(
            timeout=TERMINATE_GRACE
        )

    except subprocess.TimeoutExpired:
        print(
            f"{account}: TGCF did not terminate within "
            f"{TERMINATE_GRACE}s; killing it",
            flush=True,
        )

        PROCESS.kill()
        PROCESS.wait()


def stop_handler(signum, frame):
    """
    Handle Docker stop / Ctrl+C cleanly.
    """
    global STOP

    STOP = True

    if PROCESS is not None and PROCESS.poll() is None:
        try:
            PROCESS.terminate()
        except Exception:
            pass


# ------------------------------------------------------------------
# START
# ------------------------------------------------------------------

if len(sys.argv) != 2 or not re.fullmatch(r'user[1-9][0-9]*', sys.argv[1]):
    raise SystemExit('Usage: tgcf_single_runner.py userN')

account = sys.argv[1]
worker_lock = open(ROOT / ('.tgcf_worker_' + account + '.lock'), 'a')
os.chmod(worker_lock.name, 0o600)
try:
    fcntl.flock(worker_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    raise SystemExit('Another worker already owns this account')


signal.signal(
    signal.SIGTERM,
    stop_handler,
)

signal.signal(
    signal.SIGINT,
    stop_handler,
)

accounts = load_json(ACCOUNTS)

# Entire Telegram account paused.
# The wrapper may still wake every 5 minutes, but this runner exits
# immediately without touching any assigned backups.
if is_user_paused(
    account
):
    update_runtime(
        "user_paused",
        message="Entire Telegram user is paused",
    )

    print(
        f"{account}: USER PAUSED - all assigned backups skipped",
        flush=True,
    )

    raise SystemExit(0)


# Apply any Reset-to-0 / Start-from-latest requests already waiting
# before this round starts.
applied_offset_requests = (
    apply_pending_offset_requests(
        accounts,
        account,
    )
)

with master_lock():
    master = load_json(MASTER)

forwards = get_forwards(
    master,
    accounts,
    account,
)

if not forwards:
    update_runtime(
        "idle",
        message="No enabled connections assigned",
    )

    print(
        f"{account}: no enabled connections assigned",
        flush=True,
    )

    raise SystemExit(0)

# Weighted queues reload routes, pauses and ownership before every turn.
from tgcf.backup_schedule import scheduled_names

def available_routes():
    latest_master = load_json(MASTER)
    latest_accounts = load_json(ACCOUNTS)
    paused_routes = load_optional_json(ROOT / '.tgcf_paused.json', {})
    return [f for f in get_forwards(latest_master, latest_accounts, account)
            if not paused_routes.get(f['connection_id'])]

turns = scheduled_names(ROOT, account, available_routes, once=JOB.get('mode') == 'once')

def turn_names():
    global MAX_CONNECTION_RUNTIME, accounts, timed_out
    try: turn = next(turns)
    except StopIteration: return
    while True:
        name, MAX_CONNECTION_RUNTIME, kind = turn
        accounts = load_json(ACCOUNTS)
        timed_out = False
        before = time.monotonic()
        yield name
        try: turn = turns.send((time.monotonic() - before, globals().get('timed_out', False)))
        except StopIteration: return

# Keep the order from MASTER.
connection_names = [
    connection_name(
        forward,
        index,
    )
    for index, forward in enumerate(
        forwards
    )
]

workdir = TMPROOT / account
workdir.mkdir(
    parents=True,
    exist_ok=True,
)

root_env = ROOT / ".env"

if root_env.exists():
    shutil.copy2(
        root_env,
        workdir / ".env",
    )

config_path = workdir / "tgcf.config.json"
log_path = ROOT / f"logs_{account}.txt"

tgcf_executable = Path(
    "/usr/local/bin/tgcf"
)

overall_rc = 0

print(
    f"{account}: round contains "
    f"{len(connection_names)} connection(s)",
    flush=True,
)

print(
    f"{account}: weighted normal/dump scheduling enabled; limits reload each turn",
    flush=True,
)


# Preserve the previous round for download.
if log_path.exists():
    shutil.copy2(log_path, ROOT / f'logs_{account}.previous.txt')
with open(
    log_path,
    "w",
    encoding="utf-8",
    buffering=1,
) as logfile:

    for position, name in enumerate(
        turn_names(),
        start=1,
    ):
        if STOP:
            break

        # --------------------------------------------------------
        # WHOLE USER PAUSE
        # --------------------------------------------------------

        if is_user_paused(
            account
        ):
            print(
                f"{account}: USER PAUSED - "
                f"all remaining backups skipped",
                flush=True,
            )

            update_runtime(
                "user_paused",
                message="Entire Telegram user is paused",
            )

            break


        # --------------------------------------------------------
        # INDIVIDUAL CONNECTION PAUSE
        # --------------------------------------------------------

        if is_connection_paused(
            name
        ):
            print(
                f"{account}: [turn {position}] "
                f"{name} is PAUSED - skipping",
                flush=True,
            )

            continue


        # Apply offset changes that may have been queued while an
        # earlier connection was using its time slice.
        accounts = load_json(ACCOUNTS)

        newly_applied = (
            apply_pending_offset_requests(
                accounts,
                account,
            )
        )

        applied_offset_requests.update(
            newly_applied
        )

        timed_out = False
        # Always reload MASTER immediately before this connection.
        # This guarantees we use its newest synchronized offset.
        with master_lock():
            current_master = load_json(
                MASTER
            )

        forward = get_current_connection(
            name,
            current_master,
            accounts,
            account,
        )

        if forward is None:
            print(
                f"{account}: skipping {name}; "
                f"connection was removed, disabled, "
                f"or reassigned",
                flush=True,
            )
            continue

        login = get_login(
            account,
            current_master,
            accounts,
        )

        child_config = dict(
            current_master
        )

        child_config["login"] = login

        # CRITICAL CHANGE:
        # TGCF receives exactly ONE connection at a time.
        child_config["forwards"] = [
            forward
        ]

        save_json(
            config_path,
            child_config,
        )

        # If this connection had a GUI offset request, its freshly
        # written CHILD now contains that requested offset, so that
        # specific request can safely be removed.
        requested_value = (
            applied_offset_requests.get(
                name
            )
        )

        clear_applied_offset_request(
            name,
            requested_value,
        )

        start_offset = forward.get(
            "offset",
            0,
        )

        update_runtime(
            "running",
            connection=name,
            start_offset=start_offset,
            message="TGCF Past is running",
        )

        print(
            "",
            flush=True,
        )

        print(
            f"{account}: [turn {position}] "
            f"starting {name} "
            f"from offset {start_offset}",
            flush=True,
        )

        logfile.write(
            "\n"
            "============================================================\n"
            f"{time.strftime('%Y-%m-%d %H:%M:%S')} "
            f"- {account} - {forward.get('con_name') or name}\n"
            f"Starting offset: {start_offset}\n"
            "============================================================\n"
        )

        logfile.flush()

        started_at = time.monotonic()

        timed_out = False
        connection_paused_during_run = False
        user_paused_during_run = False

        PROCESS = subprocess.Popen(
            [
                sys.executable,
                str(ROOT / 'run_tgcf.py'),
                "past",
            ],
            cwd=workdir,
            stdout=logfile,
            stderr=subprocess.STDOUT,
        )

        while (
            not STOP
            and PROCESS.poll() is None
        ):
            sync_offsets(
                config_path
            )


            # ----------------------------------------------------
            # LIVE WHOLE-USER PAUSE
            # ----------------------------------------------------

            if is_user_paused(
                account
            ):
                user_paused_during_run = True

                print(
                    f"{account}: user pause requested while "
                    f"{name} is running; saving progress",
                    flush=True,
                )

                update_runtime(
                    "user_pausing",
                    connection=name,
                    start_offset=start_offset,
                    message="Stopping all backups for this user",
                )

                # Save the newest CHILD offset before stopping.
                sync_offsets(
                    config_path
                )

                stop_process_for_rotation()

                break


            # ----------------------------------------------------
            # LIVE INDIVIDUAL CONNECTION PAUSE
            # ----------------------------------------------------

            if is_connection_paused(
                name
            ):
                connection_paused_during_run = True

                print(
                    f"{account}: pause requested for {name}; "
                    f"saving progress and moving to next backup",
                    flush=True,
                )

                update_runtime(
                    "connection_pausing",
                    connection=name,
                    start_offset=start_offset,
                    message="Stopping this backup",
                )

                sync_offsets(
                    config_path
                )

                stop_process_for_rotation()

                break


            elapsed = (
                time.monotonic()
                - started_at
            )

            if (
                elapsed
                >= MAX_CONNECTION_RUNTIME
            ):
                timed_out = True

                print(
                    f"{account}: {name} reached "
                    f"{MAX_CONNECTION_RUNTIME // 60}-minute time slice; saving progress "
                    f"and moving to next connection",
                    flush=True,
                )

                # Sync immediately BEFORE asking TGCF to stop.
                sync_offsets(
                    config_path
                )

                stop_process_for_rotation()
                break

            time.sleep(
                SYNC_INTERVAL
            )

        if PROCESS.poll() is None:
            PROCESS.wait()

        # Always perform one last CHILD -> MASTER synchronization.
        sync_offsets(
            config_path
        )

        rc = PROCESS.returncode

        if rc is None:
            rc = 0

        if STOP:
            update_runtime(
                "stopping",
                connection=name,
                message="Worker stop requested",
            )

            print(
                f"{account}: stop requested while processing "
                f"{name}",
                flush=True,
            )

            break


        # Whole-user pause wins over all other result states.
        if user_paused_during_run:

            print(
                f"{account}: user paused successfully; "
                f"all {account} backups are stopped",
                flush=True,
            )

            update_runtime(
                "user_paused",
                connection=name,
                message="Entire Telegram user is paused",
            )

            PROCESS = None
            break


        if connection_paused_during_run:

            print(
                f"{account}: {name} paused successfully",
                flush=True,
            )

            update_runtime(
                "connection_paused",
                connection=name,
                message="Backup paused by user",
            )


        elif timed_out:

            # SIGTERM may produce a non-zero process return code.
            # This is expected for our deliberate rotation.
            print(
                f"{account}: paused {name}; "
                f"its next round will resume from the "
                f"persisted offset",
                flush=True,
            )

            update_runtime(
                "rotated",
                connection=name,
                message=f"{MAX_CONNECTION_RUNTIME // 60}-minute time slice reached",
            )


        elif rc == 0:

            print(
                f"{account}: {name} is caught up / "
                f"finished successfully",
                flush=True,
            )

            update_runtime(
                "finished",
                connection=name,
                message="Caught up",
            )


        else:

            print(
                f"{account}: {name} finished with "
                f"exit code {rc}; continuing to next connection",
                flush=True,
            )

            update_runtime(
                "error",
                connection=name,
                message=f"TGCF exit code {rc}",
            )

            if overall_rc == 0:
                overall_rc = rc


        PROCESS = None


if STOP:

    update_runtime(
        "stopped",
        message="Worker stopped",
    )

    raise SystemExit(0)


if is_user_paused(
    account
):

    update_runtime(
        "user_paused",
        message="Entire Telegram user is paused",
    )

    print(
        f"{account}: remaining paused until resumed",
        flush=True,
    )

    raise SystemExit(0)


update_runtime(
    "round_complete",
    message="Waiting for next worker cycle",
)


print(
    f"{account}: completed one full connection round",
    flush=True,
)

raise SystemExit(overall_rc)
