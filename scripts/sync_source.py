#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


DEFAULT_MANIFEST_URL = "https://gitcode.com/openharmony/manifest.git"
DEFAULT_REVISION = "OpenHarmony-7.0-Release"

# Source projects required by the verified OpenHarmony API 26 ets/static SDK
# graph.  Keep this list explicit: invoking `repo sync` without project paths
# checks out every project in the product manifest, including applications,
# device products, kernels, and services that cannot contribute to the SDK
# archive.  These 72 projects are the source/metadata closure used by the
# successful Darwin arm64 build; direct GN/Ninja edges still decide which
# targets inside them are compiled.
ETS_STATIC_SOURCE_PROJECTS = (
    "arkcompiler/ets_frontend",
    "arkcompiler/ets_runtime",
    "arkcompiler/runtime_core",
    "arkcompiler/toolchain",
    "base/customization/config_policy",
    "base/global/system_resources",
    "base/hiviewdfx/faultloggerd",
    "base/hiviewdfx/hichecker",
    "base/hiviewdfx/hicollie",
    "base/hiviewdfx/hilog",
    "base/hiviewdfx/hilog_lite",
    "base/hiviewdfx/hisysevent",
    "base/hiviewdfx/hitrace",
    "base/hiviewdfx/hiview",
    "base/notification/eventhandler",
    "base/security/access_token",
    "base/security/code_signature",
    "base/security/selinux_adapter",
    "base/startup/appspawn",
    "base/startup/hvb",
    "base/startup/init",
    "build",
    "commonlibrary/c_utils",
    "commonlibrary/ets_utils",
    "commonlibrary/rust/ylong_runtime",
    "developtools/ace_ets2bundle",
    "developtools/profiler",
    "drivers/hdf_core",
    "drivers/interface",
    "foundation/ability/ability_base",
    "foundation/ability/idl_tool",
    "foundation/arkui/napi",
    "foundation/bundlemanager/bundle_framework",
    "foundation/communication/ipc",
    "foundation/filemanagement/storage_service",
    "foundation/resourceschedule/ffrt",
    "foundation/resourceschedule/qos_manager",
    "foundation/resourceschedule/resource_schedule_service",
    "foundation/systemabilitymgr/safwk",
    "foundation/systemabilitymgr/samgr",
    "interface/sdk-js",
    "kernel/linux/patches",
    "productdefine/common",
    "third_party/FreeBSD",
    "third_party/PyYAML",
    "third_party/abseil-cpp",
    "third_party/bounds_checking_function",
    "third_party/cJSON",
    "third_party/elfio",
    "third_party/flatbuffers",
    "third_party/googletest",
    "third_party/icu",
    "third_party/jinja2",
    "third_party/json",
    "third_party/jsoncpp",
    "third_party/libuv",
    "third_party/libxml2",
    "third_party/lzma",
    "third_party/markupsafe",
    "third_party/mbedtls",
    "third_party/musl",
    "third_party/node",
    "third_party/openssl",
    "third_party/pcre2",
    "third_party/protobuf",
    "third_party/rust/crates/cxx",
    "third_party/rust/crates/libc",
    "third_party/selinux",
    "third_party/typescript",
    "third_party/vixl",
    "third_party/zlib",
    "vendor/ohemu",
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create or update a disposable OpenHarmony source checkout"
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--revision", default=DEFAULT_REVISION)
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
        [
            repo,
            "sync",
            "-c",
            f"-j{args.jobs}",
            "--fail-fast",
            "--no-tags",
            *ETS_STATIC_SOURCE_PROJECTS,
        ],
        cwd=source,
        check=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
