"""Component 2 — production & distribution optimiser (MILP, solved with HiGHS).
Reading: each SKU's TOTAL output is a whole number of 25 kL batches, split freely across plants.
Contractual SKUs and hub safety stock are SOFT penalties, so the model always returns a plan."""
import pulp

def build_netd(data):
    return {k:max(0.0, data['demand'][k]-data['openinv'].get(k,0.0)) for k in data['keys']}

def solve_plan(data, SS_hub, contractual_mult=3.0, hub_ss_penalty_frac=0.5, batch=25,
               restrict_natural=False, solver_time=60, gap=0.005, msg=False):
    P,H,S=data['plants'],data['hubs'],data['skus']
    cap,pc,tph,thc=data['cap'],data['prodcost'],data['tph'],data['thc']
    line,pen,contr=data['sku_line'],data['penalty'],data['contractual']
    keys=data['keys']; netd=build_netd(data); NAT=data['natural']
    cfas_of={}
    for (s,c) in keys: cfas_of.setdefault(s,[]).append(c)
    epen={s:pen[s]*(contractual_mult if contr[s] else 1.0) for s in S}
    hubs_for=lambda c:[NAT[c]] if restrict_natural else H

    m=pulp.LpProblem('Levisol',pulp.LpMinimize)
    X={(s,p):pulp.LpVariable(f'X_{s}_{p}',lowBound=0) for s in S for p in P}
    Nb={s:pulp.LpVariable(f'N_{s}',lowBound=0,cat='Integer') for s in S}
    Fv={(s,p,h):pulp.LpVariable(f'F_{s}_{p}_{h}',lowBound=0) for s in S for p in P for h in H}
    Gv={(s,h,c):pulp.LpVariable(f'G_{s}_{h}_{c}',lowBound=0) for (s,c) in keys for h in hubs_for(c)}
    U={(s,c):pulp.LpVariable(f'U_{s}_{c}',lowBound=0) for (s,c) in keys}
    SH={(s,h):pulp.LpVariable(f'SH_{s}_{h}',lowBound=0) for s in S for h in H}
    Ih={(s,h):pulp.LpVariable(f'I_{s}_{h}',lowBound=0) for s in S for h in H}

    m += (pulp.lpSum(X[(s,p)]*pc[p] for s in S for p in P)
          + pulp.lpSum(Fv[(s,p,h)]*tph[(p,h)] for s in S for p in P for h in H)
          + pulp.lpSum(Gv[k]*thc[(k[1],k[2])] for k in Gv)
          + pulp.lpSum(U[(s,c)]*epen[s] for (s,c) in keys)
          + pulp.lpSum(SH[(s,h)]*epen[s]*hub_ss_penalty_frac for s in S for h in H))
    for s in S: m += pulp.lpSum(X[(s,p)] for p in P)==batch*Nb[s]
    for p in P:
        for ln in data['lines']:
            m += pulp.lpSum(X[(s,p)] for s in S if line[s]==ln) <= cap[(p,ln)]
    for s in S:
        for p in P: m += pulp.lpSum(Fv[(s,p,h)] for h in H)==X[(s,p)]
        for h in H:
            out=pulp.lpSum(Gv[(s,h,c)] for c in cfas_of.get(s,[]) if (s,h,c) in Gv)
            m += Ih[(s,h)]==pulp.lpSum(Fv[(s,p,h)] for p in P)-out
            m += SH[(s,h)]>=SS_hub.get((s,h),0.0)-Ih[(s,h)]
    for (s,c) in keys:
        m += pulp.lpSum(Gv[(s,h,c)] for h in hubs_for(c))+U[(s,c)]==netd[(s,c)]

    m.solve(pulp.HiGHS(msg=msg,timeLimit=solver_time,gapRel=gap))
    v=lambda x:x.value() or 0.0
    costs=dict(production=sum(v(X[(s,p)])*pc[p] for s in S for p in P),
               transport_ph=sum(v(Fv[(s,p,h)])*tph[(p,h)] for s in S for p in P for h in H),
               transport_hc=sum(v(Gv[k])*thc[(k[1],k[2])] for k in Gv),
               penalty_unmet=sum(v(U[(s,c)])*pen[s] for (s,c) in keys),
               hub_ss_shortfall=sum(v(SH[(s,h)])*pen[s]*hub_ss_penalty_frac for s in S for h in H))
    costs['total']=sum(costs.values())
    prod=[(s,p,line[s],v(X[(s,p)]),round(v(X[(s,p)])/batch)) for s in S for p in P if v(X[(s,p)])>1e-6]
    rph=[(s,p,h,v(Fv[(s,p,h)])) for s in S for p in P for h in H if v(Fv[(s,p,h)])>1e-6]
    rhc=[(s,h,c,v(Gv[k])) for k in Gv for (s,h,c) in [k] if v(Gv[k])>1e-6]
    unmet=[(s,c,v(U[(s,c)]),pen[s],contr[s]) for (s,c) in keys if v(U[(s,c)])>1e-6]
    hubss=[(s,h,v(Ih[(s,h)]),SS_hub.get((s,h),0.0),v(SH[(s,h)])) for s in S for h in H
           if SS_hub.get((s,h),0)>0 or v(Ih[(s,h)])>1e-6]
    return dict(status=pulp.LpStatus[m.status],costs=costs,prod=prod,rph=rph,rhc=rhc,
                unmet=unmet,hubss=hubss,
                total_produced=sum(v(X[(s,p)]) for s in S for p in P),
                total_unmet_kl=sum(v(U[(s,c)]) for (s,c) in keys),
                net_demand=sum(netd.values()),
                n_batches=sum(round(v(Nb[s])) for s in S))
