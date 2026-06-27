"""
Levisol Supply Chain Planner — Streamlit tool
Run with:  streamlit run app.py
A planner edits inputs on the left, clicks "Run plan", and reads the results on the right.
No coding knowledge needed.
"""
import streamlit as st
import pandas as pd
import numpy as np
import copy, io, time

from data_loader import load_data
from norms import compute_norms
from optimizer import solve_plan, build_netd
import coords

st.set_page_config(page_title="Levisol Supply Chain Planner", layout="wide", page_icon="🛢️")

DEFAULT_FILE = "Levisol_data.xlsx"

# ----------------------------- data loading (cached) -----------------------------
@st.cache_data(show_spinner=False)
def _load_everything(file_bytes_or_path):
    data = load_data(file_bytes_or_path)
    cfa_norms, hub_norms, SS_hub = compute_norms(file_bytes_or_path)
    return data, cfa_norms, hub_norms, SS_hub

def rupees(x):
    return f"₹{x:,.0f}"

# trim Streamlit's default top padding so the title sits near the top
st.markdown("""
<style>
    .block-container { padding-top: 1.4rem; padding-bottom: 2rem; }
    div[data-testid="stExpander"] details { border-radius: 8px; }
</style>
""", unsafe_allow_html=True)

# ----------------------------- header -----------------------------
st.title("🛢️ Levisol Supply Chain Planner")
st.caption("Edit the inputs on the left, press **Run plan**, and read your production, routing, cost and "
           "inventory-norm results on the right. Everything updates from the data file you load.")

# ----------------------------- sidebar: data + policy -----------------------------
with st.sidebar:
    st.header("1 · Data")
    up = st.file_uploader("Upload a month's data file (.xlsx). Leave empty to use the bundled sample.",
                          type=["xlsx"])
    source = up if up is not None else DEFAULT_FILE
    try:
        base_data, cfa_norms, hub_norms, SS_hub = _load_everything(
            up.getvalue() if up is not None else DEFAULT_FILE)
    except Exception as e:
        st.error(f"Could not read the data file: {e}")
        st.stop()
    st.success(f"Loaded {len(base_data['skus'])} SKUs · {len(base_data['cfas'])} CFAs · "
               f"{len(base_data['keys'])} demand lines")

    st.header("2 · Policy settings")
    contractual_mult = st.slider("Contractual protection (× penalty)", 1.0, 10.0, 3.0, 0.5,
        help="How strongly to protect contractual SKUs. Higher = shorted last. They are never a hard "
             "constraint, so the plan never becomes infeasible.")
    hub_frac = st.slider("Hub safety-stock importance", 0.0, 1.0, 0.5, 0.1,
        help="How much to value holding the hub buffer vs. spending to build it.")
    gap = st.select_slider("Solver precision", options=[0.05, 0.02, 0.01, 0.005, 0.001], value=0.005,
        help="Smaller = more exact, slightly slower. 0.5% is plenty for planning.")
    run = st.button("▶  Run plan", type="primary", use_container_width=True)
    if st.button("↺  Reset inputs to loaded file", use_container_width=True,
                 help="Discard all edits and restore every value from the loaded data file."):
        for k in ["cap_editor", "ph_editor", "hc_editor"]:
            st.session_state.pop(k, None)
        for c in base_data["cfas"]:
            st.session_state.pop(f"dm_{c}", None)
        st.rerun()

# ----------------------------- editable inputs -----------------------------
st.subheader("Adjust inputs (optional)")
st.caption("Change any number below, then press **Run plan**. Use the ⤢ icon on a table to expand it. "
           "Blank cells keep the original value.")

