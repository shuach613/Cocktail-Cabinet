"""Persistent, automatically generated instance secrets.

No .env file or hard-coded Docker Compose credentials are necessary. Both
secrets live inside the persistent /data volume and survive image updates.
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path


def persistent_secret(path: Path) -> str:
    """Create a strong private key once, atomically, or reuse the existing one.

    Using O_EXCL avoids overwriting an existing key if the container restarts
    while another process is initializing the same volume.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as output:
                output.write(secrets.token_urlsafe(48) + '\n')
        except BaseException:
            path.unlink(missing_ok=True)
            raise
    value = path.read_text(encoding='utf-8').strip()
    if len(value) < 32:
        raise RuntimeError(f'Persisted secret at {path} is missing or too short. Restore it from a backup.')
    return value
