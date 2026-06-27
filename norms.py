"""Component 1 — inventory norms. Computes safety stock, reorder point, days of cover
per SKU x CFA and per SKU x Hub, from sales/forecast history and lead-time data."""
import pandas as pd, numpy as np
from scipy.stats import norm
from scipy.optimize import brentq
from data_loader import _load, _sheetname, _line, NATURAL

def _Gloss(k): return norm.pdf(k)-k*(1-norm.cdf(k))
def _k_fill(beta,Q,sig):
    if sig<=0 or Q<=0: return 0.0
    t=(1-beta)*Q/sig
    if t<=_Gloss(12):return 12.0
    if t>=_Gloss(-6):return 0.0
    return max(0.0,brentq(lambda k:_Gloss(k)-t,-6,12))

TIER_FILL={'A':0.98,'B':0.97,'C':0.92,'D':0.92}

def compute_norms(file, hub_service=0.98):
    xl=pd.ExcelFile(file); sn=lambda k:_sheetname(xl,k)
    G=_load(xl,sn('Sales'),'Product Name'); G=G[G['Product Name'].astype(str).str.startswith('SKU')]
    H=_load(xl,sn('Forecast'),'Product Name'); H=H[H['Product Name'].astype(str).str.startswith('SKU')]
    E=_load(xl,sn('Source'),'Product Name'); E=E[E['Product Name'].astype(str).str.startswith('SKU')]
    gm=[c for c in G.columns if 'in kL' in c or '-25' in c or '-26' in c]
    hm=[c for c in H.columns if 'in kL' in c or '-25' in c or '-26' in c]
    for m in gm:G[m]=pd.to_numeric(G[m],errors='coerce')
    for m in hm:H[m]=pd.to_numeric(H[m],errors='coerce')
    key=['Product Name','CFA']
    Gm=G.melt(key,gm,'month','actual'); Hm=H.melt(key,hm,'month','forecast')
    Gm['month']=Gm['month'].str[:6]; Hm['month']=Hm['month'].str[:6]
    M=Gm.merge(Hm,on=key+['month']); M['err']=M['actual']-M['forecast']
    st=M.groupby(key).agg(mean_monthly=('actual','mean'),sd_ferr=('err','std')).reset_index()
    st['d_daily']=st['mean_monthly']/30; st['sd_d_daily']=st['sd_ferr']/np.sqrt(30)

    for k,c in {'ph':'LT (Plant to Hub)(in  days)','hc':'LT (Hub to CFA ) (in  days)',
                'prod':'Production lead time (in  days)','pvar':'Production variability (in  days)',
                'tvar':'Transit lead variability (in  days)'}.items():
        E[k]=pd.to_numeric(E[c],errors='coerce')
    E['L_mean']=E['ph']+E['hc']+E['prod']; E['L_sd']=np.sqrt(E['pvar']**2+E['tvar']**2)
    E['Lhub']=E['ph']+E['prod']; E['Lhub_sd']=np.sqrt(E['pvar']**2+(E['tvar']/2)**2)
    df=st.merge(E[key+['L_mean','L_sd','Lhub','Lhub_sd']],on=key,how='left')

    # tiers by 6-mo volume
    G['t6']=G[gm].sum(axis=1); sv=G.groupby('Product Name')['t6'].sum().sort_values(ascending=False)
    cum=sv.cumsum()/sv.sum()
    tmap=cum.apply(lambda c:'A' if c<=.5 else 'B' if c<=.8 else 'C' if c<=.95 else 'D')
    df['Tier']=df['Product Name'].map(tmap); df['fill']=df['Tier'].map(TIER_FILL)
    df['Hub']=df['CFA'].map(NATURAL)

    df['sigma_dLT']=np.sqrt(df['L_mean']*df['sd_d_daily']**2 + df['d_daily']**2*df['L_sd']**2)
    df['k']=df.apply(lambda r:_k_fill(r['fill'],r['mean_monthly'],r['sigma_dLT']),axis=1)
    df['SS']=df['k']*df['sigma_dLT']
    df['ROP']=df['d_daily']*df['L_mean']+df['SS']
    df['DoC']=np.where(df['d_daily']>0,df['ROP']/df['d_daily'],0)

    # hub norms via risk pooling
    rows=[]
    for (s,h),g in df.groupby(['Product Name','Hub']):
        mm=g['mean_monthly'].sum()
        sd_day=np.sqrt((g['sd_ferr']**2).sum())/np.sqrt(30)
        w=g['mean_monthly']/mm if mm>0 else np.ones(len(g))/len(g)
        L=(w*g['Lhub']).sum(); Lsd=np.sqrt((w*g['Lhub_sd']**2).sum()); d=mm/30
        sig=np.sqrt(L*sd_day**2+d**2*Lsd**2)
        k=_k_fill(hub_service,mm,sig); ss=k*sig
        rows.append(dict(SKU=s,Hub=h,Tier=g['Tier'].iloc[0],mean_monthly=mm,sigma_dLT=sig,
                         L_mean=L,k=k,SS=ss,ROP=d*L+ss,DoC=(d*L+ss)/d if d>0 else 0))
    hub=pd.DataFrame(rows)
    cfa=df.rename(columns={'Product Name':'SKU'})[['SKU','Tier','CFA','Hub','mean_monthly','d_daily',
            'sd_ferr','L_mean','L_sd','sigma_dLT','fill','k','SS','ROP','DoC']]
    SS_hub={(r['SKU'],r['Hub']):float(r['SS']) for _,r in hub.iterrows()}
    return cfa, hub, SS_hub
