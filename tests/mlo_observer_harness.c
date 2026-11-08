/* Run real module lifecycle logic; kernel registration is the only fake boundary. */
#include <assert.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#define MT_HOST_TEST 1
#include "../native/mlo-gate/observer.c"

static int register_rc, registrations, unregistrations;
static unsigned int returned_opcode;
static unsigned int fake_text[5] = {0xd503245f, 0x52800888, 0x9ac22508, 0x12000100, 0xd65f03c0};

int _printk(const char *fmt, ...) { (void)fmt; return 0; }
int register_kprobe(struct kprobe *probe) {
    ++registrations;
    assert(!strcmp(probe->symbol_name, "wlan_drv_gen4m_eap_6639:mt6639_apsLinkPlanDecision"));
    assert(probe->offset == 8 && probe->pre_handler == mtmlo_pre);
    assert(!g_ready);
    if (register_rc) return register_rc;
    probe->addr = &fake_text[2];
    probe->opcode = returned_opcode;
    return 0;
}
void unregister_kprobe(struct kprobe *probe) {
    assert(probe == &g_probe && !g_ready);
    ++unregistrations;
}
static void reset_case(void) {
    memset(&g_probe, 0, sizeof(g_probe));
    memset(g_plan_hits, 0, sizeof(g_plan_hits));
    g_bad_hits = 0; g_ready = 0; g_registered = 0;
    register_rc = registrations = unregistrations = 0;
    returned_opcode = 0x9ac22508;
}
static void hit(unsigned long plan, unsigned long mask, unsigned long pc) {
    struct pt_regs regs, before;
    memset(&regs, 0xa5, sizeof(regs));
    regs.regs[2] = plan; regs.regs[8] = mask; regs.pc = pc;
    memcpy(&before, &regs, sizeof(regs));
    assert(mtmlo_pre(&g_probe, &regs) == 0);
    assert(!memcmp(&regs, &before, sizeof(regs)));
}
int main(void) {
    reset_case();
    hit(7, 0x44, 0);
    assert(!g_plan_hits[7] && !g_bad_hits);
    register_rc = -2;
    assert(mtmlo_init() == -2 && registrations == 1 && !unregistrations && !g_registered && !g_ready);
    reset_case(); returned_opcode = 0xd503201f;
    assert(mtmlo_init() == -22 && registrations == 1 && unregistrations == 1 && !g_registered && !g_ready);
    reset_case();
    assert(mtmlo_init() == 0 && g_registered && g_ready && registrations == 1);
    for (unsigned long i=0; i<11; ++i) { hit(i, 0x44, (unsigned long)g_probe.addr); assert(g_plan_hits[i] == 1); }
    hit(7, 0xc4, (unsigned long)g_probe.addr);
    hit(11, 0x44, (unsigned long)g_probe.addr);
    hit(~0UL, 0x44, (unsigned long)g_probe.addr);
    hit(7, 0x44, (unsigned long)g_probe.addr + 4);
    assert(g_bad_hits == 4 && g_plan_hits[7] == 1);
    mtmlo_exit();
    assert(unregistrations == 1 && !g_registered && !g_ready);
    hit(7, 0x44, (unsigned long)g_probe.addr);
    assert(g_plan_hits[7] == 1 && g_bad_hits == 4);
    mtmlo_exit(); /* Idempotent helper cleanup: no second unregister. */
    assert(unregistrations == 1);
    puts("PASS lifecycle: registration failure, opcode rejection, all plans, anomaly guards, unchanged regs, cleanup");
    return 0;
}
