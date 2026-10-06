# NECOPN.DRV Reverse-Engineering Report — Phase 1: OPNA Voice Bank Extraction

Object: `NECOPN.DRV` (30,096 bytes, SHA256 `cae5234a9ca88ac51495a065e8b85e2a7e7a11d98d213b95fa056e10b542dae8`)  
Artifacts: `out/NECOPN_GM.wopn` (WOPN v2, chip_type=OPNA, 128 GM voices) + `out/voices_dump.txt` (full dump)

---

## 1. File Identity

- Windows 3.x/95 **NE-format 16-bit DLL**, NE header @0x90, 4 segments:
  - seg1 code 0x1106 @0x240 (initialization: LibMain=seg1:0x0B6C, MYFAIRLADY=seg1:0x063E, ___EXPORTEDSTUB=seg1:0x0F8C)
  - seg2 code 0x0054 @0x1400 (WEP=seg2:0x0000)
  - seg3 code 0x1523 @0x3D60 (runtime main body: DRIVERPROC=seg3:0x0000, MODMESSAGE=seg3:0x0D23, voice loading/register output/timer)
  - seg4 data 0x2894 @0x1480 (port table, resource name strings, DGROUP initialized data)
- Version resource: `necopn.drv` 4.05.00.0129, Copyright NEC 1997; device name **"MIDI:NEC Synthesizer Driver"**
- Exports: WEP(1), DRIVERPROC(2), MODMESSAGE(3), MYFAIRLADY(4), ___EXPORTEDSTUB(5); imports KERNEL/USER/MMSYSTEM
- DOS stub carries a `Cert DX2 <hash>` watermark; MZ header fields are unreasonable, relocation count words are all 0, and the real relocation table is **after each segment's data** (count word at the start of the table) — **this file has been rearranged by a post-processing tool**, not a standard NE load flow (does not affect this analysis; relocations were parsed according to its format).

## 2. Custom Resources (file tail 0x5320–0x7590)

| File offset | Size | Content | Evidence |
|---|---|---|---|
| 0x5320 | 0x30 | Pascal string "NEC ｼﾝｾｻｲｻﾞ ﾄﾞﾗｲﾊﾞ" | SJIS decoding |
| 0x5350 | 0x1C0 | VS_VERSION_INFO | Embedded strings |
| 0x5510 | 0x2000 | **FM voice bank: 128 × 64 bytes** | seg3:0x0776 loader, `cmp ax,0x2000` size check, `rep movsw cx=0x1000` copy to DS:0x6F4 |
| 0x7510 | 0x0080 | 128-entry table (mostly 0xF4, sparse entries) | seg3:0x02E0 loader, `cmp ax,0x80` check, copy to DS:0x656 (FMOCTAVE) |

Resource type/name strings (seg4, FindResource arguments): `FMPARA`@ds:0xF9 + `VoicePara`@ds:0x100; `FMOCTAVE`@ds:0x18 + `VoiceOctave`@ds:0x21.

## 3. 64-byte Voice Record → OPN Register Mapping (core result)

> **⚠️ Phase 2 correction (important)**: the TL positions and value direction in this section are wrong and have been corrected by §7.
> In the record, AR/DR/SR/SL/RR/TL are **stored inverted** (`cs:0x1239` inversion table: `AR=0x1F-rec`,
> `SL/RR=0x0F-rec`, `TL=0x7F-rec`); the real source of TL is record **0x1B-0x1E**;
> record 0x05/0x0A/0x0F/0x14 are key-on slot mask / LFO waveform / LFO start phase / LFO rate.
> See §7.1 for details; `extract_bank.py` has been updated to re-emit WOPN according to the corrected mapping.

Load path: seg1:0x1CE `voice = DS:0x6F4 + prog*64` → seg3:0x042A → dispatcher seg3:0x0A90 (cmd=0x16) → **seg3:0x0BB9**:
copies the first 0x33=51 bytes of the record to each channel work area `DS:0x26F4 + ch*0x34`, then calls **seg3:0x1430** to write registers.
Field reader seg3:0x11F8 adds +1 to accesses with **logical index >0x14** (0x14 is a runtime field and is skipped).
Group writers seg3:0x126D/0x12DE pack two fields into one register; operator slot offset table `cs:0x142C = [0x00,0x08,0x04,0x0C]`.

