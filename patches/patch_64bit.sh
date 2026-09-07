#!/usr/bin/env bash
# Patches the "timebomb" hardcoded expiration check out of the 64-bit
# libhoudini.so (x86_64 ABI, hpe-14 branch, BuildId a8f9eb16786fb84cf49ef2a32078627220de72bb).
#
# NOPs out the `jae` branch at file offset 0xe6085 (6 bytes), which is the
# only difference between the original and patched files. See README.md
# section 2.2 for the full disassembly and rationale.
#
# Usage: ./patch_64bit.sh <path-to-libhoudini.so>

set -euo pipefail

FILE="${1:?Usage: $0 <path-to-64bit-libhoudini.so>}"

OFFSET=$((0xe6085))

ORIG_SHA256="b321439df5a64ff29a62bb168303e58f800e94113c399f52795c42793f7f5f1e"
PATCHED_SHA256="847be255d03886974ff0cff09d09b7530301d24671ce0f80172ab3a0629d23b8"

echo "== Verifying original SHA256 of $FILE"
ACTUAL_SHA256="$(sha256sum "$FILE" | awk '{print $1}')"
if [ "$ACTUAL_SHA256" != "$ORIG_SHA256" ]; then
    echo "ERROR: unexpected SHA256 for $FILE" >&2
    echo "  expected: $ORIG_SHA256" >&2
    echo "  actual:   $ACTUAL_SHA256" >&2
    echo "This script only patches the exact known stock 64-bit build. Aborting." >&2
    exit 1
fi
echo "OK: original hash matches known stock 64-bit build."

echo "== Applying NOP patch at offset 0x$(printf '%x' "$OFFSET") (6 bytes)"
printf '\x90\x90\x90\x90\x90\x90' | dd of="$FILE" bs=1 seek="$OFFSET" count=6 conv=notrunc status=none

echo "== Verifying patched SHA256"
ACTUAL_PATCHED_SHA256="$(sha256sum "$FILE" | awk '{print $1}')"
if [ "$ACTUAL_PATCHED_SHA256" != "$PATCHED_SHA256" ]; then
    echo "ERROR: patch did not produce the expected result" >&2
    echo "  expected: $PATCHED_SHA256" >&2
    echo "  actual:   $ACTUAL_PATCHED_SHA256" >&2
    exit 1
fi
echo "OK: patched hash matches expected result."
echo "Done: $FILE is patched."
