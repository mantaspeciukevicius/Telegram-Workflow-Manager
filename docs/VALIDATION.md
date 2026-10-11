# Validation scope

This repository is a sanitized snapshot of the deployed TGCF customization. Python source compilation, Compose configuration, an isolated Streamlit demo and screenshot rendering are checked during documentation preparation. Existing production workers were left running.

No end-to-end Telegram forwarding test is performed for this public release: it would require private sessions and would send real messages. Public demo accounts have empty sessions. Upstream TGCF behavior and local scheduling should be tested with your own small, authorized route before production use.

## Chat coverage and shared-dump update

41 offline automated tests cover source/receiver distinctions, pauses, worker freshness/errors, history boundaries, username/ID aliases, bulk route creation, account assignment, preserving existing topic destinations and offsets, idempotent retries, conflict handling, loop detection, private settings and actual worker route selection. They use temporary directories and synthetic sessions with network disabled.

A real Chromium/Streamlit test exercises the full Connections page: selecting a joined dump chat, adding a new source and also adding the dump to an existing topic route, retaining the original destination/offset, and rendering on desktop/mobile. Public screenshots come only from this isolated demo; Telegram queries are replaced with offline fixtures. Production verification checks the authenticated page, unchanged route/account/pause state and continued worker containers. It does not send messages or claim an end-to-end Telegram delivery test.

To repeat the offline tests after building the Compose image:

```sh
TGCF_TEST_IMAGE=$(docker compose images -q tgcf-web)
docker run --rm --network none --read-only --tmpfs /tmp:size=64m \
  -e PYTHONPATH=/vendor -e PASSWORD=synthetic-demo-password \
  -v "$PWD/app/vendor:/vendor:ro" \
  -v "$PWD/app/worker_service.py:/runner/worker_service.py:ro" \
  -v "$PWD/tests:/tests:ro" -w /tmp "$TGCF_TEST_IMAGE" \
  python -m unittest discover -s /tests -v
```

The unused-chat filter has offline checks for source and destination coverage, username/URL aliases, disabled routes, all destinations, and preservation of input data.

The actual Streamlit picker was verified with synthetic chats: the checked filter hides both sources and destinations; clearing it makes destination chats selectable again. No Telegram messages were sent.

Scheduling adds 12 offline checks covering 70/30 fairness with 1–200 dump routes, short normal turns, per-queue rotation, spare capacity, restart checkpoints, 50% custom shares, classification overrides, setting bounds, run-once and dynamic removal. The actual history runner was exercised with synthetic subprocesses and verified saved offsets without Telegram access.

Run the synthetic subprocess integration separately (read-only source, isolated network):

```sh
docker run --rm --network none --read-only --tmpfs /tmp:size=64m \
  -e PYTHONPATH=/vendor \
  -v "$PWD/app/vendor:/vendor:ro" \
  -v "$PWD/app/tgcf_single_runner.py:/actual-runner.py:ro" \
  -v "$PWD/tests/check_runner_integration.py:/check.py:ro" \
  tgcf-tgcf-web python /check.py
```

