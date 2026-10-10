import streamlit as st
from tgcf.config import read_config, write_config
from tgcf.web_ui.password import check_password
st.set_page_config(page_title='Telegram Login')
if not check_password(st):
    st.stop()
config = read_config()
st.title('Primary Telegram account')
with st.form('primary_login'):
    config.login.API_ID = int(st.number_input('API ID', min_value=0, value=config.login.API_ID))
    config.login.API_HASH = st.text_input('API hash', value=config.login.API_HASH, type='password')
    config.login.user_type = st.selectbox('Account type', [0, 1], index=config.login.user_type, format_func=lambda x: 'Bot' if x == 0 else 'User')
    config.login.BOT_TOKEN = st.text_input('Bot token', value=config.login.BOT_TOKEN, type='password')
    config.login.SESSION_STRING = st.text_input('User session string', value=config.login.SESSION_STRING, type='password')
    save = st.form_submit_button('Save login')
st.caption('Obtain API credentials from my.telegram.org. Generate user sessions locally on a trusted computer. Pause the account before changing its credentials.')
if save:
    credential = config.login.BOT_TOKEN if config.login.user_type == 0 else config.login.SESSION_STRING
    if not config.login.API_ID or not config.login.API_HASH or not credential:
        st.error('Supply API credentials and the token or session for the selected account type.')
    else:
        try:
            write_config(config)
            st.success('Saved')
        except ValueError:
            st.error('Configuration changed elsewhere. Reload before saving.')
