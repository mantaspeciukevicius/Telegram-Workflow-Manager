# Message Workflow Manager

A Docker-deployed, multi-account Telegram forwarding and history-copying service with a Streamlit control panel. This project extends [aahnik/tgcf](https://github.com/aahnik/tgcf); upstream authorship and the MIT license are preserved in [NOTICE](NOTICE.md) and [LICENSE](LICENSE).

![Backup worker dashboard using synthetic accounts](docs/screenshots/dashboard.png)

## Local extensions

- A dedicated Docker-owned scheduler for each account, plus a service for additional accounts.
- Catch-up backups, one-time history copying and live forwarding controls.
- Connection-to-account assignments, per-account pauses and per-connection controls.
- Saved offsets, progress reports, activity history and Telegram FloodWait visibility.
- File locks, atomic JSON writes and merge-aware configuration updates to reduce concurrent-write conflicts.
- A password-protected Streamlit interface with session expiry and session-local login throttling.

This is a forwarding service, not a chatbot. It depends on authorized Telegram accounts and on access to each source and destination. Telegram permissions, protected content and rate limits still apply. Copy only material you have permission to copy.

## Quick start

```sh
git clone https://github.com/mantaspeciukevicius/message-workflow-manager.git
cd message-workflow-manager
python3 scripts/configure.py
# Optional: set LAN_IP in a root .env to expose the UI on your trusted LAN.
docker compose up -d --build
```

The initial UI binds to loopback at `http://127.0.0.1:8501`. The generated dashboard password is in `app/.env`. The application refuses the upstream default password `tgcf`.

1. Sign in to the dashboard.
2. Open Telegram Login and supply your own API ID/hash from [my.telegram.org](https://my.telegram.org/), then complete the account login or supply an authorized session.
3. In Multi User, add additional accounts if needed. Session strings are equivalent to credentials: keep them private.
4. In Connections, select a source, destinations and assigned account. Confirm that the account can read the source and write to each destination.
5. Open Backup Control to choose scheduled catch-up, a single catch-up, or LIVE forwarding. Check status and logs before widening the scope.

Do not reset offsets to zero unless you intend to repeat history; that can duplicate messages. Exact once-only delivery across Telegram/network failures is not guaranteed. A successful container start alone does not prove forwarding is configured.

## How it works

```mermaid
flowchart LR
  UI[Streamlit control panel] --> State[Locked JSON configuration / requests]
  State --> Scheduler[Docker account services]
  Scheduler --> Runner[TGCF / Telethon runner]
  Runner --> Telegram[Authorized Telegram sources and destinations]
  Runner --> Progress[Offsets / activity / logs]
  Progress --> UI
```

The UI queues work instead of launching forwarding processes. Workers retain progress and report status through files in the persistent `app` bind mount. Configuration and session files are deliberately excluded from this repository. Screenshots use synthetic accounts and connections with no Telegram session, and no forwarding worker is run for screenshots.

More interface views: [screenshot gallery](docs/SCREENSHOTS.md).

## Operations and recovery

```sh
docker compose ps
docker compose logs --tail 100 tgcf-user1
docker compose up -d --build
```

Services use `restart: unless-stopped`. For a consistent backup, pause jobs, let them save offsets, then stop the stack before archiving the private `app` state; restart afterward. Keep credentials, session strings, offsets and connection assignments together in an encrypted backup. A source checkout alone cannot restore your Telegram login or forwarding progress.

See [setup and troubleshooting](docs/HOWTO.md), [validation](docs/VALIDATION.md), and the [LinkedIn project summary](docs/PORTFOLIO.md).
