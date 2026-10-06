/* necopna_ymfm.cpp -- thin wrapper around ymfm's ym2608 core.
 *
 * Builds two ymfm::ym2608 instances ("dual OPNA") and exposes the plain C API
 * declared in necopna.h.  See necopna.h for the port/register/semantics
 * contract and the cited ymfm sources for the sample-rate and mixing rules.
 *
 * Rhythm ROM note:
 *   On real hardware the YM2608's ADPCM-A (rhythm) samples live in a ROM
 *   inside the chip; ymfm does not ship that data (its vgmrender example
 *   loads "ym2608_adpcm_rom.bin" from disk, see
 *   vendor/ymfm/examples/vgmrender/vgmrender.cpp:356-358) and serves it to the
 *   core through ymfm_interface::ymfm_external_read(ACCESS_ADPCM_A, offset).
 *   The instrument start/end addresses are fixed in silicon and are preset by
 *   ymfm in ym2608::reset() (vendor/ymfm/src/ymfm_opn.cpp:1001-1007), which is
 *   exactly the layout of an authentic 8KB ROM dump.
 *   nopna_set_rhythm_rom() installs such a dump (user supplied, not
 *   distributable here); without it the wrapper falls back to a small
 *   synthetic 8KB percussion ROM (encoded with the same YM2608 ADPCM format
 *   the core decodes in ymfm_adpcm.cpp) so the rhythm path stays functional
 *   and testable.  nopna_clear_rhythm_rom() switches back to the synthetic ROM.
 */

#include "necopna.h"

#include "vendor/ymfm/src/ymfm.h"
#include "vendor/ymfm/src/ymfm_opn.h"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <new>
#include <vector>

