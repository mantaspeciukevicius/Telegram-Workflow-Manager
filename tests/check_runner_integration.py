import json,os,subprocess,tempfile
from pathlib import Path
with tempfile.TemporaryDirectory() as name:
 root=Path(name)
 forwards=[{'connection_id':k,'source':-(100+i),'dest':[-999],'con_name':label,'use_this':True,'offset':17+i} for i,(k,label) in enumerate([('topic-a','Topic A'),('dump-a','Dump · Files'),('topic-b','Topic B')])]
 master={'login':{'API_ID':100,'API_HASH':'offline','SESSION_STRING':'synthetic-only'},'forwards':forwards,'keep':'preserved'}
 (root/'tgcf.config.json').write_text(json.dumps(master));(root/'tgcf.accounts.json').write_text(json.dumps({'assignments':{f['connection_id']:'user1' for f in forwards}}))
 (root/'run_tgcf.py').write_text("import json,time\nfrom pathlib import Path\np=Path('tgcf.config.json');s=json.loads(p.read_text());s['forwards'][0]['offset']+=1;p.write_text(json.dumps(s));time.sleep(.04)\n")
 source=Path('/actual-runner.py').read_text().replace('ROOT = Path("/opt/my-tgcf")','ROOT = Path('+repr(name)+')').replace('SYNC_INTERVAL = 2','SYNC_INTERVAL = .02')
 (root/'runner.py').write_text(source)
 result=subprocess.run(['python',str(root/'runner.py'),'user1'],env=dict(os.environ,TGCF_JOB=json.dumps({'mode':'once','scope':'enabled'})),capture_output=True,text=True,timeout=20)
 if result.returncode: print(result.stdout,result.stderr);raise SystemExit(1)
 after=json.loads((root/'tgcf.config.json').read_text());assert after['keep']=='preserved';assert all(a['offset']==b['offset']+1 for a,b in zip(after['forwards'],forwards))
 state=json.loads((root/'.tgcf_schedule_user1.json').read_text());assert set(state['last'])=={'normal','dump'} and 'pending' not in state
 print('PASS actual runner: three synthetic routes; weighted queues; offsets saved; configuration retained; no Telegram calls.')
