"""Self-contained cinematic NASA NeoWs asteroid environment & Intelligence Dossier."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
from typing import Any

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from dashboard_data import DashboardDataProvider

CUSTOM_CSS = """<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Manrope:wght@400;500;600&display=swap');
.stApp{background:#02050b;color:#d8e4f3;font-family:Manrope,sans-serif}
#MainMenu,footer{visibility:hidden!important}
header{background:transparent!important}
header [data-testid="stDecoration"]{visibility:hidden!important}
header [data-testid="stStatusWidget"]{visibility:hidden!important}
header [data-testid="stToolbarActions"],header [data-testid="stAppDeployButton"]{visibility:hidden!important}
header [data-testid="stToolbar"]{background:transparent!important;visibility:visible!important}
header [data-testid="stToolbar"] > div > div:last-child{visibility:hidden!important}
header button[data-testid="stExpandSidebarButton"],
header [data-testid="stExpandSidebarButton"]{
    visibility:visible!important;
    display:inline-flex!important;
    color:#8ba4c0!important;
    background:rgba(6,11,21,0.85)!important;
    border:1px solid rgba(150,190,255,0.22)!important;
    border-radius:6px!important;
    pointer-events:auto!important;
    transition:all .15s ease!important;
}
header button[data-testid="stExpandSidebarButton"]:hover,
header [data-testid="stExpandSidebarButton"]:hover{
    color:#5bc4ff!important;
    border-color:rgba(91,196,255,0.45)!important;
    background:rgba(10,20,38,0.95)!important;
}
[data-testid="stSidebarCollapseButton"],
[data-testid="stSidebarCollapseButton"] button,
button[data-testid="stSidebarCollapseButton"]{
    visibility:visible!important;
    display:inline-flex!important;
    color:#8ba4c0!important;
    background:transparent!important;
    pointer-events:auto!important;
    transition:color .15s ease!important;
}
[data-testid="stSidebarCollapseButton"]:hover,
[data-testid="stSidebarCollapseButton"] button:hover,
button[data-testid="stSidebarCollapseButton"]:hover{
    color:#5bc4ff!important;
}
.block-container{max-width:1540px;padding:1.2rem 2.2rem 1.8rem}
section[data-testid="stSidebar"]{background:#060b15;border-right:1px solid rgba(150,190,255,.12);min-width:320px}
div[data-testid="stSelectbox"] label,div[data-testid="stSlider"] label{color:#91a5bf!important;font-size:.72rem!important;letter-spacing:.08em;text-transform:uppercase}
.missionbar{display:flex;justify-content:space-between;align-items:center;padding:0 0 14px;border-bottom:1px solid rgba(163,199,238,.13)}
.eyebrow,.system{font:.68rem 'DM Mono',monospace;letter-spacing:.14em;color:#7d94af;text-transform:uppercase}
.title{margin-top:4px;font-size:1.13rem;letter-spacing:.12em;color:#f1f7ff;font-weight:500}
.live{color:#9edfff;border:1px solid rgba(91,196,255,.28);background:rgba(26,125,187,.1);border-radius:20px;padding:6px 10px}
.live i{display:inline-block;width:6px;height:6px;margin-right:7px;background:#85dcff;border-radius:50%;box-shadow:0 0 12px #50c9ff}
.caption{font:.72rem 'DM Mono',monospace;color:#7187a0;letter-spacing:.04em;margin:11px 2px 0}
.target-box{background:rgba(6,11,21,0.85);border:1px solid rgba(150,190,255,0.18);border-radius:8px;padding:12px 18px;margin:10px 0 14px}

/* Dossier component styling */
.dossier-card{background:rgba(6,11,21,0.85);border:1px solid rgba(150,190,255,0.14);border-radius:8px;padding:16px 20px;margin-bottom:14px}
.dossier-title{font-family:'DM Mono',monospace;font-size:0.7rem;color:#7d94af;letter-spacing:0.12em;text-transform:uppercase;margin-bottom:12px}
.kpi-card{background:rgba(10,18,34,0.7);border:1px solid rgba(150,190,255,0.12);border-radius:6px;padding:12px 14px;margin-bottom:8px}
.kpi-label{font-family:'DM Mono',monospace;font-size:0.66rem;color:#7890aa;letter-spacing:0.08em;text-transform:uppercase;margin-bottom:4px}
.kpi-val{font-family:'DM Mono',monospace;font-size:1.12rem;font-weight:500;color:#f1f7ff}
.kpi-sub{font-size:0.7rem;color:#8da3bc;margin-top:3px;font-family:'DM Mono',monospace}
.field-row{display:flex;justify-content:space-between;align-items:center;padding:7px 10px;background:rgba(10,18,32,0.45);border:1px solid rgba(150,190,255,0.08);border-radius:5px;font-family:'DM Mono',monospace;font-size:0.76rem;margin-bottom:6px}
.field-name{color:#7d94af}
.field-val{color:#eaf3fd;font-weight:400;text-align:right}
.notice-box{background:rgba(8,15,27,0.85);border:1px solid rgba(150,190,255,0.15);border-left:3px solid #5bc4ff;border-radius:6px;padding:14px 18px;margin:12px 0;font-family:'DM Mono',monospace}
.notice-box.warning{border-left-color:#f5a623;background:rgba(28,20,8,0.85);border-color:rgba(245,166,35,0.25)}
.notice-head{font-size:0.74rem;font-weight:500;letter-spacing:0.1em;text-transform:uppercase;color:#9edfff;margin-bottom:4px}
.notice-box.warning .notice-head{color:#fce1b5}
.notice-desc{font-size:0.74rem;color:#8fa8c0;line-height:1.5}
.notice-box.warning .notice-desc{color:#e6c89c}

/* Streamlit Tabs Styling */
div[data-testid="stTabs"] [role="tab"],div[data-testid="stTab"]{color:#7d94af!important;font-family:'DM Mono',monospace!important;font-size:0.76rem!important;letter-spacing:0.12em!important;text-transform:uppercase!important;padding:8px 18px!important;background:transparent!important;border-bottom:2px solid transparent!important}
div[data-testid="stTabs"] [role="tab"][aria-selected="true"],div[data-testid="stTab"][aria-selected="true"]{color:#5bc4ff!important;border-bottom:2px solid #5bc4ff!important;background:rgba(91,196,255,0.08)!important;font-weight:500!important}
</style>"""


def as_bool(series: pd.Series) -> pd.Series:
    return series.astype(str).str.strip().str.lower().isin(["true", "1", "t", "yes"])


@st.cache_data(ttl=60)
def load_data() -> pd.DataFrame:
    """Load threat watchlist telemetry via DashboardDataProvider with legacy fallback."""
    base = Path(__file__).resolve().parent
    provider = DashboardDataProvider(base_dir=base)
    df = provider.get_threat_watchlist()
    if not df.empty:
        return df

    # Legacy fallback to SQLite or CSV if Lakehouse parquet is not present
    db_path = os.getenv("DB_PATH", str(base / "asteroids.db"))
    if os.path.exists(db_path):
        try:
            with sqlite3.connect(db_path) as conn:
                data = pd.read_sql_query(
                    """SELECT a.asteroid_id AS neows_id, a.asteroid_id AS id, a.name, a.hazardous,
                              c.approach_date AS closest_approach_date, c.miss_distance_km
                       FROM asteroids a
                       JOIN close_approaches c ON a.asteroid_id = c.asteroid_id
                       ORDER BY c.miss_distance_km""",
                    conn,
                )
                if not data.empty:
                    data["hazardous"] = as_bool(data["hazardous"])
                    return data
        except Exception:
            pass

    csv_path = base / "asteroids.csv"
    if csv_path.exists():
        data = pd.read_csv(csv_path)
        data["hazardous"] = as_bool(data["hazardous"])
        if "id" in data.columns and "neows_id" not in data.columns:
            data["neows_id"] = data["id"]
        return data

    return pd.DataFrame()


POPULATION_FILTER_MODES = [
    "All tracked targets",
    "Potentially hazardous only",
    "Nominal targets only",
    "Sentry-monitored targets",
    "Hazardous & Sentry-monitored",
]


def filter_population(
    df: pd.DataFrame,
    mode: str = "All tracked targets",
    horizon_mkm: float | None = None,
) -> pd.DataFrame:
    """Filter threat telemetry population deterministically by classification and radar horizon.

    Operates strictly on authoritative NeoWs telemetry:
    - mode: 'All tracked targets', 'Potentially hazardous only', 'Nominal targets only',
            'Sentry-monitored targets', 'Hazardous & Sentry-monitored'
    - horizon_mkm: Miss-distance threshold in million kilometers (miss_distance_km <= horizon_mkm * 1e6).
    """
    if df.empty:
        return df.copy()

    view = df.copy()
    if mode == "Potentially hazardous only":
        view = view[view["hazardous"].astype(bool)]
    elif mode == "Nominal targets only":
        view = view[~view["hazardous"].astype(bool)]
    elif mode == "Sentry-monitored targets":
        view = view[view["is_sentry_monitored"].astype(bool)]
    elif mode == "Hazardous & Sentry-monitored":
        view = view[view["hazardous"].astype(bool) & view["is_sentry_monitored"].astype(bool)]

    if horizon_mkm is not None:
        threshold_km = float(horizon_mkm) * 1_000_000.0
        view = view[view["miss_distance_km"].astype(float) <= threshold_km]

    return view


def build_canvas_payload(view_df: pd.DataFrame) -> list[dict[str, Any]]:
    """Construct a lightweight data payload strictly conforming to the Canvas contract."""
    records: list[dict[str, Any]] = []
    for _, row in view_df.iterrows():
        nid = str(row.get("neows_id", row.get("id", "")))
        records.append(
            {
                "id": nid,
                "neows_id": nid,
                "name": str(row.get("name", "")),
                "hazardous": bool(row.get("hazardous", False)),
                "miss_distance_km": float(row.get("miss_distance_km", 0.0)),
                "closest_approach_date": str(row.get("closest_approach_date", "")),
                "is_sentry_monitored": bool(row.get("is_sentry_monitored", False)),
                "has_sbdb_characterization": bool(row.get("has_sbdb_characterization", False)),
                "asteroid_key": str(row["asteroid_key"]) if pd.notna(row.get("asteroid_key")) else None,
            }
        )
    return records


def scene_html(objects: list[dict[str, Any]], active_neows_id: str | None = None) -> str:
    """Canvas scene has no CDN dependency, so it works inside a Streamlit iframe."""
    payload = json.dumps(objects).replace("</", "<\\/")
    active_json = json.dumps(str(active_neows_id) if active_neows_id else "")
    page = r'''<!doctype html><html><head><meta charset="utf-8"><style>
*{box-sizing:border-box}body{margin:0;background:#02050b;overflow:hidden;font-family:Arial;color:#dcecff}#scene{height:690px;position:relative;overflow:hidden;border:1px solid rgba(151,201,255,.16);border-radius:12px;background:#02050b}canvas{width:100%;height:100%;display:block;cursor:grab}.top{position:absolute;z-index:2;top:18px;left:20px;font:11px monospace;letter-spacing:.13em;color:#8ba4c0;pointer-events:none}.top b{display:block;color:#d8edff;font-weight:500;margin-top:5px}.key{position:absolute;z-index:2;bottom:17px;left:20px;font:10px monospace;letter-spacing:.09em;color:#7890aa;line-height:1.8;pointer-events:none}.key i{display:inline-block;width:6px;height:6px;border-radius:50%;background:#e3927e;margin-right:6px}.key .n{background:#91b5cf}#hint{position:absolute;z-index:3;right:18px;bottom:16px;font:10px monospace;color:#8fa8c0;letter-spacing:.08em;pointer-events:none}.tooltip{position:absolute;z-index:5;display:none;padding:10px 12px;border:1px solid rgba(180,221,255,.3);border-radius:7px;background:rgba(4,10,20,.9);font:11px monospace;pointer-events:none;box-shadow:0 12px 35px #0009}.tooltip strong{display:block;color:#eef7ff;font:500 12px Arial;margin-bottom:5px}.haz{color:#f0a08d}.nom{color:#a9cde7}#panel{position:absolute;z-index:6;right:18px;top:18px;width:min(330px,calc(100% - 36px));display:none;padding:18px;border:1px solid rgba(158,211,255,.28);border-radius:10px;background:linear-gradient(150deg,rgba(9,20,37,.96),rgba(3,8,16,.96));box-shadow:0 20px 55px #000a}#panel .tag{font:10px monospace;letter-spacing:.14em;color:#79a3c6}#panel h2{font-size:18px;font-weight:500;line-height:1.25;margin:8px 28px 17px 0;color:#f3f8ff}#close{position:absolute;right:13px;top:13px;border:0;background:transparent;color:#c6e5ff;font-size:21px;cursor:pointer}.field{padding:9px 0;border-top:1px solid rgba(165,207,242,.13);display:flex;justify-content:space-between;gap:10px;font:11px monospace;color:#7f9ab5}.field b{color:#e4f1fd;font-weight:400;text-align:right}.notice{margin-top:14px;font:10px monospace;line-height:1.55;color:#6d849c}
</style></head><body><div id="scene"><canvas id="space"></canvas><div class="top">LIVE NEO ENVIRONMENT<b>EARTH-CENTRIC VISUAL POPULATION MODEL</b></div><div class="key"><i></i> POTENTIALLY HAZARDOUS &nbsp; <i class="n"></i> NOMINAL TARGET<br>ORBITAL LINES ILLUSTRATIVE — NOT EPHEMERIS</div><div id="hint">DRAG TO ROTATE · SCROLL TO ZOOM · SELECT A TARGET</div><div id="tip" class="tooltip"></div><aside id="panel"><button id="close">×</button><div class="tag">NASA NEO // OBJECT INTELLIGENCE</div><h2 id="pname"></h2><div id="fields"></div><div class="notice">Motion is an illustrative visual representation. Values shown are verified fields from the local NASA close-approach dataset.</div></aside></div>
<script>
const data=__DATA__,activeNeowsId=__ACTIVE_ID__,canvas=document.getElementById('space'),ctx=canvas.getContext('2d'),tip=document.getElementById('tip'),panel=document.getElementById('panel');let W,H,cx,cy,dpr,zoom=1,spin=0,drag=false,lastX=0,selected=null,hovered=null;const stars=Array.from({length:700},()=>({x:Math.random(),y:Math.random(),r:Math.random()*1.35,a:.2+Math.random()*.8}));function hash(s){let h=2166136261;for(let i=0;i<s.length;i++)h=Math.imul(h^s.charCodeAt(i),16777619);return(h>>>0)/4294967295}const ast=data.slice(0,110).map(d=>{let targetId=String(d.neows_id||d.id);let a=hash(targetId),b=hash(d.name),dist=1.35+a*1.75;return{d,a,b,dist,rx:dist*(.8+a*.25),ry:dist*(.45+b*.22),tilt:(b-.5)*.78,phase:a*6.283,speed:.00045+a*.00085,size:3.2+b*3+(d.hazardous?1.5:0)}});function resize(){dpr=Math.min(devicePixelRatio,2);W=canvas.clientWidth;H=canvas.clientHeight;canvas.width=W*dpr;canvas.height=H*dpr;ctx.setTransform(dpr,0,0,dpr,0,0);cx=W/2;cy=H/2}addEventListener('resize',resize);resize();function ellipse(o,t){let x=Math.cos(o.phase+t*o.speed+spin)*o.rx,y=Math.sin(o.phase+t*o.speed+spin)*o.ry,ct=Math.cos(o.tilt),st=Math.sin(o.tilt);return{x:cx+x*cy*.39*zoom*ct-y*cy*.39*zoom*st,y:cy+x*cy*.15*zoom*st+y*cy*.39*zoom*ct}}function earth(t){let r=Math.min(W,H)*.155*zoom,g=ctx.createRadialGradient(cx-r*.32,cy-r*.36,r*.04,cx,cy,r);g.addColorStop(0,'#78d8f8');g.addColorStop(.18,'#178fc9');g.addColorStop(.5,'#075288');g.addColorStop(.78,'#032b59');g.addColorStop(1,'#010b1c');ctx.shadowColor='#198fe6';ctx.shadowBlur=44;ctx.beginPath();ctx.arc(cx,cy,r,0,7);ctx.fillStyle=g;ctx.fill();ctx.shadowBlur=0;ctx.save();ctx.beginPath();ctx.arc(cx,cy,r*.985,0,7);ctx.clip();ctx.globalAlpha=.74;for(let i=0;i<20;i++){let u=(i*.618+t*.000016)%1,x=cx-r+r*2*u,y=cy-r*.68+Math.sin(i*2.5)*r*.45,w=r*(.15+(i%4)*.07),h=r*(.07+(i%3)*.05);ctx.fillStyle=i%3?'#4b8a5e':'#b5a96a';ctx.beginPath();ctx.ellipse(x,y,w,h,i*.3,0,7);ctx.fill()}ctx.globalAlpha=.38;ctx.fillStyle='#f5fbff';for(let i=0;i<18;i++){let x=cx-r+(i*.31%2)*r,y=cy-r+(i*.71%2)*r;ctx.beginPath();ctx.ellipse(x,y,r*.2,r*.032,i*.32,0,7);ctx.fill()}let shade=ctx.createLinearGradient(cx+r*.22,0,cx+r,0);shade.addColorStop(0,'rgba(0,0,10,0)');shade.addColorStop(1,'rgba(0,0,8,.9)');ctx.fillStyle=shade;ctx.fillRect(cx,cy-r,r,r*2);ctx.restore();ctx.strokeStyle='rgba(103,203,255,.56)';ctx.lineWidth=1.4;ctx.beginPath();ctx.arc(cx,cy,r*1.018,0,7);ctx.stroke();return r}function drawRock(x,y,r,haz,hot){ctx.save();ctx.translate(x,y);ctx.rotate((x+y)*.03);ctx.shadowColor=haz?'#f36f4d':'#80b6df';ctx.shadowBlur=hot?19:5;ctx.fillStyle=haz?'#9d5847':'#7f8e99';ctx.beginPath();for(let i=0;i<9;i++){let q=i/9*6.283,v=r*(.74+((i*17)%7)/25);ctx.lineTo(Math.cos(q)*v,Math.sin(q)*v)}ctx.closePath();ctx.fill();ctx.shadowBlur=0;ctx.strokeStyle=haz?'#f7b19c':'#b8d0e1';ctx.globalAlpha=.7;ctx.stroke();ctx.restore()}function render(t){requestAnimationFrame(render);ctx.clearRect(0,0,W,H);let bg=ctx.createRadialGradient(cx,cy,0,cx,cy,Math.max(W,H)*.72);bg.addColorStop(0,'#0a1a31');bg.addColorStop(.36,'#040b18');bg.addColorStop(1,'#010207');ctx.fillStyle=bg;ctx.fillRect(0,0,W,H);stars.forEach(s=>{ctx.globalAlpha=s.a*(.75+.25*Math.sin(t*.001+s.x*20));ctx.fillStyle='#cce8ff';ctx.fillRect(s.x*W,s.y*H,s.r,s.r)});ctx.globalAlpha=1;let r=Math.min(W,H)*.155*zoom;ast.forEach(o=>{ctx.save();ctx.translate(cx,cy);ctx.rotate(o.tilt);ctx.scale(1,.55);ctx.strokeStyle='rgba(128,185,225,.13)';ctx.lineWidth=1;ctx.beginPath();ctx.ellipse(0,0,o.rx*cy*.39*zoom,o.ry*cy*.39*zoom,0,0,7);ctx.stroke();ctx.restore()});earth(t);ast.forEach(o=>{o.p=ellipse(o,t);let hot=o===hovered||o===selected;drawRock(o.p.x,o.p.y,o.size*(hot?1.55:1),o.d.hazardous,hot)});if(selected){let p=selected.p;if(p){ctx.strokeStyle='rgba(143,218,255,.75)';ctx.lineWidth=1;ctx.setLineDash([4,5]);ctx.beginPath();ctx.arc(p.x,p.y,selected.size*3.2,0,7);ctx.stroke();ctx.setLineDash([])}}}function pick(x,y){return ast.find(o=>Math.hypot(x-o.p.x,y-o.p.y)<o.size+8)}function showTip(o,x,y){tip.style.display='block';tip.style.left=(x+15)+'px';tip.style.top=(y-18)+'px';tip.innerHTML='<strong>'+o.d.name+'</strong><span class="'+(o.d.hazardous?'haz':'nom')+'">HAZARDOUS: '+(o.d.hazardous?'YES':'NO')+'</span>'}function select(o){selected=o;tip.style.display='none';document.getElementById('pname').textContent=o.d.name;document.getElementById('fields').innerHTML=[['NASA designation',o.d.neows_id||o.d.id],['Potentially hazardous',o.d.hazardous?'YES':'NO'],['Close approach',o.d.closest_approach_date],['Miss distance',(o.d.miss_distance_km/1e6).toFixed(2)+' million km'],['Lunar distance',(o.d.miss_distance_km/384400).toFixed(1)+' LD']].map(x=>'<div class="field"><span>'+x[0]+'</span><b>'+x[1]+'</b></div>').join('');panel.style.display='block';try{const targetId=o.d.neows_id||o.d.id;if(window.parent&&window.parent.location){const currentUrl=new URL(window.parent.location.href);if(currentUrl.searchParams.get('selected')!==String(targetId)){currentUrl.searchParams.set('selected',String(targetId));window.parent.history.replaceState({},'',currentUrl.toString());}}}catch(e){}}document.getElementById('close').onclick=()=>{selected=null;panel.style.display='none';zoom=1};canvas.onmousemove=e=>{let r=canvas.getBoundingClientRect(),x=e.clientX-r.left,y=e.clientY-r.top;if(drag){spin+=(x-lastX)*.008;lastX=x;return}hovered=pick(x,y);canvas.style.cursor=hovered?'pointer':'grab';if(hovered)showTip(hovered,x,y);else tip.style.display='none'};canvas.onmousedown=e=>{drag=true;lastX=e.clientX};canvas.onmouseup=e=>{drag=false;let r=canvas.getBoundingClientRect(),o=pick(e.clientX-r.left,e.clientY-r.top);if(o)select(o)};canvas.onmouseleave=()=>{drag=false;hovered=null;tip.style.display='none'};canvas.onwheel=e=>{e.preventDefault();zoom=Math.max(.62,Math.min(1.55,zoom-e.deltaY*.0007))};requestAnimationFrame(render);if(activeNeowsId){let match=ast.find(o=>String(o.d.neows_id||o.d.id)===String(activeNeowsId));if(match){select(match);}}
</script></body></html>'''
    # Keep the component self-contained while tailoring the visual motion model.
    # The static ellipses are suppressed; each object records a short, fading trail.
    return (
        page.replace("__DATA__", payload)
        .replace("__ACTIVE_ID__", active_json)
        .replace("speed:.00045+a*.00085", "speed:.000018+a*.000035")
        .replace("ctx.strokeStyle='rgba(128,185,225,.13)'", "ctx.strokeStyle='rgba(128,185,225,0)'")
    )


# ---------------------------------------------------------------------------
# Section Renderers for Dossier Tabs
# ---------------------------------------------------------------------------


def render_overview_tab(
    target_row: pd.DataFrame,
    res: dict[str, Any],
    selected_id: str,
    target_name: str,
    sentry_profile: dict[str, Any] | None,
    sbdb_profile: dict[str, Any] | None,
) -> None:
    """Render Tab 1: Primary close-approach encounter metrics & identity summary."""
    st.markdown("<div class='dossier-title'>Close-Approach Encounter Summary</div>", unsafe_allow_html=True)

    if not target_row.empty:
        r = target_row.iloc[0]
        app_date = str(r.get("closest_approach_date", "NOT REPORTED"))
        miss_km = float(r.get("miss_distance_km", 0.0))
        miss_ld = float(r.get("miss_distance_lunar", miss_km / 384400.0))
        is_haz = bool(r.get("hazardous", False))

        # Velocity check: never manufacture velocity if not in telemetry contract
        vel_val = r.get("relative_velocity_km_s", r.get("velocity_km_s"))
        if pd.notna(vel_val):
            vel_str = f"{float(vel_val):.2f} km/s"
            vel_sub = "Observed relative speed"
        else:
            vel_str = "Unavailable"
            vel_sub = "Not reported in telemetry contract"

        haz_tag = "Potentially Hazardous: YES" if is_haz else "Potentially Hazardous: NO"
        haz_color = "#e3927e" if is_haz else "#4bba7e"
    else:
        app_date = "NOT REPORTED"
        miss_km = 0.0
        miss_ld = 0.0
        vel_str = "Unavailable"
        vel_sub = "Not reported in telemetry contract"
        haz_tag = "Potentially Hazardous: NO"
        haz_color = "#4bba7e"

    # KPI Metrics row
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(
            f"""<div class='kpi-card'>
                <div class='kpi-label'>Close Approach Date</div>
                <div class='kpi-val'>{app_date}</div>
                <div class='kpi-sub'>Observation Epoch</div>
            </div>""",
            unsafe_allow_html=True,
        )
    with c2:
        st.markdown(
            f"""<div class='kpi-card'>
                <div class='kpi-label'>Miss Distance</div>
                <div class='kpi-val'>{miss_km / 1e6:.2f}M km</div>
                <div class='kpi-sub'>{miss_ld:.1f} Lunar Distances (LD)</div>
            </div>""",
            unsafe_allow_html=True,
        )
    with c3:
        st.markdown(
            f"""<div class='kpi-card'>
                <div class='kpi-label'>Relative Velocity</div>
                <div class='kpi-val'>{vel_str}</div>
                <div class='kpi-sub'>{vel_sub}</div>
            </div>""",
            unsafe_allow_html=True,
        )
    with c4:
        st.markdown(
            f"""<div class='kpi-card'>
                <div class='kpi-label'>Classification</div>
                <div class='kpi-val' style='color:{haz_color};'>{haz_tag}</div>
                <div class='kpi-sub'>NASA PHA Criteria</div>
            </div>""",
            unsafe_allow_html=True,
        )

    # Detailed two-column breakdown
    col_left, col_right = st.columns(2)
    with col_left:
        st.markdown("<div class='dossier-title'>Multi-Source Identity & Coverage</div>", unsafe_allow_html=True)
        key_val = res.get("asteroid_key") or "None (Unresolved in lakehouse crosswalk)"
        m_state = res.get("match_state", "UNRESOLVED")

        # Sentry monitoring indicator
        if sentry_profile and sentry_profile.get("has_sentry_monitoring"):
            if sentry_profile.get("is_sentry_ambiguous"):
                sentry_status = "Ambiguous (Multiple records detected)"
            else:
                sentry_status = f"Monitored (ID: {sentry_profile.get('sentry_id')})"
        else:
            sentry_status = "Not Monitored in Sentry"

        # SBDB characterization indicator
        if sbdb_profile and sbdb_profile.get("spkid"):
            sbdb_status = f"Characterized (SPK-ID: {sbdb_profile['spkid']})"
        else:
            sbdb_status = "Not Characterized in Current SBDB Snapshot"

        st.markdown(
            f"""
            <div class='field-row'><span class='field-name'>Target Name</span><span class='field-val'>{target_name}</span></div>
            <div class='field-row'><span class='field-name'>NeoWs ID</span><span class='field-val'>{selected_id}</span></div>
            <div class='field-row'><span class='field-name'>Resolution State</span><span class='field-val'>{m_state}</span></div>
            <div class='field-row'><span class='field-name'>Canonical Entity Key</span><span class='field-val'><code>{key_val}</code></span></div>
            <div class='field-row'><span class='field-name'>Sentry Monitoring</span><span class='field-val'>{sentry_status}</span></div>
            <div class='field-row'><span class='field-name'>SBDB Astronomical</span><span class='field-val'>{sbdb_status}</span></div>
            """,
            unsafe_allow_html=True,
        )

    with col_right:
        st.markdown("<div class='dossier-title'>Observational Protocol & Safety Guidance</div>", unsafe_allow_html=True)
        st.markdown(
            """
            <div class='notice-box'>
                <div class='notice-head'>Encounter Geometry Protocol</div>
                <div class='notice-desc'>
                    Close-approach distances are computed from Earth center. Potentially Hazardous Asteroid (PHA) designation is defined by absolute magnitude (H &le; 22.0) and Earth minimum orbit intersection distance (MOID &le; 0.05 AU).
                </div>
            </div>
            <div class='notice-box'>
                <div class='notice-head'>Scientific Safety Policy</div>
                <div class='notice-desc'>
                    Zero composite threat formulas, synthetic ranking indexes, or causal claims are applied. All values reflect factual observational telemetry.
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def render_sbdb_tab(sbdb_profile: dict[str, Any] | None, asteroid_key: str | None) -> None:
    """Render Tab 2: NASA/JPL Small-Body Database (SBDB) Keplerian & physical profile."""
    st.markdown("<div class='dossier-title'>Small-Body Database (SBDB) Astronomical Profile</div>", unsafe_allow_html=True)

    if not asteroid_key:
        st.markdown(
            """<div class='notice-box'>
                <div class='notice-head'>Target Unresolved — No SBDB Linkage</div>
                <div class='notice-desc'>This target has not been resolved to a canonical entity key. Small-Body Database Keplerian orbital elements and physical properties require a resolved SPK-ID linkage.</div>
            </div>""",
            unsafe_allow_html=True,
        )
        return

    if not sbdb_profile or not sbdb_profile.get("spkid"):
        st.markdown(
            """<div class='notice-box'>
                <div class='notice-head'>No SBDB Characterization Available</div>
                <div class='notice-desc'>NOT REPORTED IN CURRENT SBDB SNAPSHOT. No astronomical orbit or physical parameter record exists for this entity in the active Lakehouse snapshot.</div>
            </div>""",
            unsafe_allow_html=True,
        )
        return

    # Identification header row
    spkid = sbdb_profile.get("spkid", "NOT REPORTED")
    des = sbdb_profile.get("designation", "NOT REPORTED")
    fullname = sbdb_profile.get("fullname", "")
    name_display = f"{des} · {fullname}" if fullname and fullname != des else des
    orbit_class = f"{sbdb_profile.get('orbit_class_name', '')} ({sbdb_profile.get('orbit_class_code', '')})".strip()
    tier = sbdb_profile.get("astrometric_data_quality_tier", "UNREPORTED")

    st.markdown(
        f"""
        <div class='field-row' style='margin-bottom:12px; background:rgba(14,25,45,0.7); border-color:rgba(150,190,255,0.2);'>
            <span class='field-name'>SBDB SPK-ID: <b style='color:#f1f7ff;'>{spkid}</b> &nbsp;|&nbsp; Target: <b style='color:#f1f7ff;'>{name_display}</b> &nbsp;|&nbsp; Orbit Class: <b style='color:#f1f7ff;'>{orbit_class}</b></span>
            <span class='field-val' style='color:#9edfff;'>Tier: {tier}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("<div class='dossier-title'>Keplerian Orbit Solution Elements</div>", unsafe_allow_html=True)

        a_val = f"{sbdb_profile['semi_major_axis_au']:.4f} AU" if sbdb_profile.get("semi_major_axis_au") is not None else "NOT REPORTED IN CURRENT SBDB SNAPSHOT"
        e_val = f"{sbdb_profile['eccentricity']:.6f}" if sbdb_profile.get("eccentricity") is not None else "NOT REPORTED IN CURRENT SBDB SNAPSHOT"
        q_val = f"{sbdb_profile['perihelion_distance_au']:.4f} AU" if sbdb_profile.get("perihelion_distance_au") is not None else "NOT REPORTED IN CURRENT SBDB SNAPSHOT"
        i_val = f"{sbdb_profile['inclination_deg']:.4f}°" if sbdb_profile.get("inclination_deg") is not None else "NOT REPORTED IN CURRENT SBDB SNAPSHOT"
        moid_val = f"{sbdb_profile['earth_moid_au']:.6f} AU" if sbdb_profile.get("earth_moid_au") is not None else "NOT REPORTED IN CURRENT SBDB SNAPSHOT"
        per_val = f"{sbdb_profile['orbital_period_yr']:.3f} years" if sbdb_profile.get("orbital_period_yr") is not None else "NOT REPORTED IN CURRENT SBDB SNAPSHOT"
        orbit_id = str(sbdb_profile.get("orbit_id") or "NOT REPORTED")
        epoch_jd = f"{sbdb_profile['epoch_jd']:.1f}" if sbdb_profile.get("epoch_jd") is not None else "NOT REPORTED"

        st.markdown(
            f"""
            <div class='field-row'><span class='field-name'>Semi-Major Axis (a)</span><span class='field-val'>{a_val}</span></div>
            <div class='field-row'><span class='field-name'>Eccentricity (e)</span><span class='field-val'>{e_val}</span></div>
            <div class='field-row'><span class='field-name'>Perihelion Distance (q)</span><span class='field-val'>{q_val}</span></div>
            <div class='field-row'><span class='field-name'>Inclination (i)</span><span class='field-val'>{i_val}</span></div>
            <div class='field-row'><span class='field-name'>Earth MOID</span><span class='field-val'>{moid_val}</span></div>
            <div class='field-row'><span class='field-name'>Sidereal Orbital Period</span><span class='field-val'>{per_val}</span></div>
            <div class='field-row'><span class='field-name'>Orbit Solution ID</span><span class='field-val'>{orbit_id}</span></div>
            <div class='field-row'><span class='field-name'>Osculating Epoch (JD)</span><span class='field-val'>{epoch_jd}</span></div>
            """,
            unsafe_allow_html=True,
        )

    with col2:
        st.markdown("<div class='dossier-title'>Astrometric Fit & Physical Characterization</div>", unsafe_allow_html=True)

        cond_code = str(sbdb_profile.get("condition_code") or "NOT REPORTED")
        arc_days = f"{sbdb_profile['data_arc_days']} days" if sbdb_profile.get("data_arc_days") is not None else "NOT REPORTED IN CURRENT SBDB SNAPSHOT"
        n_obs = str(sbdb_profile.get("n_obs_used") or "NOT REPORTED")
        soln_date = str(sbdb_profile.get("soln_date") or "NOT REPORTED")
        producer = str(sbdb_profile.get("producer") or "Auto / JPL")

        # Physical parameters: strict null preservation (never 0.0)
        diam_val = f"{sbdb_profile['estimated_diameter_km']:.3f} km" if sbdb_profile.get("estimated_diameter_km") is not None else "NOT REPORTED IN CURRENT SBDB SNAPSHOT"
        h_val = f"{sbdb_profile['absolute_magnitude']:.2f} mag" if sbdb_profile.get("absolute_magnitude") is not None else "NOT REPORTED IN CURRENT SBDB SNAPSHOT"
        albedo_val = f"{sbdb_profile['albedo']:.3f}" if sbdb_profile.get("albedo") is not None else "NOT REPORTED IN CURRENT SBDB SNAPSHOT"
        rot_val = f"{sbdb_profile['rotational_period_hr']:.2f} hr" if sbdb_profile.get("rotational_period_hr") is not None else "NOT REPORTED IN CURRENT SBDB SNAPSHOT"

        st.markdown(
            f"""
            <div class='field-row'><span class='field-name'>Astrometric Quality Tier</span><span class='field-val'>{tier}</span></div>
            <div class='field-row'><span class='field-name'>Orbit Condition Code</span><span class='field-val'>{cond_code}</span></div>
            <div class='field-row'><span class='field-name'>Observation Data Arc</span><span class='field-val'>{arc_days}</span></div>
            <div class='field-row'><span class='field-name'>Observations Used</span><span class='field-val'>{n_obs}</span></div>
            <div class='field-row'><span class='field-name'>Solution Date</span><span class='field-val'>{soln_date}</span></div>
            <div class='field-row'><span class='field-name'>Producer</span><span class='field-val'>{producer}</span></div>
            <div class='field-row' style='margin-top:10px;'><span class='field-name'>Estimated Diameter</span><span class='field-val'>{diam_val}</span></div>
            <div class='field-row'><span class='field-name'>Absolute Magnitude (H)</span><span class='field-val'>{h_val}</span></div>
            <div class='field-row'><span class='field-name'>Geometric Albedo</span><span class='field-val'>{albedo_val}</span></div>
            <div class='field-row'><span class='field-name'>Rotational Period</span><span class='field-val'>{rot_val}</span></div>
            """,
            unsafe_allow_html=True,
        )


def render_sentry_tab(sentry_profile: dict[str, Any] | None, asteroid_key: str | None) -> None:
    """Render Tab 3: NASA/JPL Sentry Impact Risk Monitoring Profile."""
    st.markdown("<div class='dossier-title'>Sentry Impact Monitoring Profile (NASA/JPL Sentry System)</div>", unsafe_allow_html=True)
    st.markdown(
        """<div class='caption' style='margin:-6px 0 12px;'>Current reported Sentry metrics — Observational risk metrics reported by the source system; not system-generated predictions.</div>""",
        unsafe_allow_html=True,
    )

    if not asteroid_key:
        st.markdown(
            """<div class='notice-box'>
                <div class='notice-head'>Target Unresolved — No Sentry Linkage</div>
                <div class='notice-desc'>Target has no canonical entity key. Sentry impact monitoring metrics require a resolved entity crosswalk linkage.</div>
            </div>""",
            unsafe_allow_html=True,
        )
        return

    if not sentry_profile or not sentry_profile.get("has_sentry_monitoring"):
        st.markdown(
            """<div class='notice-box'>
                <div class='notice-head'>NO CURRENT SENTRY RECORD</div>
                <div class='notice-desc'>This object is not currently listed in the active NASA/JPL Sentry impact monitoring table. No potential Earth impact solutions are currently reported by Sentry.</div>
            </div>""",
            unsafe_allow_html=True,
        )
        return

    if sentry_profile.get("is_sentry_ambiguous"):
        count = sentry_profile.get("sentry_identifier_count", 2)
        st.markdown(
            f"""<div class='notice-box warning'>
                <div class='notice-head'>MULTIPLE SENTRY RECORDS DETECTED</div>
                <div class='notice-desc'>Cross-source ambiguity detected: Multiple Sentry identifier candidates are associated with this canonical entity key ({count} records). Scalar impact risk metrics are suppressed to prevent false attribution.</div>
            </div>""",
            unsafe_allow_html=True,
        )
        return

    # Unique resolved Sentry record
    sentry_id = sentry_profile.get("sentry_id", "NOT REPORTED")
    ip_val = sentry_profile.get("latest_impact_probability")
    ip_str = f"{ip_val:.2e}" if ip_val is not None else "NOT REPORTED"
    pot_count = str(sentry_profile.get("latest_potential_impacts_count") or "NOT REPORTED")
    year_range = str(sentry_profile.get("impact_year_range") or "NOT REPORTED")
    active_str = "Active in Sentry Table" if sentry_profile.get("is_currently_active") else "Inactive / Removed"

    # KPI row
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(
            f"""<div class='kpi-card'>
                <div class='kpi-label'>Sentry Object ID</div>
                <div class='kpi-val'>{sentry_id}</div>
                <div class='kpi-sub'>{active_str}</div>
            </div>""",
            unsafe_allow_html=True,
        )
    with c2:
        st.markdown(
            f"""<div class='kpi-card'>
                <div class='kpi-label'>Impact Probability</div>
                <div class='kpi-val'>{ip_str}</div>
                <div class='kpi-sub'>Reported Cumulative IP</div>
            </div>""",
            unsafe_allow_html=True,
        )
    with c3:
        st.markdown(
            f"""<div class='kpi-card'>
                <div class='kpi-label'>Potential Impacts</div>
                <div class='kpi-val'>{pot_count}</div>
                <div class='kpi-sub'>Computed Encounter Paths</div>
            </div>""",
            unsafe_allow_html=True,
        )
    with c4:
        st.markdown(
            f"""<div class='kpi-card'>
                <div class='kpi-label'>Impact Year Range</div>
                <div class='kpi-val'>{year_range}</div>
                <div class='kpi-sub'>Potential Encounter Window</div>
            </div>""",
            unsafe_allow_html=True,
        )

    col_l, col_r = st.columns(2)
    with col_l:
        st.markdown("<div class='dossier-title'>Technical Risk Scales (Absolute Values)</div>", unsafe_allow_html=True)

        pal_max = f"{sentry_profile['latest_palermo_scale_max']:.2f}" if sentry_profile.get("latest_palermo_scale_max") is not None else "NOT REPORTED"
        pal_cum = f"{sentry_profile['latest_palermo_scale_cum']:.2f}" if sentry_profile.get("latest_palermo_scale_cum") is not None else "NOT REPORTED"
        torino = str(sentry_profile.get("latest_torino_scale_max") if sentry_profile.get("latest_torino_scale_max") is not None else "NOT REPORTED")

        all_ip = f"{sentry_profile['all_time_max_impact_probability']:.2e}" if sentry_profile.get("all_time_max_impact_probability") is not None else "NOT REPORTED"
        all_pal = f"{sentry_profile['all_time_max_palermo_scale_max']:.2f}" if sentry_profile.get("all_time_max_palermo_scale_max") is not None else "NOT REPORTED"
        all_tor = str(sentry_profile.get("all_time_max_torino_scale_max") if sentry_profile.get("all_time_max_torino_scale_max") is not None else "NOT REPORTED")

        st.markdown(
            f"""
            <div class='field-row'><span class='field-name'>Palermo Technical Scale (Max)</span><span class='field-val'>{pal_max}</span></div>
            <div class='field-row'><span class='field-name'>Palermo Technical Scale (Cum)</span><span class='field-val'>{pal_cum}</span></div>
            <div class='field-row'><span class='field-name'>Torino Scale (Max)</span><span class='field-val'>{torino}</span></div>
            <div class='field-row'><span class='field-name'>All-Time Max Impact Probability</span><span class='field-val'>{all_ip}</span></div>
            <div class='field-row'><span class='field-name'>All-Time Max Palermo (Max)</span><span class='field-val'>{all_pal}</span></div>
            <div class='field-row'><span class='field-name'>All-Time Max Torino (Max)</span><span class='field-val'>{all_tor}</span></div>
            """,
            unsafe_allow_html=True,
        )

    with col_r:
        st.markdown("<div class='dossier-title'>Observational & Fit Constraints</div>", unsafe_allow_html=True)

        v_inf = f"{sentry_profile['v_infinity_km_s']:.2f} km/s" if sentry_profile.get("v_infinity_km_s") is not None else "NOT REPORTED"
        last_obs = str(sentry_profile.get("last_obs_date") or "NOT REPORTED")
        snaps = str(sentry_profile.get("total_snapshots_observed", 0))
        snap_key = str(sentry_profile.get("latest_snapshot_key") or "NOT REPORTED")
        des_val = str(sentry_profile.get("designation") or "NOT REPORTED")

        st.markdown(
            f"""
            <div class='field-row'><span class='field-name'>Velocity at Infinity (v-infinity)</span><span class='field-val'>{v_inf}</span></div>
            <div class='field-row'><span class='field-name'>Last Observation Epoch</span><span class='field-val'>{last_obs}</span></div>
            <div class='field-row'><span class='field-name'>Published Sentry Designation</span><span class='field-val'>{des_val}</span></div>
            <div class='field-row'><span class='field-name'>Latest Catalog Snapshot Date</span><span class='field-val'>{snap_key}</span></div>
            <div class='field-row'><span class='field-name'>Total Risk Snapshots Observed</span><span class='field-val'>{snaps}</span></div>
            """,
            unsafe_allow_html=True,
        )


def render_history_tab(history_df: pd.DataFrame, sentry_id: str | None, asteroid_key: str | None) -> None:
    """Render Tab 4: Snapshot-by-snapshot historical risk log with non-causal change flags."""
    st.markdown("<div class='dossier-title'>Sentry Risk Metric History</div>", unsafe_allow_html=True)

    if not asteroid_key:
        st.markdown(
            """<div class='notice-box'>
                <div class='notice-head'>NO HISTORICAL SENTRY DATA</div>
                <div class='notice-desc'>Historical risk tracking requires a resolved entity with an active or historical Sentry monitoring identifier.</div>
            </div>""",
            unsafe_allow_html=True,
        )
        return

    if not sentry_id or history_df.empty:
        st.markdown(
            """<div class='notice-box'>
                <div class='notice-head'>NO HISTORICAL RISK SNAPSHOTS RECORDED</div>
                <div class='notice-desc'>No multi-snapshot trajectory records exist for this target identifier.</div>
            </div>""",
            unsafe_allow_html=True,
        )
        return

    st.markdown(
        """<div class='notice-box'>
            <div class='notice-head'>Observational Trajectory History</div>
            <div class='notice-desc'>Snapshot-by-snapshot log of reported observational risk metrics. Metric changes describe changes in published catalog values across observation epochs.</div>
        </div>""",
        unsafe_allow_html=True,
    )

    display_rows = []
    for idx, r in history_df.iterrows():
        # First snapshot has no previous comparison: unavailable, not 0
        if idx == 0:
            notes = "Baseline snapshot (no prior comparison)"
        else:
            changes = []
            if r.get("is_impact_probability_changed"):
                changes.append("Reported impact probability changed.")
            if r.get("is_palermo_scale_max_changed"):
                changes.append("Palermo metric change detected.")
            if not changes:
                changes.append("Snapshot-to-snapshot change detected.")
            notes = " ".join(changes)

        ip_fmt = f"{float(r['impact_probability']):.2e}" if pd.notna(r.get("impact_probability")) else "N/A"
        pal_max = f"{float(r['palermo_scale_max']):.2f}" if pd.notna(r.get("palermo_scale_max")) else "N/A"
        pal_cum = f"{float(r['palermo_scale_cum']):.2f}" if pd.notna(r.get("palermo_scale_cum")) else "N/A"
        tor_max = str(int(r["torino_scale_max"])) if pd.notna(r.get("torino_scale_max")) else "N/A"
        pot_cnt = str(int(r["potential_impacts_count"])) if pd.notna(r.get("potential_impacts_count")) else "N/A"
        v_inf = f"{float(r['v_infinity_km_s']):.2f}" if pd.notna(r.get("v_infinity_km_s")) else "N/A"

        display_rows.append(
            {
                "Snapshot Date": str(r.get("snapshot_key", "")),
                "Impact Probability": ip_fmt,
                "Palermo Max": pal_max,
                "Palermo Cum": pal_cum,
                "Torino Max": tor_max,
                "Potential Impacts": pot_cnt,
                "v-infinity (km/s)": v_inf,
                "Year Range": str(r.get("impact_year_range", "N/A")),
                "Last Obs": str(r.get("last_obs_date", "N/A")),
                "Reported Change Log": notes,
            }
        )

    st.dataframe(pd.DataFrame(display_rows), use_container_width=True, hide_index=True)


def render_crosswalk_tab(
    crosswalk_df: pd.DataFrame,
    res: dict[str, Any],
    selected_id: str,
    target_name: str,
    asteroid_key: str | None,
) -> None:
    """Render Tab 5: Entity crosswalk, source namespaces, and identity resolution audit."""
    st.markdown("<div class='dossier-title'>Platform Crosswalk & Entity Resolution Provenance</div>", unsafe_allow_html=True)
    st.markdown(
        """<div class='caption' style='margin:-6px 0 14px;'>Demonstrating strict namespace isolation across NASA source systems. The canonical asteroid_key serves as the sole platform-wide identity bridge.</div>""",
        unsafe_allow_html=True,
    )

    m_state = res.get("match_state", "UNRESOLVED")

    if not asteroid_key or m_state != "RESOLVED":
        if m_state == "AMBIGUOUS":
            st.markdown(
                """<div class='notice-box warning'>
                    <div class='notice-head'>AMBIGUOUS RESOLUTION STATE</div>
                    <div class='notice-desc'>Cross-source metrics are suppressed because multiple candidate entities or keys match this target.</div>
                </div>""",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f"""<div class='notice-box'>
                    <div class='notice-head'>TARGET UNRESOLVED — NO CROSSWALK LINKAGE</div>
                    <div class='notice-desc'>No canonical entity key established in lakehouse crosswalk. External systems (SBDB, Sentry) are unmapped for NeoWs ID <code>{selected_id}</code>.</div>
                </div>""",
                unsafe_allow_html=True,
            )
        return

    # Resolved crosswalk presentation
    match_rule = res.get("match_rule", "EXACT_DESIGNATION_MATCH")
    evidence = res.get("evidence", "Deterministic exact match in canonical entity bridge.")

    st.markdown(
        f"""
        <div class='field-row' style='margin-bottom:14px; background:rgba(14,25,45,0.7); border-color:rgba(150,190,255,0.2);'>
            <span class='field-name'>Canonical Entity Key: <code style='color:#5bc4ff;'>{asteroid_key}</code> &nbsp;|&nbsp; Evidence: <i style='color:#8fa8c0;'>{evidence}</i></span>
            <span class='field-val' style='color:#4bba7e;'>Rule: {match_rule}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Namespace Isolation Grid (3 columns)
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown("<div class='dossier-title'>NeoWs Namespace</div>", unsafe_allow_html=True)
        st.markdown(
            f"""
            <div class='field-row'><span class='field-name'>id</span><span class='field-val'><code>{selected_id}</code></span></div>
            <div class='field-row'><span class='field-name'>name</span><span class='field-val'>{target_name}</span></div>
            <div class='field-row'><span class='field-name'>Primary Pivot</span><span class='field-val'>False</span></div>
            <div class='field-row'><span class='field-name'>Domain</span><span class='field-val'>Approach Telemetry</span></div>
            """,
            unsafe_allow_html=True,
        )

    with c2:
        st.markdown("<div class='dossier-title'>SBDB Namespace</div>", unsafe_allow_html=True)
        sbdb_rows = crosswalk_df[crosswalk_df["source_system"] == "sbdb"] if not crosswalk_df.empty else pd.DataFrame()
        spkid_row = sbdb_rows[sbdb_rows["identifier_name"] == "spkid"] if not sbdb_rows.empty else pd.DataFrame()
        des_row = sbdb_rows[sbdb_rows["identifier_name"] == "des"] if not sbdb_rows.empty else pd.DataFrame()

        spk_str = str(spkid_row.iloc[0]["identifier_value"]) if not spkid_row.empty else "NOT MAPPED"
        des_str = str(des_row.iloc[0]["identifier_value"]) if not des_row.empty else "NOT MAPPED"

        st.markdown(
            f"""
            <div class='field-row'><span class='field-name'>spkid</span><span class='field-val'><code>{spk_str}</code></span></div>
            <div class='field-row'><span class='field-name'>des</span><span class='field-val'>{des_str}</span></div>
            <div class='field-row'><span class='field-name'>Primary Pivot</span><span class='field-val' style='color:#5bc4ff;'>True (Canonical Anchor)</span></div>
            <div class='field-row'><span class='field-name'>Domain</span><span class='field-val'>Keplerian Catalog</span></div>
            """,
            unsafe_allow_html=True,
        )

    with c3:
        st.markdown("<div class='dossier-title'>Sentry Namespace</div>", unsafe_allow_html=True)
        sentry_rows = crosswalk_df[crosswalk_df["source_system"] == "sentry"] if not crosswalk_df.empty else pd.DataFrame()
        sid_row = sentry_rows[sentry_rows["identifier_name"] == "sentry_id"] if not sentry_rows.empty else pd.DataFrame()
        sdes_row = sentry_rows[sentry_rows["identifier_name"] == "des"] if not sentry_rows.empty else pd.DataFrame()

        sid_str = str(sid_row.iloc[0]["identifier_value"]) if not sid_row.empty else "NOT MAPPED"
        sdes_str = str(sdes_row.iloc[0]["identifier_value"]) if not sdes_row.empty else "NOT MAPPED"

        st.markdown(
            f"""
            <div class='field-row'><span class='field-name'>sentry_id</span><span class='field-val'><code>{sid_str}</code></span></div>
            <div class='field-row'><span class='field-name'>des</span><span class='field-val'>{sdes_str}</span></div>
            <div class='field-row'><span class='field-name'>Primary Pivot</span><span class='field-val'>False</span></div>
            <div class='field-row'><span class='field-name'>Domain</span><span class='field-val'>Impact Monitoring</span></div>
            """,
            unsafe_allow_html=True,
        )

    # Detailed Audit Trail Table
    if not crosswalk_df.empty:
        st.markdown("<div class='dossier-title' style='margin-top:14px;'>Identifier Crosswalk Audit Records</div>", unsafe_allow_html=True)
        audit_cols = ["source_system", "identifier_name", "identifier_value", "is_primary_pivot", "updated_at"]
        valid_cols = [c for c in audit_cols if c in crosswalk_df.columns]
        st.dataframe(crosswalk_df[valid_cols], use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Application Main Execution
# ---------------------------------------------------------------------------


def main() -> None:
    st.set_page_config(
        page_title="NASA Planetary Defense — NEO Orbital Intelligence",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

    provider = DashboardDataProvider()
    df = load_data()

    # Global mission header
    mode_badge = provider.get_execution_mode()
    st.markdown(
        f"<div class='missionbar'><div><div class='eyebrow'>NASA NEO // PLANETARY DEFENSE OBSERVATION</div><div class='title'>ORBITAL ENVIRONMENT</div></div><div class='system live'><i></i> {mode_badge}</div></div>",
        unsafe_allow_html=True,
    )

    if df.empty:
        st.warning("No asteroid records found. Run ingestion to populate the local NASA lakehouse cache.")
        st.stop()

    # Sidebar observation controls
    st.sidebar.markdown(
        """
        <div style='font-family:"DM Mono",monospace; font-size:0.72rem; letter-spacing:0.14em; color:#7d94af; text-transform:uppercase; margin-bottom:12px; border-bottom:1px solid rgba(150,190,255,0.12); padding-bottom:6px;'>
            Observation Controls
        </div>
        """,
        unsafe_allow_html=True,
    )
    mode = st.sidebar.selectbox(
        "Population",
        options=POPULATION_FILTER_MODES,
        index=0,
        help="Filter approaching asteroid population by NASA classification or impact monitoring status.",
    )
    max_mkm = max(1.0, float(df["miss_distance_km"].max()) / 1_000_000)
    horizon = st.sidebar.slider(
        "RADAR HORIZON",
        min_value=0.1,
        max_value=round(max_mkm, 1),
        value=round(max_mkm, 1),
        step=0.1,
        format="%.1fM km",
        key="radar_horizon_slider",
        help="Telemetry miss-distance horizon. Filters tracked population by observed miss distance threshold in million km.",
    )
    st.sidebar.markdown(
        f"""
        <div style='font-family:"DM Mono",monospace; font-size:0.7rem; color:#8fa8c0; margin-top:-6px; margin-bottom:16px;'>
            Up to <b>{horizon:.1f}M km</b> &nbsp;({horizon * 1e6 / 384400.0:.1f} LD)
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Apply population and distance filters
    view = filter_population(df, mode=mode, horizon_mkm=horizon)

    # Initialize session state for selection
    filtered_neows_ids = [str(nid) for nid in view["neows_id"].unique()] if not view.empty else []
    if "selected_neows_id" not in st.session_state:
        qp_selected = st.query_params.get("selected")
        st.session_state["selected_neows_id"] = qp_selected if qp_selected in filtered_neows_ids else None

    # Reset selection if current target is filtered out
    if st.session_state["selected_neows_id"] is not None and st.session_state["selected_neows_id"] not in filtered_neows_ids:
        st.session_state["selected_neows_id"] = None
        if "target_investigation_focus_selector" in st.session_state:
            st.session_state["target_investigation_focus_selector"] = None
        if "selected" in st.query_params:
            del st.query_params["selected"]

    # Render visual Canvas environment (Centerpiece)
    canvas_payload = build_canvas_payload(view)
    components.html(scene_html(canvas_payload, active_neows_id=st.session_state["selected_neows_id"]), height=692, scrolling=False)
    st.markdown(
        f"<div class='caption'>INTERACTIVE VISUALIZATION · {len(view)} OF {len(df)} LOCAL NEO RECORDS · RADAR HORIZON: UP TO {horizon:.1f}M KM ({horizon * 1e6 / 384400.0:.1f} LD) · SELECT AN ASTEROID FOR VERIFIED CLOSE-APPROACH INTELLIGENCE</div>",
        unsafe_allow_html=True,
    )

    # Target Selector (Directly beneath Canvas in the information hierarchy)
    target_options: list[str | None] = [None] + filtered_neows_ids

    def format_target(nid: str | None) -> str:
        if nid is None:
            if not filtered_neows_ids:
                return "— No targets within active radar horizon —"
            return "— Select an approaching asteroid to inspect —"
        s_nid = str(nid)
        if s_nid.startswith("—"):
            return s_nid
        if " [NeoWs ID: " in s_nid and s_nid.endswith(")"):
            return s_nid
        rows = view[view["neows_id"].astype(str) == s_nid]
        if rows.empty:
            return f"Target [NeoWs ID: {s_nid}]"
        r = rows.iloc[0]
        haz_tag = "PHA" if r.get("hazardous") else "Nominal"
        sentry_tag = " · Sentry Monitored" if r.get("is_sentry_monitored") else ""
        return f"{r['name']}  [NeoWs ID: {s_nid}]  ({haz_tag}{sentry_tag})"

    curr_idx = target_options.index(st.session_state["selected_neows_id"]) if st.session_state["selected_neows_id"] in target_options else 0

    chosen_target = st.selectbox(
        "Target investigation focus",
        options=target_options,
        index=curr_idx,
        format_func=format_target,
        key="target_investigation_focus_selector",
        help="Select a Near-Earth Object from the active radar population to inspect verified cross-source intelligence.",
    )

    if chosen_target != st.session_state["selected_neows_id"]:
        actual_id = chosen_target
        if chosen_target and " [NeoWs ID: " in str(chosen_target):
            actual_id = str(chosen_target).split(" [NeoWs ID: ")[1].split("]")[0]
        st.session_state["selected_neows_id"] = actual_id
        if actual_id:
            st.query_params["selected"] = actual_id
        elif "selected" in st.query_params:
            del st.query_params["selected"]

    # Resolution lookup & selected target identity header
    if st.session_state["selected_neows_id"]:
        selected_id = str(st.session_state["selected_neows_id"])
        target_row = view[view["neows_id"].astype(str) == selected_id]
        target_name = str(target_row.iloc[0]["name"]) if not target_row.empty else f"NeoWs {selected_id}"

        res = provider.get_resolution_state(selected_id)
        match_state = res.get("match_state", "UNRESOLVED")
        asteroid_key = res.get("asteroid_key")

        state_colors = {
            "RESOLVED": "#4bba7e",
            "UNRESOLVED": "#8ba4c0",
            "AMBIGUOUS": "#f5a623",
            "INVALID": "#e74c3c",
        }
        badge_color = state_colors.get(match_state, "#8ba4c0")

        key_display = (
            f"<span style='color:#a9cde7;'>Canonical Entity Key:</span> <code>{asteroid_key}</code>"
            if asteroid_key
            else "<span style='color:#7d94af;'>Canonical Entity Key:</span> <i style='color:#8ba4c0;'>None (Unresolved in lakehouse crosswalk)</i>"
        )

        st.markdown(
            f"""
            <div class='target-box'>
                <div style='display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px;'>
                    <div>
                        <span style='font-family:\"DM Mono\",monospace; font-size:0.7rem; color:#7d94af; letter-spacing:0.12em; text-transform:uppercase;'>
                            Selected Target Telemetry & Identity
                        </span>
                        <div style='font-size:1.08rem; font-weight:500; color:#f1f7ff; margin-top:2px;'>
                            <b>{target_name}</b> &nbsp;|&nbsp; NeoWs ID: <code>{selected_id}</code> &nbsp;|&nbsp; {key_display}
                        </div>
                    </div>
                    <div style='text-align:right;'>
                        <span style='font-family:\"DM Mono\",monospace; font-size:0.72rem; letter-spacing:0.1em; padding:4px 10px; border-radius:4px; border:1px solid {badge_color}; color:{badge_color}; background:rgba(0,0,0,0.3);'>
                            {match_state}
                        </span>
                    </div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        # Retrieve downstream multi-source profiles via provider
        sbdb_profile = provider.get_sbdb_profile(asteroid_key) if asteroid_key else None
        sentry_profile = provider.get_sentry_profile(asteroid_key) if asteroid_key else None
        crosswalk_df = provider.get_crosswalk(asteroid_key) if asteroid_key else pd.DataFrame()

        # Retrieve historical risk data if unique Sentry ID available
        if sentry_profile and sentry_profile.get("sentry_id"):
            sentry_id = sentry_profile["sentry_id"]
            history_df = provider.get_historical_risk(sentry_id)
        else:
            sentry_id = None
            history_df = pd.DataFrame()

        # Render 5-tab multi-source intelligence dossier
        tab_overview, tab_sbdb, tab_sentry, tab_history, tab_crosswalk = st.tabs(
            [
                "OVERVIEW",
                "SBDB",
                "SENTRY",
                "HISTORY",
                "CROSSWALK",
            ]
        )

        with tab_overview:
            render_overview_tab(
                target_row=target_row,
                res=res,
                selected_id=selected_id,
                target_name=target_name,
                sentry_profile=sentry_profile,
                sbdb_profile=sbdb_profile,
            )

        with tab_sbdb:
            render_sbdb_tab(sbdb_profile=sbdb_profile, asteroid_key=asteroid_key)

        with tab_sentry:
            render_sentry_tab(sentry_profile=sentry_profile, asteroid_key=asteroid_key)

        with tab_history:
            render_history_tab(history_df=history_df, sentry_id=sentry_id, asteroid_key=asteroid_key)

        with tab_crosswalk:
            render_crosswalk_tab(
                crosswalk_df=crosswalk_df,
                res=res,
                selected_id=selected_id,
                target_name=target_name,
                asteroid_key=asteroid_key,
            )


if __name__ == "__main__":
    main()
