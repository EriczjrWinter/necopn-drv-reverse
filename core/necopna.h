/* necopna.h -- C API for the necopn_player YM2608 (OPNA) core wrapper.
 *
 * Wraps two ymfm ym2608 instances ("dual OPNA", see below) behind a plain
 * cdecl C interface callable from Python ctypes.
 *
 * Port model (matches NECOPN.DRV, see necopn_reverse/REPORT.md):
 *   port = 0 ... the 0x188/0x18A register pair   -> chip 0, base register set
 *   port = 1 ... the 0x18C/0x18E register pair   -> chip 1, base register set
 *   port = 2 ... chip 0, extended register set   (optional extra, see below)
 *   port = 3 ... chip 1, extended register set   (optional extra, see below)
 * In other words: one nopna handle owns two YM2608 chips; port bit 0 selects
 * the chip, port bit 1 selects the register set.  Ports 2/3 are a pure
 * extension of the specified API (they reach each chip's 0x100-0x1FF
 * registers: FM channels 4-6 and ADPCM-B) and can be ignored by callers.
 *
 * Register access:
 *   nopna_write(h, port, addr, data) writes one register:
 *     -> write the chip's address port with `addr`, then its data port with
 *        `data`.  This is the same effect as the driver's "out 0x188,addr /
 *        out 0x18A,data" sequence, in one call.
 *   nopna_read(h, port, addr) reads back:
 *     addr 0x00-0x0F -> SSG register readback (data port after latching addr),
 *     any other addr -> the chip's status register (same as reading 0x188).
 *
 * Audio:
 *   nopna_generate() fills 2*nframes interleaved stereo int16 samples at
 *   nopna_sample_rate().  The mix is exactly the one ymfm's own vgmrender
 *   example uses for YM2608 (examples/vgmrender/vgmrender.cpp:268-275):
 *     L = out[0] (FM L + ADPCM-A/B) + out[2] (SSG mono)
 *     R = out[1] (FM R + ADPCM-A/B) + out[2] (SSG mono)
 *   both chips are summed into the same stereo stream, clamped to int16.
 *
 * Sample rate (verified in vendor/ymfm/src/ymfm_opn.h:519-528):
 *   ymfm ym2608::sample_rate(clock) = clock/8  (default OPN_FIDELITY_MAX),
 *   clock/24 for OPN_FIDELITY_MED, clock/48 for OPN_FIDELITY_MIN.
 *   The FM engine itself always runs at clock/144 (prescale 6); the fidelity
 *   setting only changes how often generate() emits a (held) sample.
 *   The wrapper uses ymfm's default (MAX), so nopna_sample_rate() == clock/8,
 *   e.g. 8000000/8 = 1000000 Hz for the PC-98 OPNA clock.
 */

#ifndef NECOPNA_H
#define NECOPNA_H

#include <stdint.h>

#if defined(_WIN32)
#  if defined(NOPNA_BUILD)
#    define NOPNA_API __declspec(dllexport)
#  else
#    define NOPNA_API __declspec(dllimport)
#  endif
#else
#  define NOPNA_API
#endif

#ifdef __cplusplus
extern "C" {
#endif

/* Create a core instance (two YM2608 chips) for the given master clock.
 * clock_hz must be non-zero (the PC-98 OPNA clock is 8000000).
 * Returns NULL on allocation failure. */
NOPNA_API void*    nopna_create(uint32_t clock_hz);

/* Destroy an instance created with nopna_create(NULL is ignored). */
NOPNA_API void     nopna_destroy(void* h);

/* Reset both chips (also restores the YM2608 rhythm instrument addresses). */
NOPNA_API void     nopna_reset(void* h);

/* Write register `addr` of the chip/register-set selected by `port`.
 * port = 0 -> 0x188/0x18A pair (chip 0), port = 1 -> 0x18C/0x18E pair (chip 1);
 * port = 2/3 -> extended register set of chip 0/1 (optional extension). */
NOPNA_API void     nopna_write(void* h, uint32_t port, uint8_t addr, uint8_t data);

/* Read back: addr <= 0x0F reads an SSG register, anything else returns the
 * status register of the selected chip/register-set. */
NOPNA_API uint8_t  nopna_read(void* h, uint32_t port, uint8_t addr);

/* Output sample rate of nopna_generate(): clock_hz / 8 (see header comment). */
NOPNA_API uint32_t nopna_sample_rate(void* h);

/* Generate `nframes` stereo frames (2*nframes interleaved int16 samples) from
 * both chips mixed together.  `out` must hold at least 2*nframes elements. */
NOPNA_API void     nopna_generate(void* h, int16_t* out, uint32_t nframes);

/* Same as nopna_generate(), but box-filtered down to `rate_hz`.
 * ymfm 的原生输出是 clock/8（8MHz 时钟下 = 1MHz，ZOH 过采样）；本函数在 DLL 内
 * 做加权抽取，只把最终采样率的样本交给调用方（避免海量数据穿过 ctypes）。
 * rate_hz == 0 或 >= 原生速率 时等价于 nopna_generate()。
 * 输出速率由调用方自行记录（例如 8000000/144 = 55555）。 */
NOPNA_API void     nopna_generate_rate(void* h, int16_t* out, uint32_t nframes, uint32_t rate_hz);

/* Install the authentic YM2608 rhythm (ADPCM-A) ROM dump — the 8KB percussion
 * sample ROM that lives inside the real chip.  `data` must hold at least
 * 0x2000 (8192) bytes; the first 8192 are copied into the handle and served to
 * both chips through ymfm_external_read(ACCESS_ADPCM_A, ...).  The instrument
 * start/end addresses preset by ym2608::reset() match the authentic ROM layout,
 * so a plain dump (e.g. MAME's "ym2608_adpcm_rom.bin") works as-is.
 * Passing data=NULL is the same as calling nopna_clear_rhythm_rom().
 * Returns 0 on success, -1 on NULL handle, -2 when bytes < 0x2000.
 * 未安装时两颗芯片使用内置的合成打击乐 ROM（见 necopna_ymfm.cpp）。 */
NOPNA_API int      nopna_set_rhythm_rom(void* h, uint8_t const* data, uint32_t bytes);

/* Drop the installed rhythm ROM and fall back to the built-in synthetic one. */
NOPNA_API int      nopna_clear_rhythm_rom(void* h);

#ifdef __cplusplus
}
#endif

#endif /* NECOPNA_H */
