/* Run the real plan7 adapter logic; only kernel registration is faked. */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#define MT_HOST_TEST 1
#include "../native/mlo-gate/plan7.c"

static int register_rc, registrations, unregistrations;
static unsigned int returned_opcode;
static unsigned int fake_text[5] = {0xd503245f, 0x52800888, 0x9ac22508, 0x12000100, 0xd65f03c0};
int _printk(const char *fmt, ...) { (void)fmt; return 0; }
int register_kprobe(struct kprobe *probe) {
    ++registrations;
    assert(!strcmp(probe->symbol_name, "wlan_drv_gen4m_eap_6639:mt6639_apsLinkPlanDecision"));
    assert(probe->offset == 8 && probe->pre_handler == mtmlo_plan7_pre);
    if (register_rc) return register_rc;
    probe->addr = &fake_text[2];
    probe->opcode = returned_opcode;
    return 0;
}
void unregister_kprobe(struct kprobe *probe) {
    assert(probe == &g_plan7_probe && !g_plan7_ready);
    ++unregistrations;
}
static void reset_case(void) {
    memset(&g_plan7_probe, 0, sizeof(g_plan7_probe));
    g_plan7_hits = 0; g_plan7_changed = 0; g_plan7_anomalies = 0;
    g_plan7_ready = 0; g_plan7_registered = 0;
    register_rc = registrations = unregistrations = 0;
    returned_opcode = 0x9ac22508;
}
static void hit(unsigned long plan, unsigned long mask, unsigned long pc,
                unsigned long *word_changed, unsigned long *value_changed) {
    struct pt_regs regs, before;
    memset(&regs, 0xa5, sizeof(regs));
    regs.regs[2] = plan; regs.regs[8] = mask; regs.pc = pc;
    memcpy(&before, &regs, sizeof(regs));
    assert(mtmlo_plan7_pre(&g_plan7_probe, &regs) == 0);
    *word_changed = 0; *value_changed = 0;
    for (unsigned int i = 0; i < sizeof(regs) / sizeof(unsigned long); ++i) {
        unsigned long *a = ((unsigned long *)&regs) + i;
        unsigned long *b = ((unsigned long *)&before) + i;
        if (*a != *b) { *word_changed = i; *value_changed = *a; }
    }
}
int main(void) {
    unsigned long word = 0, value = 0;
    reset_case();
    hit(7, 0x44, 0, &word, &value);
    assert(!g_plan7_hits && !g_plan7_changed && !g_plan7_anomalies);
    register_rc = -2;
    assert(mtmlo_plan7_init() == -2 && registrations == 1 && !unregistrations && !g_plan7_ready);
    reset_case(); returned_opcode = 0xd503201f;
    assert(mtmlo_plan7_init() == -22 && registrations == 1 && unregistrations == 1 && !g_plan7_ready);
    reset_case();
    assert(mtmlo_plan7_init() == 0 && g_plan7_registered && g_plan7_ready);

    hit(6, 0x44, (unsigned long)g_plan7_probe.addr, &word, &value);
    assert(g_plan7_hits == 1 && g_plan7_changed == 0 && word == 0);
    hit(7, 0x44, (unsigned long)g_plan7_probe.addr, &word, &value);
    assert(g_plan7_hits == 2 && g_plan7_changed == 1);
    assert(word == 8 && value == 0x80);
    hit(7, 0xc4, (unsigned long)g_plan7_probe.addr, &word, &value);
    hit(11, 0x44, (unsigned long)g_plan7_probe.addr, &word, &value);
    hit(~0UL, 0x44, (unsigned long)g_plan7_probe.addr, &word, &value);
    hit(7, 0x44, (unsigned long)g_plan7_probe.addr + 4, &word, &value);
    assert(g_plan7_anomalies == 4 && g_plan7_changed == 1 && word == 0);

    mtmlo_plan7_exit();
    assert(unregistrations == 1 && !g_plan7_registered && !g_plan7_ready);
    hit(7, 0x44, (unsigned long)g_plan7_probe.addr, &word, &value);
    assert(g_plan7_changed == 1 && word == 0);
    mtmlo_plan7_exit();
    assert(unregistrations == 1);
    puts("PASS plan7: only plan7/x8 changes; guard cases preserve full pt_regs; cleanup idempotent");
    return 0;
}
