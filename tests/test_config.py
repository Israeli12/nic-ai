import pytest

from nic.config import load_config


def test_defaults_without_a_config_file(tmp_path):
    config = load_config(tmp_path / "absent.yaml")
    assert config.model.name == "qwen3:4b"
    assert config.safety.confirm_dangerous_actions is True


def test_file_overrides_defaults(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "model:\n  name: llama3.1:8b\nlaptop:\n  allow_shell: true\n"
        "  app_allowlist:\n    - notepad\n    - spotify\n",
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.model.name == "llama3.1:8b"
    assert config.laptop.allow_shell is True
    assert config.laptop.app_allowlist == ["notepad", "spotify"]


def test_environment_wins_over_file(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text("web:\n  port: 1000\n", encoding="utf-8")
    monkeypatch.setenv("NIC_WEB_PORT", "9999")
    monkeypatch.setenv("NIC_WEB_ACCESS_TOKEN", "secret")
    config = load_config(path)
    assert config.web.port == 9999
    assert config.web.access_token == "secret"


def test_unknown_section_is_rejected(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("nonsense:\n  a: 1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unknown config section"):
        load_config(path)


def test_unknown_key_is_rejected(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("model:\n  nmae: typo\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unknown config key"):
        load_config(path)
