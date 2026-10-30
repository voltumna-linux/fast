# USB authentication key: tooling available in the FAST image

Research ticket: `.scratch/auth-nominale/issues/03-strumenti-usb.md`
Findings feed: `.scratch/auth-nominale/issues/04-auth-console-usb.md`

All local evidence is read-only from the build tree `build/tmp/work/d_9755_h14dsh-fast-linux/`
and `build/tmp/deploy/images/d-9755-h14dsh/` (target `d-9755-h14dsh`, distro override `zen5`).
Facts marked **[local]** were verified against built rootfs/manifest/recipe/kernel-config on disk.
Facts marked **[docs]** come from upstream primary documentation (URLs given).
Facts marked **[host-test]** were verified empirically with the build host's `ssh-keygen`
(OpenSSH 9.6p1) — target binaries were **not** executed; the 10.3p1 source was then checked to
confirm identical behaviour in the target version.

Rootfs layout note: the built image trees are
`.../fast-{sre,sde}/1.0/rootfs/` with the actual `/usr` content under
`rootfs/.osdir/<image>-d-9755-h14dsh-4.0/{bin,sbin,lib,libexec,share}` and configuration under
`rootfs/etc/`. The system is usrmerge (`/bin -> usr/bin`, `/lib -> usr/lib`, `/lib64 -> lib`), so
`osdir/bin/ssh-keygen` is `/usr/bin/ssh-keygen` (= `/bin/ssh-keygen`) at runtime. **[local]**

`fast-sre` (runtime image) and `fast-sde` (development image) contain identical
auth/USB-relevant content; `fast-sde` only adds toolchain, `-dev`/`-doc` packages, `perl`,
`git`, etc. (manifest diff). Everything below holds for both unless stated. **[local]**

## 1. ssh-keygen

| Fact | Evidence |
|---|---|
| `ssh-keygen` **is installed**: `/usr/bin/ssh-keygen`, package `openssh-keygen` **10.3p1** | manifest `build/tmp/deploy/images/d-9755-h14dsh/fast-sre-d-9755-h14dsh.manifest` (`openssh-keygen zen5 10.3p1`); binary `rootfs/.osdir/fast-sre-d-9755-h14dsh-4.0/bin/ssh-keygen` **[local]** |
| Recipe builds portable OpenSSH **10.3p1** | `meta-voltumna/openembedded-core/meta/recipes-connectivity/openssh/openssh_10.3p1.bb` (PV, `SRC_URI` = `openssh-10.3p1.tar.gz`) **[local]** |
| `-Y sign` / `-Y verify` were introduced in **OpenSSH 8.1** (2019-10-09), not 8.2: release notes 8.1, New Features: *"ssh-keygen(1): add an experimental lightweight signature and verification ability. Signatures may be made using regular ssh keys held on disk or stored in a ssh-agent and verified against an authorized_keys-like list of allowed keys."* OpenSSH 8.2 then added `-Y find-principals`. The ticket's "OpenSSH >= 8.2" requirement is therefore satisfied with margin; the true floor is 8.1. | https://www.openssh.com/txt/release-8.1 , https://www.openssh.com/txt/release-8.2 **[docs]** |
| In 10.3p1 the whole `-Y` family exists: `sign`, `verify`, `find-principals`, `match-principals`, `check-novalidate`, plus the `ALLOWED SIGNERS` file format | `ssh-keygen.1` at tag `V_10_3_P1`, https://raw.githubusercontent.com/openssh/openssh-portable/V_10_3_P1/ssh-keygen.1 **[docs]** |

### Passphrase-protected keys (behaviour of `ssh-keygen`)

Verified against `ssh-keygen.c` at tag `V_10_3_P1` (function `load_identity()` and the `-Y sign`
path) **[docs]**, cross-checked by experiment on the host **[host-test]**:

