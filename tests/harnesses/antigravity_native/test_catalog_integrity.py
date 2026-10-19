"""A picker cannot reuse models from another launch configuration."""

import os
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from omnigent.harnesses.antigravity_native import catalog


@pytest.fixture(autouse=True)
def isolated_cache():
    catalog.clear_catalog_cache()
    yield
    catalog.clear_catalog_cache()


def test_two_binaries_with_equal_timestamps_have_distinct_catalogs(tmp_path, monkeypatch):
    first, second = tmp_path / "one/agy", tmp_path / "two/agy"
    for path in (first, second):
        path.parent.mkdir()
        path.write_bytes(b"same-size")
        os.utime(path, ns=(1_000_000_000, 1_000_000_000))
    run = Mock(
        side_effect=[
            SimpleNamespace(stdout="model-a\tModel A\n"),
            SimpleNamespace(stdout="model-b\tModel B\n"),
        ]
    )
    monkeypatch.setattr(catalog.subprocess, "run", run)
    catalog.probe_agy_model_catalog(agy_path=str(first))
    rows = catalog.probe_agy_model_catalog(agy_path=str(second))
    assert rows[0]["id"] == "model-b"
    assert run.call_count == 2


def test_failed_probe_after_account_change_cannot_serve_previous_account(tmp_path, monkeypatch):
    binary = tmp_path / "agy"
    binary.touch()
    token = tmp_path / "token"
    token.write_text("synthetic account one")
    monkeypatch.setattr(catalog, "_LINUX_OAUTH_TOKEN", token)
    run = Mock(
        side_effect=[
            SimpleNamespace(stdout="account-one-model\tAccount One\n"),
            subprocess.CalledProcessError(1, "agy", stderr="account two upstream unavailable"),
        ]
    )
    monkeypatch.setattr(catalog.subprocess, "run", run)
    catalog.probe_agy_model_catalog(agy_path=str(binary))
    os.utime(token, ns=(2_000_000_000, 2_000_000_000))
    assert catalog.probe_agy_model_catalog(agy_path=str(binary)) is None


def test_returned_rows_cannot_mutate_cached_catalog(tmp_path, monkeypatch):
    binary = tmp_path / "agy"
    binary.touch()
    run = Mock(return_value=SimpleNamespace(stdout="real-model\tReal Model\n"))
    monkeypatch.setattr(catalog.subprocess, "run", run)
    catalog.probe_agy_model_catalog(agy_path=str(binary))[0]["id"] = "invented-model"
    assert catalog.probe_agy_model_catalog(agy_path=str(binary))[0]["id"] == "real-model"


def test_listing_does_not_invent_a_default_or_log_malformed_content(caplog):
    rows = catalog._parse_agy_models_output("secret-value-no-tab\nactual-model\tActual Model\n")
    assert not any(row.get("isDefault") for row in rows)
    assert "secret-value-no-tab" not in caplog.text
