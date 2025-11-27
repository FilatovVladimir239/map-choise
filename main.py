from flask import Flask, render_template_string
from PIL import Image
import io, base64, json, re, os
from bs4 import BeautifulSoup

app = Flask(__name__)

MAP_IMAGE = "static/map.png"
COORDS_FILE = "coordinates.txt"
SPLITS_FILE = "splits.html"
CACHE_FILE = "cache_participants.json"

A4_WIDTH_MM = 210.0
A4_HEIGHT_MM = 297.0

points_data = None
participants_data = None
splits_mtime = 0

def load_all_points():
    global points_data
    if points_data: return points_data
    im = Image.open(MAP_IMAGE)
    w, h = im.size
    px_per_mm_x = w / A4_WIDTH_MM
    px_per_mm_y = h / A4_HEIGHT_MM
    r = 4 * px_per_mm_y        # было 6 → стало 4 мм
    offset = 6.5 * px_per_mm_x
    points = {}
    with open(COORDS_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or ":" not in line: continue
            kp = line.split(":",1)[0].strip()
            try:
                mm = line.split("(")[1].split(")")[0]
                mm_x, mm_y = map(float, mm.split(","))
                cx = mm_x * px_per_mm_x
                cy = h - mm_y * px_per_mm_y
                points[kp] = {"cx":cx,"cy":cy,"r":r,"tx":cx+offset,"ty":cy+offset}
            except: continue
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    img_b64 = base64.b64encode(buf.getvalue()).decode()
    points_data = (points, (w,h), img_b64)
    return points_data

def parse_splits_html():
    participants = {}
    cur = None
    with open(SPLITS_FILE, encoding="windows-1251") as f:
        soup = BeautifulSoup(f, "html.parser")
    groups = ["МЮ","ЖЮ","Мужчины","Женщины","ЖВ","МВ"]
    for a in soup.find_all("a", {"name":True}):
        if a["name"] in groups:
            cur = a["name"]
            participants[cur] = []
    for table in soup.find_all("table", class_="rezult"):
        a = table.find_previous("a", {"name":True})
        if a and a["name"] in participants: cur = a["name"]
        for row in table.find_all("tr")[1:]:
            c = row.find_all("td")
            if len(c)<10: continue
            place = c[0].get_text(strip=True).replace(".","")
            name = c[2].get_text(strip=True)
            if "Фамилия" in name or not name: continue
            result = c[8].get_text(strip=True) if len(c)>8 else "-"
            path, legs = [], []
            for i,cell in enumerate(c[10:]):
                txt = cell.get_text(separator="\n",strip=True)
                lines = [l.strip() for l in txt.split("\n") if l.strip()]
                if not lines: continue
                kp_match = re.search(r"\[(\w+)\]", lines[0])
                if not kp_match: continue
                kp = kp_match.group(1)
                if kp not in ["С1","Ф1"] and not kp.isdigit(): continue
                t = lines[1] if i>0 and len(lines)>1 else (re.search(r"^(\d+:\d+)",lines[0]).group(1) if i==0 and re.search(r"^(\d+:\d+)",lines[0]) else "-")
                path.append(kp)
                legs.append(t)
            if cur:
                participants[cur].append({"name":f"{place}. {name}","path":["С1"]+path+["Ф1"],"leg_times":legs,"result":result})
    return participants

def load_participants():
    global participants_data, splits_mtime
    if not os.path.exists(SPLITS_FILE): return {}
    mtime = os.path.getmtime(SPLITS_FILE)
    if participants_data is None or mtime > splits_mtime:
        participants_data = parse_splits_html()
        with open(CACHE_FILE,"w",encoding="utf-8") as f:
            json.dump(participants_data,f,ensure_ascii=False,indent=2)
        splits_mtime = mtime
    elif os.path.exists(CACHE_FILE):
        with open(CACHE_FILE,encoding="utf-8") as f:
            participants_data = json.load(f)
    return participants_data

@app.route("/")
def index():
    points,(_,__),img_b64 = load_all_points()
    participants = load_participants()
    if not participants:
        return "<h1>Нет данных в splits.html</h1>"

    svg = []
    for kp,p in points.items():
        if kp=="Ф1":
            svg.append(f'<g id="kp_{kp}" class="kp missed"><circle cx="{p["cx"]}" cy="{p["cy"]}" r="{p["r"]*1.5}" fill="none"/><circle cx="{p["cx"]}" cy="{p["cy"]}" r="{p["r"]}" fill="none"/><text x="{p["tx"]}" y="{p["ty"]}" font-size="36" font-weight="900" text-anchor="middle" dominant-baseline="middle">ФИНИШ</text></g>')
        else:
            svg.append(f'<g id="kp_{kp}" class="kp missed"><circle cx="{p["cx"]}" cy="{p["cy"]}" r="{p["r"]}" fill="none"/><text x="{p["tx"]}" y="{p["ty"]}" font-size="40" font-weight="900" text-anchor="middle" dominant-baseline="middle">{kp}</text></g>')

    acc = ""
    first = next(iter(participants),None)
    for g,runners in participants.items():
        open_class = "open" if g==first else ""
        items = "".join(f'<div class="person" data-path="{base64.b64encode(json.dumps(r["path"]).encode()).decode()}" data-leg="{base64.b64encode(json.dumps(r["leg_times"]).encode()).decode()}" data-result="{base64.b64encode(r["result"].encode()).decode()}" onclick="selectRunner(this)">{r["name"]}</div>' for r in runners)
        acc += f'<div class="group"><div class="group-header {open_class}" onclick="toggleGroup(this)">{g} ({len(runners)})</div><div class="person-list {open_class}">{items}</div></div>'

    html = f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"><title>По следам истории 2025</title>
<style>
body,html{{margin:0;height:100%;overflow:hidden;background:#111;color:#fff;font-family:Arial,sans-serif}}
#left,#right{{position:fixed;top:0;bottom:0;width:340px;background:#222;padding:20px;overflow-y:auto;z-index:10;transition:.4s}}
#left{{left:0}}#right{{right:0}}
#left.collapsed{{transform:translateX(-340px)}}#right.collapsed{{transform:translateX(340px)}}
#map-container{{margin:0 340px;height:100%;display:flex;justify-content:center;align-items:center;background:#000;transition:.4s}}
body.collapsed-left #map-container{{margin-left:0}}body.collapsed-right #map-container{{margin-right:0}}
.panel-header{{cursor:pointer;background:#c40000;padding:12px;border-radius:8px;margin-bottom:10px;font-weight:bold}}
.group-header{{background:#333;padding:12px;border-radius:8px;cursor:pointer;font-weight:bold}}
.group-header.open{{background:#a00}}
.person-list{{max-height:0;overflow:hidden;transition:.4s;background:#2a2a2a;margin-top:5px;border-radius:6px}}
.person-list.open{{max-height:1200px;padding:8px 0}}
.person{{padding:10px 20px;cursor:pointer;border-bottom:1px solid #333}}
.person:hover{{background:#900}}.person.active{{background:#c40000;font-weight:bold}}
#splits-table{{width:100%;border-collapse:collapse;font-size:14px}}
#splits-table th,#splits-table td{{padding:8px;border-bottom:1px solid #444;cursor:pointer}}
#splits-table th{{background:#333}}#splits-table tr:hover td{{background:#444}}
#splits-table tr.active td{{background:#c40000 !important}}

/* КП */
.kp circle{{stroke:#ff0000 !important; stroke-width:4 !important}}
.kp text{{fill:#ff0000 !important; font-weight:900 !important; stroke:#fff !important; stroke-width:1.5 !important}}

/* Взятые КП — толще изначально */
.kp.taken circle{{stroke-width:10 !important}}

/* Подсветка */
.kp.highlighted circle{{stroke:yellow !important; stroke-width:16 !important; filter:drop-shadow(0 0 12px yellow)}}
</style></head><body>
<div id="left"><div class="panel-header" onclick="togglePanel('left')">Участники</div><div id="accordion">{acc}</div></div>
<div id="right"><div class="panel-header" onclick="togglePanel('right')">Сплиты</div><div id="splits-info">Выберите участника</div></div>
<div id="map-container"><div id="map"><img src="data:image/png;base64,{img_b64}" id="mapimg">
<svg style="position:absolute;top:0;left:0;width:100%;height:100%;pointer-events:none">{"".join(svg)}<path id="path" fill="none" stroke="#ff3366" stroke-width="10" opacity="0.9" stroke-linecap="round"/></svg></div></div>
<script>
const points = {json.dumps(points, ensure_ascii=False)};
const mapDiv = document.getElementById('map');
const img = document.getElementById('mapimg');
const pathLine = document.getElementById('path');
const splitsDiv = document.getElementById('splits-info');
let scale = 1, posX = 0, posY = 0;

function fitMap() {{
    const l = document.getElementById('left').classList.contains('collapsed') ? 0 : 340;
    const r = document.getElementById('right').classList.contains('collapsed') ? 0 : 340;
    scale = Math.min((window.innerWidth - l - r) / img.naturalWidth, window.innerHeight / img.naturalHeight) * 0.94;
    posX = posY = 0;
    update();
}}

function update() {{
    mapDiv.style.transform = `translate(${{posX}}px,${{posY}}px) scale(${{scale}})`;
}}

mapDiv.addEventListener('wheel', e => {{
    e.preventDefault();
    scale *= e.deltaY > 0 ? 0.9 : 1.11;
    scale = Math.max(0.3, Math.min(20, scale));
    update();
}});

let dragging = false, sx, sy;
mapDiv.addEventListener('mousedown', e => {{
    if (e.button === 0) {{
        dragging = true;
        sx = e.clientX - posX;
        sy = e.clientY - posY;
        mapDiv.style.cursor = 'grabbing';
    }}
}});
document.addEventListener('mousemove', e => {{
    if (dragging) {{
        posX = e.clientX - sx;
        posY = e.clientY - sy;
        update();
    }}
}});
document.addEventListener('mouseup', () => {{ dragging = false; }});

function togglePanel(side) {{
    document.getElementById(side).classList.toggle('collapsed');
    fitMap();
}}

function clearMap() {{
    document.querySelectorAll('.kp').forEach(g => {{
        g.className = 'kp missed';
        g.classList.remove('highlighted');
    }});
    document.querySelectorAll('.split-row').forEach(r => r.classList.remove('active'));
    pathLine.setAttribute('d', '');
    splitsDiv.innerHTML = 'Выберите участника';
    document.querySelectorAll('.person').forEach(p => p.classList.remove('active'));
}}

function toggleGroup(h) {{
    const o = h.classList.contains('open');
    document.querySelectorAll('.group-header').forEach(x => x.classList.remove('open'));
    document.querySelectorAll('.person-list').forEach(x => x.classList.remove('open'));
    clearMap();
    if (!o) {{
        h.classList.add('open');
        h.nextElementSibling.classList.add('open');
    }}
}}

function timeToSec(t) {{
    if (!t || t === '-' || !t.includes(':')) return 0;
    const a = t.split(':').map(Number);
    return a.length === 3 ? a[0]*3600 + a[1]*60 + (a[2]||0) : a[0]*60 + a[1];
}}

function secToTime(s) {{
    if (s < 3600) {{
        const m = Math.floor(s / 60);
        const sec = s % 60;
        return m + ':' + sec.toString().padStart(2, '0');
    }}
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = s % 60;
    return h + ':' + m.toString().padStart(2, '0') + ':' + sec.toString().padStart(2, '0');
}}

function selectRunner(el) {{
    clearMap();
    el.classList.add('active');
    const path = JSON.parse(atob(el.dataset.path));
    const leg = JSON.parse(atob(el.dataset.leg));
    const res = atob(el.dataset.result).trim();

    document.querySelectorAll('.kp').forEach(g => {{
        const id = g.id.replace('kp_', '');
        g.classList.toggle('taken', path.includes(id));
    }});

    let d = '', prev = null;
    path.forEach(k => {{
        if (!points[k]) return;
        const c = {{x: points[k].cx, y: points[k].cy, r: points[k].r || 30}};
        if (prev) {{
            const dx = c.x - prev.x;
            const dy = c.y - prev.y;
            const dist = Math.hypot(dx, dy);
            if (dist > prev.r + c.r + 10) {{
                const ex = prev.x + dx * (prev.r + 5) / dist;
                const ey = prev.y + dy * (prev.r + 5) / dist;
                const ix = c.x - dx * (c.r + 5) / dist;
                const iy = c.y - dy * (c.r + 5) / dist;
                d += ` M ${{ex}},${{ey}} L ${{ix}},${{iy}}`;
            }}
        }}
        prev = c;
    }});
    pathLine.setAttribute('d', d);

    let tbl = '<table id="splits-table"><tr><th>№</th><th>КП</th><th>Перегон</th><th>Общее</th></tr>';
    tbl += '<tr class="split-row"><td></td><td>СТАРТ (С1)</td><td>—</td><td>0:00</td></tr>';
    let tot = 0;
    for (let i = 1; i < path.length - 1; i++) {{
        const kp = path[i];
        const lg = (i - 1 < leg.length) ? leg[i - 1] : '-';
        if (lg && lg !== '-' && lg.includes(':')) tot += timeToSec(lg);
        tbl += `<tr onclick="highlightKP('${{kp}}')" class="split-row"><td>${{i}}</td><td>${{kp}}</td><td>${{lg}}</td><td>${{tot > 0 ? secToTime(tot) : '—'}}</td></tr>`;
    }}
    let fl = '—', ft = '—';
    if (res.includes(':')) {{
        const rs = timeToSec(res);
        ft = res;
        if (rs >= tot) fl = secToTime(rs - tot);
    }}
    tbl += `<tr class="split-row"><td></td><td style="font-weight:bold;color:#ff6666">ФИНИШ</td><td style="font-weight:bold">${{fl}}</td><td style="font-weight:bold;color:#ff6666">${{ft}}</td></tr></table>`;
    splitsDiv.innerHTML = tbl;
}}

function highlightKP(id) {{
    document.querySelectorAll('.kp').forEach(g => g.classList.remove('highlighted'));
    document.querySelectorAll('.split-row').forEach(r => r.classList.remove('active'));
    const e = document.getElementById('kp_' + id);
    if (e) e.classList.add('highlighted');
    document.querySelectorAll('.split-row').forEach(r => {{
        if (r.cells[1].textContent === id) r.classList.add('active');
    }});
}}

window.onload = () => {{
    fitMap();
    window.onresize = fitMap;
    const f = document.querySelector('.group-header');
    if (f) toggleGroup(f);
}};
</script></body></html>"""

    return render_template_string(html)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
