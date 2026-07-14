import psutil
import webbrowser
import threading
import time
from typing import List, Dict, Any
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel
import uvicorn
import sys
import os
import json
import logging
from contextlib import asynccontextmanager


def get_app_dir() -> str:
    if os.name == 'nt':
        base_dir = os.environ.get('APPDATA', os.path.expanduser('~'))
    else:
        base_dir = os.path.expanduser('~')
    app_dir = os.path.join(base_dir, '.cpu-affinity-tool')
    os.makedirs(app_dir, exist_ok=True)
    return app_dir


APP_DIR = get_app_dir()
LOG_FILE = os.path.join(APP_DIR, 'app.log')
CONFIG_FILE = os.path.join(APP_DIR, 'config.json')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.FileHandler(LOG_FILE, encoding='utf-8'), logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger('cpu-affinity')

DEFAULT_CONFIG: Dict[str, Any] = {
    'presets': {
        'Gaming cores 0-7': list(range(0, 8)),
        'Background cores 8-15': list(range(8, 16)),
        'All cores': []
    },
    'auto_apply_rules': {},
    'theme': 'dark'
}


def get_cores_count() -> int:
    return psutil.cpu_count(logical=True) or 1


def sanitize_cores(cores: List[int]) -> List[int]:
    max_cores = get_cores_count()
    return sorted({c for c in cores if isinstance(c, int) and 0 <= c < max_cores})


def load_config() -> Dict[str, Any]:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    cfg.update({k: v for k, v in loaded.items() if k != 'presets'})
                    if isinstance(loaded.get('presets'), dict):
                        cfg['presets'].update(loaded['presets'])
        except Exception as e:
            logger.error(f'Ошибка чтения конфига: {e}')
    max_cores = get_cores_count()
    if not cfg['presets'].get('All cores'):
        cfg['presets']['All cores'] = list(range(max_cores))
    return cfg


def save_config(config_data: Dict[str, Any]) -> None:
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config_data, f, indent=4, ensure_ascii=False)
    except Exception as e:
        logger.error(f'Ошибка сохранения конфига: {e}')


APP_CONFIG = load_config()


class AffinityRequest(BaseModel):
    pid: int
    cores: List[int]


class RuleRequest(BaseModel):
    name: str
    cores: List[int]


class ThemeRequest(BaseModel):
    theme: str


class PresetRequest(BaseModel):
    name: str
    cores: List[int]


def resource_path(relative_path: str) -> str:
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath('.')
    return os.path.join(base_path, relative_path)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f'Запуск. Директория: {APP_DIR} | Ядер: {get_cores_count()}')
    rules = APP_CONFIG.get('auto_apply_rules', {}) or {}
    applied = 0
    if rules:
        for proc in psutil.process_iter(['name', 'pid']):
            try:
                if proc.info.get('name') in rules:
                    cores = sanitize_cores(rules[proc.info['name']])
                    if cores:
                        proc.cpu_affinity(cores)
                        applied += 1
                        logger.info(f"Авто-применено: {proc.info.get('name')} -> {cores}")
            except Exception as e:
                logger.debug(f'Auto-apply skipped: {e}')
    if rules:
        logger.info(f'Авто-применено правил: {applied}')
    yield
    logger.info('Приложение остановлено')


app = FastAPI(title='CPU Affinity Management API', lifespan=lifespan)


@app.get('/favicon.ico', include_in_schema=False)
async def favicon():
    return FileResponse(resource_path('favicon.ico'))


@app.get('/api/config')
def get_config_api():
    return APP_CONFIG


@app.get('/api/cores')
def get_total_cores():
    return {'total_cores': get_cores_count()}


@app.get('/api/presets')
def get_presets():
    return APP_CONFIG.get('presets', {})


@app.post('/api/presets')
def save_preset(data: PresetRequest):
    cores = sanitize_cores(data.cores)
    if not cores:
        raise HTTPException(status_code=400, detail='Пустой или некорректный набор ядер')
    APP_CONFIG.setdefault('presets', {})[data.name] = cores
    save_config(APP_CONFIG)
    logger.info(f'Пресет сохранён: {data.name} -> {cores}')
    return {'status': 'success'}


