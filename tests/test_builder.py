from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from unittest.mock import patch

from static_sdk.builder import (
    DarwinBuildCompatibility,
    Node14PackageCompatibility,
    NOTICE_INSTALL_RELATIVE_PATH,
    PRODUCT_NAME,
    PRODUCT_SELECTOR,
    SourceTree,
    StaticSdkArchive,
    StaticSdkBuilder,
    StaticSdkOverlay,
    TARGETS,
)


class DarwinBuildCompatibilityTests(unittest.TestCase):
    @patch("static_sdk.builder.shutil.which")
    @patch("static_sdk.builder.subprocess.run")
    def test_maps_new_xcode_sdk_to_legacy_probe_name(self, run, which) -> None:
        run.return_value.stdout = "15.2\n"
        which.side_effect = lambda command: f"/usr/bin/{command}"

        with tempfile.TemporaryDirectory() as source_directory:
            python_modules = Path(source_directory) / "third_party/PyYAML/lib/yaml"
            python_modules.mkdir(parents=True)
            (python_modules / "__init__.py").write_text("")
            with DarwinBuildCompatibility(
                SourceTree(Path(source_directory)), TARGETS["darwin-arm64"]
            ) as compatibility:
                environment = {"PATH": "/usr/bin"}
                compatibility.apply(environment)
                bin_directory = Path(environment["PATH"].split(":", 1)[0])
                developer = bin_directory.parent / "Xcode.app/Contents/Developer"

                self.assertTrue(
                    (
                        developer
                        / "Platforms/MacOSX.platform/Developer/SDKs/MacOSX14.99.sdk"
                    ).is_dir()
                )
                self.assertIn("macosx14.99", (bin_directory / "xcrun").read_text())
                self.assertIn(
                    str(developer), (bin_directory / "xcode-select").read_text()
                )
                self.assertEqual(
                    str(python_modules.parent.resolve()), environment["PYTHONPATH"]
                )

            self.assertFalse(bin_directory.exists())

    @patch("static_sdk.builder.subprocess.run")
    def test_uses_supported_xcode_sdk_without_compatibility(self, run) -> None:
        run.return_value.stdout = "14.5\n"

        with tempfile.TemporaryDirectory() as source_directory:
            python_modules = Path(source_directory) / "third_party/PyYAML/lib/yaml"
            python_modules.mkdir(parents=True)
            (python_modules / "__init__.py").write_text("")
            with DarwinBuildCompatibility(
                SourceTree(Path(source_directory)), TARGETS["darwin-arm64"]
            ) as compatibility:
                environment = {"PATH": "/usr/bin"}
                compatibility.apply(environment)

            self.assertEqual("/usr/bin", environment["PATH"])
            self.assertEqual(
                str(python_modules.parent.resolve()), environment["PYTHONPATH"]
            )


class Node14PackageCompatibilityTests(unittest.TestCase):
    def test_uses_shell_cleanup_and_restores_upstream_packages(self) -> None:
        with tempfile.TemporaryDirectory() as source_directory:
            root = Path(source_directory)
            originals = {}
            for relative_path, (original, _) in (
                Node14PackageCompatibility.PACKAGE_PATCHES.items()
            ):
                package = root / relative_path
                package.parent.mkdir(parents=True, exist_ok=True)
                source = f'{{\n  "scripts": {{\n    {original}\n  }}\n}}\n'
                package.write_text(source, encoding="utf-8")
                package.with_name("package-lock.json").write_bytes(b"upstream-lock\n")
                originals[package] = source

            with Node14PackageCompatibility(SourceTree(root)):
                for relative_path, (original, replacement) in (
                    Node14PackageCompatibility.PACKAGE_PATCHES.items()
                ):
                    package = root / relative_path
                    patched = package.read_text(encoding="utf-8")
                    self.assertNotIn(original, patched)
                    self.assertIn(replacement, patched)
                    package.with_name("package-lock.json").unlink()

            for package, source in originals.items():
                self.assertEqual(source, package.read_text(encoding="utf-8"))
                self.assertEqual(
                    b"upstream-lock\n",
                    package.with_name("package-lock.json").read_bytes(),
                )


