# necopn_to_wopn.py
import struct
import os

from necopn_core import ChipCore
from necopn_driver import NecopnSystem, Voice, SLOT_OFFSET
from fixed_bank import VOICES_DATA, FMOCTAVE

class PseudoChipCore(ChipCore):
    name = "pseudo"
    def __init__(self, clock=8_000_000):
        self.clock = clock
        self.regs = [0] * 256
    def write(self, port: int, reg: int, val: int) -> None:
        self.regs[reg & 0xFF] = val & 0xFF
    def read(self, port: int, reg: int) -> int: return 0
    def sample_rate(self) -> int: return self.clock // 144
    def generate(self, nframes: int) -> bytes: return b"\x00" * (4 * nframes)
    def reset(self) -> None: self.regs = [0] * 256

def get_gm_name(i: int) -> str:
    names = ["Acoustic Grand Piano", "Bright Acoustic Piano", "Electric Grand Piano", "Honky-tonk Piano", 
        "Electric Piano 1", "Electric Piano 2", "Harpsichord", "Clavinet", "Celesta", "Glockenspiel", "Music Box", 
        "Vibraphone", "Marimba", "Xylophone", "Tubular Bells", "Dulcimer", "Drawbar Organ", "Percussive Organ", 
        "Rock Organ", "Church Organ", "Reed Organ", "Accordion", "Harmonica", "Tango Accordion", "Acoustic Guitar (nylon)", 
        "Acoustic Guitar (steel)", "Electric Guitar (jazz)", "Electric Guitar (clean)", "Electric Guitar (muted)", 
        "Overdriven Guitar", "Distortion Guitar", "Guitar Harmonics", "Acoustic Bass", "Electric Bass (finger)", 
        "Electric Bass (pick)", "Fretless Bass", "Slap Bass 1", "Slap Bass 2", "Synth Bass 1", "Synth Bass 2", 
        "Violin", "Viola", "Cello", "Contrabass", "Tremolo Strings", "Pizzicato Strings", "Orchestral Harp", 
        "Timpani", "String Ensemble 1", "String Ensemble 2", "SynthStrings 1", "SynthStrings 2", "Choir Aahs", 
        "Voice Oohs", "Synth Voice", "Orchestra Hit", "Trumpet", "Trombone", "Tuba", "Muted Trumpet", "French Horn", 
        "Brass Section", "SynthBrass 1", "SynthBrass 2", "Soprano Sax", "Alto Sax", "Tenor Sax", "Baritone Sax", 
        "Oboe", "English Horn", "Bassoon", "Clarinet", "Piccolo", "Flute", "Recorder", "Pan Flute", "Blown Bottle", 
        "Shakuhachi", "Whistle", "Ocarina", "Lead 1 (square)", "Lead 2 (sawtooth)", "Lead 3 (calliope)", 
        "Lead 4 (chiff)", "Lead 5 (charang)", "Lead 6 (voice)", "Lead 7 (fifths)", "Lead 8 (bass + lead)", 
        "Pad 1 (new age)", "Pad 2 (warm)", "Pad 3 (polysynth)", "Pad 4 (choir)", "Pad 5 (bowed)", "Pad 6 (metallic)", 
        "Pad 7 (halo)", "Pad 8 (sweep)", "FX 1 (rain)", "FX 2 (soundtrack)", "FX 3 (crystal)", "FX 4 (atmosphere)", 
        "FX 5 (brightness)", "FX 6 (goblins)", "FX 7 (echoes)", "FX 8 (sci-fi)", "Sitar", "Banjo", "Shamisen", "Koto", 
        "Kalimba", "Bag pipe", "Fiddle", "Shanai", "Tinkle Bell", "Agogo", "Steel Drums", "Woodblock", "Taiko Drum", 
        "Melodic Tom", "Synth Drum", "Reverse Cymbal", "Guitar Fret Noise", "Breath Noise", "Seashore", "Bird Tweet", 
        "Telephone Ring", "Helicopter", "Applause", "Gunshot"]
    return names[i] if i < len(names) else f"Instrument {i}"

def pack_instrument(inst_data, key_offset, is_blank=False):
    if is_blank: return b'\x00' * 69
    name = inst_data['name'].encode('ascii', 'ignore') + b'\x00'
    buf = bytearray()
    buf.extend(name.ljust(32, b'\x00'))
    buf.extend(struct.pack('>h', key_offset))
    buf.append(0)
    buf.append((inst_data['fb'] << 3) | inst_data['alg'])
    buf.append(0x00)
    
    # 修正 1：按 WOPN 规范要求的硬件顺序 [Op1, Op2, Op3, Op4] 重新排列
    # 我们的列表是 [Op1(0), Op3(1), Op2(2), Op4(3)]，所以要按 [0, 2, 1, 3] 取出
    reordered_ops = [inst_data['operators'][0], inst_data['operators'][2], inst_data['operators'][1], inst_data['operators'][3]]
    
    for op in reordered_ops:
        buf.append((op['dt'] << 4) | op['mul'])
        buf.append(op['tl'])
        buf.append((op['ks'] << 6) | op['ar'])
        buf.append(op['dr']) 
        buf.append(op['sr'])
        buf.append((op['sl'] << 4) | op['rr'])
        buf.append(0x00)
    buf.extend(struct.pack('>h', 1000))
    buf.extend(struct.pack('>h', 50))
    return bytes(buf)

