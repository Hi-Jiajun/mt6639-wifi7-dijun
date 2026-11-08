/* Included only in the explicit protocol+observe build. No asynchronous
 * pointers, post-handlers, driver calls, or writes to observed state. */
extern long copy_from_kernel_nofault(void *, const void *, unsigned long);
static struct kprobe g_obs[3];
static unsigned int g_obs_registered, g_obs_seen[3], g_obs_bad;

static void twt_hex(char *out, const u8 *in, unsigned int n)
{
    static const char digits[] = "0123456789abcdef";
    unsigned int i;
    for (i = 0; i < n; ++i) {
        out[2*i] = digits[in[i] >> 4];
        out[2*i+1] = digits[in[i] & 15];
    }
    out[2*n] = 0;
}

static int twt_observe_pre(struct kprobe *p, struct pt_regs *r)
{
    unsigned int which, i, slot, equal = 1;
    u8 params[24], host[24], cmd[21];
    u64 buffer = 0;
    u32 length = 0;
    char param10[21], tsf8[17], cmd21[43];
    if (!__atomic_load_n(&g_ready, __ATOMIC_ACQUIRE) ||
        !__atomic_load_n(&g_used, __ATOMIC_ACQUIRE)) return 0;
    for (which = 0; which < 3; ++which) if (p == &g_obs[which]) break;
    if (which == 3 || r->pc != (u64)p->addr) return 0;
    /* Four records per site, even on erroneous contexts. Never log pointers. */
    if (__atomic_fetch_add(&g_obs_seen[which], 1, __ATOMIC_RELAXED) >= 4) return 0;
    if (which == 0) {
        slot = (u32)r->regs[25];
        if (slot >= 16 || (u32)r->regs[24] != 0 || !r->regs[26] || !r->regs[27]) goto bad;
        if (copy_from_kernel_nofault(params, (const void *)r->regs[27], 24) ||
            copy_from_kernel_nofault(host,
                (const void *)(r->regs[26] + 0x339590UL + slot*40UL), 24)) goto bad;
        for (i = 0; i < 24; ++i) if (params[i] != host[i]) equal = 0;
        /* Offsets 10..15 may be C padding; never disclose them. */
        twt_hex(param10, params, 10);
        twt_hex(tsf8, params+16, 8);
        _printk("mttwt_obs: host idx=%u flow=0 equal=%u alloc=%u param10=%s tsf8=%s\n",
                slot, equal, r->regs[0] != 0, param10, tsf8);
    } else {
        if (copy_from_kernel_nofault(&length, (const void *)r->sp, 4) ||
            copy_from_kernel_nofault(&buffer, (const void *)(r->sp+8), 8) ||
            length != 28 || !buffer) goto bad;
        if (which == 1 && (r->regs[1] != 0xed || r->regs[2] != 0x94 ||
                          r->regs[3] != 1 || r->regs[4] != 0)) goto bad;
        /* Only bytes 0..20 are known initialized on every non-logo path. */
        if (copy_from_kernel_nofault(cmd, (const void *)buffer, sizeof(cmd)) ||
            cmd[0] >= 16 || cmd[1] != 0 || cmd[3] != 0) goto bad;
        twt_hex(cmd21, cmd, sizeof(cmd));
        if (which == 1)
            _printk("mttwt_obs: submit len=28 need_resp=0 cmd21=%s\n", cmd21);
        else
            _printk("mttwt_obs: return rc=%u cmd21=%s\n", (u32)r->regs[0], cmd21);
    }
    return 0;
bad:
    __atomic_fetch_add(&g_obs_bad, 1, __ATOMIC_RELAXED);
    return 0;
}

static void twt_observe_unregister(void)
{
    while (g_obs_registered) unregister_kprobe(&g_obs[--g_obs_registered]);
}

static int twt_observe_register(void)
{
    static const u32 offsets[] = {0x3a8, 0x494, 0x498};
    static const u32 opcodes[] = {0xb4000980U, 0x94000000U, 0x2a0003f3U};
    unsigned int i;
    int rc;
    for (i = 0; i < 3; ++i) {
        g_obs[i].symbol_name = "wlan_drv_gen4m_eap_6639:twtPlannerAddAgrtTbl";
        g_obs[i].offset = offsets[i];
        g_obs[i].pre_handler = twt_observe_pre;
        rc = register_kprobe(&g_obs[i]);
        if (rc) { twt_observe_unregister(); return rc; }
        ++g_obs_registered;
        /* CALL26 is relocated on load; its opcode class remains BL. */
        if ((i == 1 && (g_obs[i].opcode & 0xfc000000U) != opcodes[i]) ||
            (i != 1 && g_obs[i].opcode != opcodes[i])) {
            twt_observe_unregister(); return -22;
        }
    }
    return 0;
}
