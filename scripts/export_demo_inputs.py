"""Export the reproducible demo sensor packets and separate reference values."""

import argparse
import json
from pathlib import Path
from fairway_fusion.demo.fixtures import fixtures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True)
    references = {}
    for name, (packet, truth) in fixtures().items():
        (args.directory / f"{name}.sensor.json").write_text(
            json.dumps(packet, indent=2) + "\n", encoding="utf-8"
        )
        references[name] = truth
    (args.directory / "synthetic_reference.json").write_text(
        json.dumps(references, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Wrote four sensor packets and independent references to {args.directory}")


if __name__ == "__main__":
    main()
