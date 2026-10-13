"""Compare the injury routines in the two installed LE DLLs, offline."""
from pathlib import Path
import hashlib
import json
import pefile
from capstone import Cs, CS_ARCH_X86, CS_MODE_64

old = Path(r'C:\Users\prated\Desktop\FC 26 LE v26.3.5\FCLiveEditor.DLL')
new = Path(r'C:\Users\prated\Desktop\Fc26 LE\FCLiveEditor.DLL')
md = Cs(CS_ARCH_X86, CS_MODE_64)
profiles = {}
for path in (old, new):
    blob = path.read_bytes()
    pe = pefile.PE(data=blob)
    section = next(s for s in pe.sections if s.Name.rstrip(b'\0') == b'.text')
    profiles[path] = (blob, pe, section.get_data(), section.VirtualAddress)
evidence = {'new_dll_sha256': hashlib.sha256(profiles[new][0]).hexdigest(), 'routines': []}
for label, rva, length in [('fitness_getter', 0x2C6CA0, 33),
                           ('recovery_getter', 0x2C6DF0, 33),
                           ('fitness_reset', 0x47DBC0, 32),
                           ('recovery_reset', 0x47DBE0, 32),
                           ('injury_comparison', 0x390D26, 20)]:
    blob, pe, _, _ = profiles[old]
    off = pe.get_offset_from_rva(rva)
    signature = blob[off:off + length]
    # These small routines contain no relative calls in the compared window.
    target, target_pe, text, text_rva = profiles[new]
    positions = []
    start = 0
    while (pos := text.find(signature, start)) >= 0:
        positions.append(pos + text_rva)
        start = pos + 1
    row = {'label': label, 'old_rva': hex(rva), 'length': length,
           'exact_matches': [hex(p) for p in positions]}
    evidence['routines'].append(row)
    print(json.dumps(row))
    address = positions[0] if positions else rva
    offset = target_pe.get_offset_from_rva(address)
    for ins in md.disasm(target[offset:offset + max(length, 110)], address):
        print(f'{ins.address:X} {ins.mnemonic:8} {ins.op_str}')
Path('injury_26_3_6_evidence.json').write_text(json.dumps(evidence, indent=2))
assert all(len(row['exact_matches']) == 1 for row in evidence['routines']), 'Native routine comparison failed'
