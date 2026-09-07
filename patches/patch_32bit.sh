#!/usr/bin/env bash
# Patches the "timebomb" hardcoded expiration check out of the 32-bit
# libhoudini.so (x86 ABI, hpe-14 branch, BuildId 477847ceec6fe4f9bba47c79fb4511cea1861ebf).
#
# NOPs out the `jae` branch at file offset 0x8a29a (6 bytes), which is the
# only difference between the original and patched files. See README.md
# section 2.3 for the full disassembly and rationale.
#
# Usage: ./patch_32bit.sh <path-to-libhoudini.so>

set -euo pipefail

FILE="${1:?Usage: $0 <path-to-32bit-libhoudini.so>}"

OFFSET=$((0x8a29a))

ORIG_SHA256="f8df8ff7e4894feec7876135f88cb9b9f3e3c9a4221c909c9b45c35bc02f9866"
PATCHED_SHA256="96fe3acce9aec00bf5d44a187e727aa6d1ef1cf29043c661fa26d4446044e993"

echo "== Verifying original SHA256 of $FILE"
ACTUAL_SHA256="$(sha256sum "$FILE" | awk '{print $1}')"
if [ "$ACTUAL_SHA256" != "$ORIG_SHA256" ]; then
    echo "ERROR: unexpected SHA256 for $FILE" >&2
    echo "  expected: $ORIG_SHA256" >&2
    echo "  actual:   $ACTUAL_SHA256" >&2
    echo "This script only patches the exact known stock 32-bit build. Aborting." >&2
    exit 1
fi
echo "OK: original hash matches known stock 32-bit build."

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
