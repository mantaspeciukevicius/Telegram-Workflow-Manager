"""The module for running tgcf in past mode.

- past mode can only operate with a user account.
- past mode deals with all existing messages.
"""

import asyncio
import logging
import time

from telethon import TelegramClient
from telethon.errors.rpcerrorlist import FloodWaitError
from telethon.tl.custom.message import Message
from telethon.tl.patched import MessageService

from tgcf.activity import activity
from tgcf import config
from tgcf import storage as st
from tgcf.config import CONFIG, get_SESSION, write_config
from tgcf.plugins import apply_plugins
from tgcf.utils import clean_session_files, send_message


async def send_message_flood_safe(dest, tm, connection_id=None):
    """Send one message and retry the SAME message after Telegram FloodWait."""
    while True:
        try:
            return await send_message(dest, tm)

        except FloodWaitError as fwe:
            wait_seconds = int(fwe.seconds) + 5

            logging.warning(
                "Telegram FloodWait while sending to %s. "
                "Waiting %s seconds, then retrying the SAME message.",
                dest,
                wait_seconds,
            )

            if connection_id:
                activity(connection_id, state='flood_wait', flood_until=time.time() + wait_seconds, detail='Telegram rate limit; retrying the same message')
            await asyncio.sleep(wait_seconds)
            if connection_id:
                activity(connection_id, state='running', flood_until=0)

            logging.info(
                "FloodWait finished. Retrying the same message to %s.",
                dest,
            )


async def forward_job() -> None:
    """Forward all existing messages in the concerned chats."""
    clean_session_files()
    if CONFIG.login.user_type != 1:
        logging.warning(
            "You cannot use bot account for tgcf past mode. Telegram does not allow bots to access chat history."
        )
        return
    SESSION = get_SESSION()
    async with TelegramClient(
        SESSION, CONFIG.login.API_ID, CONFIG.login.API_HASH
    ) as client:
        config.from_to = await config.load_from_to(client, config.CONFIG.forwards)
        client: TelegramClient
        for from_to, forward in zip(config.from_to.items(), config.CONFIG.forwards):
            src, dest = from_to
            last_id = forward.offset
            activity(forward.connection_id, state='running', detail='Copying history', flood_until=0)
            forward: config.Forward
            logging.info(f"Forwarding messages from {src} to {dest}")
            async for message in client.iter_messages(
                src, reverse=True, offset_id=forward.offset
            ):
                message: Message
                event = st.DummyEvent(message.chat_id, message.id)
                event_uid = st.EventUid(event)

                if forward.end and message.id > forward.end:
                    break
                if isinstance(message, MessageService):
                    continue
                if not message.message and message.media is None:
                    # Telegram may expose an empty history entry. There is no
                    # content to copy, but its ID must not block later messages.
                    forward.offset = message.id
                    for current in CONFIG.forwards:
                        if current.connection_id == forward.connection_id:
                            current.offset = message.id
                            break
                    write_config(CONFIG, persist=False)
                    logging.info('Skipped empty source message with id = %s', message.id)
                    continue
                try:
                    tm = await apply_plugins(message)
                    if not tm:
                        continue
                    st.stored[event_uid] = {}

                    if message.is_reply:
                        r_event = st.DummyEvent(
                            message.chat_id, message.reply_to_msg_id
                        )
                        r_event_uid = st.EventUid(r_event)
                    for d in dest:
                        if message.is_reply and r_event_uid in st.stored:
                            tm.reply_to = st.stored.get(r_event_uid).get(d)
                        fwded_msg = await send_message_flood_safe(d, tm, forward.connection_id)
                        st.stored[event_uid].update({d: fwded_msg.id})
                    tm.clear()
                    last_id = message.id
                    logging.info(f"forwarding message with id = {last_id}")
                    forward.offset = last_id
                    # Saving validates/replaces CONFIG.forwards. The iterator's
                    # original Forward object is detached after the first save.
                    for current in CONFIG.forwards:
                        if current.connection_id == forward.connection_id:
                            current.offset = last_id
                            break
                    write_config(CONFIG, persist=False)
                    activity(forward.connection_id, state='running', last_message_id=last_id, last_event=time.time(), detail='Message copied', flood_until=0)
                    await asyncio.sleep(CONFIG.past.delay)
                    logging.info(f"slept for {CONFIG.past.delay} seconds")

                except FloodWaitError as fwe:
                    logging.info(f"Sleeping for {fwe}")
                    await asyncio.sleep(delay=fwe.seconds)
                except Exception as err:
                    activity(forward.connection_id, state='error', detail=f'{type(err).__name__}: {err}')
                    logging.error('%s: %s', type(err).__name__, err)
                    raise
