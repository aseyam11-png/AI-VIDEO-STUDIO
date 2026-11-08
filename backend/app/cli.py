import argparse
import json

from app.services.legacy_import import import_legacy_manifests


def main() -> None:
    parser = argparse.ArgumentParser(description="AI Video Studio maintenance commands")
    parser.add_argument(
        "command",
        choices=("import-legacy-manifests",),
        help="Import Phase 0.5 project and asset JSON manifests into the database.",
    )
    args = parser.parse_args()
    if args.command == "import-legacy-manifests":
        print(json.dumps(import_legacy_manifests(), indent=2))


if __name__ == "__main__":
    main()
