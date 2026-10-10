import asyncio
import copy
from pathlib import Path
import streamlit as st
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError
from tgcf.accounts import account_ids, account_label
from tgcf.state_store import locked, read_json, atomic_json, merge_changes
from tgcf.web_ui.password import check_password
async def send_login_code(
    api_id,
    api_hash,
    phone,
):

    client = TelegramClient(
        StringSession(),
        api_id,
        api_hash,
    )

    await client.connect()

    try:

        sent = (
            await client
            .send_code_request(
                phone
            )
        )

        session = (
            StringSession.save(
                client.session
            )
        )

        return (
            session,
            sent.phone_code_hash,
        )

    finally:

        await client.disconnect()

async def login_with_code(
    api_id,
    api_hash,
    phone,
    session_string,
    phone_code_hash,
    code,
):

    client = TelegramClient(
        StringSession(
            session_string
        ),
        api_id,
        api_hash,
    )

    await client.connect()

    try:

        try:

            await client.sign_in(
                phone=phone,
                code=code,
                phone_code_hash=
                    phone_code_hash,
            )

        except SessionPasswordNeededError:

            return {
                "needs_password": True,
                "temp_session":
                    StringSession.save(
                        client.session
                    ),
            }


        me = await client.get_me()

        name = " ".join(
            x
            for x in [
                getattr(
                    me,
                    "first_name",
                    "",
                ),
                getattr(
                    me,
                    "last_name",
                    "",
                ),
            ]
            if x
        ).strip()

        return {
            "needs_password": False,
            "session":
                StringSession.save(
                    client.session
                ),
            "name":
                name or str(me.id),
            "user_id":
                me.id,
        }

    finally:

        await client.disconnect()

async def login_with_password(
    api_id,
    api_hash,
    session_string,
    password,
):

    client = TelegramClient(
        StringSession(
            session_string
        ),
        api_id,
        api_hash,
    )

    await client.connect()

    try:

        await client.sign_in(
            password=password
        )

        me = await client.get_me()

        name = " ".join(
            x
            for x in [
                getattr(
                    me,
                    "first_name",
                    "",
                ),
                getattr(
                    me,
                    "last_name",
                    "",
                ),
            ]
            if x
        ).strip()

        return {
            "session":
                StringSession.save(
                    client.session
                ),
            "name":
                name or str(me.id),
            "user_id":
                me.id,
        }

    finally:

        await client.disconnect()

st.set_page_config(page_title='Telegram accounts')
if not check_password(st):
    st.stop()
st.title('Telegram accounts')
ROOT = Path.cwd()
ACCOUNTS = ROOT / 'tgcf.accounts.json'
with locked(ROOT):
    accounts = read_json(ACCOUNTS)
    master = read_json(ROOT / 'tgcf.config.json')

def save_account(account, before, draft):
    with locked(ROOT):
        latest = read_json(ACCOUNTS)
        latest[account] = merge_changes(before, draft, latest.get(account, {}))
        atomic_json(ACCOUNTS, latest)

st.subheader(account_label(accounts, 'user1') + ' · User 1 (primary)')
st.caption('Logged in' if master['login'].get('SESSION_STRING') else 'No saved user session')
with st.form('primary_account_label'):
    primary_label = st.text_input('User 1 display name', value=account_label(accounts, 'user1'))
    save_primary_label = st.form_submit_button('Save User 1 name')
if save_primary_label:
    with locked(ROOT):
        latest = read_json(ACCOUNTS)
        latest['user1_label'] = primary_label.strip() or 'User 1'
        atomic_json(ACCOUNTS, latest)
    st.experimental_rerun()
st.caption('Primary API credentials and session are editable on the Telegram Login page.')

with st.form('add_account'):
    new_label = st.text_input('New account display name')
    add_account = st.form_submit_button('Add Telegram account')
if add_account:
    with locked(ROOT):
        latest = read_json(ACCOUNTS)
        account = 'user' + str(max(int(key[4:]) for key in account_ids(latest)) + 1)
        latest[account] = {'label': new_label.strip() or account, 'reuse_user1_api': True, 'session_string': ''}
        atomic_json(ACCOUNTS, latest)
    st.experimental_rerun()
st.caption('New accounts get a worker automatically. Log in, then assign their connections below.')

