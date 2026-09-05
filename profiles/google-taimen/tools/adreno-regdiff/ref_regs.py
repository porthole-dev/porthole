# S_mesa (fd5 gallium driver) and S_kernel (a5xx_gpu/power/preempt writes) as offset sets
import os, re, glob, xml.etree.ElementTree as ET
from a5xx_regs import XML, WORKDIR
M = os.environ.get('MESA_SRC', os.path.join(
    WORKDIR, 'ref/mesa-26.1.6/src/gallium/drivers/freedreno/a5xx/'))
K = os.environ.get('KERNEL_SRC', os.path.join(
    WORKDIR, 'linux-ws/drivers/gpu/drm/msm/adreno/'))
def names():
    root=ET.parse(XML).getroot(); strip=lambda t:t.split('}')[-1]; nm={}
    for dom in root.iter():
        if strip(dom.tag)!='domain' or dom.get('name')!='A5XX': continue
        for el in dom:
            t=strip(el.tag)
            if t in('reg32','reg64'): nm.setdefault(el.get('name'),set()).add(int(el.get('offset'),0))
            elif t=='array':
                b=int(el.get('offset'),0); s=int(el.get('stride'),0); n=int(el.get('length'),0)
                for i in range(n):
                    nm.setdefault(el.get('name'),set()).add(b+i*s)
                    for c in el:
                        if strip(c.tag) in('reg32','reg64'):
                            nm.setdefault(el.get('name')+'_'+c.get('name'),set()).add(b+i*s+int(c.get('offset'),0))
    return nm
NM=names()
def expand(name,cnt,out,src):
    offs=NM.get(name)
    if offs is None: print('unmapped',name,src); return
    for o in offs:
        for k in range(cnt): out.add(o+k)
def mesa():
    out=set()
    for f in glob.glob(M+'*.[ch]'):
        txt=open(f).read()
        for m in re.finditer(r'OUT_PKT4\(\w+,\s*REG_A5XX_(\w+)(?:\([^)]*\))?,\s*(\d+)\)',txt): expand(m.group(1),int(m.group(2)),out,f)
        for m in re.finditer(r'fd5_emit_shader_obj\([^;]*?REG_A5XX_(\w+)\)',txt): expand(m.group(1),6,out,f)  # OUT_PKT4(ring, shader_obj_reg, 6)
        for m in re.finditer(r'REG_A5XX_(\w+)',txt):
            if m.group(1) in NM: expand(m.group(1),1,out,f)
    return out
def kernel():
    out=set()
    for f in('a5xx_gpu.c','a5xx_power.c','a5xx_preempt.c'):
        txt=open(K+f).read()
        txt=re.sub(r'static const u32 a5xx_registers\[\].*?\};','',txt,flags=re.S)  # snapshot read list
        for m in re.finditer(r'gpu_write(64)?\(gpu,\s*REG_A5XX_(\w+)',txt): expand(m.group(2),2 if m.group(1) else 1,out,f)
        for m in re.finditer(r'\{\s*REG_A5XX_(\w+),\s*0x',txt): expand(m.group(1),1,out,f)
        for m in re.finditer(r'OUT_RING\(ring,\s*REG_A5XX_(\w+)',txt): expand(m.group(1),1,out,f)  # CP_REG_TO_MEM etc
    return out
if __name__=='__main__':
    ms=mesa(); ks=kernel()
    open('mesa_offsets.txt','w').write('\n'.join(f'{o:04x}' for o in sorted(ms))+'\n')
    open('kernel_offsets.txt','w').write('\n'.join(f'{o:04x}' for o in sorted(ks))+'\n')
    print(len(ms),'mesa offsets',len(ks),'kernel offsets')
