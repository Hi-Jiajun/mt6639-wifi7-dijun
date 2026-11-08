/* A file-only parser entry. The stock main remains the default entry. */
#include <stdio.h>
#include <string.h>

struct hostapd_config;
extern struct hostapd_config *hostapd_config_read(const char *filename);
extern void hostapd_config_free(struct hostapd_config *config);
extern int __real_main(int argc, char **argv);

int __wrap_main(int argc, char **argv)
{
    if (argc > 1 && strcmp(argv[1], "--mt6639-check-config") == 0) {
        struct hostapd_config *config;
        if (argc != 3) {
            fputs("usage: --mt6639-check-config FILE\n", stderr);
            return 64;
        }
        /* No hostapd interface initialization, eloop, driver init or AP start. */
        config = hostapd_config_read(argv[2]);
        if (config == NULL) {
            fputs("config_parse=failed radio_started=0\n", stderr);
            return 1;
        }
        hostapd_config_free(config);
        puts("config_parse=ok radio_started=0");
        return 0;
    }
    return __real_main(argc, argv);
}