class StaticSdkOverlayTests(unittest.TestCase):
    @patch("static_sdk.builder.subprocess.run")
    def test_prepares_only_missing_ets_node_projects(self, run) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            node_bin = root / "prebuilts/build-tools/common/nodejs/current/bin"
            node_bin.mkdir(parents=True)
            for executable in ("node", "npm"):
                (node_bin / executable).write_text("", encoding="utf-8")
            projects = list(SourceTree.NODE_PROJECTS.items())
            source = SourceTree(root)
            for relative_path, markers in projects:
                project = root / relative_path
                project.mkdir(parents=True)
                (project / "package.json").write_text("{}", encoding="utf-8")
                for marker in markers:
                    path = project / marker
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("", encoding="utf-8")
                stamp = project / SourceTree.NODE_DEPENDENCY_STAMP
                stamp.parent.mkdir(parents=True, exist_ok=True)
                stamp.write_text(
                    source._node_dependency_stamp(project), encoding="utf-8"
                )
            missing_project, missing_markers = projects[1]
            (root / missing_project / missing_markers[0]).unlink()

            def install(command, *, cwd, env, check):
                self.assertEqual(str(node_bin.resolve() / "npm"), command[0])
                self.assertEqual("install", command[1])
                self.assertIn("--ignore-scripts", command)
                self.assertIn("--package-lock=false", command)
                self.assertEqual(
                    str(node_bin.resolve()), env["PATH"].split(os.pathsep, 1)[0]
                )
                self.assertTrue(check)
                for marker in missing_markers:
                    path = Path(cwd) / marker
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("", encoding="utf-8")

            run.side_effect = install

            source.prepare_node_dependencies()

            self.assertEqual(1, run.call_count)
            self.assertEqual(
                root.resolve() / missing_project, run.call_args.kwargs["cwd"]
            )

    @patch("static_sdk.builder.subprocess.run")
    def test_uses_npm_ci_for_locked_ets_node_project(self, run) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            node_bin = root / "prebuilts/build-tools/common/nodejs/current/bin"
            node_bin.mkdir(parents=True)
            for executable in ("node", "npm"):
                (node_bin / executable).write_text("", encoding="utf-8")
            relative_path, markers = next(iter(SourceTree.NODE_PROJECTS.items()))
            project = root / relative_path
            project.mkdir(parents=True)
            (project / "package.json").write_text("{}", encoding="utf-8")
            (project / "package-lock.json").write_text("{}", encoding="utf-8")

            def install(command, *, cwd, env, check):
                for marker in markers:
                    path = Path(cwd) / marker
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("", encoding="utf-8")

            run.side_effect = install
            with patch.object(
                SourceTree, "NODE_PROJECTS", {relative_path: markers}
            ):
                SourceTree(root).prepare_node_dependencies()

            command = run.call_args.args[0]
            self.assertEqual("ci", command[1])
            self.assertNotIn("--package-lock=false", command)

    @patch("static_sdk.builder.subprocess.run")
    def test_falls_back_for_inconsistent_lock_and_restores_it(self, run) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            node_bin = root / "prebuilts/build-tools/common/nodejs/current/bin"
            node_bin.mkdir(parents=True)
            for executable in ("node", "npm"):
                (node_bin / executable).write_text("", encoding="utf-8")
            relative_path, markers = next(iter(SourceTree.NODE_PROJECTS.items()))
            project = root / relative_path
            project.mkdir(parents=True)
            (project / "package.json").write_text("{}", encoding="utf-8")
            lockfile = project / "package-lock.json"
            lockfile.write_bytes(b"upstream-lock\n")

            def install(command, *, cwd, env, check):
                if command[1] == "ci":
                    partial = Path(cwd) / "node_modules/partial"
                    partial.parent.mkdir(parents=True, exist_ok=True)
                    partial.write_text("incomplete", encoding="utf-8")
                    raise subprocess.CalledProcessError(1, command)
                self.assertFalse((Path(cwd) / "node_modules/partial").exists())
                lockfile.write_bytes(b"changed-lock\n")
                for marker in markers:
                    path = Path(cwd) / marker
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("", encoding="utf-8")

            run.side_effect = install
            with patch.object(
                SourceTree, "NODE_PROJECTS", {relative_path: markers}
            ):
                SourceTree(root).prepare_node_dependencies()

            self.assertEqual(
                ["ci", "install"],
                [call.args[0][1] for call in run.call_args_list],
            )
            self.assertEqual(b"upstream-lock\n", lockfile.read_bytes())

    def test_writes_manifest_with_current_python_interpreter(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            launcher = root / ".repo/repo/repo"
            launcher.parent.mkdir(parents=True)
            launcher.write_text(
                """from pathlib import Path
import sys

if sys.argv[1:] == ['list']:
    raise SystemExit(0)
assert sys.argv[1:3] == ['manifest', '-r']
Path(sys.argv[4]).write_text('<manifest/>')
""",
                encoding="utf-8",
            )
            destination = root / "dist/source.xml"

            written = SourceTree(root).write_pinned_manifest(destination)

            self.assertTrue(written)
            self.assertEqual("<manifest/>", destination.read_text(encoding="utf-8"))

    def test_writes_only_checked_out_projects_for_partial_source_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            launcher = root / ".repo/repo/repo"
            launcher.parent.mkdir(parents=True)
            launcher.write_text(
                """import sys

assert sys.argv[1:] == ['list']
print('arkcompiler/ets_frontend : arkcompiler_ets_frontend')
print('applications/launcher : applications_launcher')
""",
                encoding="utf-8",
            )
            project = root / "arkcompiler/ets_frontend"
            project.mkdir(parents=True)
            subprocess.run(["git", "init", "-q", str(project)], check=True)
            subprocess.run(
                ["git", "-C", str(project), "config", "user.name", "Test"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(project), "config", "user.email", "test@example.com"],
                check=True,
            )
            (project / "source.txt").write_text("source", encoding="utf-8")
            subprocess.run(["git", "-C", str(project), "add", "source.txt"], check=True)
            subprocess.run(
                ["git", "-C", str(project), "commit", "-q", "-m", "source"],
                check=True,
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(project),
                    "remote",
                    "add",
                    "gitcode",
                    "https://gitcode.com/openharmony/arkcompiler_ets_frontend",
                ],
                check=True,
            )
            revision = subprocess.run(
                ["git", "-C", str(project), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            destination = root / "dist/source.xml"

            written = SourceTree(root).write_pinned_manifest(destination)

            self.assertTrue(written)
            manifest = ElementTree.parse(destination).getroot()
            self.assertEqual(
                [
                    {
                        "name": "arkcompiler_ets_frontend",
                        "path": "arkcompiler/ets_frontend",
                        "remote": "remote-0",
                        "revision": revision,
                    }
                ],
                [project.attrib for project in manifest.findall("project")],
            )
            self.assertEqual(
                {
                    "name": "remote-0",
                    "fetch": "https://gitcode.com/openharmony",
                },
                manifest.find("remote").attrib,
            )

    def test_filters_upstream_description_and_preserves_product_features(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self._write_source_fixture(root)
            source = SourceTree(root)
            with StaticSdkOverlay(source, TARGETS["linux-x64"]) as overlay:
                selected = json.loads(overlay.description_path.read_text(encoding="utf-8"))
                product = json.loads(overlay.product_path.read_text(encoding="utf-8"))
                inner_kits_allowlist = json.loads(
                    overlay.inner_kits_allowlist_path.read_text(encoding="utf-8")
                )
                self.assertTrue(selected)
                self.assertTrue(
                    all(item["install_dir"].startswith("ets/static/") for item in selected)
                )
                compat_entries = [
                    item
                    for item in selected
                    if item["install_dir"].endswith(
                        "libarkts/node_modules/@koalaui/compat/"
                    )
                ]
                self.assertEqual(1, len(compat_entries))
                self.assertEqual(
                    "//vendor/arkdown/arkdown-static-sdk:libarkts_compat_runtime",
                    compat_entries[0]["module_label"],
                )
                self.assertEqual("arkdown-static-sdk", product["product_name"])
                self.assertEqual(
                    ".arkdown-static-sdk/ohos_sdk_description_static.json",
                    product["ext_sdk_config_file"],
                )
                self.assertEqual("cross", product["compile_mode"])
                self.assertEqual("arkdown", product["product_company"])
                self.assertEqual("ohos", product["device_company"])
                self.assertEqual("sdk", product["board"])
                self.assertEqual("ohos", product["target_os"])
                self.assertEqual("arm64", product["target_cpu"])
                self.assertEqual("linux", product["kernel_type"])
                self.assertNotIn("host_target_os", product)
                self.assertNotIn("host_target_cpu", product)
                self.assertEqual({"allow_list_name": []}, inner_kits_allowlist)
                product_bundle = json.loads(
                    overlay.product_bundle_path.read_text(encoding="utf-8")
                )
                self.assertEqual(
                    "product_arkdown-static-sdk",
                    product_bundle["component"]["name"],
                )
                self.assertEqual(
                    [
                        "//vendor/arkdown/arkdown-static-sdk:arkdown_static_sdk"
                    ],
                    product_bundle["component"]["build"]["sub_component"],
                )
                self.assertIn(
                    'group("arkdown_static_sdk")',
                    overlay.product_build_path.read_text(encoding="utf-8"),
                )
                self.assertIn(
                    'ohos_copy("libarkts_compat_runtime")',
                    overlay.product_build_path.read_text(encoding="utf-8"),
                )
                self.assertIn(
                    'action("stage_libarkts_compat_runtime")',
                    overlay.product_build_path.read_text(encoding="utf-8"),
                )
                self.assertTrue(overlay.product_compat_stage_path.is_file())
                self.assertEqual(
                    [],
                    json.loads(
                        (root / NOTICE_INSTALL_RELATIVE_PATH).read_text(encoding="utf-8")
                    ),
                )
                self.assertNotIn(
                    "ohos_ndk",
                    overlay.product_build_path.read_text(encoding="utf-8"),
                )
                self.assertFalse(product["support_jsapi"])
                selected_components = {
                    component["component"]
                    for subsystem in product["subsystems"]
                    for component in subsystem["components"]
                }
                added_components = {
                    component
                    for components in StaticSdkOverlay.ADDED_COMPONENTS.values()
                    for component in components
                }
                self.assertTrue(added_components.issubset(selected_components))
                self.assertNotIn("ace_engine", selected_components)
                self.assertIn("ets_runtime", selected_components)
                self.assertIn("napi", selected_components)
                for subsystem in product["subsystems"]:
                    for component in subsystem["components"]:
                        self.assertEqual(
                            [f"//fixture:{component['component']}"],
                            component["prune_deps"]["sub_component"],
                        )
            self.assertFalse(overlay.description_path.exists())
            self.assertFalse(overlay.product_compat_stage_path.exists())
            self.assertFalse(overlay.inner_kits_allowlist_path.exists())
            self.assertFalse(overlay.product_path.exists())
            self.assertFalse(overlay.product_bundle_path.exists())
            self.assertFalse(overlay.product_build_path.exists())
            self.assertFalse(overlay.generated_directory.exists())

    def test_cleans_partial_overlay_when_metadata_generation_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self._write_source_fixture(root)
            (root / "prebuilts/build-tools/common/nodejs/current/bin/node").unlink()
            overlay = StaticSdkOverlay(SourceTree(root), TARGETS["linux-x64"])

            with self.assertRaisesRegex(RuntimeError, "Node.js prebuilt"):
                overlay.__enter__()

            self.assertFalse(overlay.description_path.parent.exists())
            self.assertFalse(overlay.product_path.parent.exists())

    def test_builds_only_the_sdk_target_without_loading_component_tests(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self._write_source_fixture(root)
            builder = StaticSdkBuilder(
                source=SourceTree(root),
                target=TARGETS["linux-x64"],
                dist=root / "dist",
            )

            command = builder._build_command()

        self.assertIn(PRODUCT_NAME, PRODUCT_SELECTOR)
        self.assertEqual("arkdown_static_sdk", command[5])
        self.assertIn("--load-test-config=false", command)
        self.assertIn("--no-prebuilt-sdk=true", command)
        self.assertIn("--deps-guard=false", command)
        self.assertIn("is_llvm_build=true", command[-1])
        self.assertIn("startup_init_with_param_base=true", command[-1])
        self.assertIn(
            "clang_base_path=//prebuilts/clang/ohos/linux-x86_64/llvm",
            command[-1],
        )

    def test_graph_only_runs_ninja_without_compiling(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self._write_source_fixture(root)
            builder = StaticSdkBuilder(
                source=SourceTree(root),
                target=TARGETS["linux-x64"],
                dist=root / "dist",
                graph_only=True,
            )

            self.assertEqual("--ninja-args=-n", builder._build_command()[-1])

    def test_limits_ninja_parallelism_for_real_builds(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self._write_source_fixture(root)
            builder = StaticSdkBuilder(
                source=SourceTree(root),
                target=TARGETS["linux-x64"],
                dist=root / "dist",
                ninja_jobs=8,
            )

            self.assertEqual("--ninja-args=-j8", builder._build_command()[-1])

    def test_maps_all_targets_to_upstream_platforms_and_host_clang(self) -> None:
        expected = {
            "linux-x64": ("sdk_platform=linux", "linux-x86_64"),
            "windows-x64": ("sdk_platform=win", "linux-x86_64"),
            "darwin-arm64": ("sdk_platform=mac", "darwin-arm64"),
        }
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self._write_source_fixture(root)
            for target_name, (sdk_argument, clang_directory) in expected.items():
                builder = StaticSdkBuilder(
                    source=SourceTree(root),
                    target=TARGETS[target_name],
                    dist=root / "dist",
                )

                gn_arguments = builder._build_command()[-1]

                self.assertIn(sdk_argument, gn_arguments)
                self.assertIn(
                    f"clang_base_path=//prebuilts/clang/ohos/{clang_directory}/llvm",
                    gn_arguments,
                )

    def test_finds_archive_only_in_the_product_platform_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self._write_source_fixture(root)
            expected = (
                root
                / "out/sdk/packages/arkdown-static-sdk/linux/ets-linux-x64.zip"
            )
            expected.parent.mkdir(parents=True)
            expected.write_bytes(b"archive")
            unrelated = root / "out/old/nested/archive/ets-unrelated.zip"
            unrelated.parent.mkdir(parents=True)
            unrelated.write_bytes(b"unrelated")
            builder = StaticSdkBuilder(
                source=SourceTree(root),
                target=TARGETS["linux-x64"],
                dist=root / "dist",
            )

            self.assertEqual(expected.resolve(), builder._find_archive())

    @staticmethod
    def _write_source_fixture(root: Path) -> None:
        description = []
        suffixes = StaticSdkOverlay.REQUIRED_LABEL_SUFFIXES
        for index, suffix in enumerate(suffixes):
            description.append(
                {
                    "install_dir": f"ets/static/item-{index}",
                    "module_label": f"//fixture{suffix}",
                    "target_os": ["linux", "windows", "darwin"],
                }
            )
        description.append(
            {
                "install_dir": "ets/dynamic/api",
                "module_label": "//fixture:dynamic",
                "target_os": ["linux"],
            }
        )
        description_path = root / "build/ohos/sdk/ohos_sdk_description_std.json"
        description_path.parent.mkdir(parents=True)
        description_path.write_text(json.dumps(description), encoding="utf-8")
        parser_path = root / "build/ohos/sdk/parse_sdk_description.py"
        parser_path.write_text(
            """import argparse
from pathlib import Path

parser = argparse.ArgumentParser()
for option in (
    '--sdk-description-file', '--sdk-install-info-file', '--sdk-modules-gni',
    '--sdk-types-file', '--base-platform', '--source-root-dir',
    '--variant-to-product', '--node-js',
):
    parser.add_argument(option)
parser.add_argument('--platforms', action='append')
args = parser.parse_args()
Path(args.sdk_install_info_file).write_text('[]')
Path(args.sdk_modules_gni).write_text('ohos_sdk_modules = {}')
Path(args.sdk_types_file).write_text('ets')
""",
            encoding="utf-8",
        )
        variant_path = root / "build/ohos/sdk/variant_to_product.json"
        variant_path.write_text("{}", encoding="utf-8")
        toolchain_path = root / "build/toolchain/toolchain.gni"
        toolchain_path.parent.mkdir(parents=True)
        toolchain_path.write_text('clang_version = "15.0.4"\n', encoding="utf-8")
        node_path = root / "prebuilts/build-tools/common/nodejs/current/bin/node"
        node_path.parent.mkdir(parents=True)
        node_path.write_text("", encoding="utf-8")
        for clang_host_directory in ("linux-x86_64", "darwin-arm64"):
            clang_base = (
                root / "prebuilts/clang/ohos" / clang_host_directory / "llvm"
            )
            clang_path = clang_base / "bin/clang"
            clang_path.parent.mkdir(parents=True)
            clang_path.write_text("", encoding="utf-8")
            builtins_path = (
                clang_base
                / "lib/clang/15.0.4/lib"
                / "aarch64-linux-ohos/libclang_rt.builtins.a"
            )
            builtins_path.parent.mkdir(parents=True)
            builtins_path.write_text("", encoding="utf-8")
            libcxxabi_path = (
                clang_base / "lib/aarch64-linux-ohos/libc++abi.a"
            )
            libcxxabi_path.parent.mkdir(parents=True)
            libcxxabi_path.write_text("", encoding="utf-8")
        for python_host_directory in ("linux-x86", "darwin-arm64"):
            python_path = (
                root
                / "prebuilts/python"
                / python_host_directory
                / "3.12.10/bin/python3"
            )
            python_path.parent.mkdir(parents=True)
            python_path.symlink_to(sys.executable)
        product_path = root / "productdefine/common/products/ohos-sdk.json"
        product_path.parent.mkdir(parents=True)
        host_product_path = root / "vendor/ohemu/host_product/config.json"
        host_product_path.parent.mkdir(parents=True)
        host_product_path.write_text(
            json.dumps(
                {
                    "product_name": "host_product",
                    "compile_mode": "host",
                    "host_target_os": "linux",
                    "host_target_cpu": "x86_64",
                    "support_jsapi": False,
                    "subsystems": [
                        {
                            "subsystem": "arkui",
                            "components": [
                                {"component": "napi"},
                                {"component": "ace_engine"},
                            ],
                        },
                        {
                            "subsystem": "arkcompiler",
                            "components": [
                                {"component": "ets_frontend"},
                                {"component": "ets_runtime"},
                            ],
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        for component_name, relative_path in StaticSdkOverlay.COMPONENT_BUNDLES.items():
            bundle_path = root / relative_path
            bundle_path.parent.mkdir(parents=True, exist_ok=True)
            bundle_path.write_text(
                json.dumps(
                    {
                        "component": {
                            "name": component_name,
                            "build": {
                                "sub_component": [f"//fixture:{component_name}"],
                                "inner_kits": [],
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )


class StaticSdkArchiveTests(unittest.TestCase):
    def test_accepts_complete_static_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            archive_path = Path(temporary_directory) / "ets.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                for fragment in StaticSdkArchive.REQUIRED_PATH_FRAGMENTS:
                    name = fragment
                    if name.endswith("/"):
                        name += "fixture"
                    archive.writestr(name, b"fixture")
            count = StaticSdkArchive(archive_path).validate()
            self.assertEqual(len(StaticSdkArchive.REQUIRED_PATH_FRAGMENTS), count)

    def test_requires_upstream_es2panda_executable_name(self) -> None:
        self.assertIn(
            "ets/static/build-tools/ets2panda/bin/es2panda",
            StaticSdkArchive.REQUIRED_PATH_FRAGMENTS,
        )
        self.assertNotIn(
            "ets/static/build-tools/ets2panda/bin/ets2panda",
            StaticSdkArchive.REQUIRED_PATH_FRAGMENTS,
        )

    def test_writes_reproducible_tar_gz_with_the_same_sdk_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            archive_path = root / "ets.zip"
            first_tarball = root / "first.tar.gz"
            second_tarball = root / "second.tar.gz"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("ets/static/api/example.d.ts", b"export {}")
                executable = zipfile.ZipInfo("ets/static/build-tools/tool")
                executable.external_attr = (stat.S_IFREG | 0o755) << 16
                archive.writestr(executable, b"#!/bin/sh\n")
                symlink = zipfile.ZipInfo("ets/static/build-tools/tool-link")
                symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(symlink, b"tool")

            sdk_archive = StaticSdkArchive(archive_path)
            sdk_archive.write_tar_gz(first_tarball)
            sdk_archive.write_tar_gz(second_tarball)

            self.assertEqual(first_tarball.read_bytes(), second_tarball.read_bytes())
            with tarfile.open(first_tarball, "r:gz") as archive:
                self.assertEqual(
                    b"export {}",
                    archive.extractfile("ets/static/api/example.d.ts").read(),
                )
                self.assertEqual(
                    0o755,
                    archive.getmember("ets/static/build-tools/tool").mode,
                )
                link = archive.getmember("ets/static/build-tools/tool-link")
                self.assertTrue(link.issym())
                self.assertEqual("tool", link.linkname)

    def test_rejects_unsafe_archive_entry_while_writing_tar_gz(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            archive_path = root / "ets.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("../outside", b"fixture")

            with self.assertRaisesRegex(RuntimeError, "unsafe"):
                StaticSdkArchive(archive_path).write_tar_gz(root / "ets.tar.gz")

    def test_rejects_dynamic_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            archive_path = Path(temporary_directory) / "ets.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("ets/dynamic/api/a.d.ts", b"fixture")
            with self.assertRaisesRegex(RuntimeError, "dynamic"):
                StaticSdkArchive(archive_path).validate()


if __name__ == "__main__":
    unittest.main()
