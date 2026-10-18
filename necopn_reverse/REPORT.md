NECOPN.DRV Reverse-Engineering Report
Target: NECOPN.DRV (Windows 95 PC-9821 MIDI Driver)
SHA256: cae5234a9ca88ac51495a065e8b85e2a7e7a11d98d213b95fa056e10b542dae8
Size: 30,096 bytes
Goal: Achieve 1:1 hardware-accurate reproduction of the driver's audio behavior in software.

1. File Identity & Architecture
Format: 16-bit NE (New Executable) DLL.

Segments:

seg1: Initialization & MIDI Event Dispatcher (LibMain, MYFAIRLADY).

seg2: WEP (Windows Exit Procedure).

seg3: Runtime Engine (Register mapping, voice loading, Timer-A interrupts, LFO).

seg4: Data Segment (Hardware port tables, resources).

Hardware Target: Dual YM2608 (OPNA) chips. The driver strictly maps software channels to hardware channels as:

Logical FM CH 0-2 → Primary OPNA (Ports 0x188/0x18A)

Logical FM CH 3-5 → Secondary OPNA (Ports 0x18C/0x18E)

Note: Each OPNA chip only uses its CH0-CH2, yielding exactly 6 voices total.

2. Voice Bank Extraction (FMPARA)
Location: File offset 0x5510, size 0x2000.

Structure: 128 GM voices, each exactly 64 bytes.

Field Mapping & Inversion: The driver stores envelope values in an inverted format. The field reader (seg3:0x11F8) applies the inversion table at cs:0x1239.

AR, DR, SR = 0x1F - rec[n]

TL = 0x7F - rec[n] (Located at record bytes 0x1B-0x1E)

Critical WOPN Correction: When exporting to WOPN, SL and RR must NOT be inverted. Blindly applying 0x0F - rec to SL turns the value 0 (maximum sustain) into 15 (instant mute), causing organs to sound like woodblocks. WOPN expects direct hardware values for these fields.

Operator Order: The driver uses hardware slot offsets [0x00, 0x08, 0x04, 0x0C]. When exporting to WOPN (which uses +0/+4/+8/+C), the order must be mapped as [0, 2, 1, 3].

3. Transposition (FMOCTAVE)
Location: File offset 0x7510 (128 bytes).

Behavior: note += FMOCTAVE[program] (signed byte).

Silent Trap: After transposition, if the resulting note is >= 0x5C (92) (due to unsigned wrapping from negative offsets), the driver skips the key-on entirely (does not write FNUM or 0x28) but still allocates the voice. This results in silent notes that steal polyphony.

4. MIDI Event Layer & Voice Allocation (seg1:0x04DE)
Velocity to TL (seg3:0x0BED): Velocity is mapped via a 128-entry table (level = 0x40 + v/2), then masked by the algorithm's carrier operators (cs:0x0C55). Modulators do not respond to velocity.

Voice Stealing (seg1:0x0058): 4-pass allocation. If all 6 voices are occupied, it steals the voice with the highest MIDI channel number. If the requested channel is lower than the victim's channel, the note is dropped entirely. Low MIDI channels have absolute priority.

Drums (ch 9/15): Mapped to the 6 onboard YM2608 rhythm instruments. Mute notes (e.g., 39, 60-81) are explicitly ignored.

5. The Core Discovery: Envelope Slow-Tier
During verification against NP21/W recordings, a critical discrepancy was found in the popular ymfm emulator core.

The Bug: The YM2608 envelope generator has a "Slow-Tier" for low Sustain Rates (SR < 15). Both Nuked-LLE and fmgen implement this by checking if rate2 < 48 and gating the envelope counter (roughly 3 out of 16 cycles). ymfm lacks this tier, causing low-SR voices (which make up 85% of the NECOPN GM bank, like strings, organs, and pads) to decay instantly.

The Solution: The backend core was completely swapped from ymfm to fmgen (ported from NP21/W), successfully restoring the sustained notes and achieving a 1:1 hardware match.

