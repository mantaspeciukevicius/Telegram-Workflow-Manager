"""Backup coverage and safe, opt-in routing to one shared dump chat."""
import re
import time
from pathlib import Path
from urllib.parse import urlparse

from tgcf.accounts import account_ids, account_label
from tgcf.state_store import locked, read_json, atomic_json, EditConflict

DUMP_FILE = '.tgcf_dump_chat.json'


def peer_key(value):
    value = str(value).strip().casefold()
    for prefix in ('https://t.me/', 'http://t.me/', 't.me/'):
        if value.startswith(prefix):
            value = value[len(prefix):].split('?', 1)[0].split('#', 1)[0].rstrip('/')
            break
    if value.lstrip('-').isdigit():
        return str(int(value))
    return value.lstrip('@')


def parse_peer(value):
    value = str(value).strip()
    if value.startswith(('https://t.me/', 'http://t.me/', 't.me/')):
        parsed = urlparse(value if '://' in value else 'https://' + value)
        if len(parsed.path.strip('/').split('/')) != 1:
            raise ValueError('Use a chat ID or public username, not a message or invite link.')
        value = parsed.path.strip('/')
    if re.fullmatch(r'-?[0-9]+', value):
        number = int(value)
        if number and abs(number) < 2**63:
            return number
    elif re.fullmatch(r'@?[A-Za-z][A-Za-z0-9_]{3,31}', value):
        return '@' + value.lstrip('@')
    raise ValueError('Enter a nonzero Telegram chat ID or a public @username.')


def alias_map(rows):
    result = {}
    for row in rows:
        ident = peer_key(row.get('ID', ''))
        if ident:
            result[ident] = ident
            username = peer_key(row.get('Username', ''))
            if username:
                result[username] = ident
    return result


def canonical(value, aliases):
    key = peer_key(value)
    return aliases.get(key, key)


def used_chat_keys(forwards, rows):
    """Chats reserved by any configured route, including disabled routes."""
    aliases = alias_map(rows)
    return {canonical(peer, aliases) for route in forwards
            for peer in [route.get('source', ''), *route.get('dest', [])]
            if peer_key(peer)}


def source_connections(row, forwards):
    keys = {peer_key(row.get('ID', '')), peer_key(row.get('Username', ''))} - {''}
    return [forward for forward in forwards if peer_key(forward.get('source', '')) in keys]


def coverage(rows, master, accounts, runtime=None, users=None, paused=None, modes=None, dump=None, now=None):
    runtime, users, paused, modes = runtime or {}, users or {}, paused or {}, modes or {}
    now = time.time() if now is None else now
    aliases = alias_map(rows)
    names = {peer_key(row['ID']): row['Name'] for row in rows}
    dump_key = canonical((dump or {}).get('peer', ''), aliases)
    result = []
    for original in rows:
        row = dict(original)
        connections = source_connections(row, master.get('forwards', []))
        states, owners, destinations = [], [], []
        for forward in connections:
            key = forward.get('connection_id')
            owner = accounts.get('assignments', {}).get(key)
            worker = runtime.get(owner, {})
            current = bool(key) and (worker.get('connection_id') == key or key in worker.get('connection_ids', []))
            fresh = now - worker.get('updated_at', 0) < 420
            mode = modes.get(owner, {}).get('mode', 'auto')
            if not forward.get('use_this', True) and not (current and fresh): state = 'Disabled'
            elif owner not in account_ids(accounts): state = 'Unassigned account'
            elif not forward.get('dest'): state = 'No destination'
            elif users.get(owner): state = 'Account paused'
            elif paused.get(key): state = 'Route paused'
            elif worker.get('state') == 'not_logged_in' and fresh: state = 'Not logged in'
            elif worker.get('state') == 'error' and fresh: state = 'Worker error'
            elif current and fresh:
                state = 'Live forwarding' if worker.get('mode') == 'live' or mode == 'live' else 'Backing up now'
            elif forward.get('end') and forward.get('offset', 0) >= forward['end'] and mode != 'live': state = 'History limit reached'
            elif not fresh or worker.get('state') == 'stopped': state = 'Configured · worker unavailable'
            else: state = 'Scheduled' if mode != 'live' else 'Live · waiting for worker'
            states.append(state)
            owners.append(account_label(accounts, owner) if owner else 'Unassigned')
            for destination in forward.get('dest', []):
                normalized = canonical(destination, aliases)
                label = names.get(normalized, str(destination))
                destinations.append(('Dump: ' if dump_key and normalized == dump_key else '') + label)
        row.update({'Backup status': '; '.join(dict.fromkeys(states)) if states else 'Not configured',
                    'Backup destination': '; '.join(dict.fromkeys(destinations)) or '—',
                    'Backup account': ', '.join(dict.fromkeys(owners)) or '—',
                    'Source connections': len(connections)})
        # Keep backup coverage next to the name, before IDs and receiver metadata.
        result.append({name: row.pop(name) for name in ('Name', 'Backup status', 'Backup destination', 'Backup account')} | row)
    return result


