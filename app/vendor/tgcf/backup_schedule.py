"""Weighted history scheduling. No Telegram API calls."""
import time
from pathlib import Path
from tgcf.state_store import locked, read_json, atomic_json

SETTINGS = '.tgcf_schedule.json'
DEFAULTS = {'normal_percent': 70, 'normal_minutes': 70, 'dump_minutes': 30, 'priorities': {}}


def settings(root):
    data = dict(DEFAULTS, **read_json(Path(root) / SETTINGS, {}))
    data['normal_percent'] = max(50, min(95, int(data['normal_percent'])))
    for field in ('normal_minutes', 'dump_minutes'):
        data[field] = max(1, min(300, int(data[field])))
    return data


def category(route, options):
    override = options.get('priorities', {}).get(route['connection_id'])
    if override in ('normal', 'dump'): return override
    # Only routes created by quick dump assignment get the lower priority.
    # Adding a dump receiver to an existing topic route keeps it normal.
    return 'dump' if route.get('con_name', '').startswith('Dump · ') else 'normal'


class FairScheduler:
    """Charge actual elapsed time, independently of the number of routes."""
    def __init__(self, state=None):
        self.state = dict(state or {})
        self.state.setdefault('used', {'normal': 0.0, 'dump': 0.0})
        self.state.setdefault('last', {})
        pending = self.state.pop('pending', None)
        if pending:
            self.record(pending['key'], pending['kind'], pending.get('elapsed', 0), pending['competing'])

    def choose(self, routes, options):
        queues = {kind: [r for r in routes if category(r, options) == kind] for kind in ('normal', 'dump')}
        available = [kind for kind in queues if queues[kind]]
        if not available: return None
        share = options['normal_percent'] / 100
        if len(available) == 1:
            # Spare capacity is free: it creates no debt against future work.
            kind = available[0]
            self.state['used'] = {'normal': 0.0, 'dump': 0.0}
        else:
            kind = min(available, key=lambda k: self.state['used'][k] / (share if k == 'normal' else 1 - share))
        queue = queues[kind]
        previous = self.state['last'].get(kind)
        ids = [r['connection_id'] for r in queue]
        index = (ids.index(previous) + 1) % len(queue) if previous in ids else 0
        return queue[index], kind, options[kind + '_minutes'] * 60, len(available) == 2

    def record(self, key, kind, elapsed, competing):
        self.state['last'][kind] = key
        if competing:
            self.state['used'][kind] += max(0, elapsed)
            floor = min(self.state['used'].values())
            # Keep values bounded without changing their weighted comparison.
            if floor > 86400:
                total = sum(self.state['used'].values())
                self.state['used'] = {k: v / total * 86400 for k, v in self.state['used'].items()}


def scheduled_names(root, account, available, scope='enabled', once=False):
    """Yield turns; runner sends back (elapsed seconds, time slice expired)."""
    root = Path(root)
    state_path = root / ('.tgcf_schedule_' + account + '.json')
    with locked(root): scheduler = FairScheduler(read_json(state_path, {}))
    finished = {}
    while True:
        with locked(root):
            options = settings(root)
            routes = available()
        routes = [r for r in routes if r['connection_id'] not in finished or (not once and time.monotonic() - finished[r['connection_id']] >= 300)]
        choice = scheduler.choose(routes, options)
        if choice is None: return
        route, kind, limit, competing = choice
        scheduler.state['pending'] = {'key': route['connection_id'], 'kind': kind, 'competing': competing, 'elapsed': 0}
        with locked(root): atomic_json(state_path, scheduler.state)
        elapsed, timed_out = yield route['connection_id'], limit, kind
        scheduler.state.pop('pending', None)
        scheduler.record(route['connection_id'], kind, elapsed, competing)
        with locked(root): atomic_json(state_path, scheduler.state)
        if once or not timed_out: finished[route['connection_id']] = time.monotonic()


def controls(st, root):
    with st.expander('History backup priorities · normal / dump'):
        with locked(root):
            options = settings(root)
            routes = read_json(Path(root) / 'tgcf.config.json').get('forwards', [])
        st.caption('Per account: normal backups receive the reserved share while both queues have work. Spare time is used by the other queue. Live forwarding is unchanged. Pauses and offsets are retained.')
        with st.form('history_priority_settings'):
            share = st.slider('Normal backup share (%)', 50, 95, options['normal_percent'])
            normal = st.number_input('Normal session limit (minutes)', 1, 300, options['normal_minutes'])
            dump = st.number_input('Dump session limit (minutes)', 1, 300, options['dump_minutes'])
            by_id = {r['connection_id']: r for r in routes}
            low = st.multiselect('Lower-priority dump routes', list(by_id), default=[k for k,r in by_id.items() if category(r, options) == 'dump'], format_func=lambda k: by_id[k].get('con_name') or k)
            save = st.form_submit_button('Save backup priorities')
        if save:
            with locked(root):
                latest = settings(root)
                latest.update(normal_percent=share, normal_minutes=int(normal), dump_minutes=int(dump))
                latest['priorities'].update({k: 'dump' if k in low else 'normal' for k in by_id})
                atomic_json(Path(root) / SETTINGS, latest)
            st.success('Saved. Applied at the next history turn; live forwarding is unaffected.')
