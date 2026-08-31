# Handoff: `porthole build` picks the wrong tree, and the workspace cannot reach the device

Written 2026-08-31 from a taimen WiFi session. **Two defects in porthole's own
tooling, both found by being bitten, neither fixed here** — the owner asked that
core changes be left to a dedicated agent. Two related tool-level fixes *were*
made and are described at the end so you do not redo them.

Everything below is reproducible on the taimen working repo as it stands.

---

## 1. `porthole build auto` defaults to a stale tree instead of finding the work

**What happens.** `porthole build` with no `PORTHOLE_KERNEL_TREE` set:

```
>> tree: /work/linux [wifi-disablekey-test 2f2df08b12e5]
>>       (default; set PORTHOLE_KERNEL_TREE to build a worktree)
>> NOTE: profile says the product branch is taimen-v7.2
>>       this tree is on wifi-disablekey-test -- one of the two is stale
>> REFUSING: tree is Linux 6.18 but linux-postmarketos-qcom-msm8998-7.2 is 7.2
>>   set PORTHOLE_KERNEL_PKG to the aport for this tree, or rebase the tree.
```

The taimen repo has two trees side by side:

| dir | branch | version |
|---|---|---|
| `linux/` | `wifi-disablekey-test` | 6.18 — a stale topic branch |
| `linux-ws/` | `taimen-v7.2` | 7.2 — **the product branch, matching the running kernel** |

`tools/ph-build.sh:51` is `_PH_TREE=${PORTHOLE_KERNEL_TREE:-$_PH_REPO/linux}`.
It defaults and stops. Around line 283 it *already* reads
`PORTHOLE_KERNEL_BRANCH`, notices the branch disagrees, and prints a note — then
builds (or refuses) against the wrong tree anyway. Every invocation this session
needed `PORTHOLE_KERNEL_TREE=linux-ws` typed by hand.

**Why it matters more than the typing.** The refusal above only fired because
the *version token* differed (6.18 vs 7.2). Two trees on the same version but
different branches would not trip it, and the build would silently use the stale
one. That is exactly the hazard the "branch divergence loses fixes" rule exists
for, and here the tooling walks into it by default.

**Suggested shape, deliberately narrow.** Auto-select only when all of:
`PORTHOLE_KERNEL_TREE` is unset, `PORTHOLE_KERNEL_BRANCH` is set, the default
tree is *not* on that branch, and **exactly one** sibling `linux*` tree is. Then
build that one and say so loudly. Anything ambiguous keeps today's behaviour —
silently choosing between two candidate trees is how a half-finished branch gets
flashed, so the bar for switching should be "there is exactly one right answer".

A sketch that was written and then deliberately not applied:

```sh
_ph_autoselect_tree() {
	local want=${PORTHOLE_KERNEL_BRANCH:-} cand match=0 chosen=""
	[ -z "${PORTHOLE_KERNEL_TREE:-}" ] || return 0
	[ -n "$want" ] || return 0
	[ "$(git -C "$_PH_TREE" rev-parse --abbrev-ref HEAD 2>/dev/null)" != "$want" ] || return 0
	for cand in "$_PH_REPO"/linux*; do
		[ -d "$cand" ] && [ "$cand" != "$_PH_TREE" ] || continue
		[ "$(git -C "$cand" rev-parse --abbrev-ref HEAD 2>/dev/null)" = "$want" ] || continue
		match=$((match + 1)); chosen=$cand
	done
	[ "$match" = 1 ] || return 0
	echo ">> tree: default is not on the product branch $want"
	echo ">>       building $chosen instead (the one tree that is)"
	_PH_TREE=$chosen
}
```

Note a wrinkle for whoever implements it: a worktree seen **from inside the
workspace container** is detached as far as git there is concerned, so
`rev-parse --abbrev-ref HEAD` returns `HEAD`. The existing branch-note code at
line ~283 already guards for this (`[ "$head" != HEAD ]`) and the selection must
too, or it will silently never fire in the container — which is where builds
actually run. Whatever lands needs a test that exercises the containerised path,
not just the host one.

---

## 2. The workspace's device key is not authorized on the phone

**What happens.** Every `porthole build mod ... --yes` ends:

```
  | make: Leaving directory '/mnt/linux'
  | scp: Connection closed
porthole: tkmod failed
```

The build succeeds; only the push fails. `scp: Connection closed` names nothing.
The first guess is a stale ssh control master (a real and separate hazard after a
reboot — `ph_ssh_mux_reset` fixes that one). It is not that. From inside the
container:

```
$ ssh -v -i /run/porthole/device_key <user>@<device> echo REACHED
debug1: Offering public key: /run/porthole/device_key ED25519 SHA256:Ap+c...
<user>@<device>: Permission denied (publickey,password,keyboard-interactive).
```

The container reaches the phone fine; **the sandbox's device key is simply not
in the phone's `~/.ssh/authorized_keys`**, most likely lost in the fresh install
noted in a recent taimen handoff. `porthole doctor` reports
`✓ device key /home/user/.porthole/device_key` — it checks the key *exists*,
not that the device accepts it, so doctor is green while every workspace push
fails.

**Two things worth fixing:**

1. **A doctor check that actually tries it.** `ssh -o BatchMode=yes -i <device
   key> <phone> true` from the workspace, reported as its own line. This is the
   difference between "the key file is present" and "the key works", and only the
   second one is what the build needs. Same class of error as trusting an exit
   code over content.
