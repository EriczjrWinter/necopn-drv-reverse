> 中文版见 [README_zh.md](README_zh.md).
# NECOPN Player (Driver Dynamic-Behavior Reproduction)

Reproduces the **dynamic behavior** of `NECOPN.DRV` (NEC "MIDI:NEC Synthesizer Driver" 4.05.00.0129) as a standalone player: MIDI in → OPNA register stream → WAV / VGM out.

Because libOPNMIDI only consumes WOPN voice banks (static parameters), the driver's **behavior** — velocity curves, channel stealing, software envelope/LFO, drum routing — can't be turned into a plugin for it. So, as requested, it's reproduced separately as a player. The emulation core is abstracted behind an interface, defaulting to **ymfm's OPNA (YM2608) core**.

## Quick Start

```bat
cd necopn_player
python selftest.py                                  :: 56 self-checks (cross-checked against reverse-engineering findings)
python core\smoke_test.py                           :: 24 core smoke tests
python player.py testdata\demo_rich.mid -o out.wav  :: produce WAV (55555 Hz)
python player.py testdata\demo_rich.mid -o out.vgm  :: produce VGM (playable with foobar2000 + vgmplay)
python player.py testdata\demo_rich.mid --trace t.txt --core null   :: registers only
```

| Option | Description |
|---|---|
| `--mode necopn` | **Real driver**: 2 OPNA chips, each using only ch0-2 (6 voices total), drums on chip 1's rhythm |
| `--mode full` | **Hypothetical full-channel NECOPN driver**: each OPNA uses all 6 voices, `--cores N` sets the number of cores |
| `--cores N` | Number of cores (default necopn=2, full=3, i.e. 18 voices) |
| `--core ymfm\|null\|auto` | Emulation core; null outputs registers only (runs without any DLL) |
| `--no-lfo` | Disable the software envelope/LFO for easy A/B comparison |
| `--rhythm-rom FILE` | **Load the native YM2608 rhythm ROM** (8192-byte ADPCM-A dump; MAME's `ym2608_adpcm_rom.bin` works directly); when unspecified, uses built-in synthesized percussion |
| `--clock` | OPNA clock, default 8000000 (matches the driver's measured FNUM table) |

## Directory

| File | Purpose |
|---|---|
| `necopn_core.py` | **Emulation core abstraction** (`ChipCore`) + ymfm DLL wrapper + Null core + VGM recording |
| `necopn_driver.py` | Driver behavior reproduction (voice loading / velocity / pitch / drums / allocation / stealing / LFO) |
| `smf.py` | Standard MIDI file parser |
| `player.py` | CLI player (render loop, WAV/VGM/trace output) |
| `selftest.py` | 56 self-checks, cross-checking the reverse-engineering findings item by item (14 of which compare directly against `NECOPN.DRV` bytes) |
| `testdata/demo.mid` `demo_rich.mid` | Test MIDI files (monophonic + drums / chords + drums + pitch bend) |
| `core/necopna_ymfm.dll` | **ymfm OPNA core** (already built, 24/24 smoke tests passing) |
| `core/vendor/ymfm/` `ymfm_SOURCE.txt` | ymfm source archive (commit 81aec25) and provenance record |
| `core/smoke_test.py` | Core smoke tests (FM sounding / dual chip / 6 rhythm instruments / percussion ROM load-unload) |
| `tools/wav2rhythmrom.py` | **WAV drum samples → YM2608 rhythm ROM** (8KB ADPCM-A packer) |
| `out/ym2608_rhythm_drum_samples.bin` | ROM packed from user drum samples with the above tool (8192 bytes) |

Output formats: **WAV fixed at 55555 Hz** (= clock/144; ymfm's native clock/8 is weighted-decimated inside the DLL); **VGM writes one file per chip** (`.vgm`, `.vgm.chip1`…), with commands 0x56/0x57 for a single chip, directly playable with foobar2000's vgmplay.

## Repository Layout

- `necopn_player/` — Python reimplementation (this README)
- `necopn_reverse/` — reverse-engineering tools, WOPN voice bank, disassembly listings
- `audio/` — real-hardware vs Python render comparison
- `docs/` — full report and audit (`REPORT.md`, `AUDIT.md`)

## Help Wanted

The only remaining problem is the **fixed-position track loss** in the Python render. 
All static analysis is complete (56/56 self-tests, behavior documented with 
disassembly addresses), but we have no way to capture a **register-level I/O trace** 
from the real driver on our current hardware.

What would unblock this:

1. **A register trace from the real driver.** MAME's PC-9821 driver supports 
   `trace 188,18a,18c,18e,1,0` under `-debug`. NP2's debug build does **not** have 
   I/O trace (confirmed). A single note's worth of writes (30–50) is enough.
2. **A working PC-98 MIDI→WAV renderer.** Generic OPL3 tools (Audio Composer with 
   `OPLERO82.DLL`) are **not** OPNA-compatible. Japanese-native tools like **htsfms** 
   would work if you have a copy.
3. **A per-second RMS comparison** between a real-hardware recording and Python's 
   render of the same MIDI. `examples/rms_diff.py` does this — you just need to 
   provide a WAV.

If you have any of the above, please open an issue with the trace/audio and the 
corresponding MIDI segment.


## Audio Comparison

Two recordings of the same track from **Crystal Rinal - Ouma no Meikyuu -** 
(1994, D.O., PC-98) are provided in `audio/`:

- `Crystal_Rinal_CR028_real_pc9821_rom.mp3` — original NECOPN.DRV on emulated 
  PC-9821
- `Crystal_Rinal_CR028_python_render_rom.mp3` — this project's Python 
  reimplementation

The Python render loses entire tracks at fixed positions. See 
`audio/README.md` for details.


## Emulation Core Interface

`ChipCore` deliberately exposes only 5 methods, so swapping cores requires no changes to the driver layer:

```python
class ChipCore:
    def write(self, port, reg, val)   # port 0=0x188/0x18A, 1=0x18C/0x18E
    def read(self, port, reg) -> int
    def sample_rate(self) -> int
    def generate(self, nframes) -> bytes   # stereo interleaved int16
    def reset(self)
```

- `YmfmCore`: `core/necopna_ymfm.dll` (ymfm `ym2608` core, preferred)
- `NullCore`: register black hole, for producing traces / VGM / running tests
- To swap in Nuked-OPNA or MAME ym2608, just write another subclass; `YmfmPool` already hides the difference of "one handle carrying 2 chips", so multiple cores simply means opening several `ChipCore`s.

## Reproduced Dynamic Behaviors (all backed by disassembly evidence)

### 1. Velocity → TL (`seg3:0x0BED`, table `cs:0x0C55`)
MIDI velocity first passes through a 128-entry table at DGROUP:0x2E: `level = 0 (v<2) | 0x40 + v/2`.
Then, based on the **algorithm**, attenuation is added to the "carrier operators":

```
TL[i] = clamp8(mask[ALG][i] & (0x7F - level)) + (0x7F - rec[0x1B+i])
```

`mask` is an 8-algorithm × 4-operator table of 0x00/0x7F (ALG0-3 only op4, ALG4=op2/op4,
ALG5=op2/3/4, ALG6=op2/3/4, ALG7=all). In other words, **only carriers receive velocity; modulator volume is fixed**.
Velocity affects only a 0x3F (~47 dB) range — not the full-scale curve of libOPNMIDI.

### 2. Channel Allocation and Voice Stealing (`seg1:0x0058`)
4 rounds of search over 6 FM voices:
1. Active voice on the same MIDI channel with `note==0` → key-off first, then reuse (no voice reload)
2. Free voice → use it
3. Active voice on another MIDI channel with `note==0` → use it
4. Stealing: take the voice with the **highest occupied MIDI channel number** (earliest started among equals);
   if it has "lower priority" than itself (smaller MIDI channel number) → **abandon the note**

Conclusion: **lower MIDI channel numbers have higher priority**; 16-channel polyphony on the real hardware has only 6 voices,
so the 7th note steals the oldest voice on the same channel, while ch3 at full load can't steal from ch0-5.
Additionally, a program change (`seg1:0x0478`) first kills all notes on that MIDI channel.

### 3. Transposition FMOCTAVE (`seg1:0x0232`, resource 0x7510)
`note += FMOCTAVE[program]` (signed semitones), then clamped to 0x7F using an **unsigned** comparison.
Of the 128 entries in this bank only 3 values appear: `-12` (122 entries), `-24` (5 entries), `-60` (1 entry,
program 125 = Gunshot). This is the purpose of that "128-entry table (full of 0xF4)".

### 4. Pitch and Pitch Bend (`seg3:0x0C75`, `seg1:0x03B2`)
```
FNUM = FNUMTable[note%12] + bendIncrementTable[note%12] * bend255 / 255     block = note/12
```
MIDI pitch bend 14-bit → `step = 8191/range + 1` (range = DGROUP:0xE2, default 2 semitones),
`note' = note + (bend14 + base)/step - range`, fractional part converted to 0-255 linear.
When writing FNUM, 0xA4 goes first, then 0xA0 (OPN latch order).

### 5. Drums (`seg3:0x0CCB`, table DGROUP:0x8B+note)
MIDI ch9/ch15, note range 35..81:

| GM Drum | NECOPN |
|---|---|
| 35/36 Bass Drum | BD |
| 38/40 Snare | SD |
| 42/44/46 Hi-Hat | Hi-Hat |
| 41/43/45/47/48/50 Tom | Tom |
| 37 Side Stick | Rim Shot |
| 49/51/52/55/57/59 Cymbal | Top Cymbal |
| **39 Hand Clap, 53/54/56/58, 60-81** | **Muted (0x7F)** |

Level = `level>>2` (0..31), write `0x18+instrument = 0xC0|level`, then `0x10 = 1<<instrument` to trigger.
**There is no drum release** (`seg1:0x2CC` is a direct `ret`).

### 6. Software Envelope + LFO (`seg3:0x0D59`, Timer-A tick ≈ 836.5 Hz)
This is the "dynamic" part entirely absent from WOPN: each sounding channel runs a software LFO
that simultaneously modulates **FNUM (vibrato)** and **TL (tremolo/volume)**:

- LFO waveform = voice record 0x0A: 0=triangle 1=square 2=triangle (with direction) 3=random
- Rate = 16-bit word at record 0x14 (period `0xFFFF/rate` ticks)
- Start phase = record 0x0F (0=run along with the global counter, 1=restart from 0)
- Counter `ws+0x19` increments at the end of each tick that completes a step (`seg3:0x0EFB`) — square/random waveforms advance phase by it
- Vibrato depth = record 0x1A × record 0x24, `fnum += (LFO*depth/0x7FFF) * fnum/0x7FF`
- TL depth = per operator `(record 0x2F+i & 0xF) * record 0x1F / 15`
  `TL = clamp8(TL_base + TL_base*(depth*LFO/0x7FFF)/0x7F)` (8-bit carry semantics reproduced verbatim)

Timer-A = 0x0E3 (reg 0x26; reg 0x25 never written = 0) → `(1024-0xE3)*12/8MHz` = 1.1955 ms.

### 7. Other
- Among MIDI CCs, **only CC≥0x7B is effective** (All Notes Off etc. → key-off everything);
  volume/pan CCs are ignored, loudness determined solely by velocity
- The RL (pan) bits in the voice record are written into 0xB0 and ignored by the hardware, so **pan is always L+R centered** (consistent with WOPN)
- key-on slot mask = low 4 bits of record 0x05 (all 128 entries in this bank are 0x0F, i.e. all four operators on)
- **When the transposed pitch ≥ 0x5C(92), key-on is skipped** (`seg1:0x0039` gate): no FNUM written, no 0x28 written,
  velocity still written → the note doesn't sound but the voice is occupied (notes wrapping around to 0x7F in the low range are thus silent)
- Drum channel checks velocity first (`seg1:0x055F`): a note-on with velocity 0 causes no writes at all (`ret 8`, no drum hit)
- AR/DR/SR/SL/RR/TL in the record are **stored inverted** (`seg3:0x1239` inversion table):
  `AR = 0x1F - rec`, `SL/RR = 0x0F - rec`, `TL = 0x7F - rec`

## Multi-Core: Hypothetical Full-Channel NECOPN Driver

Real hardware is dual OPNA, each using only ch0-2 (6 voices). `--mode full --cores N` uses each chip
as "all 6 voices", yielding `6N` voices (3 cores = 18 voices ≈ full 16-MIDI-channel polyphony),
to answer "what if NECOPN had used all channels back then". The allocation algorithm, velocity, and LFO behavior are unchanged,
only the pool of candidate voices grows.

## Native Percussion ROM (YM2608 ADPCM-A)

The real chip's 6 rhythm voices (BD/SD/TopCym/HH/Tom/Rim) live in an **8KB ROM inside the chip**,
which ymfm doesn't include. The wrapper defaults to a built-in **synthesized** percussion ROM (it makes sound and is testable, but isn't the factory timbre).
When you have the original dump, swap it in with one command:

```bat
python player.py song.mid -o song.wav --rhythm-rom ym2608_adpcm_rom.bin
```

- Requirement: ≥ 8192 bytes of raw dump (exactly 8192 is best; larger will take the first 8KB with a warning).
  MAME's `ym2608_adpcm_rom.bin` uses this layout and works directly.
- How it works: the dump is copied into the core handle via `nopna_set_rhythm_rom()`, and `ymfm_external_read(ACCESS_ADPCM_A)`
  reads from it instead; both OPNA chips are affected. The six instruments' start/end addresses preset by `ym2608::reset()`
  (`ymfm_opn.cpp:1002-1007`) are the factory ROM's fixed layout, so the dump needs no rearrangement.
- `nopna_clear_rhythm_rom()` reverts to the built-in synthesis (Python side: `YmfmCore.clear_rhythm_rom()`),
  for A/B comparison. Smoke tests (f1)-(f7) verify the whole load-decode-clear chain with test data.

Note: ymfm's FM envelope counter doesn't reset to zero on reset, and ADPCM-A sample fetching is clocked off it,
so two renders at different "phases" will naturally differ in scattered tail samples — for ROM A/B comparison,
render each once with a freshly created handle (which is what the smoke tests do).

### When You Only Have WAV Drum Samples (No Dump)

The real chip ROM layout is **hard-wired**, with six instruments occupying fixed regions, and **playback rates are not all the same**:

| Slot | Instrument | Address | Bytes | Channel | Playback rate (8MHz clock) | Slot duration |
|---|---|---|---|---|---|---|
| 0 | bd | `0x0000-0x01BF` | 448 | ch0 | 18518.52 Hz (clock/432) | 48.4 ms |
| 1 | sd | `0x01C0-0x043F` | 640 | ch1 | 18518.52 Hz | 69.1 ms |
| 2 | top | `0x0440-0x1B7F` | 5952 | ch2 | 18518.52 Hz | 642.8 ms |
| 3 | hh | `0x1B80-0x1CFF` | 384 | ch3 | 18518.52 Hz | 41.5 ms |
| 4 | tom | `0x1D00-0x1F7F` | 640 | ch4 | **9259.26 Hz (clock/864)** | 138.2 ms |
| 5 | rim | `0x1F80-0x1FFF` | 128 | ch5 | **9259.26 Hz** | 27.7 ms |

tom/rim on ch4/ch5 have **only half the playback rate** of the other channels (`ymfm_opn.cpp:1397-1400`:
ADPCM-A advances one step every 3 FM clocks, but ch4/5 advance only every two steps), so the same byte count holds twice the sound length.
The rates above were verified against this project's core by **measurement** (inferred from sounding duration with full-slot data, error < 0.1%).

Use `tools/wav2rhythmrom.py` to pack WAVs into a ROM, then use `--rhythm-rom` as usual:

```bat
python tools\wav2rhythmrom.py D:\galgame\drum_samples -o out\my_rhythm.bin
python player.py song.mid -o song.wav --rhythm-rom out\my_rhythm.bin
```

- **Instrument recognition**: by filename keyword (`bd/bass/kick`, `sd/snare`, `top/cym`, `hh/hat`, `tom`,
  `rim/rym`) automatically; unrecognized ones can be specified explicitly with `--bd xxx.wav --sd ...`.
- **Resampling**: pitch-preserving resample according to that slot's playback rate, so 44.1kHz WAVs work directly;
  silence at head/tail below the ADPCM quantization noise floor is trimmed (keeping it only wastes slot space).
- **Encoding**: YM2608 ADPCM-A 4-bit (the encoder is the inverse of the decoder at `ymfm_adpcm.cpp:151-213`);
  by default the loudest sample is pushed to ADPCM full scale ±2047, preserving relative loudness between instruments (`--gain-db` adjustable).
- **Overflowing tails**: slot length is fixed. Default `--fit fade` truncates the tail with an 8 ms fade-out
  (`--fade-ms` adjustable); `--fit speed` speeds the whole thing up to fit (raises pitch, use with caution).
  The packing report prints each instrument's "sample count / slot capacity", truncation ratio, and encoding SNR, for item-by-item verification.


## Known Uncertainties

1. **The exact algorithm of LFO random waveform (shape 3)** is reproduced from `seg3:0x10AF`,
   but the semantics of the multiplier 0x383 and the initial value can only be inferred from the assembly.
2. **The "abandon note" condition in stealing round 4** (`seg1:0x0181`) is implemented as written in the assembly;
   it manifests as high MIDI channels potentially dropping notes when fully loaded.
3. The fractional part of pitch bend uses `*255/step`, whereas the original code goes through a C runtime helper routine;
   it may differ from `*256/step` by 1/255 semitone.