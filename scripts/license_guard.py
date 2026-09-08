#!/usr/bin/env python3
"""Fail CI on dependency licenses that require explicit legal review.

This is deliberately conservative. It is an engineering guardrail, not legal
advice. The authoritative source is always each dependency's upstream license
text.
"""

from __future__ import annotations

import json
import re
import subprocess


DENY_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("AGPL", re.compile(r"\bAGPL\b|GNU AFFERO", re.I)),
    ("SSPL", re.compile(r"\bSSPL\b|SERVER SIDE PUBLIC LICENSE", re.I)),
    ("BUSL/BSL", re.compile(r"BUSINESS SOURCE LICENSE|\bBUSL\b|\bBSL[- ]?1\.1\b", re.I)),
    ("Elastic License", re.compile(r"ELASTIC LICENSE", re.I)),
    ("Commons Clause", re.compile(r"COMMONS CLAUSE", re.I)),
    ("Non-commercial", re.compile(r"NON[- ]COMMERCIAL|NC[- ]ONLY", re.I)),
]

GPL_PATTERN = re.compile(r"\bGPL(?:-|\b)|GNU GENERAL PUBLIC LICENSE", re.I)
LGPL_PATTERN = re.compile(r"\bLGPL\b|GNU LESSER GENERAL PUBLIC LICENSE", re.I)
MPL_PATTERN = re.compile(r"\bMPL\b|MOZILLA PUBLIC LICENSE", re.I)

AMBIGUOUS = {"", "UNKNOWN", "N/A", "NONE", "NOASSERTION"}


def collect() -> list[dict]:
    proc = subprocess.run(
        ["pip-licenses", "--format=json", "--with-urls"],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(proc.stdout)


def main() -> int:
    packages = collect()
    blocked: list[tuple[str, str, str]] = []
    review: list[tuple[str, str, str]] = []

    for item in packages:
        name = str(item.get("Name", ""))
        version = str(item.get("Version", ""))
        license_text = str(item.get("License", "")).strip()

        for label, pattern in DENY_PATTERNS:
            if pattern.search(license_text):
                blocked.append((name, version, f"{label}: {license_text}"))

        if GPL_PATTERN.search(license_text) and not LGPL_PATTERN.search(license_text):
            blocked.append((name, version, f"GPL: {license_text}"))

        if LGPL_PATTERN.search(license_text):
            review.append((name, version, f"LGPL review: {license_text}"))
        if MPL_PATTERN.search(license_text):
            review.append((name, version, f"MPL review: {license_text}"))
        if license_text.upper() in AMBIGUOUS:
            review.append((name, version, license_text or "<empty>"))

    print(f"Audited {len(packages)} installed Python packages.")

    if review:
        print("\nREVIEW REQUIRED / METADATA CHECK:")
        for row in sorted(set(review)):
            print(f"  - {row[0]} {row[1]}: {row[2]}")

    if blocked:
        print("\nBLOCKED LICENSES DETECTED:")
        for row in sorted(set(blocked)):
            print(f"  - {row[0]} {row[1]}: {row[2]}")
        print("\nDependency merge blocked pending explicit legal/license review.")
        return 1

    print("\nNo deny-listed dependency licenses detected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
