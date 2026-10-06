# NECOPN.DRV Reproduction Project — Audit Report on the List of Suspected Issues

Audit target: workspace `D:\` (`NECOPN.DRV` + `necopn_reverse/` reverse-engineering artifacts + `necopn_player/` reproduction implementation)  
Audit basis: all disassembly conclusions in this document include addresses; source listings are in `necopn_reverse/out/seg1_listing.txt` and `seg3_listing.txt`. All static-data conclusions were obtained by scripts directly reading raw bytes from `NECOPN.DRV` (scripts in §7).  
Audited list: `NECOPN.DRV 复现——完整疑点清单` (10 suspected issues + zero-cost verification items + items requiring hardware trace).

**Baseline (measured locally at the start of this audit)**: `python selftest.py` → 42/42 passed; `python core\smoke_test.py` → 24/24 passed;  
`player.py testdata\demo_rich.mid -o x.wav` produced sound normally (peak 4498/32767).

---

## 1. Summary of Conclusions

| # | Claim in the list | Audit conclusion | Key evidence |
|---|---|---|---|
| 1 | `note_off` matching by `midi_ch` will turn off the wrong chord note; suggested "exact match first, then fall back by `midi_ch`" | **Does not hold (the list describes old code)**. The current implementation already does exact `(midi_ch, note, active)` triple matching, taking the first; the suggested **fallback would deviate from the driver** and should not be added | seg1:0x0350 (`note_off`) + seg1:0x01EC (finder) |
| 2 | `ref_count` semantics differ from real hardware (real hardware = "number of simultaneously sounding notes on the same slot") | **Does not hold**. There is no `ref_count` in the code anymore; `VoiceState.key_alive` corresponds to driver `ws+0x14`, and the driver itself stores **0xFF/0 boolean**, not a counter. The referenced `sub_1ACC/sub_1B34` **do not exist** in this project's disassembly | seg3:0x1381 (writes 0xFF), seg3:0x139E (writes 0); actual key-on/key-off = seg3:0x130C→0x135A / 0x139A |
| 3 | `_allocate` pass 1 reusing a slot may overwrite a currently sounding note | **Does not hold**. pass 1 condition = `active && note==0 && midi_ch==req`; in this driver, "note==0" can only be caused by note_off (which also clears active) or by a note whose note value itself is 0 → this does not constitute "just occupied but not yet key-off" | seg1:0x0081–0x00D0 (pass 1); note_off clears active = 0x038E/0x0391 |
| 4 | Programs 119/121 voice-parameter mapping is abnormal (AR/DR/SR inversion direction is wrong either way) | **All static parts checked and consistent; the mapping direction (inversion) is correct**. The `cs:0x1239` inversion table matches `INVERT` byte for byte; for 119/121, head=0x38 (FB=7/ALG=0), slotmask=0xF, TL sources 0x6D/0x75/0x75/0x7F, etc., all match. "119 silent / 121 noisy" is a product of the **old (non-inverted) mapping** and disappears after correction. The remaining "listening" question cannot be judged in this environment (no hardware trace) | Inversion table seg3:0x1239; field reader seg3:0x11F8; TL path seg3:0x0BED; data in §3.1 |
| 5 | key-on slotmask inconsistent with real hardware (real hardware 0x28=0xDF, Python 0xF0\|ch) | **Does not hold**. The driver slotmask directly takes record logical byte 5 = `rec[0x05]`; all 128 entries in this bank are **0x0F**; the field writer seg3:0x11C8 is **never called** anywhere in the driver → that byte cannot be rewritten at runtime. Therefore 0xDF cannot be produced by this driver + this bank | seg3:0x1344–0x1377; `call 0x11c8` 0 hits; data in §3.1 |
| 6 | program_change side effect (frequently clearing sounding slots) | **This is the driver's designed behavior, not a defect**. Checked sentence by sentence and consistent (only clears the same MIDI channel, skips note==0, always active=0, then stores program). "Count occurrences in CR028.MID" cannot be done because the workspace has no such file (§6) | seg1:0x0478 (0x049E/0x04AA/0x04B0/0x04BF/0x04CC) |
| 7 | set_velocity / set_pitch call order may be reversed relative to real hardware | **Does not hold**. Driver order = cmd 0x1F (velocity→TL) first, cmd 0x25 (key-on, internally writes FNUM then 0x28) later; there is **no cmd 0x20 emission point** anywhere in seg1 (pitch is only written inside key-on) → Python's `set_velocity → set_pitch → key_on` is equivalent | seg1:0x0010 (0x1F at 0x002C, 0x25 at 0x004C); `b020` 0 hits |
| 8 | The LFO's TL modulation formula may have the sign reversed and "erase" notes | **Neither the formula nor the sign is reversed** (sentence-by-sentence consistent: `jae` → set 0 only on carry, equivalent to `s>0xFF→0`). **However, a real defect was found along the way**: Python's LFO counter never increments in state 2, freezing square/random waveforms (see §3.3) | seg3:0x0F75 (TL modulation); seg3:0x0EFB (`inc word [bx+0x19]`) |
| 9 | `_transpose` negative wraparound (note<12 becomes highest note) | **The code matches the assembly (this is the driver's real behavior)**; the real consequence is downstream: before key-on, the driver has a gate `note >= 0x5C(92) → skip key-on` → **notes wrapping to 127 are "silent" on real hardware**, whereas Python emits a high note (see §3.2) | seg1:0x0265–0x0273 (transpose/unsigned clamp), seg1:0x0039 (0x5C gate) |
| 10 | Drum note_off is completely ignored | **Holds and is correctly implemented**. "Ignored" applies to **all** note-offs on the drum channel (regardless of whether it is a drum note); 0x9x with velocity 0 goes through the same no-op path. Additional finding: Python still hits a drum for "drum channel + velocity 0 note-on" (§3.4) | seg1:0x04DE dispatch, 0x054A→0x0559→0x02CC (`ret 8`), 0x055F (velocity-0 branch) |

---

## 2. Review of the List's "I. Confirmed Correct" Section (Spot Checks)

| Item | Review result | Evidence |
|---|---|---|
| Global pitch formula `blk = note // 12` | ✔ Consistent with assembly | seg3:0x0C84–0x0CB4 (cmd 0x20), seg3:0x1320–0x1336 (inside key-on) |
| Allocation layer 4 rounds + stealing "lower channel number has priority" | ✔ Consistent with assembly (including the equality branch at 0x014C for `best_midi` initial value 0) | seg1:0x0058–0x0192 |
| Transposition FMOCTAVE table meaning and values | ✔ 128 entries = -12×122 / -24×5 / -60×1 (program 125) | 0x7510 measured |
| Drum mapping table | ✔ All items 35..81 consistent (including 39/53/54/56/58/60..81 muted) | DGROUP:0x8B+note measured |
| Velocity table | ✔ `0 (v<2) | 0x40+v/2`, all 128 entries correct | DGROUP:0x2E measured |
| Carrier mask table | ⚠ **ALG6 row inconsistent** (see §3.1-A), other 7 rows consistent | cs:0x0C55 measured |
| SSG not involved | ✔ The entire driver has **no writes** to 0x00–0x0F (literal constant register numbers are only 0x10/0x11/0x12/0x21/0x22/0x26/0x27/0x28/0x29/0x2D) | Full-list scan, §7 script 4 |
| ADPCM-B unrelated | ✔ Driver only touches ADPCM-A (0x10 trigger / 0x18-0x1D level), never touches ADPCM-B bank | Same as above + ymfm ADPCM-A register mapping |
| Timer-A / LFO rate 836.5 Hz | ✔ `reg 0x26 = 0x0E3`, **`reg 0x25` never written by the entire driver** ("not written" upgraded from guess to proven); 0x27=0x30; 0x29 only affects IRQ mask and is unrelated to audio | seg3:0x0FE3–0x100E; scan finds no 0x25 |
| Dual OPNA port routing | ✔ ch>=3 → offset -3 + switch port pair (0x1192 = 0x188/0x18A, 0x11AB = 0x18C/0x18E) | seg3:0x135A, 0x139A, 0x14A6 (`cmp al,3; sub al,3`) |
| Velocity affects only carriers, range 0x3F | ✔ (TL writer clamps to 0x7F) | seg3:0x0BED, 0x14A6 |