**Record field table (offset within record; op0..3 correspond to slot offsets +0/+8/+4/+C):**

| Offset | Meaning | Packed destination |
|---|---|---|
| 0x00 | RL(pan)<<6 \| FB<<3 \| ALG | unchanged → 0x20+ch group (all 128 entries have legal FB/ALG values) |
| 0x01–0x04 | AR[4] | (KS<<6)\|AR → 0x50 group |
| 0x05 / 0x0A / 0x0F / 0x14 | TL[0..3] (may be >0x7F, clamped to 0x7F at runtime) | 0x40 group (writer seg3:0x14A6) |
| 0x06–0x09 | DR[4] (bit7=AM, all 0 in this bank) | AM<<7\|DR → 0x60 group |
| 0x0B–0x0E | SR[4] | 0x70 group |
| 0x10–0x13 | SL[4] | (SL<<4)\|RR → 0x80 group |
| 0x14 | (runtime field; used as TL[3] source; many records >0x7F, e.g. 0xAD) | |
| 0x16–0x19 | RR[4] (logical 0x15–0x18) | same as above |
| 0x20–0x23 | KS[4] (logical 0x1F–0x22) | 0x50 group |
| 0x25–0x28 | ML[4] (logical 0x24–0x27) | (DT<<4)\|ML → 0x30 group |
| 0x2A–0x2D | DT[4] (logical 0x29–0x2C) | 0x30 group |
| 0x1A, 0x1F, 0x2F–0x32 | scaling knobs (seg3:0x0F16 multiply/divide, defaults ≈ pass-through) | runtime modulation |
| 0x15, 0x1B–0x1E, 0x29 | no read observed (reserved) | |
| 0x33–0x3F | not copied, padding | |

## 4. Hardware Layer

- Port table seg4:0x10: `0x0188 / 0x018A / 0x018C / 0x018E`
- Write register seg3:0x1192: busy-wait (read bit 7 of 0x188) → write 0x188 → `out 0x5F` ×2 wait → write 0x18A
- **Dual OPNA**: FM channels 0–2 use 0x188/0x18A, channels 3–5 use 0x18C/0x18E (in seg3:0x14A6, 0x147F, 0x126D `ch>=3 → offset -3 → switch port pair`)
- Clock: **8.000 MHz**. Pitch table `cs:0x14F4` (12 semitone FNUM: 617,654,692,734,777,824,873,924,979,1038,1099,1165) matches 8MHz exactly per `FNUM = f×144×2^17/clk` (A4→1038=1038)
- Pitch bend: `cs:0x150C` per-semitone increment table, pitch bend 0–255 linear interpolation (seg3:0x0C75)
- Drums: cmd 0x24 → **YM2608 rhythm channel** (0x18+instrument = 0xC0|level, 0x10 enable bits) — samples are in onboard ROM, not in this file
- Timing: OPNA Timer-A interrupt (0x27 register acknowledge, seg3:0x100C; IRQ service entry polling near seg3:0x11F8)

## 5. Known Unsolved (Phase 2 material)

- MIDI volume/velocity → TL curve (cmd 0x1F, table `cs:0xC55` is an 8×4 mask of 0x00/0x7F, semantics TBD)
- Complete routing of ch10 drums (6 rhythm instruments ↔ MIDI drum note mapping)
- Exact purpose of the FMOCTAVE table (DS:0x656, 128B)
- 128-entry table at 0x7510 (mostly 0xF4, suspected program number/drum mapping)
- `MYFAIRLADY` (seg1:0x063E, cmp 0x67 branch) private API
- Voice rotation/stealing strategy, SysEx handling, initialization register sequence (seg1)

## 6. Conversion Implementation

`extract_bank.py`: parse record → output per WOPN v2 ("WOPN2-B2NK\0", chip=OPNA). Key points:
- WOPN operator order = chip offset +0/+4/+8/+C (libOPNMIDI `0x30 + d*0x10 + op*4`), so `wopn_op = [f0, f2, f1, f3]`
- fbalg = record 0x00 with pan bits cleared (pan all 0=center in this bank)
- In v2, all-zero delay would be judged as blank voice → use AR/RR to estimate nonzero millisecond values
- 69 places with TL>0x7F are all silent operators clamped at runtime by the driver; output also uses 0x7F
- Verification: `verify_wopn.py` reads back according to wopn_file.c load logic, all 128 voices' fields within valid ranges; prog0 checked byte-for-byte against the original record

