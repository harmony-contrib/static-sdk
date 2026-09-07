#!/usr/bin/env bash
set -euo pipefail
export LC_ALL=C

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
INSTALLER="${REPO_ROOT}/scripts/install.sh"
WORK_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/arkdown-static-sdk-install-test.XXXXXX")"
trap 'rm -rf "${WORK_ROOT}"' EXIT

ASSET_NAME="arkdown-ets-static-linux-x64-testrevision.tar.gz"
ASSET_ROOT="${WORK_ROOT}/assets"
STAGE_ROOT="${WORK_ROOT}/stage"
FAKE_BIN="${WORK_ROOT}/bin"
PREFIX="${WORK_ROOT}/sdk"
mkdir -p \
  "${ASSET_ROOT}" \
  "${STAGE_ROOT}/ets/static/api" \
  "${STAGE_ROOT}/ets/static/kits" \
  "${STAGE_ROOT}/ets/static/arkts" \
  "${STAGE_ROOT}/ets/static/build-tools/ets2panda/bin" \
  "${FAKE_BIN}" \
  "${PREFIX}/ets/dynamic/api"
printf 'static notice\n' >"${STAGE_ROOT}/ets/NOTICE.txt"
printf 'api fixture\n' >"${STAGE_ROOT}/ets/static/api/example.d.ets"
printf 'kit fixture\n' >"${STAGE_ROOT}/ets/static/kits/example.d.ets"
printf 'stdlib fixture\n' >"${STAGE_ROOT}/ets/static/arkts/example.ets"
printf '#!/bin/sh\n' >"${STAGE_ROOT}/ets/static/build-tools/ets2panda/bin/es2panda"
chmod +x "${STAGE_ROOT}/ets/static/build-tools/ets2panda/bin/es2panda"
COPYFILE_DISABLE=1 tar -C "${STAGE_ROOT}" -czf "${ASSET_ROOT}/${ASSET_NAME}" ets

if command -v sha256sum >/dev/null 2>&1; then
  SHA256="$(LC_ALL=C sha256sum "${ASSET_ROOT}/${ASSET_NAME}" | awk '{print $1}')"
else
  SHA256="$(LC_ALL=C shasum -a 256 "${ASSET_ROOT}/${ASSET_NAME}" | awk '{print $1}')"
fi

METADATA="${WORK_ROOT}/release.json"
cat >"${METADATA}" <<EOF
{
  "tag_name": "v-test",
  "assets": [
    {
      "name": "${ASSET_NAME}",
      "digest": "sha256:${SHA256}",
      "browser_download_url": "https://downloads.example/${ASSET_NAME}"
    }
  ]
}
EOF

cat >"${FAKE_BIN}/curl" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
destination=""
url=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    -o)
      destination="$2"
      shift 2
      ;;
    -H)
      shift 2
      ;;
    --retry|--retry-delay|--speed-limit|--speed-time)
      shift 2
      ;;
    -fL|--http1.1|--silent|--show-error|--)
      shift
      ;;
    *)
      url="$1"
      shift
      ;;
  esac
done
case "${url}" in
  https://api.github.com/*)
    cp "${TEST_RELEASE_METADATA}" "${destination}"
    ;;
  https://downloads.example/*|file://*)
    cp "${TEST_RELEASE_ASSET}" "${destination}"
    ;;
  *)
    echo "unexpected URL: ${url}" >&2
    exit 1
    ;;
esac
EOF
chmod +x "${FAKE_BIN}/curl"

printf 'base notice\n' >"${PREFIX}/ets/NOTICE.txt"
PATH="${FAKE_BIN}:${PATH}" \
TEST_RELEASE_METADATA="${METADATA}" \
TEST_RELEASE_ASSET="${ASSET_ROOT}/${ASSET_NAME}" \
  bash -s -- \
    --prefix "${PREFIX}" \
    --target linux-x64 \
    --release v-test \
    >"${WORK_ROOT}/install.log" <"${INSTALLER}"

test -f "${PREFIX}/ets/static/api/example.d.ets"
test -x "${PREFIX}/ets/static/build-tools/ets2panda/bin/es2panda"
test -d "${PREFIX}/ets/dynamic/api"
test "$(cat "${PREFIX}/ets/NOTICE.txt")" = "base notice"
test "$(cat "${PREFIX}/ets/static/NOTICE.txt")" = "static notice"
grep -q '^installed: .*ets/static$' "${WORK_ROOT}/install.log"
grep -q '^sha256:    ' "${WORK_ROOT}/install.log"

if PATH="${FAKE_BIN}:${PATH}" \
    TEST_RELEASE_METADATA="${METADATA}" \
    TEST_RELEASE_ASSET="${ASSET_ROOT}/${ASSET_NAME}" \
      bash "${INSTALLER}" \
        --prefix "${PREFIX}" \
        --target linux-x64 \
        --release v-test \
        >"${WORK_ROOT}/existing.log" 2>&1; then
  echo "installer unexpectedly replaced an existing SDK without --force" >&2
  exit 1
fi
grep -q 'use --force to replace it' "${WORK_ROOT}/existing.log"

PATH="${FAKE_BIN}:${PATH}" \
TEST_RELEASE_METADATA="${METADATA}" \
TEST_RELEASE_ASSET="${ASSET_ROOT}/${ASSET_NAME}" \
  bash "${INSTALLER}" \
    --prefix "${PREFIX}" \
    --target linux-x64 \
    --release v-test \
    --force \
    >/dev/null

BAD_METADATA="${WORK_ROOT}/bad-release.json"
sed "s/sha256:${SHA256}/sha256:$(printf '0%.0s' {1..64})/" \
  "${METADATA}" >"${BAD_METADATA}"
if PATH="${FAKE_BIN}:${PATH}" \
    TEST_RELEASE_METADATA="${BAD_METADATA}" \
    TEST_RELEASE_ASSET="${ASSET_ROOT}/${ASSET_NAME}" \
      bash "${INSTALLER}" \
        --prefix "${WORK_ROOT}/bad-sdk" \
        --target linux-x64 \
        --release v-test \
        >"${WORK_ROOT}/digest.log" 2>&1; then
  echo "installer unexpectedly accepted an invalid digest" >&2
  exit 1
fi
grep -q 'SHA-256 mismatch' "${WORK_ROOT}/digest.log"
test ! -e "${WORK_ROOT}/bad-sdk/ets/static"

bash -n "${INSTALLER}"
echo "shell installer tests passed"
