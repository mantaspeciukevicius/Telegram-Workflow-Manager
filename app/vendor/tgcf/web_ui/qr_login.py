"""Streamlit controls; background jobs never call Streamlit APIs."""
import io
import streamlit as st
import qrcode
from tgcf.qr_login import QRLoginJob, save_qr_session
from tgcf.state_store import EditConflict


def render_qr_login(root, account, login):
    key = 'qr_login_job_' + account
    with st.expander('Log in with QR · ' + account):
        st.write('On your phone, select the correct Telegram account, then Settings → Devices → Link Desktop Device. Scan the QR on this screen.')
        st.caption('Open this dashboard on your PC so your phone can scan it. QR codes expire after about one minute. No SMS code is needed.')
        if st.button('Generate new login QR', key='qr_start_' + account):
            old = st.session_state.pop(key, None)
            if old:
                old.cancel()
            if not login.get('API_ID') or not login.get('API_HASH'):
                st.error('Save API credentials first.')
            else:
                st.session_state[key] = QRLoginJob(login).start()
        job = st.session_state.get(key)
        if not job:
            return
        st.button('Check login status / refresh QR display', key='qr_refresh_' + account)
        state = job.snapshot()
        status = state['status']
        if status == 'starting':
            st.info('Preparing QR. Click Check login status in a moment.')
        elif status == 'scan':
            image = qrcode.make(state['url'])
            buffer = io.BytesIO()
            image.save(buffer, format='PNG')
            st.image(buffer.getvalue(), width=280)
            st.info('After scanning and approving, click Check login status.')
        elif status == 'password':
            if state.get('message'):
                st.warning(state['message'])
            with st.form('qr_password_' + account):
                password = st.text_input('Telegram two-step verification password', type='password')
                submit = st.form_submit_button('Submit two-step password')
            if submit and password:
                job.password(password)
                st.info('Checking password. Click Check login status in a moment.')
        elif status == 'checking_password':
            st.info('Checking password. Click Check login status in a moment.')
        elif status == 'ready':
            identity = '@' + state['username'] if state.get('username') else state['name']
            st.write('Authorized account: ' + identity + ' · Telegram user ID ' + str(state['user_id']))
            st.caption('Confirm this is the account you intend to use for ' + account + '.')
            if st.button('Save this account session', key='qr_save_' + account):
                try:
                    save_qr_session(root, account, job)
                    st.success('Session saved and verified. Backups resume automatically from saved progress.')
                except (ValueError, EditConflict) as exc:
                    st.error(str(exc))
        elif status == 'saved':
            st.success('Session saved. Backups resume automatically.')
        elif status == 'expired':
            st.warning('QR or password entry expired. Generate a new QR.')
        elif status == 'error':
            st.error(state['message'])
        elif status == 'cancelled':
            st.info('Login cancelled.')
        if status not in ('saved', 'cancelled') and st.button('Cancel QR login', key='qr_cancel_' + account):
            job.cancel()
            st.session_state.pop(key, None)
            st.experimental_rerun()
