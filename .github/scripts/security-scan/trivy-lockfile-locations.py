#!/usr/bin/env python3
"""Give Trivy pnpm findings a source line when the lockfile match is unique."""

import argparse
import json
import re
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

_PACKAGE_DECLARATION = re.compile(
    r"^  (?P<key>'(?:[^']|'')*'|\"(?:[^\"\\]|\\.)*\"|[^#\s][^:]*):(?:[ \t].*)?$"
)
_PACKAGE_LINE = re.compile(r"^Package:\s*(\S.*?)\s*$", re.MULTILINE)
_VERSION_LINE = re.compile(r"^Installed Version:\s*(\S.*?)\s*$", re.MULTILINE)


def _unquote_key(key: str) -> str | None:
    if key.startswith("'") and key.endswith("'"):
        return key[1:-1].replace("''", "'")
    if key.startswith('"') and key.endswith('"'):
        try:
            value = json.loads(key)
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, str) else None
    return key.strip()


def _declaration_lines(lockfile: Path, package: str, version: str) -> list[tuple[int, int]]:
    try:
        lines = lockfile.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return []

    in_packages = False
    matches: list[tuple[int, int]] = []
    selector = f"{package}@{version}"
    for line_number, line in enumerate(lines, start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line[0].isspace():
            if in_packages:
                break
            in_packages = line == "packages:"
            continue
        if not in_packages:
            continue
        if "\t" in line:
            return []
        declaration = _PACKAGE_DECLARATION.fullmatch(line)
        if declaration is None:
            continue
        key = _unquote_key(declaration.group("key"))
        if key == selector or (
            key is not None and key.startswith(f"{selector}(") and key.endswith(")")
        ):
            matches.append((line_number, len(line) + 1))
    return matches


def _safe_lockfile(scan_root: Path, uri: object) -> Path | None:
    if not isinstance(uri, str) or "\\" in uri:
        return None
    try:
        parsed = urlsplit(uri)
        decoded_path = unquote(parsed.path, errors="strict")
    except (UnicodeDecodeError, ValueError):
        return None
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        return None
    relative = PurePosixPath(decoded_path)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        return None
    if relative.name != "pnpm-lock.yaml":
        return None

    try:
        root = scan_root.resolve(strict=True)
        candidate = (root / Path(*relative.parts)).resolve(strict=True)
        candidate.relative_to(root)
    except (OSError, ValueError):
        return None
    return candidate if candidate.is_file() else None


def _finding_identity(result: object) -> tuple[str, str] | None:
    if not isinstance(result, dict):
        return None
    text = (
        result.get("message", {}).get("text") if isinstance(result.get("message"), dict) else None
    )
    if not isinstance(text, str):
        return None
    packages = _PACKAGE_LINE.findall(text)
    versions = _VERSION_LINE.findall(text)
    if len(packages) != 1 or len(versions) != 1:
        return None
    package, version = packages[0].strip(), versions[0].strip()
    if not package or not version:
        return None
    return package, version


def _is_trivy_placeholder(region: object) -> bool:
    return isinstance(region, dict) and all(
        type(region.get(key)) is int and region[key] == 1
        for key in ("startLine", "startColumn", "endLine", "endColumn")
    )


def enrich_report(report: dict, scan_root: Path) -> None:
    """Normalize ROOTPATH and enrich only unambiguous pnpm placeholder locations."""
    source_uri = scan_root.resolve().as_uri() + "/"
    for run in report["runs"]:
        run["originalUriBaseIds"]["ROOTPATH"]["uri"] = source_uri
        for result in run.get("results", []):
            if not isinstance(result, dict):
                continue
            identity = _finding_identity(result)
            if identity is None:
                continue
            package, version = identity
            locations = result.get("locations", [])
            if not isinstance(locations, list):
                continue
            for location in locations:
                if not isinstance(location, dict):
                    continue
                physical = location.get("physicalLocation", {})
                if not isinstance(physical, dict):
                    continue
                artifact = physical.get("artifactLocation", {})
                if not isinstance(artifact, dict):
                    continue
                if artifact.get("uriBaseId") != "ROOTPATH":
                    continue
                region = physical.get("region")
                if not _is_trivy_placeholder(region):
                    continue
                lockfile = _safe_lockfile(scan_root, artifact.get("uri"))
                if lockfile is None:
                    continue
                matches = _declaration_lines(lockfile, package, version)
                if len(matches) != 1:
                    continue
                line, end_column = matches[0]
                region.update(
                    startLine=line,
                    startColumn=1,
                    endLine=line,
                    endColumn=end_column,
                )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sarif", type=Path)
    parser.add_argument("scan_root", type=Path)
    args = parser.parse_args()

    report = json.loads(args.sarif.read_text(encoding="utf-8"))
    enrich_report(report, args.scan_root)
    args.sarif.write_text(json.dumps(report) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
