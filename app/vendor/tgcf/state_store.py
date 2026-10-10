"""Shared transactional storage for the GUI and Linux workers."""
import copy
import fcntl
import json
import os
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

_mutex = threading.RLock()
_local = threading.local()

@contextmanager
def locked(root=None):
    root = Path(root or os.getcwd()).resolve()
    with _mutex:
        held = getattr(_local, 'held', {})
        if root in held:
            yield
            return
        with open(root / '.tgcf_config.lock', 'a') as handle:
            os.chmod(handle.name, 0o600)
            fcntl.flock(handle, fcntl.LOCK_EX)
            held[root] = handle
            _local.held = held
            try:
                yield
            finally:
                del held[root]
                fcntl.flock(handle, fcntl.LOCK_UN)

def read_json(path, default=None):
    try:
        with open(path, encoding='utf-8') as handle:
            value = json.load(handle)
    except FileNotFoundError:
        if default is None:
            raise
        return copy.deepcopy(default)
    if not isinstance(value, dict):
        raise ValueError('Configuration must be a JSON object: ' + str(path))
    return value

def atomic_json(path, value):
    path = Path(path)
    descriptor, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            os.fchmod(handle.fileno(), 0o600)
            json.dump(value, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

def update_json(path, change, default=None):
    with locked(Path(path).parent):
        value = read_json(path, default)
        change(value)
        atomic_json(path, value)
        return value

class EditConflict(ValueError):
    pass

def merge_changes(before, draft, current):
    """Three-way merge; untouched fields retain current worker/browser changes."""
    if before == draft:
        return copy.deepcopy(current)
    if isinstance(before, dict) and isinstance(draft, dict) and isinstance(current, dict):
        result = copy.deepcopy(current)
        for key in before.keys() | draft.keys():
            if key not in draft:
                if current.get(key) != before[key]:
                    raise EditConflict('This field changed elsewhere. Reload before saving.')
                result.pop(key, None)
            elif key not in before:
                if key in current and current[key] != draft[key]:
                    raise EditConflict('This field was added elsewhere. Reload before saving.')
                result[key] = copy.deepcopy(draft[key])
            else:
                result[key] = merge_changes(before[key], draft[key], current.get(key))
        return result
    if isinstance(before, list) and isinstance(draft, list) and isinstance(current, list) and all(isinstance(x, dict) and x.get('connection_id') for x in before + draft + current):
        old = {x['connection_id']: x for x in before}
        new = {x['connection_id']: x for x in draft}
        live = {x['connection_id']: x for x in current}
        result = []
        for item in current:
            key = item['connection_id']
            if key not in old:
                result.append(item)
            elif key in new:
                result.append(merge_changes(old[key], new[key], item))
            elif item != old[key]:
                # Worker progress must not prevent intentional deletion.
                if {k:v for k,v in item.items() if k != 'offset'} != {k:v for k,v in old[key].items() if k != 'offset'}:
                    raise EditConflict('Connection changed elsewhere. Reload before deleting.')
        for key, item in new.items():
            if key not in old:
                if key in live:
                    raise EditConflict('Duplicate connection identity.')
                result.append(item)
            elif key not in live:
                raise EditConflict('Connection was deleted elsewhere. Reload before saving.')
        return result
    if current != before and current != draft:
        raise EditConflict('This field changed elsewhere. Reload before saving.')
    return copy.deepcopy(draft)

def redact(value):
    secrets = {'api_hash', 'bot_token', 'session_string', 'temp_session', 'temp_session_string', 'password', 'phone_code_hash'}
    if isinstance(value, dict):
        return {k: ('[redacted]' if k.lower() in secrets and v else redact(v)) for k,v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value
