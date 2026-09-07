# Configuration reference

Two files, two questions.

| question | file | committed? |
|---|---|---|
| who am I, where is my tooling | `~/.config/porthole/config.env` | **no** |
| which device is this | `profiles/<codename>/device.env` | yes |

A developer with two phones swaps `PORTHOLE_DEVICE` and changes nothing else. A
second developer on the same phone copies neither — they write their own
`config.env` against the same committed profile.

## Resolution order

Lowest precedence to highest:

1. built-in defaults in `lib/porthole.{sh,py}`
2. `profiles/$PORTHOLE_DEVICE/device.env`
3. `${XDG_CONFIG_HOME:-~/.config}/porthole/config.env`
4. `$PORTHOLE_ROOT/.env` — per-checkout override, gitignored
5. the process environment — always wins

`porthole config` prints every resolved value with the layer it came from.

## Format

`KEY=value`, one per line, `#` comments. A leading `export` and spaces around
`=` are tolerated. A quoted value ends at its closing quote, so
`KEY="a"  # why` yields `a`.

Not YAML or TOML on purpose: shell parses it without a dependency and python
parses it in ten lines, and the toolbox is half of each.

## Identity keys

| key | meaning | default |
|---|---|---|
| `PORTHOLE_DEVICE` | which profile to load | *(none)* |
| `PORTHOLE_USER` | ssh username on the device | `user` |
| `PORTHOLE_HOST` | device IP | `172.16.42.1` |
| `PORTHOLE_SSH_PORT` | ssh port | `22` |
| `PORTHOLE_SSH_KEY` | identity file | *(none)* |
| `PORTHOLE_AGENT` | default `TK_AGENT` for the mutex | *(none)* |
| `FASTBOOT`, `ADB` | tool paths | found on `$PATH` |
| `PORTHOLE_PMB_DIR` | pmbootstrap work dir, **host builds** | `~/.local/var/pmbootstrap` |
| `PORTHOLE_SANDBOX_PMB_DIR` | pmbootstrap work dir, **workspace builds** | `~/.local/var/porthole-sandbox` |
| `PORTHOLE_PMBOOTSTRAP_SRC` | pmbootstrap checkout, for `helpers/envkernel.sh` (host builds only) | *(none)* |
| `PORTHOLE_PMAPORTS` | pmaports checkout | pmbootstrap's `cache_git/pmaports` |
| `PORTHOLE_PMAPORTS_<DEVICE>` | pmaports checkout for one device; beats the global | *(none)* |
| `PORTHOLE_WORKDIR` | the device working repo (kernel, pmaports, blobs) | *(none)* |

The two work dirs are **different directories and not interchangeable**. A
rootless container maps your uid and nothing else, so a work dir made by host
root reads as `nobody` inside it and can be neither written nor chowned. Which
one a build used is printed by that build, and by `porthole doctor`.

You should not need to set any of these by hand. `porthole init` resolves them,
and `porthole doctor` prints the resolved path for each one *and the key it
came from* -- which is the question these rows otherwise leave you to answer by
elimination. See [Setting up a new host](NEW-HOST.md).

## Behaviour keys

| key | meaning | default |
|---|---|---|
| `PORTHOLE_POLL` | poll interval, seconds | `0.5` |
| `PORTHOLE_CONNECT_TIMEOUT` | ssh connect timeout | `2` |
| `PORTHOLE_NO_MUX` | `1` disables ssh multiplexing | `0` |
| `PORTHOLE_MUX_PERSIST` | `ControlPersist` value | `60s` |
| `PORTHOLE_TIMING` | `1` prints per-probe timings to stderr | `0` |
| `PORTHOLE_RUNDIR` | ssh control sockets, locks | `$PORTHOLE_ROOT/.run` |

## Device keys

See `profiles/_template/device.env` — every key is listed there with what it
means and, for the ones marked `(TRAP)`, why getting it wrong produces a
confident wrong answer rather than an error.

`PORTHOLE_ARCH_DIR` is derived, not set: `aarch64` → `arm64`, because a package
says one and a kernel tree says the other.

## Legacy names

These are honoured and **win** over their porthole-namespaced twins, so every
command line in older documentation keeps working:

| legacy | behaviour |
|---|---|
| `PHONE` | verbatim if set; else `$PORTHOLE_USER@$HOST` |
| `HOST` | verbatim if set; else `TK_HOST`; else the host part of `PHONE`; else `PORTHOLE_HOST` |
| `TK_HOST` | as above |
| `FASTBOOT` | unchanged |
| `TK_POLL` | beats `PORTHOLE_POLL` |
| `TK_AGENT` | beats `PORTHOLE_AGENT` |
| `TK_FORCE` | unchanged |
| `TK_DEVICE_LOCK`/`_TIMEOUT`/`_MAX`/`_STATE` | unchanged |
| `tools/ph-lib.sh` | still sourceable — a symlink to `lib/porthole.sh` |

Every row is asserted in `tests/test_config.py` and `tests/test_shell_lib.sh`,
so a future refactor cannot quietly break one.
