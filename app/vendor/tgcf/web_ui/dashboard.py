import time
import uuid
from pathlib import Path
import streamlit as st
from tgcf.accounts import account_ids, account_label
from tgcf.state_store import locked, read_json, update_json, atomic_json
from tgcf.web_ui.password import check_password
from tgcf.config import read_config, write_config

ROOT = Path.cwd()


def log_tail(path, lines):
    try:
        with path.open('rb') as stream:
            stream.seek(0, 2)
            position = stream.tell()
            chunks, count = [], 0
            while position and count <= lines:
                size = min(position, 8192)
                position -= size
                stream.seek(position)
                chunk = stream.read(size)
                chunks.append(chunk)
                count += chunk.count(b'\n')
        return b''.join(reversed(chunks)).decode('utf-8', errors='replace').splitlines()[-lines:]
    except FileNotFoundError:
        return []


def queue_job(root, accounts_to_run, mode, scope, reset=False):
    if mode not in ('auto', 'once', 'live') or scope not in ('enabled', 'all') or (reset and mode == 'live'):
        raise ValueError('Invalid job')
    with locked(root):
        accounts = read_json(root / 'tgcf.accounts.json')
        if any(account not in account_ids(accounts) for account in accounts_to_run):
            raise ValueError('Account no longer exists')
        requests = read_json(root / '.tgcf_worker_requests.json', {})
        paused = read_json(root / '.tgcf_paused_users.json', {})
        for account in accounts_to_run:
            requests[account] = {'id': str(uuid.uuid4()), 'mode': mode, 'scope': scope, 'reset': reset, 'created_at': time.time()}
            paused[account] = False
        atomic_json(root / '.tgcf_worker_requests.json', requests)
        atomic_json(root / '.tgcf_paused_users.json', paused)


def connection_rows(master, accounts, runtime, history, activity, users, paused, pending):
    rows = []
    for index, forward in enumerate(master.get('forwards', [])):
        key = forward['connection_id']
        owner = accounts.get('assignments', {}).get(key, 'unassigned')
        worker = runtime.get(owner, {})
        current = worker.get('connection_id') == key or key in worker.get('connection_ids', [])
        report = history.get(key, {})
        event = activity.get(key, {})
        if not forward.get('use_this', True) and not current: state = 'Disabled'
        elif owner == 'unassigned': state = 'Unassigned'
        elif users.get(owner): state = 'User paused'
        elif paused.get(key): state = 'Paused'
        elif current and time.time() - worker.get('updated_at', 0) < 30:
            state = worker.get('state', 'running')
            if event.get('flood_until', 0) > time.time():
                state = 'FloodWait: ' + str(int(event['flood_until'] - time.time())) + 's'
            elif event.get('state') == 'error': state = 'Error'
        else: state = report.get('state', 'Waiting')
        when = event.get('last_event')
        rows.append({'Connection': forward.get('con_name') or 'Connection ' + str(index + 1),
                     'User': account_label(accounts, owner), 'Enabled': forward.get('use_this', True),
                     'Source': str(forward.get('source', '')), 'Destinations': ', '.join(map(str, forward.get('dest', []))),
                     'Status': state, 'Saved offset': forward.get('offset', 0),
                     'Worker offset': str(worker.get('offsets', {}).get(key, '—')) if current else '—',
                     'Pending offset': str(pending.get(key, '—')), 'Last copied message': str(event.get('last_message_id', '—')),
                     'Last activity': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(when)) if when else '—',
                     'Details': event.get('detail') or report.get('message', '')})
    return rows


