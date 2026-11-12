"""Bounded QR-login jobs, owned by a single authenticated browser session."""
import asyncio
import threading
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError, PasswordHashInvalidError
from tgcf.session_health import fingerprint, record_check
from tgcf.state_store import locked, read_json, atomic_json, EditConflict
from tgcf.web_ui.telegram_tools import account_login


class QRLoginJob:
    def __init__(self, login, client_factory=TelegramClient):
        self.login = dict(login)
        self.factory = client_factory
        self.lock = threading.Lock()
        self.state = {'status': 'starting'}
        self.session = None
        self.loop = None
        self.task = None
        self.passwords = None
        self.cancelled = threading.Event()

    def start(self):
        threading.Thread(target=self._thread, daemon=True).start()
        return self

    def update(self, **values):
        with self.lock:
            self.state.update(values)

    def snapshot(self):
        with self.lock:
            return dict(self.state)

    def cancel(self):
        self.cancelled.set()
        if self.loop and self.task and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self.task.cancel)
        self.session = None

    def password(self, value):
        if self.loop and self.passwords and not self.loop.is_closed():
            asyncio.run_coroutine_threadsafe(self.passwords.put(value), self.loop)
            self.update(status='checking_password')

    def _thread(self):
        asyncio.run(self._run())

    async def _run(self):
        self.loop = asyncio.get_running_loop()
        self.task = asyncio.current_task()
        self.passwords = asyncio.Queue()
        client = None
        waiter = None
        try:
            if self.cancelled.is_set():
                return
            client = self.factory(StringSession(), self.login['API_ID'], self.login['API_HASH'])
            await asyncio.wait_for(client.connect(), 15)
            qr = await asyncio.wait_for(client.qr_login(), 15)
            waiter = asyncio.create_task(qr.wait(timeout=60))
            # Register Telegram's login-token handler before exposing the QR.
            await asyncio.sleep(0)
            self.update(status='scan', url=qr.url)
            try:
                await waiter
            except SessionPasswordNeededError:
                self.update(status='password', url=None)
                for attempt in range(3):
                    password = await asyncio.wait_for(self.passwords.get(), 120)
                    try:
                        await asyncio.wait_for(client.sign_in(password=password), 20)
                        break
                    except PasswordHashInvalidError:
                        self.update(status='password', message='Incorrect password. Try again.')
                    finally:
                        password = None
                else:
                    raise ValueError('Too many password attempts')
            me = await asyncio.wait_for(client.get_me(), 15)
            if not me:
                raise ValueError('Authorization incomplete')
            self.session = client.session.save()
            self.update(status='ready', url=None, user_id=me.id, username=getattr(me, 'username', None), name=getattr(me, 'first_name', '') or '')
        except asyncio.TimeoutError:
            self.update(status='expired', url=None)
        except asyncio.CancelledError:
            self.update(status='cancelled', url=None)
        except Exception:
            self.update(status='error', url=None, message='Telegram login failed. Generate a new QR and try again.')
        finally:
            if waiter and not waiter.done():
                waiter.cancel()
                await asyncio.gather(waiter, return_exceptions=True)
            if client:
                try:
                    await asyncio.wait_for(client.disconnect(), 5)
                except Exception:
                    pass


def save_qr_session(root, account, job):
    result = job.snapshot()
    if result['status'] != 'ready' or not job.session:
        raise ValueError('Login is not complete')
    with locked(root):
        master = read_json(root / 'tgcf.config.json')
        accounts = read_json(root / 'tgcf.accounts.json')
        current = account_login(master, accounts, account)
        if fingerprint(current) != fingerprint(job.login):
            raise EditConflict('Account settings changed during login. Start again.')
        expected = accounts.get(account, {}).get('user_id')
        if expected and int(expected) != result['user_id']:
            raise ValueError('Wrong Telegram account. Switch accounts on your phone and scan a new QR.')
        if account == 'user1':
            master['login']['SESSION_STRING'] = job.session
            atomic_json(root / 'tgcf.config.json', master)
        else:
            accounts[account].update(session_string=job.session, user_id=result['user_id'], name=result['name'])
            atomic_json(root / 'tgcf.accounts.json', accounts)
        record_check(root, account, account_login(master, accounts, account), 'authorized')
    job.session = None
    job.update(status='saved')
