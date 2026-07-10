#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_DIR="${ROOT_DIR}/meme-data/memes/meme_emoji"
REPO_URL="https://github.com/anyliew/meme_emoji.git"
TARBALL_URL="https://codeload.github.com/anyliew/meme_emoji/tar.gz/refs/heads/main"

mkdir -p "$(dirname "${TARGET_DIR}")"

if [[ "${USE_GIT:-0}" == "1" && -d "${TARGET_DIR}/.git" ]]; then
  git -C "${TARGET_DIR}" pull --ff-only
else
  rm -rf "${TARGET_DIR}"
  if [[ "${USE_GIT:-0}" == "1" ]] && git clone --depth=1 "${REPO_URL}" "${TARGET_DIR}"; then
    :
  else
    TMP_DIR="$(mktemp -d)"
    trap 'rm -rf "${TMP_DIR}"' EXIT
    echo "downloading meme_emoji tarball"
    curl -L --retry 5 --connect-timeout 20 --speed-limit 1024 --speed-time 60 \
      "${TARBALL_URL}" -o "${TMP_DIR}/meme_emoji.tar.gz"
    mkdir -p "${TARGET_DIR}"
    tar -xzf "${TMP_DIR}/meme_emoji.tar.gz" -C "${TMP_DIR}"
    shopt -s dotglob
    mv "${TMP_DIR}"/meme_emoji-main/* "${TARGET_DIR}/"
    shopt -u dotglob
  fi
fi

echo "meme_emoji is ready at ${TARGET_DIR}"
