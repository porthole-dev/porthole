# Security

## Scope

porthole runs on a developer's workstation and talks to a development device
over USB. It is not a network service and has no privileged daemon.

Things genuinely worth reporting:

- a path where porthole would execute untrusted content from a device profile
  or config file (config files are **parsed**, never sourced, precisely to avoid
  this — a regression there is a real bug)
- credential or key material leaking into logs, JSON output, or a committed file
- a flaw in the device mutex that lets two workers drive one device
- anything that would let a malicious `profiles/*/device.env` in a pull request
  run code on a reviewer's machine

## Not vulnerabilities

- **`StrictHostKeyChecking=no` and a `/dev/null` known-hosts file.** Deliberate
  and documented: a device under bring-up regenerates its host keys on
  essentially every boot, and with `BatchMode=yes` a real `known_hosts` makes
  every tool fail outright. The link is a USB cable to a device you physically
  own.
- **The passwordless-sudo instruction.** It is printed, never applied, and is
  explicitly documented as belonging on a development device only and never in
  a package others install.

## Reporting

Email the maintainer at the address in the git history rather than opening a
public issue. A fix or an acknowledgement within a week is the intent.