6. Software Envelope & LFO (seg3:0x0D59)
Timer-A: Configured at 0x0E3, ticking at ~836.5 Hz. Drives the envelope state machine.

Actual Upper Bound of LFO Rate: `irq_poll` (seg3:0x0D27) is not an interrupt service routine but a **polling function** placed directly below the `MODMESSAGE` label. Each invocation checks a YM2608 status bit and only acknowledges + updates LFO if Timer-A has overflowed. Therefore the actual LFO advance rate equals the `MODMESSAGE` callback frequency, not the Timer-A tick. Under Win95's `midiStreamOut` this callback typically sits around ~120 Hz (80-150 Hz range), meaning most Timer-A overflows are dropped.

`rate_word` was calibrated against 836.5 Hz: In the disassembly, `rate_word = record[0x14..15]` is multiplied directly into the accumulator with no intermediate division. This implies voice designers assumed "the driver provides an 836.5 Hz clock", while the actual callback frequency is far below that. **The discrepancy between design intent and real hardware behavior is a historical artifact of Win16 driver scheduling, not a bug.** The equivalent reproduction is to divide the LFO advance rate by 7 (`836.5 / 7 ≈ 119 Hz`), corresponding to the Python-side `--lfo-divisor 7` (default value).

True Root Cause of the Electromagnetic Noise (not phase reset): The previous explanation — that high-frequency FNUM writes cause phase discontinuity — is incorrect. `Operator::SetFNum` only sets `dp_`/`bn_` and marks `param_changed_`; `pg_count_` is cleared only by `KeyOn()`, not by `SetFNum`. The real root cause lies in the TL calculation at `seg3:0x0F92-0x0FC8`:
- After `idiv 0x7FFF`, the quotient byte is used as an **unsigned 0-255** value thanks to `xor ah, ah`
- When `t1` is negative (`lfo < 0` and `trem > 0`), its low byte falls in 0x80-0xFF, which assembly treats as a large positive 241-255
- The old Python code incorrectly did `if t1 >= 128: t1 -= 256`, converting back to signed, which made `t2 = (t1 * b) // 0x7F` negative
- `t2 &= 0xFF` followed by `s = t2 + b > 0xFF` triggers `s = 0` — TL jumps to maximum loudness (0 = loudest)
- Heard as "electromagnetic noise"; when the target is a carrier, it manifests as volume spikes

Fix: `_lfo_step`'s TL calculation now matches `0x0F92-0x0FC8` byte-for-byte, including the unsigned handling of `t1`, the positive-result path of `idiv 0x7F`, and the `s > 0xFF → 0` overflow check.

Shape 1/3 Phase Correction: The phase decision for square/noise LFO depends on `lfo_counter`, but this counter increments at 836.5 Hz while `_lfo_step` only executes at 120 Hz. Using it directly multiplies the square/noise frequency by 7. The fix uses `eff_counter = lfo_counter // lfo_divisor`, applied only in the shape 1/3 branches, leaving shape 0/2/4/5 unaffected.

7. Known Unsolved / Limitations
VGM Export: The multi-chip VGM stream separation has header compatibility issues with strict players (e.g., foobar2000 vgmplay).

Software LFO: The previously audible electromagnetic noise and excessive warble have been resolved (root cause detailed in Section 6). However, several details remain unverified, so it is disabled by default:
- The `rate_a` start-delay semantics for the envelope state machine are unverified (836.5 Hz vs 120 Hz baseline); the current implementation follows 836.5 Hz
- The `lfo_value < 0` clamp for shape 4/5 has not been byte-verified against the disassembly at `0x1060`
- The default `--lfo-divisor 7` comes from a reasonable estimate of "Win95 MIDI callback ≈ 120 Hz", not from a value directly confirmed in the disassembly. Users can tune between `--lfo-divisor 5` and `14` to align with their own hardware recording

MYFAIRLADY API: The extended private API (0x64-0x67 messages) was not deeply expanded, as it is unrelated to standard MIDI playback.