# SPDX-License-Identifier: MIT
"""The safety boundary, and the only reason it is its own module.

A rule each caller has to remember is a rule one caller will forget. Nothing
runs without a confirmation unless the milestone that owns it says it is safe
-- safe meaning read-only or trivially reversible on the host. Anything that
flashes, writes to the device, or touches a slot drops to a full-screen confirm
naming the exact command.

Pure, and importable with nothing installed, because this is the one piece of
the console that must be testable without a terminal.
"""
from __future__ import annotations

# Words that mean a command can change the device or the host irreversibly.
# A SECOND opinion, not the first: `safe` is a human judgement recorded in the
# milestone table, and a table can be edited in a hurry. If either the table or
# this list says stop, we stop.
DANGEROUS = ("flash", "set_active", "erase", "format", "dd ", "mkfs",
             "reboot", "fastboot", "install", "zap", "rm ")

# Form fields whose value names a slot or a partition. A generated form makes
# an irreversible command easier to assemble than a bare verb ever was, so
# these force the confirm path whatever the milestone table said.
DANGEROUS_FIELDS = ("slot", "partition", "boot", "dtbo", "vendor_boot")


def is_risky(command):
    """Does the command TEXT name something irreversible?"""
    return bool(command) and any(word in command for word in DANGEROUS)


def needs_confirmation(command, safe):
    """Must this be confirmed before it runs?

    Two independent reasons to stop, and either is enough:

      - the milestone did not mark the step safe (it may be destructive, or
        merely long and attention-needing -- both deserve a prompt);
      - the command text names something irreversible, whatever the table said.
    """
    if not command:
        return True
    return (not safe) or is_risky(command)