- Unencrypted private key: no prompt at all.
- Encrypted private key given with `-f <private key>` to `-Y sign`:
  - the global **`-P <passphrase>` option supplies the passphrase non-interactively**
    (source: `load_identity()` uses `identity_passphrase` when set before falling back to
    `read_passphrase()`). It is not listed in the `-Y sign` synopsis but works —
    verified on host 9.6p1 and present in 10.3p1 source. **Beware: the passphrase is visible in
    `argv`** (see `ps`).
  - otherwise ssh-keygen prompts `Enter passphrase for "<key>": ` on the controlling tty
    (`read_passphrase(..., RP_ALLOW_STDIN)`); with no tty it falls back to `$SSH_ASKPASS`
    (governed by `SSH_ASKPASS_REQUIRE` since OpenSSH 8.4). Non-interactively with an empty/wrong
    passphrase it fails immediately with `Load key "...": incorrect passphrase supplied to
    decrypt private key` (exit ≠ 0) — it does not hang. **[host-test]**
  - **`-f <public key>` route: if the private half is loaded in `ssh-agent`, signing happens via
    the agent with no passphrase prompt** (man page: *"may refer to either a private key, or a
    public key with the private half available via ssh-agent(1)"*; source calls
    `ssh_get_authentication_socket()` / `ssh_agent_sign()`). **[docs + host-test]**
- `ssh-keygen -y -f <key>` (extract public key from a private key): prompts for the passphrase
  when the key is encrypted; `-P <passphrase>` is accepted and suppresses the prompt. **[host-test]**
- `ssh-keygen -Y verify` needs **no private key and no passphrase** — inputs are the message, the
  `.sig` file, and a public `allowed_signers` file of the form
  `principal namespaces="<ns>" <keytype> <base64-key>`. End-to-end on the host:
  `Good "test" signature for test with ED25519 key SHA256:...`, exit 0; wrong namespace →
  `namespace does not match`, exit 255. **[host-test]**
- **`ssh-agent` / `ssh-add` are NOT installed** in either image (absent from `osdir/bin` and the
  manifest). The recipe puts them in package `openssh-misc`
  (`FILES:${PN}-misc = "${bindir}/ssh* ..."`, `openssh_10.3p1.bb:222`), and
  `openssh-misc-10.3p1-r0.zen5.rpm` exists in the image's own feed
  (`fast-sre/1.0/oe-rootfs-repo/rpm/zen5/`) — installing it is a one-line image change. **[local]**

## 2. pam_exec and the login/agetty PAM flow

| Fact | Evidence |
|---|---|
| `libpam` (Linux-PAM) **1.7.2** is installed, with 22 modules in `/lib/security/`: `pam_unix`, `pam_env`, `pam_securetty`, `pam_nologin`, `pam_faildelay`, `pam_group`, `pam_limits`, `pam_motd`, `pam_mail`, `pam_umask`, `pam_namespace`, `pam_systemd`, ... — **but `pam_exec.so` is NOT among them** | manifest (`libpam zen5 1.7.2`, `pam-plugin-*` list without `pam-plugin-exec`); `rootfs/.osdir/fast-sre-d-9755-h14dsh-4.0/lib/security/` listing **[local]** |
| `pam_exec` is a standard Linux-PAM 1.7.2 module (`modules/pam_exec/`); its options include `expose_authtok`, `seteuid`, `quiet`, `quiet_log`, `log=file`, `type=`. By default the helper runs with the real UID of the calling process — for `login` that is root. | `pam_exec.8.xml` at tag `v1.7.2`, https://raw.githubusercontent.com/linux-pam/linux-pam/v1.7.2/modules/pam_exec/pam_exec.8.xml **[docs]** |
| The Yocto `libpam` recipe auto-splits every module into a `pam-plugin-<name>` package (`PACKAGES_DYNAMIC += "^${MLPREFIX}pam-plugin-.*"`). **`pam-plugin-exec-1.7.2-r0.zen5.rpm` is built and present in the image package feed**, so enabling it is only `IMAGE_INSTALL += "pam-plugin-exec"` — no recipe changes. It would land at `/lib/security/pam_exec.so`. | `meta-voltumna/openembedded-core/meta/recipes-extended/pam/libpam_1.7.2.bb:58`; `fast-sre/1.0/oe-rootfs-repo/rpm/zen5/pam-plugin-exec-1.7.2-r0.zen5.rpm` **[local]** |
| Serial console login flow: `serial-getty@ttyS0.service` is enabled (`etc/systemd/system/getty.target.wants/serial-getty@ttyS0.service`, from the `systemd-serialgetty` recipe) → `/usr/sbin/agetty` (util-linux 2.41.6) on `/dev/ttyS0` at `--keep-baud 115200,...` → execs `/bin/login` (shadow 4.19.4). `getty@tty1` is enabled as well. | rootfs `etc/systemd/system/getty.target.wants/`, `osdir/.../lib/systemd/system/serial-getty@.service` (`ExecStart=-/usr/sbin/agetty ...`), manifest (`shadow`, `util-linux-agetty`, `systemd-serialgetty`) **[local]** |
| `agetty` itself does no authentication; PAM is used by shadow's `login`, which reads **`/etc/pam.d/login`** (Debian-style shadow config). Its auth stack: `auth optional pam_faildelay.so`, `auth [success=ok ...] pam_securetty.so`, `auth requisite pam_nologin.so`, `auth include common-auth`, `auth optional pam_group.so`; `common-auth` = `pam_unix.so nullok` → `pam_deny` → `pam_permit`. | rootfs `etc/pam.d/login`, `etc/pam.d/common-auth` **[local]** |
| Root login on the serial port is permitted: `/etc/securetty` (package `shadow-securetty`) lists `console`, `ttyS0`–`ttyS6` (and more). | rootfs `etc/securetty` **[local]** |
| Layer hooks that already touch PAM: `recipes-extended/pam/libpam_%.bbappend` (root layer; adds `limits.d/dpdk.conf`, injects `pam_limits` into `common-session*`) and `meta-voltumna/recipes-extended/pam/libpam_%.bbappend` (comments out `pam_warn` in `other`). PAM stack customization by bbappend is an established pattern in this repo. | the two bbappends **[local]** |