namespace
{

int32_t clamp_i(int32_t value, int32_t lo, int32_t hi)
{
	return (value < lo) ? lo : ((value > hi) ? hi : value);
}

// ---------------------------------------------------------------
// YM2608 ADPCM encoder (inverse of adpcm_a_channel::clock(),
// vendor/ymfm/src/ymfm_adpcm.cpp:151-213)
// ---------------------------------------------------------------

int32_t const s_steps[49] =
{
	 16,  17,   19,   21,   23,   25,   28,
	 31,  34,   37,   41,   45,   50,   55,
	 60,  66,   73,   80,   88,   97,  107,
	118, 130,  143,  157,  173,  190,  209,
	230, 253,  279,  307,  337,  371,  408,
	449, 494,  544,  598,  658,  724,  796,
	876, 963, 1060, 1166, 1282, 1411, 1552
};

int8_t const s_step_inc[8] = { -1, -1, -1, -1, 2, 5, 7, 9 };

// encode `count` 12-bit target samples into ADPCM bytes (2 nibbles/byte,
// high nibble first, exactly like adpcm_a_channel::clock() consumes them)
void encode_adpcm(int16_t const *target, size_t count, std::vector<uint8_t> &dest)
{
	dest.assign((count + 1) / 2, 0);
	int32_t acc = 0;
	int32_t step = 0;
	for (size_t index = 0; index < count; index++)
	{
		int32_t want = clamp_i(target[index], -2047, 2047);
		int best = 0;
		int32_t bestcost = INT32_MAX;
		for (int cand = 0; cand < 16; cand++)
		{
			int32_t delta = (2 * (cand & 7) + 1) * s_steps[step] / 8;
			if (cand & 8)
				delta = -delta;
			int32_t newacc = (acc + delta) & 0xfff;
			int32_t value = (newacc << 20) >> 20;	// sign-extend 12 bits
			int32_t diff = ((want - value) + 2048) & 0xfff;	// wrap-aware
			diff -= 2048;
			int32_t cost = (diff < 0) ? -diff : diff;
			if (cost < bestcost)
			{
				bestcost = cost;
				best = cand;
			}
		}
		int32_t delta = (2 * (best & 7) + 1) * s_steps[step] / 8;
		if (best & 8)
			delta = -delta;
		acc = (acc + delta) & 0xfff;
		step = clamp_i(step + s_step_inc[best & 7], 0, 48);
		if ((index & 1) == 0)
			dest[index / 2] = uint8_t(best << 4);
		else
			dest[index / 2] |= uint8_t(best & 0x0f);
	}
}

// deterministic uniform noise in [-1,1]
float synth_noise(uint32_t &state)
{
	state = state * 1664525u + 1013904223u;
	return (float((state >> 8) & 0xffff) / 32768.0f) - 1.0f;
}

// build the 8KB synthetic percussion ROM
std::vector<uint8_t> const &rhythm_rom()
{
	// instrument start/end addresses as preset by ymfm's ym2608::reset()
	// (vendor/ymfm/src/ymfm_opn.cpp:1002-1007)
	static uint32_t const kStart[6] = { 0x0000, 0x01c0, 0x0440, 0x1b80, 0x1d00, 0x1f80 };
	static uint32_t const kEnd[6]   = { 0x01bf, 0x043f, 0x1b7f, 0x1cff, 0x1f7f, 0x1fff };
	static std::vector<uint8_t> const s_rom = []() {
		std::vector<uint8_t> rom(0x2000, 0);
		uint32_t noise = 0x12345678;
		for (int inst = 0; inst < 6; inst++)
		{
			size_t count = size_t(kEnd[inst] - kStart[inst] + 1) * 2;
			std::vector<int16_t> target(count);
			for (size_t i = 0; i < count; i++)
			{
				float x = (count > 1) ? float(i) / float(count - 1) : 0.0f;
				float n = synth_noise(noise);
				float s = 0.0f;
				switch (inst)
				{
					case 0:		// bass drum: low sine + click
						s = 1900.0f * std::exp(-4.0f * x) * std::sin(2.0f * 3.1415926f * 3.0f * x)
						  + 500.0f * std::exp(-60.0f * x) * n;
						break;
					case 1:		// snare drum: noise + body
						s = 1500.0f * std::exp(-5.0f * x) * (0.75f * n + 0.5f * std::sin(2.0f * 3.1415926f * 10.0f * x));
						break;
					case 2:		// top cymbal: long noisy shimmer
						s = 1400.0f * std::exp(-4.0f * x) * (0.8f * n + 0.35f * std::sin(2.0f * 3.1415926f * 120.0f * x) + 0.25f * std::sin(2.0f * 3.1415926f * 187.0f * x));
						break;
					case 3:		// high hat: short noise burst
						s = 1700.0f * std::exp(-7.0f * x) * n;
						break;
					case 4:		// tom tom: mid sine
						s = 1800.0f * std::exp(-5.0f * x) * std::sin(2.0f * 3.1415926f * 12.0f * x);
						break;
					default:	// rim shot: click + short tone
						s = 2000.0f * std::exp(-12.0f * x) * (0.6f * n + 0.7f * std::sin(2.0f * 3.1415926f * 4.0f * x));
						break;
				}
				target[i] = int16_t(clamp_i(int32_t(s), -2047, 2047));
			}
			std::vector<uint8_t> encoded;
			encode_adpcm(target.data(), target.size(), encoded);
			std::copy(encoded.begin(), encoded.end(), rom.begin() + kStart[inst]);
		}
		return rom;
	}();
	return s_rom;
}

// ---------------------------------------------------------------
// ymfm interface: serves the rhythm ROM to the ADPCM-A engine
// ---------------------------------------------------------------

class nopna_interface : public ymfm::ymfm_interface
{
public:
	// 安装原生 ROM 后指向句柄里的 8KB dump；未安装时回落到内置合成 ROM。
	void set_rom(uint8_t const *data, uint32_t bytes)
	{
		m_rom = data;
		m_rom_size = bytes;
	}

	virtual uint8_t ymfm_external_read(ymfm::access_class type, uint32_t offset) override
	{
		if (type == ymfm::ACCESS_ADPCM_A)
		{
			if (m_rom != nullptr)
				return m_rom[offset & 0x1fff];	// 真芯片 ROM 8KB，A13 以上不参与寻址
			std::vector<uint8_t> const &rom = rhythm_rom();
			return (offset < rom.size()) ? rom[offset] : 0;
		}
		return 0;
	}

private:
	uint8_t const *m_rom = nullptr;
	uint32_t m_rom_size = 0;
};

// ---------------------------------------------------------------
// handle: two YM2608 chips
// ---------------------------------------------------------------

struct nopna_handle
{
	uint32_t m_clock;
	uint32_t m_rate;
	nopna_interface m_intf0;
	nopna_interface m_intf1;
	ymfm::ym2608 m_chip0;
	ymfm::ym2608 m_chip1;
	std::vector<ymfm::ym2608::output_data> m_scratch;
	std::vector<uint8_t> m_user_rom;