def dashboard():
    if not check_password(st): st.stop()
    from tgcf.backup_schedule import controls
    controls(st, ROOT)
    st.title('Backup workers')
    st.button('Refresh status')
    try:
        with locked(ROOT):
            master = read_json(ROOT / 'tgcf.config.json')
            accounts = read_json(ROOT / 'tgcf.accounts.json')
            runtime = read_json(ROOT / '.tgcf_runtime_status.json', {})
            users = read_json(ROOT / '.tgcf_paused_users.json', {})
            paused = read_json(ROOT / '.tgcf_paused.json', {})
            history = read_json(ROOT / '.tgcf_connection_status.json', {})
            pending = read_json(ROOT / '.tgcf_offset_requests.json', {})
            requests = read_json(ROOT / '.tgcf_worker_requests.json', {})
        activity = {}
        for account in account_ids(accounts):
            activity.update(read_json(ROOT / '.tgcf_multi' / account / '.tgcf_activity.json', {}))
    except (ValueError, OSError):
        st.error('Cannot read configuration or status. Reload before using controls.'); st.stop()
    with st.expander('Forwarding settings'):
        config = read_config()
        with st.form('worker_settings'):
            config.past.delay = int(st.number_input('Delay between messages (seconds)', min_value=0, max_value=100, value=config.past.delay))
            config.show_forwarded_from = st.checkbox('Show original forwarded source', value=config.show_forwarded_from)
            config.live.delete_sync = st.checkbox('Sync deleted messages in LIVE mode', value=config.live.delete_sync)
            save = st.form_submit_button('Save forwarding settings')
        if save:
            try:
                write_config(config); st.success('Saved. Restart the job to apply changes to an active LIVE run.')
            except ValueError: st.error('Configuration changed elsewhere. Reload before saving.')
    with st.expander('Run backup / LIVE jobs'):
        with st.form('manual_job'):
            owner = st.selectbox('Run for', ['all'] + account_ids(accounts), format_func=lambda key: 'All accounts' if key == 'all' else account_label(accounts, key))
            mode = st.selectbox('Operation', ['auto', 'once', 'live'], format_func=lambda value: {'auto': 'Catch up now and continue scheduled backups', 'once': 'Catch up once, then pause', 'live': 'LIVE — forward new messages'}[value])
            scope = st.selectbox('Connections to include', ['enabled', 'all'])
            reset = st.checkbox('Restart selected histories from zero (can duplicate existing messages)')
            confirmed = st.checkbox('Confirm restarting from zero')
            run = st.form_submit_button('Start job')
        st.caption('Jobs replace the account’s current operation after saving progress. Individual connection pauses remain in effect. “All” includes disabled connections without changing their saved Enabled setting.')
        if run:
            if reset and (not confirmed or mode == 'live'):
                st.error('Confirm a reset and choose a catch-up operation, not LIVE.')
            else:
                queue_job(ROOT, account_ids(accounts) if owner == 'all' else [owner], mode, scope, reset)
                st.success('Queued. Workers save current progress before starting the requested operation.')
    for account in account_ids(accounts):
        item = runtime.get(account, {})
        label = account_label(accounts, account)
        st.subheader(label + ' · ' + account)
        count = sum(f.get('use_this', True) and accounts.get('assignments', {}).get(f['connection_id']) == account for f in master.get('forwards', []))
        st.caption(str(count) + ' enabled connections')
        age = max(0, int(time.time() - item.get('updated_at', 0)))
        state = item.get('state', 'unknown')
        st.write(('Pause requested · ' if users.get(account) else '') + state + (' · ' + item['connection'] if item.get('connection') else ''))
        if item.get('message'): st.caption(item['message'])
        if age > 420: st.warning('No recent worker report')
        if account in requests: st.info('Job queued: ' + requests[account]['mode'])
        if st.button('Resume scheduled backups' if users.get(account) else 'Pause / stop account', key='pause_' + account):
            if users.get(account): queue_job(ROOT, [account], 'auto', 'enabled')
            else: update_json(ROOT / '.tgcf_paused_users.json', lambda data: data.update({account: True}), {})
            st.experimental_rerun()
        tail = log_tail(ROOT / ('logs_' + account + '.txt'), 10)
        if tail: st.caption('Last log: ' + tail[-1])
    st.subheader('Connection overview')
    rows = connection_rows(master, accounts, runtime, history, activity, users, paused, pending)
    if rows: st.dataframe(rows, use_container_width=True)
    for forward in master.get('forwards', []):
        key = forward['connection_id']
        name = forward.get('con_name') or key
        if st.button(('Resume ' if paused.get(key) else 'Pause ') + name, key='connection_pause_' + key):
            desired = not bool(paused.get(key))
            update_json(ROOT / '.tgcf_paused.json', lambda data: data.update({key: desired}), {})
            st.experimental_rerun()
    st.subheader('Logs by user')
    lines = st.selectbox('Lines of logs to show', [50, 100, 250, 1000], index=1)
    for account in account_ids(accounts):
        with st.expander(account_label(accounts, account) + ' log'):
            try:
                content = '\n'.join(log_tail(ROOT / ('logs_' + account + '.txt'), lines))
                st.code(content or 'No log yet for this user.', language='text')
                st.download_button('Download displayed logs', content, file_name=account + '-logs.txt', key='download_' + account)
                previous = ROOT / ('logs_' + account + '.previous.txt')
                if previous.exists():
                    with previous.open('rb') as stream:
                        st.download_button('Download previous round', stream, file_name=account + '-previous.txt', key='previous_' + account)
            except OSError: st.error('Could not read this user’s log file.')
