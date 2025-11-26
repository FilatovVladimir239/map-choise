from flask import Flask, render_template_string
from PIL import Image
import io
import base64
import json
import re
from bs4 import BeautifulSoup

app = Flask(__name__)

MAP_IMAGE = "static/map.png"
COORDS_FILE = "coordinates.txt"
SPLITS_FILE = "splits.html"

A4_WIDTH_MM = 210.0
A4_HEIGHT_MM = 297.0

def load_all_points():
    points = {}
    im = Image.open(MAP_IMAGE)
    w, h = im.size
    px_per_mm_x = w / A4_WIDTH_MM
    px_per_mm_y = h / A4_HEIGHT_MM

    radius_px = 6 * px_per_mm_y
    offset_px = 6.5 * px_per_mm_x

    with open(COORDS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or ":" not in line: continue
            kp_id = line.split(":", 1)[0].strip()
            try:
                coords = line.split("(")[1].split(")")[0]
                mm_x, mm_y = map(float, coords.split(","))
                cx = mm_x * px_per_mm_x
                cy = h - mm_y * px_per_mm_y
                points[kp_id] = {
                    "cx": cx,
                    "cy": cy,
                    "r": radius_px,
                    "tx": cx + offset_px,
                    "ty": cy + offset_px
                }
            except: continue
    return points, (w, h), im

def load_participants_from_html():
    participants = {}
    current_group = None

    try:
        with open(SPLITS_FILE, "r", encoding="windows-1251") as f:
            html = f.read()
    except Exception as e:
        print("Ошибка чтения splits.html:", e)
        return participants

    soup = BeautifulSoup(html, "html.parser")

    for a in soup.find_all("a"):
        if a.get("name") in ["МЮ", "ЖЮ", "Мужчины", "Женщины", "ЖВ", "МВ"]:
            current_group = a["name"]
            participants[current_group] = []

    for table in soup.find_all("table", class_="rezult"):
        prev_a = table.find_previous("a", {"name": True})
        if prev_a and prev_a.get("name") in participants:
            current_group = prev_a["name"]

        rows = table.find_all("tr")
        if len(rows) < 2: continue

        for row in rows[1:]:
            cells = row.find_all("td")
            if len(cells) < 10: continue

            place = cells[0].get_text(strip=True).replace(".", "").strip()
            name_cell = cells[2].get_text(strip=True)
            if "Фамилия" in name_cell or not name_cell.strip(): continue

            # Результат — колонка 8 (индекс 8)
            result_str = cells[8].get_text(strip=True) if len(cells) > 8 else "-"

            path = []
            leg_times = []

            for cell in cells[10:]:
                text = cell.get_text(separator="\n", strip=True)
                lines = [l.strip() for l in text.split("\n") if l.strip()]
                if len(lines) < 1: continue

                # Первая строка — КП, вторая (если есть) — перегон
                kp_match = re.search(r"\[(\w+)\]", lines[0])
                if not kp_match: continue
                kp = kp_match.group(1)
                if kp not in ["С1", "Ф1"] and not kp.isdigit(): continue

                leg_time = lines[1] if len(lines) > 1 else "-"
                path.append(kp)
                leg_times.append(leg_time)

            final_path = ["С1"] + path + ["Ф1"]
            display_name = f"{place}. {name_cell}"

            if current_group:
                participants[current_group].append({
                    "name": display_name,
                    "path": final_path,
                    "leg_times": leg_times,
                    "result": result_str
                })

    return participants

@app.route("/")
def index():
    points, (w, h), im = load_all_points()
    participants = load_participants_from_html()

    if not participants:
        return "<h1>Ошибка: не найдены участники в splits.html</h1>"

    buf = io.BytesIO()
    im.save(buf, format="PNG")
    img_b64 = base64.b64encode(buf.getvalue()).decode()

    svg_kp = []
    for kp_id, p in points.items():
        if kp_id == "Ф1":
            svg_kp.append(f'''
            <g id="kp_{kp_id}" class="kp missed">
                <circle cx="{p["cx"]}" cy="{p["cy"]}" r="{p["r"]*1.5}" fill="none"/>
                <circle cx="{p["cx"]}" cy="{p["cy"]}" r="{p["r"]}" fill="none"/>
                <text x="{p["tx"]}" y="{p["ty"]}" font-size="38" font-weight="900"
                      text-anchor="middle" dominant-baseline="middle">ФИНИШ</text>
            </g>''')
        else:
            svg_kp.append(f'''
            <g id="kp_{kp_id}" class="kp missed">
                <circle cx="{p["cx"]}" cy="{p["cy"]}" r="{p["r"]}" fill="none"/>
                <text x="{p["tx"]}" y="{p["ty"]}" font-size="42" font-weight="900"
                      text-anchor="middle" dominant-baseline="middle">{kp_id}</text>
            </g>''')
    svg_kp_str = "".join(svg_kp)

    accordion = ""
    first_group = next(iter(participants), None)
    for group_name, runners in participants.items():
        is_first = group_name == first_group
        open_class = 'open' if is_first else ''
        person_items = ""
        for runner in runners:
            path_b64 = base64.b64encode(json.dumps(runner["path"]).encode()).decode()
            leg_times_b64 = base64.b64encode(json.dumps(runner["leg_times"]).encode()).decode()
            result_b64 = base64.b64encode(runner["result"].encode()).decode()
            person_items += f'<div class="person" data-path="{path_b64}" data-leg="{leg_times_b64}" data-result="{result_b64}" onclick="selectRunner(this)">{runner["name"]}</div>'

        accordion += f'''
        <div class="group">
            <div class="group-header {open_class}" onclick="toggleGroup(this)">
                > {group_name} ({len(runners)})
            </div>
            <div class="person-list {open_class}">{person_items}</div>
        </div>
        '''

    points_json = json.dumps(points, ensure_ascii=False)

    html = f"""<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="utf-8">
    <title>По следам истории 2025 — Результаты</title>
    <style>
        body,html {{margin:0;height:100%;overflow:hidden;background:#111;font-family:Arial,sans-serif;color:#fff}}
        #left,#right {{position:fixed;top:0;bottom:0;width:340px;background:#222;padding:20px;overflow-y:auto;z-index:10;transition:.4s}}
        #left {{left:0}} #right {{right:0}}
        #left.collapsed {{transform:translateX(-340px)}} #right.collapsed {{transform:translateX(340px)}}
        #map-container {{margin:0 340px;height:100%;display:flex;justify-content:center;align-items:center;background:#000;transition:.4s}}
        body.collapsed-left #map-container {{margin-left:0}} body.collapsed-right #map-container {{margin-right:0}}
        .panel-header {{cursor:pointer;background:#c40000;padding:12px;border-radius:8px;margin-bottom:10px;font-weight:bold}}
        .group-header {{background:#333;padding:12px;border-radius:8px;cursor:pointer;font-weight:bold}}
        .group-header.open {{background:#a00}}
        .person-list {{max-height:0;overflow:hidden;transition:.4s;background:#2a2a2a;margin-top:5px;border-radius:6px}}
        .person-list.open {{max-height:1200px;padding:8px 0}}
        .person {{padding:10px 20px;cursor:pointer;border-bottom:1px solid #333}}
        .person:hover {{background:#900}} .person.active {{background:#c40000;font-weight:bold}}
        #splits-table {{width:100%;border-collapse:collapse;font-size:14px}}
        #splits-table th,#splits-table td {{padding:8px;text-align:left;border-bottom:1px solid #444;cursor:pointer}}
        #splits-table th {{background:#333}} #splits-table tr:hover td {{background:#444}}
        #splits-table tr.active td {{background:#c40000 !important}}

        .kp circle {{stroke:#ff0000 !important}}
        .kp text {{fill:#ff0000 !important; font-weight:900 !important; stroke:#fff !important; stroke-width:4 !important}}
        .kp.taken circle {{stroke-width:8 !important}}
        .kp.missed circle {{stroke-width:4 !important}}
        .kp.highlighted circle {{stroke:yellow !important; stroke-width:14 !important; filter:drop-shadow(0 0 14px yellow)}}
    </style>
</head>
<body>
<div id="left">
    <div class="panel-header" onclick="togglePanel('left')">> Участники</div>
    <div id="accordion">{accordion}</div>
</div>
<div id="right">
    <div class="panel-header" onclick="togglePanel('right')">< Сплиты</div>
    <div id="splits-info">Выберите участника</div>
</div>
<div id="map-container">
    <div id="map">
        <img src="data:image/png;base64,{img_b64}" id="mapimg">
        <svg style="position:absolute;top:0;left:0;width:100%;height:100%;pointer-events:none">
            {svg_kp_str}
            <path id="path" fill="none" stroke="#ff3366" stroke-width="10" opacity="0.9" stroke-linecap="round"/>
        </svg>
    </div>
</div>

<script>
    const mapDiv = document.getElementById('map');
    const img = document.getElementById('mapimg');
    const pathLine = document.getElementById('path');
    const splitsDiv = document.getElementById('splits-info');
    let scale = 1, posX = 0, posY = 0;
    const points = {points_json};

    function fitMap() {{
        const lw = document.getElementById('left').classList.contains('collapsed') ? 0 : 340;
        const rw = document.getElementById('right').classList.contains('collapsed') ? 0 : 340;
        scale = Math.min((window.innerWidth - lw - rw) / img.naturalWidth, window.innerHeight / img.naturalHeight) * 0.94;
        posX = posY = 0;
        update();
    }}
    function update() {{ mapDiv.style.transform = `translate(${{posX}}px,${{posY}}px) scale(${{scale}})`; }}

    mapDiv.addEventListener('wheel', e => {{ e.preventDefault(); scale *= e.deltaY > 0 ? 0.9 : 1.11; scale = Math.max(0.3, Math.min(20, scale)); update(); }});
    let dragging = false, sx, sy;
    mapDiv.addEventListener('mousedown', e => {{ if(e.button===0){{dragging=true; sx=e.clientX-posX; sy=e.clientY-posY; mapDiv.style.cursor='grabbing'}}}});
    document.addEventListener('mousemove', e => {{if(dragging){{posX=e.clientX-sx; posY=e.clientY-sy; update()}}}});
    document.addEventListener('mouseup', () => dragging = false);

    function togglePanel(side) {{ document.getElementById(side).classList.toggle('collapsed'); fitMap(); }}

    function clearMap() {{
        document.querySelectorAll('.kp').forEach(g => {{ g.className = 'kp missed'; g.classList.remove('highlighted'); }});
        document.querySelectorAll('.split-row').forEach(r => r.classList.remove('active'));
        pathLine.setAttribute('d', '');
        splitsDiv.innerHTML = "Выберите участника";
        document.querySelectorAll('.person').forEach(p => p.classList.remove('active'));
    }}

    function toggleGroup(header) {{
        const isOpen = header.classList.contains('open');
        document.querySelectorAll('.group-header').forEach(h => h.classList.remove('open'));
        document.querySelectorAll('.person-list').forEach(l => l.classList.remove('open'));
        clearMap();
        if (!isOpen) {{ header.classList.add('open'); header.nextElementSibling.classList.add('open'); }}
    }}

    // Поддержка чч:мм:сс и мм:сс
    function timeToSec(t) {{
        if (!t || t === "-" || !t.includes(":")) return 0;
        const parts = t.split(":").map(Number);
        if (parts.length === 3) return parts[0]*3600 + parts[1]*60 + (parts[2]||0);
        if (parts.length === 2) return parts[0]*60 + (parts[1]||0);
        return 0;
    }}

    function secToTime(sec) {{
        if (sec < 3600) {{
            const m = Math.floor(sec / 60);
            const s = sec % 60;
            return `${{m}}:${{s.toString().padStart(2,'0')}}`;
        }} else {{
            const h = Math.floor(sec / 3600);
            const m = Math.floor((sec % 3600) / 60);
            const s = sec % 60;
            return `${{h}}:${{m.toString().padStart(2,'0')}}:${{s.toString().padStart(2,'0')}}`;
        }}
    }}

    function selectRunner(el) {{
        clearMap();
        el.classList.add('active');

        try {{
            const path = JSON.parse(atob(el.dataset.path));
            const legTimes = JSON.parse(atob(el.dataset.leg));
            const resultStr = atob(el.dataset.result).trim();
            const taken = new Set(path);

            // Раскрашиваем КП
            document.querySelectorAll('.kp').forEach(g => {{
                const id = g.id.replace('kp_', '');
                if (taken.has(id)) g.classList.add('taken');
                else g.classList.remove('taken');
            }});

            // Рисуем путь
            let d = ""; let prev = null;
            path.forEach(kp => {{
                if (!points[kp]) return;
                const cur = {{ x: points[kp].cx, y: points[kp].cy, r: points[kp].r || 30 }};
                if (prev) {{
                    const dx = cur.x - prev.x, dy = cur.y - prev.y;
                    const dist = Math.hypot(dx, dy);
                    if (dist > prev.r + cur.r + 10) {{
                        const ex = prev.x + dx * (prev.r + 5) / dist;
                        const ey = prev.y + dy * (prev.r + 5) / dist;
                        const ix = cur.x - dx * (cur.r + 5) / dist;
                        const iy = cur.y - dy * (cur.r + 5) / dist;
                        d += ` M ${{ex}},${{ey}} L ${{ix}},${{iy}}`;
                    }}
                }}
                prev = cur;
            }});
            pathLine.setAttribute('d', d);

            // === ИДЕАЛЬНЫЕ СПЛИТЫ ===
            let table = '<table id="splits-table"><tr><th>№</th><th>КП</th><th>Перегон</th><th>Общее</th></tr>';
            let totalSec = 0;

            // Старт
            table += '<tr class="split-row"><td></td><td>СТАРТ</td><td>—</td><td>0:00</td></tr>';

            // КП
            for (let i = 1; i < path.length - 1; i++) {{
                const kp = path[i];
                const legTime = (i - 1 < legTimes.length) ? legTimes[i - 1] : "-";

                if (legTime && legTime !== "-" && legTime.includes(":")) {{
                    const legSec = timeToSec(legTime);
                    totalSec += legSec;
                }}

                const totalDisplay = totalSec > 0 ? secToTime(totalSec) : "—";

                table += `<tr onclick="highlightKP('${{kp}}')" class="split-row">
                    <td>${{i}}</td><td>${{kp}}</td><td>${{legTime}}</td><td>${{totalDisplay}}</td>
                </tr>`;
            }}

            // ФИНИШ — из результата!
            let finishLeg = "—";
            let finishTotal = "—";

            if (resultStr && resultStr.includes(":")) {{
                finishTotal = resultStr;
                const resultSec = timeToSec(resultStr);

                if (totalSec > 0 && resultSec >= totalSec) {{
                    const legSec = resultSec - totalSec;
                    finishLeg = secToTime(legSec);
                }}
            }}

            table += `<tr class="split-row">
                <td></td>
                <td style="font-weight:bold;color:#ff6666">ФИНИШ</td>
                <td style="font-weight:bold">${{finishLeg}}</td>
                <td style="font-weight:bold;color:#ff6666">${{finishTotal}}</td>
            </tr></table>`;

            splitsDiv.innerHTML = table;

        }} catch(e) {{ console.error(e); splitsDiv.innerHTML = "Ошибка данных"; }}
    }}

    function highlightKP(id) {{
        document.querySelectorAll('.kp').forEach(g => g.classList.remove('highlighted'));
        document.querySelectorAll('.split-row').forEach(r => r.classList.remove('active'));
        const el = document.getElementById('kp_' + id);
        if (el) el.classList.add('highlighted');
        document.querySelectorAll('.split-row').forEach(r => {{
            if (r.cells[1].textContent === id) r.classList.add('active');
        }});
    }}

    window.onload = () => {{ fitMap(); window.onresize = fitMap;
        const first = document.querySelector('.group-header');
        if (first) toggleGroup(first);
    }};
</script>
</body>
</html>"""

    return render_template_string(html)

if __name__ == "__main__":
    app.run(debug=True, port=5000)
