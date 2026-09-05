# offset -> name map for the A5XX domain of mesa's a5xx.xml (arrays expanded)
import os, xml.etree.ElementTree as ET, re, sys
# WORKDIR is the same porthole-workspace root `porthole build` resolves
# PORTHOLE_KERNEL_TREE against; MESA_XML overrides the whole path outright.
WORKDIR = os.environ.get('PORTHOLE_WORKDIR', os.path.expanduser('~'))
XML = os.environ.get('MESA_XML', os.path.join(
    WORKDIR, 'ref/mesa-26.1.6/src/freedreno/registers/adreno/a5xx.xml'))
def load():
    ns={'r':'http://nouveau.freedesktop.org/'}
    root=ET.parse(XML).getroot()
    regs={}
    def strip(t): return t.split('}')[-1]
    for dom in root.iter():
        if strip(dom.tag)!='domain' or dom.get('name')!='A5XX': continue
        for el in dom:
            tag=strip(el.tag)
            if tag in('reg32','reg64'):
                off=int(el.get('offset'),0); regs.setdefault(off,el.get('name'))
                if tag=='reg64': regs.setdefault(off+1,el.get('name')+'_HI')
            elif tag=='array':
                base=int(el.get('offset'),0); stride=int(el.get('stride'),0); n=int(el.get('length'),0)
                subs=[(int(c.get('offset'),0),c.get('name'),strip(c.tag)) for c in el if strip(c.tag) in('reg32','reg64')]
                if not subs: subs=[(0,'REG','reg32')]
                for i in range(n):
                    for so,sn,st in subs:
                        nm=f"{el.get('name')}[{i}].{sn}" if sn!='REG' else f"{el.get('name')}[{i}]"
                        regs.setdefault(base+i*stride+so,nm)
                        if st=='reg64': regs.setdefault(base+i*stride+so+1,nm+'_HI')
    return regs
def block(name):
    m=re.match(r'([A-Z0-9]+)_',name); return m.group(1) if m else '?'
if __name__=='__main__':
    r=load(); print(len(r),'offsets')
    for o in sorted(r)[:5]+sorted(r)[-5:]: print(hex(o),r[o])
    for q in (0xe140,0xe094,0xe1b0,0xe150,0xe157,0x0bd1,0x0be2): print(hex(q),r.get(q))
