import hmac
import time
import threading
from collections import deque

# Shared by all browser sessions in this web-server process.
_attempts = deque()
_attempt_lock = threading.Lock()

def _limited(record=False):
    with _attempt_lock:
        now = time.monotonic()
        while _attempts and now - _attempts[0] >= 60:
            _attempts.popleft()
        if len(_attempts) >= 5:
            return True
        if record:
            _attempts.append(now)
        return False

from tgcf.config import PASSWORD

def check_password(st):
    now = time.time()
    state = st.session_state
    if state.get('password_correct') and now - state.get('auth_activity', 0) < 1800:
        if st.sidebar.button('Log out', key='auth_logout'):
            state.clear()
            st.experimental_rerun()
        state['auth_activity'] = now
        return True
    state['password_correct'] = False
    if _limited():
        st.warning('Too many attempts. Wait one minute and reload.')
        return False
    def entered():
        value = state.pop('password', '')
        if _limited(record=True):
            state['auth_error'] = True
            return
        if PASSWORD != 'tgcf' and hmac.compare_digest(value.encode(), PASSWORD.encode()):
            state['password_correct'] = True
            state['auth_activity'] = time.time()
        else:
            state['auth_error'] = True
    st.text_input('Password', type='password', on_change=entered, key='password')
    if state.get('password_correct'):
        return True
    if state.get('auth_error'):
        st.error('Password incorrect')
    return False
