import pytest

from services import secrets


@pytest.fixture(autouse=True)
def no_env_key(monkeypatch):
    monkeypatch.delenv(secrets.KEY_NAME, raising=False)


def test_save_read_clear(tmp_path):
    env = tmp_path / ".env"
    assert secrets.get_api_key(env) is None
    secrets.save_api_key("  sk-abc123456789xyz  ", env)
    assert secrets.get_api_key(env) == "sk-abc123456789xyz"
    secrets.save_api_key("sk-replacement-0000", env)
    assert secrets.get_api_key(env) == "sk-replacement-0000"
    assert env.read_text().count(secrets.KEY_NAME) == 1
    secrets.clear_api_key(env)
    assert secrets.get_api_key(env) is None


def test_rejects_bad_keys(tmp_path):
    for bad in ["", "   ", "sk-abc def"]:
        with pytest.raises(ValueError):
            secrets.save_api_key(bad, tmp_path / ".env")


def test_mask():
    assert secrets.mask_key(None) == "not set"
    masked = secrets.mask_key("sk-proj-1234567890abcd")
    assert masked == "sk-…abcd" and "1234567890" not in masked
