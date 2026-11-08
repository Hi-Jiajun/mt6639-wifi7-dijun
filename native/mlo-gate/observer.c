/* Read-only plan observer, not the plan7 adaptation. No register/memory policy writes. */
#include "abi.h"
#ifndef MT_EXPECTED_OPCODE
#define MT_EXPECTED_OPCODE 0x9ac22508U
#endif
static struct kprobe g_probe;
static unsigned long g_plan_hits[11], g_bad_hits;
static int g_registered, g_ready;

static int mtmlo_pre(struct kprobe *probe, struct pt_regs *regs)
{
    unsigned long plan;
    if (!__atomic_load_n(&g_ready, __ATOMIC_ACQUIRE)) return 0;
    plan = regs->regs[2];
    if (probe != &g_probe || regs->pc != (unsigned long)g_probe.addr ||
        regs->regs[8] != 0x44 || plan >= 11) {
        __atomic_fetch_add(&g_bad_hits, 1, __ATOMIC_RELAXED);
        return 0;
    }
    __atomic_fetch_add(&g_plan_hits[plan], 1, __ATOMIC_RELAXED);
    return 0;
}

static int mtmlo_init(void)
{
    int rc;
    g_probe.symbol_name = "wlan_drv_gen4m_eap_6639:mt6639_apsLinkPlanDecision";
    g_probe.offset = 8;
    g_probe.pre_handler = mtmlo_pre;
    rc = register_kprobe(&g_probe);
    if (rc) {
        _printk("mtmlo_observer: register rc=%d\n", rc);
        return rc;
    }
    if (g_probe.opcode != MT_EXPECTED_OPCODE) {
        unregister_kprobe(&g_probe);
        _printk("mtmlo_observer: opcode rejected; probe removed\n");
        return -22;
    }
    g_registered = 1;
    __atomic_store_n(&g_ready, 1, __ATOMIC_RELEASE);
    _printk("mtmlo_observer: ready; readonly=1; offset=8\n");
    return 0;
}

static void mtmlo_exit(void)
{
    unsigned int i;
    __atomic_store_n(&g_ready, 0, __ATOMIC_RELEASE);
    if (g_registered) {
        unregister_kprobe(&g_probe);
        g_registered = 0;
    }
    for (i = 0; i < 11; ++i)
        _printk("mtmlo_observer: plan=%u hits=%lu\n", i,
                __atomic_load_n(&g_plan_hits[i], __ATOMIC_RELAXED));
    _printk("mtmlo_observer: removed bad=%lu missed=%lu\n",
            __atomic_load_n(&g_bad_hits, __ATOMIC_RELAXED), g_probe.nmissed);
}

#ifndef MT_HOST_TEST
__attribute__((section(".gnu.linkonce.this_module"), used, aligned(64)))
struct module __this_module = {.name = "mtmlo_observer", .init = mtmlo_init, .exit = mtmlo_exit};
__attribute__((section(".modinfo"), used))
static const char modinfo[] =
    "name=mtmlo_observer\0license=GPL\0"
    "description=Version-locked read-only MT6639 plan observer\0"
    "vermagic=6.18.21-android17-5-g1d099fcb35e0-abogki538445360-4k SMP preempt mod_unload modversions aarch64\0";
struct modversion { u64 crc; char name[56]; };
/* Only independently recovered target CRCs. Missing imported-symbol CRCs are
 * explicitly documented; never manufacture them or request forced loading. */
__attribute__((section("__versions"), used))
static const struct modversion versions[] = {
    {0x60801db3, "module_layout"}, {0x16b5b21d, "_printk"}
};
__asm__(".section .plt,\"aw\",@progbits\n.byte 0\n"
        ".section .init.plt,\"aw\",@progbits\n.byte 0\n"
        ".section .text.ftrace_trampoline,\"aw\",@progbits\n.byte 0\n"
        ".section .init.text.ftrace_trampoline,\"aw\",@progbits\n.byte 0\n.text\n");
#endif
