from __future__ import annotations

import gzip
import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import zipfile
import xml.etree.ElementTree as ElementTree
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Iterable, Sequence


PRODUCT_NAME = "arkdown-static-sdk"
PRODUCT_SELECTOR = "arkdown-static-sdk@arkdown"
DESCRIPTION_RELATIVE_PATH = ".arkdown-static-sdk/ohos_sdk_description_static.json"
INNER_KITS_ALLOWLIST_RELATIVE_PATH = (
    ".arkdown-static-sdk/inner_kits_allowlist.json"
)
PRODUCT_RELATIVE_PATH = "vendor/arkdown/arkdown-static-sdk/config.json"
PRODUCT_BUNDLE_RELATIVE_PATH = "vendor/arkdown/arkdown-static-sdk/bundle.json"
PRODUCT_BUILD_RELATIVE_PATH = "vendor/arkdown/arkdown-static-sdk/BUILD.gn"
PRODUCT_COMPAT_STAGE_RELATIVE_PATH = (
    "vendor/arkdown/arkdown-static-sdk/stage_compat.py"
)
PRODUCT_BUILD_ASSET = Path(__file__).with_name("assets") / "BUILD.gn"
PRODUCT_COMPAT_STAGE_ASSET = Path(__file__).with_name("assets") / "stage_compat.py"
GENERATED_RELATIVE_DIRECTORY = ".arkdown-static-sdk/generated"
GENERATED_MODULES_RELATIVE_PATH = f"{GENERATED_RELATIVE_DIRECTORY}/ohos_sdk_modules.gni"
GENERATED_INSTALL_RELATIVE_PATH = (
    f"{GENERATED_RELATIVE_DIRECTORY}/ohos_sdk_install_paths.json"
)
NOTICE_INSTALL_RELATIVE_PATH = "out/sdk/gen/build/ohos/sdk/ohos_sdk_install_paths.json"
GENERATED_TYPES_RELATIVE_PATH = f"{GENERATED_RELATIVE_DIRECTORY}/generated_sdk_types.txt"
LIBARKTS_COMPAT_INSTALL_PATH = (
    "ets/static/build-tools/libarkts/node_modules/@koalaui/compat/"
)


@dataclass(frozen=True)
class Target:
    name: str
    sdk_platform: str
    sdk_system: str
    host_system: str
    host_machines: tuple[str, ...]
    clang_host_directory: str
    python_host_directory: str

    def verify_host(self) -> None:
        actual_system = platform.system()
        actual_machine = platform.machine().lower()
        if actual_system != self.host_system or actual_machine not in self.host_machines:
            expected = f"{self.host_system} ({', '.join(self.host_machines)})"
            actual = f"{actual_system} ({actual_machine})"
            raise RuntimeError(
                f"target {self.name} must be built on {expected}; current host is {actual}"
            )


TARGETS = {
    "linux-x64": Target(
        name="linux-x64",
        sdk_platform="linux",
        sdk_system="linux",
        host_system="Linux",
        host_machines=("x86_64", "amd64"),
        clang_host_directory="linux-x86_64",
        python_host_directory="linux-x86",
    ),
    "windows-x64": Target(
        name="windows-x64",
        sdk_platform="win",
        sdk_system="windows",
        host_system="Linux",
        host_machines=("x86_64", "amd64"),
        clang_host_directory="linux-x86_64",
        python_host_directory="linux-x86",
    ),
    "darwin-arm64": Target(
        name="darwin-arm64",
        sdk_platform="mac",
        sdk_system="darwin",
        host_system="Darwin",
        host_machines=("arm64", "aarch64"),
        clang_host_directory="darwin-arm64",
        python_host_directory="darwin-arm64",
    ),
}


