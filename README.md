# libhoudini hpe-14 timebomb fix + a second SIGILL/tkill crash caused by broken symlink topology

## TL;DR

**If apps that worked fine before — Prime Video, Kodi, SmartTube, or other
Android apps that need ARM app support on an x86 Android-x86/TV device —
suddenly started freezing on launch, endlessly loading, or crashing
outright around September 2026**, the cause is very likely `libhoudini`:
the compatibility layer that lets ARM-only apps run on x86 Android
hardware. This layer has a hardcoded expiration date baked into it, and
once that date passed, it started failing.

The known community patch ([Vvamp](https://github.com/Vvamp/Libhoudini-hpe-14-timebomb-patch))
fixes that expiration check — but if you apply it and apps *still* crash
(often with `SIGILL`/`SIGABRT` errors mentioning `libhoudini.so`), there's
a second, previously undocumented cause: the patch was likely applied in
a way that breaks a separate internal check inside houdini, unrelated to
the date. See §3–4 for the full fix.

## Status of this document

This is a raw technical dump meant as input for drafting a public README/GitHub writeup — accuracy over polish. Two distinct bugs are covered:

1. **A known, already-documented hardcoded expiration check** ("the timebomb") — community-diagnosed and patched by Vvamp (credit below). Not new.
2. **A previously-undocumented second self-destruct mechanism** that fires when houdini's on-disk symlink topology is disturbed — e.g. by patching bug #1 the naive way (dropping in independent regular files instead of preserving symlinks). This is the new finding from this investigation, and it's the reason some people who applied the timebomb patch still see hangs/crashes.

Everything in section 3 was derived empirically on one specific device/ROM via live tombstone analysis and controlled A/B testing — not from any existing writeup. Confidence is high for the 32-bit mechanism (fully reverse-engineered down to the syscall) and somewhat lower for the exact 64-bit internals (empirically confirmed to be the same root cause via the same fix, but not reverse-engineered to the same instruction-level depth — see caveat in 3.5).

---

## 1. Background

### 1.1 Symptom

Apps that previously worked fine under houdini's ARM-on-x86 translation (Disney+, Prime Video, Kodi, SmartTube, etc.) started hanging on a loading/splash screen or crashing outright, on a schedule that lined up with a hardcoded date check ("timebomb") baked into this houdini build. This is a known behavior of certain leaked/ported Intel Houdini builds used on non-Intel-official Android-x86 ROMs and TV boxes.

### 1.2 Affected build

- Source: `supremegamers/vendor_intel_proprietary_houdini`, `hpe-14` branch
- Originally pulled from Google Play Games for PC, build `UKW1.251130.001`
- Two binaries affected: 32-bit (`libhoudini.so`, x86 ABI) and 64-bit (`libhoudini.so`, x86_64 ABI)
- BuildIds (from the ELF, useful for confirming you have the exact same build):
  - 32-bit: `477847ceec6fe4f9bba47c79fb4511cea1861ebf`
  - 64-bit: `a8f9eb16786fb84cf49ef2a32078627220de72bb`

### 1.3 Original (stock, unpatched) SHA256

```
64-bit: b321439df5a64ff29a62bb168303e58f800e94113c399f52795c42793f7f5f1e
32-bit: f8df8ff7e4894feec7876135f88cb9b9f3e3c9a4221c909c9b45c35bc02f9866
```

Verify what you have before doing anything else:

```sh
sha256sum /vendor/lib64/libhoudini.so /vendor/lib/libhoudini.so
```

(Not `/system/lib64/...` — see section 3.1 on why that path is usually a symlink and why that matters.)

---

## 2. Root cause #1: the hardcoded expiration check ("the timebomb")

**Credit:** originally identified and patched by **Vvamp** — [`Libhoudini-hpe-14-timebomb-patch`](https://github.com/Vvamp/Libhoudini-hpe-14-timebomb-patch) on GitHub. This section documents that patch precisely (exact offsets, bytes, disassembly) for reproducibility; the discovery itself is not ours.

### 2.1 The mechanism

Both binaries contain a single check of the same shape: compare a small integer (likely a build/version counter, not a raw timestamp) against the literal `2`, and branch away from normal execution if the value is `>= 2` (unsigned `jae`). The patch simply NOPs out that branch so the comparison result is never acted on and execution always falls through to the normal path.

### 2.2 64-bit patch

File offset `0xe607e`–`0xe6090`:

```
ORIGINAL:
  0x000e607e: 83 3d bf ac 76 00 02      cmp dword ptr [rip + 0x76acbf], 2
  0x000e6085: 0f 83 68 0d 00 00         jae 0xe6df3        ; <-- 6 bytes patched
  0x000e608b: 48 8d 05 ee e3 88 00      lea rax, [rip + 0x88e3ee]

PATCHED (offset 0xe6085, 6 bytes):
  0x000e6085: 90 90 90 90 90 90         nop × 6
```

### 2.3 32-bit patch

File offset `0x8a293`–`0x8a2a0`:

```
ORIGINAL:
  0x0008a293: 83 ba 50 a3 00 00 02      cmp dword ptr [edx + 0xa350], 2
  0x0008a29a: 0f 83 0b 0d 00 00         jae 0x8afab        ; <-- 6 bytes patched
  0x0008a2a0: 8b 54 24 68               mov edx, dword ptr [esp + 0x68]

PATCHED (offset 0x8a29a, 6 bytes):
  0x0008a29a: 90 90 90 90 90 90         nop × 6
```

Note the two architectures use different addressing (`rip`-relative on x86_64, an absolute-plus-register displacement on x86) for what is structurally the identical check — same comparison, same `jae`, same patch shape.

### 2.4 Patched SHA256

```
64-bit: 847be255d03886974ff0cff09d09b7530301d24671ce0f80172ab3a0629d23b8
32-bit: 96fe3acce9aec00bf5d44a187e727aa6d1ef1cf29043c661fa26d4446044e993
```

Each patched file differs from its original by **exactly these 6 bytes** — confirmed via full binary diff, not just at this one location. No other bytes changed. This is important context for section 3: whatever broke afterward was not caused by any other change to file *content*.

---

## 3. Root cause #2: the symlink-topology self-destruct (new finding)

This is the part that isn't documented anywhere we could find, and explains why some people who apply Vvamp's patch (or any patch) still get hangs/crashes on certain ROMs, while it reportedly works fine for others (e.g. under Waydroid).

### 3.1 The stock file layout is symlinks, not four independent files

On the ROM this was diagnosed on (see section 5), there are only **two real houdini binaries on disk**, at the vendor paths. The two "system" paths are symlinks to them:

```
$ stat /system/lib64/libhoudini.so
  File: /system/lib64/libhoudini.so -> '/vendor/lib64/libhoudini.so'
  ... symbolic link ...
  Modify: 2009-01-01 02:00:00.000000000   <- fixed, reproducible-build timestamp

$ stat /system/vendor/lib64/libhoudini.so   # i.e. /vendor/lib64/libhoudini.so
  ... regular file, 9323480 bytes ...
  Modify: 2009-01-01 02:00:00.000000000
```

Same pattern for the 32-bit pair (`/system/lib/libhoudini.so` → `/vendor/lib/libhoudini.so`).

**If you naively "patch all four paths" by dropping in four independent regular-file copies (even byte-identical ones), you destroy this symlink relationship.** That structural change — not any byte of file content — is what the second check reacts to.

### 3.2 Symptom of this second check firing

Apps crash near/during native library load with no useful backtrace:

```
signal 4 (SIGILL), code 0 (SI_USER), fault addr --------
backtrace:
  #00 pc 00426acf  /system/lib/libhoudini.so (BuildId: 477847ce...)
```

Two details matter immediately:

- `code 0 (SI_USER)` — this is **not a hardware illegal-instruction trap**. `SI_USER` means the signal was explicitly sent via a syscall (`kill`/`tkill`/`raise`), not raised by the CPU decoding a bad opcode. This is deliberate self-termination, not a crash-from-broken-code.
- The single-frame backtrace with no further unwinding is typical of a raw syscall trampoline with no unwind info, not a real stack corruption.

### 3.3 Confirming it's a deliberate `tkill(own_tid, SIGILL)`

The static disassembly at the crash site is a completely mundane 2-argument Linux syscall wrapper:

```
0x00426ac0: push ebx
0x00426ac1: mov eax, [esp+8]      ; syscall number
0x00426ac5: mov ebx, [esp+0xc]    ; arg1
0x00426ac9: mov ecx, [esp+0x10]   ; arg2
0x00426acd: int 0x80
0x00426acf: pop ebx               ; <-- reported crash PC lands here, right after the syscall returns
0x00426ad0: ret
```

The tombstone's live register dump at the fault (Linux's `int 0x80` ABI preserves `ebx`/`ecx`, only clobbering `eax` with the return value):

```
eax 00000000   ebx 00002857   ecx 00000004   edx 00002857
...
pid: 10274, tid: 10327, name: DefaultDispatch
```

`ebx = 0x2857 = 10327` = **the crashing thread's own tid**. `ecx = 4` = **SIGILL**. `eax = 0` = the syscall **succeeded**. This is `tkill(gettid(), SIGILL)` (or equivalent), self-inflicted, confirmed by live register state — not inferred.

This exact signature (same PC `0x426acf`, same tid-in-ebx / `4`-in-ecx pattern) was independently reproduced across multiple different apps (Disney+, SmartTube — 10 separate tombstones for the latter alone) hitting the identical call site, confirming it's a generic, app-independent internal houdini mechanism, not something app-specific.

### 3.4 What decides to call it: the 5-branch dispatch

Backward-tracing the call chain (`0x426ac0` ← `0x426980` ← two call sites at `0x28fc7f` / `0x290035`, each pushing literal `0xee` = `__NR_tkill`) leads to this dispatch, present (with minor variation) at both call sites:

```
call  <fn>                      ; e.g. call 0x2938a0
test  eax, eax
jne   KILL                      ; (1) nonzero return -> self-destruct

call  [eax]                     ; indirect call through a function pointer
mov   eax, [eax + 0xc]
mov   eax, [eax + 0x4c]
mov   eax, [eax + edx*4 + 4]    ; table lookup, indexed by edx
test  eax, eax
je    KILL                      ; (2) looked-up value is NULL       -> self-destruct
cmp   eax, 1
je    KILL                      ; (3) looked-up value == 1          -> self-destruct
cmp   eax, -1
je    KILL                      ; (4) looked-up value == -1         -> self-destruct
lea   edx, [ebx - 0x17c774]     ; a fixed, position-independent sentinel address
cmp   eax, edx
je    KILL                      ; (5) looked-up value == sentinel   -> self-destruct

KILL:
  push <tid>
  push 0xee                     ; __NR_tkill
  call 0x426980                 ; -> tkill(tid, SIGILL)
```

This is the shape of an **integrity/anti-tamper check on a looked-up pointer** (validating some table entry — plausibly a JNI method binding or similar — isn't null, isn't a sentinel value, and isn't a specific known-bad address). It is *not* a date/clock check — there is no time-of-day syscall or arithmetic anywhere near it. We were not able to pin down, purely statically, which of the five branches fires or exactly what real-world object backs the pointer being validated (that would require live tracing/symbol recovery we didn't pursue once the actual trigger was isolated empirically — see 3.6).

### 3.5 The 64-bit crash is a different failure mode, same root cause

Prime Video and Kodi (64-bit processes) did **not** hit this self-destruct path. They hit a genuine, hardware-raised `SIGSEGV` instead:

```
signal 11 (SIGSEGV) ... Abort message: 'exiting due to SIG_DFL handler for signal 11 ...'
backtrace:
  #03 pc 0000000000305a83  /system/lib64/libhoudini.so
  #04 pc 000000000030dc59  /system/lib64/libhoudini.so (s_000067+937)
  #05 pc 000000000050ebd5  /system/lib64/libhoudini.so
```

Disassembling the file at that address shows what looks like a self-modifying/anti-tamper stub (a pointer built from a 16-bit register OR'd into a `0xdead____`-tagged constant, then written to):

```
0x00305a76: movzx eax, r14w
0x00305a7a: mov edi, 0xba
0x00305a7f: add eax, 0xdead0000
0x00305a84: mov byte ptr [rax], 0x61     ; write to a pointer built from r14w — if r14 is garbage, this segfaults
```

**Important evidence:** the reported fault PC (`0x305a83`) falls *in the middle* of the `add eax, 0xdead0000` instruction (it's that instruction's last immediate byte), not at any real instruction boundary. A CPU's instruction pointer at a hardware exception can only ever point to the start of a real instruction — never mid-instruction. That mismatch proves the bytes actually executing at crash time were **not** the bytes currently on disk at that file offset — i.e. this address is inside houdini's JIT/self-modifying code cache, not static code, and static disassembly of the file at this offset can't show you the real logic that ran.

We did not reverse-engineer the 64-bit failure to the same depth as the 32-bit one (no equivalent register/tkill smoking gun — this one really is a genuine unhandled SIGSEGV, not a self-raised signal). But the empirical fix (section 4) resolved both failure modes identically and simultaneously, strongly suggesting a common trigger (most likely an analogous integrity check in the 64-bit binary reacting the same way to the same structural condition, hit via a different code path that ends in a wild-pointer write instead of an explicit `tkill`).

### 3.6 The controlled test that isolated topology (not content) as the trigger

This is the key evidence. Four states were tested on identical hardware/ROM, holding one variable at a time:

| # | File content at `/system/lib(64)/` | Topology | Result |
|---|---|---|---|
| A | Patched (timebomb removed) | **4 independent regular files** (naive "overlay all 4 paths" approach) | **Crash** — self-destruct fires |
| B | **Byte-identical to stock original** | Still 4 independent regular files (same broken topology, just original bytes copied into all 4 module-provided paths) | **Crash, identical signature** |
| C | Stock original, untouched | Stock topology — `/system/lib(64)/` are real symlinks to `/vendor/lib(64)/` | **Works** |
| D | Patched (timebomb removed) | Stock topology restored — only 2 real files, at the vendor paths; `/system/lib(64)/` are symlinks to them | **Works** |

B vs. C is the key comparison: **byte-for-byte identical content, one crashes and one doesn't** — the only variable is whether the file is served as an independent copy vs. resolved through the original symlink relationship. That isolates topology, not content, as the actual trigger. A vs. D then confirms patched content is fine as long as topology is preserved.

(A previously enabled Zygisk/LSPosed setup on the test device was also considered as a possible cause — hook-based anti-tamper detection reacting to Xposed module injection is a very plausible-looking failure mode for a check like this. It was ruled out: state C above passed cleanly with Zygisk/LSPosed still active and unchanged, so the check is not reacting to hooking frameworks.)

### 3.7 Why this explains conflicting reports online

A stock, untouched ROM ships houdini through this symlink structure already, so the check silently passes on every unmodified install — nobody notices it exists. It only becomes visible once *something* changes that topology: a patch applied by copying in independent files, a Magisk/KernelSU module doing the same, or a differently-structured Waydroid deployment. This is a plausible explanation for why the same timebomb patch reportedly works fine under Waydroid (which likely mounts/serves libraries in a structurally different way that doesn't trip this specific check) while causing hangs/crashes on native Android-x86 system-image ROMs that expect the symlink layout to be preserved. See also [`LineageOS-TV-x86/x86_64_tv#3`](https://github.com/LineageOS-TV-x86/x86_64_tv/issues/3) — an open, unresolved community report of "should work" houdini builds still freezing on this class of ROM, consistent with people hitting exactly this structural issue without identifying the cause.

---

## 4. The fix (generic, ROM-agnostic)

### 4.1 Identify your real files first

```sh
ls -la /system/lib64/libhoudini.so /system/lib/libhoudini.so
```

If either line shows `-> /vendor/lib.../libhoudini.so`, **that symlink is load-bearing.** Do not replace it with a regular file. If instead both paths are already independent regular files on your stock ROM (some builds may genuinely ship it that way), this specific failure mode may not apply to you — but preserving whatever the stock relationship is (hardlink, symlink, or independent files with matching stat info) is the safe default regardless.

### 4.2 Patch only the real target file(s)

Apply the byte patch from section 2 to the actual file(s) — the vendor-path files in the symlink case, i.e. exactly the two real binaries, not the symlinks themselves.

The patches in [`patches/`](patches/) automate this: [`patches/patch_64bit.sh`](patches/patch_64bit.sh) and [`patches/patch_32bit.sh`](patches/patch_32bit.sh) each verify the original file's SHA256, apply the NOP patch at the correct offset, and verify the resulting patched SHA256.

### 4.3 Deploy while preserving topology

Whatever packaging/overlay mechanism you use (KernelSU module, Magisk module, or any other systemless-overlay approach), make sure the deployed module tree mirrors the stock structure exactly:

```
system/
  vendor/
    lib64/libhoudini.so   <- real patched file
    lib/libhoudini.so     <- real patched file
  lib64/libhoudini.so     <- symlink to /vendor/lib64/libhoudini.so
  lib/libhoudini.so       <- symlink to /vendor/lib/libhoudini.so
```

For a KernelSU/Magisk-style module zip, this means the zip needs actual symlink entries (not regular files) at the two `system/lib(64)/libhoudini.so` paths, with the link target stored as the entry's content and the Unix `S_IFLNK` mode bit set in `external_attr` — a plain `zip`/`Compress-Archive` call will not produce this; you need something that writes symlink entries explicitly (e.g. Python's `zipfile` with `ZipInfo.external_attr = (stat.S_IFLNK | 0o777) << 16`).

Note: module installers may reset the *timestamp* of extracted files to install time rather than preserving whatever mtime you set in the zip. In testing, this did not matter — topology (symlink vs. independent file) was the operative variable, not mtime — but if you're troubleshooting a build where this fix doesn't fully resolve things, mtime divergence from the stock `2009-01-01`-style reproducible-build timestamp is worth checking next.

### 4.4 Verification

Before testing apps, confirm the deployed state matches intent:

```sh
# Topology: should show real symlinks, not independent files
ls -la /system/lib64/libhoudini.so /system/lib/libhoudini.so

# Content: both nominal paths for each arch should show the SAME hash
# (proving they really do resolve to the same underlying file)
sha256sum /system/lib64/libhoudini.so /system/vendor/lib64/libhoudini.so
sha256sum /system/lib/libhoudini.so   /system/vendor/lib/libhoudini.so

# Expected patched hashes (this exact build):
#   64-bit: 847be255d03886974ff0cff09d09b7530301d24671ce0f80172ab3a0629d23b8
#   32-bit: 96fe3acce9aec00bf5d44a187e727aa6d1ef1cf29043c661fa26d4446044e993
```

If you see a crash matching section 3.2's signature after deploying, re-check topology first — it is very likely the symlink relationship got broken again somewhere in your packaging pipeline, not that the byte patch itself is wrong.

---

## 5. Confirmed working on

- **ROM:** ATV13 / MRDTeamOS (Android 13, x86)
- **Apps confirmed fixed** (previously hanging/crashing, now stable through full playback):
  - Disney+
  - Prime Video
  - Kodi
  - SmartTube (see caveat below)

**Caveat on SmartTube:** the houdini self-destruct was confirmed to be independently killing SmartTube too (10 separate tombstones with the identical `tkill`/`SIGILL` signature from section 3.3, all timestamped during the window when the broken 4-file topology was in place) — this fix resolves that. However, SmartTube has a **separate, unrelated** upstream bug (a `TrackSelectorManager` initialization race — see [yuliskov/SmartTube#6179](https://github.com/yuliskov/SmartTube/issues/6179) and [#6030](https://github.com/yuliskov/SmartTube/issues/6030)) that can still cause a brief (sub-second, in testing) non-instant start on some playback attempts. That race is pure Java/ExoPlayer logic with no houdini involvement (confirmed via clean logcat capture — zero houdini log lines, zero signals, process stays alive throughout) and is not addressed by this fix.

This is likely relevant to any native (non-Waydroid) Android-x86 boot environment shipping this specific houdini build via the symlink structure described in 3.1 — see the referenced [LineageOS-TV-x86/x86_64_tv#3](https://github.com/LineageOS-TV-x86/x86_64_tv/issues/3) for a community report consistent with the same unidentified root cause.

---

## Appendix: raw tombstone excerpts referenced above

**Disney+ (32-bit, tombstone_27):**
```
pid: 10274, tid: 10327, name: DefaultDispatch  >>> com.disney.disneyplus <<<
signal 4 (SIGILL), code 0 (SI_USER), fault addr --------
    eax 00000000  ebx 00002857  ecx 00000004  edx 00002857
    edi 00006db6  esi 00000000
    ebp ba4600b0  esp ba460068  eip db429acf
backtrace:
      #00 pc 00426acf  /system/lib/libhoudini.so (BuildId: 477847ceec6fe4f9bba47c79fb4511cea1861ebf)
```

**SmartTube (32-bit, tombstone_35) — same call site, different app, confirming genericity:**
```
pid: 6549, tid: 6549, name: marttube.stable  >>> org.smarttube.stable <<<
signal 4 (SIGILL), code 0 (SI_USER), fault addr --------
    eax 00000000  ebx 00001995  ecx 00000004  edx 00001995
backtrace:
      #00 pc 00426acf  /system/lib/libhoudini.so (BuildId: 477847ceec6fe4f9bba47c79fb4511cea1861ebf)
```

**Prime Video (64-bit) — genuine SIGSEGV, different mechanism, same root cause per section 3.6:**
```
pid: 767, tid: 900, name: IgniteThread  >>> com.amazon.amazonvideo.livingroom <<<
Fatal signal 6 (SIGABRT), code -1 (SI_QUEUE)
Abort message: 'exiting due to SIG_DFL handler for signal 11, ucontext ...'
backtrace:
      #03 pc 0000000000305a83  /system/lib64/libhoudini.so (BuildId: a8f9eb16786fb84cf49ef2a32078627220de72bb)
      #04 pc 000000000030dc59  /system/lib64/libhoudini.so (s_000067+937)
      #05 pc 000000000050ebd5  /system/lib64/libhoudini.so
```
