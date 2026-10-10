"""Device credential storage.

Preference order for the device id/secret:

1. **Windows Credential Manager** (via ``keyring``): encrypted by the OS under your login,
   never written to a plain-text file.
2. ``JARVIS_DEVICE_ID`` / ``JARVIS_DEVICE_SECRET`` from the environment or ``.env``.

Store them once (the secret is read with a hidden prompt):

    python -m jarvis_local_agent.credentials set
    python -m jarvis_local_agent.credentials clear
"""
from __future__ import annotations

import getpass
import sys
from typing import Optional, Tuple

SERVICE = "jarvis-local-agent"


def load_from_keyring() -> Optional[Tuple[str, str]]:
    try:
        import keyring
        device_id = keyring.get_password(SERVICE, "device_id")
        secret = keyring.get_password(SERVICE, "device_secret")
    except Exception:  # no usable backend (headless Linux, etc.)
        return None
    return (device_id, secret) if device_id and secret else None


def store_in_keyring(device_id: str, secret: str) -> None:
    import keyring
    keyring.set_password(SERVICE, "device_id", device_id)
    keyring.set_password(SERVICE, "device_secret", secret)


def clear_keyring() -> None:
    import keyring
    from keyring.errors import PasswordDeleteError

    for name in ("device_id", "device_secret"):
        try:
            keyring.delete_password(SERVICE, name)
        except PasswordDeleteError:
            pass  # nothing was stored under that name; any other failure should surface


def main(argv: Optional[list] = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if args[:1] == ["set"]:
        device_id = input("Device id: ").strip()
        secret = getpass.getpass("Device secret (hidden): ").strip()
        if not device_id or not secret:
            print("Both values are required.")
            return 2
        store_in_keyring(device_id, secret)
        print("Stored in the OS credential store. You can now remove them from any .env file.")
        return 0
    if args[:1] == ["clear"]:
        clear_keyring()
        print("Removed stored device credentials.")
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