for account in account_ids(accounts):
    if account == 'user1':
        continue
    before = copy.deepcopy(accounts.get(account, {}))
    draft = copy.deepcopy(before)
    st.subheader(before.get('label', account))
    st.caption('Logged in' if before.get('session_string') else 'Not logged in')
    with st.form('account_' + account):
        draft['label'] = st.text_input('Label', value=before.get('label', account))
        draft['reuse_user1_api'] = st.checkbox('Reuse primary API credentials', value=before.get('reuse_user1_api', True))
        draft['api_id'] = int(st.number_input('API ID', min_value=0, value=int(before.get('api_id') or 0)))
        draft['api_hash'] = st.text_input('API hash', value=before.get('api_hash', ''), type='password')
        draft['phone'] = st.text_input('Phone number', value=before.get('phone', ''))
        saved = st.form_submit_button('Save account settings')
    if saved:
        if not draft['reuse_user1_api'] and (not draft['api_id'] or not draft['api_hash']):
            st.error('Supply an API ID and hash, or reuse the primary credentials.')
        else:
            try:
                save_account(account, before, draft)
                st.success('Saved')
            except ValueError:
                st.error('Account changed elsewhere. Reload before saving.')
    api_id = master['login']['API_ID'] if before.get('reuse_user1_api', True) else int(before.get('api_id') or 0)
    api_hash = master['login']['API_HASH'] if before.get('reuse_user1_api', True) else before.get('api_hash', '')
    pending_key = 'login_pending_' + account
    if st.button('Send login code', key='send_' + account):
        try:
            if not api_id or not api_hash or not before.get('phone'):
                raise ValueError('Save API credentials and a phone number first.')
            session, code_hash = asyncio.run(send_login_code(api_id, api_hash, before['phone']))
            st.session_state[pending_key] = {'session': session, 'hash': code_hash, 'before': before, 'api_id': api_id, 'api_hash': api_hash, 'phone': before['phone']}
            st.success('Code sent')
        except Exception:
            st.error('Could not send login code. Check saved settings and Telegram connectivity.')
    pending = st.session_state.get(pending_key)
    if pending:
        with st.form('verify_' + account):
            value = st.text_input('Two-factor password' if pending.get('needs_password') else 'Telegram login code', type='password')
            verify = st.form_submit_button('Complete login')
        if verify:
            try:
                if pending.get('needs_password'):
                    result = asyncio.run(login_with_password(pending['api_id'], pending['api_hash'], pending['session'], value))
                else:
                    result = asyncio.run(login_with_code(pending['api_id'], pending['api_hash'], pending['phone'], pending['session'], pending['hash'], value))
                if result.get('needs_password'):
                    pending.update(needs_password=True, session=result['temp_session'])
                    st.experimental_rerun()
                else:
                    login_draft = copy.deepcopy(pending['before'])
                    login_draft.update(session_string=result['session'], name=result['name'], user_id=result['user_id'])
                    save_account(account, pending['before'], login_draft)
                    del st.session_state[pending_key]
                    st.success('Logged in')
            except Exception:
                st.error('Login failed or account settings changed. Retry or reload.')
    confirm = st.checkbox('Confirm removing saved session', key='logout_confirm_' + account)
    if st.button('Remove session', key='logout_' + account, disabled=not confirm):
        draft = copy.deepcopy(before)
        draft['session_string'] = ''
        save_account(account, before, draft)
        st.session_state.pop(pending_key, None)
        st.experimental_rerun()

st.subheader('Connection assignments')
before_assignments = copy.deepcopy(accounts.get('assignments', {}))
draft_assignments = copy.deepcopy(before_assignments)
with st.form('assignments'):
    for index, forward in enumerate(master.get('forwards', [])):
        key = forward['connection_id']
        choices = ['unassigned'] + account_ids(accounts)
        current = before_assignments.get(key, 'unassigned')
        draft_assignments[key] = st.selectbox(forward.get('con_name') or 'Connection ' + str(index + 1), choices, index=choices.index(current) if current in choices else 0, key='assign_' + key, format_func=lambda value: account_label(accounts, value))
    save = st.form_submit_button('Save assignments')
if save:
    try:
        with locked(ROOT):
            latest = read_json(ACCOUNTS)
            runtime = read_json(ROOT / '.tgcf_runtime_status.json', {})
            for identity, owner in draft_assignments.items():
                if owner != before_assignments.get(identity):
                    if any((item.get('connection_id') == identity or identity in item.get('connection_ids', [])) and item.get('state') in ('running', 'pausing', 'user_pausing', 'connection_pausing') for item in runtime.values()):
                        raise ValueError('Pause the connection and wait for acknowledgement before moving it')
            latest['assignments'] = merge_changes(before_assignments, draft_assignments, latest.get('assignments', {}))
            atomic_json(ACCOUNTS, latest)
        st.success('Saved; assignments apply when the next connection starts.')
    except ValueError:
        st.error('Pause active connections and wait for acknowledgement before moving them. If assignments changed elsewhere, reload before saving.')
