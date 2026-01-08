#!/usr/bin/env bash
set -euo pipefail

URL_X86_64="https://eew.earthquake.tw/releases/linux/x64/oxwu-linux-x86_64.AppImage"
URL_ARM64="https://eew.earthquake.tw/releases/linux/arm64/oxwu-linux-arm64.AppImage"
URL_ARMV7L="https://eew.earthquake.tw/releases/linux/armv7l/oxwu-linux-armv7l.AppImage"

usage() {
  cat <<'EOF'
OXWU Linux patch script

Downloads the official OXWU AppImage, extracts it, overwrites:
  - resources/app/app/main.js
  - resources/app/app/node_modules (from this folder's ./node_modules)
Then outputs either a patched folder or a patched AppImage.

Usage:
  ./patch.sh [--arch x86_64|arm64|armv7l] [--url URL] [--output dir|appimage] [--out PATH] [--workdir DIR] [--keep-work]

Options:
  --arch ARCH          Auto-select download URL by architecture.
                       Supported: x86_64, arm64, armv7l (default: auto-detect via uname -m)
  --url URL            AppImage download URL (default: official eew.earthquake.tw release)
  --output MODE        dir (default) or appimage
  --out PATH           Output path.
                       - dir: output directory (default: ./oxwu-patched.AppDir)
                       - appimage: output file (default: ./oxwu-patched-<arch>.AppImage)
  --workdir DIR        Working directory (default: temp dir)
  --keep-work          Do not delete working directory

Notes:
  - Repacking to AppImage requires appimagetool. If not found, the script will download it.
  - This script expects ./main.js and (optionally) ./node_modules next to patch.sh.
EOF
}

log() { printf '[patch.sh] %s\n' "$*"; }

die() { printf '[patch.sh] ERROR: %s\n' "$*" >&2; exit 1; }

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1"
}

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

ARCH_OVERRIDE=""
URL=""
OUTPUT_MODE="dir"
OUT_PATH=""
WORKDIR=""
KEEP_WORK=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --url)
      URL="${2:-}"; shift 2
      ;;
    --arch)
      ARCH_OVERRIDE="${2:-}"; shift 2
      ;;
    --output)
      OUTPUT_MODE="${2:-}"; shift 2
      ;;
    --out)
      OUT_PATH="${2:-}"; shift 2
      ;;
    --workdir)
      WORKDIR="${2:-}"; shift 2
      ;;
    --keep-work)
      KEEP_WORK=1; shift
      ;;
    *)
      die "Unknown arg: $1"
      ;;
  esac
done

case "$OUTPUT_MODE" in
  dir|appimage) ;;
  *) die "--output must be 'dir' or 'appimage'" ;;
esac

need_cmd chmod
need_cmd mkdir
need_cmd rm
need_cmd cp
need_cmd mv
need_cmd find
need_cmd sed
need_cmd grep
need_cmd uname

detect_arch() {
  local m
  m="$(uname -m)"
  case "$m" in
    x86_64|amd64) echo "x86_64" ;;
    aarch64|arm64) echo "arm64" ;;
    armv7l|armv7*) echo "armv7l" ;;
    *) die "Unsupported architecture from uname -m: $m (use --arch or --url)" ;;
  esac
}

select_url_for_arch() {
  case "$1" in
    x86_64) echo "$URL_X86_64" ;;
    arm64) echo "$URL_ARM64" ;;
    armv7l) echo "$URL_ARMV7L" ;;
    *) die "Unsupported --arch: $1 (supported: x86_64, arm64, armv7l)" ;;
  esac
}

ARCH="${ARCH_OVERRIDE:-}"  
if [[ -z "$ARCH" ]]; then
  ARCH="$(detect_arch)"
fi

if [[ -z "$URL" ]]; then
  URL="$(select_url_for_arch "$ARCH")"
fi

if command -v curl >/dev/null 2>&1; then
  DL_TOOL="curl"
