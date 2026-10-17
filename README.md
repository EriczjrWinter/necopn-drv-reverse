# NECOPN.DRV Reverse Engineering & Renderer (English)

This project is a deep reverse engineering and high-fidelity reimplementation of the `NECOPN.DRV` MIDI driver, exclusively used in NEC PC-9821 running Windows 95. By combining Python with a custom C++ core, it aims to reproduce the exact sonic behavior and voice allocation logic of the original hardware.

> **🎯 Status**: Basic complete reproduction of the original hardware (Pitch, Envelope, Voice allocation, and Rhythm) is achieved. (Excluding the software LFO feature, which has known limitations).

## 🏗️ Architecture & Core Notes

**1. Software Mapping of "Dual Chips"**
Although the PC-98 hardware features a YM2608 (OPNA), the official driver strictly limits itself to CH0-CH2 on each chip to avoid hardware quirks. Therefore, this project maps the **2 core instances × 3 channels per core (6 voices total)** software architecture, perfectly replicating the driver's logic rather than the physical hardware limits.

**2. Why `fmgen` instead of `ymfm`?**
We initially used `ymfm`. However, during waveform comparisons against real hardware recordings, `ymfm` showed slight deviations in sustained notes (e.g., low SR values) compared to our expectations. To achieve the most authentic hardware listening experience, the backend core was swapped to `fmgen` (used by the NP21/W emulator).
*Note: For API compatibility, the Python wrapper class remains named `YmfmCore`, but dynamically loads `necopna_fmgen.dll`.*

## 📦 Required Assets
**Due to copyright restrictions, this repo does NOT distribute any original binaries or samples.**

1. **`NECOPN.DRV`**
   * **Source**: Extract from a Japanese PC-98 Windows 95 image.
   * **Location**: **Place `NECOPN.DRV` directly in the root directory** (where `player.py` is located). Alternatively, use `--drv <path>`.
2. **Rhythm WAVs (`RHY` folder)**
   * **Source**: You must provide 6 16-bit PCM WAVs named exactly: `2608_bd.wav`, `2608_sd.wav`, `2608_top.wav`, `2608_hh.wav`, `2608_tom.wav`, `2608_rim.wav`.
   * **Location**: Place them in a folder (e.g., `RHY/`). You **MUST specify the path manually** using `--rhythm-rom <path>`, otherwise rhythm will be silent.

## 🚀 Quick Start
```bash
# Render to WAV
python player.py example.mid -o output.wav --rhythm-rom ./RHY

# Export to VGM (Side feature, may have compatibility issues)
python player.py example.mid -o output.vgm --vgm
🗂️ About necopn_reverse/ (Byproducts)
NECOPN_GM.wopn: A byproduct WOPN bank generated from our reverse engineering. NOTE: It is strictly for visual inspection (e.g., in OPN2-BE) and is NOT used by the Python player. The player reads the raw records directly from the .DRV file.

⚠️ Known Issues
Software LFO Noise: Enabling --enable-lfo may cause slight background noise due to high-frequency phase resets. It is disabled by default for clean audio.

VGM Export Issue: Exported .vgm files may fail to play in strict players like foobar2000. This is a side-feature and is currently deprioritized.

🔬 Technical Details
Voice Decoding: Reads 64-byte records directly from the DRV, mapping hardware values via the cs:0x1239 inversion table.

Velocity & Envelope: Strictly follows the operator mask logic at 0x0BED and hardware SR/DR mechanisms.

Timer Tick: Driven by the 836.5 Hz Timer-A interrupt, precisely aligned with assembly instructions.

🙏 Credits & Disclaimer
Thanks to Wohlstand for his guidance on WOPN and core comparison.

Thanks to cisc (fmgen), the NP21/W team, and Aaron Giles (ymfm - an excellent emulator framework, swapped only due to differing project priorities).

This project is for educational and preservation purposes only. All rights belong to NEC and YAMAHA.