	nopna_handle(uint32_t clock) :
		m_clock(clock),
		m_rate(0),
		m_intf0(),
		m_intf1(),
		m_chip0(m_intf0),
		m_chip1(m_intf1)
	{
		m_chip0.reset();
		m_chip1.reset();
		m_rate = m_chip0.sample_rate(m_clock);
	}

	ymfm::ym2608 &chip(uint32_t port) { return (port & 1) ? m_chip1 : m_chip0; }

	// 安装/清除原生 YM2608 打击乐 ROM（ADPCM-A 板载 8KB dump）。
	// data=NULL 清除（回落内置合成 ROM）；bytes < 0x2000 返回 -2；成功返回 0。
	int set_rhythm_rom(uint8_t const *data, uint32_t bytes)
	{
		if (data == nullptr)
		{
			m_user_rom.clear();
			m_intf0.set_rom(nullptr, 0);
			m_intf1.set_rom(nullptr, 0);
			return 0;
		}
		if (bytes < 0x2000)
			return -2;
		m_user_rom.assign(data, data + 0x2000);
		m_intf0.set_rom(m_user_rom.data(), uint32_t(m_user_rom.size()));
		m_intf1.set_rom(m_user_rom.data(), uint32_t(m_user_rom.size()));
		return 0;
	}

	// 把两颗芯片混成 nframes 个原生立体声帧（int32 交错，未钳位）
	void mix_native(uint32_t nframes, int32_t *out)
	{
		if (m_scratch.size() < nframes)
			m_scratch.resize(nframes);
		std::memset(out, 0, size_t(nframes) * 2 * sizeof(int32_t));
		for (int index = 0; index < 2; index++)
		{
			ymfm::ym2608 &c = (index == 0) ? m_chip0 : m_chip1;
			c.generate(m_scratch.data(), nframes);
			for (uint32_t f = 0; f < nframes; f++)
			{
				ymfm::ym2608::output_data const &s = m_scratch[f];
				out[f * 2 + 0] += s.data[0] + s.data[2];
				out[f * 2 + 1] += s.data[1] + s.data[2];
			}
		}
	}

	// nopna_generate_rate 的降采样状态
	std::vector<int32_t> m_pool;
	uint32_t m_pool_len = 0;
	uint32_t m_pool_pos = 0;
	uint32_t m_rate_out = 0;
	uint64_t m_step_fp = 0;
	uint64_t m_avail_fp = 0;
	int32_t  m_last[2] = { 0, 0 };
};

}

// ---------------------------------------------------------------
// exported C API
// ---------------------------------------------------------------

extern "C" NOPNA_API void *nopna_create(uint32_t clock_hz)
{
	if (clock_hz == 0)
		return nullptr;
	return new (std::nothrow) nopna_handle(clock_hz);
}

extern "C" NOPNA_API void nopna_destroy(void *h)
{
	delete reinterpret_cast<nopna_handle *>(h);
}

extern "C" NOPNA_API void nopna_reset(void *h)
{
	nopna_handle *hh = reinterpret_cast<nopna_handle *>(h);
	if (hh == nullptr)
		return;
	hh->m_chip0.reset();
	hh->m_chip1.reset();
}

extern "C" NOPNA_API void nopna_write(void *h, uint32_t port, uint8_t addr, uint8_t data)
{
	nopna_handle *hh = reinterpret_cast<nopna_handle *>(h);
	if (hh == nullptr)
		return;
	ymfm::ym2608 &chip = hh->chip(port);
	if ((port & 2) != 0)
	{
		chip.write_address_hi(addr);
		chip.write_data_hi(data);
	}
	else
	{
		chip.write_address(addr);
		chip.write_data(data);
	}
}

extern "C" NOPNA_API uint8_t nopna_read(void *h, uint32_t port, uint8_t addr)
{
	nopna_handle *hh = reinterpret_cast<nopna_handle *>(h);
	if (hh == nullptr)
		return 0;
	ymfm::ym2608 &chip = hh->chip(port);
	if ((port & 2) != 0)
	{
		if (addr <= 0x0f)
		{
			chip.write_address_hi(addr);
			return chip.read_data_hi();
		}
		return chip.read_status_hi();
	}
	if (addr <= 0x0f)
	{
		chip.write_address(addr);
		return chip.read_data();
	}
	return chip.read_status();
}

