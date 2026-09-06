from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from static_sdk.builder import (
    PRODUCT_NAME,
    PRODUCT_SELECTOR,
    SourceTree,
    StaticSdkArchive,
    StaticSdkBuilder,
    StaticSdkOverlay,
    TARGETS,
)


class StaticSdkOverlayTests(unittest.TestCase):
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
        self.assertIn("is_llvm_build=true", command[-1])
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

    def test_rejects_dynamic_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            archive_path = Path(temporary_directory) / "ets.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("ets/dynamic/api/a.d.ts", b"fixture")
            with self.assertRaisesRegex(RuntimeError, "dynamic"):
                StaticSdkArchive(archive_path).validate()


if __name__ == "__main__":
    unittest.main()
