#include "emu2149.h"
#include <stdint.h>

#ifdef _WIN32
#define EXPORT __declspec(dllexport)
#else
#define EXPORT __attribute__((visibility("default")))
#endif

#define PSG_CLOCK 1789772 // MSX/PC-88 标准时钟
#define OUTPUT_RATE 55930 // OPM 项目的统一采样率

/* 通用入口：OPNA 的 SSG 时钟是主时钟/4（8 MHz → 2 MHz），
 * 输出采样率要和 FM 的 55 555 Hz 对齐。 */
EXPORT void* ssg_create_ex(uint32_t clock, uint32_t rate) {
    if (!clock || !rate) return 0;
    PSG *psg = PSG_new(clock, rate);
    if (!psg) return 0;
    PSG_reset(psg);
    return (void*)psg;
}

EXPORT void* ssg_create() {
    return ssg_create_ex(PSG_CLOCK, OUTPUT_RATE);
}

// 【核心修复】：直接调用底层的寄存器写入函数
EXPORT void ssg_write(void* handle, uint8_t reg, uint8_t data) {
    PSG_writeReg((PSG*)handle, reg, data); 
}

EXPORT void ssg_generate(void* handle, int16_t* buffer, int num_samples) {
    PSG *psg = (PSG*)handle;
    for (int i = 0; i < num_samples; i++) {
        buffer[i] = PSG_calc(psg);
    }
}

EXPORT void ssg_destroy(void* handle) {
    PSG_delete((PSG*)handle);
}
