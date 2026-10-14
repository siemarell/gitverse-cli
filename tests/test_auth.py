import pathlib
import stat

import keyring.errors
import pytest
from conftest import TOKEN, FakeKeyring

from gitverse_cli.auth import TokenStore


def test_the_environment_variable_wins_over_the_keyring(tmp_path: pathlib.Path) -> None:
    fake_keyring = FakeKeyring()
    TokenStore(tmp_path, fake_keyring, {}).save("stored")
    token = TokenStore(tmp_path, fake_keyring, {"GV_TOKEN": "from-env"}).load()
    assert token is not None
    assert (token.value, token.source) == ("from-env", "environment variable GV_TOKEN")


def test_the_keyring_holds_the_token_and_no_file_is_written(tmp_path: pathlib.Path) -> None:
    store = TokenStore(tmp_path / "gv", FakeKeyring(), {})
    assert store.save(TOKEN) == "system keyring"
    token = store.load()
    assert token is not None
    assert (token.value, token.source) == (TOKEN, "system keyring")
    assert not store.token_file.exists()


def test_without_a_keyring_the_token_goes_to_a_file_only_its_owner_reads(tmp_path: pathlib.Path) -> None:
    store = TokenStore(tmp_path / "gv", FakeKeyring(available=False), {})
    assert store.save(TOKEN) == str(store.token_file)
    assert stat.S_IMODE(store.token_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(store.token_file.parent.stat().st_mode) == 0o700
    token = store.load()
    assert token is not None
    assert token.value == TOKEN


def test_logout_removes_the_token_from_both_places(tmp_path: pathlib.Path) -> None:
    fake_keyring = FakeKeyring()
    store = TokenStore(tmp_path / "gv", fake_keyring, {})
    store.save(TOKEN)
    store.token_file.parent.mkdir(parents=True, exist_ok=True)
    store.token_file.write_text("old\n")
    assert store.delete() == ["system keyring", str(store.token_file)]
    assert store.load() is None
    assert store.delete() == []


def test_the_token_value_stays_out_of_its_repr(tmp_path: pathlib.Path) -> None:
    store = TokenStore(tmp_path, FakeKeyring(), {"GV_TOKEN": TOKEN})
    assert TOKEN not in repr(store.load())


def test_a_locked_keyring_is_an_error_not_a_missing_token(tmp_path: pathlib.Path) -> None:
    locked = FakeKeyring(failure=keyring.errors.KeyringError("locked"))
    with pytest.raises(keyring.errors.KeyringError):
        TokenStore(tmp_path, locked, {}).load()
