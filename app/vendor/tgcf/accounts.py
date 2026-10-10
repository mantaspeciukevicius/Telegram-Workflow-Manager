"""Account enumeration shared by pages and worker services."""
import re


def account_ids(accounts):
    return sorted({'user1'} | {key for key, value in accounts.items()
                  if re.fullmatch(r'user[1-9][0-9]*', key) and isinstance(value, dict)},
                  key=lambda key: int(key[4:]))


def account_label(accounts, account):
    if account == 'user1':
        return accounts.get('user1_label') or accounts.get(account, {}).get('label') or 'User 1'
    return accounts.get(account, {}).get('label') or account
