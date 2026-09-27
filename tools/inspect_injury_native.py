"""Offline inspection of the installed Live Editor DLL; never opens FC26."""
from pathlib import Path
import re
import struct
import sys
import pefile
from capstone import Cs, CS_ARCH_X86, CS_MODE_64

path = Path(__file__).resolve().parents[2] / 'FCLiveEditor.DLL'
blob = path.read_bytes()
pe = pefile.PE(data=blob)
base = pe.OPTIONAL_HEADER.ImageBase
md = Cs(CS_ARCH_X86, CS_MODE_64)

if sys.argv[1] == 'disasm':
    address = int(sys.argv[2], 0)
    length = int(sys.argv[3], 0) if len(sys.argv) > 3 else 256
    offset = pe.get_offset_from_rva(address - base)
    for ins in md.disasm(blob[offset:offset + length], address):
        print(f'{ins.address:X}  {ins.mnemonic:8} {ins.op_str}')
elif sys.argv[1] == 'refs':
    needle = sys.argv[2].encode()
    addresses = {}
    for match in re.finditer(re.escape(needle), blob):
        target = base + pe.get_rva_from_offset(match.start())
        addresses[target] = match.start()
        print(f'string {target:X}')
    sec = next(s for s in pe.sections if s.Name.rstrip(b'\0') == b'.text')
    code = sec.get_data()
    va = base + sec.VirtualAddress
    for match in re.finditer(rb'[\x48\x4c]\x8d[\x05\x0d\x15\x1d\x25\x2d\x35\x3d]', code):
        pos = match.start()
        target = va + pos + 7 + struct.unpack_from('<i', code, pos + 3)[0]
        if target in addresses:
            print(f'ref {va + pos:X} -> {target:X}')
    for pos in range(len(code) - 4):
        target = va + pos + 4 + struct.unpack_from('<i', code, pos)[0]
        if target in addresses:
            print(f'displacement_end {va + pos + 4:X} -> {target:X}')