---

## 7. Phase 2: Driver Dynamic Behavior (seg1 MIDI event layer + seg3 runtime)

> Deliverables: `../necopn_player/` (standalone player + ymfm OPNA core + multi-core emulation).
> This section is the authoritative basis for behavior reproduction; all conclusions have disassembly addresses; a complete list is also in
> `../necopn_player/README.md`. Full disassembly listings: `out/seg1_listing.txt`, `out/seg3_listing.txt`.

### 7.1 Field Mapping Correction: Inversion Table `cs:0x1239` + Real TL Location

The `seg3:0x11F8` field reader performs one transformation after reading a byte field:

```
121B  mov al, [bx+di]                  ; work-area byte
121D  cmp byte ptr cs:[bx+0x1239], 0   ; inversion table[index]
1223  je  return as-is
1225  mov bl, cs:[bx+0x1239]
122A  sub bl, al                       ; return (table value - stored value)
```

Inversion table (index = logical index; when >0x14 it has already been +1 to skip the 16-bit runtime field):

| Index | 0x00 | 0x01-04 | 0x05 | 0x06-09 | 0x0A | 0x0B-0E | 0x0F | 0x10-13 | 0x14 | 0x15 | 0x16-19 | 0x1A | 0x1B-1E | 0x1F+ |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Table value | 0 | 0x1F | 0 | 0x1F | 0 | 0x1F | 0 | 0x0F | 0 | 0 | 0x0F | 0 | **0x7F** | 0 |
| Meaning | FB/ALG | AR | key-on slot mask | DR | LFO waveform | SR | LFO start phase | SL | LFO rate (word) | — | RR | vibrato depth A | **TL** | KS/ML/DT etc. |

Therefore: **`AR = 0x1F-rec`, `DR = 0x1F-rec`, `SR = 0x1F-rec`, `SL = 0x0F-rec`,
`RR = 0x0F-rec`, `TL = 0x7F-rec`**; KS/ML/DT/key-on mask etc. are unchanged.

**The real source of TL is record 0x1B-0x1E** (logical 0x1A-0x1D), read by `seg3:0x0BED` (cmd 0x1F
velocity path) and handed to TL writer `seg3:0x14A6`. The original table in §3 treating 0x05/0x0A/0x0F/0x14
as TL is wrong — those four fields are key-on slot mask, LFO waveform, LFO start phase,
and LFO rate word. Value-range evidence: among all 512 operators in the bank, **69 are >0x7F** (max 0xCF),
while 0x1B-0x1E after inversion fall exactly in the normal TL range 0..50.

After correction, voice#0's envelope is AR=29/29/29/30, SL=3/3/3/5, RR=14/11/11/15,
TL=33/47/35/0 (piano-like), while the old mapping gave AR=2/2/2/1, SL=12, RR=1, TL=15/2/0/12.
`extract_bank.py` now uses `field_inv()`; old artifacts are kept as `out/*_prevfields.wopn` for A/B.

### 7.2 MIDI Event Layer (`seg1:0x04DE` dispatch)

Message → handling (only these 5 classes are handled, others ignored):

| Status | Handling | Address |
|---|---|---|
| 0x8x note off | `seg1:0x0350`: find voice → key-off → clear state | |
| 0x9x note on | velocity 0 = note off; ch9/ch15 go to drums | `seg1:0x02D0` |
| 0xBx CC | **only CC≥0x7B** (All Notes Off etc.) → key-off everything | `seg1:0x05AE` |
| 0xCx program | first kill all notes on that MIDI channel, then store program | `seg1:0x0478` |
| 0xEx pitch bend | 14-bit bend → modify FNUM per voice | `seg1:0x03B2` |

Volume/pan CCs are **ignored**; loudness is determined solely by velocity.

### 7.3 Velocity → TL (`seg3:0x0BED`)

Velocity first passes through the 128-entry table at DGROUP:0x2E: `level = 0 (v<2) | 0x40 + v/2` (monotonic, covers only the upper half).
Then:

