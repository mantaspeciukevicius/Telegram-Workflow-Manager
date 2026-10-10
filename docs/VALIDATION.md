# Validation scope

This repository is a sanitized snapshot of the deployed TGCF customization. Python source compilation, Compose configuration, an isolated Streamlit demo and screenshot rendering are checked during documentation preparation. Existing production workers were left running.

No end-to-end Telegram forwarding test is performed for this public release: it would require private sessions and would send real messages. Public demo accounts have empty sessions. Upstream TGCF behavior and local scheduling should be tested with your own small, authorized route before production use.
