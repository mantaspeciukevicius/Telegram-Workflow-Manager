from tgcf.accounts import account_ids, account_label
from tgcf.web_ui.chat_browser import chat_browser
import asyncio
from pathlib import Path
from tgcf.web_ui.telegram_tools import account_login, query, chat_usage
import streamlit as st
from tgcf.config import Forward, read_config, write_config
from tgcf.state_store import locked, read_json, atomic_json
from tgcf.web_ui.password import check_password
from tgcf.web_ui.utils import get_list, get_string

st.set_page_config(page_title='Connections', layout='wide')
if not check_password(st):
    st.stop()
st.title('Connections')
ROOT = Path.cwd()
try:
    config = read_config()
except (ValueError, OSError):
    st.error('Cannot read configuration safely. Repair it before saving.')
    st.stop()
chat_browser(ROOT)

if st.button('Add connection'):
    config.forwards.append(Forward())
    write_config(config)
    st.experimental_rerun()
for index, forward in enumerate(config.forwards):
    key = forward.connection_id
    with st.expander(forward.con_name or 'Connection ' + str(index + 1)):
        with st.form('edit_' + key):
            forward.con_name = st.text_input('Name', value=forward.con_name)
            forward.use_this = st.checkbox('Enabled', value=forward.use_this)
            forward.source = st.text_input('Source ID or username', value=str(forward.source)).strip()
            forward.dest = get_list(st.text_area('Destination IDs or usernames, one per line', value=get_string(forward.dest)))
            forward.end = int(st.number_input('End message ID (0 means no limit)', min_value=0, value=int(forward.end or 0)))
            submitted = st.form_submit_button('Save connection')
        st.caption('Current offset: ' + str(forward.offset))
        with locked(ROOT):
            pending = read_json(ROOT / '.tgcf_offset_requests.json', {})
        if key in pending:
            st.info('Offset change queued for next start: ' + str(pending[key]))
        if submitted:
            if not forward.source or not forward.dest:
                st.error('Supply a source and at least one destination.')
            elif forward.end and forward.end < forward.offset:
                st.error('End must be zero or at least the current offset.')
            else:
                try:
                    write_config(config)
                    st.success('Saved')
                except ValueError:
                    st.error('Configuration changed elsewhere. Reload before saving.')
        with st.form('offset_' + key):
            offset = int(st.number_input('Requested offset', min_value=0, value=int(forward.offset)))
            confirm = st.checkbox('I understand changing the offset can repeat or skip messages')
            request = st.form_submit_button('Queue offset change')
        if request:
            if not confirm:
                st.error('Confirm the offset change first.')
            else:
                with locked(ROOT):
                    latest = read_json(ROOT / 'tgcf.config.json')
                    if not any(f.get('connection_id') == key for f in latest.get('forwards', [])):
                        st.error('Connection was deleted. Reload the page.')
                    else:
                        pending = read_json(ROOT / '.tgcf_offset_requests.json', {})
                        pending[key] = offset
                        atomic_json(ROOT / '.tgcf_offset_requests.json', pending)
                        st.success('Queued for the worker at its next connection start.')
        if st.button('Show chats for assigned account', key='chats_' + key):
            try:
                with locked(ROOT):
                    live_master = read_json(ROOT / 'tgcf.config.json')
                    accounts = read_json(ROOT / 'tgcf.accounts.json')
                owner = accounts.get('assignments', {}).get(key, 'unassigned')
                st.table(asyncio.run(query(account_login(live_master, accounts, owner))))
            except Exception:
                st.error('Could not load chats. Check account assignment, session and connectivity.')
        skip_confirm = st.checkbox('Confirm skipping existing source messages', key='latest_confirm_' + key)
        if st.button('Queue start from latest message', key='latest_' + key, disabled=not skip_confirm):
            try:
                with locked(ROOT):
                    live_master = read_json(ROOT / 'tgcf.config.json')
                    accounts = read_json(ROOT / 'tgcf.accounts.json')
                    live_forward = next(f for f in live_master['forwards'] if f['connection_id'] == key)
                owner = accounts.get('assignments', {}).get(key, 'unassigned')
                latest = asyncio.run(query(account_login(live_master, accounts, owner), live_forward['source']))
                with locked(ROOT):
                    current_master = read_json(ROOT / 'tgcf.config.json')
                    current_forward = next(f for f in current_master['forwards'] if f['connection_id'] == key)
                    current_accounts = read_json(ROOT / 'tgcf.accounts.json')
                    if current_forward['source'] != live_forward['source'] or current_accounts.get('assignments', {}).get(key) != owner:
                        raise ValueError('Connection changed during lookup')
                    pending = read_json(ROOT / '.tgcf_offset_requests.json', {})
                    pending[key] = latest
                    atomic_json(ROOT / '.tgcf_offset_requests.json', pending)
                st.success('Queued latest offset: ' + str(latest))
            except Exception:
                st.error('Could not queue latest offset. Check assignment, source and session, then reload.')
        delete = st.checkbox('Confirm deletion', key='delete_confirm_' + key)
        if st.button('Delete connection', key='delete_' + key, disabled=not delete):
            config.forwards = [f for f in config.forwards if f.connection_id != key]
            try:
                write_config(config)
                st.experimental_rerun()
            except ValueError:
                st.error('Configuration changed elsewhere. Reload before deleting.')
