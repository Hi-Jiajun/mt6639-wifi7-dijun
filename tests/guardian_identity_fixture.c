/* Only supplies a live executable/argv identity for wrapper tests. No mounts,
 * Binder calls or filesystem writes; parent owns and terminates this child. */
#include "../native/recovery/abi.h"
int main(int argc, char **argv) {
    struct timespec delay = {60, 0};
    if (argc != 3 || strcmp(argv[1], "run-wlan-persistent")) return 64;
    nanosleep(&delay, NULL);
    return 0;
}