---

## 3. New Findings from This Audit (Reproducible, None in the Original List)

### 3.1-A The **ALG=6 row of `cs:0x0C55` is copied incorrectly** (real defect, ✅ fixed)

```
DRV  ALG6: [0x00, 0x7F, 0x7F, 0x7F]      # carriers = S2,S3,S4 (same as ALG5)
python   : [0x7F, 0x7F, 0x7F, 0x7F]      # mistakenly written as "all carriers"
```
The other 7 rows of `OPMASK` match DRV exactly; `REPORT.md §7.3` and `README.md`'s
"ALG6/7 = all" is only true for ALG7. Consequence: in ALG=6 voices, **op1 (modulator) TL is
additionally attenuated by velocity**, whereas the driver does not do this — measured for the same note (velocity 60),
the difference when writing 0x40 is `python 0x2A` vs `driver 0x09` (33 levels ≈ 25 dB), so both timbre and velocity response differ.
ALG distribution in this bank: {0:8, 1:8, 2:61, 3:4, 4:31, 5:12, 6:1, 7:3} → 1 affected voice (program 18).
`selftest.py` uses `v0` (ALG=2), so the existing 42 assertions cannot catch this error.

### 3.2 Missing the `note >= 0x5C → no key-on` gate (real defect, ✅ fixed)

