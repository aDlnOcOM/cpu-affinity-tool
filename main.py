import psutil
import webbrowser
import threading
import time
import sys
import os
import logging
import json
import platform
from typing import List
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

DEFAULT_CONFIG: Dict[str, Any] = {
    "presets": {
        "Gaming (первые 4)": [0, 1, 2, 3],
        "Background (последние 4)": [],  # Заполняется динамически
        "All cores": []  # Заполнится позже
    },
    "auto_apply_rules": {}
}


def load_config() -> Dict[str, Any]:
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


def save_config(config_data: Dict[str, Any]) -> None:
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
# ИНИЦИАЛИЗАЦИЯ И АВТО-ПРАВИЛА
# =======================

@app.on_event("startup")
def apply_saved_rules():
    logger.info(f"Рабочая директория (Конфиги/Логи): {APP_DIR}")
    rules = APP_CONFIG.get("auto_apply_rules", {})
    if not rules:
        return
    logger.info("Применение сохраненных правил affinity при старте...")
    for proc in psutil.process_iter(['name']):
        try:
            name = proc.info['name']
            if name in rules:
                proc.cpu_affinity(rules[name])
                logger.info(f"Авто-применение: {name} -> {rules[name]}")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue


# =======================
# API ЭНДПОИНТЫ
# =======================
@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse(resource_path("favicon.ico"))


@app.get("/api/config")
def get_config_api():
    """Отдает пресеты и правила для отрисовки интерфейса."""
    return APP_CONFIG


@app.post("/api/save_rule")
def save_rule(data: RuleRequest):
    """Сохраняет правило авто-применения для процесса."""
    APP_CONFIG.setdefault("auto_apply_rules", {})[data.name] = data.cores
    save_config(APP_CONFIG)
    logger.info(f"Сохранено правило: {data.name} -> {data.cores}")
    return {"status": "success", "message": "Правило сохранено"}


@app.get("/api/cores")
def get_total_cores():
    return {"total_cores": num_cores}


@app.get("/api/processes")
def get_processes(limit: int = 100):
    proc_list = []
    for proc in psutil.process_iter(['pid', 'name', 'cpu_affinity']):
        try:
            proc.cpu_percent(interval=None)
            proc_list.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    time.sleep(0.1)

    processes = []
    for proc in proc_list:
        try:
            raw_cpu = proc.cpu_percent(interval=None)
            info = proc.info
            info['cpu_percent'] = round(raw_cpu / num_cores, 1)
            if info['cpu_affinity'] is None:
                info['cpu_affinity'] = list(range(num_cores))
            processes.append(info)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    processes = sorted(processes, key=lambda x: x.get('cpu_percent') or 0, reverse=True)[:limit]
    return processes


