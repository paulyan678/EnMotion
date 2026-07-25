from __future__ import annotations

import stat

from scripts.local_setup import _ensure_provider_config_master_key


def test_provider_config_master_key_is_backfilled_once(tmp_path) -> None:
    env_file = tmp_path / "control.env"
    env_file.write_text("ENMOTION_ENV=development", encoding="utf-8")
    env_file.chmod(0o600)

    _ensure_provider_config_master_key(env_file)
    first = env_file.read_text(encoding="utf-8")
    _ensure_provider_config_master_key(env_file)
    second = env_file.read_text(encoding="utf-8")

    assert first == second
    assert first.startswith("ENMOTION_ENV=development\n")
    key_lines = [
        line
        for line in first.splitlines()
        if line.startswith("ENMOTION_PROVIDER_CONFIG_MASTER_KEY=")
    ]
    assert len(key_lines) == 1
    assert len(key_lines[0].partition("=")[2]) == 44
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
