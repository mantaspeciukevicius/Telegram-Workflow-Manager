import importlib.util,sys,types,unittest
from pathlib import Path
from unittest.mock import patch
config=types.ModuleType('tgcf.config');config.PASSWORD='test-only-password';
spec=importlib.util.spec_from_file_location('login_under_test',str(Path(__file__).resolve().parents[1] / 'app/vendor/tgcf/web_ui/password.py'));login=importlib.util.module_from_spec(spec);
with patch.dict(sys.modules, {'tgcf.config': config}):
 spec.loader.exec_module(login)
class Sidebar:
 def button(self,*a,**k):return False
class Browser:
 def __init__(self,password):self.session_state={'password':password};self.sidebar=Sidebar();self.warning_seen=False
 def text_input(self,*a,**k):k['on_change']()
 def warning(self,*a):self.warning_seen=True
 def error(self,*a):pass
class TestLogin(unittest.TestCase):
 def setUp(self):login._attempts.clear()
 def test_reconnect_does_not_reset_limit(self):
  for _ in range(5):self.assertFalse(login.check_password(Browser('wrong')))
  browser=Browser('test-only-password');self.assertFalse(login.check_password(browser));self.assertTrue(browser.warning_seen)
 def test_limit_expires(self):
  with patch.object(login.time,'monotonic',return_value=100):
   for _ in range(5):login.check_password(Browser('wrong'))
  with patch.object(login.time,'monotonic',return_value=161):self.assertTrue(login.check_password(Browser('test-only-password')))
 def test_default_password_rejected(self):
  with patch.object(login,'PASSWORD','tgcf'):self.assertFalse(login.check_password(Browser('tgcf')))
 def test_existing_authenticated_session_survives_throttle(self):
  for _ in range(5):login.check_password(Browser('wrong'))
  browser=Browser('');browser.session_state.update(password_correct=True,auth_activity=login.time.time());self.assertTrue(login.check_password(browser))
if __name__=='__main__':unittest.main()
