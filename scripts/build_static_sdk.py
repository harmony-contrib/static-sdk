#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from static_sdk.builder import SourceTree, StaticSdkBuilder, TARGETS, supported_targets


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a static-only OpenHarmony ArkTS SDK archive"
    )
    parser.add_argument("--source", type=Path, required=True, help="OpenHarmony source root")
    parser.add_argument("--target", choices=tuple(supported_targets()), required=True)
    parser.add_argument(
        "--dist",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "dist",
        help="artifact output directory",
    )
    parser.add_argument(
        "--download-prebuilts",
        action="store_true",
        help="run build/prebuilts_download.sh before building",
    )
    parser.add_argument(
        "--keep-overlay",
        action="store_true",
        help="keep generated product files in the disposable source tree for debugging",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="validate inputs and print the build command without compiling",
    )
    mode.add_argument(
        "--graph-only",
        action="store_true",
        help="generate GN and run a Ninja dry run without compiling outputs",
    )
    args = parser.parse_args()

    builder = StaticSdkBuilder(
        source=SourceTree(args.source),
        target=TARGETS[args.target],
        dist=args.dist,
        download_prebuilts=args.download_prebuilts,
        keep_overlay=args.keep_overlay,
        dry_run=args.dry_run,
        graph_only=args.graph_only,
    )
    try:
        artifact = builder.run()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    if artifact is not None:
        print(f"Published {artifact}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
