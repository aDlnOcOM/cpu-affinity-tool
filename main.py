import psutil
import webbrowser
import threading
import time
import sys
import os
import logging
import json
import platform
from typing import List, Any
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel
import uvicorn

# =======================
# КОНФИГ И ЛОГИРОВАНИЕ
# =======================

def get_app_dir() -> str:
    """
    Возвращает директорию для хранения конфигов и логов.
    Windows: %APPDATA%\.cpu-affinity-tool
    Linux/macOS: ~/.cpu-affinity-tool
    """
    if platform.system() == "Windows":
        base_dir = os.environ.get("APPDATA", os.path.expanduser("~"))
    else:
        base_dir = os.path.expanduser("~")
    app_dir = os.path.join(base_dir, ".cpu-affinity-tool")
    os.makedirs(app_dir, exist_ok=True)
    return app_dir


APP_DIR = get_app_dir()
LOG_FILE = os.path.join(APP_DIR, "app.log")
CONFIG_FILE = os.path.join(APP_DIR, "config.json")

# Настройка логирования (в консоль + файл)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("cpu-affinity")

DEFAULT_CONFIG: dict[str, Any] = {
    "presets": {
        "Gaming (первые 4)": [0, 1, 2, 3],
        "Background (последние 4)": [],  # Заполняется динамически
        "All cores": []  # Заполнится позже
    },
    "auto_apply_rules": {}
}


def load_config() -> dict[str, Any]:
    """Загружает конфигурацию из JSON или возвращает дефолт."""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                config = json.load(f)
                logger.info("Конфигурация успешно загружена")
                return config
        except Exception as e:
            logger.error(f"Ошибка чтения конфига: {e}")
    logger.info("Используется конфигурация по умолчанию")
    return DEFAULT_CONFIG.copy()


def save_config(config_data: dict[str, Any]) -> None:
    """Сохраняет конфигурацию в JSON."""
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=4, ensure_ascii=False)
        logger.debug("Конфигурация сохранена")
    except Exception as e:
        logger.error(f"Ошибка сохранения конфига: {e}")


# Загружаем конфиг
APP_CONFIG = load_config()

# =======================
# ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ И ИНИЦИАЛИЗАЦИЯ
# =======================

num_cores = psutil.cpu_count(logical=True) or 1

# Динамическое заполнение пресетов
if not APP_CONFIG["presets"].get("Background (последние 4)"):
    APP_CONFIG["presets"]["Background (последние 4)"] = list(range(max(0, num_cores - 4), num_cores))

if not APP_CONFIG["presets"].get("All cores"):
    APP_CONFIG["presets"]["All cores"] = list(range(num_cores))

app = FastAPI(title="CPU Affinity Management API")


class AffinityRequest(BaseModel):
    """Модель для изменения affinity."""
    pid: int
    cores: List[int]


class RuleRequest(BaseModel):
    """Модель для сохранения правила авто-применения."""
    name: str
    cores: List[int]


def resource_path(relative_path: str) -> str:
    """Получает путь к ресурсам (работает в PyInstaller)."""
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

# =======================
# STARTUP EVENT — АВТО-ПРИМЕНЕНИЕ ПРАВИЛ
# =======================

@app.on_event("startup")
async def startup_event():
    """При старте приложения применяем сохранённые правила."""
    logger.info(f"Запуск приложения. Рабочая директория: {APP_DIR}")
    logger.info(f"Количество ядер: {num_cores}")

    rules = APP_CONFIG.get("auto_apply_rules", {})
    if not rules:
        logger.info("Нет сохранённых правил авто-применения")
        return

    logger.info(f"Применяем {len(rules)} сохранённых правил...")
    applied_count = 0

    for proc in psutil.process_iter(['name', 'pid']):
        try:
            name = proc.info['name']
            if name in rules:
                cores = rules[name]
                if cores:  # Проверяем, что список не пустой
                    proc.cpu_affinity(cores)
                    logger.info(f"Авто-применение: {name} (PID {proc.info['pid']}) -> {cores}")
                    applied_count += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied, Exception) as e:
            continue  # Тихо пропускаем

    logger.info(f"Авто-применение завершено. Успешно: {applied_count}")


# =======================
# API ЭНДПОИНТЫ
# =======================

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse(resource_path("favicon.ico"))


@app.get("/api/config")
def get_config_api():
    """Возвращает текущие пресеты и правила для фронтенда."""
    return APP_CONFIG


@app.post("/api/save_rule")
def save_rule(data: RuleRequest):
    """Сохраняет новое правило авто-применения."""
    if not data.cores:
        raise HTTPException(status_code=400, detail="Список ядер не может быть пустым")

    APP_CONFIG.setdefault("auto_apply_rules", {})[data.name] = data.cores
    save_config(APP_CONFIG)
    logger.info(f"Сохранено правило для {data.name}: {data.cores}")
    return {"status": "success", "message": f"Правило для {data.name} сохранено"}


