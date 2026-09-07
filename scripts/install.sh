#!/usr/bin/env bash
set -euo pipefail
export LC_ALL=C

REPO_URL="${ARKDOWN_STATIC_SDK_REPO_URL:-https://github.com/harmony-contrib/static-sdk}"
RELEASE_TAG="${ARKDOWN_STATIC_SDK_RELEASE_TAG:-latest}"
PREFIX="${ARKDOWN_STATIC_SDK_PREFIX:-${HOME}/.arkdown/static-sdk}"
TARGET="${ARKDOWN_STATIC_SDK_TARGET:-auto}"
DOWNLOAD_BASE_URL="${ARKDOWN_STATIC_SDK_DOWNLOAD_BASE_URL:-}"
GITHUB_TOKEN="${ARKDOWN_STATIC_SDK_GITHUB_TOKEN:-${GITHUB_TOKEN:-}}"
FORCE=0
KEEP_ARCHIVE=0

usage() {
  cat <<'USAGE'
Usage:
  install.sh [options]

Network installer for the Arkdown ArkTS static SDK.

Options:
  --prefix DIR       OpenHarmony SDK root. Default: $HOME/.arkdown/static-sdk
  --target TARGET    auto, linux-x64, windows-x64, or darwin-arm64
  --release TAG      GitHub Release tag. Default: latest
  --repo URL         GitHub repository URL
  --download-base-url URL
                     Download the selected Release asset from URL/<asset>
  --force            Atomically replace an existing ets/static directory
  --keep-archive     Keep the downloaded archive under <prefix>/downloads
  -h, --help         Show this help

Examples:
  curl -fsSL https://raw.githubusercontent.com/harmony-contrib/static-sdk/main/scripts/install.sh | bash -s -- --release v1.0.0
  bash scripts/install.sh --prefix "$OHOS_SDK_HOME" --release v1.0.0
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --prefix|--destination)
      PREFIX="${2:-}"
      shift 2
      ;;
    --target)
      TARGET="${2:-}"
      shift 2
      ;;
    --release|--version)
      RELEASE_TAG="${2:-}"
      shift 2
      ;;
    --repo)
      REPO_URL="${2:-}"
      shift 2
      ;;
    --download-base-url)
      DOWNLOAD_BASE_URL="${2:-}"
      shift 2
      ;;
    --force)
      FORCE=1
      shift
      ;;
    --keep-archive)
      KEEP_ARCHIVE=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [ -z "${PREFIX}" ] || [ -z "${REPO_URL}" ] || [ -z "${RELEASE_TAG}" ]; then
  usage >&2
  exit 2
fi

detect_target() {
  local system
  local machine
  system="$(uname -s)"
  machine="$(uname -m)"
  case "${system}:${machine}" in
    Darwin:arm64|Darwin:aarch64)
      printf '%s\n' darwin-arm64
      ;;
    Linux:x86_64|Linux:amd64)
      printf '%s\n' linux-x64
      ;;
    MINGW*:x86_64|MINGW*:amd64|MSYS*:x86_64|MSYS*:amd64|CYGWIN*:x86_64|CYGWIN*:amd64)
      printf '%s\n' windows-x64
      ;;
    *)
      echo "unsupported host: ${system} ${machine}; pass --target explicitly" >&2
      exit 2
      ;;
  esac
}

case "${TARGET}" in
  auto)
    TARGET="$(detect_target)"
    ;;
  linux-x64|windows-x64|darwin-arm64)
    ;;
  *)
    echo "unsupported --target: ${TARGET}" >&2
    exit 2
    ;;
esac

repository_path="${REPO_URL#https://github.com/}"
repository_path="${repository_path#http://github.com/}"
repository_path="${repository_path%.git}"
repository_path="${repository_path%/}"
case "${REPO_URL}" in
  https://github.com/*|http://github.com/*)
    ;;
  *)
    echo "--repo must be a github.com owner/repository URL" >&2
    exit 2
    ;;
