import time
from pathlib import Path
from tgcf.state_store import update_json


def activity(connection_id, **fields):
    def change(data):
        data.setdefault(connection_id, {}).update(fields, updated_at=time.time())
    update_json(Path.cwd() / '.tgcf_activity.json', change, {})
