/* Version-locked registration bridge. Default build is diagnostic bare-only.
 * At priv_driver_cmds+0x94 the normal caller is about to test its lookup result.
 * Substitute the original handler only for a bare, one-shot SET_TWT_PARAMS.
 * The original caller, after leaving kprobe context, performs its KCFI check
 * and invokes that handler, whose argc=1 rejection cannot enqueue a TWT action.
 */
#include "../mlo-gate/abi.h"
#ifdef MT_TWT_PROTOCOL
#define MT_NAME "mttwt_once"
static unsigned int g_del_used;
#else
#define MT_NAME "mttwt_probe"
#endif

static struct kprobe g_resolver, g_dispatch;
static unsigned long g_entry, g_mapped, g_rejected;
static unsigned int g_used;
static int g_ready, g_registered;
#ifdef MT_TWT_OBSERVE
#ifndef MT_TWT_PROTOCOL
#error Observation requires the fixed protocol build
#endif
#include "observe.inc.h"
#endif

static int same_bytes(const char *left, const char *right, unsigned int count)
{
    unsigned int i;
    for (i = 0; i < count; ++i)
        if (left[i] != right[i]) return 0;
    return 1;
}

static int mttwt_pre(struct kprobe *probe, struct pt_regs *regs)
{
    const char *command, *device_name;
    unsigned int unused = 0;
    int kind = 0;
    if (!__atomic_load_n(&g_ready, __ATOMIC_ACQUIRE)) return 0;
    if (probe != &g_dispatch || regs->pc != (u64)g_dispatch.addr ||
        regs->regs[0] != 0 || !regs->regs[19] || !regs->regs[21] ||
        regs->regs[20] < sizeof("SET_TWT_PARAMS") || regs->regs[20] > 2047)
        return 0;
    command = (const char *)regs->regs[19];
    /* Exact target BTF: net_device.name is at 2240 bits, not at struct start. */
    device_name = (const char *)regs->regs[21] + 0x118;
    if (!same_bytes(device_name, "wlan0", sizeof("wlan0"))) return 0;
#ifdef MT_TWT_PROTOCOL
    if (regs->regs[20] >= sizeof("SET_TWT_PARAMS 4 0 0 0 0 8 0 64 128") &&
        same_bytes(command, "SET_TWT_PARAMS 4 0 0 0 0 8 0 64 128",
                   sizeof("SET_TWT_PARAMS 4 0 0 0 0 8 0 64 128"))) kind = 1;
    else if (regs->regs[20] >= sizeof("SET_TWT_PARAMS 5 0") &&
             same_bytes(command, "SET_TWT_PARAMS 5 0", sizeof("SET_TWT_PARAMS 5 0"))) kind = 2;
#else
    if (same_bytes(command, "SET_TWT_PARAMS", sizeof("SET_TWT_PARAMS"))) kind = 1;
#endif
    if (!kind) {
        __atomic_fetch_add(&g_rejected, 1, __ATOMIC_RELAXED);
        return 0;
    }
#ifdef MT_TWT_PROTOCOL
    if (kind == 2) {
        if (!__atomic_load_n(&g_used, __ATOMIC_ACQUIRE) ||
            !__atomic_compare_exchange_n(&g_del_used, &unused, 1, 0,
                                         __ATOMIC_ACQ_REL, __ATOMIC_RELAXED)) return 0;
    } else
#endif
    if (!__atomic_compare_exchange_n(&g_used, &unused, 1, 0,
                                     __ATOMIC_ACQ_REL, __ATOMIC_RELAXED)) return 0;
    regs->regs[0] = g_entry;
    __atomic_fetch_add(&g_mapped, 1, __ATOMIC_RELAXED);
    return 0;
}