@app.post('/api/theme')
def set_theme(data: ThemeRequest):
    if data.theme not in ('dark', 'light'):
        raise HTTPException(status_code=400, detail='Некорректная тема')
    APP_CONFIG['theme'] = data.theme
    save_config(APP_CONFIG)
    logger.info(f'Тема изменена: {data.theme}')
    return {'status': 'success', 'theme': data.theme}


@app.post('/api/save_rule')
def save_rule(data: RuleRequest):
    cores = sanitize_cores(data.cores)
    if not cores:
        raise HTTPException(status_code=400, detail='Список ядер не может быть пустым')
    APP_CONFIG.setdefault('auto_apply_rules', {})[data.name] = cores
    save_config(APP_CONFIG)
    logger.info(f'Правило сохранено: {data.name} -> {cores}')
    return {'status': 'success'}


@app.get('/api/processes')
def get_processes(limit: int = 150, q: str = '', sort_by: str = 'cpu'):
    num_cores = get_cores_count()
    proc_list = []
    ql = q.strip().lower()
    for proc in psutil.process_iter(['pid', 'name', 'cpu_affinity']):
        try:
            proc.cpu_percent(interval=None)
            proc_list.append(proc)
        except Exception:
            continue
    time.sleep(0.08)
    processes = []
    for proc in proc_list:
        try:
            raw = proc.cpu_percent(interval=None)
            info = proc.info
            info['cpu_percent'] = round(raw / num_cores if num_cores > 0 else raw, 1)
            if info.get('cpu_affinity') is None:
                info['cpu_affinity'] = list(range(num_cores))
            if ql:
                name = str(info.get('name', '')).lower()
                pid = str(info.get('pid', ''))
                cpu = str(info.get('cpu_percent', ''))
                if ql not in name and ql not in pid and ql not in cpu:
                    continue
            processes.append(info)
        except Exception:
            continue
    if sort_by == 'pid':
        processes.sort(key=lambda x: x.get('pid') or 0)
    elif sort_by == 'name':
        processes.sort(key=lambda x: (x.get('name') or '').lower())
    elif sort_by == 'cpu':
        processes.sort(key=lambda x: x.get('cpu_percent') or 0, reverse=True)
    return processes[:limit]


