/*
 * opn2_wrapper.c —— Nuked-OPN2 (YM3438) 的 C ABI 封装，供 Python ctypes 使用。
 *
 * 设计要点（和 OPM 那个 wrapper 的坑相反）：
 *   1. **write 不推进芯片时钟**。Nuked 的 OPN2_Write 只是把地址/数据挂起，
 *      真正生效要等 OPN2_Clock 里的 OPN2_DoIO/DoRegWrite。所以寄存器写先入队，
 *      在 generate 的时钟槽里插入：地址落在第 0 槽、数据落在第 1 槽。
 *   2. 一个输出帧 = 24 个 OPN2_Clock。每个输出帧最多消化 **1 个**寄存器写：
 *      数据写之后有 22 个时钟让它在匹配的 operator slot 上落地（12 槽轮转，
 *      最多等 12 个时钟）。如果一帧塞多个写，下一个地址写会在数据落地前
 *      把 write_fm_data 清掉，寄存器就丢了 —— 这是 Nuked 的时分复用语义，
 *      不是 wrapper 能绕过的。55 555 写/秒的吞吐对 NECOPN 足够。
 *   3. YM2608 的 FM 输出是 6 通道 **时分复用** 的：24 个时钟里每个通道出现 3 次
 *      （YM3438 模式 out_en = (cycles&3)!=0）。所以一个 55.5 kHz 采样要把 24 个
 *      时钟的 mol/mor 累加再 /3，不能只取最后一个（那样只剩一个通道）。
 *   4. 输出是 9-bit 有符号（-256..255），6 通道满幅约 ±1530。
 *      这里乘 OPN2_GAIN 拉到接近 int16 满幅，再由 Python 侧做总音量。
 *
 * 编译：
 *   gcc -O2 -shared -static -o opn2.dll core-opn2\ym3438.c core-opn2\opn2_wrapper.c
 */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "ym3438.h"

#ifdef _WIN32
#define EXPORT __declspec(dllexport)
#else
#define EXPORT __attribute__((visibility("default")))
#endif

/* 9-bit DAC × 6 通道 ≈ ±1530；×32 后满幅约 ±49000（真音乐远达不到）。
 * 与 fmgen 参考渲染的 RMS 对齐（实测 5.mid：fmgen 473 / Nuked 230 @×16，
 * ×32 后约 460，见 docs/PERFORMANCE.md）。 */
#define OPN2_GAIN 32

/* 输出耦合电容的高通（~5 Hz @55 555 Hz），去掉 YM3438 9-bit DAC 的固定直流偏置。 */
#define OPN2_DC_ALPHA 0.000566f

/* 写队列容量。NECOPN 一次 note-on 最多 ~30 个写，8192 足够吸收整首歌的爆发。 */
#define WRITEQ_CAP 8192

typedef struct {
    ym3438_t chip;
    uint32_t clock;
    uint32_t rate;          /* clock / 144，整数截断（8 MHz → 55555） */

    /* 寄存器写队列：addr 是 9-bit（0x000..0x1FF），data 是 8-bit */
    uint32_t q_addr[WRITEQ_CAP];
    uint8_t  q_data[WRITEQ_CAP];
    uint32_t q_head, q_tail;
    uint32_t q_overflow;

    /* 输出隔直状态（左/右） */
    float dc_l, dc_r;
} nopn2_t;

EXPORT void* nopn2_create(uint32_t clock_hz) {
    if (!clock_hz) return NULL;
    nopn2_t *h = (nopn2_t*)calloc(1, sizeof(nopn2_t));
    if (!h) return NULL;
    h->clock = clock_hz;
    h->rate = clock_hz / 144u;
    /* YM3438 模式（默认 0x02 = readmode）：输出路径是 YM3438，不做 YM2612 的 ×3。
     * chip_type 是 Nuked 里的文件级全局量，多实例共享；本项目只用这一种模式。 */
    OPN2_SetChipType(ym3438_mode_readmode);
    OPN2_Reset(&h->chip);
    return h;
}

EXPORT void nopn2_destroy(void* handle) {
    free(handle);
}

