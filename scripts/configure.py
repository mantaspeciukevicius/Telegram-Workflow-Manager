from pathlib import Path
import secrets,json
p=Path('app/.env')
if p.exists(): raise SystemExit('app/.env already exists; refusing to overwrite')
p.write_text('PASSWORD='+secrets.token_urlsafe(24)+'\n');p.chmod(0o600)
config=Path('app/tgcf.config.json')
if not config.exists(): config.write_text(json.dumps({'login':{},'forwards':[],'admins':[],'past':{'delay':1}},indent=2))
accounts=Path('app/tgcf.accounts.json')
if not accounts.exists(): accounts.write_text(json.dumps({'assignments':{},'user1_label':'Account 1','user2':{'label':'Account 2'},'user3':{'label':'Account 3'}},indent=2))
print('Created password and empty configuration. Add authorized accounts in the UI.')
