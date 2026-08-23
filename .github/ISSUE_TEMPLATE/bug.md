---
name: Bug
about: Something does not work
labels: bug
---

## What happened

## What you expected

## Reproduce

```
(the exact command)
```

## Environment

```
$ porthole version
(paste -- this includes your OS, python and host tool versions)
```

## Relevant checks

```
$ porthole doctor --all
(paste)
```

<details><summary>If a tool hung</summary>

Did `PORTHOLE_NO_MUX=1 <the command>` fix it? If so a stale ssh control master
is involved, which means a reboot path is failing to tear the master down —
please say which command you ran before it.
</details>