# Row 1: the wide table (cost + 5 capacity columns) gets the full width
with st.expander("Plant production cost & line capacity (kL/month)", expanded=True):
    cap_rows = []
    for p in base_data["plants"]:
        row = {"Plant": p, "₹/kL": base_data["prodcost"][p]}
        for ln in base_data["lines"]:
            row[ln] = base_data["cap"][(p, ln)]
        cap_rows.append(row)
    cap_df = st.data_editor(pd.DataFrame(cap_rows), hide_index=True, use_container_width=True,
                            num_rows="fixed", height=145, key="cap_editor")

# Row 2: the two transport tables side by side, each filling its column
tc1, tc2 = st.columns(2)
with tc1:
    with st.expander("Transport cost — Plant → Hub (₹/kL)", expanded=True):
        ph_rows = [{"Plant": p, "→ MHW": base_data["tph"][(p, "MHW")],
                    "→ MHE": base_data["tph"][(p, "MHE")]} for p in base_data["plants"]]
        ph_df = st.data_editor(pd.DataFrame(ph_rows), hide_index=True, use_container_width=True,
                               num_rows="fixed", height=145, key="ph_editor")
with tc2:
    with st.expander("Transport cost — Hub → CFA (₹/kL)", expanded=True):
        hc_rows = [{"CFA": c, "from MHW": base_data["thc"][("MHW", c)],
                    "from MHE": base_data["thc"][("MHE", c)]} for c in base_data["cfas"]]
        hc_df = st.data_editor(pd.DataFrame(hc_rows), hide_index=True, use_container_width=True,
                               num_rows="fixed", height=388, key="hc_editor")

with st.expander("Demand adjustment — quick what-if by CFA (multiplier)", expanded=False):
    st.caption("e.g. 1.20 = +20% demand at that warehouse. Use for fast scenario testing.")
    dm_cols = st.columns(5)
    demand_mult = {}
    for i, c in enumerate(base_data["cfas"]):
        with dm_cols[i % 5]:
            demand_mult[c] = st.number_input(c.replace(" CFA", ""), 0.0, 5.0, 1.0, 0.05, key=f"dm_{c}")

# ----------------------------- assemble edited data -----------------------------
def assemble_data():
    d = copy.deepcopy(base_data)
    for _, r in cap_df.iterrows():
        p = r["Plant"]; d["prodcost"][p] = float(r["₹/kL"])
        for ln in base_data["lines"]:
            d["cap"][(p, ln)] = float(r[ln])
    for _, r in ph_df.iterrows():
        p = r["Plant"]; d["tph"][(p, "MHW")] = float(r["→ MHW"]); d["tph"][(p, "MHE")] = float(r["→ MHE"])
    for _, r in hc_df.iterrows():
        c = r["CFA"]; d["thc"][("MHW", c)] = float(r["from MHW"]); d["thc"][("MHE", c)] = float(r["from MHE"])
    for k in list(d["demand"].keys()):
        s, c = k
        d["demand"][k] = base_data["demand"][k] * demand_mult.get(c, 1.0)
    return d

def list_changes():
    """Return a list of human-readable edits vs the loaded file."""
    ch = []
    for _, r in cap_df.iterrows():
        p = r["Plant"]
        if float(r["₹/kL"]) != base_data["prodcost"][p]:
            ch.append(f"{p} cost → ₹{float(r['₹/kL']):,.0f}/kL")
        for ln in base_data["lines"]:
            if float(r[ln]) != base_data["cap"][(p, ln)]:
                ch.append(f"{p} {ln} capacity → {float(r[ln]):,.0f} kL")
    for _, r in ph_df.iterrows():
        p = r["Plant"]
        if float(r["→ MHW"]) != base_data["tph"][(p, "MHW")]:
            ch.append(f"{p}→MHW transport → ₹{float(r['→ MHW']):,.0f}")
        if float(r["→ MHE"]) != base_data["tph"][(p, "MHE")]:
            ch.append(f"{p}→MHE transport → ₹{float(r['→ MHE']):,.0f}")
    for _, r in hc_df.iterrows():
        c = r["CFA"]
        if float(r["from MHW"]) != base_data["thc"][("MHW", c)]:
            ch.append(f"MHW→{c} transport → ₹{float(r['from MHW']):,.0f}")
        if float(r["from MHE"]) != base_data["thc"][("MHE", c)]:
            ch.append(f"MHE→{c} transport → ₹{float(r['from MHE']):,.0f}")
    for c in base_data["cfas"]:
        m = demand_mult.get(c, 1.0)
        if m != 1.0:
            ch.append(f"{c.replace(' CFA','')} demand ×{m:.2f}")
    return ch

