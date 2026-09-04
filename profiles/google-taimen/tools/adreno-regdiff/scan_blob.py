# scan llvm-objdump text for 32-bit constants that decode as a5xx CP_TYPE4 headers
import re, sys, struct, bisect
from a5xx_regs import load, block
regs=load()
funcs=[]
for l in open('funcs.txt'):
    a,n=l.split(); funcs.append((int(a,16),n))
funcs.sort(); faddr=[a for a,_ in funcs]
def func_of(a):
    i=bisect.bisect_right(faddr,a)-1
    return funcs[i] if i>=0 else (0,'?')
def par(v):  # 1 iff popcount even (mesa pm4_odd_parity_bit)
    return 1 if bin(v).count('1')%2==0 else 0
def decode(v):
    if v>>28!=4: return None
    reg=(v>>8)&0x3ffff; cnt=v&0x7f
    if reg>0xffff or cnt==0: return None
    rp=((v>>27)&1)==par(reg); cp=((v>>7)&1)==par(cnt)
    return reg,cnt,rp,cp
ins=re.compile(r'^\s*([0-9a-f]+):\s+(\S+)\s*(.*)$')
imm=re.compile(r'#(-?0x[0-9a-f]+|-?\d+)')
hits=[]
state={}
def rn(r): return r[1:] if r and r[0] in 'wx' else None
for line in open('gles.dis'):
    m=ins.match(line)
    if not m:
        state.clear(); continue
    addr=int(m.group(1),16); op=m.group(2); args=[a.strip() for a in m.group(3).split(',')] if m.group(3) else []
    if op in('b','bl','ret','br','blr','cbz','cbnz','tbz','tbnz') or op.startswith('b.'):
        if op in('ret','b','br'): state.clear()
        continue
    if not args: continue
    d=rn(args[0])
    val=None
    ims=imm.findall(m.group(3))
    if op in('mov','movz') and d and len(args)==2 and ims:
        val=int(ims[0],0)&0xffffffff
    elif op=='movk' and d and d in state and ims:
        sh=int(ims[1],0) if len(ims)>1 else 0
        val=(state[d]&~(0xffff<<sh))|((int(ims[0],0)&0xffff)<<sh)
    elif op in('orr','add','sub','eor') and d and len(args)>=3 and ims and rn(args[1]) in state:
        s=state[rn(args[1])]; i=int(ims[0],0)
        val={'orr':s|i,'add':s+i,'sub':s-i,'eor':s^i}[op]&0xffffffff
    elif op=='mov' and d and len(args)==2 and rn(args[1]) in state:
        val=state[rn(args[1])]
    if val is not None:
        state[d]=val
        dd=decode(val)
        if dd and dd[2]:
            reg,cnt,rp,cp=dd
            hits.append((addr,val,reg,cnt,cp))
    elif d and op not in('cmp','cmn','tst','str','strb','strh','stp','stur','prfm'):
        state.pop(d,None)
        if op in('ldp','stp') and len(args)>1: state.pop(rn(args[1]),None)
    if op in('bl','blr'): state.clear()
# also scan data sections for raw header words
import subprocess
OC='/home/user/.local/share/swiftly/bin/llvm-objcopy'
dhits=[]
for sec,vma in(('.rodata',0xa850),('.data.rel.ro',0x32e000),('.data',0x336c08)):
    subprocess.run([OC,'--dump-section',f'{sec}=sec.bin','../libGLESv2_adreno.so','/dev/null'],check=True)
    b=open('sec.bin','rb').read()
    for i in range(0,len(b)-3,4):
        v=struct.unpack_from('<I',b,i)[0]; dd=decode(v)
        if dd and dd[2] and dd[3]: dhits.append((vma+i,v,dd[0],dd[1],sec))
with open('blob_hits.txt','w') as f:
    f.write('# addr hdr reg cnt cntparity func name   (code)\n')
    for addr,val,reg,cnt,cp in hits:
        fa,fn=func_of(addr)
        f.write(f'{addr:08x} {val:08x} {reg:04x} {cnt:2d} {"P" if cp else "-"} {fa:08x} {regs.get(reg,"?")}\n')
    f.write('# data-section words\n')
    for a,v,reg,cnt,sec in dhits:
        f.write(f'{a:08x} {v:08x} {reg:04x} {cnt:2d} P {sec:>8} {regs.get(reg,"?")}\n')
print(len(hits),'code hits',len(dhits),'data hits')
known=sum(1 for h in hits if h[2] in regs); print(known,'code hits name a known a5xx reg')
# pass 2: runtime-built headers -- bare 16-bit reg immediate with the 0x9669 parity table
# or a 0x4000<<16 movk within 40 instructions in the same function (indexed regs, e.g. RB_MRT base + i*7)
lines=open('gles.dis').read().split('\n')
rt=[]
for i,l in enumerate(lines):
    m=re.match(r'^\s*([0-9a-f]+):\s+mov\s+w\d+, #(0x[0-9a-f]+)\s',l)
    if not m: continue
    v=int(m.group(2),16)
    if v not in regs or v<0x100: continue
    win='\n'.join(lines[max(0,i-40):i+40])
    if '#0x9669' in win or re.search(r'movk\s+w\d+, #0x4000, lsl #16',win):
        rt.append((int(m.group(1),16),v))
with open('blob_hits.txt','a') as f:
    f.write('# runtime-built headers (bare reg immediate + parity code nearby)\n')
    for a,v in rt:
        fa,fn=func_of(a); f.write(f'{a:08x} -------- {v:04x}  1 R {fa:08x} {regs.get(v)}\n')
print(len(rt),'runtime-header hits')