```
TL[i] = clamp8(mask[ALG][i] & (0x7F - level)) + (0x7F - rec[0x1B+i])
```

`mask` = `cs:0x0C55` (8 algorithms × 4 operators, 0x00/0x7F) = **carrier operator** set:

| ALG | 0-3 | 4 | 5 | 6 | 7 |
|---|---|---|---|---|---|
| Operators receiving velocity (S1..S4) | S4 | S2,S4 | S2,S3,S4 | S2,S3,S4 | all |

Only carriers receive velocity; modulator volume is fixed; velocity affects a range of 0x3F (≈47 dB).
When `TL>0x7F`, writer `seg3:0x14A6` clamps to 0x7F (silent operator).

### 7.4 Channel Allocation and Voice Stealing (`seg1:0x0058`, 6 FM voices)

Channel records are in instance struct `+0x4A` (stride 0x14): `+0=note, +1=MIDI channel, +3=active, +6..9=sequence number`.
Four search rounds:

1. Active voice on the same MIDI channel with `note==0` → key-off first, then reuse (**no voice reload**)
2. Free voice → use
3. Active voice on another MIDI channel with `note==0` → use
4. Stealing: take the occupied voice with the **largest MIDI channel number** (same number → smallest sequence = oldest);
   `seg1:0x0181` if `that voice's MIDI channel < requested channel` → **return 0xFF and abandon the note**

That is, **lower MIDI channel numbers have higher priority**, and at full load higher channels may lose notes.
Voice loading (`seg1:0x01C7` → cmd 0x16) is performed only when "the voice is not occupied by this MIDI channel";
pointer = `DS:0x6F4 + program*64`.

### 7.5 Transposition FMOCTAVE (`seg1:0x0232`, resource 0x7510)

`note += FMOCTAVE[program]` (**signed** byte), then clamped to 0x7F by **unsigned** comparison.
This bank's 128 entries have only 3 values: `0xF4=-12` (122 entries), `0xE8=-24` (5 entries), `0xC4=-60` (1 entry,
program 125 Gunshot). §5's "purpose of table 0x7510 unsolved" is resolved here.
ch9/ch15 are not transposed. The voice record stores the **pre-transposition** original pitch (`seg1:0x0301`).

The transposition result has another gate before sending cmd 0x25 via `seg1:0x0010` (`seg1:0x0039`:
`cmp byte ptr [bp+6], 0x5C` + `jae 0x51`, bp+6 = transposed note): **when the transposed pitch is ≥ 0x5C(92),
no key-on is performed** (no FNUM, no 0x28), while velocity (cmd 0x1F) is still written → the note is **silent on real hardware,
but the voice has already been allocated and occupied**.
That is, notes in the low range where `note + FMOCTAVE < 0` wraps to 0x7F are actually silent rather than the highest note.

### 7.6 Pitch / Pitch Bend (`seg3:0x0C75`, `seg1:0x03B2`)

```
FNUM = cs:0x14F4[note%12] + cs:0x150C[note%12] * bend255 / 255     block = note/12
write 0xA4+ch (block<<3|fnum9:8) then write 0xA0+ch
```

Pitch bend: `bend14 = msb<<7|lsb`, `step = 8191/range + 1`, `base = step*range - 8192`
(range = DGROUP:0xE2, default 2 semitones), `note' = note + (bend14+base)/step - range`,
remainder linearly converted to 0-255. Center 8192 → original pitch.

### 7.7 Drums (`seg3:0x0CCB`, table DGROUP:0x8B+note)

ch9/ch15, note range 35..81, mapped to YM2608 onboard 6 rhythms (**0x7F = mute**):

| Instrument | GM notes |
|---|---|
| BD(0) | 35, 36 |
| SD(1) | 38, 40 |
| Top Cymbal(2) | 49, 51, 52, 55, 57, 59 |
| Hi-Hat(3) | 42, 44, 46 |
| Tom(4) | 41, 43, 45, 47, 48, 50 |
| Rim Shot(5) | 37 |
| Mute | 39(Hand Clap), 53, 54, 56, 58, **60-81 all** |

