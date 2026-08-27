// SPDX-License-Identifier: GPL-2.0
/*
 * taimen_pmcrumb -- DO NOT MERGE. Resume-hang witness for taimen (msm8998).
 *
 * The residual s2idle failure on this device resets at alarm + ~21 s, which
 * decodes as DPM_WATCHDOG_TIMEOUT (10 s) + PANIC_TIMEOUT (10 s) + ~1 s: a
 * device suspend/resume callback wedges, the DPM watchdog panics naming the
 * device, and no log channel survives the reset (DRAM is lost, BP-07/BP-11).
 *
 * arm64 has no PM_TRACE_RTC. This is its moral equivalent over SPMI: hook the
 * device_pm_callback_start/end tracepoints and keep a 16-bit hash of the
 * in-flight device's name in the PM8998 PON spare registers 0x88c/0x88d,
 * which survive every reset class on this platform (the bootloader reboot-mode
 * protocol depends on the neighbouring 0x88f surviving). The crumb is written
 * at callback start and zeroed at callback end, so after a watchdog reset:
 *   nonzero crumb -> the SoC died inside that device's PM callback;
 *   zero crumb    -> it died outside any dpm callback (a different mechanism).
 * Decode by hashing every device name (userspace: tk-pmcrumb-decode).
 *
 * ponytail: polls two SPMI writes per PM callback (~2k writes per suspend
 * cycle, tens of us each). Fine for a diagnostic soak, not for shipping.
 */
#define pr_fmt(fmt) "pmcrumb: " fmt

#include <linux/module.h>
#include <linux/tracepoint.h>
#include <linux/regmap.h>
#include <linux/spmi.h>
#include <linux/of.h>
#include <linux/notifier.h>
#include <linux/panic_notifier.h>
#include <linux/kdebug.h>
#include <linux/string.h>
#include <linux/trace_events.h>
#include <linux/irq.h>

/*
 * Survivability, measured 2026-08-22 (testpat write -> full reset -> read):
 * pm8998  0x88c: zeroed by the boot chain.   0x88d: bit0 cleared, 7 bits live.
 * pmi8998 0x88c: zeroed.                     0x88d: fully preserved, 8 bits.
 * So the crumb is split: hash[7:0] -> pmi8998 0x88d, hash[15:8] -> pm8998
 * 0x88d (bit 8 of the stored value is lost; decode matches h & 0xfeff).
 */
#define CRUMB_PM_REG  0x88d
#define CRUMB_PMI_REG 0x88d

static struct regmap *pon_map;
static struct tracepoint *tp_phase;
static atomic_t hits = ATOMIC_INIT(0);
module_param_named(hits, hits.counter, int, 0444);
static int testpat;
module_param(testpat, int, 0444);
static struct regmap *pmi_map;
static struct tracepoint *tp_start, *tp_end;

/*
 * v9. Bug A kills the SoC 115-613 s into an s2idle sleep and the phase crumb
 * reads timekeeping_freeze *END* every time -- i.e. the tick had just been
 * unfrozen, so it dies in the microseconds-long AWAKE window after a micro-wake,
 * not in the ~96 s frozen window between them. Six out of six landing in that
 * window is not chance; something that runs on the wake path is the killer.
 *
 * So: while the system is inside a suspend, stamp the IRQ in flight into the
 * pmi byte (which is otherwise idle once the dpm phases are over) and clear it
 * on handler exit. A death then names the handler, or reads 0 for "no handler
 * was running".
 *
 * Traffic: the micro-wake rate at rest is ~1 per 96 s, so this is ~2 SPMI
 * writes a minute -- three orders of magnitude below the ~2k writes per cycle
 * of dpm-phase stamping that wedged v7 on the suspend side. Do NOT arm this
 * with the SLPI accelerator running (488 irq/s).
 */

static u16 name_hash(const char *s)
{
	u32 h = 2166136261u;		/* FNV-1a, folded to 16 bits */

	while (*s)
		h = (h ^ *s++) * 16777619u;
	h = (h >> 16) ^ (h & 0xffff);
	return h ? h : 1;		/* 0 means "no callback in flight" */
}

static bool resume_side;

static void crumb_write(u16 v)
{
	/* device byte only -- pm8998 0x88d belongs to the phase breadcrumb */
	if (pmi_map)
		regmap_write(pmi_map, CRUMB_PMI_REG, v & 0xff);
}

static void probe_start(void *data, struct device *dev, const char *pm_ops,
			int event)
{
	atomic_inc(&hits);
	/*
	 * SPMI writes on the SUSPEND side wedge the SoC (measured: 3 hangs in
	 * 6 cycles, all torn at probe_end, soak of 2026-08-22). The natural
	 * hang is resume-side, so instrument only resume-class events
	 * (RESUME/THAW/RESTORE/RECOVER = 0xf0) and stay silent on the way down.
	 */
	/* v8: both sides -- bug #2 dies on the way down */
	crumb_write(name_hash(dev_name(dev)));
}

static void probe_end(void *data, struct device *dev, int error)
{
	/* v8: sticky -- the crumb keeps the LAST STARTED callback's device, so a
	 * death between callbacks names the one whose work just completed. */
}

/*
 * Phase breadcrumb: pm8998 0x88d carries ((idx << 1 | start) << 1) for the
 * last suspend_resume core event (bit 0 of the register is lost at boot);
 * the device byte in pmi8998 0x88d is untouched here. Code 0x3f = action
 * not in the table; 0x82 = panic marker (see CRUMB_PANIC).
 */
