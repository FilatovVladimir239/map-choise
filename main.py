from flask import Flask, render_template_string, send_from_directory, jsonify, Response, request
from PIL import Image, ImageDraw, ImageFont
import io, base64, json, re, os, tempfile
from bs4 import BeautifulSoup
import math
from weasyprint import HTML, CSS
from weasyprint.text.fonts import FontConfiguration
import json
import os
import urllib.parse
import base64

app = Flask(__name__)

MAP_IMAGE = "static/map.png"
COORDS_FILE = "coordinates.txt"
SPLITS_FILE = "splits.html"
CACHE_FILE = "cache_participants.json"
GROUPS_FILE = "groups.txt"

A4_WIDTH_MM = 297.0
A4_HEIGHT_MM = 210.0

points_data = None
participants_data = None
splits_mtime = 0
group_kps = {}
group_starts = {}  # Словарь для хранения старта группы (С1 или С2)
map_image_b64 = None  # Кеш для base64 карты

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
            
            # Ищем старт (С1 или С2) в строке
            parts = kps_str.split()
            start_code = None
            for part in parts:
                if part.startswith("С"):
                    start_code = part.strip()
                    break
            
            # Извлекаем КП, исключая С1/С2 и Ф1
            kps = [kp.strip() for kp in kps_str.split() 
                  if kp.strip() and kp.strip() not in ["С1", "С2", "Ф1"]]
            
            group_kps[group] = kps
            group_starts[group] = start_code or "С1"  # По умолчанию С1

load_group_kps()

def get_map_base64():
    """Возвращает base64 изображение карты с кешированием"""
    global map_image_b64
    if map_image_b64 is None:
        with open(MAP_IMAGE, "rb") as f:
            map_image_b64 = base64.b64encode(f.read()).decode()
    return map_image_b64