elif command -v wget >/dev/null 2>&1; then
  DL_TOOL="wget"
else
  die "Need curl or wget to download AppImage"
fi

MAIN_JS_SRC="$SCRIPT_DIR/main.js"
[[ -f "$MAIN_JS_SRC" ]] || die "Missing $MAIN_JS_SRC"

NODE_MODULES_SRC="$SCRIPT_DIR/node_modules"

if [[ -z "$WORKDIR" ]]; then
  WORKDIR="$(mktemp -d)"
fi
mkdir -p "$WORKDIR"

cleanup() {
  if [[ "$KEEP_WORK" -eq 1 ]]; then
    log "Keeping workdir: $WORKDIR"
    return
  fi
  rm -rf "$WORKDIR"
}
trap cleanup EXIT

APPIMAGE_FILE="$WORKDIR/oxwu.AppImage"
log "Downloading AppImage..."
if [[ "$DL_TOOL" == "curl" ]]; then
  curl -L --fail -o "$APPIMAGE_FILE" "$URL"
else
  wget -O "$APPIMAGE_FILE" "$URL"
fi
chmod +x "$APPIMAGE_FILE"

log "Extracting AppImage..."
(
  cd "$WORKDIR"
  "$APPIMAGE_FILE" --appimage-extract >/dev/null
)

ROOT="$WORKDIR/squashfs-root"
[[ -d "$ROOT" ]] || die "Extraction failed: $ROOT not found"

TARGET_MAIN="$ROOT/resources/app/app/main.js"
TARGET_NODE_MODULES="$ROOT/resources/app/app/node_modules"

[[ -f "$TARGET_MAIN" ]] || die "Target main.js not found at: $TARGET_MAIN"

log "Patching main.js -> $TARGET_MAIN"
cp -f "$MAIN_JS_SRC" "$TARGET_MAIN"

if [[ -d "$NODE_MODULES_SRC" ]]; then
  log "Patching node_modules -> $TARGET_NODE_MODULES"
  rm -rf "$TARGET_NODE_MODULES"
  # Preserve symlinks/permissions where possible
  cp -a "$NODE_MODULES_SRC" "$TARGET_NODE_MODULES"
else
  log "No ./node_modules found; skipping node_modules patch"
fi

# Output
if [[ -z "$OUT_PATH" ]]; then
  if [[ "$OUTPUT_MODE" == "dir" ]]; then
    OUT_PATH="$SCRIPT_DIR/oxwu-patched.AppDir"
  else
    OUT_PATH="$SCRIPT_DIR/oxwu-patched-${ARCH}.AppImage"
  fi
fi

if [[ "$OUTPUT_MODE" == "dir" ]]; then
  log "Writing patched folder -> $OUT_PATH"
  rm -rf "$OUT_PATH"
  cp -a "$ROOT" "$OUT_PATH"
  log "Done. You can run it via: $OUT_PATH/AppRun"
  exit 0
fi

# Repack to AppImage
APPIMAGETOOL=""
if command -v appimagetool >/dev/null 2>&1; then
  APPIMAGETOOL="appimagetool"
else
  log "appimagetool not found; downloading..."
  # Official continuous build from AppImageKit
  TOOL_URL="https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage"
  APPIMAGETOOL="$WORKDIR/appimagetool-x86_64.AppImage"
  if [[ "$DL_TOOL" == "curl" ]]; then
    curl -L --fail -o "$APPIMAGETOOL" "$TOOL_URL"
  else
    wget -O "$APPIMAGETOOL" "$TOOL_URL"
  fi
  chmod +x "$APPIMAGETOOL"
fi

log "Repacking AppImage -> $OUT_PATH"
(
  cd "$WORKDIR"
  ARCH="$ARCH" "$APPIMAGETOOL" "$ROOT" "$OUT_PATH" >/dev/null
)
chmod +x "$OUT_PATH" || true
log "Done. Patched AppImage: $OUT_PATH"