2. **A real message when the push fails.** `tkmod` should distinguish
   "permission denied" from "connection closed" and say
   *"the workspace key is not authorized on the device; add
   `$(ssh-keygen -y -f ~/.porthole/device_key)` to the phone's authorized_keys"*.

**Note for whoever does this:** installing that key is a privileged, security-
relevant write to the device. In this session the attempt was blocked by the
harness classifier, correctly — do not have the tool do it silently. Print the
command and let a human run it, the way `porthole doctor` already prints fixes it
will not apply itself.

The workaround used here: push from the host with
`tools/tk-push-module.sh`, which uses the host's own ssh and works.

---

## 3. Already fixed here — do not redo

Both in `tools/`, both tool-level rather than core:

- **`tools/tk-strip-btf.py`** (new) — a module built from the tree carries
  `.BTF` referencing the *aport* kernel's BTF by type id. MODVERSIONS passes,
  BTF does not, and `btf_module_notify()` fails the load with `-40`, which
  modprobe renders as `could not insert 'mac80211': Symbolic link loop`. On this
  device that meant a reboot with **no `wlan0` at all**. `tk-push-module.sh` now
  calls it. Covered by `tests/test_strip_btf.py`. Full story in
  `brain/traps/a-tree-built-module-carries-btf-the-running-kernel-rejects.md`.
- **`tools/tk-push-module.sh`** — now stages modules into a temp dir before
  touching them, because `.output` belongs to the workspace container's uid and
  is not writable by the host (nor ours to rewrite). Done after the existing
  sibling-mismatch warning, which needs the real build paths.

**A note on the `mod` rung generally.** The ladder documents MODVERSIONS as the
thing that makes `mod` safe to try on an aport-shipping device — "a mismatched
module is *refused* by the loader, loudly, not silently accepted". That is true
and it is not the whole story: BTF is a second gate, it is not mentioned
anywhere, and its refusal is loud but names the wrong thing entirely. Worth a
line in the ladder docs alongside the fix.

---

## 4. `build auto` cannot actually run the rung it recommends (2026-08-31, second session)

The owner's question was "why do you keep avoiding `porthole build auto`". The
honest answer is that it was tried, repeatedly, and each attempt hit a
different wall. All four are reproducible on the taimen repo with a DTS-only
change in `linux-ws`.

### 4a. A preview run consumes the evidence `auto` routes on

This is the important one, because it makes the documented workflow
self-defeating.

`porthole build` (no `--yes`) does not just plan -- it runs an incremental
`make` and routes on what got rebuilt. So the preview *builds the artefact*.
The next invocation measures again, finds everything up to date, and concludes
there is nothing to do:

```
$ porthole build                      # preview
  what make rebuilt
    arch/arm64/boot/dts/qcom/msm8998-google-taimen.dtb
  cheapest rung that covers it: boot

$ porthole build auto --yes           # now actually do it
  what make rebuilt
    nothing
  make rebuilt nothing -- there is nothing to push
```

Nothing was wrong with the tree; the dtb had simply already been built by the
preview. An agent that follows the documented "preview, then run" flow ends up
with a build that refuses to do anything, and the natural next move is to type
the rung by hand -- which the skill explicitly warns against. The routing needs
to be based on something durable (artefact mtime against what is on the device,
or a recorded stamp) rather than on what one `make` invocation happened to
touch.

### 4b. The `boot` rung needs a base image that nothing seeds

```
>> no base image at /tmp/tk-base-boot.img
   seed it once from a known-good UUID-patched boot.img:
     cp <good>.img /tmp/tk-base-boot.img
```

`auto` routes to `boot` without checking that it can run, so the failure lands
after the compile rather than before it. Worse, the instruction is not
followable as written from an agent's position: the build runs **in the
workspace container**, which does not mount the host's `/tmp`, so seeding the
named path on the host changes nothing. Pointing `TK_BASEIMG` at a scratchpad
path fails the same way, for the same reason.

The obvious source is the device itself -- the active slot's boot partition is
by definition a known-good, UUID-patched image:

```sh
dd if=/dev/disk/by-partlabel/boot_$(slot) of=/tmp/boot.img
```

That is four lines and removes the manual step entirely. Doing it by hand plus
`tools/bootimg-repack-dtb.py` and `fastboot boot` worked first try, which is
what this session ended up doing.

### 4c. `--host` is offered as the escape hatch and is not viable

```
$ porthole build boot --yes --host
  building ON THE HOST (--host)
  Failed to install all dependencies, see the error above for details.
```

The host pmbootstrap has no dependencies installed -- which is the entire
reason the workspace exists. Notably the `boot` rung's own description says it
needs "no pmbootstrap", so it is being dragged through an environment check it
does not require.

### 4d. `PORTHOLE_KERNEL_TREE` resolves differently for host and workspace

A relative value works for the workspace build and breaks `--host`:

```
ph-build.sh: line 414: pushd: linux-ws: No such file or directory
```

It should be resolved to an absolute path once, early, against the device
working repo, rather than being interpreted by whatever `pwd` each path
happens to have.

### 4e. Minor: `tk_expired` called with no argument

`tools/tk-to-fastboot.sh` into `tk_wait_fastboot` prints this five times:

```
tools/tk-lib.sh: line 230: [: : integer expected
```

Harmless, but it is noise in exactly the window where a human is watching for
whether the device moved.
