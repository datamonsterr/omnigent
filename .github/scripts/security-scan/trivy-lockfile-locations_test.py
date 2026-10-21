import copy
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).with_name("trivy-lockfile-locations.py")
SPEC = importlib.util.spec_from_file_location("trivy_lockfile_locations", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def sarif_result(uri: str, package: str, version: str) -> dict:
    return {
        "ruleId": "CVE-2026-93687",
        "level": "error",
        "message": {"text": f"Package: {package}\nInstalled Version: {version}\nSeverity: HIGH"},
        "locations": [
            {
                "physicalLocation": {
                    "artifactLocation": {"uri": uri, "uriBaseId": "ROOTPATH"},
                    "region": {
                        "startLine": 1,
                        "startColumn": 1,
                        "endLine": 1,
                        "endColumn": 1,
                    },
                }
            }
        ],
        "partialFingerprints": {"primaryLocationLineHash": "keep-me"},
    }


def sarif_report(*results: dict) -> dict:
    return {
        "version": "2.1.0",
        "runs": [
            {
                "originalUriBaseIds": {"ROOTPATH": {"uri": "file:///old/"}},
                "results": list(results),
            }
        ],
    }


def lockfile(*, include_braces: bool = True) -> str:
    entries = [
        "lockfileVersion: '9.0'",
        "packages:",
    ]
    if include_braces:
        entries += ["  braces@3.0.3:", "    resolution: {integrity: sha512-test}"]
    entries += [
        "  '@scope/pkg@2.4.1':",
        "    resolution: {integrity: sha512-test}",
        "snapshots:",
        "  braces@3.0.3:",
        "    transitivePeerDependencies: []",
        "  '@scope/pkg@2.4.1': {}",
    ]
    return "\n".join(entries) + "\n"


def region(result: dict) -> dict:
    return result["locations"][0]["physicalLocation"]["region"]


def test_enriches_unique_package_declarations_and_preserves_findings(tmp_path):
    (tmp_path / "pnpm-lock.yaml").write_text(lockfile())
    braces = sarif_result("pnpm-lock.yaml", "braces", "3.0.3")
    scoped = sarif_result("pnpm-lock.yaml", "@scope/pkg", "2.4.1")
    report = sarif_report(braces, scoped)
    original_non_location = copy.deepcopy(report)

    MODULE.enrich_report(report, tmp_path)

    assert region(braces) == {
        "startLine": 3,
        "startColumn": 1,
        "endLine": 3,
        "endColumn": 16,
    }
    assert region(scoped) == {
        "startLine": 5,
        "startColumn": 1,
        "endLine": 5,
        "endColumn": 22,
    }
    assert len(report["runs"][0]["results"]) == 2
    assert [result["level"] for result in report["runs"][0]["results"]] == [
        "error",
        "error",
    ]
    assert [result["partialFingerprints"] for result in report["runs"][0]["results"]] == [
        {"primaryLocationLineHash": "keep-me"},
        {"primaryLocationLineHash": "keep-me"},
    ]
    assert report["runs"][0]["originalUriBaseIds"]["ROOTPATH"]["uri"] == (
        tmp_path.resolve().as_uri() + "/"
    )
    for current, original in zip(
        report["runs"][0]["results"],
        original_non_location["runs"][0]["results"],
        strict=True,
    ):
        current_location = current.pop("locations")
        original.pop("locations")
        assert current == original
        assert len(current_location) == 1


@pytest.mark.parametrize(
    ("package", "version", "contents"),
    [
        ("missing", "1.0.0", lockfile()),
        (
            "braces",
            "3.0.3",
            "packages:\n  braces@3.0.3:\n    resolution: {}\n"
            "  'braces@3.0.3(peer@1.0.0)':\n    resolution: {}\nsnapshots:\n",
        ),
    ],
)
def test_missing_or_ambiguous_match_keeps_placeholder(tmp_path, package, version, contents):
    (tmp_path / "pnpm-lock.yaml").write_text(contents)
    result = sarif_result("pnpm-lock.yaml", package, version)

    MODULE.enrich_report(sarif_report(result), tmp_path)

    assert region(result) == {
        "startLine": 1,
        "startColumn": 1,
        "endLine": 1,
        "endColumn": 1,
    }


def test_does_not_read_lockfile_outside_scan_root(tmp_path):
    scan_root = tmp_path / "scan"
    outside = tmp_path / "outside"
    scan_root.mkdir()
    outside.mkdir()
    (outside / "pnpm-lock.yaml").write_text(lockfile())
    (scan_root / "pnpm-lock.yaml").symlink_to(outside / "pnpm-lock.yaml")
    result = sarif_result("pnpm-lock.yaml", "braces", "3.0.3")

    MODULE.enrich_report(sarif_report(result), scan_root)

    assert region(result)["startLine"] == 1


@pytest.mark.parametrize(
    "uri",
    [
        "../outside/pnpm-lock.yaml",
        "%2e%2e/outside/pnpm-lock.yaml",
        "/tmp/pnpm-lock.yaml",
        "https://example.test/pnpm-lock.yaml",
    ],
)
def test_unsafe_or_external_artifact_uri_keeps_placeholder(tmp_path, uri):
    (tmp_path / "pnpm-lock.yaml").write_text(lockfile())
    result = sarif_result(uri, "braces", "3.0.3")

    MODULE.enrich_report(sarif_report(result), tmp_path)

    assert region(result)["startLine"] == 1


def test_keeps_existing_non_default_region(tmp_path):
    (tmp_path / "pnpm-lock.yaml").write_text(lockfile())
    result = sarif_result("pnpm-lock.yaml", "braces", "3.0.3")
    region(result).update(startLine=42, startColumn=8, endLine=42, endColumn=18)
    expected = copy.deepcopy(region(result))

    MODULE.enrich_report(sarif_report(result), tmp_path)

    assert region(result) == expected


def test_malformed_lockfile_keeps_placeholder(tmp_path):
    (tmp_path / "pnpm-lock.yaml").write_text("packages:\n\tbraces@3.0.3:\n")
    result = sarif_result("pnpm-lock.yaml", "braces", "3.0.3")

    MODULE.enrich_report(sarif_report(result), tmp_path)

    assert region(result)["startLine"] == 1


def test_dev_tools_rootpath_uses_scanned_subdirectory(tmp_path):
    dev_tools = tmp_path / "dev"
    dev_tools.mkdir()
    (dev_tools / "pnpm-lock.yaml").write_text(lockfile())
    result = sarif_result("pnpm-lock.yaml", "braces", "3.0.3")
    report = sarif_report(result)

    MODULE.enrich_report(report, dev_tools)

    assert region(result)["startLine"] == 3
    assert report["runs"][0]["originalUriBaseIds"]["ROOTPATH"]["uri"] == (
        dev_tools.resolve().as_uri() + "/"
    )


def test_malformed_location_is_left_unchanged(tmp_path):
    result = sarif_result("pnpm-lock.yaml", "braces", "3.0.3")
    result["locations"] = ["not a location"]
    report = sarif_report(result)

    MODULE.enrich_report(report, tmp_path)

    assert result["locations"] == ["not a location"]