def read_dump(root):
    with locked(root):
        return read_json(Path(root) / DUMP_FILE, {})


def save_dump(root, peer, label='', expected=None):
    parsed = parse_peer(peer)
    value = {'peer': parsed, 'label': str(label).strip()[:200] or str(parsed)}
    with locked(root):
        path = Path(root) / DUMP_FILE
        if expected is not None and read_json(path, {}) != expected:
            raise EditConflict('The dump chat changed elsewhere. Reload before saving.')
        atomic_json(path, value)
    return value


def clear_dump(root, expected):
    with locked(root):
        path = Path(root) / DUMP_FILE
        if read_json(path, {}) != expected:
            raise EditConflict('The dump chat changed elsewhere. Reload before clearing.')
        atomic_json(path, {})


def _would_cycle(forwards, source, destination, aliases):
    graph = {}
    for forward in forwards:
        graph.setdefault(canonical(forward.get('source', ''), aliases), set()).update(
            canonical(value, aliases) for value in forward.get('dest', []))
    pending, seen = [destination], set()
    while pending:
        node = pending.pop()
        if node == source: return True
        if node not in seen:
            seen.add(node); pending.extend(graph.get(node, []))
    return False


def assign_to_dump(root, account, selected_rows, expected_dump, all_rows=None, include_existing=False):
    """Create new routes or explicitly add a destination, preserving topics and offsets."""
    from tgcf.config import Config, Forward
    from tgcf.web_ui.telegram_tools import account_login
    root = Path(root)
    if not selected_rows or len(selected_rows) > 100:
        raise ValueError('Select between 1 and 100 chats.')
    aliases = alias_map(all_rows or selected_rows)
    with locked(root):
        dump = read_json(root / DUMP_FILE, {})
        if not dump or dump != expected_dump:
            raise EditConflict('The dump chat changed. Reload and review the destination.')
        destination = parse_peer(dump.get('peer', ''))
        dest_key = canonical(destination, aliases)
        master = read_json(root / 'tgcf.config.json')
        accounts = read_json(root / 'tgcf.accounts.json')
        if account not in account_ids(accounts):
            raise ValueError('The selected Telegram account no longer exists.')
        login = account_login(master, accounts, account)
        if not (login.get('SESSION_STRING') or login.get('BOT_TOKEN')):
            raise ValueError('Log in to the selected Telegram account first.')
        forwards = master.setdefault('forwards', [])
        assignments = accounts.setdefault('assignments', {})
        created, seen = [], set()
        for row in selected_rows:
            source = parse_peer(row.get('ID', ''))
            src_key = canonical(source, aliases)
            if src_key in seen: continue
            seen.add(src_key)
            if src_key == dest_key or _would_cycle(forwards, src_key, dest_key, aliases):
                raise ValueError('This selection would create a forwarding loop. Choose another source or dump chat.')
            existing = [f for f in forwards if canonical(f.get('source', ''), aliases) == src_key]
            if existing:
                if include_existing and len(existing) == 1 and assignments.get(existing[0]['connection_id']) == account and existing[0].get('use_this', True):
                    forward = existing[0]
                    if dest_key not in [canonical(d, aliases) for d in forward.get('dest', [])]:
                        forward.setdefault('dest', []).append(destination)
                        created.append(forward['connection_id'])
                    continue
                # A retry after successful submission does not create a duplicate.
                if len(existing) == 1 and existing[0].get('use_this', True) and assignments.get(existing[0]['connection_id']) == account and [canonical(d, aliases) for d in existing[0].get('dest', [])] == [dest_key]:
                    continue
                raise EditConflict('A selected chat already has a source connection. Refresh the list or edit that connection; its destinations will not be replaced.')
            forward = Forward(con_name='Dump · ' + str(row.get('Name') or source)[:160],
                              source=source, dest=[destination], offset=0, use_this=True)
            forwards.append(forward.dict())
            assignments[forward.connection_id] = account
            created.append(forward.connection_id)
        if created:
            # Validate before either write; assignments first makes a partial failure
            # leave harmless unused assignments, never an unassigned enabled route.
            try: Config.parse_obj(master)
            except ValueError:
                raise ValueError('The existing configuration is invalid. Repair it before adding routes.') from None
            atomic_json(root / 'tgcf.accounts.json', accounts)
            atomic_json(root / 'tgcf.config.json', master)
        return created
