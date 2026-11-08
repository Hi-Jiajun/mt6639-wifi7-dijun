/* TEST ONLY: models the external Binder/settings boundary, not the transaction
 * implementation. This executable is never used by a production profile. */
#include "../native/recovery/abi.h"
extern int sscanf(const char *, const char *, ...);
static int state(const char *dir, const char *name, int value) {
    char path[400], b[4]; int fd; ssize_t n;
    snprintf(path, sizeof path, "%s/%s", dir, name);
    fd = open(path, (value < 0 ? O_RDONLY : O_WRONLY) | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0) return -1;
    if (value < 0) {
        n = read(fd, b, sizeof b); close(fd);
        return n == 2 && (b[0] == '0' || b[0] == '1') && b[1] == '\n' ? b[0] - '0' : -1;
    }
    b[0] = (char)('0' + value); b[1] = '\n';
    n = write(fd, b, 2); value = n == 2 && fsync(fd) == 0 ? value : -1;
    close(fd); return value;
}
static int string_state(const char *dir, const char *name, const char *value) {
    char path[400], b[8]; int fd; ssize_t n;
    snprintf(path, sizeof path, "%s/%s", dir, name);
    fd = open(path, (value ? O_WRONLY | O_TRUNC : O_RDONLY) | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0) return 0;
    if (value) {
        n = write(fd, value, strlen(value)); fd = n == (ssize_t)strlen(value) && !fsync(fd) ? 0 : -1;
        close(fd); return fd == 0;
    }
    n = read(fd, b, sizeof b - 1); close(fd);
    if (n <= 0) return 0;
    b[n] = 0;
    while (n && (b[n - 1] == '\n' || b[n - 1] == '\r')) b[--n] = 0;
    printf("%s", b);
    return 1;
}
int main(int argc, char **argv) {
    char path[400], fail[80] = {0}; int fd, value = -1;
    if (argc != 3) return 64;
    snprintf(path, sizeof path, "%s/wlan-actions", argv[2]);
    fd = open(path, O_WRONLY | O_CREAT | O_APPEND | O_NOFOLLOW | O_CLOEXEC, 0600);
    if (fd < 0) return 2;
    if (write(fd, argv[1], strlen(argv[1])) != (ssize_t)strlen(argv[1]) || write(fd, "\n", 1) != 1) {
        close(fd); return 2;
    }
    fsync(fd); close(fd);
    snprintf(path, sizeof path, "%s/wlan-fail", argv[2]);
    fd = open(path, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
    if (fd >= 0) { read(fd, fail, sizeof fail - 1); close(fd); }
    if (!strcmp(fail, "nul") && !strcmp(argv[1], "get-scan")) {
        write(1, "1\0junk\n", 7); return 0;
    }
    if (!strcmp(fail, argv[1])) { printf("1\n"); return 7; }
    if (!strcmp(argv[1], "get-override")) {
        printf("mOverrideCountryCode: ");
        if (!string_state(argv[2], "wlan-override", NULL)) return 3;
        printf("\n"); return 0;
    }
    if (!strcmp(argv[1], "get-country")) {
        printf("Wifi Country Code = "); string_state(argv[2], "wlan-country", NULL);
        printf("\n"); return 0;
    }
    if (!strcmp(argv[1], "country-us")) return string_state(argv[2], "wlan-override", "US\n") ? 0 : 3;
    if (!strcmp(argv[1], "country-cn")) return string_state(argv[2], "wlan-override", "CN\n") ? 0 : 3;
    if (!strcmp(argv[1], "country-auto")) return string_state(argv[2], "wlan-override", "null\n") ? 0 : 3;
    if (!strcmp(argv[1], "get-scan")) value = state(argv[2], "wlan-scan", -1);
    else if (!strcmp(argv[1], "get-wifi")) value = state(argv[2], "wlan-wifi", -1);
    else if (!strcmp(argv[1], "scan-off")) value = state(argv[2], "wlan-scan", 0);
    else if (!strcmp(argv[1], "scan-on")) value = state(argv[2], "wlan-scan", 1);
    else if (!strcmp(argv[1], "wifi-off")) value = state(argv[2], "wlan-wifi", 0);
    else if (!strcmp(argv[1], "wifi-on")) value = state(argv[2], "wlan-wifi", 1);
    else if (!strcmp(argv[1], "scan-now")) value = 0;
    else if (!strcmp(argv[1], "get-link") || !strcmp(argv[1], "get-ipv4")) {
        char out[8] = {0}; char name[24];
        snprintf(name, sizeof name, "%s", !strcmp(argv[1], "get-link") ? "wlan-link" : "wlan-ipv4");
        snprintf(path, sizeof path, "%s/%s", argv[2], name);
        fd = open(path, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
        if (fd < 0) return 3;
        value = read(fd, out, sizeof out) == 2 && out[0] == '1' && out[1] == '\n'; close(fd);
        printf("%s\n", value ? "Wifi is connected to \"x\"" : "Wifi is not connected");
        if (!strcmp(argv[1], "get-ipv4") && value) printf("2: wlan0    inet 192.0.2.1/24 scope global wlan0\n");
        return 0;
    }
    if (value < 0) return 3;
    if (!strncmp(argv[1], "get-", 4)) printf("%d\n", value);
    return 0;
}
