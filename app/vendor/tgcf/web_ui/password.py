import hmac
import time
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
    attempts = [t for t in state.get('auth_attempts', []) if now - t < 60]
    state['auth_attempts'] = attempts
    if len(attempts) >= 5:
        st.warning('Too many attempts. Wait one minute and reload.')
        return False
    def entered():
        value = state.pop('password', '')
        state['auth_attempts'].append(time.time())
        if PASSWORD != 'tgcf' and hmac.compare_digest(value.encode(), PASSWORD.encode()):
            state['password_correct'] = True
            state['auth_activity'] = time.time()
            state['auth_attempts'] = []
        else:
            state['auth_error'] = True
    st.text_input('Password', type='password', on_change=entered, key='password')
    if state.get('password_correct'):
        return True
    if state.get('auth_error'):
        st.error('Password incorrect')
    return False
