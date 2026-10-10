"""Worker entry point with useful plain logs and concise actionable errors."""
import asyncio
import logging
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / 'vendor'))
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logging.getLogger('telethon').setLevel(logging.WARNING)
from tgcf.config import CONFIG
from tgcf.activity import activity

try:
    if sys.argv[1] == 'live':
        from tgcf.live import start_sync
        asyncio.run(start_sync())
    else:
        from tgcf.past import forward_job
        asyncio.run(forward_job())
except Exception as exc:
    # Avoid dumping session/config locals into web-visible tracebacks.
    message = f'{type(exc).__name__}: {exc}'
    logging.error(message)
    for forward in CONFIG.forwards:
        activity(forward.connection_id, state='error', detail=message)
    raise SystemExit(1)
