# Arkdown ArkTS Static SDK

Builds the OpenHarmony ArkTS 1.2 `ets/static` SDK for the three host targets
supported by Arkdown:

| Target | Build host | OpenHarmony SDK platform |
| --- | --- | --- |
| `linux-x64` | Linux x86_64 | `linux` |
| `windows-x64` | Linux x86_64 | `win` |
| `darwin-arm64` | macOS arm64 | `mac` |

## Install

Download the matching host package from the `v1.0.0` release and install it to
`$HOME/.arkdown/static-sdk`:

```bash
curl -fsSL https://raw.githubusercontent.com/harmony-contrib/static-sdk/main/scripts/install.sh | bash -s -- --release v1.0.0
```

To merge `ets/static` into an existing OpenHarmony SDK, add
`--prefix "$OHOS_SDK_HOME"`. The installer supports `linux-x64`,
`windows-x64`, and `darwin-arm64`; it detects the current host by default. It
verifies the SHA-256 digest published by GitHub, validates the archive before
extracting it, and preserves an existing base `ets/NOTICE.txt`.

An existing `ets/static` is never overwritten implicitly. Add `--force` to
replace it atomically. This package supplies only the ArkTS 1.2 static slice;
keep the other SDK components needed by the intended build in the same SDK
root.

This repository follows the source/orchestrator split used by
[ark_standalone_build/manifest](https://gitee.com/ark_standalone_build/manifest),
but targets the OpenHarmony SDK graph instead of the standalone VM graph. It is
a build controller: it does not fork or patch QEMU/OpenHarmony compiler source.
It creates a temporary downstream product in a disposable source checkout,
derives a static-only SDK description from the upstream description, composes
only that SDK archive, and removes the overlay after the build.

The temporary product is selected as `arkdown-static-sdk@arkdown`. It keeps the
normal OpenHarmony `target_os=ohos`, `target_cpu=arm64`, and cross-build
semantics needed by the static runtime. `sdk_platform` independently selects
the Linux, Windows, or Darwin host tools to package. The build uses upstream's
`is_llvm_build` graph boundary to avoid loading system-image targets, then
points its compiler paths back to the downloaded official host Clang prebuilt;
it does not build a separate `out/llvm-install` toolchain.

The product derives its component registrations from `host_product`, adds the
owners required by the selected static SDK labels, and prunes every component's
default product modules. It therefore retains components such as `ets_runtime`
and `napi` when upstream `external_deps` must resolve them, without pulling in
their complete product package lists. The product deliberately does not copy
the roughly 181-component definition used by the complete OpenHarmony SDK.

Each retained component keeps its upstream inner-kit metadata so GN can resolve
typed `external_deps`, but its default product module list is pruned. An upstream
`check_innerkits_path` allowlist also disables bulk-building every declared
inner kit. Direct dependency edges remain active, so the SDK description's
current 35 `ets/static` delivery labels are the only artifact roots. The custom
GN composition instantiates OpenHarmony's archive and NOTICE templates
directly; it never instantiates the NDK or full-SDK verification/signing targets.

Source synchronization currently uses the official OpenHarmony manifest. The
standalone manifest cannot be used as-is: it does not include the complete
`interface/sdk-js` and `developtools/ace_ets2bundle` packaging path required by
`ets/static`. The compile and package graph is static-only even though the
initial source checkout is not yet a reduced manifest.

## Prerequisites

- Python 3.10 or newer.
- Git and the `repo` launcher.
- About 200 GB of free disk space for a clean OpenHarmony checkout and build.
- Linux x86_64 for Linux and Windows artifacts.
- Apple Silicon macOS for the Darwin arm64 artifact.

Install the same Python 3 `repo` launcher used by Ark standalone build:

```bash
mkdir -p "$HOME/bin"
curl -fsSL https://gitee.com/oschina/repo/raw/fork_flow/repo-py3 \
  -o "$HOME/bin/repo"
chmod +x "$HOME/bin/repo"
```

## Build

Create a disposable OpenHarmony source tree:

```bash
python3 scripts/sync_source.py \
  --source work/openharmony \
  --revision master
```

Download host prebuilts once:

```bash
bash work/openharmony/build/prebuilts_download.sh
```

Build one artifact:

```bash
python3 scripts/build_static_sdk.py \
  --source work/openharmony \
  --target linux-x64 \
  --jobs 4
```

Validate product loading, GN generation, and the complete Ninja dependency
graph without compiling outputs:

```bash
python3 scripts/build_static_sdk.py \
  --source work/openharmony \
  --target linux-x64 \
  --graph-only
```

Use `windows-x64` on a Linux x86_64 builder and `darwin-arm64` on an
Apple Silicon builder. Add `--download-prebuilts` when the source tree does not
already contain the host prebuilts.

Artifacts are written to `dist/`:

```text
arkdown-ets-static-<target>-<revision>.zip
arkdown-ets-static-<target>-<revision>.zip.sha256
arkdown-ets-static-<target>-<revision>.tar.gz
arkdown-ets-static-<target>-<revision>.tar.gz.sha256
arkdown-ets-static-<target>-<revision>.zip.manifest.json
source-manifest-<target>-<revision>.xml
```

The ZIP is the archive produced by OpenHarmony. The reproducible `.tar.gz`
contains the same SDK tree with normalized archive metadata. The sidecar
manifest records both digests and the exact commits of the SDK behavior owners,
and validates that the archive contains the complete static toolchain, APIs,
stdlib, bindings, and plugins.

## Build contract

The generated GN arguments include:

```text
build_ohos_sdk=true
build_ohos_ndk=false
sdk_build_arkts=true
sdk_for_hap_build=false
enable_archive_sdk=true
enable_process_notice=true
sdk_check_flag=false
is_llvm_build=true
startup_init_with_param_base=true
sdk_platform=linux|win|mac
```

The controller also derives the six Clang path arguments from the upstream
`clang_version` and the current host prebuilt directory. This preserves the SDK
cross-build behavior while preventing an unrelated local LLVM build from
becoming a prerequisite.

`sdk_check_flag` is disabled because the upstream completeness list describes
the entire OpenHarmony SDK. This repository performs a stricter static-only
archive check instead. The build also passes `--skip-partlist-check=true` so
the upstream product whitelist accepts the temporary downstream product; it
does not disable GN dependency checking or archive validation.

## Source integrity

Run builds only in the disposable tree created by `sync_source.py`. Some
upstream npm build actions create `node_modules` or update lockfiles in their
checkout. Those files never enter this repository, and no source change is
committed or carried into another build.