def generate_wopn_from_driver(output_path="NECOPN_Dynamic.wopn"):
    print("正在初始化虚拟芯片和 NECOPN 驱动...")
    chip0 = PseudoChipCore()
    chip1 = PseudoChipCore()
    
    system = NecopnSystem([chip0, chip1], lfo_enabled=False)
    voices = [Voice.from_record(bytes(r)) for r in VOICES_DATA]
    system.load_bank(voices, list(FMOCTAVE))
    
    velocity = 100 
    test_note = 60 # C4
    
    melodic_entries = []
    
    for i, voice in enumerate(voices):
        chip0.regs = [0] * 256
        system.vstates[0] = system.vstates[0].__class__()
        
        system.programs[0] = i
        system.slots[0].midi_ch = 0
        system.slots[0].active = True
        system.slots[0].note = test_note
        
        system.load_voice(0, voice)
        system.set_velocity(0, velocity)
        system.set_pitch(0, test_note, 0)
        system.key_on(0, test_note)
        
        for _ in range(2):
            system.tick()
            
        regs = chip0.regs
        fbalg = regs[0xB0]
        alg = fbalg & 0x07
        inst_data = {
            'name': get_gm_name(i),
            'alg': alg,
            'fb': (fbalg >> 3) & 0x07,
            'operators': []
        }
        
        for slot_idx, off in enumerate(SLOT_OFFSET):
            dt_ml = regs[0x30 + off]
            tl    = regs[0x40 + off]
            ks_ar = regs[0x50 + off]
            dr    = regs[0x60 + off]
            sr    = regs[0x70 + off]
            sl_rr = regs[0x80 + off]
            
            op = {
                'dt': (dt_ml >> 4) & 0x07,
                'mul': dt_ml & 0x0F,
                'tl': tl,
                'ks': (ks_ar >> 6) & 0x03,
                'ar': ks_ar & 0x1F,
                'dr': dr,
                'sr': sr,
                'sl': (sl_rr >> 4) & 0x0F,
                'rr': sl_rr & 0x0F
            }
            inst_data['operators'].append(op)
            
        # 载波音量最大化
        olevels = [inst_data['operators'][k]['tl'] for k in range(4)]
        dec = 0
        if alg in (0, 1, 2, 3):
            inst_data['operators'][3]['tl'] = 0
        elif alg == 4:
            dec = min(olevels[1], olevels[3])
            inst_data['operators'][1]['tl'] = olevels[1] - dec
            inst_data['operators'][3]['tl'] = olevels[3] - dec
        elif alg in (5, 6):
            dec = min(olevels[2], olevels[1], olevels[3])
            inst_data['operators'][2]['tl'] = olevels[2] - dec
            inst_data['operators'][1]['tl'] = olevels[1] - dec
            inst_data['operators'][3]['tl'] = olevels[3] - dec
        elif alg == 7:
            dec = min(olevels[0], olevels[2], olevels[1], olevels[3])
            inst_data['operators'][0]['tl'] = olevels[0] - dec
            inst_data['operators'][2]['tl'] = olevels[2] - dec
            inst_data['operators'][1]['tl'] = olevels[1] - dec
            inst_data['operators'][3]['tl'] = olevels[3] - dec
            
        # 修正 2：因为截取时使用的是硬件寄存器，音高已经是固定的 C4 了。
        # 不需要、也不能再加上 FMOCTAVE 偏移（否则会降八度）。
        melodic_entries.append(pack_instrument(inst_data, key_offset=0))
        
    out_buf = bytearray()
    out_buf.extend(b'WOPN2-B2NK\x00')
    out_buf.extend(struct.pack('<H', 2))
    out_buf.extend(struct.pack('>H', 1))
    out_buf.extend(struct.pack('>H', 1))
    out_buf.append(0x01) # 标记为 OPNA 芯片
    
    out_buf.extend(b'NECOPN Dynamic'.ljust(32, b'\x00'))
    out_buf.extend(b'\x00\x00')
    out_buf.extend(b'NECOPN Percussion'.ljust(32, b'\x00'))
    out_buf.extend(b'\x00\x01')
    
    for entry in melodic_entries: out_buf.extend(entry)
    for _ in range(128): out_buf.extend(pack_instrument(None, 0, is_blank=True))
        
    with open(output_path, 'wb') as f:
        f.write(out_buf)
        
    print(f"✅ 成功！已生成 {output_path}")
    print("已修正 Operator 顺序偏移与八度偏移问题。")

if __name__ == "__main__":
    generate_wopn_from_driver()