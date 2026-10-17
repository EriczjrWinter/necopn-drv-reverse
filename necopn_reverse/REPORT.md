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
Timer-A: Configured at 0x0E3, ticking at ~836.5 Hz. This drives the software state machine and LFO.

LFO Implementation: Modulates both Pitch (FNUM) and Total Level (TL). The phase and step logic are fully replicated.

Known Issue (Phase Resets): In software emulators, writing to the FNUM registers at 836Hz causes internal phase discontinuity, resulting in a slight background noise (the "electric buzz"). Since the original hardware relies on physical CPU timing and IO wait-states (which smooth out these updates), this behavior is inherently difficult to replicate purely in synchronous Python. The LFO is therefore disabled by default in the Python player to ensure clean audio.

7. Known Unsolved / Limitations
VGM Export: The multi-chip VGM stream separation has header compatibility issues with strict players (e.g., foobar2000 vgmplay).

Software LFO Noise: As mentioned above, disabled by default due to phase reset artifacts in FM synthesis cores.

MYFAIRLADY API: The extended private API (0x64-0x67 messages) was not deeply expanded, as it is unrelated to standard MIDI playback.