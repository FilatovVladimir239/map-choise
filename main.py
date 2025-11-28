from flask import Flask, render_template_string, send_from_directory, jsonify
from PIL import Image
import io, base64, json, re, os
from bs4 import BeautifulSoup

app = Flask(__name__)

MAP_IMAGE = "static/map.png"
COORDS_FILE = "coordinates.txt"
SPLITS_FILE = "splits.html"
CACHE_FILE = "cache_participants.json"
GROUPS_FILE = "groups.txt"

A4_WIDTH_MM = 210.0
A4_HEIGHT_MM = 297.0

points_data = None
participants_data = None
splits_mtime = 0
group_kps = {}

def load_group_kps():
    global group_kps
    group_kps.clear()
    if not os.path.exists(GROUPS_FILE):
        print("[WARNING] groups.txt не найден")
        return
    with open(GROUPS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or ":" not in line: continue
            name, kps_str = line.split(":", 1)
            group = name.strip()
            kps = [kp.strip() for kp in kps_str.split() if kp.strip() and kp.strip() not in ["С1", "Ф1"]]
            group_kps[group] = kps

load_group_kps()

def load_all_points():
    global points_data
    if points_data: return points_data
    im = Image.open(MAP_IMAGE)
    w, h = im.size
    px_per_mm_x = w / A4_WIDTH_MM
    px_per_mm_y = h / A4_HEIGHT_MM
    r = 4 * px_per_mm_y
    # Убрал offset - теперь текст будет позиционироваться относительно кружка
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
                points[kp] = {"cx":cx,"cy":cy,"r":r}
            except: continue
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    img_b64 = base64.b64encode(buf.getvalue()).decode()
    points_data = (points, (w,h), img_b64)
    return points_data

def parse_splits_html():
    participants = {}
    cur_group = None
    try:
        with open(SPLITS_FILE, encoding="windows-1251") as f:
            soup = BeautifulSoup(f, "html.parser")
    except Exception as e:
        print(f"[ERROR] splits.html: {e}")
        return {}

    for a in soup.find_all("a", {"name": True}):
        raw = a["name"].strip()
        for group_name in group_kps.keys():
            if group_name.lower() in raw.lower() or raw.lower() in group_name.lower():
                cur_group = group_name
                participants[cur_group] = []
                break

    for table in soup.find_all("table", class_="rezult"):
        prev_a = table.find_previous("a", {"name": True})
        if prev_a:
            raw = prev_a["name"].strip()
            for group_name in group_kps.keys():
                if group_name.lower() in raw.lower():
                    cur_group = group_name
                    break

        for row in table.find_all("tr")[1:]:
            c = row.find_all("td")
            if len(c) < 10: continue
            place = c[0].get_text(strip=True).replace(".", "")
            name = c[2].get_text(strip=True)
            if "Фамилия" in name or not name: continue
            result = c[8].get_text(strip=True) if len(c) > 8 else "-"
            path, legs = [], []
            for i, cell in enumerate(c[10:]):
                txt = cell.get_text(separator="\n", strip=True)
                lines = [l.strip() for l in txt.split("\n") if l.strip()]
                if not lines: continue
                kp_match = re.search(r"\[(\w+)\]", lines[0])
                if not kp_match: continue
                kp = kp_match.group(1)
                if kp in ["С1", "Ф1"]: continue
                t = lines[1] if i > 0 and len(lines) > 1 else (
                    re.search(r"^(\d+:\d+)", lines[0]).group(1) if i == 0 and re.search(r"^(\d+:\d+)", lines[0]) else "-"
                )
                path.append(kp)
                legs.append(t)

            if cur_group:
                participants.setdefault(cur_group, []).append({
                    "name": f"{place}. {name}",
                    "group": cur_group,
                    "path": ["С1"] + path + ["Ф1"],
                    "leg_times": legs,
                    "result": result
                })

    print(f"[SUCCESS] Загружено {sum(len(v) for v in participants.values())} участников")
    return participants

def load_participants():
    global participants_data, splits_mtime
    if not os.path.exists(SPLITS_FILE): return {}
    mtime = os.path.getmtime(SPLITS_FILE)
    if participants_data is None or mtime > splits_mtime:
        participants_data = parse_splits_html()
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(participants_data, f, ensure_ascii=False, indent=2)
        splits_mtime = mtime
    else:
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                participants_data = json.load(f)
    return participants_data

@app.route("/")
def index():
    points, (_, _), img_b64 = load_all_points()
    participants = load_participants()
    if not participants:
        return "<h1 style='color:#c40000;text-align:center;margin-top:100px'>Нет данных<br><small>Проверьте splits.html и groups.txt</small></h1>"

    svg = []
    for kp, p in points.items():
        if kp == "Ф1":
            # Финиш - двойной кружок (уменьшенный)
            svg.append(f'''
                <g id="kp_{kp}" class="kp">
                    <circle cx="{p["cx"]}" cy="{p["cy"]}" r="{p["r"]*1.3}" fill="none" stroke="#ff0000" stroke-width="6"/>
                    <circle cx="{p["cx"]}" cy="{p["cy"]}" r="{p["r"]*0.7}" fill="none" stroke="#ff0000" stroke-width="6"/>
                    <text x="{p["cx"] + p["r"]*1.3 + 8}" y="{p["cy"] + p["r"]*1.3 + 8}" font-size="32" font-weight="900" text-anchor="start" dominant-baseline="hanging" fill="#ff0000" stroke="#fff" stroke-width="1.5">ФИНИШ</text>
                </g>
            ''')
        elif kp == "С1":
            # Старт - треугольник (уменьшенный)
            triangle_size = p["r"] * 1.2
            points_str = f'{p["cx"]},{p["cy"] - triangle_size} {p["cx"] - triangle_size},{p["cy"] + triangle_size} {p["cx"] + triangle_size},{p["cy"] + triangle_size}'
            svg.append(f'''
                <g id="kp_{kp}" class="kp">
                    <polygon points="{points_str}" fill="none" stroke="#ff0000" stroke-width="6"/>
                    <text x="{p["cx"] + triangle_size + 8}" y="{p["cy"] + triangle_size + 8}" font-size="32" font-weight="900" text-anchor="start" dominant-baseline="hanging" fill="#ff0000" stroke="#fff" stroke-width="1.5">СТАРТ</text>
                </g>
            ''')
        else:
            # Обычные КП - кружки
            svg.append(f'''
                <g id="kp_{kp}" class="kp">
                    <circle cx="{p["cx"]}" cy="{p["cy"]}" r="{p["r"]}" fill="none" stroke="#ff0000" stroke-width="4"/>
                    <text x="{p["cx"] + p["r"] + 8}" y="{p["cy"] + p["r"] + 8}" font-size="40" font-weight="900" text-anchor="start" dominant-baseline="hanging" fill="#ff0000" stroke="#fff" stroke-width="1.5">{kp}</text>
                </g>
            ''')

    acc = ""
    first = next(iter(participants), None)
    for g, runners in participants.items():
        open_class = "open" if g == first else ""
        items = ""
        for i, r in enumerate(runners):
            items += f'<div class="person" data-id="{i}" data-group="{g}" onclick="selectRunner(this)">{r["name"]}</div>'
        acc += f'<div class="group"><div class="group-header {open_class}" onclick="toggleGroup(this,\'{g}\')">{g} ({len(runners)})</div><div class="person-list {open_class}">{items}</div></div>'

    html = f'''<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"><title>По следам истории 2025</title>
<style>
body,html{{margin:0;height:100%;overflow:hidden;background:#111;color:#fff;font-family:Arial,sans-serif}}
#left,#right{{position:fixed;top:0;bottom:0;width:340px;background:#222;z-index:10;transition:.4s}}
#left{{left:0}}#right{{right:0}}
#left.collapsed{{width:0}}#right.collapsed{{width:0}}
#left-content,#right-content{{padding:20px;height:100%;overflow-y:auto}}
#map-container{{margin:0 340px;height:100%;display:flex;justify-content:center;align-items:center;background:#000;transition:.4s}}
body.collapsed-left #map-container{{margin-left:0}}body.collapsed-right #map-container{{margin-right:0}}
.panel-toggle{{position:fixed;top:50%;z-index:15;background:#c40000;border:none;color:white;width:30px;height:60px;cursor:pointer;font-size:20px;font-weight:bold;display:flex;align-items:center;justify-content:center;transition:.3s}}
.panel-toggle:hover{{background:#a00}}
#left-toggle{{left:340px;border-radius:0 8px 8px 0}}
#right-toggle{{right:340px;border-radius:8px 0 0 8px}}
body.collapsed-left #left-toggle{{left:0;transform:rotate(180deg)}}
body.collapsed-right #right-toggle{{right:0;transform:rotate(180deg)}}
.panel-header{{position:relative;cursor:pointer;background:#c40000;padding:12px;border-radius:8px;margin-bottom:10px;font-weight:bold;min-height:20px}}
.group-header{{background:#333;padding:12px;border-radius:8px;cursor:pointer;font-weight:bold}}
.group-header.open{{background:#a00}}
.person-list{{max-height:0;overflow:hidden;transition:.4s;background:#2a2a2a;margin-top:5px;border-radius:6px}}
.person-list.open{{max-height:1200px;padding:8px 0}}
.person{{padding:10px 20px;cursor:pointer;border-bottom:1px solid #333}}
.person:hover{{background:#900}}.person.active{{background:#c40000;font-weight:bold}}
#splits-table{{width:100%;border-collapse:collapse;font-size:14px;border:1px solid #444}}
#splits-table th,#splits-table td{{padding:8px;text-align:left;border-bottom:1px solid #444;cursor:pointer}}
#splits-table th{{background:#333}}
#splits-table tr:hover td{{background:#444}}
#splits-table tr.active td{{background:#c40000 !important;color:#fff !important}}
.kp circle,.kp polygon{{display:none}}
.kp text{{display:none}}
.kp.visible circle,.kp.visible polygon,.kp.visible text{{display:block}}
.kp.own circle{{stroke:#ff0000;stroke-width:10}}
.kp.own polygon{{stroke:#ff0000;stroke-width:10}}
.kp.alien circle{{stroke:#0088ff;stroke-width:10}}
.kp.alien polygon{{stroke:#0088ff;stroke-width:10}}
.kp.highlighted circle{{stroke:yellow;stroke-width:16;filter:drop-shadow(0 0 12px yellow)}}
.kp.highlighted polygon{{stroke:yellow;stroke-width:16;filter:drop-shadow(0 0 12px yellow)}}
.control-btn{{position:absolute;top:10px;z-index:20;background:#c40000;border:none;color:white;width:40px;height:40px;border-radius:6px;cursor:pointer;font-size:18px;display:flex;align-items:center;justify-content:center}}
.control-btn:hover{{background:#a00}}
#left-control{{left:10px}}
#right-control{{right:10px}}
</style></head><body>
<div id="left">
    <div id="left-content">
        <div class="panel-header">
            <span>Участники</span>
        </div>
        <div id="accordion">{acc}</div>
    </div>
</div>
<button id="left-toggle" class="panel-toggle" onclick="togglePanel('left')">◀</button>

<div id="right">
    <div id="right-content">
        <div class="panel-header">
            <span>Сплиты</span>
        </div>
        <div id="splits-info">Выберите участника</div>
    </div>
</div>
<button id="right-toggle" class="panel-toggle" onclick="togglePanel('right')">▶</button>

<div id="map-container">
    <button id="left-control" class="control-btn" onclick="togglePanel('left')" title="Участники">☰</button>
    <button id="right-control" class="control-btn" onclick="togglePanel('right')" title="Сплиты">📊</button>
    <div id="map"><img src="data:image/png;base64,{img_b64}" id="mapimg">
    <svg style="position:absolute;top:0;left:0;width:100%;height:100%;pointer-events:none">{"".join(svg)}<path id="path" fill="none" stroke="#ff3366" stroke-width="10" opacity="0.9" stroke-linecap="round"/></svg></div>
</div>
<script>
const points = {json.dumps(points, ensure_ascii=False)};
const groupKps = {json.dumps(group_kps, ensure_ascii=False)};
let participants = null;
const mapDiv = document.getElementById('map');
const img = document.getElementById('mapimg');
const pathLine = document.getElementById('path');
const splitsDiv = document.getElementById('splits-info');
let scale = 1, posX = 0, posY = 0;

fetch('data.json').then(r => r.json()).then(d => participants = d);

function fitMap() {{
    const leftCollapsed = document.getElementById('left').classList.contains('collapsed');
    const rightCollapsed = document.getElementById('right').classList.contains('collapsed');
    const l = leftCollapsed ? 0 : 340;
    const r = rightCollapsed ? 0 : 340;
    scale = Math.min((innerWidth-l-r)/img.naturalWidth, innerHeight/img.naturalHeight)*0.94;
    posX = posY = 0; update();
}}
function update() {{ mapDiv.style.transform = `translate(${{posX}}px,${{posY}}px) scale(${{scale}})`; }}

mapDiv.addEventListener('wheel', e => {{ e.preventDefault(); scale *= e.deltaY > 0 ? 0.9 : 1.11; scale = Math.max(0.3, Math.min(20, scale)); update(); }});
let dragging = false, sx, sy;
mapDiv.addEventListener('mousedown', e => {{ if(e.button===0){{ dragging=true; sx=e.clientX-posX; sy=e.clientY-posY; mapDiv.style.cursor='grabbing'; }}}});
document.addEventListener('mousemove', e => {{ if(dragging){{ posX=e.clientX-sx; posY=e.clientY-sy; update(); }}}});
document.addEventListener('mouseup', () => {{ dragging = false; }});

function togglePanel(side) {{
    const panel = document.getElementById(side);
    const toggleBtn = document.getElementById(side + '-toggle');
    const isCollapsed = panel.classList.toggle('collapsed');
    document.body.classList.toggle(`collapsed-${{side}}`, isCollapsed);
    
    // Поворачиваем стрелку
    if (side === 'left') {{
        toggleBtn.style.transform = isCollapsed ? 'rotate(180deg)' : 'rotate(0deg)';
        toggleBtn.style.left = isCollapsed ? '0' : '340px';
    }} else {{
        toggleBtn.style.transform = isCollapsed ? 'rotate(180deg)' : 'rotate(0deg)';
        toggleBtn.style.right = isCollapsed ? '0' : '340px';
    }}
    
    fitMap();
}}

function clearMap() {{
    document.querySelectorAll('.kp').forEach(g => g.classList.remove('visible','own','alien','highlighted'));
    document.querySelectorAll('.split-row').forEach(r => r.classList.remove('active'));
    pathLine.setAttribute('d', '');
    splitsDiv.innerHTML = 'Выберите участника';
    document.querySelectorAll('.person').forEach(p => p.classList.remove('active'));
}}

function toggleGroup(h, group) {{
    const o = h.classList.contains('open');
    document.querySelectorAll('.group-header,.person-list').forEach(x => x.classList.remove('open'));
    clearMap();
    if (!o) {{
        h.classList.add('open');
        h.nextElementSibling.classList.add('open');
        document.querySelectorAll('.kp').forEach(g => {{
            const id = g.id.replace('kp_', '');
            if (id === 'С1' || id === 'Ф1' || (groupKps[group] && groupKps[group].includes(id))) g.classList.add('visible');
        }});
    }}
}}

function selectRunner(el) {{
    if (!participants) return;
    clearMap();
    el.classList.add('active');
    const group = el.dataset.group;
    const id = parseInt(el.dataset.id);
    const r = participants[group][id];
    const path = r.path;
    const leg = r.leg_times;
    const result = r.result;
    const ownKps = new Set(groupKps[group] || []);
    const taken = new Set(path.filter(k => k !== 'С1' && k !== 'Ф1'));

    document.querySelectorAll('.kp').forEach(g => {{
        const id = g.id.replace('kp_', '');
        if (id === 'С1' || id === 'Ф1') {{
            g.classList.add('visible');
        }} else if (ownKps.has(id)) {{
            g.classList.add('visible');
            if (taken.has(id)) g.classList.add('own');
        }} else if (taken.has(id)) {{
            g.classList.add('visible');
            g.classList.add('alien');
        }}
    }});

    let d = '', prev = null;
    path.forEach(k => {{
        if (!points[k]) return;
        let c = {{x: points[k].cx, y: points[k].cy, r: points[k].r || 30}};
        // Для старта (треугольника) и финиша (двойного круга) увеличиваем радиус для лучшего отображения линии
        if (k === 'С1') c.r = c.r * 1.2;
        if (k === 'Ф1') c.r = c.r * 1.3;
        
        if (prev) {{
            const dx = c.x-prev.x, dy = c.y-prev.y, dist = Math.hypot(dx,dy);
            if (dist > prev.r + c.r + 10) {{
                const ex = prev.x + dx*(prev.r+5)/dist, ey = prev.y + dy*(prev.r+5)/dist;
                const ix = c.x - dx*(c.r+5)/dist, iy = c.y - dy*(c.r+5)/dist;
                d += ` M ${{ex}},${{ey}} L ${{ix}},${{iy}}`;
            }}
        }}
        prev = c;
    }});
    pathLine.setAttribute('d', d);

    let tbl = '<table id="splits-table"><tr><th>№</th><th>КП</th><th>Перегон</th><th>Общее</th></tr><tr class="split-row"><td></td><td>СТАРТ (С1)</td><td>—</td><td>0:00</td></tr>', total = 0;
    for (let i = 1; i < path.length - 1; i++) {{
        const kp = path[i], legTime = (i-1 < leg.length) ? leg[i-1] : '-';
        if (legTime && legTime !== '-' && legTime.includes(':')) total += timeToSec(legTime);
        tbl += `<tr onclick="highlightKP('${{kp}}')" class="split-row"><td>${{i}}</td><td>${{kp}}</td><td>${{legTime}}</td><td>${{total>0?secToTime(total):'—'}}</td></tr>`;
    }}
    let fl = '—', ft = result;
    if (result.includes(':')) {{ const rs = timeToSec(result); if (rs >= total) fl = secToTime(rs-total); }}
    tbl += `<tr class="split-row"><td></td><td style="font-weight:bold;color:#ff6666">ФИНИШ</td><td style="font-weight:bold">${{fl}}</td><td style="font-weight:bold;color:#ff6666">${{ft}}</td></tr></table>`;
    splitsDiv.innerHTML = tbl;
}}

function highlightKP(id) {{
    document.querySelectorAll('.kp').forEach(g => g.classList.remove('highlighted'));
    document.querySelectorAll('.split-row').forEach(r => r.classList.remove('active'));
    const el = document.getElementById('kp_' + id);
    if (el) el.classList.add('highlighted');
    document.querySelectorAll('.split-row').forEach(r => {{ if (r.cells[1].textContent === id) r.classList.add('active'); }});
}}

function timeToSec(t) {{ if (!t || t === '-' || !t.includes(':')) return 0; const a = t.split(':').map(Number); return a.length === 3 ? a[0]*3600 + a[1]*60 + (a[2]||0) : a[0]*60 + a[1]; }}
function secToTime(s) {{ if (s < 3600) return Math.floor(s/60) + ':' + (s%60).toString().padStart(2,'0'); const h=Math.floor(s/3600),m=Math.floor((s%3600)/60),sec=s%60; return h+':'+m.toString().padStart(2,'0')+':'+sec.toString().padStart(2,'0'); }}

window.onload = () => {{ fitMap(); window.onresize = fitMap; }};
</script></body></html>'''

    # Сохраняем данные отдельно
    with open("static/data.json", "w", encoding="utf-8") as f:
        json.dump(participants_data, f, ensure_ascii=False)

    return render_template_string(html)

@app.route('/data.json')
def data_json():
    return send_from_directory('static', 'data.json')

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
