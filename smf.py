# -*- coding: utf-8 -*-
"""标准 MIDI 文件（SMF type 0/1/2）解析 —— 只取播放需要的部分。

产出绝对时间（秒）排序的事件列表：(t, kind, ch, d1, d2)
kind: 'on' | 'off' | 'cc' | 'prog' | 'bend' | 'tempo' | 'meta'
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import List, Tuple


@dataclass
class Event:
    t: float
    kind: str
    ch: int = 0
    d1: int = 0
    d2: int = 0


def _read_vlq(buf: bytes, i: int) -> Tuple[int, int]:
    v = 0
    while True:
        b = buf[i]
        i += 1
        v = (v << 7) | (b & 0x7F)
        if not (b & 0x80):
            return v, i


def parse_smf(data: bytes) -> Tuple[List[Event], int, int]:
    """返回 (事件表, 每四分音符 tick 数, 曲长秒数)。"""
    if data[:4] != b"MThd":
        raise ValueError("不是 SMF 文件")
    hdr_len, fmt, ntrk, division = struct.unpack_from(">IHHH", data, 4)
    if division & 0x8000:
        raise ValueError("SMPTE 时间格式暂不支持")
    tpq = division or 480

    pos = 8 + hdr_len
    tempo_map: List[Tuple[int, int]] = []      # (tick, usec_per_quarter)
    raw_events: List[Tuple[int, int, int, int, int]] = []   # (tick, kind, ch, d1, d2)

    for _ in range(ntrk):
        if pos + 8 > len(data) or data[pos:pos + 4] != b"MTrk":
            break
        tlen = struct.unpack_from(">I", data, pos + 4)[0]
        i = pos + 8
        end = i + tlen
        tick = 0
        status = 0
        while i < end:
            delta, i = _read_vlq(data, i)
            tick += delta
            b = data[i]
            if b & 0x80:
                status = b
                i += 1
            if status == 0xFF:
                meta = data[i]
                i += 1
                ln, i = _read_vlq(data, i)
                payload = data[i:i + ln]
                i += ln
                if meta == 0x51 and ln == 3:
                    tempo_map.append((tick, (payload[0] << 16) | (payload[1] << 8) | payload[2]))
                continue
            if status in (0xF0, 0xF7):
                ln, i = _read_vlq(data, i)
                i += ln
                continue
            kind = status & 0xF0
            ch = status & 0x0F
            if kind in (0xC0, 0xD0):
                d1 = data[i]
                i += 1
                d2 = 0
            else:
                d1 = data[i]
                d2 = data[i + 1]
                i += 2
            if kind in (0x80, 0x90, 0xB0, 0xC0, 0xE0):
                raw_events.append((tick, kind, ch, d1, d2))
        pos = end

    if not tempo_map:
        tempo_map = [(0, 500000)]
    tempo_map.sort()
    # 若第一个 tempo 不在 tick 0，补一个默认 tempo —— 否则 tick_to_sec 对早期事件会返回负数，
    # events.sort() 把负数时间排到最前，整个时间轴错乱（"固定位置丢音/走调"的嫌疑来源）
    if tempo_map[0][0] > 0:
        tempo_map.insert(0, (0, 500000))

    def tick_to_sec(t: int) -> float:
        sec = 0.0
        last_tick, last_tempo = tempo_map[0]
        # 起点之前的 tempo 用第一条
        for idx in range(1, len(tempo_map)):
            mt, mtempo = tempo_map[idx]
            if mt >= t:
                break
            sec += (mt - last_tick) * last_tempo / 1e6 / tpq
            last_tick, last_tempo = mt, mtempo
        sec += (t - last_tick) * last_tempo / 1e6 / tpq
        return sec

    events: List[Event] = []
    for tick, kind, ch, d1, d2 in raw_events:
        name = {0x80: "off", 0x90: "on", 0xB0: "cc",
                0xC0: "prog", 0xE0: "bend"}[kind]
        events.append(Event(tick_to_sec(tick), name, ch, d1, d2))
    events.sort(key=lambda e: e.t)
    total = max((e.t for e in events), default=0.0)
    return events, tpq, total
