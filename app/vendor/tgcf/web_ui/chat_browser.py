"""Joined chat coverage and opt-in shared-dump controls."""
import asyncio
from pathlib import Path
import streamlit as st
from tgcf.accounts import account_ids, account_label
from tgcf.backup_routes import read_dump, save_dump, clear_dump, assign_to_dump, peer_key, alias_map, canonical, source_connections, used_chat_keys
from tgcf.state_store import locked, read_json
from tgcf.web_ui.telegram_tools import account_login, query, chat_usage


def chat_browser(root):
    root = Path(root)
    with st.container():
        st.subheader('Chat & channel list · backup coverage')
        try:
            with locked(root):
                master = read_json(root / 'tgcf.config.json')
                accounts = read_json(root / 'tgcf.accounts.json')
                runtime = read_json(root / '.tgcf_runtime_status.json', {})
                users = read_json(root / '.tgcf_paused_users.json', {})
                paused = read_json(root / '.tgcf_paused.json', {})
                modes = read_json(root / '.tgcf_worker_modes.json', {})
                dump = read_dump(root)
        except (OSError, ValueError):
            st.error('Cannot read backup status safely. Repair the configuration before saving.'); return
        account = st.selectbox('Telegram account', account_ids(accounts),
                              format_func=lambda key: account_label(accounts, key), key='chat_browser_account')
        cache_key = 'joined_channels_' + account
        if st.button('Load joined chats / channels'):
            try:
                st.session_state[cache_key] = asyncio.run(query(account_login(master, accounts, account)))
            except Exception:
                st.session_state.pop(cache_key, None)
                st.error('Could not load channels. Check this account’s saved session and connectivity.')
        joined = st.session_state.get(cache_key, [])
        joined_by_id = {row['ID']: row for row in joined}
        with st.expander('Shared dump chat', expanded=not bool(dump)):
            st.caption('Choose one destination for miscellaneous chats. Existing topic connections stay unchanged. Every assigned account needs permission to post to this chat.')
            if dump:
                st.info('Current dump: ' + dump.get('label', str(dump['peer'])) + ' · ' + str(dump['peer']))
            with st.form('shared_dump_settings'):
                entered = st.text_input('Dump chat ID or @username', value=str(dump.get('peer', '')),
                                        help='Paste an ID/username, or choose a joined chat below.')
                chosen = st.selectbox('Or choose a joined chat as the dump', [''] + list(joined_by_id),
                                     format_func=lambda key: 'Use the ID / username field above' if not key else joined_by_id[key]['Name'] + ' · ' + key)
                label = st.text_input('Friendly dump name (optional)', value=dump.get('label', ''))
                save = st.form_submit_button('Save dump chat')
            if save:
                try:
                    save_dump(root, chosen or entered, joined_by_id[chosen]['Name'] if chosen else label, expected=dump)
                    st.session_state['route_notice'] = 'Shared dump chat saved. Existing routes were not changed.'
                    st.experimental_rerun()
                except ValueError as exc: st.error(str(exc))
                except OSError: st.error('Could not save the dump setting. Check file permissions and reload.')
            if dump and st.button('Clear dump setting'):
                try:
                    clear_dump(root, dump)
                    st.session_state['route_notice'] = 'Dump default cleared; existing backup connections remain in place.'
                    st.experimental_rerun()
                except ValueError as exc: st.error(str(exc))
                except OSError: st.error('Could not clear the dump setting. Reload and try again.')
        if 'route_notice' in st.session_state:
            st.success(st.session_state.pop('route_notice'))
        if joined:
            rows = chat_usage(joined, master.get('forwards', []), accounts=accounts,
                              runtime=runtime, users=users, paused=paused, modes=modes, dump=dump)
            configured = sum(row['Source connections'] > 0 for row in rows)
            left, right = st.columns(2)
            left.metric('Chats with a source backup route', configured)
            right.metric('Chats without a source backup route', len(rows) - configured)
            search = st.text_input('Search chats by name, ID, username or destination').strip().casefold()
            view = st.selectbox('Backup filter', ['All chats', 'No source backup route', 'Configured source routes', 'Paused, disabled or needs attention'])
            filtered = [row for row in rows if not search or any(search in str(value).casefold() for value in row.values())]
            if view == 'No source backup route': filtered = [row for row in filtered if not row['Source connections']]
            elif view == 'Configured source routes': filtered = [row for row in filtered if row['Source connections']]
            elif view == 'Paused, disabled or needs attention':
                filtered = [row for row in filtered if any(word in row['Backup status'].casefold() for word in ('paused', 'disabled', 'unassigned', 'unavailable', 'error', 'not logged', 'no destination', 'limit'))]
            st.caption('Showing ' + str(len(filtered)) + ' of ' + str(len(rows)) + ' chats. A receiver alone is not a backed-up source. Scheduled means configured for the worker; it does not prove all history has been copied.')
            if filtered:
                columns = ('Name', 'Backup status', 'Backup destination', 'Backup account', 'Used as', 'Type', 'ID', 'Username')
                display = [{key: row.get(key, '') for key in columns} for row in filtered]
                if any(row.get('Migration') for row in filtered):
                    for shown, row in zip(display, filtered): shown['Migration'] = row.get('Migration', '')
                st.dataframe(display, use_container_width=True)
            else: st.info('No chats match this filter.')
            st.subheader('Quick backup to dump')
            hide_used = st.checkbox('Hide chats used as sources or destinations', value=True)
            include_existing = st.checkbox('Also include my existing backups · keep their current destinations')
            if include_existing and hide_used:
                st.caption('Uncheck the filter above to select existing backups.')
            if include_existing:
                st.caption('For existing routes, the dump receives messages from the saved offset on the next worker cycle. Earlier messages stay in their existing topic chats; offsets are not reset.')
            aliases = alias_map(joined)
            dump_key = canonical(dump.get('peer', ''), aliases)
            used_keys = used_chat_keys(master.get('forwards', []), joined)
            candidates = {}
            for row in filtered:
                if canonical(row['ID'], aliases) == dump_key: continue
                if hide_used and canonical(row['ID'], aliases) in used_keys: continue
                existing = source_connections(row, master.get('forwards', []))
                eligible = not existing
                if include_existing and len(existing) == 1:
                    forward = existing[0]
                    eligible = (accounts.get('assignments', {}).get(forward['connection_id']) == account
                                and forward.get('use_this', True)
                                and dump_key not in [canonical(d, aliases) for d in forward.get('dest', [])])
                if eligible: candidates[row['ID']] = row
            if not dump: st.info('Set your shared dump chat above first.')
            elif not candidates: st.info('No eligible chats match these filters. Uncheck the used-chat filter to include destinations, or edit existing routes below.')
            else:
                with st.form('quick_dump_assignments'):
                    sources = st.multiselect('Chats / channels to back up', list(candidates),
                        format_func=lambda key: candidates[key]['Name'] + ' · ' + key)
                    st.caption('Destination: ' + dump.get('label', str(dump['peer'])) + ' · Account: ' + account_label(accounts, account))
                    st.caption('New sources get enabled history-backup connections from message zero. Existing routes keep their destinations, offsets and pauses. The worker applies changes on its next cycle; LIVE follows new messages.')
                    submit = st.form_submit_button('Back up selected chats to dump')
                if submit:
                    try:
                        created = assign_to_dump(root, account, [candidates[key] for key in sources], dump, all_rows=joined, include_existing=include_existing)
                        st.session_state['route_notice'] = str(len(created)) + ' dump backup connection(s) configured and assigned. Check Backup Control for progress; resume the account there if it is paused.'
                        st.experimental_rerun()
                    except ValueError as exc: st.error(str(exc))
                    except OSError: st.error('Could not save the connections. Reload the list before retrying.')
        else:
            st.info('Load your joined chats to see backup coverage and select sources without copying IDs.')
        st.caption('Use the editor below for existing topic routes, custom destinations and advanced offset controls.')
