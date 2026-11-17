# NECOPN-RT — realtime OPNA (YM2608) player

A realtime port of the [`necopn-drv-reverse-main`](../necopn-drv-reverse-main)
NECOPN.DRV renderer: MIDI → register writes → YM2608 → 55 555 Hz → 48 kHz → sound card.

Sister project of `OPMMIDI-RT` (OPM realtime); that tree is untouched.

## Usage

```powershell
python app.py                                   # tkinter GUI (loop / RHY / SSG-only / export / zh-en)
python app.py --selfcheck
python rtplayer.py song.mid                     # fmgen full OPNA (default)
python rtplayer.py song.mid --core nuked        # two Nuked OPN2 chips
python rtplayer.py song.mid --core nuked1       # one Nuked OPN2, 6 FM channels
python rtplayer.py song.mid --ssg-only          # ignore RHY, SSG drums only
python rtplayer.py song.mid --export out.wav
python rtplayer.py song.mid --bench 20
python tool/selftest.py --midi song.mid --limit 3
```

The GUI supports loop playback, a user RHY folder, SSG-only drums, WAV export,
live volume, core selection and zh/en switching. There is **no voice-bank
switcher** — NECOPN.DRV's 128 voices are part of the driver.

Measured on an i5-class machine, 48 kHz WASAPI, block 256:

- latency **22.0 ms**, **0 underrun / 0 xrun** over a 3 s run (both backends)
- end-to-end **92.7x realtime** (fmgen), **8.1x** (Nuked composition)
- single-file GUI EXE: launches and self-checks; same Session/Engine as the
  CLI (22.0 ms latency, 0 underrun)
- native YM2608 rate **55 555 Hz** (8 MHz / 144); exact PC-98 value is
  **55 466 Hz** (7 987 200 / 144) — use `--clock 7987200`

Build the GUI EXE with `.\build_exe.ps1`; it bundles `opn2.dll`, `ssg.dll`,
`core/necopna_fmgen.dll` only (no samples).

## Backends

| `--core` | Chips | Realtime | Notes |
|---|---|---:|---|
| `fmgen` (default) | full OPNA: FM + SSG + ADPCM-B + rhythm | 92.7x | same core as the offline reference |
| `nuked1` | one Nuked OPN2 (6 FM channels) + emu2149 + PCM rhythm | 15.3x | single-chip Nuked, 1.85x faster than two chips |
| `nuked` | two Nuked OPN2 (3+3) + emu2149 + PCM rhythm | 8.3x | original NECOPN 2-chip structure |

`nuked1` maps the two logical units (3 voices each) onto one YM2608's six FM
channels: unit0 uses physical ch0-2, unit1 uses ch3-5 (bank 1, key-on codes 4-6).
Loudness is matched to the two-chip path (5.mid RMS 262.7 vs 241.2).

The full Nuked YM2608 LLE (`nuked_lle`) was measured at
**0.22x realtime for one chip** (`-O2`), is GPLv2+, and embeds the 8 KB rhythm
ROM, so it is kept only as an offline reference/validator.

## No bundled samples

The YM2608 rhythm channel needs a user-supplied `RHY/` folder with
`2608_bd.wav`, `2608_sd.wav`, `2608_top.wav`, `2608_hh.wav`, `2608_tom.wav`,
`2608_rim.wav`. Without it the player falls back to synthetic SSG drums.

## Licenses

Project code MIT. Nuked OPN2 LGPL-2.1. emu2149 MIT. fmgen (cisc) free-software
terms, non-commercial. Nuked YM2608 LLE (GPLv2+) is **not** distributed here.
See [NOTICE.md](NOTICE.md) and [README_zh.md](README_zh.md) for details.
