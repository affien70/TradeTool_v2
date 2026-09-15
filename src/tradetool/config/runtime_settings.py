from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import os
from pathlib import Path
import sqlite3

APP_DB_ENV_VAR = 'TRADETOOL_V2_DB_PATH'
DEFAULT_APP_DB_PATH = Path('.local') / 'tradetool_v2.sqlite'
PRICE_HISTORY_V2_TABLE = 'price_history_v2'


@dataclass(frozen=True, slots=True)
class RuntimeDatabaseStatus:
    configured_path: Path
    env_var_name: str
    env_override_active: bool
    exists: bool
    readable: bool
    price_history_v2_table_exists: bool
    row_count: int | None
    latest_price_date: str | None
    error: str | None = None

    @property
    def configured_path_text(self) -> str:
        return str(self.configured_path)

    def to_dict(self) -> dict[str, object]:
        return {
            'configured_path': self.configured_path_text,
            'env_var_name': self.env_var_name,
            'env_override_active': self.env_override_active,
            'exists': self.exists,
            'readable': self.readable,
            'price_history_v2_table_exists': self.price_history_v2_table_exists,
            'row_count': self.row_count,
            'latest_price_date': self.latest_price_date,
            'error': self.error,
        }


def get_app_database_path(*, env: Mapping[str, str] | None = None, cwd: Path | None = None) -> Path:
    active_env = os.environ if env is None else env
    override = active_env.get(APP_DB_ENV_VAR, '').strip()
    raw_path = Path(override).expanduser() if override else DEFAULT_APP_DB_PATH
    if raw_path.is_absolute():
        return raw_path.resolve()
    base = Path.cwd() if cwd is None else cwd
    return (base / raw_path).resolve()


def inspect_app_database(*, env: Mapping[str, str] | None = None, cwd: Path | None = None) -> RuntimeDatabaseStatus:
    active_env = os.environ if env is None else env
    db_path = get_app_database_path(env=active_env, cwd=cwd)
    env_override_active = bool(active_env.get(APP_DB_ENV_VAR, '').strip())
    if not db_path.exists():
        return RuntimeDatabaseStatus(
            configured_path=db_path,
            env_var_name=APP_DB_ENV_VAR,
            env_override_active=env_override_active,
            exists=False,
            readable=False,
            price_history_v2_table_exists=False,
            row_count=None,
            latest_price_date=None,
        )
    if not db_path.is_file():
        return RuntimeDatabaseStatus(
            configured_path=db_path,
            env_var_name=APP_DB_ENV_VAR,
            env_override_active=env_override_active,
            exists=True,
            readable=False,
            price_history_v2_table_exists=False,
            row_count=None,
            latest_price_date=None,
            error='configured path is not a file',
        )
    try:
        with sqlite3.connect(f'file:{db_path}?mode=ro', uri=True) as connection:
            table_exists = (
                connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                    (PRICE_HISTORY_V2_TABLE,),
                ).fetchone()
                is not None
            )
            if not table_exists:
                return RuntimeDatabaseStatus(
                    configured_path=db_path,
                    env_var_name=APP_DB_ENV_VAR,
                    env_override_active=env_override_active,
                    exists=True,
                    readable=True,
                    price_history_v2_table_exists=False,
                    row_count=None,
                    latest_price_date=None,
                )
            row_count = int(connection.execute(f'SELECT COUNT(*) FROM "{PRICE_HISTORY_V2_TABLE}"').fetchone()[0])
            latest_price_date = connection.execute(
                f'SELECT MAX(price_date) FROM "{PRICE_HISTORY_V2_TABLE}"'
            ).fetchone()[0]
    except sqlite3.Error as exc:
        return RuntimeDatabaseStatus(
            configured_path=db_path,
            env_var_name=APP_DB_ENV_VAR,
            env_override_active=env_override_active,
            exists=True,
            readable=False,
            price_history_v2_table_exists=False,
            row_count=None,
            latest_price_date=None,
            error=str(exc),
        )
    return RuntimeDatabaseStatus(
        configured_path=db_path,
        env_var_name=APP_DB_ENV_VAR,
        env_override_active=env_override_active,
        exists=True,
        readable=True,
        price_history_v2_table_exists=True,
        row_count=row_count,
        latest_price_date=latest_price_date,
    )
