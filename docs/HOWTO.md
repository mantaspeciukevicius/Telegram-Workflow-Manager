# Setup and troubleshooting

## Configure an account

Create Telegram API credentials at my.telegram.org and use the Telegram Login page. Complete any Telegram login code and two-factor authentication yourself. The first account uses the main configuration; additional accounts use the separate accounts file. Accounts must already belong to the source and have write permission at the destination.

## Add a route

Open Connections, choose an account, select a source and add destinations. Use the chat browser to confirm IDs and distinguish migrated groups. Save, then run a one-time catch-up on a small, authorized test route before starting scheduled or LIVE operation.

## Understand offsets

A saved offset records history progress. Pending offset requests are applied by the worker. Restarting from zero can copy messages again. An interrupted API call can succeed remotely before the local offset is saved; this service cannot guarantee exactly-once Telegram delivery.

## Missing or stale status

Check `docker compose ps` and `docker compose logs --tail 100 tgcf-user1`. Ensure each connection is assigned, enabled and not paused. A stale runtime report means the worker may have stopped; investigate before issuing another job. The account lock prevents a second scheduler for the same account.

## Telegram errors

FloodWait is a Telegram-imposed delay: wait for the displayed cooldown. Invalid peers usually indicate the wrong account, missing membership, a migrated chat ID or missing destination permission. Expired/revoked sessions require a fresh account login. Never publish session strings in issues or screenshots.

## Persistence

Keep the entire private runtime configuration, accounts, offsets and sessions under `app` in an encrypted backup. The Docker image alone does not contain this state. Restore with workers stopped and start only after verifying destination assignments. Never run the original and restored forwarding stack simultaneously against the same routes.
