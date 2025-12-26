#!/bin/bash
# Build whatsapp-cli binary.
#
# Usage:
#   ./build.sh              # Build for current platform only
#   ./build.sh --all        # Build for all supported platforms
#
# Output goes to ../jean_claude/bin/ with platform suffixes.

set -e

cd "$(dirname "$0")"

BIN_DIR="../jean_claude/bin"
mkdir -p "$BIN_DIR"

build_platform() {
    local GOOS="$1"
    local GOARCH="$2"
    local OUTPUT="$BIN_DIR/whatsapp-cli-${GOOS}-${GOARCH}"

    echo "Building $GOOS/$GOARCH..."
    CGO_ENABLED=0 GOOS="$GOOS" GOARCH="$GOARCH" go build \
        -ldflags="-s -w" \
        -o "$OUTPUT" .
}

if [[ "$1" == "--all" ]]; then
    # Build for all supported platforms
    build_platform darwin arm64
    build_platform darwin amd64
    build_platform linux amd64
    build_platform linux arm64
else
    # Build for current platform only
    GOOS="$(go env GOOS)"
    GOARCH="$(go env GOARCH)"
    build_platform "$GOOS" "$GOARCH"
fi

echo ""
echo "Built binaries:"
ls -lh "$BIN_DIR"/whatsapp-cli-* 2>/dev/null || echo "  (none)"
