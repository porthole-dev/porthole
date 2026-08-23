#!/usr/bin/env python3
# scope: device:google-taimen
# needs: -  (host only, no device)
# env: -
# exits: 0 ok · non-zero on failure
"""Invert the SoftwareIsp pipeline over a crop to recover raw (black-subtracted,
normalised) channel ratios, and report the AWB gains that WOULD neutralise it.

pipeline: out = ((CCM * clamp(gain*((raw-bl)/(1-bl)))) ) ** (1/2.2)
"""
import sys

def inv3(m):
    a,b,c,d,e,f,g,h,i = m
    det = a*(e*i-f*h) - b*(d*i-f*g) + c*(d*h-e*g)
    return [(e*i-f*h)/det, (c*h-b*i)/det, (b*f-c*e)/det,
            (f*g-d*i)/det, (a*i-c*g)/det, (c*d-a*f)/det,
            (d*h-e*g)/det, (b*g-a*h)/det, (a*e-b*d)/det]

def mul(m, v):
    return [m[0]*v[0]+m[1]*v[1]+m[2]*v[2],
            m[3]*v[0]+m[4]*v[1]+m[5]*v[2],
            m[6]*v[0]+m[7]*v[1]+m[8]*v[2]]

def linmean(path, w, h, crop):
    lut = [(v/255.0)**2.2 for v in range(256)]
    with open(path,'rb') as f: d = f.read()
    stride = len(d)//h
    cx,cy,cw,ch = crop
    s=[0.0,0.0,0.0]; n=0
    for y in range(cy, cy+ch):
        row = d[y*stride+cx*4 : y*stride+(cx+cw)*4]
        for c in range(3):
            s[c] += sum(lut[v] for v in row[c::4])
        n += cw
    return [x/n for x in s]

path, w, h = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
crop = tuple(int(v) for v in sys.argv[4].split(','))
ccm  = [float(v) for v in sys.argv[5].split(',')]
gain = [float(v) for v in sys.argv[6].split(',')]

out = linmean(path, w, h, crop)
pre = mul(inv3(ccm), out)                 # after gains, before CCM
raw = [pre[i]/gain[i] for i in range(3)]  # black-subtracted normalised raw
print("linear out RGB      : %.4f %.4f %.4f   R/G %.3f  B/G %.3f" % (out[0],out[1],out[2],out[0]/out[1],out[2]/out[1]))
print("pre-CCM (post-gain) : %.4f %.4f %.4f   R/G %.3f  B/G %.3f" % (pre[0],pre[1],pre[2],pre[0]/pre[1],pre[2]/pre[1]))
print("recovered raw       : %.4f %.4f %.4f   R/G %.3f  B/G %.3f" % (raw[0],raw[1],raw[2],raw[0]/raw[1],raw[2]/raw[1]))
print("gains AWB used      : R %.3f  B %.3f" % (gain[0], gain[2]))
print("gains for neutral   : R %.3f  B %.3f" % (raw[1]/raw[0], raw[1]/raw[2]))
print("AWB gain error      : R x%.3f  B x%.3f" % (gain[0]/(raw[1]/raw[0]), gain[2]/(raw[1]/raw[2])))
