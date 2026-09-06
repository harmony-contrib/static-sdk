#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


DEFAULT_MANIFEST_URL = "https://gitee.com/openharmony/manifest.git"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create or update a disposable OpenHarmony source checkout"
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--revision", default="master")
    parser.add_argument("--manifest-url", default=DEFAULT_MANIFEST_URL)
    parser.add_argument("--jobs", type=int, default=max(1, os.cpu_count() or 1))
    args = parser.parse_args()

    repo = shutil.which("repo")
    if repo is None:
        print(
            "error: repo launcher not found; install the Python 3 repo-py3 launcher documented in README.md",
            file=sys.stderr,
        )
        return 1

    source = args.source.resolve()
    source.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            repo,
            "init",
            "-u",
            args.manifest_url,
            "-b",
            args.revision,
            "--no-repo-verify",
        ],
        cwd=source,
        check=True,
    )
    subprocess.run(
        [repo, "sync", "-c", f"-j{args.jobs}", "--fail-fast"],
        cwd=source,
        check=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
