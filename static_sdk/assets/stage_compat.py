#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compile and stage the libarkts @koalaui/compat dependency"
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    source = args.source.resolve()
    output = args.output.resolve()
    subprocess.run(["npm", "run", "compile"], cwd=source, check=True)

    source_build = source / "build"
    if not (source_build / "src/index.js").is_file():
        raise RuntimeError(f"compat compilation did not produce {source_build}")

    shutil.rmtree(output, ignore_errors=True)
    shutil.copytree(source_build, output / "build")
    shutil.copy2(source / "package.json", output / "package.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
