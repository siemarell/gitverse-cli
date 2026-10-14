import os
import pathlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

import keyring
import keyring.errors

ENVIRONMENT_VARIABLE = "GV_TOKEN"
KEYRING_SERVICE = "gitverse-cli"
KEYRING_ACCOUNT = "api.gitverse.ru"
TOKEN_FILE_NAME = "token"


class KeyringBackend(Protocol):
    def get_password(self, service_name: str, username: str) -> str | None: ...
    def set_password(self, service_name: str, username: str, password: str) -> None: ...
    def delete_password(self, service_name: str, username: str) -> None: ...


@dataclass(frozen=True)
class Token:
    value: str = field(repr=False)
    # Where it came from, for `gv auth status`: never the value itself.
    source: str


class TokenStore:
    """GV_TOKEN, then the system keyring (Keychain on macOS), then a 0600 file."""

    def __init__(
        self,
        config_directory: pathlib.Path,
        keyring_backend: KeyringBackend,
        environment: Mapping[str, str],
    ) -> None:
        self._token_file = config_directory / TOKEN_FILE_NAME
        self._keyring = keyring_backend
        self._environment = environment

    @property
    def token_file(self) -> pathlib.Path:
        return self._token_file

    def load(self) -> Token | None:
        from_environment = self.load_from_environment()
        if from_environment is not None:
            return from_environment
        # Only "no keyring at all" falls through; a locked or denied keyring is the user's to see.
        try:
            from_keyring = self._keyring.get_password(KEYRING_SERVICE, KEYRING_ACCOUNT)
        except keyring.errors.NoKeyringError:
            from_keyring = None
        if from_keyring:
            return Token(from_keyring, "system keyring")
        if self._token_file.is_file():
            from_file = self._token_file.read_text().strip()
            if from_file:
                return Token(from_file, str(self._token_file))
        return None

    def load_from_environment(self) -> Token | None:
        value = self._environment.get(ENVIRONMENT_VARIABLE, "").strip()
        return Token(value, f"environment variable {ENVIRONMENT_VARIABLE}") if value else None

    def save(self, token: str) -> str:
        """Returns where the token went."""
        try:
            self._keyring.set_password(KEYRING_SERVICE, KEYRING_ACCOUNT, token)
        except keyring.errors.NoKeyringError:
            self._write_token_file(token)
            return str(self._token_file)
        # A file left from a login without a keyring would outlive this one's logout.
        self._token_file.unlink(missing_ok=True)
        return "system keyring"

    def delete(self) -> list[str]:
        """Returns the places a token was removed from."""
        removed: list[str] = []
        try:
            self._keyring.delete_password(KEYRING_SERVICE, KEYRING_ACCOUNT)
            removed.append("system keyring")
        except (keyring.errors.PasswordDeleteError, keyring.errors.NoKeyringError):
            pass  # nothing stored there, or no keyring at all
        if self._token_file.is_file():
            self._token_file.unlink()
            removed.append(str(self._token_file))
        return removed

    def _write_token_file(self, token: str) -> None:
        self._token_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Created 0600 from the start: write-then-chmod leaves a moment the token is readable.
        descriptor = os.open(self._token_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as token_file:
            token_file.write(token + "\n")
        # O_CREAT's mode does not apply to a file that already existed.
        self._token_file.chmod(0o600)


def default_config_directory(environment: Mapping[str, str]) -> pathlib.Path:
    base = environment.get("XDG_CONFIG_HOME") or str(pathlib.Path.home() / ".config")
    return pathlib.Path(base) / "gv"


def default_token_store() -> TokenStore:
    return TokenStore(default_config_directory(os.environ), keyring, os.environ)
