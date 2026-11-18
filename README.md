# NECOPN.DRV Reverse Engineering & Renderer

> **Language**: [English | [简体中文](README_zh.md)]

This project is a deep reverse engineering and high-fidelity reimplementation of the `NECOPN.DRV` MIDI driver, exclusively used in NEC PC-9821 running Windows 95. By combining Python with a custom C++ core, it aims to reproduce the exact sonic behavior and voice allocation logic of the original hardware.

> **🎯 Status**: Basic complete reproduction of the original hardware (Pitch, Envelope, Voice allocation, and Rhythm) is achieved. Software LFO is now functional with all previously audible artifacts (electromagnetic noise, excessive warble) resolved — though undiscovered quirks may remain.
> 
> **📦 Out-of-the-box**: A hardcoded `fixed_bank.py` (extracted from the original driver) is included. **You do NOT need the original `NECOPN.DRV` to run the player.**
>
> **⚡ Realtime**: `NECOPN-RT/` plays MIDI directly (GUI/CLI, 22 ms latency, loop / RHY folder / SSG-only / WAV export / zh-en). See **[NECOPN-RT/README.md](NECOPN-RT/README.md)**.

## 🏗️ Architecture & Core Notes

**1. Software Mapping of "Dual Chips"**
Although the PC-98 hardware features a YM2608 (OPNA), the official driver strictly limits itself to CH0-CH2 on each chip to avoid hardware quirks. Therefore, this project maps the **2 core instances × 3 channels per core (6 voices total)** software architecture, perfectly replicating the driver's logic rather than the physical hardware limits.

**2. Why `fmgen` instead of `ymfm`?**
We initially used `ymfm`. However, during waveform comparisons against real hardware recordings, `ymfm` showed slight deviations in sustained notes (e.g., low SR values) compared to our expectations. To achieve the most authentic hardware listening experience, the backend core was swapped to `fmgen` (used by the NP21/W emulator).
*Note: For API compatibility, the Python wrapper class remains named `YmfmCore`, but dynamically loads `necopna_fmgen.dll`.*

## 📦 Required Assets

1. **Built-in Voice Bank**
   * The 128 GM voices are already hardcoded in `fixed_bank.py` (extracted via reverse engineering). No external bank files are required.
   * *(Optional)* If you have the original `NECOPN.DRV`, you can place it in the root directory and the player will automatically verify and load from it.
2. **Rhythm WAVs (`RHY` folder)**
   * **Source**: You must provide 6 16-bit PCM WAVs named exactly: `2608_bd.wav`, `2608_sd.wav`, `2608_top.wav`, `2608_hh.wav`, `2608_tom.wav`, `2608_rim.wav`.
   * **Location**: Place them in a folder (e.g., `RHY/`). You **MUST specify the path manually** using `--rhythm-rom <path>`, otherwise rhythm will be silent.

## ⚡ Realtime Player (NECOPN-RT)

`NECOPN-RT/` is the realtime version: MIDI plays directly, no files written, sharing the same NECOPN.DRV driver behavior as the offline `player.py`. Its own docs: [English](NECOPN-RT/README.md) / [简体中文](NECOPN-RT/README_zh.md).

### Quick start
```bash
# GUI: loop / RHY folder / SSG-only / WAV export / zh-en
python NECOPN-RT/app.py

# CLI
python NECOPN-RT/rtplayer.py your_song.mid
python NECOPN-RT/rtplayer.py your_song.mid --core nuked1
python NECOPN-RT/rtplayer.py your_song.mid --ssg-only
python NECOPN-RT/rtplayer.py your_song.mid --export out.wav
```

### Backends
| `--core` | Chips | Measured |
|---|---|---:|
| `fmgen` (default) | full OPNA: FM + SSG + ADPCM-B + rhythm | 92.7x realtime |
| `nuked1` | one Nuked OPN2 (6 FM channels) + emu2149 + PCM rhythm | 15.3x |
| `nuked` | two Nuked OPN2 (3+3) + emu2149 + PCM rhythm | 8.3x |

The full Nuked YM2608 LLE measures **0.24x** (`-O3 -march=native`) and is kept as an offline reference only.

### Notes
- Native **55 555 Hz** (8 MHz / 144) → 4-point Catmull-Rom → **48 000 Hz**; `--clock 7987200` selects the exact PC-98 55 466 Hz.
- **No rhythm samples are bundled**: point `--rhythm-dir` at your own `RHY/` (offline `player.py` uses `--rhythm-rom`); SSG fallback drums are used when missing.
- WASAPI 256 frames: **22.0 ms** latency, **0 underrun / 0 xrun** over 3 s.
- Build the single-file EXE with `NECOPN-RT/build_exe.ps1` (the repo does not ship the 24.8 MB binary; use Releases).