extern "C" NOPNA_API uint32_t nopna_sample_rate(void *h)
{
	nopna_handle *hh = reinterpret_cast<nopna_handle *>(h);
	return (hh != nullptr) ? hh->m_rate : 0;
}

extern "C" NOPNA_API void nopna_generate(void *h, int16_t *out, uint32_t nframes)
{
	nopna_handle *hh = reinterpret_cast<nopna_handle *>(h);
	if (hh == nullptr || out == nullptr || nframes == 0)
		return;
	if (hh->m_scratch.size() < nframes)
		hh->m_scratch.resize(nframes);
	std::memset(out, 0, size_t(nframes) * 2 * sizeof(int16_t));
	for (int index = 0; index < 2; index++)
	{
		ymfm::ym2608 &chip = (index == 0) ? hh->m_chip0 : hh->m_chip1;
		chip.generate(hh->m_scratch.data(), nframes);
		for (uint32_t frame = 0; frame < nframes; frame++)
		{
			ymfm::ym2608::output_data const &sample = hh->m_scratch[frame];
			// ymfm vgmrender YM2608 mix: L = out0 + out2, R = out1 + out2
			int32_t left = sample.data[0] + sample.data[2];
			int32_t right = sample.data[1] + sample.data[2];
			out[frame * 2 + 0] = int16_t(clamp_i(out[frame * 2 + 0] + left, -32768, 32767));
			out[frame * 2 + 1] = int16_t(clamp_i(out[frame * 2 + 1] + right, -32768, 32767));
		}
	}
}

extern "C" NOPNA_API void nopna_generate_rate(void *h, int16_t *out, uint32_t nframes, uint32_t rate_hz)
{
	nopna_handle *hh = reinterpret_cast<nopna_handle *>(h);
	if (hh == nullptr || out == nullptr || nframes == 0)
		return;
	if (rate_hz == 0 || rate_hz >= hh->m_rate)
	{
		nopna_generate(h, out, nframes);
		return;
	}
	if (hh->m_step_fp == 0 || hh->m_rate_out != rate_hz)
	{
		hh->m_rate_out = rate_hz;
		hh->m_step_fp = (uint64_t(hh->m_rate) << 32) / rate_hz;
		hh->m_avail_fp = 0;
		hh->m_pool_pos = hh->m_pool_len = 0;
	}
	const uint64_t FP1 = uint64_t(1) << 32;
	for (uint32_t f = 0; f < nframes; f++)
	{
		int64_t acc[2] = { 0, 0 };
		uint64_t need = hh->m_step_fp;
		while (need > 0)
		{
			if (hh->m_avail_fp == 0)
			{
				if (hh->m_pool_pos >= hh->m_pool_len)
				{
					const uint32_t BLOCK = 2048;
					hh->m_pool.resize(size_t(BLOCK) * 2);
					hh->mix_native(BLOCK, hh->m_pool.data());
					hh->m_pool_len = BLOCK;
					hh->m_pool_pos = 0;
				}
				hh->m_last[0] = hh->m_pool[size_t(hh->m_pool_pos) * 2 + 0];
				hh->m_last[1] = hh->m_pool[size_t(hh->m_pool_pos) * 2 + 1];
				hh->m_pool_pos++;
				hh->m_avail_fp = FP1;
			}
			uint64_t use = (need < hh->m_avail_fp) ? need : hh->m_avail_fp;
			acc[0] += int64_t(hh->m_last[0]) * int64_t(use);
			acc[1] += int64_t(hh->m_last[1]) * int64_t(use);
			need -= use;
			hh->m_avail_fp -= use;
		}
		out[f * 2 + 0] = int16_t(clamp_i(int32_t(acc[0] / int64_t(hh->m_step_fp)), -32768, 32767));
		out[f * 2 + 1] = int16_t(clamp_i(int32_t(acc[1] / int64_t(hh->m_step_fp)), -32768, 32767));
	}
}

extern "C" NOPNA_API int nopna_set_rhythm_rom(void *h, uint8_t const *data, uint32_t bytes)
{
	nopna_handle *hh = reinterpret_cast<nopna_handle *>(h);
	if (hh == nullptr)
		return -1;
	return hh->set_rhythm_rom(data, bytes);
}

extern "C" NOPNA_API int nopna_clear_rhythm_rom(void *h)
{
	nopna_handle *hh = reinterpret_cast<nopna_handle *>(h);
	if (hh == nullptr)
		return -1;
	return hh->set_rhythm_rom(nullptr, 0);
}
