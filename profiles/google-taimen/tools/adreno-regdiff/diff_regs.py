import collections
from a5xx_regs import load, block
regs=load()
mesa={int(l,16) for l in open('mesa_offsets.txt')}; kern={int(l,16) for l in open('kernel_offsets.txt')}
code=collections.defaultdict(list); data=collections.defaultdict(list); fn_regs=collections.defaultdict(set)
for l in open('blob_hits.txt'):
    if l.startswith('#'): continue
    p=l.split()
    reg=int(p[2],16); cnt=int(p[3])
    if p[5].startswith('.'): data[reg].append((int(p[0],16),cnt)); continue
    code[reg].append((int(p[0],16),int(p[5],16),cnt,p[4]=='P')); fn_regs[int(p[5],16)].add(reg)
# expand PKT4 counts: header at reg with cnt covers reg..reg+cnt-1
blob=collections.defaultdict(lambda:{'code':0,'data':0,'hdr':0,'fns':set(),'cp':0})
for reg,sites in code.items():
    for a,f,cnt,cp in sites:
        for k in range(min(cnt,64)):
            e=blob[reg+k]; e['code']+=1; e['fns'].add(f); e['cp']+=cp
            if k==0: e['hdr']+=1
for reg,sites in data.items():
    for a,cnt in sites:
        for k in range(min(cnt,64)): blob[reg+k]['data']+=1
rows=[]
for reg,e in blob.items():
    if reg in mesa or reg in kern: continue
    name=regs.get(reg)
    # plausibility: header seen in code with correct count parity, or in a function that also hits other named regs
    nb=max((len(fn_regs[f]) for f in e['fns']),default=0)
    if name is None and not (0xe000<=reg<=0xefff or 0x0b00<=reg<=0x0cff): continue
    rows.append((block(name) if name else 'UNNAMED',reg,name or '?',e['hdr'],e['code'],e['data'],e['cp'],nb,sorted(e['fns'])))
rows.sort(key=lambda r:(r[0],r[1]))
with open('candidates.txt','w') as f:
    f.write('# blob writes NOT in mesa fd5 or kernel a5xx init.  hdr=#PKT4 headers starting at this reg, code=#code sites covering it,\n# data=#rodata/data words, cp=#sites with count parity OK, nb=max #distinct regs hit by the same function (context plausibility)\n')
    cur=None
    for b,reg,name,hdr,c,d,cp,nb,fns in rows:
        if b!=cur: f.write(f'\n== {b} ==\n'); cur=b
        f.write(f'{reg:04x} {name:40s} hdr={hdr:2d} code={c:2d} data={d:2d} cp={cp:2d} nb={nb:3d} fns={",".join(f"{x:x}" for x in fns[:6])}\n')
print(len(rows),'candidate offsets;', sum(1 for r in rows if r[2]!="?"),'named')
print('blob total distinct offsets',len(blob),'| in mesa',len([r for r in blob if r in mesa]),'| in kernel only',len([r for r in blob if r in kern and r not in mesa]))