More: [Performance](NECOPN-RT/docs/PERFORMANCE.md) · [Sample rate](NECOPN-RT/docs/SAMPLE_RATE.md) · [Rhythm/licensing](NECOPN-RT/docs/RHYTHM.md)

## 🚀 Quick Start

### Render to WAV (No DRV required!)
```bash
python player.py your_song.mid -o output.wav --rhythm-rom ./RHY
```

### Enable software LFO (experimental; undiscovered quirks may remain)
```bash
python player.py your_song.mid -o output.wav --rhythm-rom ./RHY --enable-lfo
```

### Tune LFO rate (slower / faster)
```bash
python player.py your_song.mid -o output.wav --enable-lfo --lfo-divisor 10
python player.py your_song.mid -o output.wav --enable-lfo --lfo-divisor 5
```

### Export to VGM (Side feature)
```bash
python player.py your_song.mid -o output.vgm --vgm
```

### 🧪 Experimental: OPN MIDI Synth (3 FM + 3 SSG, `opn-midi-synth`)
Located in `experimental/opn-midi-synth`, this takes the idea to its limit: **drop the drums, give all 6 voices to melody** — 3 FM + 3 SSG, exactly one YM2203 (OPN).

*   **How it differs from `necopn-drv-reverse-gai`**: GAI bolts SSG onto a 6-FM layout and routes dynamically at runtime, which causes allocation jitter. The OPN version treats SSG as a **fixed section**: every MIDI channel is locked to the FM group or the SSG group at load time, and allocation only happens inside that group, so a channel never jumps between FM and SSG.
*   **Voice stealing**: reuses NECOPN's "battle royale" — same-channel released slot → free slot in the group → oldest slot on the highest MIDI channel; the note is dropped if the stolen channel number is lower.
*   **SSG envelopes**: modelled on the **MID2VGM beta 5** SSG instrument table (psgtick ≈ 11.6 ms, decay2 up to 1.67 s/step), so notes sustain and ring out instead of being short plucks.
*   **Core**: fmgen OPNA ch0-2 + SSG, used as an OPN (its FM channels are register-compatible with the YM2203).
*   **Known costs**: GM MIDI is not written for 3 FM + 3 SSG, so songs with more than 6 melodic channels drop or steal notes; drums are gone entirely; SSG is a square wave, so pianos, strings and long pads sound noticeably "electronic". This is an OPN arrangement, not a NECOPN reproduction.
*   **Disclaimer**: an experimental arrangement tool, **not** the original `NECOPN.DRV` behavior.

```bash
python experimental/opn-midi-synth/opn_synth.py your_song.mid -o out.wav
python experimental/opn-midi-synth/opn_synth.py your_song.mid --list-channels
python experimental/opn-midi-synth/opn_synth.py your_song.mid --ssg-channels 3,4,5
python experimental/opn-midi-synth/opn_synth.py your_song.mid --solo ssg --duration 10 -o ssg.wav
```

See [experimental/opn-midi-synth/README_zh.md](experimental/opn-midi-synth/README_zh.md).

 ### 🧪 Experimental: NECOPN with SSG Support (`necopn-drv-reverse-gai`)
Located in the `experimental/necopn-drv-reverse-gai` directory, this is a highly experimental Python renderer based on the original NECOPN driver, but extended with **SSG (Software-Controlled Sound Generator) synthesis** to participate in MIDI playback.

*   **Inspiration**: The allocation logic is inspired by the `MID2VGM` tool, which statically binds the quietest MIDI channels (lowest CC7 peak) to SSG channels to save FM polyphony.
*   **Architecture**: Theoretically capable of providing real-time SSG responses during streaming MIDI playback.
*   **Known Limitations**: Currently highly imperfect. Because `NECOPN` relies on real-time streaming decisions (unlike offline global allocation), it cannot predict future notes. This results in the intrinsic flaw of **allocation jitter**—in dense musical passages, notes may jump erratically between FM and SSG, and SSG slots may be chaotically stolen, causing inconsistent timbres.
*   **Disclaimer**: This is an architectural extension, **not** the original behavior of `NECOPN.DRV` (which completely ignores SSG registers). Use with caution.

