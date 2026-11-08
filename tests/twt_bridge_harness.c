/* Exercise the real bridge callback/lifecycle; only kernel APIs are mocked. */
#include <assert.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#define MT_HOST_TEST 1
#include "../native/twt-bridge/probe.c"

static u32 handler_words[] = {0x3d249d35, 0xd503233f, 0xd10683ff};
static u32 dispatch_words[] = {0xb40004e0, 0xaa0003e8, 0xaa1503e0,
                              0xaa1303e1, 0x2a1403e2, 0xb85fc110, 0x7293a6b1};
static int registrations, removals, fail_call;
int _printk(const char *fmt, ...) { (void)fmt; return 0; }
int register_kprobe(struct kprobe *p)
{
    registrations++;
    if (registrations == fail_call) return -19;
    if (p == &g_resolver) {
        assert(strcmp(p->symbol_name, "wlan_drv_gen4m_eap_6639:priv_driver_set_twtparams") == 0);
        assert(p->offset == 4);
        p->addr = &handler_words[2];
        p->opcode = handler_words[2];
    } else {
        assert(p == &g_dispatch);
        assert(strcmp(p->symbol_name, "wlan_drv_gen4m_eap_6639:priv_driver_cmds") == 0);
        assert(p->offset == 0x94 && removals == 1);
        p->addr = dispatch_words;
        p->opcode = dispatch_words[0];
    }
    return 0;
}
void unregister_kprobe(struct kprobe *p)
{
    assert(p == &g_resolver || p == &g_dispatch);
    if (p == &g_dispatch) assert(g_ready == 0);
    removals++;
}
static void reset(void)
{
    memset(&g_resolver, 0, sizeof(g_resolver));
    memset(&g_dispatch, 0, sizeof(g_dispatch));
    g_ready = g_registered = g_used = 0;
#ifdef MT_TWT_PROTOCOL
    g_del_used = 0;
#endif
    g_entry = g_mapped = g_rejected = 0;
    registrations = removals = fail_call = 0;
    handler_words[0] = 0x3d249d35;
    handler_words[2] = 0xd10683ff;
    dispatch_words[0] = 0xb40004e0;
    dispatch_words[1] = 0xaa0003e8;
}
static struct pt_regs input(char *command, unsigned char *dev)
{
    struct pt_regs r;
    memset(&r, 0xa5, sizeof(r));
    r.regs[0] = 0;
    r.regs[19] = (u64)command;
    r.regs[20] = 2047;
    r.regs[21] = (u64)dev;
    r.pc = (u64)g_dispatch.addr;
    return r;
}
static void unchanged(struct pt_regs r)
{
    struct pt_regs before = r;
    assert(mttwt_pre(&g_dispatch, &r) == 0);
    assert(memcmp(&r, &before, sizeof(r)) == 0);
}
int main(void)
{
    unsigned char dev[2816] = {0};
    char bare[] = "SET_TWT_PARAMS";
    char setup[] = "SET_TWT_PARAMS 4 0 0 0 0 8 0 64 128";
    char bypass[] = "SET_TWT_PARAMS 1 0 0 0 0 8 0 64 128";
    char teardown[] = "SET_TWT_PARAMS 5 0";
    char prefix[] = "SET_TWT_PARAMSX";
    char other[] = "GET_CFG TWTRequester";
    struct pt_regs r, expected;
    reset();
    memcpy(dev + 0x118, "wlan0", 6);
    assert(mttwt_init() == 0 && g_ready && g_registered);
    assert(registrations == 2 && removals == 1);
    unchanged(input(bypass, dev));
    unchanged(input(teardown, dev)); unchanged(input(prefix, dev));
    unchanged(input(other, dev));
    r = input(bare, dev); r.regs[0] = 0x1234; unchanged(r);
    r = input(bare, dev); r.regs[20] = 14; unchanged(r);
    r = input(bare, dev); r.regs[21] = 0; unchanged(r);
    r = input(bare, dev); r.regs[19] = 0; unchanged(r);
    r = input(bare, dev); r.pc += 4; unchanged(r);
    memcpy(dev + 0x118, "p2p0", 5); unchanged(input(bare, dev));
    memcpy(dev + 0x118, "wlan0", 6);
#ifdef MT_TWT_PROTOCOL
    unchanged(input(bare, dev));
    r = input(setup, dev); expected = r; expected.regs[0] = (u64)&handler_words[1];
    assert(mttwt_pre(&g_dispatch, &r) == 0);
    assert(memcmp(&r, &expected, sizeof(r)) == 0 && g_mapped == 1);
    unchanged(input(setup, dev));
    r = input(teardown, dev); expected = r; expected.regs[0] = (u64)&handler_words[1];
    assert(mttwt_pre(&g_dispatch, &r) == 0);
    assert(memcmp(&r, &expected, sizeof(r)) == 0 && g_mapped == 2);
    unchanged(input(teardown, dev)); unchanged(input(bypass, dev));
#else
    unchanged(input(setup, dev));
    r = input(bare, dev); expected = r; expected.regs[0] = (u64)&handler_words[1];
    assert(mttwt_pre(&g_dispatch, &r) == 0);
    assert(memcmp(&r, &expected, sizeof(r)) == 0 && g_mapped == 1);
    unchanged(input(bare, dev)); /* One mapping per load, including concurrent CAS. */
#endif
    mttwt_exit(); assert(removals == 2 && !g_ready && !g_registered);
    unchanged(input(bare, dev));
    reset(); fail_call = 1;
    assert(mttwt_init() == -19 && removals == 0 && !g_ready);
    reset(); handler_words[0] ^= 1;
    assert(mttwt_init() == -22 && removals == 1 && !g_ready);
    reset(); handler_words[2] ^= 1;
    assert(mttwt_init() == -22 && removals == 1 && !g_ready);
    reset(); fail_call = 2;
    assert(mttwt_init() == -19 && removals == 1 && !g_ready);
    reset(); dispatch_words[0] ^= 1;
    assert(mttwt_init() == -22 && removals == 2 && !g_ready);
    reset(); dispatch_words[1] ^= 1;
    assert(mttwt_init() == -22 && removals == 2 && !g_ready);
    puts("PASS TWT bridge: bare-only, one-shot, registers, cleanup, failure paths");
    return 0;
}