_changes = list_changes()
if _changes:
    st.warning("✏️ **Inputs differ from the loaded file** ("
               + str(len(_changes)) + " change" + ("s" if len(_changes) != 1 else "")
               + "). Press **Run plan** to apply. Use **Reset** in the sidebar to undo.")
    with st.expander(f"See the {len(_changes)} changed input(s)"):
        for c in _changes:
            st.write("• " + c)

# ----------------------------- run -----------------------------
if run:
    d = assemble_data()
    with st.spinner("Optimising production & distribution…"):
        t0 = time.time()
        R = solve_plan(d, SS_hub, contractual_mult=contractual_mult,
                       hub_ss_penalty_frac=hub_frac, gap=gap, solver_time=60)
        R["_elapsed"] = time.time() - t0
        R["_data"] = d
        R["_changes"] = _changes
    # keep previous as baseline for comparison
    if "current" in st.session_state:
        st.session_state["baseline"] = st.session_state["current"]
    st.session_state["current"] = R

R = st.session_state.get("current")

if R is None:
    st.info("Set your inputs on the left and press **Run plan** to generate a plan.")
    st.stop()

# ----------------------------- status banner -----------------------------
c = R["costs"]
if R["total_unmet_kl"] > 0.05:
    st.warning(f"⚠️ Plan returned with **{R['total_unmet_kl']:.1f} kL of demand unmet** "
               f"(penalty {rupees(c['penalty_unmet'])}). See the **Unmet demand** tab for exactly what and why.")
else:
    st.success("✅ All demand met within capacity.")

# ----------------------------- KPI row -----------------------------
k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Total cost", rupees(c["total"]))
k2.metric("Produced", f"{R['total_produced']:,.0f} kL")
k3.metric("Net demand", f"{R['net_demand']:,.0f} kL")
k4.metric("Unmet", f"{R['total_unmet_kl']:.1f} kL")
k5.metric("Solve time", f"{R.get('_elapsed',0):.1f} s")

# ----------------------------- tabs -----------------------------
tabs = st.tabs(["💰 Cost", "🏭 Production", "🚚 Routing", "🗺️ Map", "📦 Inventory norms",
                "❗ Unmet demand", "🔀 Compare scenarios"])

# --- Cost ---
with tabs[0]:
    st.subheader("Cost breakdown")
    cost_df = pd.DataFrame({
        "Component": ["Production", "Transport: Plant→Hub", "Transport: Hub→CFA",
                      "Penalty: unmet demand", "Hub safety-stock shortfall"],
        "₹": [c["production"], c["transport_ph"], c["transport_hc"],
              c["penalty_unmet"], c["hub_ss_shortfall"]]})
    cost_df["% of total"] = (100 * cost_df["₹"] / c["total"]).round(1)
    cc1, cc2 = st.columns([3, 2])
    with cc1:
        st.bar_chart(cost_df.set_index("Component")["₹"], height=320)
    with cc2:
        show = cost_df.copy(); show["₹"] = show["₹"].map(lambda x: f"{x:,.0f}")
        st.dataframe(show, hide_index=True, use_container_width=True)
        st.metric("TOTAL", rupees(c["total"]))

