/* Minimal 5+6 plan gate adapter: alter one local register for plan7 only. */
#include "abi.h"
#ifndef MT_EXPECTED_OPCODE
#define MT_EXPECTED_OPCODE 0x9ac22508U
#endif
static struct kprobe g_plan7_probe;
static unsigned long g_plan7_hits, g_plan7_changed, g_plan7_anomalies;
static int g_plan7_registered, g_plan7_ready;

static int mtmlo_plan7_pre(struct kprobe *probe, struct pt_regs *regs)
{
    unsigned long plan;
    if (!__atomic_load_n(&g_plan7_ready, __ATOMIC_ACQUIRE)) return 0;
    plan = regs->regs[2];
    if (probe != &g_plan7_probe || regs->pc != (unsigned long)g_plan7_probe.addr ||
        regs->regs[8] != 0x44 || plan >= 11) {
        __atomic_fetch_add(&g_plan7_anomalies, 1, __ATOMIC_RELAXED);
        return 0;
    }
    __atomic_fetch_add(&g_plan7_hits, 1, __ATOMIC_RELAXED);
    if (plan == 7) {
        /* Function's next instruction is lsr x8,x8,x2; 0x80 >> 7 == 1,
         * which the following and-w0-1 sequence returns without code changes. */
        regs->regs[8] = 0x80;
        __atomic_fetch_add(&g_plan7_changed, 1, __ATOMIC_RELAXED);
    }
    return 0;
}

static int mtmlo_plan7_init(void)
{
    int rc;
    g_plan7_probe.symbol_name = "wlan_drv_gen4m_eap_6639:mt6639_apsLinkPlanDecision";
    g_plan7_probe.offset = 8;
    g_plan7_probe.pre_handler = mtmlo_plan7_pre;
    rc = register_kprobe(&g_plan7_probe);
    if (rc) {
        _printk("mtmlo_plan7: register rc=%d\n", rc);
        return rc;
    }
    if (g_plan7_probe.opcode != MT_EXPECTED_OPCODE) {
        unregister_kprobe(&g_plan7_probe);
        _printk("mtmlo_plan7: opcode rejected; probe removed\n");
        return -22;
    }
    g_plan7_registered = 1;
    __atomic_store_n(&g_plan7_ready, 1, __ATOMIC_RELEASE);
    _printk("mtmlo_plan7: enabled; scope=plan7-register-only\n");
    return 0;
}

static void mtmlo_plan7_exit(void)
{
    __atomic_store_n(&g_plan7_ready, 0, __ATOMIC_RELEASE);
    if (g_plan7_registered) {
        unregister_kprobe(&g_plan7_probe);
        g_plan7_registered = 0;
    }
    _printk("mtmlo_plan7: removed hits=%lu changed=%lu anomalies=%lu missed=%lu\n",
            __atomic_load_n(&g_plan7_hits, __ATOMIC_RELAXED),
            __atomic_load_n(&g_plan7_changed, __ATOMIC_RELAXED),
            __atomic_load_n(&g_plan7_anomalies, __ATOMIC_RELAXED),
            g_plan7_probe.nmissed);
}

#ifndef MT_HOST_TEST
__attribute__((section(".gnu.linkonce.this_module"), used, aligned(64)))
struct module __this_module = {.name = "mtmlo_plan7", .init = mtmlo_plan7_init, .exit = mtmlo_plan7_exit};
__attribute__((section(".modinfo"), used))
static const char modinfo[] =
    "name=mtmlo_plan7\0license=GPL\0"
    "description=Version-locked MT6639 plan7-only gate adapter\0"
    "vermagic=6.18.21-android17-5-g1d099fcb35e0-abogki538445360-4k SMP preempt mod_unload modversions aarch64\0";
struct modversion { u64 crc; char name[56]; };
__attribute__((section("__versions"), used))
static const struct modversion versions[] = {
    {0x60801db3, "module_layout"}, {0x16b5b21d, "_printk"}
};
__asm__(".section .plt,\"aw\",@progbits\n.byte 0\n"
        ".section .init.plt,\"aw\",@progbits\n.byte 0\n"
        ".section .text.ftrace_trampoline,\"aw\",@progbits\n.byte 0\n"
        ".section .init.text.ftrace_trampoline,\"aw\",@progbits\n.byte 0\n.text\n");
#endif
