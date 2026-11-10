"""Synthetic session checks; no live Telegram connection."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from tgcf.session_health import check_session, record_check, session_label

LOGIN = {'API_ID': 1, 'API_HASH': 'synthetic', 'SESSION_STRING': 'synthetic'}


class HealthTests(unittest.IsolatedAsyncioTestCase):
    async def probe(self, authorized=True, error=None):
        client = AsyncMock()
        client.is_user_authorized.return_value = authorized
        if error:
            client.connect.side_effect = error
        with patch('tgcf.session_health.StringSession'), patch('tgcf.session_health.TelegramClient', return_value=client):
            status = await check_session(LOGIN)
        client.disconnect.assert_awaited_once()
        client.start.assert_not_called()
        return status

    async def test_authorized(self):
        self.assertEqual(await self.probe(), 'authorized')

    async def test_revoked(self):
        self.assertEqual(await self.probe(False), 'not_logged_in')

    async def test_network_failure_is_not_revocation(self):
        self.assertEqual(await self.probe(error=OSError()), 'verification_error')

    async def test_malformed_session(self):
        with patch('tgcf.session_health.StringSession', side_effect=ValueError()):
            self.assertEqual(await check_session(LOGIN), 'not_logged_in')

    async def test_missing_session_does_not_connect(self):
        with patch('tgcf.session_health.TelegramClient') as factory:
            self.assertEqual(await check_session({}), 'not_logged_in')
            factory.assert_not_called()

    async def test_bot_is_not_falsely_claimed_verified(self):
        self.assertEqual(await check_session({'BOT_TOKEN': 'synthetic'}), 'unchecked')

    async def test_labels_invalidate_when_credentials_change(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(session_label(root, 'user1', LOGIN), 'Session saved · not verified')
            record_check(root, 'user1', LOGIN, 'authorized')
            self.assertIn('Logged in', session_label(root, 'user1', LOGIN))
            self.assertNotIn('Logged in', session_label(root, 'user1', dict(LOGIN, SESSION_STRING='new')))
            record_check(root, 'user1', LOGIN, 'not_logged_in')
            self.assertIn('Login required', session_label(root, 'user1', LOGIN))
            self.assertNotIn('synthetic', (Path(root) / '.tgcf_session_health.json').read_text())
            record_check(root, 'user1', LOGIN, 'authorized')
            with patch('tgcf.session_health.time.time', return_value=10**12):
                self.assertNotIn('Logged in', session_label(root, 'user1', LOGIN))


class WorkerHealthTests(unittest.TestCase):
    def test_revoked_session_prevents_job_start(self):
        import importlib.util
        from tgcf.state_store import atomic_json
        path = Path(__file__).resolve().parents[1] / 'app' / 'worker_service.py'
        spec = importlib.util.spec_from_file_location('session_health_worker', path)
        worker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(worker)
        with tempfile.TemporaryDirectory() as root:
            atomic_json(Path(root) / 'tgcf.config.json', {'login': LOGIN, 'forwards': []})
            atomic_json(Path(root) / 'tgcf.accounts.json', {})
            worker.ROOT = Path(root)
            worker.STOP = False
            def finish(_):
                worker.STOP = True
            with patch.object(worker, 'check_session', AsyncMock(return_value='not_logged_in')), patch.object(worker.subprocess, 'Popen') as spawn, patch.object(worker, 'publish') as publish, patch.object(worker.time, 'sleep', finish):
                worker.account_service('user1')
                spawn.assert_not_called()
                self.assertTrue(any(c.kwargs.get('state') == 'not_logged_in' for c in publish.call_args_list))