@app.get("/api/cores")
def get_total_cores():
    """Возвращает количество логических ядер."""
    return {"total_cores": num_cores}


@app.get("/api/processes")
def get_processes(limit: int = 150):
    """
    Возвращает список процессов с CPU% и affinity.
    Увеличен лимит + улучшена производительность.
    """
    proc_list = []
    for proc in psutil.process_iter(['pid', 'name', 'cpu_affinity']):
        try:
            proc.cpu_percent(interval=None)
            proc_list.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    # Короткая пауза для точного замера CPU
    time.sleep(0.08)

    processes = []
    for proc in proc_list:
        try:
            raw_cpu = proc.cpu_percent(interval=None)
            info = proc.info
            info['cpu_percent'] = round(raw_cpu / num_cores if num_cores > 0 else raw_cpu, 1)

            # Нормализация affinity
            if info['cpu_affinity'] is None:
                info['cpu_affinity'] = list(range(num_cores))
            processes.append(info)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    # Сортировка по CPU
    processes = sorted(processes, key=lambda x: x.get('cpu_percent') or 0, reverse=True)[:limit]
    return processes


@app.post("/api/set_affinity")
def set_affinity(data: AffinityRequest):
    """Устанавливает affinity для процесса."""
    if not data.cores:
        raise HTTPException(status_code=400, detail="Должен быть выбран хотя бы один core")

    try:
        proc = psutil.Process(data.pid)
        proc.cpu_affinity(data.cores)
        logger.info(f"Изменён affinity: {proc.name()} (PID: {data.pid}) -> {data.cores}")
        return {"status": "success", "message": f"Процесс {proc.name()} привязан к {data.cores}"}
    except psutil.NoSuchProcess:
        logger.warning(f"Процесс {data.pid} не найден")
        raise HTTPException(status_code=404, detail="Процесс не найден")
    except psutil.AccessDenied:
        logger.warning(f"Нет прав для PID {data.pid}")
        raise HTTPException(status_code=403, detail="Запустите от имени администратора/root")
    except Exception as e:
        logger.error(f"Неожиданная ошибка: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))

# =======================
# --- ФРОНТЕНД (Веб ,hfepth) ---
# =======================

@app.get("/", response_class=HTMLResponse)
def index():
    """Отдаёт основной HTML-интерфейс."""
    return """
<!DOCTYPE html>
<html lang="ru" class="dark">
<head>
    <meta charset="UTF-8">
    <link rel="icon" href="/favicon.ico" type="image/x-icon">
    <title>CPU Affinity Dashboard</title>
    <script src="https://cdn.jsdelivr.net/npm/@tailwindcss/browser@4"></script>
    <style>
        .cores-scroll::-webkit-scrollbar { height: 6px; }
        .cores-scroll::-webkit-scrollbar-track { background: transparent; }
        .cores-scroll::-webkit-scrollbar-thumb { background-color: #6b7280; border-radius: 10px; }
        .cores-scroll::-webkit-scrollbar-thumb:hover { background-color: #9ca3af; }
        .process-row { transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1); }
    </style>
</head>
<body class="bg-gray-100 dark:bg-gray-900 text-gray-900 dark:text-gray-100 font-sans p-6 transition-colors duration-300">
    <div class="max-w-7xl mx-auto">
        <header class="mb-8 border-b border-gray-300 dark:border-gray-700 pb-5 flex flex-col md:flex-row justify-between items-start md:items-center gap-4">
            <div>
                <h1 class="text-4xl font-bold text-teal-600 dark:text-teal-400 flex items-center gap-3">
                    ⚡ CPU Affinity Dashboard
                </h1>
                <p class="text-gray-500 dark:text-gray-400 text-sm mt-1">Управление привязкой процессов к ядрам</p>
            </div>
            <div class="flex items-center gap-4">
                <div class="text-sm text-gray-600 dark:text-gray-400">
                    Ядер: <span id="cores-count" class="font-mono font-bold text-gray-900 dark:text-white">...</span>
                </div>
                <button onclick="toggleTheme()" 
                        class="px-4 py-2 bg-white dark:bg-gray-800 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-xl shadow-sm border border-gray-200 dark:border-gray-600 flex items-center gap-2 text-sm font-medium transition-all">
                    🌓 Переключить тему
                </button>
            </div>
        </header>

        <!-- Фильтры -->
        <div class="mb-6 flex flex-wrap gap-4 bg-white dark:bg-gray-800 p-5 rounded-2xl shadow">
            <input type="text" id="search-input" 
                   placeholder="🔍 Поиск по имени процесса или PID..." 
                   class="flex-1 min-w-[280px] px-4 py-3 bg-gray-50 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-2xl text-sm focus:outline-none focus:ring-2 focus:ring-teal-500 transition-all"
                   oninput="updateTableRender()">

            <input type="number" id="cpu-filter" placeholder="Мин. CPU %" 
                   class="w-40 px-4 py-3 bg-gray-50 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-2xl text-sm focus:outline-none focus:ring-2 focus:ring-teal-500 transition-all"
                   oninput="updateTableRender()">
        </div>

        <div class="bg-white dark:bg-gray-800 rounded-3xl shadow-xl overflow-hidden">
            <table class="w-full text-left border-collapse">
                <thead>
                    <tr class="bg-gray-50 dark:bg-gray-700 text-teal-700 dark:text-teal-300 uppercase text-xs tracking-widest">
                        <th class="p-5 w-24">PID</th>
                        <th class="p-5">Процесс</th>
                        <th class="p-5 w-28">CPU %</th>
                        <th class="p-5">Affinity + Пресеты</th>
                    </tr>
                </thead>
                <tbody id="process-table" class="divide-y divide-gray-100 dark:divide-gray-700"></tbody>
            </table>
        </div>
    </div>

    <script>
        let totalCores = 0;
        let frozenNames = JSON.parse(localStorage.getItem('frozenProcesses')) || [];
        let allProcesses = [];
        let appConfig = {};

        function toggleTheme() {
            document.documentElement.classList.toggle('dark');
        }

        async function initData() {
            try {
                // Загружаем количество ядер
                const coresRes = await fetch('/api/cores');
                const coresData = await coresRes.json();
                totalCores = coresData.total_cores;
                document.getElementById('cores-count').innerText = totalCores;

                // Загружаем конфиг (пресеты)
                const configRes = await fetch('/api/config');
                appConfig = await configRes.json();

                await updateProcesses();
                setInterval(updateProcesses, 2800); // Чуть чаще
            } catch (e) {
                console.error("Ошибка инициализации:", e);
            }
        }

        function toggleFreeze(name) {
            if (frozenNames.includes(name)) {
                frozenNames = frozenNames.filter(n => n !== name);
            } else {
                frozenNames.push(name);
            }
            localStorage.setItem('frozenProcesses', JSON.stringify(frozenNames));
            updateTableRender();
        }

        async function updateProcesses() {
            try {
                const res = await fetch('/api/processes?limit=200');
                allProcesses = await res.json();
                updateTableRender();
            } catch (err) {
                console.error("Ошибка обновления процессов:", err);
            }
        }

        function updateTableRender() {
            const searchQuery = (document.getElementById('search-input').value || '').toLowerCase().trim();
            const minCpu = parseFloat(document.getElementById('cpu-filter').value) || 0;

            let filtered = allProcesses.filter(p => {
                const nameMatch = p.name.toLowerCase().includes(searchQuery);
                const pidMatch = p.pid.toString().includes(searchQuery);
                const cpuMatch = p.cpu_percent >= minCpu;
                return (nameMatch || pidMatch) && cpuMatch;
            });

            // Сортировка: frozen сверху + по CPU
            filtered.sort((a, b) => {
                const aF = frozenNames.includes(a.name) ? 1 : 0;
                const bF = frozenNames.includes(b.name) ? 1 : 0;
                if (aF !== bF) return bF - aF;
                return b.cpu_percent - a.cpu_percent;
            });

            const tbody = document.getElementById('process-table');
            tbody.innerHTML = '';

            if (filtered.length === 0) {
                tbody.innerHTML = `<tr><td colspan="4" class="p-12 text-center text-gray-400">Ничего не найдено</td></tr>`;
                return;
            }

            filtered.forEach(p => {
                const isFrozen = frozenNames.includes(p.name);
                const tr = document.createElement('tr');
                tr.className = `process-row ${isFrozen ? 'bg-teal-50 dark:bg-teal-900/30 border-l-4 border-teal-500' : 'hover:bg-gray-50 dark:hover:bg-gray-750'}`;

                // Чекбоксы ядер
                let coreHtml = '<div class="cores-scroll flex flex-nowrap overflow-x-auto gap-1 pb-2 max-w-[460px]">';
                for (let i = 0; i < totalCores; i++) {
                    const checked = p.cpu_affinity.includes(i) ? 'checked' : '';
                    coreHtml += `
                        <label class="flex-none inline-flex items-center bg-gray-100 dark:bg-gray-700 px-3 py-1.5 rounded-xl text-xs cursor-pointer hover:bg-gray-200 dark:hover:bg-gray-600 border border-gray-200 dark:border-gray-600">
                            <input type="checkbox" data-pid="${p.pid}" data-name="${p.name}" data-core="${i}" ${checked}
                                   onchange="changeAffinity(this)" class="mr-1 accent-teal-500">
                            <span class="font-mono">${i}</span>
                        </label>`;
                }
                coreHtml += '</div>';

                // Пресеты
                let presetHtml = `<select onchange="applyPreset(this, ${p.pid})" class="text-xs bg-white dark:bg-gray-700 border border-gray-300 dark:border-gray-600 rounded-xl px-3 py-2 cursor-pointer focus:ring-1">`;
                presetHtml += `<option value="">Пресет...</option>`;
                Object.entries(appConfig.presets || {}).forEach(([name, cores]) => {
                    presetHtml += `<option value='${JSON.stringify(cores)}'>${name}</option>`;
                });
                presetHtml += `</select>`;

                const hasRule = !!(appConfig.auto_apply_rules && appConfig.auto_apply_rules[p.name]);
                const saveBtn = `<button onclick="saveRule('${p.name}', ${p.pid})" 
                    class="ml-3 text-xs px-3 py-2 rounded-xl transition-all ${hasRule ? 'bg-teal-100 dark:bg-teal-900 text-teal-700 dark:text-teal-300' : 'hover:bg-gray-100 dark:hover:bg-gray-700'}">
                    💾 ${hasRule ? '✓' : 'Сохранить правило'}
                </button>`;

                const freezeIcon = isFrozen ? '❄️ Открепить' : '📌 Закрепить';
                const freezeClass = isFrozen ? 'text-teal-600 dark:text-teal-400 font-semibold' : 'text-gray-500 hover:text-teal-600 dark:hover:text-teal-400';

                tr.innerHTML = `
                    <td class="p-5 font-mono text-sm text-gray-500 dark:text-gray-400">${p.pid}</td>
                    <td class="p-5">
                        <div class="font-medium">${p.name}</div>
                        <button onclick="toggleFreeze('${p.name}')" class="text-xs mt-1 ${freezeClass}">${freezeIcon}</button>
                    </td>
                    <td class="p-5 font-mono text-teal-600 dark:text-teal-400 font-semibold">${p.cpu_percent}%</td>
                    <td class="p-5">
                        <div class="space-y-3">
                            ${coreHtml}
                            <div class="flex items-center gap-2 flex-wrap">
                                ${presetHtml}
                                ${saveBtn}
                            </div>
                        </div>
                    </td>
                `;
                tbody.appendChild(tr);
            });
        }

        async function changeAffinity(checkbox) {
            const pid = parseInt(checkbox.getAttribute('data-pid'));
            const checkedBoxes = document.querySelectorAll(`input[data-pid="${pid}"]:checked`);
            const cores = Array.from(checkedBoxes).map(cb => parseInt(cb.getAttribute('data-core')));

            if (cores.length === 0) {
                alert("Нужно выбрать минимум одно ядро!");
                checkbox.checked = true;
                return;
            }
            await sendAffinity(pid, cores);
        }

        async function applyPreset(select, pid) {
            if (!select.value) return;
            const cores = JSON.parse(select.value);
            await sendAffinity(pid, cores);

            // Обновляем чекбоксы
            const boxes = document.querySelectorAll(`input[data-pid="${pid}"]`);
            boxes.forEach(box => {
                const core = parseInt(box.getAttribute('data-core'));
                box.checked = cores.includes(core);
            });
            select.value = "";
        }

        async function sendAffinity(pid, cores) {
            try {
                const resp = await fetch('/api/set_affinity', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({pid, cores})
                });

                if (!resp.ok) {
                    const err = await resp.json();
                    alert(`Ошибка: ${err.detail}`);
                }
            } catch (e) {
                console.error(e);
            }
        }

        async function saveRule(name, pid) {
            const checked = document.querySelectorAll(`input[data-pid="${pid}"]:checked`);
            const cores = Array.from(checked).map(c => parseInt(c.getAttribute('data-core')));

            if (!cores.length) {
                alert("Выберите ядра перед сохранением правила");
                return;
            }

            try {
                const resp = await fetch('/api/save_rule', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({name, cores})
                });

                if (resp.ok) {
                    if (!appConfig.auto_apply_rules) appConfig.auto_apply_rules = {};
                    appConfig.auto_apply_rules[name] = cores;
                    updateTableRender();
                    console.log(`Правило сохранено для ${name}`);
                }
            } catch (e) {
                console.error(e);
            }
        }

        // Запуск
        window.onload = initData;
    </script>
</body>
</html>
    """


def open_browser():
    """Открывает браузер после запуска сервера."""
    time.sleep(2.2)
    webbrowser.open("http://127.0.0.1:8000")


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()

    threading.Thread(target=open_browser, daemon=True).start()

    logger.info("Запуск сервера...")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")