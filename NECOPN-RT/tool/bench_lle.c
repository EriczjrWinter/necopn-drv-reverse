/* Nuked YM2608 LLE 独立吞吐基准（-O2）。
 * 编译：
 *   gcc -O2 -o tool\bench_lle.exe tool\bench_lle.c nuked_lle\nopna.c nuked_lle\fmopna_2608.c -I nuked_lle
 * （源码在 D:\gf\OPN_Chipset-main\nuked_lle，这里只做外部基准，不改它）
 */
#include <stdio.h>
#include <stdint.h>
#include <time.h>
#include "nopna.h"

static void wr(void *h, int port, int val) { nopna_write(h, port, val); }
static void wrreg(void *h, int port, int reg, int val) {
    wr(h, port * 2 + 0, reg);
    wr(h, port * 2 + 1, val);
}

static void voice(void *h, int ch) {
    wrreg(h, 0, 0x29, 0x80);                 /* OPNA 模式 */
    wrreg(h, 0, 0xB0 + ch, 0x00);
    int offs[4] = {0x00, 0x08, 0x04, 0x0C};
    for (int i = 0; i < 4; i++) {
        int s = offs[i] + ch;
        wrreg(h, 0, 0x30 + s, 0x01);
        wrreg(h, 0, 0x40 + s, i < 3 ? 0x20 : 0x00);
        wrreg(h, 0, 0x50 + s, 0x1F);
        wrreg(h, 0, 0x60 + s, 0x05);
        wrreg(h, 0, 0x70 + s, 0x02);
        wrreg(h, 0, 0x80 + s, 0x0A);
    }
    wrreg(h, 0, 0xA4 + ch, 0x2A);
    wrreg(h, 0, 0xA0 + ch, 0x80);
    wrreg(h, 0, 0x28, 0xF0 | ch);
}

int main(int argc, char **argv) {
    double seconds = argc > 1 ? atof(argv[1]) : 3.0;
    int chips = argc > 2 ? atoi(argv[2]) : 1;
    if (chips < 1) chips = 1;
    if (chips > 2) chips = 2;

    void *h[2] = {0, 0};
    for (int c = 0; c < chips; c++) {
        h[c] = nopna_init(7987200u, 55466u);
        if (!h[c]) { printf("init failed\n"); return 1; }
        for (int ch = 0; ch < 3; ch++) voice(h[c], ch);
    }

    long n = (long)(55466 * seconds);
    short buf[2];
    clock_t t0 = clock();
    for (long i = 0; i < n; i++)
        for (int c = 0; c < chips; c++)
            nopna_getsample_one_native(h[c], buf);
    double el = (double)(clock() - t0) / CLOCKS_PER_SEC;

    for (int c = 0; c < chips; c++) nopna_shutdown(h[c]);
    printf("Nuked YM2608 LLE %s-O2 x%d: %.1f ms/audio-sec, %.2fx realtime\n",
           "-", chips, el / seconds * 1000.0, seconds / el);
    return 0;
}
