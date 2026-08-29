#!/bin/bash
# SPDX-License-Identifier: MIT
# scope: generic
# needs: BOOTED
# env: HOST, PHONE
# exits: 0 ok · 1 failed
# Install a freshly built kernel module onto the running phone, no reflash.
#
# Why this exists: msm.ko and the panel driver are modules, so a driver change
# needs neither `pmbootstrap install` nor a rootfs flash -- rebuild with
# `make modules` under envkernel and drop the .ko straight into the rootfs.
# That turns a ~10 minute cycle into ~30 seconds, and it avoids the
# install-without-flash_rootfs UUID desync trap entirely.
#
# Two traps this handles:
#   - modules on the phone are XZ-compressed, and kmod decides by *extension*.
#     Copying a plain ELF over msm.ko.xz leaves modprobe unable to load it, in
#     a way that looks like a stale module rather than a broken one.
#   - depmod has to re-run or the new file is ignored.
#
# The kernel image itself is untouched, so vermagic still matches as long as
# only module sources changed. Rebuild+reflash boot.img if you touched the
# kernel proper or the DTS (for the DTS see bootimg-repack-dtb.py).
#
# Usage: tk-push-module.sh path/to/foo.ko [more.ko ...]
set -eu

# shellcheck source=tk-lib.sh
. "$(dirname "$0")/tk-lib.sh"

[ $# -ge 1 ] || { echo "usage: tk-push-module.sh FOO.ko [BAR.ko ...]"; exit 1; }

ping -c1 -W2 "$HOST" >/dev/null 2>&1 || {
    echo "phone is not on the USB network -- boot it first"; exit 1; }

# The phone decides which /lib/modules tree to write into, not this script: a
# hardcoded default was still 6.0.0 long after 6.18 became the daily kernel,
# and the push then silently found nothing to replace.
KVER=${KVER:-$(ssh "${TK_SSH_OPTS[@]}" "$PHONE" uname -r)}
[ -n "$KVER" ] || { echo "could not read the phone's kernel version"; exit 1; }
echo "### /lib/modules/$KVER"

# Warn about siblings left behind.
#
# MODVERSIONS does NOT protect you here, which is the whole point: it checks
# exported *symbol* CRCs, and a struct that appears in no exported signature
# has no CRC to disagree about. Two modules built from one changed header will
# therefore load happily while disagreeing about where that struct's fields
# live -- one writing at the new offsets, the other reading at the old.
#
# Cost on taimen 2026-08-29: a header under drivers/iio/common/qcom_smgr/qmi/
# is compiled into both qcom_smgr.ko and qmi/qmi_sns_smgr.ko. Only the first
# was pushed. Every boot then took a watchdog reset with an empty journal --
# no oops, no panic, nothing to read but androidboot.bootreason=watchdog on
# the boot after. A mixed set is worse than a stale one: a stale set is at
# least self-consistent.
#
# So: name every other .ko built alongside the ones being pushed. This is a
# warning and not a refusal, because pushing a genuine subset is legitimate
# when the change really is confined to one module -- but it should be a
# decision rather than an oversight.
# An array read line by line, not `for x in $(find ...)`: unquoted command
# substitution splits on every space, so a build directory containing a space
# would make this warn about two paths that do not exist while staying silent
# about the one that does -- and this warning exists precisely to be trusted.
_siblings=()
for ko in "$@"; do
    d=$(dirname "$ko")
    while IFS= read -r other; do
        case " $* " in *" $other "*) continue ;; esac
        _siblings+=("$other")
    done < <(find "$d" -name '*.ko' 2>/dev/null)
done
if [ ${#_siblings[@]} -gt 0 ]; then
    echo "### WARNING: built alongside, but NOT being pushed:"
    for s in "${_siblings[@]}"; do echo "###   $s"; done
    echo "### If your change touched a header these share, pushing a subset"
    echo "### corrupts the struct layout between them -- see"
    echo "### brain/traps/pushing-one-module-of-a-pair-corrupts-the-other.md"
fi

for ko in "$@"; do
    [ -f "$ko" ] || { echo "no such module: $ko"; exit 1; }
    base=$(basename "$ko")
    echo "### $base ($(stat -c %s "$ko") bytes, $(date -r "$ko" +%H:%M))"

    scp -q "${TK_SSH_OPTS[@]}" "$ko" "$PHONE:/tmp/$base"
    # Verify what LANDED, not that scp exited 0. On 2026-08-19 a module arrived
    # 0 bytes -- ext4 delayed allocation lost the write across a watchdog reset
    # -- and this script reported success, so IPA then would not load at all and
    # the cause looked like the patch under test. Content, never exit codes.
    want_sum=$(sha256sum "$ko" | cut -d' ' -f1)
    want_size=$(stat -c %s "$ko")
    ssh "${TK_SSH_OPTS[@]}" "$PHONE" "set -e
        target=\$(find /lib/modules/$KVER -name '$base' -o -name '$base.xz' \
                  -o -name '$base.gz' | head -1)
        if [ -z \"\$target\" ]; then echo '  not found in /lib/modules'; exit 1; fi
        dir=\$(dirname \"\$target\")
        got_size=\$(stat -c %s /tmp/$base)
        got_sum=\$(sha256sum /tmp/$base | cut -d' ' -f1)
        [ \"\$got_size\" = '$want_size' ] || { echo \"  !! transfer truncated: \$got_size of $want_size bytes\"; exit 1; }
        [ \"\$got_sum\" = '$want_sum' ] || { echo \"  !! transfer corrupt: sha256 \$got_sum != $want_sum\"; exit 1; }
        sudo rm -f \"\$dir/$base\" \"\$dir/$base\".xz \"\$dir/$base\".gz
        sudo cp /tmp/$base \"\$dir/$base\"
        sudo sync \"\$dir/$base\"
        inst=\$(sha256sum \"\$dir/$base\" | cut -d' ' -f1)
        [ \"\$inst\" = '$want_sum' ] || { echo \"  !! installed copy differs: \$inst\"; exit 1; }
        case \"\$target\" in
            *.xz) sudo xz -f \"\$dir/$base\" ;;
            *.gz) sudo gzip -f \"\$dir/$base\" ;;
        esac
        sudo depmod -a
        echo \"  installed to \$dir\"
        # A bare .ko left beside a stale .ko.xz is invisible and expensive:
        # modprobe resolves the .xz from modules.dep, so the module you pushed,
        # sha256'd and grepped is NEVER the one that loads. That is what made
        # HANDOFF-audio.md 9.7 conclude a function was never called when the
        # instrumentation simply was not in the running binary.
        n=\$(ls \"\$dir/$base\" \"\$dir/$base\".xz \"\$dir/$base\".gz 2>/dev/null | wc -l)
        [ \"\$n\" -eq 1 ] || { echo \"  !! $base exists \$n times in \$dir -- modprobe will pick one at random\"; exit 1; }
        printf '  modprobe resolves: %s\n' \"\$(modinfo -F filename ${base%.ko} 2>/dev/null)\""
done

echo "### done -- reboot or reload the module to pick it up"
