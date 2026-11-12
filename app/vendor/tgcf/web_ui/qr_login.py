"""Streamlit controls; background jobs never call Streamlit APIs."""
import io
import streamlit as st
import qrcode
from tgcf.qr_login import QRLoginJob, save_qr_session
from tgcf.state_store import EditConflict


def render_qr_login(root, account, login):
    key = 'qr_login_job_' + account
    with st.expander('Log in with QR · ' + account):
        st.write('**3 steps: 1. Scan QR → 2. Enter two-step password (if enabled) → 3. Confirm and save.**')
        st.warning('Scanning the QR is not the final step. Return here, continue to the password step, and save the session.')
        st.write('On your phone, select the correct Telegram account, then Settings → Devices → Link Desktop Device.')
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
        state = job.snapshot()
        status = state['status']
        if status == 'starting':
            st.info('Step 1 of 3 · Preparing your QR code.')
            st.button('Show QR code', key='qr_refresh_' + account)
        elif status == 'scan':
            st.markdown('### Step 1 of 3 · Scan and approve the QR')
            image = qrcode.make(state['url'])
            buffer = io.BytesIO()
            image.save(buffer, format='PNG')
            st.image(buffer.getvalue(), width=280)
            st.info('After approving on your phone, click Continue below. Your Telegram two-step password may be required next; it is entered here on this page.')
            st.button('I have scanned the QR — continue to step 2', key='qr_refresh_' + account)
        elif status == 'password':
            st.markdown('### Step 2 of 3 · Enter your Telegram password')
            st.info('QR approved. Login is not complete yet. Enter the two-step verification password you set in Telegram, not a login code or phone unlock PIN.')
            if state.get('message'):
                st.warning(state['message'])
            with st.form('qr_password_' + account):
                password = st.text_input('Telegram two-step verification password', type='password')
                submit = st.form_submit_button('Verify password and continue to step 3')
            if submit and password:
                job.password(password)
                st.experimental_rerun()
        elif status == 'checking_password':
            st.info('Step 2 of 3 · Verifying your password. Login is not saved yet.')
            st.button('Check password result — continue', key='qr_refresh_' + account)
        elif status == 'ready':
            st.markdown('### Step 3 of 3 · Confirm and save')
            st.info('Telegram authorization succeeded, including any required password. Save below to reconnect the backup tool.')
            identity = '@' + state['username'] if state.get('username') else state['name']
            st.write('Authorized account: ' + identity + ' · Telegram user ID ' + str(state['user_id']))
            st.caption('Confirm this is the account you intend to use for ' + account + '.')
            if st.button('Finish login and save session', key='qr_save_' + account):
                try:
                    save_qr_session(root, account, job)
                    st.success('All 3 steps complete. Session saved and verified. Backups resume automatically from saved progress.')
                except (ValueError, EditConflict) as exc:
                    st.error(str(exc))
        elif status == 'saved':
            st.success('All 3 steps complete. Session saved. Backups resume automatically.')
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
