"""Synthetic, offline coverage/routing tests. Never connects to Telegram."""
import copy
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from tgcf import backup_routes as routes
from tgcf.state_store import atomic_json, read_json, EditConflict
from tgcf.web_ui.telegram_tools import chat_usage

ROWS = [
    {'Name': 'Specific topic', 'ID': '-1001', 'Username': '@topic_source', 'Type': 'Channel', 'Migration': ''},
    {'Name': 'Miscellaneous models', 'ID': '-1002', 'Username': '@model_files', 'Type': 'Channel', 'Migration': ''},
    {'Name': 'Shared archive', 'ID': '-1009', 'Username': '@archive_dump', 'Type': 'Supergroup', 'Migration': ''},
]


def fixture():
    master = {'login': {'API_ID': 100, 'API_HASH': 'synthetic-only', 'SESSION_STRING': 'synthetic-never-connect', 'user_type': 1},
              'forwards': [{'connection_id': 'topic-route', 'source': -1001, 'dest': [-1009], 'con_name': 'Existing topic', 'use_this': True, 'offset': 55}],
              'future_setting': {'keep': True}}
    accounts = {'user2': {'label': 'Second account', 'session_string': 'synthetic-never-connect'},
                'assignments': {'topic-route': 'user1'}, 'keep_setting': True}
    return master, accounts


class UsedChatFilterTests(unittest.TestCase):
    def test_sources_and_destinations_are_reserved(self):
        master, _ = fixture()
        self.assertEqual(routes.used_chat_keys(master['forwards'], ROWS), {'-1001', '-1009'})
        self.assertNotIn('-1002', routes.used_chat_keys(master['forwards'], ROWS))

    def test_username_and_url_aliases_resolve_to_ids(self):
        forwards = [{'source': 'https://t.me/TOPIC_SOURCE', 'dest': ['@ARCHIVE_DUMP']}]
        self.assertEqual(routes.used_chat_keys(forwards, ROWS), {'-1001', '-1009'})

    def test_disabled_routes_still_reserve_chats(self):
        master, _ = fixture()
        master['forwards'][0]['use_this'] = False
        self.assertEqual(routes.used_chat_keys(master['forwards'], ROWS), {'-1001', '-1009'})

    def test_all_routes_and_destinations_without_mutation(self):
        forwards = [{'source': -1001, 'dest': [-1009, -1002]}, {'source': '@elsewhere', 'dest': []}]
        before = copy.deepcopy(forwards)
        self.assertEqual(routes.used_chat_keys(forwards, ROWS), {'-1001', '-1009', '-1002', 'elsewhere'})
        self.assertEqual(forwards, before)


