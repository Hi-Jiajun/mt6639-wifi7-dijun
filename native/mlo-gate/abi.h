#ifndef MTMLO_ABI_H
#define MTMLO_ABI_H
/* Version-specific, independently checked against target BTF cff622b3... */
typedef unsigned char u8;
typedef unsigned int u32;
typedef unsigned long u64;
struct pt_regs {
    u64 regs[31], sp, pc, pstate, orig_x0;
    int syscallno;
    u8 reserved[320 - 0x11c];
};
struct kprobe {
    u8 links[0x20];
    u64 nmissed;
    u32 *addr;
    const char *symbol_name;
    u32 offset, alignment0;
    int (*pre_handler)(struct kprobe *, struct pt_regs *);
    void *post_handler;
    u32 opcode, alignment1;
    u8 ainsn[0x18];
    u32 flags, alignment2;
};
struct module {
    u8 pad0[0x18];
    char name[56];
    u8 pad1[0x178 - 0x50];
    int (*init)(void);
    u8 pad2[0x600 - 0x180];
    void (*exit)(void);
    u8 pad3[0x680 - 0x608];
};
#define MT_OFF(type, field, offset) _Static_assert(__builtin_offsetof(struct type, field) == offset, #type "." #field)
_Static_assert(sizeof(u64) == 8, "64-bit ABI required");
_Static_assert(sizeof(struct pt_regs) == 320, "pt_regs size");
MT_OFF(pt_regs, regs, 0); MT_OFF(pt_regs, sp, 0xf8);
MT_OFF(pt_regs, pc, 0x100); MT_OFF(pt_regs, pstate, 0x108);
MT_OFF(pt_regs, orig_x0, 0x110); MT_OFF(pt_regs, syscallno, 0x118);
_Static_assert(sizeof(struct kprobe) == 0x78, "kprobe size");
MT_OFF(kprobe, nmissed, 0x20); MT_OFF(kprobe, addr, 0x28);
MT_OFF(kprobe, symbol_name, 0x30); MT_OFF(kprobe, offset, 0x38);
MT_OFF(kprobe, pre_handler, 0x40); MT_OFF(kprobe, post_handler, 0x48);
MT_OFF(kprobe, opcode, 0x50); MT_OFF(kprobe, ainsn, 0x58); MT_OFF(kprobe, flags, 0x70);
_Static_assert(sizeof(struct module) == 0x680, "module size");
MT_OFF(module, name, 0x18); MT_OFF(module, init, 0x178); MT_OFF(module, exit, 0x600);
extern int register_kprobe(struct kprobe *);
extern void unregister_kprobe(struct kprobe *);
extern int _printk(const char *, ...);
#endif
