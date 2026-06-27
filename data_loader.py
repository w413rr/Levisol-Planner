"""Loads the Levisol case data file into clean Python structures.
Works with any month's file that follows the same sheet layout."""
import pandas as pd, numpy as np, re

LINES=['<=1.5 LT','3-5 LT','7-20 LT','50 LT','180-210 LT']
CAPCOL={'<=1.5 LT':'Line Capacity \n<=1.5 LT (kl / month)','3-5 LT':'Line Capacity \n3- 5 LT (kl / month)',
        '7-20 LT':'Line Capacity \n7- 20 LT (kl / month)','50 LT':'Line Capacity \n50 LT (kl / month)',
        '180-210 LT':'Line Capacity \n180- 210LT (kl / month)'}
PNAME={'Mumbai':'BOM','Ahmedabad':'AHM','Kolkata':'KOL'}
NATURAL={'Guwahati CFA':'MHE','Kolkata CFA':'MHE','Jamshedpur CFA':'MHE','Kanpur CFA':'MHE',
         'Haryana CFA':'MHW','Rajpura CFA':'MHW','Bhiwandi CFA':'MHW','Ahmedabad CFA':'MHW',
         'Bangalore CFA':'MHW','Hyderabad CFA':'MHW'}

def _load(xl, sheet, header_key):
    raw=pd.read_excel(xl,sheet_name=sheet,header=None)
    hdr=None
    for i,row in raw.iterrows():
        if any(str(v).strip()==header_key for v in row.tolist()): hdr=i;break
    cols=[str(v).strip() for v in raw.iloc[hdr].tolist()]
    df=raw.iloc[hdr+1:].copy(); df.columns=cols
    return df.dropna(how='all').reset_index(drop=True)

def _per_unit(p):
    m=re.match(r'\s*\d+\s*[xX]\s*([\d.]+)\s*(ML|LT|KG|L)',str(p))
    if not m:return None,None
    v=float(m.group(1));u=m.group(2).upper()
    return (v/1000.0,'ML') if u=='ML' else (v,u)

def _line(p):
    L,u=_per_unit(p)
    if u is None:return 'NA'
    if u=='KG':return '180-210 LT'
    if L<=1.5:return '<=1.5 LT'
    if 3<=L<=5:return '3-5 LT'
    if 7<=L<=20:return '7-20 LT'
    if L==50:return '50 LT'
    if 180<=L<=210:return '180-210 LT'
    return 'NA'

def _sheetname(xl,contains):
    for s in pd.ExcelFile(xl).sheet_names:
        if contains.lower() in s.lower(): return s
    raise KeyError(contains)

def load_data(file):
    """file: path or file-like buffer of the case .xlsx. Returns dict of model inputs."""
    xl=pd.ExcelFile(file)
    sn=lambda k:_sheetname(xl,k)
    A=_load(xl,sn('Plants'),'Plant Code'); A=A[A['Plant Code'].isin(['BOM','AHM','KOL'])]
    plants=list(A['Plant Code'])
    prodcost={r['Plant Code']:float(r['Production Cost (₹/kl)']) for _,r in A.iterrows()}
    cap={(r['Plant Code'],ln):float(r[CAPCOL[ln]]) for _,r in A.iterrows() for ln in LINES}

    B=_load(xl,sn('Plant-Hub'),'From Plant'); B=B[B['From Plant'].isin(PNAME)]
    tph={}
    for _,r in B.iterrows():
        p=PNAME[r['From Plant'].strip()]
        tph[(p,'MHW')]=float(r['To Mother Hub West (MHW)']); tph[(p,'MHE')]=float(r['To Mother Hub East (MHE)'])

    C=_load(xl,sn('Hub-CFA'),'CFA'); C=C[C['CFA'].notna() & ~C['CFA'].astype(str).str.contains('supply|restriction|hub',case=False,na=False)]
    thc={}
    for _,r in C.iterrows():
        c=str(r['CFA']).strip(); c=c if c.endswith('CFA') else c+' CFA'
        tlw=float(r['From Mother Hub West (MHW)']); tle=float(r['From Mother Hub East (MHE)'])
        thc[('MHW',c)]=tlw; thc[('MHE',c)]=tle

    D=_load(xl,sn('Penalty'),'Product Name'); D=D[D['Product Name'].astype(str).str.startswith('SKU')]
    sku_line={r['Product Name']:_line(r['Pack size']) for _,r in D.iterrows()}
    penalty={r['Product Name']:float(r['Penalty cost (per kL)']) for _,r in D.iterrows()}
    contractual={r['Product Name']:str(r['Contractual?']).strip().upper().startswith('YES') for _,r in D.iterrows()}
    packsize={r['Product Name']:str(r['Pack size']) for _,r in D.iterrows()}
    skus=list(D['Product Name'])

    J=_load(xl,sn('Jan'),'Product Name'); J=J[J['Product Name'].astype(str).str.startswith('SKU')]
    jcol=[c for c in J.columns if '2026' in c or 'Jan' in c][-1]
    demand={(r['Product Name'],str(r['CFA']).strip()):float(r[jcol]) for _,r in J.iterrows()}

    try:
        I=_load(xl,sn('opening'),'Product Name'); I=I[I['Product Name'].astype(str).str.startswith('SKU')]
        icol=[c for c in I.columns if '2026' in c or 'Jan' in c][-1]; I=I.drop_duplicates(['Product Name','CFA'])
        openinv={(r['Product Name'],str(r['CFA']).strip()):float(r[icol]) for _,r in I.iterrows()}
    except Exception: openinv={}

    cfas=sorted({c for (_,c) in demand}); keys=list(demand.keys())
    return dict(plants=plants,hubs=['MHW','MHE'],cfas=cfas,skus=skus,lines=LINES,
                prodcost=prodcost,cap=cap,tph=tph,thc=thc,sku_line=sku_line,
                penalty=penalty,contractual=contractual,packsize=packsize,
                demand=demand,openinv=openinv,keys=keys,natural=NATURAL,
                # raw history sheets kept for norms
                _xl=file)