esac
case "${repository_path}" in
  */*) ;;
  *)
    echo "--repo must be a github.com owner/repository URL" >&2
    exit 2
    ;;
esac
if [ "$(printf '%s' "${repository_path}" | awk -F/ '{print NF}')" -ne 2 ] || \
    [[ ! "${repository_path}" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; then
  echo "--repo must be a github.com owner/repository URL" >&2
  exit 2
fi
case "${RELEASE_TAG}" in
  latest|*[!A-Za-z0-9._-]*|"")
    if [ "${RELEASE_TAG}" != latest ]; then
      echo "unsafe --release value: ${RELEASE_TAG}" >&2
      exit 2
    fi
    ;;
esac

if ! command -v curl >/dev/null 2>&1 && ! command -v wget >/dev/null 2>&1; then
  echo "curl or wget is required to download SDK releases" >&2
  exit 1
fi
if ! command -v tar >/dev/null 2>&1; then
  echo "tar is required to install the SDK" >&2
  exit 1
fi

INSTALLED_ETS="${PREFIX}/ets"
INSTALLED_STATIC="${INSTALLED_ETS}/static"
INSTALLED_NOTICE="${INSTALLED_ETS}/NOTICE.txt"
if { [ -e "${INSTALLED_STATIC}" ] || [ -L "${INSTALLED_STATIC}" ]; } && [ "${FORCE}" != 1 ]; then
  echo "SDK already contains ${INSTALLED_STATIC}" >&2
  echo "use --force to replace it" >&2
  exit 1
fi

try_download_file() {
  local url="$1"
  local destination="$2"
  if command -v curl >/dev/null 2>&1; then
    local curl_args=(
      -fL
      --http1.1
      --retry 3
      --retry-delay 2
      --speed-limit 1024
      --speed-time 60
      --silent
      --show-error
      -o "${destination}"
    )
    if [ -n "${GITHUB_TOKEN}" ]; then
      curl_args+=(-H "Authorization: Bearer ${GITHUB_TOKEN}")
    fi
    curl "${curl_args[@]}" -- "${url}"
  else
    local wget_args=(--timeout=60 --tries=4 -O "${destination}")
    if [ -n "${GITHUB_TOKEN}" ]; then
      wget_args+=(--header="Authorization: Bearer ${GITHUB_TOKEN}")
    fi
    wget "${wget_args[@]}" -- "${url}"
  fi
}

sha256_file() {
  local path="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    LC_ALL=C sha256sum "${path}" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    LC_ALL=C shasum -a 256 "${path}" | awk '{print $1}'
  elif command -v openssl >/dev/null 2>&1; then
    LC_ALL=C openssl dgst -sha256 -r "${path}" | awk '{print $1}'
  else
    echo "sha256sum, shasum, or openssl is required to verify the SDK" >&2
    return 1
  fi
}

mkdir -p "${PREFIX}"
TEMPORARY_ROOT="$(mktemp -d "${PREFIX}/.arkdown-static-sdk-install.XXXXXX")"
trap 'rm -rf "${TEMPORARY_ROOT}"' EXIT

METADATA_PATH="${TEMPORARY_ROOT}/release.json"
if [ "${RELEASE_TAG}" = latest ]; then
  METADATA_URL="https://api.github.com/repos/${repository_path}/releases/latest"
else
  METADATA_URL="https://api.github.com/repos/${repository_path}/releases/tags/${RELEASE_TAG}"
fi

echo "Arkdown ArkTS static SDK installer"
echo "repo:      ${REPO_URL}"
echo "release:   ${RELEASE_TAG}"
echo "target:    ${TARGET}"
echo "prefix:    ${PREFIX}"
echo "metadata:  ${METADATA_URL}"
try_download_file "${METADATA_URL}" "${METADATA_PATH}"

ASSET_INFO="${TEMPORARY_ROOT}/asset-info.tsv"
LC_ALL=C awk -v prefix="arkdown-ets-static-${TARGET}-" '
  function json_string(line, value) {
    value = line
    sub(/^[^:]*:[[:space:]]*"/, "", value)
    sub(/",?[[:space:]]*$/, "", value)
    return value
  }
  /^[[:space:]]*"tag_name":[[:space:]]*"/ {
    tag = json_string($0)
  }
  /^[[:space:]]*"name":[[:space:]]*"/ {
    name = json_string($0)
  }
  /^[[:space:]]*"digest":[[:space:]]*"/ {
    digest = json_string($0)
  }
  /^[[:space:]]*"browser_download_url":[[:space:]]*"/ {
    url = json_string($0)
    if (index(name, prefix) == 1 && name ~ /\.tar\.gz$/) {
      print name "\t" digest "\t" url "\t" tag
    }
    name = ""
    digest = ""
  }
' "${METADATA_PATH}" >"${ASSET_INFO}"

if [ "$(wc -l <"${ASSET_INFO}" | tr -d ' ')" != 1 ]; then
  echo "release must contain exactly one ${TARGET} tar.gz asset" >&2
  exit 1
fi

IFS=$'\t' read -r ASSET_NAME ASSET_DIGEST ASSET_URL RESOLVED_TAG <"${ASSET_INFO}"
case "${ASSET_NAME}" in
  *[!A-Za-z0-9._-]*|"")
    echo "unsafe release asset name: ${ASSET_NAME}" >&2
    exit 1
    ;;
esac
if ! [[ "${ASSET_DIGEST}" =~ ^sha256:[0-9a-fA-F]{64}$ ]]; then
  echo "release asset does not publish a SHA-256 digest: ${ASSET_NAME}" >&2
  exit 1
fi
if [ -n "${DOWNLOAD_BASE_URL}" ]; then
  ASSET_URL="${DOWNLOAD_BASE_URL%/}/${ASSET_NAME}"
fi

ARCHIVE_PATH="${TEMPORARY_ROOT}/${ASSET_NAME}"
echo "asset:     ${ASSET_NAME}"
echo "resolved:  ${RESOLVED_TAG:-${RELEASE_TAG}}"
echo "downloading: ${ASSET_URL}"
try_download_file "${ASSET_URL}" "${ARCHIVE_PATH}"

EXPECTED_SHA256="$(printf '%s' "${ASSET_DIGEST#sha256:}" | tr 'A-F' 'a-f')"
ACTUAL_SHA256="$(sha256_file "${ARCHIVE_PATH}" | tr 'A-F' 'a-f')"
if [ "${ACTUAL_SHA256}" != "${EXPECTED_SHA256}" ]; then
  echo "SHA-256 mismatch for ${ASSET_NAME}" >&2
  echo "expected: ${EXPECTED_SHA256}" >&2
  echo "actual:   ${ACTUAL_SHA256}" >&2
  exit 1
fi
echo "sha256:    ${ACTUAL_SHA256}"

MEMBER_LIST="${TEMPORARY_ROOT}/members.txt"
tar -tzf "${ARCHIVE_PATH}" >"${MEMBER_LIST}"
while IFS= read -r member; do
  case "${member}" in
    ""|/*|../*|*/../*|*/..|*\\*)
      echo "unsafe SDK archive entry: ${member}" >&2
      exit 1
      ;;
    ets|ets/|ets/NOTICE.txt|ets/static|ets/static/|ets/static/*)
      ;;
    *)
      echo "unexpected SDK archive entry: ${member}" >&2
      exit 1
      ;;
  esac
done <"${MEMBER_LIST}"

if ! tar -tvzf "${ARCHIVE_PATH}" | LC_ALL=C awk '
  substr($0, 1, 1) != "-" && substr($0, 1, 1) != "d" { exit 1 }
'; then
  echo "SDK archive contains unsupported links or special files" >&2
  exit 1
fi

EXTRACTED_ROOT="${TEMPORARY_ROOT}/extracted"
mkdir -p "${EXTRACTED_ROOT}"
tar -xzf "${ARCHIVE_PATH}" -C "${EXTRACTED_ROOT}"

STAGED_STATIC="${EXTRACTED_ROOT}/ets/static"
STAGED_NOTICE="${EXTRACTED_ROOT}/ets/NOTICE.txt"
for required in \
  "${STAGED_STATIC}/api" \
  "${STAGED_STATIC}/kits" \
  "${STAGED_STATIC}/arkts" \
  "${STAGED_STATIC}/build-tools/ets2panda/bin"
do
  if [ ! -d "${required}" ]; then
    echo "downloaded static SDK is incomplete: ${required}" >&2
    exit 1
  fi
done
if [ ! -f "${STAGED_NOTICE}" ]; then
  echo "downloaded static SDK does not contain ets/NOTICE.txt" >&2
  exit 1
fi

mkdir -p "${INSTALLED_ETS}"
if [ -e "${INSTALLED_NOTICE}" ] || [ -L "${INSTALLED_NOTICE}" ]; then
  cp -p "${STAGED_NOTICE}" "${STAGED_STATIC}/NOTICE.txt"
fi

BACKUP_STATIC="${TEMPORARY_ROOT}/previous-static"
REPLACED_EXISTING=0
if [ -e "${INSTALLED_STATIC}" ] || [ -L "${INSTALLED_STATIC}" ]; then
  mv "${INSTALLED_STATIC}" "${BACKUP_STATIC}"
  REPLACED_EXISTING=1
fi

if ! mv "${STAGED_STATIC}" "${INSTALLED_STATIC}"; then
  if [ "${REPLACED_EXISTING}" = 1 ]; then
    mv "${BACKUP_STATIC}" "${INSTALLED_STATIC}"
  fi
  echo "failed to activate the static SDK" >&2
  exit 1
fi

if [ ! -e "${INSTALLED_NOTICE}" ] && [ ! -L "${INSTALLED_NOTICE}" ]; then
  if ! mv "${STAGED_NOTICE}" "${INSTALLED_NOTICE}"; then
    mv "${INSTALLED_STATIC}" "${TEMPORARY_ROOT}/failed-static"
    if [ "${REPLACED_EXISTING}" = 1 ]; then
      mv "${BACKUP_STATIC}" "${INSTALLED_STATIC}"
    fi
    echo "failed to install the static SDK NOTICE" >&2
    exit 1
  fi
fi

if [ "${KEEP_ARCHIVE}" = 1 ]; then
  ARCHIVE_DIR="${PREFIX}/downloads"
  mkdir -p "${ARCHIVE_DIR}"
  mv "${ARCHIVE_PATH}" "${ARCHIVE_DIR}/${ASSET_NAME}"
fi

echo
echo "installed: ${INSTALLED_STATIC}"
echo "release:   ${RESOLVED_TAG:-${RELEASE_TAG}}"
echo "target:    ${TARGET}"