class CoverageTests(unittest.TestCase):
    def setUp(self):
        self.master, self.accounts = fixture()
        self.runtime = {'user1': {'state': 'running', 'connection_id': 'topic-route', 'updated_at': time.time()}}

    def status(self, **kwargs):
        return routes.coverage(ROWS, self.master, self.accounts, runtime=kwargs.pop('runtime', self.runtime), **kwargs)[0]['Backup status']

    def test_receiver_is_not_a_backed_up_source(self):
        rows = chat_usage(ROWS, self.master['forwards'], accounts=self.accounts, runtime=self.runtime)
        self.assertEqual(rows[0]['Backup status'], 'Backing up now')
        self.assertEqual(rows[2]['Used as'], 'receiver')
        self.assertEqual(rows[2]['Backup status'], 'Not configured')
        self.assertEqual(rows[2]['Source connections'], 0)

    def test_disabled_and_unassigned(self):
        self.master['forwards'][0]['use_this'] = False
        self.assertEqual(self.status(runtime={}), 'Disabled')
        self.master['forwards'][0]['use_this'] = True
        self.accounts['assignments'].clear()
        self.assertEqual(self.status(), 'Unassigned account')

    def test_pauses_are_not_reported_as_active_backups(self):
        self.assertEqual(self.status(users={'user1': True}), 'Account paused')
        self.assertEqual(self.status(paused={'topic-route': True}), 'Route paused')

    def test_live_scheduled_and_worker_errors(self):
        self.assertEqual(self.status(modes={'user1': {'mode': 'live'}}), 'Live forwarding')
        self.runtime['user1']['connection_id'] = 'another-route'
        self.assertEqual(self.status(), 'Scheduled')
        self.runtime['user1']['state'] = 'error'
        self.assertEqual(self.status(), 'Worker error')
        self.runtime['user1']['state'] = 'not_logged_in'
        self.assertEqual(self.status(), 'Not logged in')

    def test_stale_worker_and_missing_destination(self):
        self.runtime['user1']['updated_at'] -= 1000
        self.assertEqual(self.status(), 'Configured · worker unavailable')
        self.master['forwards'][0]['dest'] = []
        self.assertEqual(self.status(), 'No destination')

    def test_history_end_is_distinct_from_scheduled(self):
        self.master['forwards'][0]['end'] = 55
        self.runtime['user1']['connection_id'] = None
        self.assertEqual(self.status(), 'History limit reached')

    def test_usernames_urls_numeric_aliases_and_destinations(self):
        self.master['forwards'][0]['source'] = 'https://t.me/TOPIC_SOURCE/'
        self.master['forwards'][0]['dest'] = ['@ARCHIVE_DUMP']
        rows = routes.coverage(ROWS, self.master, self.accounts, self.runtime, dump={'peer': -1009})
        self.assertEqual(rows[0]['Source connections'], 1)
        self.assertEqual(rows[0]['Backup destination'], 'Dump: Shared archive')
        self.assertEqual(routes.peer_key('-001001'), '-1001')

    def test_multiple_routes_all_reported_without_mutation(self):
        before = copy.deepcopy((ROWS, self.master, self.accounts))
        self.master['forwards'].append({'connection_id': 'disabled', 'source': -1001, 'dest': [-1008], 'use_this': False})
        result = routes.coverage(ROWS, self.master, self.accounts, self.runtime)
        self.assertEqual(result[0]['Backup status'], 'Backing up now; Disabled')
        self.assertEqual(ROWS, before[0])
        self.assertEqual(self.accounts, before[2])


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.previous_cwd = Path.cwd(); os.chdir(self.root)
        self.master, self.accounts = fixture()
        atomic_json(self.root / 'tgcf.config.json', self.master)
        atomic_json(self.root / 'tgcf.accounts.json', self.accounts)
        atomic_json(self.root / '.tgcf_paused_users.json', {'user2': True})
        atomic_json(self.root / '.tgcf_offset_requests.json', {'topic-route': 77})
        self.dump = routes.save_dump(self.root, -1009, 'Shared archive', expected={})

    def tearDown(self):
        os.chdir(self.previous_cwd); self.tmp.cleanup()

    def assign(self, rows=None, account='user1', expected=None):
        return routes.assign_to_dump(self.root, account, rows or [ROWS[1]], expected or self.dump, all_rows=ROWS)

    def test_creates_bulk_routes_and_assignments_preserving_existing_state(self):
        second = {'ID': '-1003', 'Name': 'More random files', 'Username': '@more_models'}
        created = self.assign([ROWS[1], second], account='user2')
        self.assertEqual(len(created), 2)
        master = read_json(self.root / 'tgcf.config.json'); accounts = read_json(self.root / 'tgcf.accounts.json')
        self.assertEqual(master['forwards'][0], self.master['forwards'][0])
        self.assertEqual(master['future_setting'], {'keep': True})
        self.assertEqual(accounts['assignments']['topic-route'], 'user1')
        for forward in master['forwards'][1:]:
            self.assertEqual(forward['offset'], 0); self.assertEqual(forward['dest'], [-1009])
            self.assertTrue(forward['use_this']); self.assertEqual(accounts['assignments'][forward['connection_id']], 'user2')
        self.assertEqual(read_json(self.root / '.tgcf_paused_users.json'), {'user2': True})
        self.assertEqual(read_json(self.root / '.tgcf_offset_requests.json'), {'topic-route': 77})

    def test_duplicate_and_repeat_submission_are_idempotent(self):
        self.assertEqual(len(self.assign([ROWS[1], ROWS[1]])), 1)
        before = (self.root / 'tgcf.config.json').read_bytes()
        self.assertEqual(self.assign(), [])
        self.assertEqual((self.root / 'tgcf.config.json').read_bytes(), before)

    def test_existing_topic_destination_cannot_be_replaced(self):
        changed = copy.deepcopy(self.master); changed['forwards'][0]['dest'] = [-1008]
        atomic_json(self.root / 'tgcf.config.json', changed)
        with self.assertRaises(EditConflict): self.assign([ROWS[0]])
        self.assertEqual(read_json(self.root / 'tgcf.config.json'), changed)

    def test_explicit_existing_route_adds_dump_without_replacing_topic_or_offset(self):
        self.master['forwards'][0]['dest'] = [-1008]
        atomic_json(self.root / 'tgcf.config.json', self.master)
        changed = routes.assign_to_dump(self.root, 'user1', [ROWS[0]], self.dump, all_rows=ROWS, include_existing=True)
        forward = read_json(self.root / 'tgcf.config.json')['forwards'][0]
        self.assertEqual(changed, ['topic-route'])
        self.assertEqual(forward['dest'], [-1008, -1009])
        self.assertEqual(forward['offset'], 55)
        self.assertEqual(forward['connection_id'], 'topic-route')
        self.assertEqual(read_json(self.root / 'tgcf.accounts.json'), self.accounts)
        self.assertEqual(routes.assign_to_dump(self.root, 'user1', [ROWS[0]], self.dump, all_rows=ROWS, include_existing=True), [])

    def test_existing_route_cannot_be_reassigned_to_another_account(self):
        with self.assertRaises(EditConflict):
            routes.assign_to_dump(self.root, 'user2', [ROWS[0]], self.dump, all_rows=ROWS, include_existing=True)
        self.assertEqual(read_json(self.root / 'tgcf.config.json'), self.master)

    def test_disabled_route_is_not_silently_enabled_by_dump_action(self):
        self.master['forwards'][0]['use_this'] = False
        atomic_json(self.root / 'tgcf.config.json', self.master)
        with self.assertRaises(EditConflict):
            routes.assign_to_dump(self.root, 'user1', [ROWS[0]], self.dump, all_rows=ROWS, include_existing=True)
        self.assertEqual(read_json(self.root / 'tgcf.config.json'), self.master)

    def test_mixed_batch_conflict_is_atomic(self):
        changed = copy.deepcopy(self.master); changed['forwards'][0]['dest'] = [-1008]
        atomic_json(self.root / 'tgcf.config.json', changed)
        with self.assertRaises(EditConflict): self.assign([ROWS[1], ROWS[0]])
        self.assertEqual(read_json(self.root / 'tgcf.config.json'), changed)
        self.assertEqual(read_json(self.root / 'tgcf.accounts.json'), self.accounts)

    def test_stale_dump_preview_is_rejected(self):
        routes.save_dump(self.root, -1008, expected=self.dump)
        with self.assertRaises(EditConflict): self.assign()
        self.assertEqual(read_json(self.root / 'tgcf.config.json'), self.master)

    def test_save_conflict_clear_and_existing_routes_preserved(self):
        with self.assertRaises(EditConflict): routes.save_dump(self.root, -1008, expected={})
        routes.clear_dump(self.root, self.dump)
        self.assertEqual(routes.read_dump(self.root), {})
        self.assertEqual(read_json(self.root / 'tgcf.config.json'), self.master)
        with self.assertRaises(EditConflict): self.assign()

    def test_no_self_or_transitive_loop(self):
        with self.assertRaises(ValueError): self.assign([ROWS[2]])
        self.master['forwards'].append({'connection_id': 'loop', 'source': '@archive_dump', 'dest': ['@model_files']})
        atomic_json(self.root / 'tgcf.config.json', self.master)
        with self.assertRaises(ValueError): self.assign()

    def test_username_source_alias_prevents_duplicate(self):
        self.master['forwards'][0]['source'] = '@MODEL_FILES'
        self.master['forwards'][0]['dest'] = [-1008]
        atomic_json(self.root / 'tgcf.config.json', self.master)
        with self.assertRaises(EditConflict): self.assign()

    def test_invalid_account_or_missing_session(self):
        with self.assertRaises(ValueError): self.assign(account='user99')
        self.accounts['user2']['session_string'] = ''
        atomic_json(self.root / 'tgcf.accounts.json', self.accounts)
        with self.assertRaises(ValueError): self.assign(account='user2')

    def test_invalid_ids_links_and_empty_batch(self):
        for peer in ('0', '', 'https://t.me/c/123/456', 'https://evil.example/archive', '+invite-token', 'me', str(2**64)):
            with self.subTest(peer=peer), self.assertRaises(ValueError): routes.parse_peer(peer)
        with self.assertRaises(ValueError): routes.assign_to_dump(self.root, 'user1', [], self.dump)
        self.assertEqual(routes.parse_peer('https://t.me/Public_Archive/'), '@Public_Archive')

    def test_private_setting_permissions(self):
        self.assertEqual((self.root / routes.DUMP_FILE).stat().st_mode & 0o777, 0o600)

    def test_latest_worker_offset_preserved(self):
        self.master['forwards'][0]['offset'] = 999
        atomic_json(self.root / 'tgcf.config.json', self.master)
        self.assign()
        self.assertEqual(read_json(self.root / 'tgcf.config.json')['forwards'][0]['offset'], 999)

    def test_failed_config_write_never_exposes_an_unassigned_enabled_route(self):
        def fail_master(path,value):
            if Path(path).name == 'tgcf.config.json': raise OSError('Synthetic write failure')
            atomic_json(path,value)
        with patch.object(routes,'atomic_json',side_effect=fail_master), self.assertRaises(OSError):
            self.assign()
        self.assertEqual(read_json(self.root / 'tgcf.config.json'), self.master)
        accounts = read_json(self.root / 'tgcf.accounts.json')
        self.assertEqual(accounts['assignments']['topic-route'], 'user1')
        self.assertEqual(len(self.assign()), 1)
        master = read_json(self.root / 'tgcf.config.json')
        accounts = read_json(self.root / 'tgcf.accounts.json')
        self.assertTrue(all(forward['connection_id'] in accounts['assignments'] for forward in master['forwards']))

    def test_worker_selects_new_route_for_correct_account(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('worker_service_under_test', '/runner/worker_service.py')
        worker = importlib.util.module_from_spec(spec); spec.loader.exec_module(worker)
        created = self.assign(account='user2')
        master = read_json(self.root / 'tgcf.config.json'); accounts = read_json(self.root / 'tgcf.accounts.json')
        self.assertEqual([f['connection_id'] for f in worker.selected(master, accounts, 'user2', 'enabled')], created)
        self.assertEqual([f['connection_id'] for f in worker.selected(master, accounts, 'user1', 'enabled')], ['topic-route'])


if __name__ == '__main__': unittest.main()