Before cmd 0x25 in driver `seg1:0x0010` there is a gate:

```
0039  807e065c   cmp byte ptr [bp + 6], 0x5c     ; transposed note
003D  7312       jae 0x51                        ; >= 92 → skip key-on directly
```

That is, when the transposed pitch is ≥ 92: TL is still written (cmd 0x1F has no such gate), **FNUM is not written, 0x28 is not written**
(FNUM is written inside key-on at seg3:0x130C) → on real hardware **silent but the voice is occupied**.
Python lacks this gate; measured (FMOCTAVE[0] = -12):

| MIDI note | After transpose | Driver | Python |
|---|---|---|---|
| 5 | 127 (wrap) | silent (skip key-on) | **emits a high note** |
| 0 | 127 (wrap) | silent | **emits a high note** |
| 104 | 92 | silent | **emits a high note** |
| 103 | 91 | sounds | sounds ✔ |

In other words: what list issue 9 describes as "low range becomes highest note" actually manifests on real hardware as **silence**,
while Python currently emits a wrong note. This also explains "notes that should exist are missing / notes that shouldn't exist appear."

### 3.3 LFO counter does not increment → **square (shape1) / random (shape3) waveforms frozen** (real defect, ✅ fixed)

The driver does `inc` on `ws+0x19` (LFO counter) at the end of each processed tick:

```
0EFB  ff4719   inc word ptr [bx + 0x19]
```

The square-wave phase (seg3:0x108D: parity of `[0x654]/period`) and random stepping (seg3:0x10AF:
`[0x654] % (period|1)`) both read a copy of this counter `[0x654]` (copied from `ws+0x19` at 0x0E40).
Python's `_lfo_step` only reads `vs.lfo_counter`, and state 2 never increments → the count stays at the value from note-on.
Measured before the fix, 8 ticks (after the fix the counter increments per tick and the square-wave phase alternates, see §4):

```
prog  80 shape=1: LFO value = 7FFF 7FFF 7FFF 7FFF 7FFF 7FFF 7FFF 7FFF   counter = 1 1 1 1 1 1 1 1   (driver should alternate 7FFF/8000)
prog 102 shape=3: LFO value = 1234 1234 1234 1234 1234 1234 1234 1234   counter = 1 1 1 1 1 1 1 1   (driver steps once per period)
```

Impact: shape distribution in this bank is {0:1, 1:2, 2:124, 3:1} → only programs 80, 101 (square) and 102 (random) are affected;
the other 124 use shape2 (triangle with direction), which follows the "accumulator" path and is **unaffected**.
Moreover, all three have very small modulation depth (101: vib_depth=2, trem=[0,0,0,2]; 102: vib_depth=10; 80: vib_depth=2),
so the audible effect is "the expected vibrato becomes a constant offset / slight detune," not a major failure — but it is indeed a fixable deviation.

### 3.4 Drum-channel "velocity-0 note-on" is treated by Python as a drum hit (✅ fixed)

Driver: `seg1:0x055F` checks velocity first, `je 0x538` → goes down the note-off path → the drum channel eventually lands at 0x2CC (`ret 8`, **completely no writes**).
Python: `note_on()` first checks `midi_ch in (9,15)` then `velocity == 0` → enters `_drum_note_on`.
Measured register writes: `vel=100 → (0x19,0xDC),(0x10,0x02)`; `vel=0 → (0x19,0xC0),(0x10,0x02)` (two extra writes and a trigger).
`level=0` in YM2608 is **maximum attenuation** (ymfm: `vol = (level ^ 0x1F) + (total_level ^ 0x3F)`),
so the audible impact is very small; but "note-off using note-on velocity 0" is extremely common in MIDI, so it is recommended to change it to a pure no-op as the driver does.

### 3.5 Low-priority / harmless differences (recorded for reference, no need to change)

- When LFO state 3 and `rate_a >= 2`, the driver jumps to 0x0EFB and **does not step the LFO**, while Python steps once more —
  in this bank `rec[0x0F]` is only {0:126, 1:2}, and for the two branches with rate_a∈{0,1} the driver **does step on the same tick** (0xE1E→0xE3D, 0xDE0→0xE3D),
  so the actual impact = 0.
