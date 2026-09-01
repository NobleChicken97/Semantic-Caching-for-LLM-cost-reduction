"""Convert a pip-audit JSON report into a minimal SARIF 2.1.0 file.

Why this exists: pip-audit (as of 2.10.x) cannot emit SARIF directly, but
GitHub code scanning ingests SARIF — so the CI security-audit job runs
pip-audit with ``--format json`` and pipes it through this script.

Design goals:
  * Never crash CI: any unreadable/empty input yields a valid empty-SARIF
    document so the upload step always has a file.
  * Every vulnerability becomes a code-scanning alert (level "warning")
    carrying the package, installed version, CVE/GHSA ids and fix versions
    in its message — nothing is filtered or suppressed.

Usage:
    python pip_audit_to_sarif.py <pip-audit.json> <output.sarif>
"""

from __future__ import annotations

import json
import sys

EMPTY_SARIF = {
    "version": "2.1.0",
    "$schema": "http://json.schemastore.org/sarif-2.1.0.json",
    "runs": [],
}


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2

    src_path, dst_path = argv[1], argv[2]

    try:
        with open(src_path, encoding="utf-8") as f:
            report = json.load(f)
    except (OSError, ValueError) as exc:
        print(f"[pip_audit_to_sarif] unreadable input ({exc}); emitting empty SARIF")
        write(dst_path, EMPTY_SARIF)
        return 0

    deps = report.get("dependencies", []) if isinstance(report, dict) else []

    rules: dict[str, dict] = {}
    results: list[dict] = []

    for dep in deps:
        name = dep.get("name", "unknown-package")
        version = dep.get("version", "?")
        for vuln in dep.get("vulns", []):
            vuln_id = vuln.get("id", "UNKNOWN")
            fixes = ", ".join(vuln.get("fix_versions", [])) or "no fix published"
            aliases = " ".join(vuln.get("aliases", []))
            desc = (vuln.get("description") or "").strip()
            if len(desc) > 400:
                desc = desc[:397] + "..."

            msg = f"{name}=={version} is affected by {vuln_id}"
            if aliases:
                msg += f" ({aliases})"
            msg += f". Fixed in: {fixes}."
            if desc:
                msg += f" {desc}"

            if vuln_id not in rules:
                rule = {
                    "id": vuln_id,
                    "shortDescription": {"text": f"{vuln_id} affects {name}"},
                    "helpUri": (f"https://osv.dev/vulnerability/{vuln_id}"),
                }
                if desc:
                    rule["fullDescription"] = {"text": desc}
                rules[vuln_id] = rule

            results.append(
                {
                    "ruleId": vuln_id,
                    "level": "warning",
                    "message": {"text": msg},
                    "locations": [
                        {
                            # A dependency finding has no source location;
                            # code scanning requires a physicalLocation stub.
                            "physicalLocation": {
                                "artifactLocation": {"uri": "requirements.txt"}
                            }
                        }
                    ],
                }
            )

    sarif = {
        "version": "2.1.0",
        "$schema": "http://json.schemastore.org/sarif-2.1.0.json",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "pip-audit",
                        "informationUri": "https://github.com/pypa/pip-audit",
                        "version": "2.10.1",
                        "rules": sorted(rules.values(), key=lambda r: r["id"]),
                    }
                },
                "results": results,
            }
        ],
    }

    write(dst_path, sarif)
    print(
        f"[pip_audit_to_sarif] wrote {dst_path}: "
        f"{len(results)} finding(s) across {len(rules)} unique vulnerability id(s)"
    )
    return 0


def write(path: str, doc: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