@app.post('/api/set_affinity')
def set_affinity(data: AffinityRequest):
    cores = sanitize_cores(data.cores)
    if not cores:
        raise HTTPException(status_code=400, detail='Выберите хотя бы одно корректное ядро')
    try:
        proc = psutil.Process(data.pid)
        proc.cpu_affinity(cores)
        logger.info(f'Affinity изменён: {proc.name()}({data.pid}) -> {cores}')
        return {'status': 'success', 'message': f'Процесс {proc.name()} привязан к ядрам {cores}'}
    except psutil.NoSuchProcess:
        raise HTTPException(status_code=404, detail='Процесс не найден')
    except psutil.AccessDenied:
        raise HTTPException(status_code=403, detail='Недостаточно прав (запустите от Администратора/Root)')
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get('/', response_class=HTMLResponse)
def index():
    theme = APP_CONFIG.get('theme', 'dark')
    html_class = 'dark' if theme == 'dark' else ''
    return f"""
    <!DOCTYPE html>
    <html lang='ru' class='{html_class}'>
    <head>
        <meta charset='UTF-8'>
        <link rel='icon' href='/favicon.ico' type='image/x-icon'>
        <title>CPU Affinity Web Tool</title>
        <script src='https://cdn.jsdelivr.net/npm/@tailwindcss/browser@4'></script>

        <style type="text/tailwindcss">
            @custom-variant dark (&:where(.dark, .dark *));
        </style>

        <style>
            .cores-scroll::-webkit-scrollbar {{ height: 6px; }}
            .cores-scroll::-webkit-scrollbar-track {{ background: transparent; }}
            .cores-scroll::-webkit-scrollbar-thumb {{ background-color: #9ca3af; border-radius: 10px; }}
            .dark .cores-scroll::-webkit-scrollbar-thumb {{ background-color: #4b5563; }}
            .cores-scroll::-webkit-scrollbar-thumb:hover {{ background-color: #6b7280; }}
        </style>
    </head>
    <body id='app-body' class='bg-gray-100 text-gray-900 dark:bg-gray-900 dark:text-gray-100 font-sans p-8 transition-colors duration-300'>
        <div class='max-w-6xl mx-auto'>
            <header class='mb-6 border-b border-gray-300 dark:border-gray-700 pb-4 flex flex-wrap gap-3 justify-between items-center'>
                <div>
                    <h1 class='text-3xl font-bold text-teal-600 dark:text-teal-400'>⚡ CPU Affinity Dashboard</h1>
                    <div class='text-sm opacity-80 mt-1'>Всего ядер в системе: <span id='cores-count' class='font-bold text-teal-600 dark:text-teal-400'>...</span></div>
                </div>
                <div class='flex flex-wrap items-center gap-2'>
                    <input id='searchBox' oninput='updateProcesses()' placeholder='Поиск: имя, PID, CPU%' class='px-3 py-2 rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white outline-none min-w-64 focus:border-teal-500'>
                    <select id='sortBy' onchange='updateProcesses()' class='px-3 py-2 rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white'>
                        <option value='cpu'>CPU%</option>
                        <option value='pid'>PID</option>
                        <option value='name'>Имя</option>
                    </select>
                    <select id='themeSelect' onchange='setTheme(this.value)' class='px-3 py-2 rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white'>
                        <option value='dark' {'selected' if theme == 'dark' else ''}>Темная тема</option>
                        <option value='light' {'selected' if theme == 'light' else ''}>Светлая тема</option>
                    </select>
                </div>
            </header>

            <div class='mb-4 flex flex-wrap gap-2 items-center'>
                <input id='presetName' placeholder='Имя новой группы ядер' class='px-3 py-2 rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white'>
                <button onclick='saveCurrentPreset()' class='px-4 py-2 rounded bg-teal-600 hover:bg-teal-500 text-white font-medium transition-colors'>Сохранить группу</button>
                <select id='presetSelect' class='px-3 py-2 rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white ml-auto'></select>
                <button onclick='applyPreset()' class='px-4 py-2 rounded bg-sky-600 hover:bg-sky-500 text-white font-medium transition-colors'>Применить к PID</button>
            </div>

            <div class='mb-6 flex flex-wrap gap-2 items-center text-sm'>
                <input id='ruleProcessName' placeholder='Имя процесса (напр. chrome.exe)' class='px-3 py-2 rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white min-w-72'>
                <button onclick='saveRuleForName()' class='px-4 py-2 rounded bg-emerald-600 hover:bg-emerald-500 text-white font-medium transition-colors'>Сохранить авто-правило</button>
                <span class='opacity-70 ml-2'>Правила применяются автоматически при старте.</span>
            </div>

            <div class='bg-white dark:bg-gray-800/90 rounded-lg shadow-xl overflow-hidden border border-gray-200 dark:border-gray-700'>
                <table class='w-full text-left border-collapse'>
                    <thead>
                        <tr class='bg-gray-200 dark:bg-gray-700 text-gray-700 dark:text-teal-300 uppercase text-sm tracking-wider'>
                            <th class='p-4 w-24'>PID</th>
                            <th class='p-4'>Имя процесса</th>
                            <th class='p-4 w-24'>CPU %</th>
                            <th class='p-4 w-1/2'>Привязка к ядрам (Affinity)</th>
                        </tr>
                    </thead>
                    <tbody id='process-table' class='divide-y divide-gray-200 dark:divide-gray-700'>
                        <tr><td colspan='4' class='p-4 text-center text-gray-500'>Загрузка процессов...</td></tr>
                    </tbody>
                </table>
            </div>
        </div>

        <script>
            let totalCores = 0;
            let frozenNames = JSON.parse(localStorage.getItem('frozenProcesses')) || [];
            let presets = {{}};

            async function setTheme(theme) {{
                const html = document.documentElement;
                if (theme === 'light') {{
                    html.classList.remove('dark');
                }} else {{
                    html.classList.add('dark');
                }}
                await fetch('/api/theme', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ theme }})
                }});
            }}

            function toggleFreeze(name) {{
                if (frozenNames.includes(name)) frozenNames = frozenNames.filter(n => n !== name);
                else frozenNames.push(name);
                localStorage.setItem('frozenProcesses', JSON.stringify(frozenNames));
                updateProcesses();
            }}

            function getCheckedCoresForPid(pid) {{
                const checkboxes = document.querySelectorAll(`input[data-pid="${{pid}}"]:checked`);
                return Array.from(checkboxes).map(cb => parseInt(cb.getAttribute('data-core')));
            }}

            async function loadCores() {{
                const res = await fetch('/api/cores');
                const data = await res.json();
                totalCores = data.total_cores;
                document.getElementById('cores-count').innerText = totalCores;
            }}

            async function loadPresets() {{
                const res = await fetch('/api/presets');
                presets = await res.json();
                const sel = document.getElementById('presetSelect');
                sel.innerHTML = '';
                Object.keys(presets).forEach(name => {{
                    const opt = document.createElement('option');
                    opt.value = name;
                    opt.textContent = `${{name}} -> [${{(presets[name] || []).join(', ')}}]`;
                    sel.appendChild(opt);
                }});
            }}

            function parseCoresInput(input, processes) {{
                if (input.includes(',')) {{
                    return input.split(',').map(n => parseInt(n.trim())).filter(n => !Number.isNaN(n));
                }} else {{
                    const pid = parseInt(input);
                    if (Number.isNaN(pid)) return null;
                    const p = processes.find(x => x.pid === pid);
                    if (!p) {{ alert('Процесс с таким PID не найден'); return null; }}
                    return (p.cpu_affinity || []).filter(c => Number.isInteger(c));
                }}
            }}

            async function saveCurrentPreset() {{
                const name = document.getElementById('presetName').value.trim();
                if (!name) return alert('Введите имя группы ядер');

                const input = prompt('Введите номера ядер через запятую (напр. 0,1,2,3) ИЛИ введите PID процесса, чтобы скопировать его ядра:');
                if (!input) return;

                const res = await fetch('/api/processes?limit=1000');
                const processes = await res.json();

                const cores = parseCoresInput(input, processes);
                if (!cores || cores.length === 0) return alert('Не удалось определить список ядер');

                const resp = await fetch('/api/presets', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ name, cores }})
                }});
                if (!resp.ok) {{
                    const e = await resp.json();
                    alert(e.detail || 'Ошибка сохранения');
                    return;
                }}
                await loadPresets();
                document.getElementById('presetName').value = '';
            }}

            async function saveRuleForName() {{
                const name = document.getElementById('ruleProcessName').value.trim();
                if (!name) return alert('Введите имя процесса');

                const input = prompt('Введите номера ядер через запятую (напр. 0,1,2,3) ИЛИ введите PID процесса, чтобы скопировать его ядра:');
                if (!input) return;

                const res = await fetch('/api/processes?limit=1000');
                const processes = await res.json();

                const cores = parseCoresInput(input, processes);
                if (!cores || cores.length === 0) return alert('Не удалось определить список ядер');

                const resp = await fetch('/api/save_rule', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ name, cores }})
                }});
                if (!resp.ok) {{
                    const e = await resp.json();
                    alert(e.detail || 'Ошибка сохранения');
                    return;
                }}
                alert('Правило успешно сохранено');
                document.getElementById('ruleProcessName').value = '';
            }}

            async function applyPreset() {{
                const name = document.getElementById('presetSelect').value;
                const cores = presets[name] || [];
                if (!cores.length) return alert('У группы пустой список ядер');
                const pid = parseInt(prompt(`Применить группу "${{name}}" к PID:`));
                if (Number.isNaN(pid)) return;
                const resp = await fetch('/api/set_affinity', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ pid, cores }})
                }});
                if (!resp.ok) {{
                    const e = await resp.json();
                    alert(e.detail || 'Ошибка применения');
                    return;
                }}
                updateProcesses();
            }}

            async function updateProcesses() {{
                try {{
                    const q = document.getElementById('searchBox').value || '';
                    const sortBy = document.getElementById('sortBy').value || 'cpu';
                    const res = await fetch(`/api/processes?q=${{encodeURIComponent(q)}}&sort_by=${{encodeURIComponent(sortBy)}}`);
                    let processes = await res.json();

                    processes.sort((a, b) => {{
                        const aFrozen = frozenNames.includes(a.name);
                        const bFrozen = frozenNames.includes(b.name);
                        if (aFrozen && !bFrozen) return -1;
                        if (!aFrozen && bFrozen) return 1;
                        return (b.cpu_percent || 0) - (a.cpu_percent || 0);
                    }});

                    const tbody = document.getElementById('process-table');
                    tbody.innerHTML = '';

                    processes.forEach(p => {{
                        const isFrozen = frozenNames.includes(p.name);
                        const tr = document.createElement('tr');

                        const bgClass = isFrozen ? 'bg-amber-50 dark:bg-teal-900/20' : 'bg-white dark:bg-transparent';
                        const borderClass = isFrozen ? 'border-l-4 border-teal-500' : 'border-l-4 border-transparent';
                        const hoverClass = isFrozen
                            ? 'hover:bg-[#f3e5ab] dark:hover:bg-teal-900/60'
                            : 'hover:bg-[#fdf6e3] dark:hover:bg-gray-900';

                        tr.className = `${{bgClass}} ${{borderClass}} ${{hoverClass}} transition-colors`;

                        let coreCheckboxes = '<div class="cores-scroll flex flex-nowrap overflow-x-auto gap-1 pb-2" style="max-width: 450px;">';
                        for (let i = 0; i < totalCores; i++) {{
                            const isChecked = (p.cpu_affinity || []).includes(i) ? 'checked' : '';
                            coreCheckboxes += `
                                <label class="flex-none inline-flex items-center bg-gray-100 dark:bg-gray-700 px-2 py-1 rounded text-xs cursor-pointer hover:bg-gray-200 dark:hover:bg-gray-600 transition-colors border border-gray-300 dark:border-gray-600 text-gray-800 dark:text-gray-200">
                                    <input type="checkbox" data-pid="${{p.pid}}" data-core="${{i}}" ${{isChecked}} onchange="changeAffinity(this)" class="mr-1 accent-teal-600 dark:accent-teal-400">
                                    <span>${{i}}</span>
                                </label>
                            `;
                        }}
                        coreCheckboxes += '</div>';

                        const freezeIcon = isFrozen ? '❄️ Открепить' : '📌 Закрепить';
                        const freezeBtnClass = isFrozen ? 'text-teal-600 dark:text-teal-400 font-bold hover:text-teal-700 dark:hover:text-teal-300' : 'text-gray-500 hover:text-teal-600 dark:hover:text-teal-400';
                        const freezeBtn = `<button onclick="toggleFreeze('${{String(p.name).replaceAll("'", "\\'")}}')" class="ml-3 text-xs ${{freezeBtnClass}} transition-colors uppercase tracking-wider">${{freezeIcon}}</button>`;

                        tr.innerHTML = `
                            <td class='p-4 font-mono text-gray-500 dark:text-gray-400'>${{p.pid}}</td>
                            <td class='p-4 font-semibold text-gray-900 dark:text-white'>
                                <div class='flex flex-col items-start gap-1'>
                                    <span>${{p.name}}</span>
                                    ${{freezeBtn}}
                                </div>
                            </td>
                            <td class='p-4 font-mono text-teal-600 dark:text-teal-400'>${{p.cpu_percent}}%</td>
                            <td class='p-4'>${{coreCheckboxes}}</td>
                        `;
                        tbody.appendChild(tr);
                    }});
                }} catch (err) {{
                    console.error('Ошибка обновления данных:', err);
                }}
            }}

            async function changeAffinity(checkbox) {{
                const pid = parseInt(checkbox.getAttribute('data-pid'));
                const cores = getCheckedCoresForPid(pid);
                if (cores.length === 0) {{
                    alert('Процесс должен быть привязан хотя бы к одному ядру!');
                    checkbox.checked = true;
                    return;
                }}
                const response = await fetch('/api/set_affinity', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ pid, cores }})
                }});
                if (!response.ok) {{
                    const errorData = await response.json();
                    alert(`Ошибка: ${{errorData.detail}}`);
                    updateProcesses();
                }}
            }}

            loadCores().then(() => {{
                loadPresets();
                updateProcesses();
                setInterval(updateProcesses, 3000);
            }});
        </script>
    </body>
    </html>
    """


def open_browser():
    time.sleep(2)
    webbrowser.open('http://127.0.0.1:8000')


if __name__ == '__main__':
    import multiprocessing

    multiprocessing.freeze_support()
    threading.Thread(target=open_browser, daemon=True).start()
    uvicorn.run(app, host='127.0.0.1', port=8000, log_level='info')