/* Read-only generic-netlink GET_WIPHY dump; no scan/set/vendor commands.
 * No headers needed: built with the device's already installed Termux tcc.
 * Constants checked against Linux include/uapi/linux/nl80211.h.
 */
extern int printf(const char *, ...);
extern void perror(const char *);
extern int socket(int, int, int);
extern int bind(int, const void *, unsigned int);
extern int setsockopt(int, int, int, const void *, unsigned int);
extern long sendto(int, const void *, unsigned long, int, const void *, unsigned int);
extern long recvfrom(int, void *, unsigned long, int, void *, unsigned int *);
extern int close(int);
extern void *memset(void *, int, unsigned long);
extern void *memcpy(void *, const void *, unsigned long);
extern unsigned long strlen(const char *);
extern int strcmp(const char *, const char *);
typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
struct nh { u32 len; u16 type, flags; u32 seq, pid; };
struct gh { u8 cmd, version; u16 reserved; };
struct sa { u16 family, pad; u32 pid, groups; };
struct attr { u16 len, type; };
struct tv { long sec, usec; };
#define A4(x) (((x) + 3u) & ~3u)
static union { unsigned long align; u8 b[262144]; } rx;
static u32 freq_count[8], disabled_count[8], no_ir_count[8];
static u32 messages;

