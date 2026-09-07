#!/usr/bin/env python3
"""
Packages two patched libhoudini.so files (64-bit and 32-bit, produced by
patch_64bit.sh / patch_32bit.sh) into a KernelSU/Magisk module zip that
preserves the stock symlink topology described in README.md section 3-4:

  system/
    vendor/
      lib64/libhoudini.so   <- real patched file
      lib/libhoudini.so     <- real patched file
    lib64/libhoudini.so     <- REAL symlink to /vendor/lib64/libhoudini.so
    lib/libhoudini.so       <- REAL symlink to /vendor/lib/libhoudini.so

A plain `zip` or `Compress-Archive` call cannot produce true symlink
entries; this script writes them explicitly via ZipInfo.external_attr
with the S_IFLNK mode bit set, per README.md section 4.3.

Usage:
    python3 build_module.py --lib64 <patched_64bit.so> --lib32 <patched_32bit.so> [-o OUTPUT.zip]

By default, refuses to package files that don't match the known patched
SHA256 hashes from README.md section 2.4 (i.e. it wants files that have
already been run through patch_64bit.sh / patch_32bit.sh). Pass --force
to skip that check if you're using a different build.
"""

import argparse
import hashlib
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile

EXPECTED_PATCHED_SHA256 = {
    "lib64": "847be255d03886974ff0cff09d09b7530301d24671ce0f80172ab3a0629d23b8",
    "lib32": "96fe3acce9aec00bf5d44a187e727aa6d1ef1cf29043c661fa26d4446044e993",
}

MODULE_ID = "houdini_timebomb_fix"
MODULE_NAME = "libhoudini hpe-14 timebomb + symlink fix"
MODULE_VERSION = "v1.0"
MODULE_VERSION_CODE = "1"
MODULE_AUTHOR = "https://github.com/itstaftaf/houdini-timebomb-fix"
MODULE_DESCRIPTION = (
    "Patches the libhoudini hpe-14 timebomb (hardcoded expiration check) "
    "and deploys it while preserving the stock /system -> /vendor symlink "
    "topology, avoiding the second SIGILL/tkill self-destruct."
)

UPDATE_BINARY = """#!/sbin/sh
umask 022
OUTFD=$2
ZIPFILE=$3
mount /data 2>/dev/null
[ -f /data/adb/magisk/util_functions.sh ] || abort "! Magisk/KernelSU is not installed"
. /data/adb/magisk/util_functions.sh
install_module
exit 0
"""

UPDATER_SCRIPT = "#MAGISK\n"


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# Unix (3), not whatever OS this script happens to run on. Python's
# zipfile defaults ZipInfo.create_system to 0 (MS-DOS/FAT) when run on
# Windows, and unzip/Android's on-device extractor only interpret the
# upper 16 bits of external_attr as unix mode+symlink bits when the
# entry claims a Unix creator -- get this wrong and symlink entries
# silently extract as regular files containing the link-target text,
# with no error anywhere. This is exactly the failure mode README.md
# section 4.3 warns about with plain `zip`/`Compress-Archive`.
UNIX_CREATE_SYSTEM = 3


def add_file(zf, arcpath, data, mode=0o644):
    zi = zipfile.ZipInfo(arcpath)
    zi.create_system = UNIX_CREATE_SYSTEM
    zi.external_attr = (stat.S_IFREG | mode) << 16
    zi.compress_type = zipfile.ZIP_DEFLATED
    zf.writestr(zi, data)


def add_symlink(zf, arcpath, target):
    zi = zipfile.ZipInfo(arcpath)
    zi.create_system = UNIX_CREATE_SYSTEM
    zi.external_attr = (stat.S_IFLNK | 0o777) << 16
    zi.compress_type = zipfile.ZIP_STORED
    zf.writestr(zi, target)


EXPECTED_SYMLINKS = {
    "system/lib64/libhoudini.so": "/vendor/lib64/libhoudini.so",
    "system/lib/libhoudini.so": "/vendor/lib/libhoudini.so",
}
EXPECTED_REAL_FILES = [
    "system/vendor/lib64/libhoudini.so",
    "system/vendor/lib/libhoudini.so",
]


def verify_extraction(zip_path):
    """
    Extract the built zip with a real `unzip` binary and check with
    os.path.islink()/os.readlink() that the symlink entries actually come
    out as symlinks -- not as regular files containing the link-target
    text, which is the silent failure mode this whole script exists to
    avoid (see README.md section 4.3). Returns True/False/None (None =
    could not verify on this platform/toolchain, not a failure).
    """
    unzip_bin = shutil.which("unzip")
    if unzip_bin is None:
        print("\nWARNING: no `unzip` binary found on PATH -- cannot self-verify symlink "
              "extraction. Verify manually on Linux/WSL/the target device before trusting "
              "this zip:\n"
              f"  unzip -o {zip_path} -d /tmp/check && ls -la /tmp/check/system/lib64 /tmp/check/system/lib",
              file=sys.stderr)
        return None

    if os.name == "nt":
        print("\nNOTE: running on Windows -- Windows filesystems generally can't represent "
              "POSIX symlinks the way `unzip` writes them, so a local extraction test here "
              "would produce a false failure even for a correctly-built zip. The zip's "
              "internal symlink entries were verified directly above (create_system=Unix, "
              "S_IFLNK bit set), which is what actually matters for Android/Linux "
              "extraction. For a full extraction test, run this same zip through "
              "`unzip -o ... -d somedir; ls -la` on WSL, Linux, or the target device.")
        return None

    with tempfile.TemporaryDirectory() as tmpdir:
        result = subprocess.run(
            [unzip_bin, "-o", "-q", zip_path, "-d", tmpdir],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f"\nERROR: unzip failed while self-verifying: {result.stderr}", file=sys.stderr)
            return False

        ok = True
        for arcpath, expected_target in EXPECTED_SYMLINKS.items():
            extracted = os.path.join(tmpdir, arcpath)
            if not os.path.islink(extracted):
                print(f"FAIL: {arcpath} extracted as a REGULAR FILE, not a symlink "
                      f"(this is the exact silent-failure mode section 4.3 warns about)",
                      file=sys.stderr)
                ok = False
                continue
            actual_target = os.readlink(extracted)
            if actual_target != expected_target:
                print(f"FAIL: {arcpath} is a symlink but points to '{actual_target}', "
                      f"expected '{expected_target}'", file=sys.stderr)
                ok = False
            else:
                print(f"PASS: {arcpath} -> {actual_target} (real symlink, confirmed via extraction)")

        for arcpath in EXPECTED_REAL_FILES:
            extracted = os.path.join(tmpdir, arcpath)
            if os.path.islink(extracted):
                print(f"FAIL: {arcpath} extracted as a symlink; expected a real regular file",
                      file=sys.stderr)
                ok = False
            elif not os.path.isfile(extracted):
                print(f"FAIL: {arcpath} did not extract at all", file=sys.stderr)
                ok = False
            else:
                print(f"PASS: {arcpath} is a real file (confirmed via extraction)")

        return ok


