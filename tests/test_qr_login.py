"""Offline QR login and session persistence tests."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from telethon.errors import SessionPasswordNeededError
from tgcf.qr_login import QRLoginJob, save_qr_session
from tgcf.state_store import atomic_json, read_json, EditConflict

LOGIN = {'API_ID': 1, 'API_HASH': 'synthetic', 'SESSION_STRING': 'old', 'user_type': 1}


def fixture():
    qr = SimpleNamespace(url='tg://login?token=synthetic', wait=AsyncMock())
    client = SimpleNamespace(connect=AsyncMock(), disconnect=AsyncMock(), qr_login=AsyncMock(return_value=qr),
                             get_me=AsyncMock(return_value=SimpleNamespace(id=123, username='test', first_name='Test')),
                             sign_in=AsyncMock(), session=SimpleNamespace(save=lambda: 'new-session'))
    return QRLoginJob(LOGIN, client_factory=lambda *args: client), client, qr


class QRTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_exposes_identity_but_not_session(self):
        job, client, qr = fixture()
        await job._run()
        self.assertEqual(job.snapshot()['status'], 'ready')
        self.assertEqual(job.snapshot()['user_id'], 123)
        self.assertNotIn('new-session', str(job.snapshot()))
        self.assertEqual(job.session, 'new-session')
        client.disconnect.assert_awaited_once()

    async def test_expiration(self):
        job, client, qr = fixture()
        qr.wait.side_effect = asyncio.TimeoutError
        await job._run()
        self.assertEqual(job.snapshot()['status'], 'expired')
        self.assertIsNone(job.session)

    async def test_two_factor_keeps_client_and_accepts_password(self):
        job, client, qr = fixture()
        qr.wait.side_effect = SessionPasswordNeededError(None)
        task = asyncio.create_task(job._run())
        for _ in range(50):
            await asyncio.sleep(.001)
            if job.snapshot()['status'] == 'password':
                break
        self.assertEqual(job.snapshot()['status'], 'password')
        job.password('synthetic-password')
        await task
        self.assertEqual(job.snapshot()['status'], 'ready')
        client.sign_in.assert_awaited_once_with(password='synthetic-password')
        self.assertNotIn('synthetic-password', str(job.snapshot()))

    async def test_cancel_before_start_never_connects(self):
        job, client, _ = fixture()
        job.cancel()
        await job._run()
        client.connect.assert_not_awaited()
        self.assertIsNone(job.session)

    async def test_failure_is_generic_and_disconnects(self):
        job, client, _ = fixture()
        client.connect.side_effect = ValueError('private credentials')
        await job._run()
        self.assertEqual(job.snapshot()['status'], 'error')
        self.assertNotIn('private credentials', str(job.snapshot()))
        client.disconnect.assert_awaited_once()


class SaveTests(unittest.TestCase):
    def setup_files(self, root):
        atomic_json(root / 'tgcf.config.json', {'login': LOGIN, 'forwards': [{'offset': 42}]})
        atomic_json(root / 'tgcf.accounts.json', {'user2': {'reuse_user1_api': True, 'session_string': 'old', 'user_id': 123}, 'assignments': {'route': 'user2'}})
        job, _, _ = fixture()
        job.session = 'new-session'
        job.update(status='ready', user_id=123, name='Test')
        return job

    def test_primary_save_preserves_offsets_and_clears_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = self.setup_files(root)
            save_qr_session(root, 'user1', job)
            data = read_json(root / 'tgcf.config.json')
            self.assertEqual(data['login']['SESSION_STRING'], 'new-session')
            self.assertEqual(data['forwards'][0]['offset'], 42)
            self.assertIsNone(job.session)

    def test_secondary_save_preserves_assignments(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = self.setup_files(root)
            # account_login for secondary accounts omits BOT_TOKEN, matching fingerprint.
            from tgcf.web_ui.telegram_tools import account_login
            job.login = account_login(read_json(root/'tgcf.config.json'), read_json(root/'tgcf.accounts.json'), 'user2')
            save_qr_session(root, 'user2', job)
            data = read_json(root/'tgcf.accounts.json')
            self.assertEqual(data['user2']['session_string'], 'new-session')
            self.assertEqual(data['assignments'], {'route': 'user2'})

    def test_wrong_account_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = self.setup_files(root)
            from tgcf.web_ui.telegram_tools import account_login
            job.login = account_login(read_json(root/'tgcf.config.json'), read_json(root/'tgcf.accounts.json'), 'user2')
            job.update(user_id=999)
            with self.assertRaisesRegex(ValueError, 'Wrong Telegram account'):
                save_qr_session(root, 'user2', job)
            self.assertEqual(read_json(root/'tgcf.accounts.json')['user2']['session_string'], 'old')

    def test_concurrent_credentials_change_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = self.setup_files(root)
            master = read_json(root/'tgcf.config.json')
            master['login']['SESSION_STRING'] = 'changed-elsewhere'
            atomic_json(root/'tgcf.config.json', master)
            with self.assertRaises(EditConflict):
                save_qr_session(root, 'user1', job)
