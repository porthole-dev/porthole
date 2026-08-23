---
name: New device
about: You are porting a device porthole has never seen
labels: new-device
---

**Device:** vendor, model, codename
**SoC:**
**Existing work:** any community device tree, pmaports package, or downstream kernel

## What porthole got wrong

This is the most valuable part. porthole has only ever been proven against one
device, so an assumption that does not hold for yours is a real bug, not user
error.

- [ ] a `device.env` key that does not fit, or one that is missing
- [ ] a tool marked `scope: generic` that is not
- [ ] a brain note that is wrong for your hardware
- [ ] something the checklist does not cover

## Output

```
$ porthole version
(paste)

$ porthole doctor --all --no-device
(paste)
```
