// necopna_fmgen.cpp
#include <cstdint>
#include <cstring>
#include <vector>
#include <string>
#include <new>
#include <cstdio>

#include "necopna.h"
#include "fmgen/fmgen_fmgwrap.h"

#ifdef _WIN32
#  include <windows.h>
#endif

struct nopna_handle {
    void* chip[2];              // 两个 FM::OPNA 实例
    uint32_t clock;
    uint32_t rate;
    std::string rhythm_path;
    std::vector<int32_t> mixbuf;
    
    nopna_handle(uint32_t clk, uint32_t r)
        : clock(clk), rate(r), rhythm_path() {
        chip[0] = OPNA_Construct();
        chip[1] = OPNA_Construct();
        if (chip[0]) OPNA_Init(chip[0], clk, r, false, nullptr);
        if (chip[1]) OPNA_Init(chip[1], clk, r, false, nullptr);
    }
    
    ~nopna_handle() {
        if (chip[0]) OPNA_Destruct(chip[0]);
        if (chip[1]) OPNA_Destruct(chip[1]);
    }
};

extern "C" {

NOPNA_API void* nopna_create(uint32_t clock_hz) {
    if (!clock_hz) return nullptr;
    uint32_t r = clock_hz / 144;      // 与 Python 侧一致
    return new (std::nothrow) nopna_handle(clock_hz, r);
}

NOPNA_API void nopna_destroy(void* h) {
    delete reinterpret_cast<nopna_handle*>(h);
}

NOPNA_API void nopna_reset(void* h) {
    auto* hh = reinterpret_cast<nopna_handle*>(h);
    if (!hh) return;
    OPNA_Reset(hh->chip[0]);
    OPNA_Reset(hh->chip[1]);
}

NOPNA_API void nopna_write(void* h, uint32_t port, uint8_t addr, uint8_t data) {
    auto* hh = reinterpret_cast<nopna_handle*>(h);
    if (!hh) return;
    uint32_t chip_idx = port & 1;
    uint32_t fmgen_addr = addr | ((port & 2) ? 0x100u : 0u);
    OPNA_SetReg(hh->chip[chip_idx], fmgen_addr, data);
}

NOPNA_API uint8_t nopna_read(void* h, uint32_t port, uint8_t addr) {
    auto* hh = reinterpret_cast<nopna_handle*>(h);
    if (!hh) return 0;
    uint32_t chip_idx = port & 1;
    uint32_t fmgen_addr = addr | ((port & 2) ? 0x100u : 0u);
    if (addr <= 0x0F) {
        return (uint8_t)OPNA_GetReg(hh->chip[chip_idx], fmgen_addr);
    }
    return (uint8_t)OPNA_ReadStatusEx(hh->chip[chip_idx]);
}

NOPNA_API uint32_t nopna_sample_rate(void* h) {
    auto* hh = reinterpret_cast<nopna_handle*>(h);
    return hh ? hh->rate : 0;
}

NOPNA_API void nopna_generate(void* h, int16_t* out, uint32_t nframes) {
    auto* hh = reinterpret_cast<nopna_handle*>(h);
    if (!hh || !out || !nframes) return;
    
    hh->mixbuf.assign(nframes * 2, 0);   // fmgen 的 Mix 是加算 → 必须先清零
    OPNA_Mix(hh->chip[0], hh->mixbuf.data(), nframes);
    OPNA_Mix(hh->chip[1], hh->mixbuf.data(), nframes);
    
    for (uint32_t i = 0; i < nframes * 2; i++) {
        int32_t v = hh->mixbuf[i];
        if (v > 32767) v = 32767;
        else if (v < -32768) v = -32768;
        out[i] = (int16_t)v;
    }
}

NOPNA_API void nopna_generate_rate(void* h, int16_t* out, uint32_t nframes, uint32_t rate_hz) {
    // fmgen 已经在目标速率生成 → 忽略 rate_hz
    (void)rate_hz;
    nopna_generate(h, out, nframes);
}

NOPNA_API int nopna_set_rhythm_rom(void* h, uint8_t const* data, uint32_t bytes) {
    auto* hh = reinterpret_cast<nopna_handle*>(h);
    if (!hh) return -1;
    if (!data || bytes == 0) return nopna_clear_rhythm_rom(h);
    
    // 收到的其实是路径字符串
    std::string dir_str(reinterpret_cast<const char*>(data), bytes);
    
    // fmgen 源码里是用 strncat 直接拼 "2608_bd.wav"，
    // 所以文件夹路径末尾必须有 '/' 或 '\\'，否则会变成 "C:\wav2608_bd.wav"
    if (!dir_str.empty() && dir_str.back() != '/' && dir_str.back() != '\\') {
        dir_str += '/';
    }
    
    bool ok0 = OPNA_LoadRhythmSample(hh->chip[0], dir_str.c_str());
    bool ok1 = OPNA_LoadRhythmSample(hh->chip[1], dir_str.c_str());
    return (ok0 && ok1) ? 0 : -4;
}

NOPNA_API int nopna_clear_rhythm_rom(void* h) {
    // fmgen 没有"清空 ROM"的语义 —— 不做任何事
    (void)h;
    return 0;
}

}  // extern "C"