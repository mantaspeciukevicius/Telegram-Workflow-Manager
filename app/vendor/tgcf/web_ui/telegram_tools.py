from tgcf.accounts import account_ids
from tgcf.backup_routes import peer_key
from telethon import TelegramClient, types, utils
from telethon.sessions import StringSession

def account_login(master, accounts, account):
    if account == 'user1':
        return master['login']
    if account not in account_ids(accounts):
        raise ValueError('Assign a Telegram account first')
    user = accounts[account]
    primary = master['login']
    return {'API_ID': primary['API_ID'] if user.get('reuse_user1_api', True) else int(user.get('api_id') or 0),
            'API_HASH': primary['API_HASH'] if user.get('reuse_user1_api', True) else user.get('api_hash', ''),
            'SESSION_STRING': user.get('session_string', ''), 'user_type': 1}

async def query(login, source=None):
    if not login.get('SESSION_STRING'):
        raise ValueError('This tool requires a saved user session')
    client = TelegramClient(StringSession(login['SESSION_STRING']), login['API_ID'], login['API_HASH'])
    try:
        await client.connect()
        if source is not None:
            source = str(source).strip()
            peer = int(source) if source.lstrip('-').isdigit() else source
            await client.get_dialogs()
            messages = await client.get_messages(peer, limit=1)
            return messages[0].id if messages else 0
        dialogs = await client.get_dialogs()
        rows = []
        for dialog in dialogs:
            if not (dialog.is_group or dialog.is_channel):
                continue
            entity = dialog.entity
            migrated = getattr(entity, 'migrated_to', None)
            target = str(utils.get_peer_id(types.PeerChannel(migrated.channel_id))) if migrated else ''
            username = getattr(entity, 'username', None)
            rows.append({'Name': dialog.name or '(unnamed)',
                         'Type': 'Supergroup' if getattr(entity, 'megagroup', False) else ('Channel' if dialog.is_channel else 'Basic group'),
                         'ID': str(dialog.id), 'Username': '@' + username if username else '',
                         'Migration': 'Old → ' + target if target else '', '_target': target})
        by_id = {row['ID']: row for row in rows}
        for row in rows:
            target = row.pop('_target')
            if target in by_id:
                by_id[target]['Migration'] = 'Current ← ' + row['ID']
        return sorted(rows, key=lambda row: row['Name'].casefold())
    finally:
        await client.disconnect()


def chat_usage(rows, forwards, accounts=None, **status):
    result = []
    for original in rows:
        row = dict(original)
        keys = {peer_key(row['ID']), peer_key(row.get('Username', ''))} - {''}
        usage, names = [], []
        for index, forward in enumerate(forwards):
            roles = []
            if peer_key(forward.get('source', '')) in keys:
                roles.append('source')
            if any(peer_key(value) in keys for value in forward.get('dest', [])):
                roles.append('receiver')
            if roles:
                usage.extend(roles)
                names.append((forward.get('con_name') or 'Connection ' + str(index + 1)) + ' [' + ', '.join(roles) + ']')
        row.update({'Used': 'Yes' if usage else 'No', 'Used as': ', '.join(dict.fromkeys(usage)), 'Connection(s)': '; '.join(names)})
        result.append(row)
    if accounts is not None:
        from tgcf.backup_routes import coverage
        return coverage(result, {'forwards': forwards}, accounts, **status)
    return result