def load_all_points():
    """Загружает координаты КП, возвращает points и размеры карты"""
    global points_data
    if points_data:
        return points_data

    im = Image.open(MAP_IMAGE)
    w, h = im.size
    px_per_mm_x = w / A4_WIDTH_MM
    px_per_mm_y = h / A4_HEIGHT_MM

    # Радиус КП ≈ 3 мм на карте (уменьшен с 4 мм)
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
                    cx = mm_x * px_per_mm_x  # Убрали +15 для стартов
                else:
                    cx = mm_x * px_per_mm_x + 15   
                cy = h - mm_y * px_per_mm_y - 3    
                points[kp] = {"cx": cx, "cy": cy, "r": r, "mm_x": mm_x, "mm_y": mm_y}
            except Exception as e:
                print(f"[ERROR] Ошибка парсинга {kp}: {e}")
                continue

    points_data = (points, (w, h))
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
                if kp in ["С1", "С2", "Ф1"]: continue
                t = lines[1] if i > 0 and len(lines) > 1 else (
                    re.search(r"^(\d+:\d+)", lines[0]).group(1) if i == 0 and re.search(r"^(\d+:\d+)", lines[0]) else "-"
                )
                path.append(kp)
                legs.append(t)

            if cur_group:
                # Добавляем соответствующий старт для группы
                start_code = group_starts.get(cur_group, "С1")
                participants.setdefault(cur_group, []).append({
                    "name": f"{place}. {name}",
                    "group": cur_group,
                    "path": [start_code] + path + ["Ф1"],
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

def get_available_font():
    """Проверяет доступные шрифты с поддержкой кириллицы"""
    font_paths = [
        "arial.ttf",
        "DejaVuSans.ttf",
        "LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/tahoma.ttf",
        "C:/Windows/Fonts/verdana.ttf",
    ]
    
    for font_path in font_paths:
        try:
            font = ImageFont.truetype(font_path, 40)
            return font_path
        except Exception as e:
            continue
    
    print("[WARNING] Не найден шрифт с поддержкой кириллицы, используется стандартный")
    return None

def create_map_image_with_route(map_image_path, points, visible_kps, path_points, runner_name, group_name, runner_group_kps):
    """Создает изображение карты с маршрутом"""
    # Открываем основное изображение карты
    base_image = Image.open(map_image_path).convert("RGBA")
    draw = ImageDraw.Draw(base_image)
    
    # Получаем доступный шрифт
    font_path = get_available_font()
    try:
        if font_path:
            kp_font = ImageFont.truetype(font_path, 35)
            title_font = ImageFont.truetype(font_path, 45)
            info_font = ImageFont.truetype(font_path, 32)
        else:
            kp_font = ImageFont.load_default()
            title_font = ImageFont.load_default()
            info_font = ImageFont.load_default()
    except:
        kp_font = ImageFont.load_default()
        title_font = ImageFont.load_default()
        info_font = ImageFont.load_default()
    
    # Рисуем маршрут (линию)
    if path_points and len(path_points) > 1:
        for i in range(1, len(path_points)):
            start_point = path_points[i-1]
            end_point = path_points[i]
            draw.line([start_point, end_point], 
                     fill=(255, 51, 102), width=10)
    
    # Получаем старт для этой группы
    start_code = group_starts.get(group_name, "С1")
    
    # Рисуем только КП группы спортсмена + старт/финиш + чужие КП которые он взял
    kps_to_draw = [start_code, 'Ф1'] + runner_group_kps
    
    # Добавляем чужие КП которые взял спортсмен
    for kp_info in visible_kps:
        if kp_info.get('isAlien') and kp_info['id'] not in kps_to_draw:
            kps_to_draw.append(kp_info['id'])
    
    for kp_id in kps_to_draw:
        if kp_id not in points:
            continue
            
        point = points[kp_id]
        x, y = point['cx'], point['cy']
        r = point.get('r', 20)
        
        # Находим информацию о КП из visible_kps
        kp_info = next((kp for kp in visible_kps if kp['id'] == kp_id), None)
        
        if kp_id == start_code:
            # Старт - треугольник зеленого цвета
            size = r * 1.5
            draw.polygon([
                (x, y - size),
                (x - size, y + size),
                (x + size, y + size)
            ], outline=(0, 128, 0), width=8)
            # Подпись старта
            try:
                if font_path:
                    draw.text((x + size + 10, y + size + 10), start_code, 
                             fill=(0, 128, 0), font=title_font, 
                             stroke_width=2, stroke_fill=(255, 255, 255))
            except:
                pass
                
        elif kp_id == 'Ф1':
            # Финиш - двойной круг красного цвета
            draw.ellipse([x - r*1.5, y - r*1.5, x + r*1.5, y + r*1.5], 
                       outline=(255, 0, 0), width=8)
            draw.ellipse([x - r*0.8, y - r*0.8, x + r*0.8, y + r*0.8], 
                       outline=(255, 0, 0), width=8)
            # Подпись Ф1
            try:
                if font_path:
                    draw.text((x + r*1.5 + 10, y + r*1.5 + 10), "Ф1", 
                             fill=(255, 0, 0), font=title_font,
                             stroke_width=2, stroke_fill=(255, 255, 255))
            except:
                pass
        else:
            # Обычные КП - только контур, без заливки
            if kp_info and kp_info.get('isOwn'):
                color = "#ff0000"
            elif kp_info and kp_info.get('isAlien'):
                color = "#0066ff"
            elif kp_id in runner_group_kps:
                color = "#ff0000"        # свои КП группы — всегда ярко-красные
            else:
                color = "#ff8888"
                
            # Рисуем кружок КП (только контур)
            draw.ellipse([x - r, y - r, x + r, y + r], 
                        outline=color, width=4)
            
            # Подпись КП (только цифры, чтобы избежать проблем с кодировкой)
            try:
                # Проверяем, что КП состоит только из цифр
                if kp_id.isdigit():
                    draw.text((x + r + 8, y + r + 8), kp_id, 
                             fill=color, font=kp_font,
                             stroke_width=1, stroke_fill=(255, 255, 255))
            except:
                pass
    
    # Добавляем информацию об участнике в верхний левый угол (только латиницей)
    try:
        # Используем только латинские символы для информации
        safe_name = "".join(c for c in runner_name if c.isalnum() or c in " ._-")
        safe_group = "".join(c for c in group_name if c.isalnum() or c in " ._-")
        info_text = f"{safe_name}\n{safe_group}"
        
        draw.rectangle([15, 15, 400, 100], fill=(255, 255, 255, 200))
        if font_path:
            draw.text((20, 20), info_text, fill=(0, 0, 0), font=info_font)
    except:
        pass
    
    return base_image

@app.route("/")
def index():
    points, (_, _) = load_all_points()
    participants = load_participants()
    if not participants:
        return "<h1 style='color:#c40000;text-align:center;margin-top:100px'>Нет данных<br><small>Проверьте splits.html и groups.txt</small></h1>"

    # Получаем base64 карты из кеша
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
    first = next(iter(participants), None)
    for g, runners in participants.items():
        open_class = "open" if g == first else ""
        items = ""
        for i, r in enumerate(runners):
            items += f'<div class="person" data-id="{i}" data-group="{g}" onclick="selectRunner(this)">{r["name"]}</div>'
        acc += f'<div class="group"><div class="group-header {open_class}" onclick="toggleGroup(this,\'{g}\')">{g} ({len(runners)})</div><div class="person-list {open_class}">{items}</div></div>'

    html = f'''<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"><title>Снежная тропа</title>
<style>
body,html{{margin:0;height:100%;overflow:hidden;background:#111;color:#fff;font-family:Arial,sans-serif}}
#left,#right{{position:fixed;top:0;bottom:0;z-index:10;transition:.4s}}
#left{{left:0;width:340px;background:#222}}
#right{{right:0;width:450px;background:#222}}
#left.collapsed{{width:0;overflow:hidden}}
#right.collapsed{{width:0;overflow:hidden}}
#left-content,#right-content{{padding:20px;height:100%;overflow-y:auto}}
#map-container{{margin:0 450px 0 340px;height:100%;display:flex;justify-content:center;align-items:center;background:#000;transition:.4s}}
body.collapsed-left #map-container{{margin-left:0}}body.collapsed-right #map-container{{margin-right:0}}
.panel-toggle{{position:fixed;top:50%;z-index:15;background:#c40000;border:none;color:white;width:30px;height:60px;cursor:pointer;font-size:20px;font-weight:bold;display:flex;align-items:center;justify-content:center;transition:.3s}}
.panel-toggle:hover{{background:#a00}}
#left-toggle{{left:340px;border-radius:0 8px 8px 0}}
#right-toggle{{right:450px;border-radius:8px 0 0 8px}}
body.collapsed-left #left-toggle{{left:0;transform:rotate(180deg)}}
body.collapsed-right #right-toggle{{right:0;transform:rotate(180deg)}}
.panel-header{{position:relative;cursor:pointer;background:#c40000;padding:12px;border-radius:8px;margin-bottom:10px;font-weight:bold;min-height:20px}}
.panel-header:hover{{background:#a00}}
.group-header{{background:#333;padding:12px;border-radius:8px;cursor:pointer;font-weight:bold}}
.group-header.open{{background:#a00}}
.person-list{{max-height:0;overflow:hidden;transition:.4s;background:#2a2a2a;margin-top:5px;border-radius:6px}}
.person-list.open{{max-height:1200px;padding:8px 0}}
.person{{padding:10px 20px;cursor:pointer;border-bottom:1px solid #333}}
.person:hover{{background:#900}}.person.active{{background:#c40000;font-weight:bold}}
#splits-table{{width:100%;border-collapse:collapse;font-size:13px;border:1px solid #444}}
#splits-table th,#splits-table td{{padding:6px;text-align:left;border-bottom:1px solid #444;cursor:pointer}}
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
#print-btn{{position:fixed;bottom:20px;left:50%;transform:translateX(-50%);z-index:20;background:#c40000;border:none;color:white;padding:12px 24px;border-radius:6px;cursor:pointer;font-size:16px;font-weight:bold}}
#print-btn:hover{{background:#a00}}
</style></head><body>
<div id="left">
    <div id="left-content">
        <div class="panel-header" onclick="togglePanel('left')">
            <span>Участники</span>
        </div>
        <div id="accordion">{acc}</div>
    </div>
</div>
<button id="left-toggle" class="panel-toggle" onclick="togglePanel('left')">◀</button>

<div id="right">
    <div id="right-content">
        <div class="panel-header" onclick="togglePanel('right')">
            <span>Сплиты</span>
        </div>
        <div id="splits-info">Выберите участника</div>
    </div>
</div>
<button id="right-toggle" class="panel-toggle" onclick="togglePanel('right')">▶</button>

<div id="map-container">
    <div id="map"><img src="data:image/png;base64,{map_b64}" id="mapimg">
    <svg style="position:absolute;top:0;left:0;width:100%;height:100%;pointer-events:none">{"".join(svg)}<path id="path" fill="none" stroke="#ff3366" stroke-width="10" opacity="0.9" stroke-linecap="round"/></svg></div>
</div>

<button id="print-btn" onclick="exportToPDF()">🖨️ Печать карты</button>

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
    
    // Получаем координаты в миллиметрах
    const x1 = points[kp1].mm_x || 0;
    const y1 = points[kp1].mm_y || 0;
    const x2 = points[kp2].mm_x || 0;
    const y2 = points[kp2].mm_y || 0;
    
    // Рассчитываем расстояние в миллиметрах
    const dx = x2 - x1;
    const dy = y2 - y1;
    const distanceMm = Math.sqrt(dx*dx + dy*dy);
    
    // Переводим в метры (1 мм = 4 метра по масштабу карты) - изменено с 7.5 на 4
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

    // Рассчитываем расстояния
    let totalDistance = 0;
    const distances = [];
    for (let i = 0; i < path.length - 1; i++) {{
        const distance = calculateDistance(path[i], path[i+1]);
        distances.push(distance);
        totalDistance += distance;
    }}

    // Поменял местами колонки "(м)" и "Общее"
    let tbl = '<table id="splits-table"><tr><th>№</th><th>КП</th><th>Перегон</th><th>Общее</th><th>(м)</th><th>Всего</th></tr>';
    tbl += `<tr class="split-row"><td></td><td>${{startCode}}</td><td>—</td><td>0:00</td><td>—</td><td>0</td></tr>`;
    
    let total = 0;
    let cumulativeDistance = 0;
    
    for (let i = 1; i < path.length - 1; i++) {{
        const kp = path[i];
        const legTime = (i-1 < leg.length) ? leg[i-1] : '-';
        const legDistance = distances[i-1];
        cumulativeDistance += legDistance;
        
        if (legTime && legTime !== '-' && legTime.includes(':')) total += timeToSec(legTime);
        
        // Поменял местами значения колонок
        tbl += `<tr onclick="highlightKP('${{kp}}')" class="split-row">
            <td>${{i}}</td>
            <td>${{kp}}</td>
            <td>${{legTime}}</td>
            <td>${{total>0?secToTime(total):'—'}}</td>
            <td>${{legDistance}}</td>
            <td>${{cumulativeDistance}}</td>
        </tr>`;
    }}
    
    let fl = '—', ft = result;
    const finishDistance = distances[distances.length - 1] || 0;
    cumulativeDistance += finishDistance;
    
    if (result.includes(':')) {{ 
        const rs = timeToSec(result); 
        if (rs >= total) fl = secToTime(rs-total); 
    }}
    
    // Поменял местами значения колонок для финишной строки
    tbl += `<tr class="split-row">
        <td></td>
        <td style="font-weight:bold;color:#ff6666">Ф1</td>
        <td style="font-weight:bold">${{fl}}</td>
        <td style="font-weight:bold;color:#ff6666">${{ft}}</td>
        <td style="font-weight:bold">${{finishDistance}}</td>
        <td style="font-weight:bold;color:#ff6666">${{cumulativeDistance}}</td>
    </tr></table>`;
    
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

function exportToPDF() {{
    if (!currentRunnerData) {{
        alert('Сначала выберите участника для печати его маршрута');
        return;
    }}

    // Собираем данные о видимых КП
    const visibleKPs = Array.from(document.querySelectorAll('.kp.visible')).map(kp => {{
        const id = kp.id.replace('kp_', '');
        return {{
            id: id,
            isOwn: kp.classList.contains('own'),
            isAlien: kp.classList.contains('alien')
        }};
    }});

    // Создаем простой маршрут через контрольные точки
    const pathPoints = currentRunnerData.path.map(kp => {{
        if (points[kp]) {{
            return [points[kp].cx, points[kp].cy];
        }}
        return null;
    }}).filter(point => point !== null);

    const exportData = {{
        name: currentRunnerData.name,
        group: currentRunnerData.group,
        path: currentRunnerData.path,
        result: currentRunnerData.result,
        leg_times: currentRunnerData.leg_times,
        timestamp: new Date().toLocaleString('ru-RU'),
        visibleKPs: visibleKPs,
        pathData: pathLine.getAttribute('d'),
        pathPoints: pathPoints,
        points: points,
        runnerGroupKps: currentGroupKps || []
    }};

    fetch('/export-pdf', {{
        method: 'POST',
        headers: {{
            'Content-Type': 'application/json',
        }},
        body: JSON.stringify(exportData)
    }})
    .then(response => {{
        if (!response.ok) {{
            throw new Error('Ошибка сервера');
        }}
        return response.blob();
    }})
    .then(blob => {{
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.style.display = 'none';
        a.href = url;
        a.download = `маршрут_${{currentRunnerData.name.replace(/[^a-z0-9а-яё]/gi, '_')}}.pdf`;
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
        document.body.removeChild(a);
    }})
    .catch(error => {{
        console.error('Ошибка при создании PDF:', error);
        alert('Ошибка при создании PDF файла: ' + error.message);
    }});
}}

window.onload = () => {{ 
    fitMap(); 
    window.onresize = fitMap;
    setTimeout(showAllKPs, 100);
}};
</script></body></html>'''

    with open("static/data.json", "w", encoding="utf-8") as f:
        json.dump(participants_data, f, ensure_ascii=False)

    return render_template_string(html)

@app.route('/export-pdf', methods=['POST'])
def export_pdf():
    try:
        from weasyprint import HTML, CSS
        from weasyprint.text.fonts import FontConfiguration

        data = request.get_json()
        # Используем кешированный base64 вместо чтения файла
        map_b64 = get_map_base64()
        runner           = data['name']
        group            = data['group']
        result           = data['result']
        timestamp        = data['timestamp']
        path             = data['path']
        leg_times        = data['leg_times']
        points           = data['points']
        visible_kps_data = data['visibleKPs']
        runner_group_kps = data['runnerGroupKps']

        # --- размеры карты (пиксели) ---
        points_all, (map_width, map_height) = load_all_points()   # получаем актуальные размеры

        # --- масштаб карты 1:4000 → 1 мм = 4 м ---
        SCALE_FACTOR = 4

        # --- считаем дистанцию по всем перегонам ---
        distances = []
        total_distance = 0
        for i in range(len(path) - 1):
            kp1 = path[i]
            kp2 = path[i + 1]
            if kp1 in points and kp2 in points:
                dx = points[kp2]['mm_x'] - points[kp1]['mm_x']
                dy = points[kp2]['mm_y'] - points[kp1]['mm_y']
                dist_mm = (dx*dx + dy*dy) ** 0.5
                dist_m  = round(dist_mm * SCALE_FACTOR)
                distances.append(dist_m)
                total_distance += dist_m
            else:
                distances.append(0)

        # --- строим SVG‑элементы (точно как в браузере) ---
        svg_parts = []
        for kp_id, p in points.items():
            cx, cy, r = p['cx'], p['cy'], p.get('r', 20)

            # Видимость КП
            kp_info = next((k for k in visible_kps_data if k['id'] == kp_id), None)
            if kp_id not in ('С1', 'С2', 'Ф1') and not kp_info:
                continue

            if kp_id == path[0]:  # Старт участника
                size = r * 1.5
                polygon = f"{cx},{cy-size} {cx-size},{cy+size} {cx+size},{cy+size}"
                svg_parts.append(f'''
                    <polygon points="{polygon}" fill="none" stroke="#ff0000" stroke-width="8"/>
                    <text x="{cx + size + 10}" y="{cy + size + 10}" font-size="40" fill="#ff0000" font-weight="bold">{kp_id}</text>
                ''')
            elif kp_id == 'Ф1':
                svg_parts.append(f'''
                    <circle cx="{cx}" cy="{cy}" r="{r*1.5}" fill="none" stroke="#ff0000" stroke-width="8"/>
                    <circle cx="{cx}" cy="{cy}" r="{r*0.8}" fill="none" stroke="#ff0000" stroke-width="8"/>
                    <text x="{cx + r*1.5 + 10}" y="{cy + r*1.5 + 10}" font-size="40" fill="#ff0000" font-weight="bold">Ф1</text>
                ''')
            else:
                if kp_info and kp_info.get('isOwn'):
                    color = "#ff0000"
                elif kp_info and kp_info.get('isAlien'):
                    color = "#0066ff"
                else:
                    color = "#ff8888"

                svg_parts.append(f'''
                    <circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{color}" stroke-width="6"/>
                    <text x="{cx + r + 8}" y="{cy + r + 8}" font-size="36" fill="{color}" font-weight="bold">{kp_id}</text>
                ''')

        # --- линия маршрута ---
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

        # --- HTML для PDF (полностью поддерживает кириллицу) ---
        html_content = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
    @page {{ size: A4; margin: 12mm 15mm; }}
    body {{ font-family: 'DejaVu Sans', Arial, sans-serif; margin:0; color:#000; }}
    
    .page1 {{ page-break-after: always; }}
    
    .title   {{ font-size: 22pt; font-weight: bold; color: #c40000; text-align: center; margin: 0 0 12px 0; }}
    .subtitle{{ font-size: 14pt; text-align: center; margin: 8px 0; color: #333; }}
    .info    {{ font-size: 16pt; text-align: center; margin: 6px 0; }}
    .info strong {{ color: #c40000; }}
    
    .map     {{ position: relative; width: 100%; height: 720px; margin: 15px 0; border: 2px solid #c40000; border-radius: 8px; overflow: hidden; }}
    .map img {{ width: 100%; height: 100%; object-fit: contain; }}
    svg      {{ position: absolute; top: 0; left: 0; width: 100%; height: 100%; }}
    
    table    {{ width: 100%; border-collapse: collapse; margin-top: 20px; font-size: 11pt; }}
    th       {{ background: #c40000; color: white; padding: 8px; font-weight: bold; }}
    td       {{ padding: 6px 8px; text-align: center; border: 1px solid #aaa; }}
    .total   {{ background: #ffe6e6; font-weight: bold; font-size: 12pt; }}
    .header2 {{ font-size: 16pt; font-weight: bold; text-align: center; margin: 10px 0 15px 0; color: #c40000; }}
</style>
</head>
<body>

<!-- ==================== СТРАНИЦА 1 ==================== -->
<div class="page1">
    <h1 class="title">Снежная тропа</h1> 
    <div class="info">{runner}</div>
    <div class="info"><strong>Группа:</strong> {group}</div>
    <div class="info"><strong>Результат:</strong> {result}  |  <strong>Дистанция:</strong> {total_distance} м</div>
    
    <div class="map">
        <img src="data:image/png;base64,{map_b64}" alt="Карта">
        <svg viewBox="0 0 {map_width} {map_height}">
            {"".join(svg_parts)}
            <path d="{path_d}" fill="none" stroke="#ff3366" stroke-width="10" stroke-linecap="round"/>
        </svg>
    </div>
</div>

<!-- ==================== СТРАНИЦА 2 ==================== -->
<div>
    <h2 class="header2">Сплиты участника: {runner}</h2>
    
    <table>
        <tr>
            <th>№</th>
            <th>КП</th>
            <th>Перегон</th>
            <th>Время на перегоне</th>
            <th>Расстояние, м</th>
            <th>Общее расстояние, м</th>
            <th>Общее время</th>
        </tr>
        <tr style="background:#f8f8f8;">
            <td></td>
            <td>{path[0]}</td>
            <td>—</td>
            <td>—</td>
            <td>—</td>
            <td>0</td>
            <td>0:00</td>
        </tr>
"""

        total_sec = 0
        accum_m   = 0
        for i in range(1, len(path) - 1):
            kp       = path[i]
            leg_time = leg_times[i-1] if i-1 < len(leg_times) else "—"
            leg_dist = distances[i-1]
            accum_m += leg_dist

            if leg_time != "—" and ":" in leg_time:
                parts = list(map(int, leg_time.replace(".", ":").split(":")))
                secs = (parts[0]*3600 + parts[1]*60 + parts[2]) if len(parts)==3 else (parts[0]*60 + parts[1])
                total_sec += secs

            total_str = (f"{total_sec//60}:{total_sec%60:02d}" 
                        if total_sec < 3600 else 
                        f"{total_sec//3600}:{(total_sec%3600)//60:02d}:{total_sec%60:02d}")

            html_content += f"""        <tr>
            <td>{i}</td>
            <td>{kp}</td>
            <td>{leg_time}</td>
            <td>{leg_time}</td>
            <td>{leg_dist}</td>
            <td>{accum_m}</td>
            <td>{total_str}</td>
        </tr>\n"""

        # Финиш
        finish_dist = distances[-1] if distances else 0
        accum_m += finish_dist

        html_content += f"""        <tr class="total">
            <td></td>
            <td>Финиш</td>
            <td>—</td>
            <td>—</td>
            <td>{finish_dist}</td>
            <td>{accum_m}</td>
            <td><strong>{result}</strong></td>
        </tr>
    </table>
</div>

</body>
</html>"""

        # ---------- генерируем PDF ----------
        font_config = FontConfiguration()
        html = HTML(string=html_content,
                    base_url=os.path.dirname(os.path.abspath(__file__)))

        css = CSS(string="""
            @font-face {
                font-family: 'DejaVu Sans';
                src: url('https://github.com/dejavu-fonts/dejavu-fonts.github.io/raw/master/dejavu-fonts-ttf-2.37/ttf/DejaVuSans.ttf');
            }
            body { font-family: 'DejaVu Sans', sans-serif; }
        """, font_config=font_config)

        buffer = io.BytesIO()
        html.write_pdf(buffer, stylesheets=[css], font_config=font_config)
        buffer.seek(0)

        safe_name = "".join(c if c.isalnum() or c in " _-()" else "_" for c in runner)
        encoded_name = urllib.parse.quote(f"маршрут_{safe_name}.pdf")

        return Response(
            buffer.getvalue(),
            mimetype="application/pdf",
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_name}"
            }
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
