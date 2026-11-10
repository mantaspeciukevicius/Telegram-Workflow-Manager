"""Non-interactive session checks. Never request a login code or print credentials."""
import asyncio
import hashlib
import json
import time
from pathlib import Path
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import UnauthorizedError
from tgcf.state_store import update_json, read_json


def fingerprint(login):
    fields = {k: login.get(k) for k in ('API_ID', 'API_HASH', 'SESSION_STRING', 'BOT_TOKEN', 'user_type')}
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


async def check_session(login):
    if not login.get('SESSION_STRING'):
        return 'unchecked' if login.get('BOT_TOKEN') else 'not_logged_in'
    client = None
    try:
        client = TelegramClient(StringSession(login['SESSION_STRING']), login['API_ID'], login['API_HASH'])
        async def probe():
            await client.connect()
            return await client.is_user_authorized()
        return 'authorized' if await asyncio.wait_for(probe(), timeout=15) else 'not_logged_in'
    except (UnauthorizedError, ValueError):
        return 'not_logged_in'
    except Exception:
        # A network failure is not evidence that a saved session was revoked.
        return 'verification_error'
    finally:
        if client is not None:
            try:
                await asyncio.wait_for(client.disconnect(), timeout=5)
            except Exception:
                pass


def record_check(root, account, login, status):
    entry = dict(fingerprint=fingerprint(login), status=status, checked_at=time.time())
    update_json(Path(root) / '.tgcf_session_health.json', lambda data: data.update({account: entry}), {})


def session_label(root, account, login):
    if not login.get('SESSION_STRING'):
        return 'Bot token saved · not verified' if login.get('BOT_TOKEN') else 'Login required'
    entry = read_json(Path(root) / '.tgcf_session_health.json', {}).get(account, {})
    if entry.get('fingerprint') != fingerprint(login):
        return 'Session saved · not verified'
    if entry.get('status') == 'not_logged_in':
        return 'Login required · saved session is no longer authorized'
    if entry.get('status') == 'verification_error':
        return 'Could not verify session · check connectivity and retry'
    if entry.get('status') == 'authorized' and time.time() - entry.get('checked_at', 0) < 300:
        return 'Logged in · verified with Telegram recently'
    return 'Session saved · verification needed'