So the natural hook point is an `auth` line in `/etc/pam.d/login` (or `common-auth`) *before*
`auth include common-auth`, e.g. `auth sufficient pam_exec.so ...`, but it requires adding
`pam-plugin-exec` to the image first. Alternatives (facts, not prescriptions): replace/wrap
`/bin/login` (agetty's `--login-program`, via a unit drop-in on `serial-getty@.service`), or a
custom PAM module.

## 3. USB tooling

Installed in both images (manifest + `osdir/bin`, `osdir/sbin`) **[local]**:

- `mount`, `umount`, `mountpoint` (util-linux **2.41.6**, `/usr/bin/mount`)
- `blkid` (`/usr/sbin/blkid`), `lsblk`, `findmnt`, `findfs`, `wipefs` (util-linux 2.41.6)
- `udevadm` (systemd-**259.5**-based `udev` package; `/usr/bin/udevadm`)
- `modprobe`/`insmod`/etc. (kmod **34.2**), `systemd-mount`, `busctl`
- `mkfs.fat` (dosfstools 4.2) — only needed to *create* filesystems
- **No** `udisks2`, `pmount`, `autofs`, no automount units, no custom rules in
  `etc/udev/rules.d/`; `etc/fstab` has **no** USB/removable entry (contents verified).
  Nothing mounts the stick automatically — the auth script must mount and unmount itself.

Kernel `6.18.33-amd-standard` config —
`build/tmp/work/d_9755_h14dsh-fast-linux/linux-amd/6.18.33/linux-d_9755_h14dsh-standard-build/.config` **[local]**:

| Feature | Setting | Consequence |
|---|---|---|
| `CONFIG_VFAT_FS`, `CONFIG_FAT_FS`, `CONFIG_MSDOS_FS` | `=y` | FAT/vfat mounts work out of the box, no modprobe needed |
| `CONFIG_NLS_CODEPAGE_437=y`, `CONFIG_NLS_ISO8859_1=y`, `CONFIG_NLS_DEFAULT="iso8859-1"` | built-in | 8.3/latin1 names fine; UTF-8 long names via `nls_utf8` |
| `CONFIG_NLS_UTF8=m` | module shipped (`lib/modules/6.18.33-amd-standard/kernel/fs/nls/nls_utf8.ko` + many other `nls_*.ko`) | `mount -o utf8` or auto-load via `modprobe nls_utf8` works |
| **`CONFIG_EXFAT_FS` is not set** | **disabled** | **exFAT sticks cannot be mounted at all** (no module in `lib/modules`) |
| `CONFIG_USB=y`, `USB_XHCI_HCD=y`, `USB_EHCI_HCD=y`, `USB_OHCI_HCD=y`, `CONFIG_USB_STORAGE=y`, `CONFIG_BLK_DEV_SD=y` | built-in | USB mass-storage sticks work with no modules |
| **`CONFIG_USB_UAS` is not set** | disabled | only classic BOT (`usb-storage`) sticks enumerate; UAS-only devices will not |
| `CONFIG_DEVTMPFS=y` | built-in | kernel device nodes appear directly in `/dev` |

**Where USB devices appear:** with devtmpfs + udev (systemd-udevd, rules `60-persistent-storage.rules`
shipped in `osdir/lib/udev/rules.d/`) a stick shows up as a block device **`/dev/sdX`**
(and `/dev/sdX1` for partitions), with stable symlinks under **`/dev/disk/by-id/`,
`/dev/disk/by-uuid/`, `/dev/disk/by-label/`, `/dev/disk/by-path/`**. It stays **unmounted** until
something mounts it; mount points `/media` and `/mnt` exist (base-files) but are empty. Identify
the device with `lsblk`/`blkid` (e.g. by FAT LABEL or UUID), mount with `mount /dev/disk/by-... /mnt/...`
as root, then `umount`. **[local: rules present, fstab empty of USB entries, kernel config]**

## 4. Shells / interpreters for the script

Verified in `osdir/bin` and manifest **[local]**:

| Interpreter | Version | In fast-sre | In fast-sde |
|---|---|---|---|
| bash | **5.3** (`/bin/sh -> /usr/bin/bash.bash`, also `/bin/bash`) | yes | yes |
| python3 | **3.14.7** (`/usr/bin/python3` → `python3.14`; `python3-core` + many `python3-*` modules incl. `python3-unixadmin`, `python3-json`) | yes | yes |
| gawk | 5.4.0 (provides `awk`) | yes | yes |
| perl | (full perl + modules) | **no** | yes |
| busybox, dash, ash | — | **not present** | **not present** |
| screen | 5.0.1 (in `/etc/shells`) | yes | yes |

`/etc/shells` (rootfs `etc/shells`): `/bin/sh`, `/usr/bin/bash`, `/usr/bin/screen`.

## Constraints for the serial-USB auth script

1. **Verification needs no secrets on the machine**: `ssh-keygen -Y verify` (OpenSSH 10.3p1, well
   above the 8.1 floor) works with only public inputs — message, `.sig`, and an `allowed_signers`
   file kept on the host disk. This is the only `-Y` flow that is passphrase-free by construction.
   Format: `principal namespaces="<ns>" <keytype> <base64-key>`; wrong namespace ⇒ exit 255.
2. **If the machine must sign** (unlikely for auth): a passphrase-protected key forces either an
   interactive prompt (breaks a non-interactive PAM helper), `$SSH_ASKPASS` (no askpass helper is
   installed), `-P <passphrase>` (passphrase visible in `argv`), or `ssh-agent` — **which is not
   installed**; it needs `openssh-misc` added to the image (rpm already in the feed).
3. **`pam_exec.so` is missing from both images**: add `pam-plugin-exec` to `IMAGE_INSTALL`
   (rpm ready in the feed, no recipe change) before any `pam_exec` line. Hook point: an `auth`
   line in `/etc/pam.d/login` before `auth include common-auth` (stack content documented above);
   the helper runs as root (can mount/umount). `expose_authtok` would hand it the password typed
   at the `pam_unix` prompt — avoid keeping that in the environment/log.
4. **USB stick must be FAT/vfat**: exFAT is disabled in the kernel (`CONFIG_EXFAT_FS` unset) and
   no module exists; vfat is built-in (with `nls_utf8` loadable for UTF-8 names). vfat is
   case-insensitive and has no Unix permissions — fine for keys/signatures, but paths must be
   matched case-insensitively or normalized.
5. **No automount exists**: the script must locate the device (`lsblk`/`blkid` or
   `/dev/disk/by-*`), `mount` it, read, and `umount` — all tools are present (util-linux 2.41.6,
   udev 259.5). USB sticks appear as `/dev/sdX`; UAS-only sticks won't enumerate
   (`CONFIG_USB_UAS` off).
6. **Write it in bash 5.3 or python3 3.14**: both are in both images; perl only in fast-sde; there
   is no busybox/dash. Keep `/bin/sh` compatibility if invoked via PAM (sh is bash).
7. **Console layout**: the serial login is `serial-getty@ttyS0.service` (agetty 115200 →
   `/bin/login` shadow 4.19.4 → `/etc/pam.d/login`); root is allowed on `ttyS0`–`ttyS6`
   (`/etc/securetty`). A non-root "auth service" user would also need `pam_securetty` not to be
   an obstacle (it only restricts root).