"""Message copying and per-destination retry progress, independent of configuration."""

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from telethon.client import TelegramClient
from telethon.hints import EntityLike
from telethon.tl.custom.message import Message
from telethon.tl.types import MessageMediaInvoice, MessageMediaStory
from telethon.errors import MediaCaptionTooLongError

if TYPE_CHECKING:
    from tgcf.plugins import TgcfMessage


@dataclass
class CaptionDelivery:
    """Accepted sends survive FloodWait retries within a single message copy."""

    chunks: list[str]
    sent: int = 0
    media: Optional[Message] = None


class MessageCopier:
    """Own copying behavior and retry state for one source message.

    A caller supplies the Telegram client and forwarding preference; this class
    neither loads private configuration nor advances history offsets.
    """

    def __init__(self, client: TelegramClient, message: "TgcfMessage"):
        self.client = client
        self.message = message
        self._captions: dict[str, CaptionDelivery] = {}

    @staticmethod
    def text_chunks(text: str, limit: int = 4096) -> list[str]:
        """Split plain text at Telegram's UTF-16 limit without splitting emoji."""
        chunks = []
        chunk, units = [], 0
        for char in text:
            size = len(char.encode('utf-16-le')) // 2
            if units + size > limit:
                chunks.append(''.join(chunk))
                chunk, units = [], 0
            chunk.append(char)
            units += size
        if chunk:
            chunks.append(''.join(chunk))
        return chunks

    async def send(self, recipient: EntityLike, *, show_forwarded_from: bool) -> Message:
        tm = self.message
        if show_forwarded_from or isinstance(tm.message.media, (MessageMediaInvoice, MessageMediaStory)):
            # Telegram invoices cannot be uploaded as ordinary files.
            return await self.client.forward_messages(recipient, tm.message)
        key = str(recipient)
        if key not in self._captions:
            try:
                if tm.new_file:
                    return await self.client.send_file(
                        recipient, tm.new_file, caption=tm.text, reply_to=tm.reply_to
                    )
                tm.message.text = tm.text
                return await self.client.send_message(recipient, tm.message, reply_to=tm.reply_to)
            except MediaCaptionTooLongError:
                logging.warning('Oversized media caption: preserving full text in replies to the copied file')
                text, _ = await self.client._parse_message_text(tm.text, ())
                self._captions[key] = CaptionDelivery(self.text_chunks(text))
        return await self._send_caption(recipient, self._captions[key])

    async def _send_caption(self, recipient: EntityLike, state: CaptionDelivery) -> Message:
        tm = self.message
        if state.media is None:
            state.media = await self.client.send_file(
                recipient, tm.new_file or tm.message.media,
                caption='', parse_mode=None, reply_to=tm.reply_to
            )
        while state.sent < len(state.chunks):
            await self.client.send_message(
                recipient, state.chunks[state.sent], parse_mode=None,
                link_preview=False, reply_to=state.media.id
            )
            state.sent += 1
        return state.media