def module_prop():
    return (
        f"id={MODULE_ID}\n"
        f"name={MODULE_NAME}\n"
        f"version={MODULE_VERSION}\n"
        f"versionCode={MODULE_VERSION_CODE}\n"
        f"author={MODULE_AUTHOR}\n"
        f"description={MODULE_DESCRIPTION}\n"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--lib64", required=True, help="Path to the patched 64-bit libhoudini.so")
    parser.add_argument("--lib32", required=True, help="Path to the patched 32-bit libhoudini.so")
    parser.add_argument("-o", "--output", default="houdini-timebomb-fix-module.zip", help="Output module zip path")
    parser.add_argument("--force", action="store_true", help="Skip the patched-SHA256 verification")
    args = parser.parse_args()

    if not args.force:
        actual64 = sha256_of(args.lib64)
        actual32 = sha256_of(args.lib32)
        errors = []
        if actual64 != EXPECTED_PATCHED_SHA256["lib64"]:
            errors.append(
                f"--lib64 ({args.lib64}) does not match the expected patched SHA256.\n"
                f"  expected: {EXPECTED_PATCHED_SHA256['lib64']}\n"
                f"  actual:   {actual64}"
            )
        if actual32 != EXPECTED_PATCHED_SHA256["lib32"]:
            errors.append(
                f"--lib32 ({args.lib32}) does not match the expected patched SHA256.\n"
                f"  expected: {EXPECTED_PATCHED_SHA256['lib32']}\n"
                f"  actual:   {actual32}"
            )
        if errors:
            print("ERROR: refusing to package unverified/unpatched files:\n", file=sys.stderr)
            print("\n\n".join(errors), file=sys.stderr)
            print("\nRun patch_64bit.sh / patch_32bit.sh first, or pass --force to override.", file=sys.stderr)
            sys.exit(1)

    with open(args.lib64, "rb") as f:
        lib64_data = f.read()
    with open(args.lib32, "rb") as f:
        lib32_data = f.read()

    with zipfile.ZipFile(args.output, "w") as zf:
        add_file(zf, "META-INF/com/google/android/update-binary", UPDATE_BINARY, mode=0o755)
        add_file(zf, "META-INF/com/google/android/updater-script", UPDATER_SCRIPT, mode=0o644)
        add_file(zf, "module.prop", module_prop(), mode=0o644)

        # Real files, at the vendor paths.
        add_file(zf, "system/vendor/lib64/libhoudini.so", lib64_data, mode=0o644)
        add_file(zf, "system/vendor/lib/libhoudini.so", lib32_data, mode=0o644)

        # Symlinks, at the system paths -- these are true S_IFLNK zip
        # entries, not regular files, so the stock topology survives
        # extraction on-device.
        add_symlink(zf, "system/lib64/libhoudini.so", "/vendor/lib64/libhoudini.so")
        add_symlink(zf, "system/lib/libhoudini.so", "/vendor/lib/libhoudini.so")

    print(f"Wrote {args.output}")
    print("Contents (as declared in the zip's own headers):")
    with zipfile.ZipFile(args.output) as zf:
        for info in zf.infolist():
            mode = info.external_attr >> 16
            kind = "symlink" if stat.S_ISLNK(mode) else "file"
            print(f"  [{kind:7}] {info.filename}")

    print("\nSelf-verifying by actually extracting the zip and checking the result...")
    verified = verify_extraction(args.output)
    if verified is False:
        print(
            "\nThe zip you have is BROKEN -- its symlink entries did not survive a real "
            "extraction. Do not flash this. This usually means the `unzip` used to verify "
            "(or the toolchain producing the zip) doesn't handle unix symlink entries; "
            "please report this as a bug.",
            file=sys.stderr,
        )
        sys.exit(1)
    elif verified is True:
        print("\nAll checks passed: symlink topology survives a real extraction.")

    print(
        "\nInstall this zip as a Magisk or KernelSU module, then reboot.\n"
        "After install, verify on-device with:\n"
        "  ls -la /system/lib64/libhoudini.so /system/lib/libhoudini.so\n"
        "(both should show '-> /vendor/lib.../libhoudini.so', not be regular files)"
    )


if __name__ == "__main__":
    main()
