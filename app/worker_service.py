"""Docker-owned account scheduler; UI submits jobs, never spawns forwarding processes."""
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'vendor'))
from tgcf.accounts import account_ids
from tgcf.state_store import locked, read_json, atomic_json, update_json
from tgcf.web_ui.telegram_tools import account_login

STOP = False

def shutdown(*_):
    global STOP
    STOP = True

signal.signal(signal.SIGTERM, shutdown)
signal.signal(signal.SIGINT, shutdown)


def stop_process(process):
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=40)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def selected(master, accounts, account, scope):
    return [dict(f, use_this=True) for f in master.get('forwards', [])
            if accounts.get('assignments', {}).get(f['connection_id']) == account
            and (f.get('use_this', True) or scope == 'all')]


def publish(account, **fields):
    def change(data):
        data.setdefault(account, {}).update(fields, updated_at=time.time())
    update_json(ROOT / '.tgcf_runtime_status.json', change, {})


def live_job(account, spec):
    """Run all selected live connections together, so none waits behind another."""
    with locked(ROOT):
        master = read_json(ROOT / 'tgcf.config.json')
        accounts = read_json(ROOT / 'tgcf.accounts.json')
        paused = read_json(ROOT / '.tgcf_paused.json', {})
        forwards = [f for f in selected(master, accounts, account, spec.get('scope')) if not paused.get(f['connection_id'])]
        if not forwards:
            return
        master['login'] = account_login(master, accounts, account)
        master['forwards'] = forwards
        master['mode'] = 0
        import shutil
        log = ROOT / f'logs_{account}.txt'
        if log.exists(): shutil.copy2(log, ROOT / f'logs_{account}.previous.txt')
        folder = ROOT / '.tgcf_multi' / account
        folder.mkdir(parents=True, exist_ok=True)
        if (ROOT / '.env').exists(): shutil.copy2(ROOT / '.env', folder / '.env')
        atomic_json(folder / 'tgcf.config.json', master)
    process = None
    try:
        with (ROOT / f'logs_{account}.txt').open('w', buffering=1) as output:
            process = subprocess.Popen([sys.executable, str(ROOT / 'run_tgcf.py'), 'live'], cwd=folder, stdout=output, stderr=subprocess.STDOUT)
            while not STOP and process.poll() is None:
                publish(account, state='running', connection=None, connection_id=None,
                        connection_ids=[f['connection_id'] for f in forwards], mode='live', message='Live forwarding')
                time.sleep(2)
    finally:
        if process and process.poll() is None:
            process.terminate()
            try: process.wait(timeout=30)
            except subprocess.TimeoutExpired: process.kill(); process.wait()
    if process and process.returncode not in (0, -15):
        raise SystemExit(process.returncode)


