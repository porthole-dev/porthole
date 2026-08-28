---
id: qmi-data-len-is-u32-on-the-host
title: QMI_DATA_LEN fields must be u32 on the host since 7.2, or every request is -EINVAL
scope: soc:msm8998
subsystem: sensors
severity: finding
confidence: proven
evidence: "On taimen/7.2.2, `dmesg` carries exactly 16 `qmi_encode: Invalid data length` and exactly 16 `qcom_smgr: Failed to send buffering request: -22` -- a 1:1 pairing, and -22 is -EINVAL, returned by drivers/soc/qcom/qmi_encdec.c:392. Upstream fe099c387e06 changed QMI_DATA_LEN encoding to `memcpy(&data_len_value, buf_src, sizeof(u32))`; sns_smgr_buffering_req.item_len is a u8 followed by items[], so the read takes three bytes of the array as the high bytes of the length."
refutes: "the 7.2 sensor regression is in the smgr driver's logic; the QMI service changed; the sensors need a DTS change; the sensor firmware rejects the request"
first-learned: 2026-08-29
---

**The question** — why do the light and proximity sensors stop reporting after
moving msm8998 from 6.18 to 7.2, when the driver applied unchanged and loads
fine?

**The answer** — the QMI encoder changed underneath it. Upstream
`fe099c387e06` ("soc: qcom: preserve CPU endianness for QMI_DATA_LEN", in 7.2)
replaced a width-aware read of the length field:

```c
-       val8 = *(u8 *)buf_src;              /* 6.18: reads ONE byte */
-       data_len_value = (u32)val8;
+       memcpy(&data_len_value, buf_src, sizeof(u32));   /* 7.2: reads FOUR */
```

with an unconditional 4-byte read, on the stated premise that "QMI_DATA_LEN is
always of type `u32` on the host". Any driver that declares that field
narrower now has the following struct members read as the high bytes of its
length. `sns_smgr_buffering_req` declares

```c
	u8 item_len;
	struct sns_smgr_buffering_req_item items[SNS_SMGR_DATA_TYPE_COUNT];
```

so `data_len_value` picks up three bytes of `items[0]`, comes out far larger
than `elem_len`, and `qmi_encode()` bails at qmi_encdec.c:392 with -EINVAL
before anything reaches the ADSP. The sensor never refuses the request; the
request is never sent.

**What this rules out** — that anything is wrong with the smgr driver's logic,
the sensor firmware, the DT, or the QMI service on the ADSP side. Nothing in
the driver's behaviour changed and no message ever left the host. It also rules
out reading the count of affected sensors as a clue: every sensor whose stream
is armed through a buffering request fails identically, so which ones a user
notices is about which ones the desktop polls, not about which are broken.

**The fix**, and the trap inside it: the struct field becomes `u32`, but
`.elem_size` must then be written as the **wire** size explicitly rather than
`sizeof_field(...)`. `qmi_encode()` derives the on-wire width from elem_size:

```c
data_len_sz = temp_ei->elem_size == sizeof(u8) ? sizeof(u8) : sizeof(u16);
```

so widening the field while leaving `.elem_size = sizeof_field(struct x, len)`
silently changes the message from a 1-byte to a 2-byte length and breaks it a
second, quieter way. In-tree `drivers/soc/qcom/qcom_pdr_msg.c` is the pattern
to copy: `u32 domain_list_len;` in the struct, `.elem_size = sizeof(u8)`
hardcoded in the ei_array.

**How it was established** — the 1:1 count match between the encoder error and
the driver error, then reading fe099c387e06 against
`drivers/iio/common/qcom_smgr/qmi/qmi_sns_smgr.{c,h}`. Not yet fixed: applying
it means auditing every QMI_DATA_LEN site in that driver, not just the
buffering request. Related: [[kernel-7-2-rebase-is-cheap]].