static const char *const phase_names[] = {
	"suspend_enter", "machine_suspend", "syscore_suspend", "syscore_resume",
	"dpm_prepare", "dpm_suspend", "dpm_suspend_late", "dpm_suspend_noirq",
	"dpm_resume_noirq", "dpm_resume_early", "dpm_resume", "dpm_complete",
	"resume_console", "suspend_console", "thaw_processes", "freeze_processes",
	"CPU_OFF", "CPU_ON",
};

static void probe_phase(void *data, const char *action, int val, bool start)
{
	int i;
	u8 code = 0x3f;

	for (i = 0; i < ARRAY_SIZE(phase_names); i++)
		if (!strcmp(action, phase_names[i])) {
			code = i;
			break;
		}
	/*
	 * timekeeping_freeze START is the first thing that happens once every
	 * CPU is in s2idle, and dpm_resume_noirq is the first phase of the real
	 * resume, so this brackets exactly the window with no dpm traffic in it.
	 */
	/*
	 * Clear the witness byte on the way into the freeze, so 0x00 read after
	 * a death means "nothing recorded", not "a stale value from earlier".
	 */
	if (start && !strcmp(action, "timekeeping_freeze") && pmi_map)
		regmap_write(pmi_map, CRUMB_PMI_REG, 0);

	regmap_write(pon_map, CRUMB_PM_REG, ((code << 1) | start) << 1);
}



/*
 * v11. v10 answered: pmi read 0xc0|0x3f, i.e. NO die() at all -- not an
 * external abort, not a translation fault, not a BUG(), not an SError. Bug A
 * is an explicit panic() call from inside the s2idle loop. Six bits of message
 * hash was not enough to name it: no plausible candidate in the tree lands on
 * 0x3f.
 *
 * So spend every surviving bit on the message. pm8998 0x88d keeps 7 bits (bit 0
 * is cleared by the boot chain) and pmi8998 0x88d keeps 8, so a 15-bit hash of
 * the panic string fits exactly. Against the ~1000 constant panic() literals in
 * the tree plus the formatted ones, 15 bits is decisive where 6 was not.
 *
 * This deliberately gives up the phase crumb and the die encoding, both of
 * which have already been read three times running (timekeeping_freeze END,
 * and no die). Naming the panic is now worth more than repeating them.
 */
static u16 msg_hash15(const char *s)
{
	u32 h = 2166136261u;

	while (*s)
		h = (h ^ *s++) * 16777619u;
	h = (h >> 16) ^ (h & 0xffff);
	return h & 0x7fff;
}

static int pmcrumb_panic(struct notifier_block *nb, unsigned long ev, void *p)
{
	u16 h = msg_hash15(p ? (const char *)p : "?");

	if (pmi_map)
		regmap_write(pmi_map, CRUMB_PMI_REG, h & 0xff);
	regmap_write(pon_map, CRUMB_PM_REG, ((h >> 8) & 0x7f) << 1);
	return NOTIFY_DONE;
}

static struct notifier_block pmcrumb_panic_nb = {
	.notifier_call = pmcrumb_panic,
	.priority = INT_MAX,		/* before anything that might not return */
};

static void find_tp(struct tracepoint *tp, void *priv)
{
	if (!strcmp(tp->name, "device_pm_callback_start"))
		tp_start = tp;
	else if (!strcmp(tp->name, "device_pm_callback_end"))
		tp_end = tp;
	else if (!strcmp(tp->name, "suspend_resume"))
		tp_phase = tp;
}

static int __init pmcrumb_init(void)
{
	struct device_node *np;
	struct spmi_device *sdev;
	int ret;

	np = of_find_compatible_node(NULL, NULL, "qcom,pm8998");
	if (!np)
		return -ENODEV;
	sdev = spmi_find_device_by_of_node(np);
	of_node_put(np);
	if (!sdev)
		return -EPROBE_DEFER;
	pon_map = dev_get_regmap(&sdev->dev, NULL);
	put_device(&sdev->dev);
	if (!pon_map)
		return -ENODEV;

	np = of_find_compatible_node(NULL, NULL, "qcom,pmi8998");
	if (np) {
		sdev = spmi_find_device_by_of_node(np);
		of_node_put(np);
		if (sdev) {
			pmi_map = dev_get_regmap(&sdev->dev, NULL);
			put_device(&sdev->dev);
		}
	}

	for_each_kernel_tracepoint(find_tp, NULL);
	if (!tp_start || !tp_end || !tp_phase)
		return -ENOENT;

	crumb_write(testpat);
	ret = tracepoint_probe_register(tp_start, probe_start, NULL);
	if (ret)
		return ret;
	ret = tracepoint_probe_register(tp_end, probe_end, NULL);
	if (ret) {
		tracepoint_probe_unregister(tp_start, probe_start, NULL);
		return ret;
	}
	tracepoint_probe_register(tp_phase, probe_phase, NULL);
	atomic_notifier_chain_register(&panic_notifier_list, &pmcrumb_panic_nb);
	pr_info("armed v11: pm 0x%x + pmi 0x%x, 15-bit panic-message hash\n",
		CRUMB_PM_REG, CRUMB_PMI_REG);
	return 0;
}

static void __exit pmcrumb_exit(void)
{
	atomic_notifier_chain_unregister(&panic_notifier_list, &pmcrumb_panic_nb);
	tracepoint_probe_unregister(tp_phase, probe_phase, NULL);
	tracepoint_probe_unregister(tp_start, probe_start, NULL);
	tracepoint_probe_unregister(tp_end, probe_end, NULL);
	tracepoint_synchronize_unregister();
	crumb_write(0);
}

module_init(pmcrumb_init);
module_exit(pmcrumb_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("DO NOT MERGE: PM callback crumb in PMIC spare regs");