@app.post("/api/set_affinity")
def set_affinity(data: AffinityRequest):
    try:
        proc = psutil.Process(data.pid)
        proc.cpu_affinity(data.cores)
        logger.info(f"Изменен affinity: {proc.name()} (PID: {data.pid}) -> {data.cores}")
        return {"status": "success", "message": f"Процесс {proc.name()} привязан к ядрам {data.cores}"}
    except psutil.NoSuchProcess:
        logger.error(f"Процесс {data.pid} не найден")
        raise HTTPException(status_code=404, detail="Процесс не найден")
    except psutil.AccessDenied:
        logger.warning(f"Нет прав для изменения {data.pid}")
        raise HTTPException(status_code=403, detail="Недостаточно прав (запустите от Администратора/Root)")
    except Exception as e:
        logger.error(f"Ошибка изменения affinity: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))

# =======================
# --- ФРОНТЕНД (Веб ,hfepth) ---
# =======================

@app.get("/", response_class=HTMLResponse)
def index():
    return """
<!DOCTYPE html>
<html lang="ru" class="dark">
<head>
    <meta charset="UTF-8">
    <link rel="icon" href="/favicon.ico" type="image/x-icon">
    <title>CPU Affinity Web Tool</title>
    <script src="https://cdn.jsdelivr.net/npm/@tailwindcss/browser@4"></script>
    <style>
        .cores-scroll::-webkit-scrollbar { height: 6px; }
        .cores-scroll::-webkit-scrollbar-track { background: transparent; }
        .cores-scroll::-webkit-scrollbar-thumb { background-color: #6b7280; border-radius: 10px; }
        .cores-scroll::-webkit-scrollbar-thumb:hover { background-color: #9ca3af; }
    </style>
</head>
<body class="bg-gray-100 dark:bg-gray-900 text-gray-900 dark:text-gray-100 font-sans p-8 transition-colors duration-300">
    <div class="max-w-6xl mx-auto">
        <header class="mb-6 border-b border-gray-300 dark:border-gray-700 pb-4 flex flex-col md:flex-row justify-between items-center gap-4">
            <h1 class="text-3xl font-bold text-teal-600 dark:text-teal-400">⚡ CPU Affinity Dashboard</h1>
            <div class="flex items-center gap-4">
                <div class="text-sm text-gray-600 dark:text-gray-400">Всего ядер: <span id="cores-count" class="font-bold text-gray-900 dark:text-white">...</span></div>
                <button onclick="toggleTheme()" class="bg-gray-200 dark:bg-gray-800 hover:bg-gray-300 dark:hover:bg-gray-700 px-3 py-1 rounded shadow text-sm transition-colors border border-gray-300 dark:border-gray-600">
                    🌓 Тема
                </button>
            </div>
        </header>

        <div class="mb-4 flex flex-wrap gap-4 bg-white dark:bg-gray-800 p-4 rounded-lg shadow">
            <input type="text" id="search-input" placeholder="Поиск по имени или PID..." class="flex-1 min-w-[200px] p-2 bg-gray-50 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded text-sm focus:outline-none focus:ring-2 focus:ring-teal-500 transition-colors" oninput="updateTableRender()">
            <input type="number" id="cpu-filter" placeholder="Мин. CPU %" class="w-32 p-2 bg-gray-50 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded text-sm focus:outline-none focus:ring-2 focus:ring-teal-500 transition-colors" oninput="updateTableRender()">
        </div>

        <div class="bg-white dark:bg-gray-800 rounded-lg shadow-xl overflow-hidden">
            <table class="w-full text-left border-collapse">
                <thead>
                    <tr class="bg-gray-200 dark:bg-gray-700 text-teal-700 dark:text-teal-300 uppercase text-sm tracking-wider">
                        <th class="p-4 w-20">PID</th>
                        <th class="p-4">Имя процесса</th>
                        <th class="p-4 w-24">CPU %</th>
                        <th class="p-4 w-1/2">Привязка к ядрам (Affinity) & Пресеты</th>
                    </tr>
                </thead>
                <tbody id="process-table" class="divide-y divide-gray-200 dark:divide-gray-700">
                    <tr>
                        <td colspan="4" class="p-4 text-center text-gray-500">Загрузка процессов...</td>
                    </tr>
                </tbody>
            </table>
        </div>
    </div>

    <script>
        let totalCores = 0;
        let frozenNames = JSON.parse(localStorage.getItem('frozenProcesses')) || [];
        let allProcesses = [];
        let appConfig = {};

        // Темная/Светлая тема
        function toggleTheme() {
            document.documentElement.classList.toggle('dark');
        }

        async function initData() {
            const coresRes = await fetch('/api/cores');
            const coresData = await coresRes.json();
            totalCores = coresData.total_cores;
            document.getElementById('cores-count').innerText = totalCores;

            const configRes = await fetch('/api/config');
            appConfig = await configRes.json();

            updateProcesses();
            setInterval(updateProcesses, 3000);
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
                const res = await fetch('/api/processes');
                allProcesses = await res.json();
                updateTableRender(); // Отрисовка с учетом фильтров
            } catch (err) {
                console.error("Ошибка обновления данных:", err);
            }
        }

        function updateTableRender() {
            const searchQuery = document.getElementById('search-input').value.toLowerCase();
            const minCpu = parseFloat(document.getElementById('cpu-filter').value) || 0;

            // Применяем фильтры
            let processes = allProcesses.filter(p => {
                const matchSearch = p.name.toLowerCase().includes(searchQuery) || p.pid.toString().includes(searchQuery);
                const matchCpu = p.cpu_percent >= minCpu;
                return matchSearch && matchCpu;
            });

            processes.sort((a, b) => {
                const aFrozen = frozenNames.includes(a.name);
                const bFrozen = frozenNames.includes(b.name);
                if (aFrozen && !bFrozen) return -1;
                if (!aFrozen && bFrozen) return 1;
                return b.cpu_percent - a.cpu_percent;
            });

            const tbody = document.getElementById('process-table');
            tbody.innerHTML = '';

            processes.forEach(p => {
                const isFrozen = frozenNames.includes(p.name);
                const tr = document.createElement('tr');

                tr.className = isFrozen 
                    ? "bg-teal-50 dark:bg-teal-900/20 border-l-4 border-teal-500" 
                    : "hover:bg-gray-50 dark:hover:bg-gray-750 transition-colors border-l-4 border-transparent";

                let coreCheckboxes = '<div class="cores-scroll flex flex-nowrap overflow-x-auto gap-1 pb-2" style="max-width: 420px;">';
                for(let i = 0; i < totalCores; i++) {
                    const isChecked = p.cpu_affinity.includes(i) ? 'checked' : '';
                    coreCheckboxes += `
                        <label class="flex-none inline-flex items-center bg-gray-100 dark:bg-gray-700 px-2 py-1 rounded text-xs cursor-pointer hover:bg-gray-200 dark:hover:bg-gray-600 transition-colors border border-gray-300 dark:border-gray-600">
                            <input type="checkbox" data-pid="${p.pid}" data-name="${p.name}" data-core="${i}" ${isChecked} onchange="changeAffinity(this)" class="mr-1 accent-teal-500">
                            <span>${i}</span>
                        </label>
                    `;
                }
                coreCheckboxes += '</div>';

                // Выпадающий список пресетов
                let presetsSelect = `<select onchange="applyPreset(this, ${p.pid})" class="text-xs bg-gray-50 dark:bg-gray-700 border border-gray-300 dark:border-gray-600 rounded p-1 ml-1 cursor-pointer">
                    <option value="">-- Пресет --</option>`;
                for (const [presetName, cores] of Object.entries(appConfig.presets || {})) {
                    presetsSelect += `<option value="[${cores.join(',')}]">${presetName}</option>`;
                }
                presetsSelect += `</select>`;

                // Кнопка сохранения правила
                const hasRule = appConfig.auto_apply_rules && appConfig.auto_apply_rules[p.name];
                const saveRuleBtn = `<button onclick="saveRule('${p.name}', ${p.pid})" class="ml-2 text-xs ${hasRule ? 'text-teal-600 dark:text-teal-400 font-bold' : 'text-gray-500 hover:text-teal-500'} transition-colors" title="Сохранить текущую привязку">💾 ${hasRule ? 'Сохранено' : 'Сохранить'}</button>`;

                const freezeIcon = isFrozen ? '❄️ Открепить' : '📌 Закрепить';
                const freezeBtnClass = isFrozen ? 'text-teal-600 dark:text-teal-400 font-bold' : 'text-gray-500 hover:text-teal-500';

                tr.innerHTML = `
                    <td class="p-4 font-mono text-sm text-gray-500 dark:text-gray-400">${p.pid}</td>
                    <td class="p-4 font-semibold text-gray-800 dark:text-white">
                        <div class="flex flex-col items-start gap-1">
                            <span class="break-all">${p.name}</span>
                            <button onclick="toggleFreeze('${p.name}')" class="text-xs ${freezeBtnClass} transition-colors uppercase tracking-wider">${freezeIcon}</button>
                        </div>
                    </td>
                    <td class="p-4 font-mono text-sm text-teal-600 dark:text-teal-400">${p.cpu_percent}%</td>
                    <td class="p-4">
                        <div class="flex flex-col gap-2">
                            ${coreCheckboxes}
                            <div class="flex items-center">
                                <span class="text-xs text-gray-500 dark:text-gray-400">Группы: </span>
                                ${presetsSelect}
                                ${saveRuleBtn}
                            </div>
                        </div>
                    </td>
                `;
                tbody.appendChild(tr);
            });
        }

        async function changeAffinity(checkbox) {
            const pid = parseInt(checkbox.getAttribute('data-pid'));
            const checkboxes = document.querySelectorAll(`input[data-pid="${pid}"]:checked`);
            const cores = Array.from(checkboxes).map(cb => parseInt(cb.getAttribute('data-core')));

            if (cores.length === 0) {
                alert("Процесс должен быть привязан хотя бы к одному ядру!");
                checkbox.checked = true;
                return;
            }
            await sendAffinityRequest(pid, cores);
        }

        async function applyPreset(selectElem, pid) {
            if (!selectElem.value) return;
            const cores = JSON.parse(selectElem.value);
            await sendAffinityRequest(pid, cores);

            // Визуально обновляем чекбоксы
            const checkboxes = document.querySelectorAll(`input[data-pid="${pid}"]`);
            checkboxes.forEach(cb => {
                const coreIdx = parseInt(cb.getAttribute('data-core'));
                cb.checked = cores.includes(coreIdx);
            });
            selectElem.value = ""; // Сбрасываем выбор селекта
        }

        async function sendAffinityRequest(pid, cores) {
            const response = await fetch('/api/set_affinity', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ pid, cores })
            });

            if (!response.ok) {
                const errorData = await response.json();
                alert(`Ошибка: ${errorData.detail}`);
            }
        }

        async function saveRule(name, pid) {
            const checkboxes = document.querySelectorAll(`input[data-pid="${pid}"]:checked`);
            const cores = Array.from(checkboxes).map(cb => parseInt(cb.getAttribute('data-core')));

            const response = await fetch('/api/save_rule', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name, cores })
            });

            if (response.ok) {
                if (!appConfig.auto_apply_rules) appConfig.auto_apply_rules = {};
                appConfig.auto_apply_rules[name] = cores;
                updateTableRender(); 
            }
        }

        initData();
    </script>
</body>
</html>
    """


def open_browser():
    time.sleep(2)
    webbrowser.open("http://127.0.0.1:8000")


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    threading.Thread(target=open_browser, daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")