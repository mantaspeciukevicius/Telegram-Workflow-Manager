"""Offline delivery tests with a fake client; never calls Telegram."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from telethon.errors import FloodWaitError, MediaCaptionTooLongError
from telethon.tl.types import MessageMediaInvoice
from tgcf.message_copy import MessageCopier


class MessageCopyTests(unittest.IsolatedAsyncioTestCase):
    def make_copier(self, text='caption', new_file=None, media=None):
        client = SimpleNamespace(
            send_message=AsyncMock(return_value=SimpleNamespace(id=20)),
            send_file=AsyncMock(return_value=SimpleNamespace(id=10)),
            forward_messages=AsyncMock(return_value=SimpleNamespace(id=30)),
            _parse_message_text=AsyncMock(return_value=(text, [])),
        )
        tm = SimpleNamespace(message=SimpleNamespace(media=media or object(), text=text),
                             text=text, new_file=new_file, reply_to=7)
        return MessageCopier(client, tm), client, tm

    async def test_normal_copy_keeps_reply_and_text(self):
        copier, client, tm = self.make_copier()
        result = await copier.send(-1, show_forwarded_from=False)
        self.assertEqual(result.id, 20)
        client.send_message.assert_awaited_once_with(-1, tm.message, reply_to=7)
        client.send_file.assert_not_awaited()

    async def test_modified_file_keeps_caption(self):
        copier, client, _ = self.make_copier(new_file='modified.png')
        await copier.send(-1, show_forwarded_from=False)
        client.send_file.assert_awaited_once_with(-1, 'modified.png', caption='caption', reply_to=7)

    async def test_forwarding_preference_uses_native_forward(self):
        copier, client, tm = self.make_copier()
        await copier.send(-1, show_forwarded_from=True)
        client.forward_messages.assert_awaited_once_with(-1, tm.message)
        client.send_message.assert_not_awaited()

    async def test_invoice_uses_native_forward_in_copy_mode(self):
        invoice = MessageMediaInvoice(title='Donation', description='Optional', currency='EUR', total_amount=100, start_param='donate')
        copier, client, tm = self.make_copier(media=invoice)
        await copier.send(-1, show_forwarded_from=False)
        client.forward_messages.assert_awaited_once_with(-1, tm.message)
        client.send_file.assert_not_awaited()

    async def test_caption_retry_preserves_unicode_and_accepted_sends(self):
        text = 'a'*4095 + '😀' + 'b'*4100
        copier, client, _ = self.make_copier(text)
        client.send_message.side_effect = [MediaCaptionTooLongError(None), SimpleNamespace(id=21), FloodWaitError(None, capture=1)]
        with self.assertRaises(FloodWaitError):
            await copier.send(-1, show_forwarded_from=False)
        client.send_message.side_effect = None
        result = await copier.send(-1, show_forwarded_from=False)
        self.assertEqual(result.id, 10)
        client.send_file.assert_awaited_once()
        calls = client.send_message.await_args_list
        # The failed second chunk is retried; the first chunk is not repeated.
        accepted = [calls[1].args[1]] + [call.args[1] for call in calls[3:]]
        self.assertEqual(''.join(accepted), text)
        self.assertTrue(all(len(chunk.encode('utf-16-le'))//2 <= 4096 for chunk in accepted))
        self.assertEqual(calls[2].args[1], calls[3].args[1])

    async def test_caption_progress_is_separate_for_each_destination(self):
        copier, client, _ = self.make_copier('long caption')
        client.send_message.side_effect = [MediaCaptionTooLongError(None), SimpleNamespace(id=21), MediaCaptionTooLongError(None), SimpleNamespace(id=22)]
        await copier.send(-1, show_forwarded_from=False)
        await copier.send(-2, show_forwarded_from=False)
        self.assertEqual(client.send_file.await_count, 2)
        self.assertEqual([call.args[0] for call in client.send_file.await_args_list], [-1, -2])

    async def test_modified_file_caption_failure_retries_same_file(self):
        copier, client, _ = self.make_copier(new_file='modified.png')
        client.send_file.side_effect = [MediaCaptionTooLongError(None), SimpleNamespace(id=10)]
        await copier.send(-1, show_forwarded_from=False)
        self.assertEqual(client.send_file.await_args_list[1].args, (-1, 'modified.png'))
        self.assertEqual(client.send_file.await_args_list[1].kwargs['caption'], '')

    async def test_public_wrapper_reuses_copier_after_flood_wait(self):
        from unittest.mock import patch
        from tgcf.utils import send_message
        _, client, tm = self.make_copier()
        tm.message.client = client
        client.send_message.side_effect = [MediaCaptionTooLongError(None), FloodWaitError(None, capture=1)]
        with patch('tgcf.utils.CONFIG', SimpleNamespace(show_forwarded_from=False)):
            with self.assertRaises(FloodWaitError):
                await send_message(-1, tm)
            copier = tm._message_copier
            client.send_message.side_effect = None
            await send_message(-1, tm)
        self.assertIs(tm._message_copier, copier)
        client.send_file.assert_awaited_once()

    async def test_shared_story_uses_native_forward(self):
        from telethon.tl.types import MessageMediaStory
        story = MessageMediaStory.__new__(MessageMediaStory)
        copier, client, tm = self.make_copier(media=story)
        await copier.send(-1, show_forwarded_from=False)
        client.forward_messages.assert_awaited_once_with(-1, tm.message)
        client.send_file.assert_not_awaited()
