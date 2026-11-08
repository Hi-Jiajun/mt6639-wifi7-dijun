/* Smoke test: actual observation callbacks, only kernel APIs mocked. */
#include <assert.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define MT_HOST_TEST 1
#define MT_TWT_PROTOCOL 1
#define MT_TWT_OBSERVE 1
#include "../native/twt-bridge/probe.c"

static u32 handler[] = {0x3d249d35, 0xd503233f, 0xd10683ff};
static u32 dispatch[] = {0xb40004e0, 0xaa0003e8, 0xaa1503e0,
                        0xaa1303e1, 0x2a1403e2, 0xb85fc110, 0x7293a6b1};
static u32 observed[] = {0xb4000980, 0x94000123, 0x2a0003f3};
static unsigned int removed;
int _printk(const char *format, ...)
{
    va_list ap; va_start(ap, format); vprintf(format, ap); va_end(ap); return 0;
}
long copy_from_kernel_nofault(void *to, const void *from, unsigned long n)
{
    if (!from) return -14;
    memcpy(to, from, n); return 0;
}
int register_kprobe(struct kprobe *p)
{
    unsigned int i;
    if (p == &g_resolver) { p->addr = handler+2; p->opcode = handler[2]; }
    else if (p == &g_dispatch) { p->addr = dispatch; p->opcode = dispatch[0]; }
    else {
        for (i = 0; i < 3; ++i) if (p == &g_obs[i]) break;
        assert(i < 3); p->addr = observed+i; p->opcode = observed[i];
    }
    return 0;
}
void unregister_kprobe(struct kprobe *p) { (void)p; ++removed; }
static void call_readonly(unsigned int i, struct pt_regs *r)
{
    struct pt_regs before;
    r->pc = (u64)g_obs[i].addr; before = *r;
    assert(twt_observe_pre(&g_obs[i], r) == 0);
    assert(memcmp(&before, r, sizeof(before)) == 0);
}
int main(void)
{
    u8 *adapter = calloc(1, 0x33a000);
    u8 params[24] = {0}, command[28] = {0}, saved_command[28];
    u64 stack[3] = {28, (u64)command, 0};
    struct pt_regs r = {0};
    assert(adapter && mttwt_init() == 0 && g_obs_registered == 3);
    g_used = 1; /* The mapping itself is already covered by the bridge smoke. */
    params[5] = 32; params[6] = 9; params[8] = 128;
    params[16] = 1; params[20] = 1;
    memset(params+10, 0xaa, 6); /* Padding must never be logged. */
    memcpy(adapter+0x339590+2*40, params, sizeof(params));
    r.regs[0] = (u64)command; r.regs[24] = 0; r.regs[25] = 2;
    r.regs[26] = (u64)adapter; r.regs[27] = (u64)params;
    call_readonly(0, &r);
    assert(memcmp(params, adapter+0x339590+2*40, 24) == 0);
    command[0] = 2; command[6] = 32; command[7] = 1;
    command[8] = 1; command[12] = 1; command[16] = 128; command[18] = 9;
    memcpy(saved_command, command, sizeof(command));
    r.sp = (u64)stack; r.regs[1] = 0xed; r.regs[2] = 0x94;
    r.regs[3] = 1; r.regs[4] = 0;
    call_readonly(1, &r);
    r.regs[0] = 0x103; /* Record raw pending status; do not relabel it FW commit. */
    call_readonly(2, &r);
    assert(memcmp(saved_command, command, sizeof(command)) == 0);
    assert(g_obs_bad == 0);
    mttwt_exit(); assert(g_obs_registered == 0 && removed == 5);
    free(adapter);
    puts("PASS observation: host parameters, submit, return, readonly, cleanup");
    return 0;
}
