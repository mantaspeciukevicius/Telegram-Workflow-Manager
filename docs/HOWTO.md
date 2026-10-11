# Setup and troubleshooting

## Configure an account

Create Telegram API credentials at my.telegram.org and use the Telegram Login page. Complete any Telegram login code and two-factor authentication yourself. The first account uses the main configuration; additional accounts use the separate accounts file. Accounts must already belong to the source and have write permission at the destination.

## Add a route

Open Connections, choose an account, select a source and add destinations. Use the chat browser to confirm IDs and distinguish migrated groups. Save, then run a one-time catch-up on a small, authorized test route before starting scheduled or LIVE operation.

## See which chats are backed up

Open **Connections → Chat & channel list**, choose a Telegram account, then click **Load joined chats / channels**. Search by name, ID, username or destination and use **Backup filter** to focus on unconfigured or problematic routes.

| Status | Meaning |
| --- | --- |
| Not configured | No source connection; being a receiver alone does not count |
| Backing up now / Live forwarding | Recent worker report lists this connection as active |
| Scheduled | An enabled assigned route is waiting for its worker turn |
| Account paused / Route paused / Disabled | Collection is intentionally stopped for that scope |
| Unassigned account / No destination / Not logged in | Configuration or login needs attention |
| Configured · worker unavailable / Worker error | Route exists, but worker activity is missing or failed |
| History limit reached | The configured finite history boundary was reached |

A configured or scheduled route is not proof that all its history has finished copying. Use Backup Control for offsets, activity, Telegram errors and logs. Joined-chat membership is refreshed only when you load it; route and worker status are read again on page interaction.

## Configure a shared dump

1. Load joined chats for the account that can read your sources and post to your archive.
2. Open **Shared dump chat**. Pick a joined destination or enter a chat ID/public username. Save once; it is a shared default across accounts.
3. In **Quick backup to dump**, select one or several source chats and click **Back up selected chats to dump**. No source ID copying or separate account-assignment step is needed.
4. Check Backup Control. New sources get enabled routes starting at message zero; existing workers pick them up on their next cycle. A paused account stays paused until you resume it. LIVE mode handles new messages rather than historical catch-up.

To also send an existing topic backup to the dump, enable **Also include my existing backups · keep their current destinations** and select the route. Only enabled routes assigned to the selected account are offered. Its existing destination, identity and offset remain intact; the dump receives messages processed from that saved offset on the next cycle. Existing history is not automatically replayed. For a deliberate history replay or destination replacement, use the existing connection/offset controls and consider duplicates.

Changing or clearing the shared default affects future shortcut operations, not existing routes. Every assigned account needs permission to post to the destination. Invite/message links are not accepted as destination IDs; join the chat normally and use its ID or public username.

The shared setting persists privately in `app/.tgcf_dump_chat.json`. Keep it with configuration, assignments and offsets in encrypted backups. It contains private chat metadata and is excluded from Git.

## Understand offsets

A saved offset records history progress. Pending offset requests are applied by the worker. Restarting from zero can copy messages again. An interrupted API call can succeed remotely before the local offset is saved; this service cannot guarantee exactly-once Telegram delivery.

## Missing or stale status

Check `docker compose ps` and `docker compose logs --tail 100 tgcf-user1`. Ensure each connection is assigned, enabled and not paused. A stale runtime report means the worker may have stopped; investigate before issuing another job. The account lock prevents a second scheduler for the same account.

## Telegram errors

FloodWait is a Telegram-imposed delay: wait for the displayed cooldown. Invalid peers usually indicate the wrong account, missing membership, a migrated chat ID or missing destination permission. Expired/revoked sessions require a fresh account login. Never publish session strings in issues or screenshots.

## Persistence

Keep the entire private runtime configuration, accounts, offsets and sessions under `app` in an encrypted backup. The Docker image alone does not contain this state. Restore with workers stopped and start only after verifying destination assignments. Never run the original and restored forwarding stack simultaneously against the same routes.

Quick backup to dump now defaults to **Hide chats used as sources or destinations**. This excludes chats referenced anywhere in configured routes, including disabled routes and routes assigned to other accounts. Uncheck it to show available destinations; also enable the existing-backups option when adding the dump to an existing source route. Existing destinations and offsets are preserved.

### History scheduling priorities

Backup Control → **History backup priorities · normal / dump** configures the normal time share (default 70%, minimum 50%), normal session limit (70 minutes), dump session limit (30 minutes), and which routes belong to the lower-priority queue. Settings apply at the next turn. Newly created quick-dump routes default to dump priority; existing topic routes remain normal when another destination is appended.

Each account schedules its own two queues. Actual elapsed time determines fairness, independently of route counts. Within each queue, routes take turns. While both queues have runnable history work, the long-run share follows the configured percentage; individual turns can temporarily differ. Spare capacity is available to the other queue when a queue is empty or finished. Completed routes have a five-minute cooldown; timed-out routes resume from their saved offsets. Paused, disabled and reassigned routes are filtered before each turn. Run-once visits every selected route once; live forwarding is unchanged.

Queue usage, interrupted-turn checkpoints and cursors persist in private sidecars. No offset resets or Telegram rate-limit bypasses are introduced.