- shape 4/5: the driver only clamps the **return value** to 0, while the accumulator continues; Python directly writes the accumulator to 0.
  This bank has no shape 4/5 voices → impact 0. Similarly, when `rate_a==1 && shape==4`, the driver sets the accumulator initial value to 0x7FFF, while Python sets 0 (this combination does not exist in this bank).
- Random waveform initial value: driver `DS:0x2876` initial value = **0** (measured from the data-segment mirror), LCG stays 0 (the driver's own "dumb" behavior);
  Python originally used 0x1234 → program 102 had a slight constant detune. **Changed to 0 as the driver does** (see §4 ⑤).
- Numerical details: `imul/idiv` (truncate toward zero) and Python `//` (floor) differ by 1 TL level / 1 FNUM unit for negative values (≤0.75 dB / ≤0.2 cents), negligible.
- `cs:0x150C` BEND table item 12 (note%12 = 11) has its **high byte outside the declared length of seg3** (seg3 ends at file 0x5283; this word is at 0x5282-0x5283).
  Measured low byte = 0x44 = 68 = Python's value ✔; the real hardware reads a byte from the adjacent segment in memory. This is a driver quirk and needs no change.
- Initialization write-stream differences (no audio impact): Python writes `0xB4..0xB6 = 0xC0` one extra time (duplicate at source lines 264/265),
  and writes `0x25 = 0` (the driver never writes 0x25); the driver writes `0x21 = 0`, `0x29 = 2/0x82`, which Python does not
  (in ymfm, 0x29 is only an IRQ mask and unrelated to audio).

---

## 4. Applied Changes (Executed in This Audit, All with Regression Assertions)

Based on §3 conclusions, `necopn_player/necopn_driver.py` was changed directly, and
`necopn_reverse/REPORT.md` §7.3/§7.5/§7.7/§7.8, `necopn_player/README.md` §1/§6/§7, and `STATUS.md` §2.2 were corrected accordingly.

```python
# ① OPMASK: ALG6 row changed to (0x00, 0x7F, 0x7F, 0x7F) per cs:0x0C55
#    (REPORT §7.3 "ALG6/7 = all" → "ALG6 = S2,S3,S4, ALG7 = all"; README synchronized)

# ② note_on(): added the driver gate at seg1:0x0039 (TL still written, slot still occupied)
self.set_velocity(ch, level)
if n2 >= 0x5C:            # after transpose >= 92 → do not write FNUM, do not key-on
    return
self.set_pitch(ch, n2, 0)
self.key_on(ch, n2)

# ③ tick(): increment LFO counter at the end of ticks that complete a step (seg3:0x0EFB inc word [bx+0x19])
#    including the st==0 branch; the state-1 branch that decrements to non-zero does not increment (0x0E3A jmp 0x0EFE);
#    the state-3 branch with rate_a>=2 only sets the count without stepping

# ④ note_on(): drum channel checks velocity first (driver 0x055F)
if midi_ch in (9, 15):
    if velocity:                      # velocity == 0 → driver is a no-op
        self._drum_note_on(velocity, note)
    return

# ⑤ _lfo_step() shape3: divisor = period|1 (not rate_word|1);
#    state = (state * 0x383) mod 0x7FFF (not & 0x7FFF); VoiceState.random_state initial value 0x1234 → 0
```

**Regression assertions (`selftest.py`, 42 → 56 items, all green)**: the six tables `OPMASK`/`INVERT`/`VELOCITY_TABLE`/`DRUM_MAP`/
`FNUM_SEMITONE`/`BEND_DELTA` were changed to **cross-check directly against bytes from `NECOPN.DRV`**; behavior assertions were also added for
"ALG=6 TL matches the driver", "after transpose 0x5C does not write 0x28/FNUM but still occupies the voice", "0x5B keys on normally",
"LFO counter increments every tick", "square wave alternates 7FFF/8000 over time", "drum channel velocity 0 causes no writes", etc.

**Measured before/after comparison** (same script, same input):

| Check | Before | After |
|---|---|---|
| program 18 (ALG6), velocity 60 writes 0x40 | `0x2A` | `0x09` (= driver value) |
| MIDI note 104 (transposed 92) | writes 0x28 + FNUM (emits high note) | does not write 0x28/FNUM, only writes TL, occupies voice |
| prog 80 square-wave LFO (8 ticks) | constant `7FFF`, counter constant 1 | counter 2..9, alternates `7FFF/8000` within 400 ticks |
| prog 102 random LFO | constant `1234` (constant detune) | constant `0000` (= driver, unseeded) |
| Drum ch9 note-on vel 0 | writes `0x19=0xC0` + `0x10=0x02` | no writes |
| `selftest.py` | 42/42 | **56/56** |
| `core/smoke_test.py` | 24/24 | 24/24 (unaffected) |
| `demo_rich.mid` render | peak 4498 | peak 4498 (this song has no ALG6 / out-of-range transpose / square LFO voices; listening unchanged) |

---

## 5. Response to the List's "Core Judgment"

The list concludes "issues 1 and 2 are the most likely sources of widespread note loss" — **the audit does not support this**:
items 1 and 2 are no longer problems in the current code (1 is already exact matching; 2's `ref_count` no longer exists, and the driver itself has no reference counting).
The reproducible mechanisms that can actually cause "lost/wrong notes", ordered by likelihood, are:

1. **Inherent to the driver + faithfully reproduced**: only 6 FM voices; when fully loaded, `seg1:0x0181` (if it cannot steal from a lower MIDI channel number, abandon the note) → high channel numbers lose notes;
   plus overlapping note-ons on the same (channel, pitch) leave orphan slots (single-slot single-ownership model; note_off only turns off the first).
   This is not a bug, it is driver behavior; to be "more listenable" a separate switch must be added (the same conclusion is already recorded in STATUS.md §2.1).
2. **program_change kills sounding notes on the same channel** (driver design, seg1:0x0478) → sections with dense program changes in GM files will noticeably lose notes.
3. **New finding §3.2**: notes transposed to ≥92 are inherently silent on real hardware; Python now emits wrong notes (audibly like "extra strange notes").
4. **New findings §3.1-A / §3.3**: deviations at the single-voice level (program 18 velocity response, program 80/101/102 vibrato), not widespread note loss.

---

## 6. Completion Status of Zero-Cost Verification Items and Hardware-Trace Items

**List "IV. Zero-cost verifiable items"**
1. `--no-lfo` comparison → **already run** (but has no decisive power for this conclusion): `demo_rich.mid` peak 4498 (on) vs 4465 (off),
   a very small difference, because this song uses program 0 (vib_depth=0/trem=0, no modulation). The list originally intended to use CR028.MID; the workspace **does not have** that file.
2. CR028.MID event statistics → **cannot be done**: the workspace only has `testdata/demo.mid` and `demo_rich.mid` (a full-disk search also found only these two).
   The statistics script has been written and runs against these two files (`program_change` count, note<12 count, duplicate note-on count for the same (channel, pitch), maximum chord size per channel); it can be reused directly once CR028.MID is available.
3. Change `note_off` to exact matching → **not needed**: the current implementation is already exact matching (see issue 1); and "fall back by midi_ch" is inconsistent with the driver.
4. `diag_timbre.py --all` (list voices with peak=0) → that script **does not exist**; an equivalent static check was used instead. Conclusion:
   under the corrected mapping, at full velocity **0/128** voices have all carriers silent (peak not zero); with the old (non-inverted) mapping, 82/128 are silent —
   this conversely proves the inversion mapping direction is correct.

**List "V. Items requiring hardware trace"** → cannot be executed in this environment (no NP2/MAME register log, no `single_p119.mid`):
0x28 write value/count, write sequences for 0x40–0x4C and 0x50/0x60/0x70/0x80, whether 0x00–0x0F are written, whether 0x25 has a value, 0x90–0x9E initial values.
Three of these can already be **statically concluded from code + data**: 0x00–0x0F are never written by the driver (§2); reg 0x25 is never written by the driver (§2);
the slotmask for 0x28 in this bank is always 0xF (§3.1). Only "whether 119/121 sound consistent with real hardware" must rely on trace/recording.

---

## 7. Reproduction Commands

```powershell
cd D:\galgame
python $env:PI_SCRATCH_DIR\audit_data2.py      # inversion table / mask table / ALG distribution / drum mapping / table boundaries
python $env:PI_SCRATCH_DIR\audit_repro.py      # chord note_off, ALG6 register difference, LFO freeze, 0x5C gate
python $env:PI_SCRATCH_DIR\audit_midi_stats.py # MIDI event distribution (currently only demo*.mid)
python $env:PI_SCRATCH_DIR\audit_regs.py       # full-list scan: register numbers written by the driver
cd necopn_player; python selftest.py; python core\smoke_test.py
```
(`audit_*.py` are all read-only; the only code changes in this audit are `necopn_player/necopn_driver.py` and `selftest.py`;
documentation changes are in §4.)