import streamlit as st
from tgcf.web_ui.dashboard import dashboard
st.set_page_config(page_title='Backup workers')
dashboard()
