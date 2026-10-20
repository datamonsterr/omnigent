"""``omnigent accounts`` — manage the harness subscription account inventory.

The account inventory lives in the account pool store (the server database),
so these commands talk to the same rows the web UI, the mobile clients, and
the session-create path read.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import click

#: Credential home kinds an account may carry.
HOME_KINDS = ("codex_home", "gemini_dir", "generic")


def _load_config(path: str | None) -> dict[str, Any]:
    """Load a server config file into a mapping.

    :param path: The config file path, or ``None``.
    :returns: The parsed mapping, or an empty dict when there is no file.
    """
    if not path:
        return {}
    import yaml

    with open(path, encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return data if isinstance(data, dict) else {}


def _resolve_db_uri(config_path: str | None, db_uri: str | None) -> str:
    """Resolve the database URI the account inventory lives in.

    :param config_path: The server config file to read ``database_uri``
        from, or ``None``.
    :param db_uri: An explicit database URI that wins over the config file.
    :returns: The database URI.
    """
    if db_uri:
        return db_uri
    config = _load_config(config_path)
    configured = config.get("database_uri")
    if isinstance(configured, str) and configured.strip():
        return configured.strip()
    from omnigent.cli import _default_db_uri

    return _default_db_uri()


def _open_store(config_path: str | None, db_uri: str | None) -> Any:
    """Open the account pool store for a command.

    :param config_path: The server config file, or ``None``.
    :param db_uri: An explicit database URI, or ``None``.
    :returns: The account pool store.
    """
    from omnigent.stores.account_pool_store.sqlalchemy_store import (
        SqlAlchemyAccountPoolStore,
    )

    return SqlAlchemyAccountPoolStore(_resolve_db_uri(config_path, db_uri))


@click.group("accounts")
def accounts_cli() -> None:
    """Manage the harness subscription account pools."""


@accounts_cli.command("list")
@click.option("--config", "config_path", default=None, help="Server config file to read.")
@click.option("--db-uri", default=None, help="Database URI of the account inventory.")
def accounts_list(config_path: str | None, db_uri: str | None) -> None:
    """List the configured account pools and their accounts."""
    store = _open_store(config_path, db_uri)
    pools = store.list_pools()
    if not pools:
        click.echo("No account pools configured.")
        return
    for pool in pools:
        click.echo(f"{pool.name}  (harness: {pool.harness}, id: {pool.id})")
        if not pool.accounts:
            click.echo("  (no accounts)")
            continue
        for account in pool.accounts:
            state = "enabled" if account.enabled else "disabled"
            click.echo(f"  {account.id}  [{state}]  {account.home_kind}: {account.home_path}")


@accounts_cli.command("pool")
@click.option("--name", required=True, help="Pool name, unique per workspace.")
@click.option("--harness", required=True, help="Harness family the pool serves.")
@click.option("--description", default=None, help="Optional description.")
@click.option("--config", "config_path", default=None, help="Server config file to read.")
@click.option("--db-uri", default=None, help="Database URI of the account inventory.")
def accounts_pool(
    name: str,
    harness: str,
    description: str | None,
    config_path: str | None,
    db_uri: str | None,
) -> None:
    """Create an account pool."""
    store = _open_store(config_path, db_uri)
    pool = store.create_pool(uuid.uuid4().hex, name.strip(), harness.strip(), description)
    click.echo(f"Created pool {pool.name!r} (id: {pool.id}) for harness {pool.harness!r}.")


@accounts_cli.command("add")
@click.option("--pool", "pool_name", required=True, help="Pool the account belongs to.")
@click.option("--id", "account_id", required=True, help="Account id, unique per workspace.")
@click.option("--home", "home_path", required=True, help="Credential home directory.")
@click.option(
    "--kind",
    "home_kind",
    type=click.Choice(HOME_KINDS),
    default="generic",
    help="Which credential home the path is.",
)
@click.option("--disabled", is_flag=True, help="Create the account disabled.")
@click.option("--config", "config_path", default=None, help="Server config file to read.")
@click.option("--db-uri", default=None, help="Database URI of the account inventory.")
def accounts_add(
    pool_name: str,
    account_id: str,
    home_path: str,
    home_kind: str,
    disabled: bool,
    config_path: str | None,
    db_uri: str | None,
) -> None:
    """Add an account to a pool."""
    store = _open_store(config_path, db_uri)
    pool = store.get_pool_by_name(pool_name.strip())
    if pool is None:
        raise click.ClickException(f"No account pool named {pool_name!r}.")
    resolved = str(Path(home_path).expanduser().resolve())
    account = store.create_account(
        account_id.strip(),
        pool.id,
        resolved,
        home_kind=home_kind,
        enabled=not disabled,
    )
    click.echo(f"Added account {account.id!r} to pool {pool.name!r} -> {account.home_path}")


@accounts_cli.command("enable")
@click.argument("account_id")
@click.option("--disabled", is_flag=True, help="Disable the account instead of enabling it.")
@click.option("--config", "config_path", default=None, help="Server config file to read.")
@click.option("--db-uri", default=None, help="Database URI of the account inventory.")
def accounts_enable(
    account_id: str,
    disabled: bool,
    config_path: str | None,
    db_uri: str | None,
) -> None:
    """Enable or disable an account."""
    store = _open_store(config_path, db_uri)
    account = store.get_account(account_id)
    if account is None:
        raise click.ClickException(f"No account with id {account_id!r}.")
    updated = store.update_account(account_id, enabled=not disabled)
    click.echo(f"Account {updated.id!r} is now {'disabled' if disabled else 'enabled'}.")


@accounts_cli.command("remove")
@click.argument("account_id")
@click.option("--config", "config_path", default=None, help="Server config file to read.")
@click.option("--db-uri", default=None, help="Database URI of the account inventory.")
def accounts_remove(account_id: str, config_path: str | None, db_uri: str | None) -> None:
    """Remove an account from its pool."""
    store = _open_store(config_path, db_uri)
    if not store.delete_account(account_id):
        raise click.ClickException(f"No account with id {account_id!r}.")
    click.echo(f"Removed account {account_id!r}.")


@accounts_cli.command("import")
@click.option(
    "--config",
    "config_path",
    required=True,
    help="Server config file holding the legacy 'account_pools:' block.",
)
@click.option("--db-uri", default=None, help="Database URI of the account inventory.")
def accounts_import(config_path: str, db_uri: str | None) -> None:
    """Import the legacy 'account_pools:' config block into the database.

    The import is idempotent: an account that already exists with the same
    id, home path, and enabled flag is left untouched.
    """
    import yaml

    from omnigent.account_pools.import_legacy import import_account_pools_block

    with open(config_path, encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    if not isinstance(raw, dict):
        raise click.ClickException(f"{config_path} is not a mapping.")

    store = _open_store(config_path, db_uri)
    report = import_account_pools_block(store, raw.get("account_pools"))
    for pool_name, outcome in report.pools.items():
        click.echo(
            f"{pool_name}: {outcome.created} created, {outcome.skipped} skipped, "
            f"{len(outcome.dropped_keys)} dropped secret key(s)"
        )
    if report.errors:
        for error in report.errors:
            click.echo(f"error: {error}", err=True)
        raise click.ClickException(f"{len(report.errors)} pool(s) could not be imported.")
    click.echo(f"Imported {report.total_created} account(s), skipped {report.total_skipped}.")


__all__ = ["accounts_cli"]