Level = `(0x40+v/2)>>2` (0..31), write `0x18+instrument = 0xC0|level`, then `0x10 = 1<<instrument` to trigger.
Drums **have no release** (`seg1:0x2CC` = `ret 8`).
The dispatcher at `seg1:0x055F` checks velocity first: velocity 0 goes down the note-off path → the drum channel also lands at 0x2CC,
so `0x9x + velocity 0` note-on causes **completely no writes** in this driver (it will not hit the drum).

### 7.8 Software Envelope + LFO (`seg3:0x0D59`, Timer-A tick)

`seg3:0x0D27` polls OPNA status bit 1 (Timer-A flag) → `seg3:0x100C` acknowledge → `seg3:0x0D59`.
Timer-A = 0x0E3 (reg 0x26; reg 0x25 never written, treated as 0) → `(1024-0xE3)*12/8MHz` = 1.1955 ms
≈ **836.5 Hz**.

Per-channel state machine (`ws+0x18` low 2 bits = 0..3, bit7 = ever keyed on; `ws+0x14` = sounding):

- state 3 (after key-on) → according to field 0x0F (LFO start phase): 1=start from 0, 0=follow global count, others=delay count
- state 1 → decrement count to 0 → state 2
- state 2 → step LFO and modulate every tick

LFO waveform (field 0x0A): 0=triangle (overflow inverted value), 1=square (parity of `counter/period`),
2=triangle (overflow flips direction, `ws+0x648` = ±1), 3=random (`seg3:0x10AF`, multiplier 0x383),
4/5=sawtooth clamped to 0. Rate = field 0x14 (16-bit), period `0xFFFF/rate` ticks.

At the end of each tick that completes a step, `ws+0x19` (LFO counter) increments once (`seg3:0x0EFB inc word ptr [bx+0x19]`);
only the state-1 branch that decrements to nonzero skips it (`0x0E3A jmp 0x0EFE`). Square-wave phase (`0x108D`: parity of `[0x654]/period`)
and random stepping (`0x10AF`: `[0x654] % (period|1)`) both read a copy of this counter `[0x654]` (copied at `0x0E40`);
random waveform state is stored at `DS:0x2876`, data segment initial value is **0** and the driver never seeds it → this waveform is always 0 in this driver (dumb behavior).

Modulation (every tick):

```
Vibrato:  fnum += (LFO * depth / 0x7FFF) * fnum / 0x7FF      depth = field 0x19 × field 0x23
TL:    TL[i] = clamp8(TLbase[i] + TLbase[i] * (ws[i] * LFO / 0x7FFF) / 0x7F)
       ws[i] = (record 0x2F+i & 0xF) * record 0x1F / 15       (seg3:0x0F16 scale_knob)
```

The TL formula is reproduced byte-for-byte from `seg3:0x0F75` (including `xor ah,ah` truncation and `add` carry clamp).

### 7.9 Hardware Initialization (`seg3:0x0B22` / `0x0BA4` / `0x1133`)

- 0x90-0x9E (skip 0x93/0x97/0x9B) = 0 (SSG-EG)
- 0xB4-0xB6 = 0xC0 (pan L+R); **the RL bits in the record written to 0xB0 are ignored by hardware, pan is always center**
- Rhythm: 0x12=0, 0x11=0x30, 0x18-0x1D=0xC4
- Timer: 0x27=0x30, 0x26=0xE3
- key-on: `0x28 = (record 0x05 & 0x0F)<<4 | ch` (ch≥3 → +1, corresponding to OPN channel encoding 0,1,2,4,5,6);
  key-off: `0x28 = ch`

### 7.10 Still Unsolved / Uncertain

1. Timer-A's reg 0x25 is never written; if real hardware has another initial value, the LFO rate scales globally
2. The multiplier 0x383 and initial-value semantics of the `seg3:0x10AF` random waveform can only be inferred from assembly
3. The pitch bend fractional part goes through C runtime helper routines (`0x0FA2/0x0FAE`), implemented as `*255/step`,
   and may differ from `*256/step` by 1/255 semitone
4. `MYFAIRLADY` (`seg1:0x063E`) 0x64-0x67 extended messages and MODM_OPEN instance struct initialization
   are not expanded line by line (does not affect the MIDI playback path)
5. Channel record `+0x4C` byte has not been seen read