def account_service(account):
    import fcntl
    handle = (ROOT / ('.tgcf_service_' + account + '.lock')).open('a')
    try: fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError: raise SystemExit('An account service already exists')
    process = None
    spec = {'mode': 'auto', 'scope': 'enabled'}
    last_request = None
    next_run = 0
    live_signature = None
    try:
        while not STOP:
            with locked(ROOT):
                accounts = read_json(ROOT / 'tgcf.accounts.json')
                master = read_json(ROOT / 'tgcf.config.json')
                paused = read_json(ROOT / '.tgcf_paused_users.json', {})
                connections_paused = read_json(ROOT / '.tgcf_paused.json', {})
                requests = read_json(ROOT / '.tgcf_worker_requests.json', {})
                request = requests.get(account)
            if account not in account_ids(accounts):
                break
            if request and request.get('id') != last_request:
                stop_process(process); process = None
                last_request = request['id']
                spec = request
                if spec.get('reset'):
                    with locked(ROOT):
                        latest = read_json(ROOT / 'tgcf.config.json')
                        latest_accounts = read_json(ROOT / 'tgcf.accounts.json')
                        pending = read_json(ROOT / '.tgcf_offset_requests.json', {})
                        for forward in selected(latest, latest_accounts, account, spec.get('scope')):
                            pending[forward['connection_id']] = 0
                        atomic_json(ROOT / '.tgcf_offset_requests.json', pending)
                # Remove consumed commands; persistent desired mode is stored separately.
                with locked(ROOT):
                    current = read_json(ROOT / '.tgcf_worker_requests.json', {})
                    if current.get(account, {}).get('id') == last_request:
                        current.pop(account, None)
                        atomic_json(ROOT / '.tgcf_worker_requests.json', current)
                    settings = read_json(ROOT / '.tgcf_worker_modes.json', {})
                    settings[account] = {'mode': spec.get('mode', 'auto'), 'scope': spec.get('scope', 'enabled')}
                    atomic_json(ROOT / '.tgcf_worker_modes.json', settings)
                next_run = 0
            elif last_request is None:
                with locked(ROOT):
                    spec = read_json(ROOT / '.tgcf_worker_modes.json', {}).get(account, spec)
            if paused.get(account):
                stop_process(process); process = None
                publish(account, state='user_paused', message='Account paused', connection_ids=[])
                next_run = 0
                time.sleep(2); continue
            mode = spec.get('mode', 'auto')
            signature = json.dumps([selected(master, accounts, account, spec.get('scope')), connections_paused], sort_keys=True)
            if mode == 'live' and process and signature != live_signature:
                stop_process(process); process = None; next_run = 0
            if process and process.poll() is not None:
                rc = process.returncode
                process = None
                next_run = time.monotonic() + 300
                if mode == 'once':
                    update_json(ROOT / '.tgcf_paused_users.json', lambda data: data.update({account: True}), {})
                    update_json(ROOT / '.tgcf_worker_modes.json', lambda data: data.update({account: {'mode': 'auto', 'scope': 'enabled'}}), {})
                    spec = {'mode': 'auto', 'scope': 'enabled'}
                publish(account, state='error' if rc else 'round_complete', message=f'Last run exit code {rc}; next cycle in five minutes', connection_ids=[])
            if process is None and time.monotonic() >= next_run:
                login = account_login(master, accounts, account)
                if not login.get('SESSION_STRING') and not login.get('BOT_TOKEN'):
                    publish(account, state='not_logged_in', message='Log in on Telegram accounts', connection_ids=[])
                    time.sleep(2); continue
                env = dict(os.environ, TGCF_JOB=json.dumps(spec))
                args = [sys.executable, str(ROOT / 'worker_service.py'), '--live', account] if mode == 'live' else [sys.executable, str(ROOT / 'tgcf_single_runner.py'), account]
                publish(account, mode='live' if mode == 'live' else 'past', connection_ids=[])
                process = subprocess.Popen(args, cwd=ROOT, env=env, start_new_session=True)
                live_signature = signature
                next_run = time.monotonic() + 300
            time.sleep(2)
    finally:
        stop_process(process)
        publish(account, state='stopped', message='Worker service stopped', connection_ids=[])


def extras_service():
    processes = {}
    try:
        while not STOP:
            with locked(ROOT):
                accounts = read_json(ROOT / 'tgcf.accounts.json')
            desired = set(account_ids(accounts)) - {'user1', 'user2', 'user3'}
            for account in set(processes) - desired:
                stop_process(processes.pop(account))
            for account in desired:
                if account not in processes or processes[account].poll() is not None:
                    processes[account] = subprocess.Popen([sys.executable, str(ROOT / 'worker_service.py'), account], start_new_session=True)
            time.sleep(2)
    finally:
        for process in processes.values(): stop_process(process)


if __name__ == '__main__':
    if sys.argv[1] == '--extras': extras_service()
    elif sys.argv[1] == '--live': live_job(sys.argv[2], json.loads(os.environ['TGCF_JOB']))
    elif re.fullmatch(r'user[1-9][0-9]*', sys.argv[1]): account_service(sys.argv[1])
    else: raise SystemExit('Invalid account')