static int mttwt_init(void)
{
    int rc;
    volatile const u32 *entry;
    g_resolver.symbol_name = "wlan_drv_gen4m_eap_6639:priv_driver_set_twtparams";
    g_resolver.offset = 4;
    rc = register_kprobe(&g_resolver);
    if (rc) { _printk("mttwt_probe: handler_resolve_rc=%d\n", rc); return rc; }
    entry = (volatile const u32 *)g_resolver.addr - 1;
    if (g_resolver.opcode != 0xd10683ffU || entry[-1] != 0x3d249d35U ||
        entry[0] != 0xd503233fU) {
        unregister_kprobe(&g_resolver);
        _printk("mttwt_probe: handler_signature_rejected\n");
        return -22;
    }
    g_entry = (unsigned long)entry;
    unregister_kprobe(&g_resolver);

    g_dispatch.symbol_name = "wlan_drv_gen4m_eap_6639:priv_driver_cmds";
    g_dispatch.offset = 0x94;
    g_dispatch.pre_handler = mttwt_pre;
    rc = register_kprobe(&g_dispatch);
    if (rc) { _printk("mttwt_probe: dispatch_register_rc=%d\n", rc); return rc; }
    entry = (volatile const u32 *)g_dispatch.addr;
    if (g_dispatch.opcode != 0xb40004e0U || entry[1] != 0xaa0003e8U ||
        entry[2] != 0xaa1503e0U || entry[3] != 0xaa1303e1U ||
        entry[4] != 0x2a1403e2U || entry[5] != 0xb85fc110U ||
        entry[6] != 0x7293a6b1U) {
        unregister_kprobe(&g_dispatch);
        _printk("mttwt_probe: dispatch_signature_rejected\n");
        return -22;
    }
    g_registered = 1;
#ifdef MT_TWT_OBSERVE
    rc = twt_observe_register();
    if (rc) {
        unregister_kprobe(&g_dispatch);
        g_registered = 0;
        _printk(MT_NAME ": observation_register_rc=%d\n", rc);
        return rc;
    }
#endif
    __atomic_store_n(&g_ready, 1, __ATOMIC_RELEASE);
#ifdef MT_TWT_OBSERVE
    _printk(MT_NAME ": observation=host,submit,return limit=4 readonly=1\n");
#endif
#ifdef MT_TWT_PROTOCOL
    _printk(MT_NAME ": ready fixed-setup=1 one-del=1\n");
#else
    _printk(MT_NAME ": ready bare-only=1 one-shot=1 no_setup=1\n");
#endif
    return 0;
}

static void mttwt_exit(void)
{
    __atomic_store_n(&g_ready, 0, __ATOMIC_RELEASE);
#ifdef MT_TWT_OBSERVE
    twt_observe_unregister();
    _printk(MT_NAME ": observation_removed seen=%u,%u,%u bad=%u\n",
            g_obs_seen[0], g_obs_seen[1], g_obs_seen[2], g_obs_bad);
#endif
    if (g_registered) {
        unregister_kprobe(&g_dispatch);
        g_registered = 0;
    }
    _printk(MT_NAME ": removed mapped=%lu rejected=%lu missed=%lu\n",
            __atomic_load_n(&g_mapped, __ATOMIC_RELAXED),
            __atomic_load_n(&g_rejected, __ATOMIC_RELAXED), g_dispatch.nmissed);
}

#ifndef MT_HOST_TEST
__attribute__((section(".gnu.linkonce.this_module"), used, aligned(64)))
struct module __this_module = {.name = MT_NAME, .init = mttwt_init, .exit = mttwt_exit};
__attribute__((section(".modinfo"), used))
static const char modinfo[] =
    "name=" MT_NAME "\0license=GPL\0"
#ifdef MT_TWT_OBSERVE
    "description=Fixed Request/DEL with bounded read-only host/FW observation\0"
#else
#ifdef MT_TWT_PROTOCOL
    "description=Version-locked fixed Request flow0 and one DEL; no bypass\0"
#else
    "description=Version-locked TWT bridge probe-only; no Setup or DEL\0"
#endif
#endif
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
