"""Update integrity metadata after reviewing CoreIoT artifact changes."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "implementation/coreiot"


def main():
    checksums = {
        path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for folder in ("profiles", "rule_chains", "dashboard_actions")
        for path in sorted((ROOT / folder).rglob("*"))
        if path.is_file()
    }
    target = ROOT / "manifests/artifact_checksums.json"
    target.write_text(json.dumps({"sha256": checksums}, indent=2) + "\n", encoding="utf-8")
    print(f"Updated checksums for {len(checksums)} reviewed artifacts")


if __name__ == "__main__":
    main()