### 💻 CLI Arguments (Experimental)
Description 
--pure-ssg	Pure SSG Mode. Disables FM entirely. Forces all non-drum notes to share the 3 SSG channels with LRKO (Last Recently Killed Oldest) voice stealing.	

--ssg-mode {off, manual, fm_first, ssg_first}	Sets the real-time routing strategy.

off: Disabled (Default).

manual: Routes specified MIDI channels to SSG.

fm_first: Routes to SSG only when FM active voices reach the threshold.

--ssg-channels "0,1"	Comma-separated MIDI channels to be routed to SSG (Only valid in manual mode).	

--ssg-atten-db FLOAT	Global SSG volume attenuation in dB. 0.0 is full volume, positive values reduce volume, negative values increase it.	

--fm-ssg-threshold INT	FM active voice count required to trigger SSG routing in fm_first mode (Default: 4).	

```
# 1. Pure SSG mode (The 3-channel hunger games)
python player.py song.mid -o out.wav --pure-ssg --ssg-atten-db 0

# 2. Let FM handle the main load, overflow to SSG when >4 voices are active
python player.py song.mid -o out.wav --ssg-mode fm_first --fm-ssg-threshold 4

# 3. Manual routing (Assign only MIDI channel 2 to SSG)
python player.py song.mid -o out.wav --ssg-mode manual --ssg-channels 2
```

### 🎛️ The Ultimate Conclusion on the Software LFO: It Was Never Enabled
During our initial reverse engineering and listening tests, we noticed that enabling --enable-lfo caused audible phase drifting ("big-small" waveform jumps) or clipping, and that the "LFO disabled" mode perfectly matched the audio output of the NP21/W emulator. After a final cross-examination of the disassembly, we have reached the ultimate conclusion: NECOPN.DRV explicitly "sealed" the software LFO at the data structure level right during driver initialization.

🔍 Technical Evidence (seg1:0x01CD & seg3:0x0D6D)
In the initialization routine inside seg1, when the driver allocates the channel context structure, there is a very subtle instruction:

```assembly
01CD: c745180100    mov word ptr [di + 0x18], 1
The channel status word at offset 0x18 is forced to 1 (binary 0000 0001).
```
Subsequently, in the Timer-A interrupt polling routine (irq_poll -> 0x0D59) inside seg3:
```assembly
0D6D: f6471880      test byte ptr [bx + 0x18], 0x80
0D71: 7503          jne 0xd76
0D73: e98501        jmp 0xefb
```
The sole condition for the LFO to execute is if Bit 7 at offset 0x18 is 1. Because the initial value is 1, Bit 7 remains 0. Therefore, every time the interrupt hits this point, it executes jmp 0xefb, completely bypassing all LFO calculation logic. The seal is only broken if the driver receives a specific private SysEx that triggers the or [bx+0x18], 0x83 instruction.

### 🎛️The Historical Truth & Listening Verification:

Hardware Default is No-LFO: A real PC-98 playing MIDI under Windows 95 outputs pure hardware ADSR envelopes. Our Python player defaults to LFO-disabled (--enable-lfo kept off), which perfectly aligns 1:1 with the authentic hardware behavior.

Why the "Weird Sound" Happened: Forcing --enable-lfo in Python is equivalent to awakening a beast abandoned by NEC. The "big-small" audio artifacts are actually caused by an unoptimized Sawtooth LFO wave combined with unreset phase accumulators, resulting in phase drift.

Why It Was Abandoned: It is highly likely that the scheduling latency under Win16 protected mode could not provide a stable 836Hz clock, or the NEC engineers simply found the LFO algorithm sounded terrible, ultimately disabling it at the code level. If you want to use it, please add the --enable-lfo parameter.