class SourceTree:
    NODE_DEPENDENCY_STAMP = "node_modules/.arkdown-static-sdk-dependencies.json"

    NODE_PROJECTS = {
        "arkcompiler/ets_frontend/ets2panda/bindings": (
            "node_modules/node-api-headers/include/node_api.h",
            "node_modules/.bin/tsc",
        ),
        "arkcompiler/ets_frontend/ets2panda/driver/build_system": (
            "node_modules/.bin/tsc",
        ),
        "arkcompiler/ets_frontend/ets2panda/linter": (
            "node_modules/.bin/webpack",
            "node_modules/@types/node/package.json",
        ),
        "arkcompiler/ets_frontend/ets2panda/linter/arkanalyzer": (
            "node_modules/commander/package.json",
            "node_modules/log4js/package.json",
        ),
        "arkcompiler/ets_frontend/ets2panda/linter/homecheck": (
            "node_modules/commander/package.json",
            "node_modules/fs-extra/package.json",
        ),
        "arkcompiler/ets_frontend/arkguard": (
            "node_modules/@types/node/package.json",
            "node_modules/fs-extra/package.json",
        ),
        "arkcompiler/runtime_core/static_core/plugins/ets/tools/declgen_ts2sts": (
            "node_modules/.bin/tsc",
            "node_modules/commander/index.js",
        ),
        "developtools/ace_ets2bundle/koala-wrapper": (
            "node_modules/node-api-headers/include/node_api.h",
            "node_modules/node-addon-api/napi.h",
            "node_modules/.bin/babel",
        ),
        "developtools/ace_ets2bundle/ets1.2": (
            "node_modules/node-api-headers/include/node_api.h",
            "node_modules/node-addon-api/napi.h",
            "node_modules/.bin/rollup",
            "node_modules/.bin/tsc",
        ),
        "developtools/ace_ets2bundle/ets1.2/compat": (
            "node_modules/.bin/tsc",
        ),
        "developtools/ace_ets2bundle/arkui-plugins": (
            "node_modules/.bin/babel",
        ),
        "interface/sdk-js/build-tools": (
            "node_modules/typescript/lib/typescript.js",
            "node_modules/commander/index.js",
        ),
        "interface/sdk-js/build-tools/compile-plugins/api-check-plugin-static": (
            "node_modules/.bin/babel",
        ),
    }

    REQUIRED_PATHS = (
        "build.sh",
        "build/ohos/sdk/ohos_sdk_description_std.json",
        "build/toolchain/toolchain.gni",
        "productdefine/common/products/ohos-sdk.json",
        "vendor/ohemu/host_product/config.json",
        "arkcompiler/runtime_core",
        "arkcompiler/ets_frontend",
        "developtools/ace_ets2bundle",
        "interface/sdk-js",
    )

    REVISION_OWNERS = {
        "build": "build",
        "productdefine": "productdefine/common",
        "runtime_core": "arkcompiler/runtime_core",
        "ets_frontend": "arkcompiler/ets_frontend",
        "ace_ets2bundle": "developtools/ace_ets2bundle",
        "sdk_js": "interface/sdk-js",
    }

    CLANG_VERSION_PATTERN = re.compile(
        r'^\s*clang_version\s*=\s*"([^"]+)"', re.MULTILINE
    )

    def __init__(self, root: Path):
        self.root = root.resolve()

    def validate(self) -> None:
        missing = [item for item in self.REQUIRED_PATHS if not (self.root / item).exists()]
        if missing:
            formatted = "\n".join(f"  - {item}" for item in missing)
            raise RuntimeError(
                f"{self.root} is not a complete OpenHarmony SDK source tree; missing:\n{formatted}"
            )

    def revision(self, relative_path: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(self.root / relative_path), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    def revisions(self) -> dict[str, str]:
        return {
            name: self.revision(relative_path)
            for name, relative_path in self.REVISION_OWNERS.items()
        }

    def python_executable(self, target: Target) -> Path:
        root = self.root / "prebuilts/python" / target.python_host_directory
        candidates = sorted(root.glob("*/bin/python3"), reverse=True)
        if not candidates:
            raise RuntimeError(
                "OpenHarmony Python prebuilt is incomplete; run "
                f"build/prebuilts_download.sh first. Missing under: {root}"
            )
        return candidates[0]

    def prepare_node_dependencies(self) -> None:
        """Install npm dependencies required by the selected ETS SDK graph."""
        node_bin = self.root / "prebuilts/build-tools/common/nodejs/current/bin"
        node = node_bin / "node"
        npm = node_bin / "npm"
        missing_tools = [path for path in (node, npm) if not path.is_file()]
        if missing_tools:
            formatted = "\n".join(f"  - {path}" for path in missing_tools)
            raise RuntimeError(
                "OpenHarmony Node.js prebuilts are incomplete; run "
                "build/prebuilts_download.sh first. Missing:\n"
                f"{formatted}"
            )

        environment = os.environ.copy()
        environment["PATH"] = f"{node_bin}{os.pathsep}{environment.get('PATH', '')}"
        environment["CI"] = "true"
        for relative_path, markers in self.NODE_PROJECTS.items():
            project = self.root / relative_path
            expected_stamp = self._node_dependency_stamp(project)
            stamp_path = project / self.NODE_DEPENDENCY_STAMP
            if (
                all((project / marker).exists() for marker in markers)
                and stamp_path.is_file()
                and stamp_path.read_text(encoding="utf-8") == expected_stamp
            ):
                continue
            if not (project / "package.json").is_file():
                raise RuntimeError(
                    f"ETS npm project is missing package.json: {relative_path}"
                )
            print(f"Preparing ETS npm dependencies: {relative_path}")
            lockfile = project / "package-lock.json"
            common_arguments = ["--ignore-scripts", "--no-audit", "--no-fund"]
            if lockfile.is_file():
                original_lockfile = lockfile.read_bytes()
                try:
                    subprocess.run(
                        [str(npm), "ci", *common_arguments],
                        cwd=project,
                        env=environment,
                        check=True,
                    )
                except subprocess.CalledProcessError:
                    print(
                        "Upstream package lock is not installable with npm ci; "
                        f"using it as an npm install baseline: {relative_path}"
                    )
                    # npm ci may leave a partially linked tree when an upstream
                    # local-file dependency is inconsistent.  npm 6 cannot
                    # reliably repair that tree in place, so give the fallback
                    # install the same clean starting point.
                    shutil.rmtree(project / "node_modules", ignore_errors=True)
                    try:
                        subprocess.run(
                            [str(npm), "install", *common_arguments],
                            cwd=project,
                            env=environment,
                            check=True,
                        )
                    finally:
                        lockfile.write_bytes(original_lockfile)
                else:
                    if lockfile.read_bytes() != original_lockfile:
                        lockfile.write_bytes(original_lockfile)
                        raise RuntimeError(
                            f"npm ci unexpectedly changed package lock: {relative_path}"
                        )
            else:
                subprocess.run(
                    [str(npm), "install", *common_arguments, "--package-lock=false"],
                    cwd=project,
                    env=environment,
                    check=True,
                )
            missing = [marker for marker in markers if not (project / marker).exists()]
            if missing:
                formatted = "\n".join(f"  - {marker}" for marker in missing)
                raise RuntimeError(
                    f"npm install did not prepare {relative_path}; missing:\n{formatted}"
                )
            stamp_path.write_text(expected_stamp, encoding="utf-8")

    @staticmethod
    def _node_dependency_stamp(project: Path) -> str:
        inputs = {}
        for name in ("package.json", "package-lock.json"):
            path = project / name
            inputs[name] = (
                hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
            )
        return json.dumps(
            {"schema": 1, "inputs": inputs}, sort_keys=True, separators=(",", ":")
        )

    def clang_arguments(self, target: Target) -> tuple[str, ...]:
        toolchain_configuration = self.root / "build/toolchain/toolchain.gni"
        source = toolchain_configuration.read_text(encoding="utf-8")
        matches = self.CLANG_VERSION_PATTERN.findall(source)
        if len(matches) != 1:
            raise RuntimeError(
                "could not resolve one clang_version from "
                f"{toolchain_configuration}"
            )
        clang_version = matches[0]
        relative_base = PurePosixPath(
            "prebuilts", "clang", "ohos", target.clang_host_directory, "llvm"
        )
        base = self.root / Path(relative_base)
        required = (
            base / "bin/clang",
            base
            / "lib/clang"
            / clang_version
            / "lib/aarch64-linux-ohos/libclang_rt.builtins.a",
            base / "lib/aarch64-linux-ohos/libc++abi.a",
        )
        missing = [path for path in required if not path.is_file()]
        if missing:
            formatted = "\n".join(f"  - {path}" for path in missing)
            raise RuntimeError(
                "OpenHarmony Clang prebuilts are incomplete; run "
                "build/prebuilts_download.sh first. Missing:\n"
                f"{formatted}"
            )

        gn_base = f"//{relative_base.as_posix()}"
        runtime = f"{gn_base}/lib/clang/{clang_version}/lib/aarch64-linux-ohos"
        standard = f"{gn_base}/lib/aarch64-linux-ohos"
        return (
            f"clang_base_path={gn_base}",
            f"clang_lib_base_path={gn_base}/lib/clang/{clang_version}/lib",
            f"standard_clang_path={standard}",
            f"runtime_clang_path={runtime}",
            f"standard_ohos_clang_path={standard}",
            f"runtime_ohos_clang_path={runtime}",
        )

    def write_pinned_manifest(self, destination: Path) -> bool:
        repo_launcher = self.root / ".repo/repo/repo"
        if not repo_launcher.is_file():
            return False
        destination.parent.mkdir(parents=True, exist_ok=True)

        listed = subprocess.run(
            [sys.executable, str(repo_launcher), "list"],
            cwd=self.root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        projects = []
        for line in listed:
            relative_path, separator, name = line.partition(" : ")
            if not separator:
                raise RuntimeError(f"unexpected repo list output: {line!r}")
            projects.append((relative_path, name))

        checked_out = [
            project
            for project in projects
            if (self.root / project[0] / ".git").exists()
        ]
        if checked_out:
            self._write_partial_pinned_manifest(destination, checked_out)
            return True

        subprocess.run(
            [
                sys.executable,
                str(repo_launcher),
                "manifest",
                "-r",
                "-o",
                str(destination),
            ],
            cwd=self.root,
            check=True,
        )
        return True

    def _write_partial_pinned_manifest(
        self, destination: Path, projects: Sequence[tuple[str, str]]
    ) -> None:
        """Write a reproducible manifest for the projects present in a partial tree."""

        pinned = []
        for relative_path, name in projects:
            project_root = self.root / relative_path
            revision = self.revision(relative_path)
            remote_names = subprocess.run(
                ["git", "-C", str(project_root), "remote"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.splitlines()
            if not remote_names:
                raise RuntimeError(f"source project has no remote: {relative_path}")
            remote_name = "gitcode" if "gitcode" in remote_names else remote_names[0]
            remote_url = subprocess.run(
                ["git", "-C", str(project_root), "remote", "get-url", remote_name],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            suffixes = (f"/{name}.git", f"/{name}")
            fetch = next(
                (
                    remote_url[: -len(suffix)]
                    for suffix in suffixes
                    if remote_url.endswith(suffix)
                ),
                None,
            )
            if fetch is None:
                raise RuntimeError(
                    f"cannot derive manifest remote for {relative_path}: {remote_url}"
                )
            pinned.append((relative_path, name, revision, fetch))

        manifest = ElementTree.Element("manifest")
        remote_names_by_fetch = {
            fetch: f"remote-{index}"
            for index, fetch in enumerate(sorted({item[3] for item in pinned}))
        }
        for fetch, remote_name in remote_names_by_fetch.items():
            ElementTree.SubElement(
                manifest, "remote", {"name": remote_name, "fetch": fetch}
            )
        for relative_path, name, revision, fetch in pinned:
            ElementTree.SubElement(
                manifest,
                "project",
                {
                    "name": name,
                    "path": relative_path,
                    "remote": remote_names_by_fetch[fetch],
                    "revision": revision,
                },
            )
        ElementTree.indent(manifest, space="  ")
        ElementTree.ElementTree(manifest).write(
            destination, encoding="utf-8", xml_declaration=True
        )


class DarwinBuildCompatibility(AbstractContextManager["DarwinBuildCompatibility"]):
    """Supply host dependencies missing from the upstream Darwin prebuilts."""

    SUPPORTED_VERSION_PATTERN = re.compile(r"^(?:10|11|12|13|14)\.\d+$")
    COMPATIBLE_VERSION = "14.99"

    def __init__(self, source: SourceTree, target: Target):
        self.source = source
        self.target = target
        self._temporary_directory: tempfile.TemporaryDirectory[str] | None = None
        self._bin_directory: Path | None = None

    def __enter__(self) -> "DarwinBuildCompatibility":
        if self.target.host_system != "Darwin":
            return self

        sdk_version = subprocess.run(
            ["xcrun", "--sdk", "macosx", "--show-sdk-version"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if self.SUPPORTED_VERSION_PATTERN.fullmatch(sdk_version):
            return self

        xcrun = shutil.which("xcrun")
        xcode_select = shutil.which("xcode-select")
        if xcrun is None or xcode_select is None:
            raise RuntimeError("Xcode command-line tools are not available")

        self._temporary_directory = tempfile.TemporaryDirectory(
            prefix="arkdown-xcode-compat-"
        )
        root = Path(self._temporary_directory.name)
        developer = root / "Xcode.app/Contents/Developer"
        sdk_directory = developer / "Platforms/MacOSX.platform/Developer/SDKs"
        sdk_directory.mkdir(parents=True)
        (sdk_directory / f"MacOSX{self.COMPATIBLE_VERSION}.sdk").mkdir()

        self._bin_directory = root / "bin"
        self._bin_directory.mkdir()
        self._write_executable(
            self._bin_directory / "xcode-select",
            "#!/bin/sh\n"
            'if [ "$#" -eq 1 ] && [ "$1" = "-print-path" ]; then\n'
            f"  printf '%s\\n' {shlex.quote(str(developer))}\n"
            "  exit 0\n"
            "fi\n"
            f"exec {shlex.quote(xcode_select)} \"$@\"\n",
        )
        self._write_executable(
            self._bin_directory / "xcrun",
            "#!/bin/sh\n"
            'if [ "$#" -ge 2 ] && [ "$1" = "-sdk" ] && '
            f'[ "$2" = "macosx{self.COMPATIBLE_VERSION}" ]; then\n'
            "  shift 2\n"
            f"  exec {shlex.quote(xcrun)} -sdk macosx \"$@\"\n"
            "fi\n"
            f"exec {shlex.quote(xcrun)} \"$@\"\n",
        )
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if self._temporary_directory is not None:
            self._temporary_directory.cleanup()

    def apply(self, environment: dict[str, str]) -> None:
        if self.target.host_system != "Darwin":
            return
        bundled_python_modules = self.source.root / "third_party/PyYAML/lib"
        if not (bundled_python_modules / "yaml/__init__.py").is_file():
            raise RuntimeError(
                "OpenHarmony Darwin Python is missing PyYAML and the bundled "
                f"fallback is unavailable: {bundled_python_modules}"
            )
        existing_python_path = environment.get("PYTHONPATH", "")
        environment["PYTHONPATH"] = os.pathsep.join(
            item for item in (str(bundled_python_modules), existing_python_path) if item
        )
        if self._bin_directory is not None:
            existing_path = environment.get("PATH", "")
            environment["PATH"] = (
                f"{self._bin_directory}{os.pathsep}{existing_path}"
            )

    @staticmethod
    def _write_executable(path: Path, source: str) -> None:
        path.write_text(source, encoding="utf-8")
        path.chmod(0o755)


class Node14PackageCompatibility(AbstractContextManager["Node14PackageCompatibility"]):
    """Use shell cleanup where API 26 packages pin Node 20-only rimraf 6."""

    PACKAGE_PATCHES = {
        "arkcompiler/ets_frontend/ets2panda/bindings/package.json": (
            '"clean": "rimraf dist tsconfig.tsbuildinfo package-lock.json"',
            '"clean": "rm -rf dist tsconfig.tsbuildinfo package-lock.json"',
        ),
        "arkcompiler/ets_frontend/ets2panda/driver/build_system/package.json": (
            '"clean": "rimraf ${npm_config_outdir:-dist}"',
            '"clean": "rm -rf ${npm_config_outdir:-dist}"',
        ),
        (
            "arkcompiler/runtime_core/static_core/plugins/ets/tools/"
            "declgen_ts2sts/package.json"
        ): (
            '"clean": "rimraf build dist bundle"',
            '"clean": "rm -rf build dist bundle"',
        ),
    }

    def __init__(self, source: SourceTree):
        self.source = source
        self.original_sources: dict[Path, str] = {}
        self.original_lockfiles: dict[Path, bytes | None] = {}

    def __enter__(self) -> "Node14PackageCompatibility":
        try:
            for relative_path, (original, replacement) in self.PACKAGE_PATCHES.items():
                path = self.source.root / relative_path
                lockfile = path.with_name("package-lock.json")
                self.original_lockfiles[lockfile] = (
                    lockfile.read_bytes() if lockfile.is_file() else None
                )
                source = path.read_text(encoding="utf-8")
                if source.count(original) != 1:
                    raise RuntimeError(
                        "unexpected upstream npm clean script; refusing to apply "
                        f"Node 14 compatibility at {path}"
                    )
                self.original_sources[path] = source
                path.write_text(source.replace(original, replacement), encoding="utf-8")
            return self
        except BaseException:
            self._restore()
            raise

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self._restore()

    def _restore(self) -> None:
        for path, source in self.original_sources.items():
            path.write_text(source, encoding="utf-8")
        for path, source in self.original_lockfiles.items():
            if source is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(source)
        self.original_sources.clear()
        self.original_lockfiles.clear()


class StaticSdkOverlay(AbstractContextManager["StaticSdkOverlay"]):
    EXCLUDED_COMPONENTS = frozenset({"ace_engine"})

    COMPONENT_BUNDLES = {
        "build_framework": "build/bundle.json",
        "ability_base": "foundation/ability/ability_base/bundle.json",
        "idl_tool": "foundation/ability/idl_tool/bundle.json",
        "thirdparty_bounds_checking_function": "third_party/bounds_checking_function/bundle.json",
        "c_utils": "commonlibrary/c_utils/bundle.json",
        "ets_utils": "commonlibrary/ets_utils/bundle.json",
        "ylong_runtime": "commonlibrary/rust/ylong_runtime/bundle.json",
        "ets_frontend": "arkcompiler/ets_frontend/bundle.json",
        "ets_runtime": "arkcompiler/ets_runtime/bundle.json",
        "runtime_core": "arkcompiler/runtime_core/bundle.json",
        "toolchain": "arkcompiler/toolchain/bundle.json",
        "abseil-cpp": "third_party/abseil-cpp/bundle.json",
        "flatbuffers": "third_party/flatbuffers/bundle.json",
        "icu": "third_party/icu/bundle.json",
        "json": "third_party/json/bundle.json",
        "libuv": "third_party/libuv/bundle.json",
        "libxml2": "third_party/libxml2/bundle.json",
        "elfio": "third_party/elfio/bundle.json",
        "musl": "third_party/musl/bundle.json",
        "node": "third_party/node/bundle.json",
        "openssl": "third_party/openssl/bundle.json",
        "pcre2": "third_party/pcre2/bundle.json",
        "cJSON": "third_party/cJSON/bundle.json",
        "jsoncpp": "third_party/jsoncpp/bundle.json",
        "lzma": "third_party/lzma/bundle.json",
        "mbedtls": "third_party/mbedtls/bundle.json",
        "selinux": "third_party/selinux/bundle.json",
        "rust_cxx": "third_party/rust/crates/cxx/bundle.json",
        "rust_libc": "third_party/rust/crates/libc/bundle.json",
        "protobuf": "third_party/protobuf/bundle.json",
        "typescript": "third_party/typescript/bundle.json",
        "vixl": "third_party/vixl/bundle.json",
        "zlib": "third_party/zlib/bundle.json",
        "hilog": "base/hiviewdfx/hilog/bundle.json",
        "faultloggerd": "base/hiviewdfx/faultloggerd/bundle.json",
        "hisysevent": "base/hiviewdfx/hisysevent/bundle.json",
        "hitrace": "base/hiviewdfx/hitrace/bundle.json",
        "hicollie": "base/hiviewdfx/hicollie/bundle.json",
        "hilog_lite": "base/hiviewdfx/hilog_lite/bundle.json",
        "hiview": "base/hiviewdfx/hiview/bundle.json",
        "hichecker": "base/hiviewdfx/hichecker/bundle.json",
        "hiprofiler": "developtools/profiler/bundle.json",
        "init": "base/startup/init/bundle.json",
        "appspawn": "base/startup/appspawn/bundle.json",
        "hvb": "base/startup/hvb/bundle.json",
        "ffrt": "foundation/resourceschedule/ffrt/bundle.json",
        "qos_manager": "foundation/resourceschedule/qos_manager/bundle.json",
        "resource_schedule_service": (
            "foundation/resourceschedule/resource_schedule_service/bundle.json"
        ),
        "napi": "foundation/arkui/napi/bundle.json",
        "eventhandler": "base/notification/eventhandler/bundle.json",
        "ipc": "foundation/communication/ipc/bundle.json",
        "safwk": "foundation/systemabilitymgr/safwk/bundle.json",
        "samgr": "foundation/systemabilitymgr/samgr/bundle.json",
        "bundle_framework": "foundation/bundlemanager/bundle_framework/bundle.json",
        "storage_service": "foundation/filemanagement/storage_service/bundle.json",
        "config_policy": "base/customization/config_policy/bundle.json",
        "access_token": "base/security/access_token/bundle.json",
        "code_signature": "base/security/code_signature/bundle.json",
        "selinux_adapter": "base/security/selinux_adapter/bundle.json",
        "hdf_core": "drivers/hdf_core/bundle.json",
        "drivers_interface_partitionslot": "drivers/interface/partitionslot/bundle.json",
        "ace_ets2bundle": "developtools/ace_ets2bundle/bundle.json",
        "sdk": "interface/sdk-js/bundle.json",
    }

    ADDED_COMPONENTS = {
        "ability": ("ability_base", "idl_tool"),
        "arkcompiler": ("runtime_core", "ets_runtime", "toolchain"),
        "arkui": ("napi",),
        "bundlemanager": ("bundle_framework",),
        "communication": ("ipc",),
        "commonlibrary": ("ets_utils", "ylong_runtime"),
        "customization": ("config_policy",),
        "developtools": ("ace_ets2bundle", "hiprofiler"),
        "filemanagement": ("storage_service",),
        "hdf": ("drivers_interface_partitionslot", "hdf_core"),
        "hiviewdfx": (
            "faultloggerd",
            "hicollie",
            "hichecker",
            "hilog_lite",
            "hisysevent",
            "hitrace",
            "hiview",
        ),
        "notification": ("eventhandler",),
        "resourceschedule": (
            "ffrt",
            "qos_manager",
            "resource_schedule_service",
        ),
        "sdk": ("sdk",),
        "security": ("access_token", "code_signature", "selinux_adapter"),
        "startup": ("appspawn", "hvb", "init"),
        "systemabilitymgr": ("safwk", "samgr"),
        "thirdparty": (
            "cJSON",
            "elfio",
            "jsoncpp",
            "libxml2",
            "lzma",
            "mbedtls",
            "musl",
            "node",
            "openssl",
            "pcre2",
            "rust_cxx",
            "rust_libc",
            "selinux",
        ),
    }

    REQUIRED_LABEL_SUFFIXES = (
        "/ets2panda/aot:ets2panda",
        "/static_core/static_linker:ark_link",
        "/ark_guard:ark_guard",
        "/driver/dependency_analyzer:dependency_analyzer",
        "/ets1.2:ets2panda_libarkts",
        "/ets2panda/bindings:ets2panda_build_bindings",
        "/arkui-plugins:ui_plugin",
        "/static_core/plugins/ets:etsstdlib_abc",
        "/static_core/plugins/ets:gen_arktsconfig",
        "/sdk-js:ohos_build_static_sdk_api",
        "/sdk-js:ohos_build_static_sdk_kits",
        "/sdk-js:ohos_build_static_sdk_arkts",
    )

    def __init__(self, source: SourceTree, target: Target, keep: bool = False):
        self.source = source
        self.target = target
        self.keep = keep
        self.description_path = source.root / DESCRIPTION_RELATIVE_PATH
        self.inner_kits_allowlist_path = (
            source.root / INNER_KITS_ALLOWLIST_RELATIVE_PATH
        )
        self.product_path = source.root / PRODUCT_RELATIVE_PATH
        self.product_bundle_path = source.root / PRODUCT_BUNDLE_RELATIVE_PATH
        self.product_build_path = source.root / PRODUCT_BUILD_RELATIVE_PATH
        self.product_compat_stage_path = (
            source.root / PRODUCT_COMPAT_STAGE_RELATIVE_PATH
        )
        self.generated_directory = source.root / GENERATED_RELATIVE_DIRECTORY
        self.entry_count = 0

    def __enter__(self) -> "StaticSdkOverlay":
        occupied = [
            path
            for path in (
                self.description_path,
                self.inner_kits_allowlist_path,
                self.product_path,
                self.product_bundle_path,
                self.product_build_path,
                self.product_compat_stage_path,
            )
            if path.exists()
        ]
        if occupied:
            paths = ", ".join(str(path) for path in occupied)
            raise RuntimeError(f"refusing to overwrite existing SDK overlay files: {paths}")

        try:
            upstream_description = (
                self.source.root / "build/ohos/sdk/ohos_sdk_description_std.json"
            )
            entries = json.loads(upstream_description.read_text(encoding="utf-8"))
            static_entries = [
                entry
                for entry in entries
                if str(entry.get("install_dir", "")).startswith("ets/static/")
            ]
            self._validate_description(static_entries)
            static_entries.append(
                {
                    "install_dir": LIBARKTS_COMPAT_INSTALL_PATH,
                    "module_label": (
                        "//vendor/arkdown/arkdown-static-sdk:libarkts_compat_runtime"
                    ),
                    "target_os": ["linux", "windows", "darwin", "linux_arm64"],
                }
            )

            host_product = self.source.root / "vendor/ohemu/host_product/config.json"
            product = json.loads(host_product.read_text(encoding="utf-8"))
            product["product_name"] = PRODUCT_NAME
            product["product_company"] = "arkdown"
            product["device_company"] = "ohos"
            product["board"] = "sdk"
            product["target_os"] = "ohos"
            product["target_cpu"] = "arm64"
            product["kernel_type"] = "linux"
            product["compile_mode"] = "cross"
            product.pop("host_target_os", None)
            product.pop("host_target_cpu", None)
            product["ext_sdk_config_file"] = DESCRIPTION_RELATIVE_PATH
            product["support_jsapi"] = False
            product["subsystems"] = self._select_product_components(product)

            self.description_path.parent.mkdir(parents=True, exist_ok=False)
            self.product_path.parent.mkdir(parents=True, exist_ok=False)
            self.description_path.write_text(
                json.dumps(static_entries, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            self.inner_kits_allowlist_path.write_text(
                json.dumps({"allow_list_name": []}, indent=2) + "\n",
                encoding="utf-8",
            )
            self.product_path.write_text(
                json.dumps(product, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            self.product_bundle_path.write_text(
                json.dumps(self._product_bundle(), indent=2, ensure_ascii=False)
                + "\n",
                encoding="utf-8",
            )
            shutil.copy2(PRODUCT_BUILD_ASSET, self.product_build_path)
            shutil.copy2(
                PRODUCT_COMPAT_STAGE_ASSET, self.product_compat_stage_path
            )
            self._generate_sdk_metadata()
            self.entry_count = len(static_entries)
            return self
        except BaseException:
            if not self.keep:
                self._cleanup()
            raise

    def _generate_sdk_metadata(self) -> None:
        self.generated_directory.mkdir(parents=False, exist_ok=False)
        node = self.source.root / "prebuilts/build-tools/common/nodejs/current/bin/node"
        if not node.is_file():
            raise RuntimeError(
                "OpenHarmony Node.js prebuilt is missing; run "
                "build/prebuilts_download.sh first"
            )
        subprocess.run(
            [
                str(self.source.python_executable(self.target)),
                str(self.source.root / "build/ohos/sdk/parse_sdk_description.py"),
                "--sdk-description-file",
                str(self.description_path),
                "--sdk-install-info-file",
                str(self.source.root / GENERATED_INSTALL_RELATIVE_PATH),
                "--sdk-modules-gni",
                str(self.source.root / GENERATED_MODULES_RELATIVE_PATH),
                "--sdk-types-file",
                str(self.source.root / GENERATED_TYPES_RELATIVE_PATH),
                "--base-platform",
                "phone",
                "--platforms",
                "phone",
                "--source-root-dir",
                str(self.source.root),
                "--variant-to-product",
                str(self.source.root / "build/ohos/sdk/variant_to_product.json"),
                "--node-js",
                str(node),
            ],
            cwd=self.source.root,
            check=True,
        )
        # notice.gni imports sdk.gni in each installed module's target scope,
        # where the install manifest resolves to the standard output path. The
        # static-only product intentionally does not instantiate the full
        # //build/ohos/sdk target that normally creates it, so materialize the
        # same upstream-generated manifest before GN/Ninja starts.
        notice_install_path = self.source.root / NOTICE_INSTALL_RELATIVE_PATH
        notice_install_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(
            self.source.root / GENERATED_INSTALL_RELATIVE_PATH,
            notice_install_path,
        )

    def _select_product_components(
        self, product: dict[str, object]
    ) -> list[dict[str, object]]:
        selected: list[dict[str, object]] = []
        for subsystem_entry in product.get("subsystems", []):
            if not isinstance(subsystem_entry, dict):
                continue
            subsystem = str(subsystem_entry.get("subsystem", ""))
            components: list[dict[str, object]] = []
            for component in subsystem_entry.get("components", []):
                if not isinstance(component, dict):
                    continue
                component_name = str(component.get("component", ""))
                if component_name in self.EXCLUDED_COMPONENTS:
                    continue
                components.append(
                    self._prune_default_modules(component_name, component)
                )
            if components:
                selected.append({"subsystem": subsystem, "components": components})

        by_subsystem = {
            str(entry["subsystem"]): entry for entry in selected
        }
        existing = {
            str(component["component"])
            for entry in selected
            for component in entry["components"]
        }
        for subsystem, component_names in self.ADDED_COMPONENTS.items():
            entry = by_subsystem.get(subsystem)
            if entry is None:
                entry = {"subsystem": subsystem, "components": []}
                selected.append(entry)
                by_subsystem[subsystem] = entry
            for component_name in component_names:
                if component_name not in existing:
                    entry["components"].append(
                        self._prune_default_modules(
                            component_name,
                            {"component": component_name, "features": []},
                        )
                    )
                    existing.add(component_name)
        return selected

    def _prune_default_modules(
        self, component_name: str, component: dict[str, object]
    ) -> dict[str, object]:
        relative_path = self.COMPONENT_BUNDLES.get(component_name)
        if relative_path is None:
            raise RuntimeError(
                f"no bundle.json owner is registered for component {component_name}"
            )
        bundle_path = self.source.root / relative_path
        if not bundle_path.is_file():
            raise RuntimeError(
                f"component {component_name} is missing its upstream metadata: "
                f"{relative_path}"
            )
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        build = bundle.get("component", {}).get("build", {})
        pruning = dict(component.get("prune_deps", {}))
        for key in ("sub_component", "modules"):
            module_list = build.get(key)
            if isinstance(module_list, list) and module_list:
                pruning[key] = list(module_list)
        group_type = build.get("group_type")
        if isinstance(group_type, dict):
            modules = [
                module
                for group in group_type.values()
                if isinstance(group, list)
                for module in group
            ]
            if modules:
                pruning["group_type"] = modules

        result = dict(component)
        if pruning:
            result["prune_deps"] = pruning
        return result

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        if not self.keep:
            self._cleanup()
        return False

    def _cleanup(self) -> None:
        self.product_path.unlink(missing_ok=True)
        self.product_bundle_path.unlink(missing_ok=True)
        self.product_build_path.unlink(missing_ok=True)
        self.product_compat_stage_path.unlink(missing_ok=True)
        self.description_path.unlink(missing_ok=True)
        self.inner_kits_allowlist_path.unlink(missing_ok=True)
        shutil.rmtree(self.generated_directory, ignore_errors=True)
        for directory in (
            self.product_path.parent,
            self.product_path.parent.parent,
        ):
            try:
                directory.rmdir()
            except OSError:
                pass
        try:
            self.description_path.parent.rmdir()
        except OSError:
            pass

    @staticmethod
    def _product_bundle() -> dict[str, object]:
        return {
            "name": "@arkdown/static-sdk",
            "version": "1.0.0",
            "component": {
                "name": "product_arkdown-static-sdk",
                "subsystem": "product_arkdown-static-sdk",
                "syscap": [],
                "features": [],
                "adapted_system_type": ["standard"],
                "rom": "0KB",
                "ram": "0KB",
                "deps": {},
                "build": {
                    "sub_component": [
                        "//vendor/arkdown/arkdown-static-sdk:arkdown_static_sdk"
                    ],
                    "inner_kits": [],
                    "test": [],
                },
            },
        }

    def _validate_description(self, entries: Sequence[dict[str, object]]) -> None:
        if not entries:
            raise RuntimeError("upstream SDK description contains no ets/static entries")
        labels = {str(entry.get("module_label", "")) for entry in entries}
        missing = [
            suffix
            for suffix in self.REQUIRED_LABEL_SUFFIXES
            if not any(label.endswith(suffix) for label in labels)
        ]
        if missing:
            formatted = "\n".join(f"  - *{suffix}" for suffix in missing)
            raise RuntimeError(
                "upstream static SDK description is incomplete; missing required labels:\n"
                f"{formatted}"
            )
        for entry in entries:
            install_dir = str(entry.get("install_dir", ""))
            if not install_dir.startswith("ets/static/"):
                raise RuntimeError(f"non-static install path selected: {install_dir}")


class StaticSdkArchive:
    REQUIRED_PATH_FRAGMENTS = (
        "ets/static/api/",
        "ets/static/kits/",
        "ets/static/arkts/",
        "ets/static/build-tools/ets2panda/bin/es2panda",
        "ets/static/build-tools/ets2panda/bin/ark_link",
        "ets/static/build-tools/ets2panda/bin/ark_guard",
        "ets/static/build-tools/ets2panda/bin/dependency_analyzer",
        "ets/static/build-tools/ets2panda/lib/etsstdlib.abc",
        "ets/static/build-tools/libarkts/",
        "ets/static/build-tools/libarkts/node_modules/@koalaui/compat/package.json",
        "ets/static/build-tools/libarkts/node_modules/@koalaui/compat/build/src/index.js",
        "ets/static/build-tools/bindings/",
        "ets/static/build-tools/ui-plugins/",
    )

    def __init__(self, path: Path):
        self.path = path

    def validate(self) -> int:
        with zipfile.ZipFile(self.path) as archive:
            names = [self._validated_name(name) for name in archive.namelist()]
        dynamic = [name for name in names if "ets/dynamic/" in name]
        if dynamic:
            raise RuntimeError(
                f"static SDK archive unexpectedly contains dynamic entries: {dynamic[0]}"
            )
        missing = [
            fragment
            for fragment in self.REQUIRED_PATH_FRAGMENTS
            if not any(fragment in name for name in names)
        ]
        if missing:
            formatted = "\n".join(f"  - {item}" for item in missing)
            raise RuntimeError(f"static SDK archive is incomplete; missing:\n{formatted}")
        return len(names)

    def write_tar_gz(self, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with (
            zipfile.ZipFile(self.path) as source,
            destination.open("wb") as output,
            gzip.GzipFile(
                filename="", mode="wb", fileobj=output, mtime=0
            ) as compressed,
            tarfile.open(fileobj=compressed, mode="w") as archive,
        ):
            for source_info in sorted(source.infolist(), key=lambda item: item.filename):
                name = self._validated_name(source_info.filename)
                target_info = tarfile.TarInfo(name=name)
                target_info.mtime = 0
                target_info.uid = 0
                target_info.gid = 0
                target_info.uname = ""
                target_info.gname = ""
                source_mode = source_info.external_attr >> 16
                mode = source_mode & 0o777
                if source_info.is_dir():
                    target_info.type = tarfile.DIRTYPE
                    target_info.mode = mode or 0o755
                    archive.addfile(target_info)
                    continue
                if stat.S_ISLNK(source_mode):
                    target_info.type = tarfile.SYMTYPE
                    target_info.mode = mode or 0o777
                    target_info.linkname = source.read(source_info).decode("utf-8")
                    archive.addfile(target_info)
                    continue
                target_info.mode = mode or 0o644
                target_info.size = source_info.file_size
                with source.open(source_info) as contents:
                    archive.addfile(target_info, contents)

    @staticmethod
    def _normalize(name: str) -> str:
        return PurePosixPath(name.replace("\\", "/")).as_posix()

    @classmethod
    def _validated_name(cls, name: str) -> str:
        normalized = cls._normalize(name)
        path = PurePosixPath(normalized)
        if path.is_absolute() or ".." in path.parts:
            raise RuntimeError(f"unsafe SDK archive entry: {name}")
        return normalized


class StaticSdkBuilder:
    GN_ARGUMENTS = (
        "build_ohos_sdk=true",
        "build_ohos_ndk=false",
        "sdk_build_arkts=true",
        "sdk_for_hap_build=false",
        "enable_archive_sdk=true",
        "enable_notice_collection=true",
        "enable_process_notice=true",
        "sdk_check_flag=false",
        "sdk_build_cangjie=false",
        "skip_generate_module_list_file=true",
        "point_split=true",
        "use_cfi=false",
        "use_thin_lto=false",
        "is_llvm_build=true",
        # The LLVM-only musl graph does not inject parameterbase into libc.
        # Keep libsystemparam self-contained when transitive host-tool support
        # libraries select it through config_policy.
        "startup_init_with_param_base=true",
    )

    def __init__(
        self,
        source: SourceTree,
        target: Target,
        dist: Path,
        download_prebuilts: bool = False,
        keep_overlay: bool = False,
        dry_run: bool = False,
        graph_only: bool = False,
        ninja_jobs: int | None = None,
    ):
        self.source = source
        self.target = target
        self.dist = dist.resolve()
        self.download_prebuilts = download_prebuilts
        self.keep_overlay = keep_overlay
        self.dry_run = dry_run
        self.graph_only = graph_only
        self.ninja_jobs = ninja_jobs

    def run(self) -> Path | None:
        self.source.validate()
        self.target.verify_host()
        if self.download_prebuilts:
            self._download_prebuilts()
        if not self.dry_run and not self.graph_only:
            self.source.prepare_node_dependencies()

        with Node14PackageCompatibility(self.source), DarwinBuildCompatibility(
            self.source, self.target
        ) as host_compatibility, StaticSdkOverlay(
            self.source, target=self.target, keep=self.keep_overlay
        ) as overlay:
            command = self._build_command()
            print("Build command:")
            print(" ".join(command))
            print(f"Selected {overlay.entry_count} ets/static delivery entries")
            if self.dry_run:
                return None
            environment = os.environ.copy()
            host_compatibility.apply(environment)
            environment.update(
                {
                    "CI": "true",
                    "NPM_CONFIG_PACKAGE_LOCK": "false",
                    "npm_config_package_lock": "false",
                }
            )
            subprocess.run(command, cwd=self.source.root, env=environment, check=True)
            if self.graph_only:
                return None
            archive = self._find_archive()
            return self._publish(archive, overlay.entry_count)

    def _download_prebuilts(self) -> None:
        subprocess.run(
            ["bash", str(self.source.root / "build/prebuilts_download.sh")],
            cwd=self.source.root,
            check=True,
        )

    def _build_command(self) -> list[str]:
        gn_arguments = (
            *self.GN_ARGUMENTS,
            *self.source.clang_arguments(self.target),
            f"check_innerkits_path=//{INNER_KITS_ALLOWLIST_RELATIVE_PATH}",
            f"sdk_platform={self.target.sdk_platform}",
        )
        command = [
            "bash",
            str(self.source.root / "build.sh"),
            "--product-name",
            PRODUCT_SELECTOR,
            "--build-target",
            "arkdown_static_sdk",
            "--skip-partlist-check=true",
            "--load-test-config=false",
            "--no-prebuilt-sdk=true",
            "--deps-guard=false",
            "--gn-args",
            " ".join(gn_arguments),
        ]
        if self.graph_only:
            command.append("--ninja-args=-n")
        elif self.ninja_jobs is not None:
            command.append(f"--ninja-args=-j{self.ninja_jobs}")
        return command

    def _find_archive(self) -> Path:
        output_root = self.source.root / "out"
        candidates = sorted(
            path
            for variant_root in output_root.iterdir()
            if variant_root.is_dir()
            for path in (
                variant_root
                / "packages"
                / PRODUCT_NAME
                / self.target.sdk_system
            ).glob("ets-*.zip")
        )
        if len(candidates) != 1:
            rendered = "\n".join(f"  - {path}" for path in candidates) or "  (none)"
            raise RuntimeError(
                f"expected exactly one {self.target.sdk_system} ETS archive under "
                f"{output_root}, found {len(candidates)}:\n{rendered}"
            )
        return candidates[0]

    def _publish(self, source_archive: Path, description_entries: int) -> Path:
        archive = StaticSdkArchive(source_archive)
        archive_entries = archive.validate()
        revisions = self.source.revisions()
        revision = revisions["ets_frontend"][:12]
        self.dist.mkdir(parents=True, exist_ok=True)
        artifact = self.dist / f"arkdown-ets-static-{self.target.name}-{revision}.zip"
        shutil.copy2(source_archive, artifact)
        digest = self._sha256(artifact)
        artifact.with_suffix(artifact.suffix + ".sha256").write_text(
            f"{digest}  {artifact.name}\n", encoding="utf-8"
        )
        tarball = self.dist / f"arkdown-ets-static-{self.target.name}-{revision}.tar.gz"
        archive.write_tar_gz(tarball)
        tarball_digest = self._sha256(tarball)
        tarball.with_suffix(tarball.suffix + ".sha256").write_text(
            f"{tarball_digest}  {tarball.name}\n", encoding="utf-8"
        )
        metadata = {
            "schemaVersion": 1,
            "artifact": artifact.name,
            "sha256": digest,
            "gzipArtifact": tarball.name,
            "gzipSha256": tarball_digest,
            "target": self.target.name,
            "sdkPlatform": self.target.sdk_platform,
            "sdkSystem": self.target.sdk_system,
            "descriptionEntries": description_entries,
            "archiveEntries": archive_entries,
            "sourceRevisions": revisions,
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "gnArgs": [
                *self.GN_ARGUMENTS,
                *self.source.clang_arguments(self.target),
                f"check_innerkits_path=//{INNER_KITS_ALLOWLIST_RELATIVE_PATH}",
                f"sdk_platform={self.target.sdk_platform}",
            ],
        }
        artifact.with_suffix(artifact.suffix + ".manifest.json").write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        manifest_path = self.dist / f"source-manifest-{self.target.name}-{revision}.xml"
        self.source.write_pinned_manifest(manifest_path)
        return tarball

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()


def supported_targets() -> Iterable[str]:
    return TARGETS.keys()