static u16 u16_at(const void *p) { u16 v; memcpy(&v, p, 2); return v; }
static u32 u32_at(const void *p) { u32 v; memcpy(&v, p, 4); return v; }
/* Returns 1 for one complete attribute, 0 at exact end, -1 on malformed. */
static int next_attr(const u8 **p, unsigned long *left, const struct attr **a) {
    u32 len, aligned;
    if (!*left) return 0;
    if (*left < 4) return -1;
    *a = (const struct attr *)*p;
    len = u16_at(*p); aligned = A4(len);
    if (len < 4 || len > *left || aligned > *left) return -1;
    *p += aligned; *left -= aligned;
    return 1;
}
static u16 atype(const struct attr *a) { return u16_at((const u8 *)a + 2) & 0x3fff; }
static const u8 *payload(const struct attr *a) { return (const u8 *)a + 4; }
static unsigned long plen(const struct attr *a) { return u16_at(a) - 4; }
static int walk_freqs(const struct attr *outer, u32 band) {
    const u8 *p = payload(outer); unsigned long left = plen(outer);
    const struct attr *entry, *a; int r, s;
    while ((r = next_attr(&p, &left, &entry)) > 0) {
        const u8 *q = payload(entry); unsigned long n = plen(entry);
        u32 freq=0, disabled=0, no_ir=0, power=0; int have_power=0;
        while ((s = next_attr(&q, &n, &a)) > 0) {
            u16 t = atype(a);
            if ((t == 1 || t == 6) && plen(a) < 4) return -1;
            if (t == 1) freq=u32_at(payload(a));
            if (t == 2) disabled=1;
            if (t == 3) no_ir=1;
            if (t == 6) { power=u32_at(payload(a)); have_power=1; }
        }
        if (s < 0 || !freq) return -1;
        if (band < 8) {
            freq_count[band]++; disabled_count[band]+=disabled; no_ir_count[band]+=no_ir;
        }
        printf("frequency band_enum=%u mhz=%u disabled=%u no_ir=%u max_tx_mbm=%u power_present=%d\n",
               band, freq, disabled, no_ir, power, have_power);
    }
    return r;
}
static int walk_iftypes(const struct attr *outer, u32 band) {
    const u8 *p=payload(outer); unsigned long left=plen(outer);
    const struct attr *entry, *a; int r, s;
    while ((r=next_attr(&p,&left,&entry)) > 0) {
        const u8 *q=payload(entry); unsigned long n=plen(entry);
        u32 he_len=0, eht_len=0, types=0;
        while ((s=next_attr(&q,&n,&a)) > 0) {
            if (atype(a)==3) he_len=(u32)plen(a);
            if (atype(a)==9) eht_len=(u32)plen(a);
            if (atype(a)==1) {
                const u8 *z=payload(a); unsigned long m=plen(a); const struct attr *t; int v;
                while ((v=next_attr(&z,&m,&t)) > 0) {
                    if (atype(t)<32) types |= 1u << atype(t);
                }
                if (v<0) return -1;
            }
        }
        if (s<0) return -1;
        printf("iftype band_enum=%u types_mask=0x%x he_phy_bytes=%u eht_phy_bytes=%u\n",
               band,types,he_len,eht_len);
    }
    return r;
}
static int walk_bands(const struct attr *outer) {
    const u8 *p=payload(outer); unsigned long left=plen(outer);
    const struct attr *band, *a; int r, s;
    while ((r=next_attr(&p,&left,&band)) > 0) {
        const u8 *q=payload(band); unsigned long n=plen(band); u32 id=atype(band);
        printf("band_chunk enum=%u\n",id);
        while ((s=next_attr(&q,&n,&a)) > 0) {
            if (atype(a)==1 && walk_freqs(a,id)<0) return -1;
            if (atype(a)==9 && walk_iftypes(a,id)<0) return -1;
        }
        if (s<0) return -1;
    }
    return r;
}
static int parse_phy(const u8 *p, unsigned long left) {
    const struct attr *a; int r;
    while ((r=next_attr(&p,&left,&a)) > 0) {
        if (atype(a)==1) {
            if (plen(a)<4) return -1;
            printf("wiphy index=%u\n",u32_at(payload(a)));
        }
        if (atype(a)==22 && walk_bands(a)<0) return -1;
    }
    return r;
}
static int request(int fd, u16 family, u8 cmd, u16 flags, u32 seq, int resolve) {
    union { unsigned long align; u8 b[256]; } tx;
    struct nh *n=(struct nh *)tx.b; struct gh *g=(struct gh *)(tx.b+16);
    struct attr *a=(struct attr *)(tx.b+20); struct sa dest={16,0,0,0};
    memset(&tx,0,sizeof(tx));
    n->type=family; n->flags=flags; n->seq=seq; g->cmd=cmd; g->version=1;
    if (resolve) {
        a->type=2; a->len=12; memcpy(tx.b+24,"nl80211",8); n->len=32;
    } else { a->type=174; a->len=4; n->len=24; }
    if (sendto(fd,tx.b,n->len,0,&dest,sizeof(dest)) != n->len) {
        perror("sendto"); return -1;
    }
    return 0;
}
static int receive(int fd, u32 seq, int resolve) {
    unsigned int rounds=0;
    while (++rounds<=512) {
        struct sa src; unsigned int srclen=sizeof(src);
        long got=recvfrom(fd,rx.b,sizeof(rx.b),0x20,&src,&srclen);
        unsigned long off=0;
        if (got<0) { perror("recvfrom"); return -1; }
        if (!got || got>(long)sizeof(rx.b) || srclen!=sizeof(src) || src.pid) return -1;
        while (off<(unsigned long)got) {
            const struct nh *n=(const struct nh *)(rx.b+off);
            u32 len;
            if ((unsigned long)got-off<16) return -1;
            len=n->len;
            if (len<16 || A4(len)>(unsigned long)got-off || n->seq!=seq) return -1;
            if (n->flags & 0x10) { printf("ERROR dump interrupted\n"); return -1; }
            if (n->type==2) {
                int err;
                if (len<20) return -1;
                memcpy(&err,rx.b+off+16,4);
                if (err) { printf("netlink_error=%d\n",err); return -1; }
            } else if (n->type==3) {
                if (len>=20 && u32_at(rx.b+off+16)) return -1;
                return 0;
            } else {
                const u8 *p=rx.b+off+20; unsigned long left;
                if (len<20) return -1;
                left=len-20;
                if (resolve) {
                    const struct attr *a; int r;
                    while ((r=next_attr(&p,&left,&a))>0) {
                        if (atype(a)==1 && plen(a)>=2) return u16_at(payload(a));
                    }
                    return -1;
                }
                if (parse_phy(p,left)<0) return -1;
                messages++;
            }
            off+=A4(len);
        }
    }
    printf("ERROR message budget exhausted\n"); return -1;
}
static int selftest(void) {
    const u8 empty[8]={0}; const u8 badlen[8]={3,0,1,0};
    const u8 overrun[8]={9,0,1,0}; const u8 valid[8]={8,0,1,0,7,0,0,0};
    const u8 *p; unsigned long n; const struct attr *a; int fail=0;
    p=empty;n=0;fail+=next_attr(&p,&n,&a)!=0;
    p=empty;n=3;fail+=next_attr(&p,&n,&a)!=-1;
    p=empty;n=8;fail+=next_attr(&p,&n,&a)!=-1;
    p=badlen;n=8;fail+=next_attr(&p,&n,&a)!=-1;
    p=overrun;n=8;fail+=next_attr(&p,&n,&a)!=-1;
    p=valid;n=8;fail+=next_attr(&p,&n,&a)!=1 || n!=0 || plen(a)!=4 || atype(a)!=1;
    fail+=parse_phy(badlen,8)!=-1;
    fail+=parse_phy(valid,8)!=0;
    printf("parser_selftest cases=8 failures=%d\n",fail); return !!fail;
}
int main(int argc, char **argv) {
    int fd,family,result; u32 b; struct sa addr={16,0,0,0}; struct tv limit={4,0};
    if (argc==2 && !strcmp(argv[1],"--self-test")) return selftest();
    if (argc!=1) return 2;
    fd=socket(16,3,16);
    if (fd<0) { perror("socket"); return 1; }
    if (bind(fd,&addr,sizeof(addr))<0 || setsockopt(fd,1,20,&limit,sizeof(limit))<0) {
        perror("socket setup"); close(fd); return 1;
    }
    if (request(fd,16,3,1,1,1)<0) { close(fd); return 1; }
    family=receive(fd,1,1);
    if (family<16) { close(fd); return 1; }
    printf("nl80211 family=%d; GET_WIPHY only\n",family);
    if (request(fd,(u16)family,1,0x301,2,0)<0) { close(fd); return 1; }
    result=receive(fd,2,0); close(fd);
    if (result<0 || !messages) return 1;
    for (b=0;b<8;b++) if (freq_count[b])
        printf("summary band_enum=%u frequencies=%u disabled=%u no_ir=%u\n",
               b,freq_count[b],disabled_count[b],no_ir_count[b]);
    printf("dump_complete messages=%u\n",messages);
    return 0;
}