### 🎛️ LFO Tuning (Advanced)
The software LFO is disabled by default. Once enabled with --enable-lfo, the following parameters are available for fine-tuning:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--lfo-divisor N` | 7 | LFO advance frequency = 836.5 / N Hz. 1 = Timer-A rate (~16 Hz, "warble"-y, old behavior). 7 ≈ 119 Hz, matching the Win95 MIDI callback rate. 10–14 = slower / more subtle.|
| `--lfo-rate-scale F` | 1.0 | Multiplies rate_word per voice. Prefer --lfo-divisor; use only when a specific voice needs a different LFO rate. |
| `--lfo-depth-scale F` | 1.0 | Multiplies trem amplitude (tremolo depth). 0.5 halves the depth — useful for patches whose LFO still swings too far after the fixes. |
| `--lfo-jitter-us U` | 6.0 | Adds uniform ±U µs jitter to Timer-A tick timing. Currently ineffective after the fixes; retained for diagnostic purposes. |

**Recommended workflow:** Start with `--enable-lfo` alone (default `lfo_divisor = 7`). If vibrato/tremolo still sounds too fast, try `--lfo-divisor 10`; if it feels too static, try `--lfo-divisor 5`. Only reach for `--lfo-depth-scale` when a specific voice has excessive amplitude swing.

### 🗂️ Banks & Presets (Byproducts)
In the folder, you can find three WOPN banks and an OPM preset bank:

#### WOPN Banks (for libOPNMIDI)
- **NECOPN_Dynamic.wopn**: *Highly Recommended for libOPNMIDI / Ideal State* Generated by `necopn_to_wopn.py` using a dynamic snapshot method (with Wohlstand's Carrier Maximization algorithm). It captures the exact dynamic `OPMASK` harmonic structures calculated by the original driver, making the timbres **extremely close to the real hardware state**. Since a static WOPN cannot fully replicate the original 6-channel dynamic environment, slight differences remain. Think of these as the **"ideal state"** of the instruments. The volume is perfectly normalized.
- **NECOPN_GM-fixed.wopn**: *Ultimate Tuned Version* A heavily tweaked version meticulously tuned by **Wohlstand**. Features include: forcing OPNA (8MHz) clock frequency for accurate cymbals, adding a complete percussion kit, and fixing the specific `+12` octave offset for *Synth Bass 2*.
- **NECOPN_GM.wopn**: The raw byproduct generated by `extract_bank.py` from the original hardcoded data. Strictly for visual inspection (e.g., in OPN2-BE), NOT recommended for direct playback.

#### OPM Presets (for VOPM / FB-02)
- **NECOPN_GM.opm**: A direct translation of the dynamic snapshots into the `.opm` text format for OPM (YM2151) synthesizers. This is a full-circle experiment: translating OPN2/OPNA registers back to their OPM ancestors. , this script meticulously maps the exact envelope logic and operator swaps. It perfectly retains the original NECOPN timbre DNA and allows you to play these classic PC-98 sounds on hardware-accurate OPM engines with smooth hardware LFO. *(Note: VOPM is a VSTi, not a MIDI player. You'll need a DAW to fully utilize this preset bank.)*

#### Script Generation
To generate these files without the original `.DRV`:
- Place `extract_bank.py`, `necopn_to_wopn.py`, and `fixed_bank.py` in the same folder.
- Run: `python extract_bank.py` (generates raw WOPN)
- Run: `python necopn_to_wopn.py` (generates Dynamic WOPN and OPM preset)

### ⚠️ Known Issues
- **Software LFO:** The previously audible electromagnetic noise and excessive warble have been fixed. The LFO is currently functional but disabled by default. For details, please see [🎛️ The Ultimate Conclusion on the Software LFO: It Was Never Enabled].
- **VGM Export Issue:** Exported .vgm files may fail to play in strict players like foobar2000. This is a side-feature and is currently deprioritized.

### 🔬 Technical Details
- **Voice Decoding:** Reads 64-byte records directly from the driver, mapping hardware values via the cs:0x1239 inversion table. Verified byte-for-byte against the original NECOPN.DRV.
- **Velocity & Envelope:** Strictly follows the operator mask logic at 0x0BED and hardware SR/DR mechanisms.
- **Timer Tick:** Driven by the 836.5 Hz Timer-A interrupt, precisely aligned with assembly instructions. Envelope state machine runs at this rate.

### Software LFO:
- **Advance rate:** ~120 Hz, gated by `lfo_divisor = 7 (836.5 / 7)`. This matches the Win95 MIDI callback rate rather than the Timer-A rate — the driver polls irq_poll at MIDI event granularity, so LFO updates decimated relative to Timer-A.
- **TL calculation:** Follows seg3:0x0F92-0x0FC8 byte-for-byte, including the unsigned-byte handling of the intermediate t1 that was the root cause of the old "electromagnetic noise".
- **Shape 1/3 phase:** Counter uses `eff_counter = lfo_counter // lfo_divisor` so square/noise LFO frequencies are preserved despite the decimation.

### 🙏 Credits & Disclaimer
- Massive thanks to **Wohlstand** for his guidance on WOPN, core comparison, manual WOPN bank tuning, and the cross-platform CMake/Linux build fixes.
- Thanks to **cisc (fmgen),** the NP21/W team, and **Aaron Giles (ymfm)**.
- This project is for educational and preservation purposes only. All rights belong to NEC and YAMAHA.