# --- Production ---
with tabs[1]:
    st.subheader("Production plan — how much of each SKU at each plant")
    prod = pd.DataFrame(R["prod"], columns=["SKU", "Plant", "Line", "Volume (kL)", "Batches"])
    prod["Volume (kL)"] = prod["Volume (kL)"].round(1)
    # capacity utilisation
    util_rows = []
    for p in R["_data"]["plants"]:
        used = prod.loc[prod.Plant == p, "Volume (kL)"].sum()
        capt = sum(R["_data"]["cap"][(p, ln)] for ln in R["_data"]["lines"])
        util_rows.append({"Plant": p, "Used (kL)": round(used), "Capacity (kL)": round(capt),
                          "Utilisation %": round(100 * used / capt, 0) if capt else 0})
    st.dataframe(pd.DataFrame(util_rows), hide_index=True, use_container_width=True)
    st.dataframe(prod.sort_values(["SKU", "Plant"]), hide_index=True, use_container_width=True, height=380)
    st.download_button("⬇ Download production plan (CSV)", prod.to_csv(index=False),
                       "production_plan.csv", "text/csv")

# --- Routing ---
with tabs[2]:
    st.subheader("Routing")
    rph = pd.DataFrame(R["rph"], columns=["SKU", "Plant", "Hub", "Volume (kL)"])
    rhc = pd.DataFrame(R["rhc"], columns=["SKU", "Hub", "CFA", "Volume (kL)"])
    cc1, cc2 = st.columns(2)
    with cc1:
        st.markdown("**Plant → Hub (totals)**")
        a = rph.groupby(["Plant", "Hub"])["Volume (kL)"].sum().round(0).reset_index()
        st.dataframe(a, hide_index=True, use_container_width=True)
    with cc2:
        st.markdown("**Hub → CFA (totals)**")
        b = rhc.groupby(["Hub", "CFA"])["Volume (kL)"].sum().round(0).reset_index()
        st.dataframe(b, hide_index=True, use_container_width=True)
    st.download_button("⬇ Download full hub→CFA routing (CSV)", rhc.to_csv(index=False),
                       "routing_hub_cfa.csv", "text/csv")

# --- Map ---
with tabs[3]:
    st.subheader("Network map — hub → CFA flows")
    try:
        import pydeck as pdk
        rhc = pd.DataFrame(R["rhc"], columns=["SKU", "Hub", "CFA", "Volume (kL)"])
        flows = rhc.groupby(["Hub", "CFA"])["Volume (kL)"].sum().reset_index()
        arcs = []
        for _, r in flows.iterrows():
            if r["Hub"] in coords.HUBS and r["CFA"] in coords.CFAS:
                h = coords.HUBS[r["Hub"]]; cc = coords.CFAS[r["CFA"]]
                arcs.append({"from_lat": h[0], "from_lon": h[1], "to_lat": cc[0], "to_lon": cc[1],
                             "vol": float(r["Volume (kL)"]),
                             "color": [31, 119, 180] if r["Hub"] == "MHW" else [214, 39, 40]})
        nodes = []
        for code, (la, lo) in coords.PLANTS.items():
            nodes.append({"lat": la, "lon": lo, "label": f"Plant {code}", "c": [46, 125, 50], "r": 40000})
        for code, (la, lo) in coords.HUBS.items():
            nodes.append({"lat": la, "lon": lo, "label": code, "c": [120, 80, 200], "r": 35000})
        for code, (la, lo) in coords.CFAS.items():
            nodes.append({"lat": la, "lon": lo, "label": code, "c": [240, 140, 20], "r": 25000})
        arc_layer = pdk.Layer("ArcLayer", data=pd.DataFrame(arcs),
            get_source_position=["from_lon", "from_lat"], get_target_position=["to_lon", "to_lat"],
            get_source_color="color", get_target_color="color",
            get_width="vol/40 + 1", pickable=True)
        node_layer = pdk.Layer("ScatterplotLayer", data=pd.DataFrame(nodes),
            get_position=["lon", "lat"], get_fill_color="c", get_radius="r", pickable=True)
        st.pydeck_chart(pdk.Deck(layers=[arc_layer, node_layer],
            initial_view_state=pdk.ViewState(latitude=22.5, longitude=80.0, zoom=3.6),
            map_style=None, tooltip={"text": "{label}"}))
        st.caption("Green = plants · Purple = hubs · Orange = CFAs. Arc thickness ∝ volume. "
                   "Blue arcs from Hub West, red from Hub East.")
    except Exception as e:
        st.info(f"Map needs the 'pydeck' package (ships with Streamlit). Detail: {e}")

