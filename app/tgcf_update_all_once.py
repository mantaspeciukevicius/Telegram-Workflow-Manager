# ============================================================
# TGCF - ENABLE ALL + UPDATE ALL ONCE
# Created: 2026-08-23
#
# Temporarily enables every connection,
# runs TGCF Past mode using the existing saved offsets,
# then restores each connection's original enabled/disabled state.
# ============================================================

import json
import subprocess
import sys
from pathlib import Path

from tgcf.config import read_config, write_config


STATE_FILE = Path("tgcf.update_all_state.json")


def main():
    cfg = read_config()

    original_states = [
        bool(forward.use_this)
        for forward in cfg.forwards
    ]

    # Keep a recovery file in case something crashes.
    STATE_FILE.write_text(
        json.dumps(original_states),
        encoding="utf-8",
    )

    print("Original connection states:")
    for i, forward in enumerate(cfg.forwards):
        print(
            f"{i + 1}. {forward.con_name} = "
            f"{'ENABLED' if original_states[i] else 'DISABLED'}"
        )

    # Temporarily enable everything.
    for forward in cfg.forwards:
        forward.use_this = True

    cfg.mode = 1  # Past mode
    write_config(cfg)

    print()
    print("All connections temporarily ENABLED.")
    print("Starting Past-mode backup update...")
    print()

    return_code = 1

    try:
        return_code = subprocess.call(
            ["tgcf", "--loud", "past"]
        )

    finally:
        # IMPORTANT:
        # Re-read the config after Past mode so we preserve
        # all newly updated offsets.
        latest = read_config()

        for i, old_state in enumerate(original_states):
            if i < len(latest.forwards):
                latest.forwards[i].use_this = old_state

        write_config(latest)

        if STATE_FILE.exists():
            STATE_FILE.unlink()

        print()
        print("Original enabled/disabled states restored.")

    return return_code


sys.exit(main())
