import os
from bootstrap import persistent_secret


def test_persistent_random_secrets_survive_restarts(tmp_path):
    path = tmp_path / '.session-secret'
    first = persistent_secret(path)
    assert len(first) >= 48
    assert persistent_secret(path) == first
    assert path.stat().st_mode & 0o077 == 0  # not group/world readable
    assert persistent_secret(tmp_path / '.admin-setup-token') != first