# --- Inventory norms ---
with tabs[4]:
    st.subheader("Inventory norms (Component 1)")
    st.caption("Safety stock, reorder point and days of cover. Independent of the chosen month's plan.")
    nshow = cfa_norms.copy()
    for col in nshow.select_dtypes("float").columns:
        nshow[col] = nshow[col].round(2)
    st.markdown("**Per SKU × CFA**")
    st.dataframe(nshow, hide_index=True, use_container_width=True, height=320)
    st.markdown("**Per SKU × Hub (98% service, risk-pooled)**")
    hshow = hub_norms.copy()
    for col in hshow.select_dtypes("float").columns:
        hshow[col] = hshow[col].round(2)
    st.dataframe(hshow, hide_index=True, use_container_width=True, height=240)
    st.download_button("⬇ Download CFA norms (CSV)", cfa_norms.to_csv(index=False), "cfa_norms.csv", "text/csv")

# --- Unmet ---
with tabs[5]:
    st.subheader("Unmet demand — what the plan chose to short, and why")
    if R["unmet"]:
        um = pd.DataFrame(R["unmet"], columns=["SKU", "CFA", "Unmet (kL)", "Penalty ₹/kL", "Contractual"])
        um["Unmet (kL)"] = um["Unmet (kL)"].round(2)
        um["Penalty cost ₹"] = (um["Unmet (kL)"] * um["Penalty ₹/kL"]).round(0)
        um["Contractual"] = um["Contractual"].map({True: "YES", False: "No"})
        st.dataframe(um.sort_values("Penalty cost ₹", ascending=False), hide_index=True,
                     use_container_width=True)
        st.caption("Items here are uneconomic to fully supply at current capacity — producing them "
                   "would cost more than the penalty. Contractual SKUs are protected first.")
    else:
        st.success("Nothing unmet — all demand satisfied.")

# --- Compare ---
with tabs[6]:
    st.subheader("Scenario comparison")
    base = st.session_state.get("baseline")
    if base is None:
        st.info("Run the plan twice (e.g. change an input, then Run again). "
                "The previous run becomes the baseline and the difference is shown here.")
    else:
        cb, cc_ = base["costs"], R["costs"]
        comp = pd.DataFrame({
            "Metric": ["Total cost ₹", "Production ₹", "Transport P→H ₹", "Transport H→CFA ₹",
                       "Unmet penalty ₹", "Unmet kL", "Produced kL"],
            "Baseline (previous)": [cb["total"], cb["production"], cb["transport_ph"], cb["transport_hc"],
                                    cb["penalty_unmet"], base["total_unmet_kl"], base["total_produced"]],
            "Current": [cc_["total"], cc_["production"], cc_["transport_ph"], cc_["transport_hc"],
                        cc_["penalty_unmet"], R["total_unmet_kl"], R["total_produced"]]})
        comp["Change"] = comp["Current"] - comp["Baseline (previous)"]
        fmt = comp.copy()
        for col in ["Baseline (previous)", "Current", "Change"]:
            fmt[col] = fmt[col].map(lambda x: f"{x:,.1f}")
        st.dataframe(fmt, hide_index=True, use_container_width=True)
        st.metric("Total cost change", rupees(cc_["total"] - cb["total"]),
                  delta=f"{100*(cc_['total']-cb['total'])/cb['total']:.1f}%")
