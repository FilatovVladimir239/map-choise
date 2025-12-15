import re
import os
import io
import base64
import time
import json
import math
import urllib.parse
from flask import Flask, render_template_string, send_from_directory, jsonify, Response, request
from PIL import Image
from bs4 import BeautifulSoup
from weasyprint import HTML, CSS
from weasyprint.text.fonts import FontConfiguration

app = Flask(__name__)

MAP_IMAGE = "static/map.png"
COORDS_FILE = "coordinates.txt"
SPLITS_FILE = "splits.htm"
CACHE_FILE = "cache_participants.json"
CACHE_POINTS = "cache_points.json"
GROUPS_FILE = "groups.txt"

points_data = None
participants_data = None
group_kps = {}
group_starts = {}
map_image_b64 = None

def load_group_kps():
    global group_kps, group_starts
    group_kps.clear()
    group_starts.clear()
    if not os.path.exists(GROUPS_FILE):
        print("[WARNING] groups.txt не найден")
        return
    with open(GROUPS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or ":" not in line: continue
            name, kps_str = line.split(":", 1)
            group = name.strip()
            
            parts = kps_str.split()
            start_code = None
            for part in parts:
                if part.startswith("С"):
                    start_code = part.strip()
                    break
            
            kps = [kp.strip() for kp in kps_str.split() 
                  if kp.strip() and kp.strip() not in ["С1", "С2", "Ф1"]]
            
            group_kps[group] = kps
            group_starts[group] = start_code or "С1"

load_group_kps()

def get_map_base64():
    global map_image_b64
    if map_image_b64 is None:
        with open(MAP_IMAGE, "rb") as f:
            map_image_b64 = base64.b64encode(f.read()).decode()
    return map_image_b64

def load_all_points():
    global points_data
    
    if points_data:
        return points_data
    
    if os.path.exists(CACHE_POINTS):
        try:
            with open(CACHE_POINTS, 'r', encoding='utf-8') as f:
                cached = json.load(f)
            
            points = cached['points']
            map_size_raw = cached['map_size']
            
            if isinstance(map_size_raw, (list, tuple)) and len(map_size_raw) >= 2:
                map_size = (map_size_raw[0], map_size_raw[1])
            else:
                raise ValueError("Неверный формат map_size")
            
            print(f"[INFO] Координаты загружены из кеша: {len(points)} КП")
            points_data = (points, map_size)
            return points_data
        except Exception as e:
            print(f"[WARNING] Ошибка чтения кэша координат (будет пересоздан): {e}")
    
    im = Image.open(MAP_IMAGE)
    w, h = im.size
    px_per_mm_x = w / 297.0
    px_per_mm_y = h / 210.0
    r = 3 * max(px_per_mm_x, px_per_mm_y)

    points = {}
    with open(COORDS_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or ":" not in line:
                continue
            kp = line.split(":", 1)[0].strip()
            try:
                mm_part = line.split("(")[1].split(")")[0]
                mm_x, mm_y = map(float, mm_part.split(","))
                
                if kp in ["С1", "С2"]:
                    cx = mm_x * px_per_mm_x
                else:
                    cx = mm_x * px_per_mm_x + 15   
                cy = h - mm_y * px_per_mm_y - 3    
                points[kp] = {"cx": cx, "cy": cy, "r": r, "mm_x": mm_x, "mm_y": mm_y}
            except Exception as e:
                print(f"[ERROR] Ошибка парсинга {kp}: {e}")
                continue

    points_data = (points, (w, h))
    
    try:
        with open(CACHE_POINTS, 'w', encoding='utf-8') as f:
            json.dump({
                'points': points,
                'map_size': [w, h]
            }, f, ensure_ascii=False, indent=2)
        print(f"[INFO] Координаты сохранены в кеш: {CACHE_POINTS}")
    except Exception as e:
        print(f"[WARNING] Не удалось сохранить кеш координат: {e}")
    
    return points_data

def parse_splits_html():
    participants = {g: [] for g in group_kps.keys()}
    
    if not os.path.exists(SPLITS_FILE):
        print(f"[ERROR] Файл {SPLITS_FILE} не найден")
        return participants
        
    try:
        with open(SPLITS_FILE, encoding='windows-1251') as f:
            content = f.read()
            soup = BeautifulSoup(content, 'html.parser')
    except:
        try:
            with open(SPLITS_FILE, encoding='utf-8') as f:
                content = f.read()
                soup = BeautifulSoup(content, 'html.parser')
        except Exception as e2:
            print(f"[ERROR] Ошибка с кодировкой: {e2}")
            return participants
    
    tables = soup.find_all('table', class_='rezult')
    
    for table in tables:
        group_name = None
        
        prev_h2 = table.find_previous('h2')
        if prev_h2:
            text = prev_h2.get_text(strip=True)
            if text in group_kps:
                group_name = text
        
        if not group_name:
            prev_span = table.find_previous('span', class_='group')
            if prev_span:
                text = prev_span.get_text(strip=True)
                text = re.sub(r'\s*\(\d+\)\s*', '', text)
                if text in group_kps:
                    group_name = text
        
        if not group_name:
            continue
        
        header_row = table.find("tr")
        if not header_row:
            continue
            
        header_cells = header_row.find_all(["th", "td"])
        kp_by_col = {}
        leg_start_idx = None
        
        for idx, cell in enumerate(header_cells):
            text = cell.get_text(strip=True)
            if text.startswith("#"):
                m = re.search(r'\((\d+)\)', text)
                if m:
                    kp_by_col[idx] = m.group(1)
                else:
                    m = re.search(r'\[(\d+)\]', text)
                    if m:
                        kp_by_col[idx] = m.group(1)
                
                if idx in kp_by_col and leg_start_idx is None:
                    leg_start_idx = idx
        
        if leg_start_idx is None:
            for idx, cell in enumerate(header_cells):
                text = cell.get_text(strip=True)
                m = re.search(r'(\d{2,3})', text)
                if m and not text.startswith('#'):
                    kp = m.group(1)
                    if kp not in ['240', 'F', 'Ф']:
                        kp_by_col[idx] = kp
                        if leg_start_idx is None:
                            leg_start_idx = idx
        
        if leg_start_idx is None:
            continue
        
        for row in table.find_all("tr")[1:]:
            cells = row.find_all("td")
            if len(cells) < 4:
                continue
                
            place_cell = cells[0].get_text(strip=True)
            place = place_cell.replace(".", "") if "." in place_cell else place_cell
            
            name = ""
            if len(cells) > 2:
                name = cells[2].get_text(strip=True)
            elif len(cells) > 1:
                name = cells[1].get_text(strip=True)
            
            if not name or "Фамилия" in name or "Имя" in name or name.isdigit():
                continue
            
            result = cells[3].get_text(strip=True) if len(cells) > 3 else "-"
            
            path = []
            leg_times = []
            
            for col_idx in range(leg_start_idx, len(cells)):
                kp = kp_by_col.get(col_idx)
                if not kp and col_idx < len(cells):
                    cell_text = cells[col_idx].get_text(strip=True)
                    m = re.search(r'\[(\d+)\]', cell_text)
                    if m:
                        kp = m.group(1)
                    else:
                        m = re.search(r'\b(\d{2,3})\b', cell_text)
                        if m:
                            kp = m.group(1)
                
                if not kp or kp in ["240", "F", "Ф"]:
                    continue
                
                cell_text = cells[col_idx].get_text(strip=True, separator='\n')
                cell_lines = [l.strip() for l in cell_text.split('\n') if l.strip()]
                
                time_str = "-"
                if len(cell_lines) > 1:
                    time_line = cell_lines[1]
                    time_match = re.match(r'(\d+:\d+(?::\d+)?)', time_line)
                    if time_match:
                        time_str = time_match.group(1)
                elif cell_lines:
                    time_match = re.search(r'(\d+:\d+(?::\d+)?)', cell_lines[0])
                    if time_match:
                        time_str = time_match.group(1)
                
                path.append(kp)
                leg_times.append(time_str)
            
            if path:
                start_code = group_starts.get(group_name, "С1")
                
                participants[group_name].append({
                    "name": f"{place}. {name}",
                    "group": group_name,
                    "path": [start_code] + path + ["Ф1"],
                    "leg_times": leg_times,
                    "result": result
                })
    
    total = sum(len(v) for v in participants.values())
    print(f"[SUCCESS] Загружено {total} участников")
    
    return participants

def load_participants():
    global participants_data
    
    if participants_data is not None:
        return participants_data
    
    if os.path.exists(CACHE_FILE):
        try:
            print("[INFO] Загрузка участников из кеша...")
            with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                participants_data = json.load(f)
            total = sum(len(v) for v in participants_data.values())
            print(f"[SUCCESS] Загружено {total} участников из кеша")
            return participants_data
        except Exception as e:
            print(f"[WARNING] Ошибка загрузки кеша: {e}")
            participants_data = None
    
    print("[INFO] Парсинг splits.htm...")
    start_time = time.time()
    
    participants_data = parse_splits_html()
    
    elapsed = time.time() - start_time
    print(f"[SUCCESS] Парсинг завершен за {elapsed:.2f} секунд")
    
    try:
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(participants_data, f, ensure_ascii=False, indent=2)
        print(f"[INFO] Кеш сохранен: {CACHE_FILE}")
    except Exception as e:
        print(f"[ERROR] Ошибка сохранения кеша: {e}")
    
    return participants_data

print("[INFO] Инициализация кеша...")
load_participants()
print("[INFO] Инициализация завершена")

@app.route("/")
def index():
    points, (_, _) = load_all_points()
    participants = load_participants()

    map_b64 = get_map_base64()

    svg = []
    for kp, p in points.items():
        if kp == "Ф1":
            svg.append(f'''
                <g id="kp_{kp}" class="kp">
                    <circle cx="{p["cx"]}" cy="{p["cy"]}" r="{p["r"]*1.3}" fill="none" stroke="#ff0000" stroke-width="6"/>
                    <circle cx="{p["cx"]}" cy="{p["cy"]}" r="{p["r"]*0.7}" fill="none" stroke="#ff0000" stroke-width="6"/>
                    <text x="{p["cx"] + p["r"]*1.3 + 8}" y="{p["cy"] + p["r"]*1.3 + 8}" font-size="32" font-weight="900" text-anchor="start" dominant-baseline="hanging" fill="#ff0000" stroke="#fff" stroke-width="1.5">Ф1</text>
                </g>
            ''')
        elif kp == "С1" or kp == "С2":
            triangle_size = p["r"] * 1.2
            points_str = f'{p["cx"]},{p["cy"] - triangle_size} {p["cx"] - triangle_size},{p["cy"] + triangle_size} {p["cx"] + triangle_size},{p["cy"] + triangle_size}'
            svg.append(f'''
                <g id="kp_{kp}" class="kp">
                    <polygon points="{points_str}" fill="none" stroke="#ff0000" stroke-width="6"/>
                    <text x="{p["cx"] + triangle_size + 8}" y="{p["cy"] + triangle_size + 8}" font-size="32" font-weight="900" text-anchor="start" dominant-baseline="hanging" fill="#ff0000" stroke="#fff" stroke-width="1.5">{kp}</text>
                </g>
            ''')
        else:
            svg.append(f'''
                <g id="kp_{kp}" class="kp">
                    <circle cx="{p["cx"]}" cy="{p["cy"]}" r="{p["r"]}" fill="none" stroke="#ff0000" stroke-width="4"/>
                    <text x="{p["cx"] + p["r"] + 8}" y="{p["cy"] + p["r"] + 8}" font-size="40" font-weight="900" text-anchor="start" dominant-baseline="hanging" fill="#ff0000" stroke="#fff" stroke-width="1.5">{kp}</text>
                </g>
            ''')

    acc = ""
    sorted_groups = list(participants.keys())
    first = sorted_groups[0] if sorted_groups else None

    for g in sorted_groups:
        runners = participants.get(g, [])
        open_class = "open" if g == first else ""
        items = ""
        for i, r in enumerate(runners):
            items += f'<div class="person" data-id="{i}" data-group="{g}" onclick="selectRunner(this)">{r["name"]}</div>'
        
        if not runners:
            items = '<div class="person" style="color:#888;font-style:italic;">Нет участников</div>'
            
        acc += f'<div class="group"><div class="group-header {open_class}" onclick="toggleGroup(this,\'{g}\')">{g} ({len(runners)})</div><div class="person-list {open_class}">{items}</div></div>'

    html = f'''<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"><title>Снежная тропа</title>
<style>
body,html{{margin:0;height:100%;overflow:hidden;background:#111;color:#fff;font-family:Arial,sans-serif}}
#left,#right{{position:fixed;top:0;bottom:0;z-index:10;transition:.4s;background:#222}}
#left{{left:0;width:340px}}
#right{{right:0;width:450px}}
#left.collapsed{{width:0;overflow:hidden}}
#right.collapsed{{width:0;overflow:hidden}}
#left-content, #right-content {{height: calc(100% - 80px); overflow-y: auto; padding: 20px; box-sizing: border-box;}}
#map-container {{margin: 0 450px 80px 340px; height: calc(100% - 80px); display: flex; justify-content: center; align-items: center; background: #000; transition: .4s;}}
body.collapsed-left #map-container {{margin-left:0}}
body.collapsed-right #map-container {{margin-right:0}}
.panel-toggle{{position:fixed;top:50%;z-index:15;background:#c40000;border:none;color:white;width:30px;height:60px;cursor:pointer;font-size:20px;font-weight:bold;display:flex;align-items:center;justify-content:center;transition:.3s}}
.panel-toggle:hover{{background:#a00}}
#left-toggle{{left:340px;border-radius:0 8px 8px 0}}
#right-toggle{{right:450px;border-radius:8px 0 0 8px}}
body.collapsed-left #left-toggle{{left:0;transform:rotate(180deg)}}
body.collapsed-right #right-toggle{{right:0;transform:rotate(180deg)}}
.group-header{{background:#333;padding:12px;border-radius:8px;cursor:pointer;font-weight:bold}}
.group-header.open{{background:#a00}}
.person-list{{max-height:0;overflow:hidden;transition:max-height 0.6s cubic-bezier(0.4, 0, 0.2, 1);background:#2a2a2a;margin-top:5px;border-radius:6px}}
.person-list.open{{max-height:15000px;padding:8px 0}}
.person{{padding:10px 20px;cursor:pointer;border-bottom:1px solid #333}}
.person:hover{{background:#900}}.person.active{{background:#c40000;font-weight:bold}}
.kp circle,.kp polygon{{display:none}}
.kp text{{display:none}}
.kp.visible circle,.kp.visible polygon,.kp.visible text{{display:block}}
.kp.own circle{{stroke:#ff0000;stroke-width:10}}
.kp.own polygon{{stroke:#ff0000;stroke-width:10}}
.kp.alien circle{{stroke:#0088ff;stroke-width:10}}
.kp.alien polygon{{stroke:#0088ff;stroke-width:10}}
.kp.highlighted circle{{stroke:yellow;stroke-width:16;filter:drop-shadow(0 0 12px yellow)}}
.kp.highlighted polygon{{stroke:yellow;stroke-width:16;filter:drop-shadow(0 0 12px yellow)}}
#print-btn{{position:fixed;bottom:90px;left:50%;transform:translateX(-50%);z-index:20;background:#c40000;border:none;color:white;padding:12px 24px;border-radius:6px;cursor:pointer;font-size:16px;font-weight:bold}}
#print-btn:hover {{background: #a00;}}
.footer {{position: fixed; bottom: 0; left: 0; right: 0; height: 80px; background: rgba(20,20,20,0.95); border-top: 2px solid #c40000; padding: 12px 20px; display: flex; align-items: center; z-index: 100; box-sizing: border-box;}}
.footer-logo {{display: flex; align-items: center; gap: 15px;}}
.footer-logo img {{height: 40px; width: auto;}}
.footer-text {{color: #fff; font-size: 14px; text-align: right; font-style: italic; margin-left: auto; padding-left: 30px; max-width: 800px;}}
.footer-text strong {{color: #c40000; font-weight: bold;}}
@media (max-width: 768px) {{ .footer-text {{display: none;}} }}

/* Стили для таблицы сплитов */
.splits-table {{width: 100%; border-collapse: collapse; margin-top: 15px; font-size: 15px; background: #333; border-radius: 8px; overflow: hidden; box-shadow: 0 4px 10px rgba(0,0,0,0.3);}}
.splits-table th {{background: #c40000; color: white; padding: 10px 8px; text-align: center; font-weight: bold;}}
.splits-table td {{padding: 8px; text-align: center; border-bottom: 1px solid #444;}}
.splits-table tr:hover td {{background: #555 !important;}}
.splits-table .start-row td {{color: #88ff88; font-weight: bold;}}
.splits-table .finish-row td {{background: #440000; color: #ff8888; font-weight: bold;}}
.splits-table .split-row.active td {{background: #c40000 !important; color: white !important; font-weight: bold;}}
.distance-summary {{margin-top: 15px; font-size: 16px; color: #ffdd88; text-align: center; font-weight: bold;}}
</style></head><body>
<div id="left"><div id="left-content"><div class="panel-header" onclick="togglePanel('left')">Участники</div><div id="accordion">{acc}</div></div></div>
<button id="left-toggle" class="panel-toggle" onclick="togglePanel('left')">◀</button>
<div id="right"><div id="right-content"><div class="panel-header" onclick="togglePanel('right')">Сплиты</div><div id="splits-info">Выберите участника</div></div></div>
<button id="right-toggle" class="panel-toggle" onclick="togglePanel('right')">▶</button>
<div id="map-container"><div id="map"><img src="data:image/png;base64,{map_b64}" id="mapimg">
<svg style="position:absolute;top:0;left:0;width:100%;height:100%;pointer-events:none">{"".join(svg)}<path id="path" fill="none" stroke="#ff3366" stroke-width="6" opacity="0.7" stroke-linecap="round"/></svg></div></div>
<button id="print-btn" onclick="exportToPDF()">🖨️ Печать карты</button>
<div class="footer">
    <div class="footer-logo">
        <img src="/static/logo.png" alt="Логотип Импульс">
        <div style="color:#fff;font-size:16px;font-weight:bold;">Импульс</div>
    </div>
    <div class="footer-text">
        Создано при поддержке организации по развитию спортивного ориентирования<br>
        и смежных видов спорта "Импульс"
    </div>
</div>
<script>
const points = {json.dumps(points, ensure_ascii=False)};
const groupKps = {json.dumps(group_kps, ensure_ascii=False)};
const groupStarts = {json.dumps(group_starts, ensure_ascii=False)};
let participants = null;
const mapDiv = document.getElementById('map');
const img = document.getElementById('mapimg');
const pathLine = document.getElementById('path');
const splitsDiv = document.getElementById('splits-info');
let scale = 1, posX = 0, posY = 0;
let selectedRunner = null;
let currentRunnerData = null;
let currentGroupKps = null;

fetch('data.json').then(r => r.json()).then(d => participants = d);

function showAllKPs() {{
    document.querySelectorAll('.kp').forEach(g => g.classList.add('visible'));
    pathLine.setAttribute('d', '');
}}

function fitMap() {{
    const leftCollapsed = document.getElementById('left').classList.contains('collapsed');
    const rightCollapsed = document.getElementById('right').classList.contains('collapsed');
    const l = leftCollapsed ? 0 : 340;
    const r = rightCollapsed ? 0 : 450;
    scale = Math.min((innerWidth-l-r)/img.naturalWidth, (innerHeight-80)/img.naturalHeight)*0.94;
    posX = posY = 0; 
    update();
}}
function update() {{ mapDiv.style.transform = `translate(${{posX}}px,${{posY}}px) scale(${{scale}})`; }}

mapDiv.addEventListener('wheel', e => {{ e.preventDefault(); scale *= e.deltaY > 0 ? 0.9 : 1.11; scale = Math.max(0.3, Math.min(20, scale)); update(); }});

let dragging = false, sx, sy;
mapDiv.addEventListener('mousedown', e => {{ if(e.button===0){{ dragging=true; sx=e.clientX-posX; sy=e.clientY-posY; mapDiv.style.cursor='grabbing'; }} }});
document.addEventListener('mousemove', e => {{ if(dragging){{ posX=e.clientX-sx; posY=e.clientY-sy; update(); }} }});
document.addEventListener('mouseup', () => {{ dragging = false; mapDiv.style.cursor = 'grab'; }});

function togglePanel(side) {{
    const panel = document.getElementById(side);
    const toggleBtn = document.getElementById(side + '-toggle');
    const isCollapsed = panel.classList.toggle('collapsed');
    document.body.classList.toggle(`collapsed-${{side}}`, isCollapsed);
    if (side === 'left') {{
        toggleBtn.style.transform = isCollapsed ? 'rotate(180deg)' : 'rotate(0deg)';
        toggleBtn.style.left = isCollapsed ? '0' : '340px';
    }} else {{
        toggleBtn.style.transform = isCollapsed ? 'rotate(180deg)' : 'rotate(0deg)';
        toggleBtn.style.right = isCollapsed ? '0' : '450px';
    }}
    fitMap();
}}

function clearMap() {{
    document.querySelectorAll('.kp').forEach(g => g.classList.remove('visible','own','alien','highlighted'));
    document.querySelectorAll('.split-row').forEach(r => r.classList.remove('active'));
    pathLine.setAttribute('d', '');
    splitsDiv.innerHTML = 'Выберите участника';
    document.querySelectorAll('.person').forEach(p => p.classList.remove('active'));
    selectedRunner = null;
    currentRunnerData = null;
    currentGroupKps = null;
    showAllKPs();
}}

function toggleGroup(h, group) {{
    const o = h.classList.contains('open');
    document.querySelectorAll('.group-header,.person-list').forEach(x => x.classList.remove('open'));
    clearMap();
    if (!o) {{
        h.classList.add('open');
        h.nextElementSibling.classList.add('open');
        currentGroupKps = groupKps[group] || [];
        const startCode = groupStarts[group] || 'С1';
        document.querySelectorAll('.kp').forEach(g => {{
            const id = g.id.replace('kp_', '');
            if (id === startCode || id === 'Ф1' || (groupKps[group] && groupKps[group].includes(id))) {{
                g.classList.add('visible');
            }} else {{
                g.classList.remove('visible');
            }}
        }});
    }} else {{
        currentGroupKps = null;
        showAllKPs();
    }}
}}

function calculateDistance(kp1, kp2) {{
    if (!points[kp1] || !points[kp2]) return 0;
    const x1 = points[kp1].mm_x || 0;
    const y1 = points[kp1].mm_y || 0;
    const x2 = points[kp2].mm_x || 0;
    const y2 = points[kp2].mm_y || 0;
    const dx = x2 - x1;
    const dy = y2 - y1;
    const distanceMm = Math.sqrt(dx*dx + dy*dy);
    const scaleFactor = 4;
    const distanceMeters = Math.round(distanceMm * scaleFactor);
    return distanceMeters;
}}

function selectRunner(el) {{
    if (!participants) return;
    clearMap();
    el.classList.add('active');
    selectedRunner = el;
    const group = el.dataset.group;
    const id = parseInt(el.dataset.id);
    const r = participants[group][id];
    currentRunnerData = r;
    currentGroupKps = groupKps[group] || [];
    const path = r.path;
    const leg = r.leg_times;
    const result = r.result;
    const ownKps = new Set(groupKps[group] || []);
    const taken = new Set(path.filter(k => k !== groupStarts[group] && k !== 'Ф1'));

    const startCode = groupStarts[group] || 'С1';
    
    document.querySelectorAll('.kp').forEach(g => {{
        const id = g.id.replace('kp_', '');
        if (id === startCode || id === 'Ф1') {{
            g.classList.add('visible');
        }} else if (ownKps.has(id)) {{
            g.classList.add('visible');
            if (taken.has(id)) g.classList.add('own');
        }} else if (taken.has(id)) {{
            g.classList.add('visible');
            g.classList.add('alien');
        }} else {{
            g.classList.remove('visible');
        }}
    }});

    let d = '', prev = null;
    path.forEach(k => {{
        if (!points[k]) return;
        let c = {{x: points[k].cx, y: points[k].cy, r: points[k].r || 30}};
        if (k === startCode) c.r = c.r * 1.2;
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

    let totalDistance = 0;
    const distances = [];
    for (let i = 0; i < path.length - 1; i++) {{
        const distance = calculateDistance(path[i], path[i+1]);
        distances.push(distance);
        totalDistance += distance;
    }}

    let tbl = `
    <table class="splits-table">
        <thead>
            <tr>
                <th>№</th>
                <th>КП</th>
                <th>Перегон</th>
                <th>Общее время</th>
                <th>Перегон (м)</th>
                <th>Всего (м)</th>
            </tr>
        </thead>
        <tbody>
            <tr class="start-row">
                <td></td>
                <td><strong>${{startCode}}</strong></td>
                <td>—</td>
                <td>0:00</td>
                <td>—</td>
                <td>0</td>
            </tr>`;

    let total = 0;
    let cumulativeDistance = 0;

    for (let i = 1; i < path.length - 1; i++) {{
        const kp = path[i];
        const legTime = (i-1 < leg.length) ? leg[i-1] : '-';
        const legDistance = distances[i-1];
        cumulativeDistance += legDistance;

        if (legTime && legTime !== '-' && legTime.includes(':')) {{
            total += timeToSec(legTime);
        }}

        tbl += `
            <tr class="split-row" onclick="highlightKP('${{kp}}')">
                <td>${{i}}</td>
                <td><strong>${{kp}}</strong></td>
                <td>${{legTime}}</td>
                <td>${{total > 0 ? secToTime(total) : '—'}}</td>
                <td>${{legDistance}}</td>
                <td>${{cumulativeDistance}}</td>
            </tr>`;
    }}

    const finishDistance = distances[distances.length - 1] || 0;
    cumulativeDistance += finishDistance;

    let fl = '—';
    if (result.includes(':')) {{
        const rs = timeToSec(result);
        if (rs >= total) fl = secToTime(rs - total);
    }}

    tbl += `
            <tr class="finish-row">
                <td></td>
                <td><strong style="color:#ff4444;">Ф1</strong></td>
                <td><strong>${{fl}}</strong></td>
                <td><strong style="color:#ff4444;">${{result}}</strong></td>
                <td><strong>${{finishDistance}}</strong></td>
                <td><strong style="color:#ff4444;">${{cumulativeDistance}}</strong></td>
            </tr>
        </tbody>
    </table>

    <div class="distance-summary">
        Примерная дистанция: <strong>${{cumulativeDistance}} м</strong> (масштаб ≈ 1:4000)
    </div>`;
    
    splitsDiv.innerHTML = tbl;
}}

function highlightKP(id) {{
    document.querySelectorAll('.kp').forEach(g => g.classList.remove('highlighted'));
    document.querySelectorAll('.split-row').forEach(r => r.classList.remove('active'));
    const el = document.getElementById('kp_' + id);
    if (el) el.classList.add('highlighted');
    document.querySelectorAll('.split-row').forEach(r => {{ 
        if (r.cells[1] && r.cells[1].textContent.trim() === id) r.classList.add('active'); 
    }});
}}

function timeToSec(t) {{ 
    if (!t || t === '-' || !t.includes(':')) return 0; 
    const a = t.split(':').map(Number); 
    return a.length === 3 ? a[0]*3600 + a[1]*60 + (a[2]||0) : a[0]*60 + a[1]; 
}}
function secToTime(s) {{ 
    if (s < 3600) return Math.floor(s/60) + ':' + (s%60).toString().padStart(2,'0'); 
    const h=Math.floor(s/3600),m=Math.floor((s%3600)/60),sec=s%60; 
    return h+':'+m.toString().padStart(2,'0')+':'+sec.toString().padStart(2,'0'); 
}}

function exportToPDF() {{
    if (!currentRunnerData) {{
        alert('Сначала выберите участника для печати его маршрута');
        return;
    }}

    const visibleKPs = Array.from(document.querySelectorAll('.kp.visible')).map(kp => {{
        const id = kp.id.replace('kp_', '');
        return {{
            id: id,
            isOwn: kp.classList.contains('own'),
            isAlien: kp.classList.contains('alien')
        }};
    }});

    const exportData = {{
        name: currentRunnerData.name,
        group: currentRunnerData.group,
        path: currentRunnerData.path,
        result: currentRunnerData.result,
        leg_times: currentRunnerData.leg_times,
        timestamp: new Date().toLocaleString('ru-RU'),
        visibleKPs: visibleKPs,
        points: points,
        runnerGroupKps: currentGroupKps || []
    }};

    fetch('/export-pdf', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify(exportData)
    }})
    .then(response => {{
        if (!response.ok) throw new Error('Ошибка сервера');
        return response.blob();
    }})
    .then(blob => {{
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `маршрут_${{currentRunnerData.name.replace(/[^a-z0-9а-яё]/gi, '_')}}.pdf`;
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
        document.body.removeChild(a);
    }})
    .catch(error => {{
        console.error('Ошибка:', error);
        alert('Ошибка при создании PDF: ' + error.message);
    }});
}}

window.onload = () => {{ fitMap(); window.onresize = fitMap; setTimeout(showAllKPs, 100); }};
</script></body></html>'''

    with open("static/data.json", "w", encoding="utf-8") as f:
        json.dump(participants_data, f, ensure_ascii=False)

    return render_template_string(html)

@app.route('/export-pdf', methods=['POST'])
def export_pdf():
    try:
        data = request.get_json()
        map_b64 = get_map_base64()
        runner = data['name']
        group = data['group']
        result = data['result']
        timestamp = data['timestamp']
        path = data['path']
        points = data['points']
        visible_kps_data = data['visibleKPs']
        runner_group_kps = data['runnerGroupKps']

        points_all, map_size = load_all_points()
        map_width, map_height = map_size

        SCALE_FACTOR = 4

        distances = []
        total_distance = 0
        for i in range(len(path) - 1):
            kp1 = path[i]
            kp2 = path[i + 1]
            if kp1 in points and kp2 in points:
                dx = points[kp2]['mm_x'] - points[kp1]['mm_x']
                dy = points[kp2]['mm_y'] - points[kp1]['mm_y']
                dist_mm = math.sqrt(dx*dx + dy*dy)
                dist_m = round(dist_mm * SCALE_FACTOR)
                distances.append(dist_m)
                total_distance += dist_m
            else:
                distances.append(0)

        svg_parts = []
        for kp_id, p in points.items():
            cx, cy, r = p['cx'], p['cy'], p.get('r', 20)

            kp_info = next((k for k in visible_kps_data if k['id'] == kp_id), None)
            if kp_id not in ('С1', 'С2', 'Ф1') and not kp_info:
                continue

            if kp_id == path[0]:
                size = r * 1.5
                polygon = f"{cx},{cy-size} {cx-size},{cy+size} {cx+size},{cy+size}"
                svg_parts.append(f'''
                    <polygon points="{polygon}" fill="none" stroke="#ff0000" stroke-width="10"/>
                    <text x="{cx + size + 15}" y="{cy + size + 15}" font-size="48" fill="#ff0000" font-weight="bold">{kp_id}</text>
                ''')
            elif kp_id == 'Ф1':
                svg_parts.append(f'''
                    <circle cx="{cx}" cy="{cy}" r="{r*1.8}" fill="none" stroke="#ff0000" stroke-width="10"/>
                    <circle cx="{cx}" cy="{cy}" r="{r*1.0}" fill="none" stroke="#ff0000" stroke-width="10"/>
                    <text x="{cx + r*1.8 + 15}" y="{cy + r*1.8 + 15}" font-size="48" fill="#ff0000" font-weight="bold">Ф1</text>
                ''')
            else:
                if kp_info and kp_info.get('isOwn'):
                    color = "#ff0000"
                elif kp_info and kp_info.get('isAlien'):
                    color = "#0066ff"
                else:
                    color = "#ff8888"

                svg_parts.append(f'''
                    <circle cx="{cx}" cy="{cy}" r="{r*1.2}" fill="none" stroke="{color}" stroke-width="8"/>
                    <text x="{cx + r*1.2 + 12}" y="{cy + r*1.2 + 12}" font-size="42" fill="{color}" font-weight="bold">{kp_id}</text>
                ''')

        path_d = ""
        prev = None
        for kp in path:
            if kp not in points:
                continue
            x, y = points[kp]['cx'], points[kp]['cy']
            if prev is None:
                path_d = f"M {x},{y}"
            else:
                path_d += f" L {x},{y}"
            prev = (x, y)

        logo_path = os.path.abspath('static/logo.png')
        logo_url = f"file://{logo_path.replace(os.sep, '/')}"

        html_content = f"""<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="utf-8">
    <title>Маршрут {runner}</title>
    <style>
        @page {{ size: A4 landscape; margin: 10mm; }}
        body {{ margin: 0; padding: 0; font-family: 'DejaVu Sans', Arial, sans-serif; background: white; height: 100vh; display: flex; align-items: center; justify-content: center; }}
        .container {{ position: relative; width: 100%; max-width: 277mm; height: auto; aspect-ratio: {map_width} / {map_height}; max-height: 190mm; margin: auto; box-shadow: 0 0 10px rgba(0,0,0,0.2); }}
        .map {{ width: 100%; height: 100%; object-fit: contain; }}
        /* Удалена надпись "Снежная тропа — маршрут" */
        .info {{ position: absolute; top: 10px; right: 20px; font-size: 26px; color: #000; background: rgba(255,255,255,0.9); padding: 12px 16px; border-radius: 8px; z-index: 10; text-align: right; max-width: 45%; }}
        .timestamp {{ position: absolute; top: 140px; right: 20px; font-size: 18px; color: #555; background: rgba(255,255,255,0.8); padding: 8px 12px; border-radius: 6px; z-index: 10; }}
        .footer {{ position: absolute; bottom: 20px; left: 20px; right: 20px; display: flex; align-items: center; justify-content: space-between; z-index: 10; background: rgba(255,255,255,0.8); padding: 10px; border-radius: 8px; }}
        .footer-logo {{ display: flex; align-items: center; gap: 15px; }}
        .footer-logo img {{ height: 60px; }}
        .footer-text {{ font-size: 18px; color: #333; font-style: italic; }}
        svg {{ position: absolute; top: 0; left: 0; width: 100%; height: 100%; z-index: 5; pointer-events: none; }}
    </style>
</head>
<body>
    <div class="container">
        <img src="data:image/png;base64,{map_b64}" class="map">
        
        <!-- Информация об участнике теперь справа -->
        <div class="info">
            <strong>{runner}</strong><br>
            Группа: {group}<br>
            Результат: {result}<br>
            Дистанция: ≈ {total_distance} м (масштаб 1:{int(1000 * SCALE_FACTOR)})
        </div>
        
        <div class="timestamp">Распечатано: {timestamp}</div>
        
        <svg viewBox="0 0 {map_width} {map_height}">
            {"".join(svg_parts)}
            <path d="{path_d}" fill="none" stroke="#ff3366" stroke-width="16" stroke-linecap="round" opacity="0.9"/>
        </svg>
    </div>
</body>
</html>"""

        font_config = FontConfiguration()
        html_obj = HTML(string=html_content, base_url=os.path.dirname(os.path.abspath(__file__)))
        css = CSS(string="""
            @font-face {
                font-family: 'DejaVu Sans';
                src: url('https://github.com/dejavu-fonts/dejavu-fonts.github.io/raw/master/dejavu-fonts-ttf-2.37/ttf/DejaVuSans.ttf');
            }
            body { font-family: 'DejaVu Sans', sans-serif; }
        """, font_config=font_config)

        buffer = io.BytesIO()
        html_obj.write_pdf(buffer, stylesheets=[css], font_config=font_config)
        buffer.seek(0)

        safe_name = "".join(c if c.isalnum() or c in " _-()" else "_" for c in runner).strip()
        filename = f"маршрут_{safe_name}.pdf"
        encoded_name = urllib.parse.quote(filename)

        return Response(
            buffer.getvalue(),
            mimetype="application/pdf",
            headers={'Content-Disposition': f"attachment; filename*=UTF-8''{encoded_name}"}
        )

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route('/data.json')
def data_json():
    return send_from_directory('static', 'data.json')

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)