EXPORT void nopn2_reset(void* handle) {
    nopn2_t *h = (nopn2_t*)handle;
    if (!h) return;
    h->q_head = h->q_tail = 0;
    h->q_overflow = 0;
    OPN2_Reset(&h->chip);
}

EXPORT uint32_t nopn2_sample_rate(void* handle) {
    nopn2_t *h = (nopn2_t*)handle;
    return h ? h->rate : 0;
}

EXPORT uint32_t nopn2_pending(void* handle) {
    nopn2_t *h = (nopn2_t*)handle;
    return h ? (h->q_tail - h->q_head) : 0;
}

EXPORT uint32_t nopn2_overflow(void* handle) {
    nopn2_t *h = (nopn2_t*)handle;
    return h ? h->q_overflow : 0;
}

/* reg: 0x000..0x1FF。bit8 = 第二寄存器区（Nuked 的 port bit1）。 */
EXPORT void nopn2_write(void* handle, uint32_t reg, uint8_t val) {
    nopn2_t *h = (nopn2_t*)handle;
    if (!h) return;
    uint32_t tail = h->q_tail;
    if (tail - h->q_head >= WRITEQ_CAP) {
        h->q_overflow++;
        return;                    /* 队列满只丢写并计数，绝不推进时钟 */
    }
    h->q_addr[tail % WRITEQ_CAP] = (uint16_t)(reg & 0x1FF);
    h->q_data[tail % WRITEQ_CAP] = val;
    h->q_tail = tail + 1;
}

EXPORT uint8_t nopn2_read(void* handle, uint32_t reg) {
    nopn2_t *h = (nopn2_t*)handle;
    if (!h) return 0;
    return OPN2_Read(&h->chip, (reg & 0x100) ? 2u : 0u);
}

/*
 * 生成 nframes 个 55.5 kHz 立体声帧。
 *
 * 每个输出帧 = 24 个 OPN2_Clock：
 *   - 若队列非空：第 0 槽发地址（port 0 或 2），Clock；第 1 槽发数据（port 1 或 3），Clock
 *   - 剩余 22 槽只 Clock
 *   - 24 槽的 mol/mor 累加后 /3，得到 6 通道之和
 */
EXPORT void nopn2_generate(void* handle, int16_t* out, uint32_t nframes) {
    nopn2_t *h = (nopn2_t*)handle;
    if (!h || !out || !nframes) return;

    ym3438_t *chip = &h->chip;

    for (uint32_t i = 0; i < nframes; i++) {
        int32_t acc_l = 0, acc_r = 0;
        int16_t buf[2];
        int c = 0;

        if (h->q_head != h->q_tail) {
            uint32_t idx = h->q_head % WRITEQ_CAP;
            uint16_t addr = h->q_addr[idx];
            uint8_t  data = h->q_data[idx];
            h->q_head++;

            uint32_t base_port = (addr & 0x100) ? 2u : 0u;
            OPN2_Write(chip, base_port, (uint8_t)(addr & 0xFF));
            OPN2_Clock(chip, buf);
            acc_l += buf[0]; acc_r += buf[1];
            OPN2_Write(chip, base_port | 1u, data);
            OPN2_Clock(chip, buf);
            acc_l += buf[0]; acc_r += buf[1];
            c = 2;
        }

        for (; c < 24; c++) {
            OPN2_Clock(chip, buf);
            acc_l += buf[0];
            acc_r += buf[1];
        }

        float fl = (float)((acc_l / 3) * OPN2_GAIN);
        float fr = (float)((acc_r / 3) * OPN2_GAIN);
        h->dc_l += (fl - h->dc_l) * OPN2_DC_ALPHA;
        h->dc_r += (fr - h->dc_r) * OPN2_DC_ALPHA;
        fl -= h->dc_l;
        fr -= h->dc_r;

        int32_t l = (int32_t)fl;
        int32_t r = (int32_t)fr;
        if (l > 32767) l = 32767;
        else if (l < -32768) l = -32768;
        if (r > 32767) r = 32767;
        else if (r < -32768) r = -32768;
        out[2 * i] = (int16_t)l;
        out[2 * i + 1] = (int16_t)r;
    }
}
