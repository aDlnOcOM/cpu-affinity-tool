import psutil
import webbrowser
import threading
import time
from typing import List
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import uvicorn
from fastapi.responses import FileResponse
import sys
import os

app = FastAPI(title="CPU Affinity Management API")


# Модель данных для изменения affinity
class AffinityRequest(BaseModel):
    pid: int
    cores: List[int]


def get_cores_count():
    return psutil.cpu_count() or 1

# --- Абсолютный путь ---
def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

# --- API ЭНДПОИНТЫ ---
@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse(resource_path("favicon.ico"))

@app.get("/api/cores")
def get_total_cores():
    """Возвращает количество доступных логических ядер."""
    return {"total_cores": get_cores_count()}


@app.get("/api/processes")
def get_processes(limit: int = 100):  # Увеличили лимит до 100, чтобы замороженные процессы не пропадали
    """Возвращает топ-N процессов по загрузке CPU в формате JSON."""
    num_cores = get_cores_count()
    proc_list = []

    # Первый проход: инициализация счетчиков
    for proc in psutil.process_iter(['pid', 'name', 'cpu_affinity']):
        try:
            proc.cpu_percent(interval=None)
            proc_list.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    time.sleep(0.1)  # Короткая пауза для замера

    processes = []
    for proc in proc_list:
        try:
            raw_cpu = proc.cpu_percent(interval=None)
            info = proc.info
            # Нормализация загрузки на одно ядро
            info['cpu_percent'] = round(raw_cpu / num_cores, 1)
            # Если affinity равен None, значит процесс может использовать все ядра
            if info['cpu_affinity'] is None:
                info['cpu_affinity'] = list(range(num_cores))
            processes.append(info)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    # Сортировка по убыванию CPU
    processes = sorted(processes, key=lambda x: x.get('cpu_percent') or 0, reverse=True)[:limit]
    return processes


@app.post("/api/set_affinity")
def set_affinity(data: AffinityRequest):
    """Устанавливает маску ядер для процесса."""
    try:
        proc = psutil.Process(data.pid)
        proc.cpu_affinity(data.cores)
        return {"status": "success", "message": f"Процесс {proc.name()} привязан к ядрам {data.cores}"}
    except psutil.NoSuchProcess:
        raise HTTPException(status_code=404, detail="Процесс не найден")
    except psutil.AccessDenied:
        raise HTTPException(status_code=403, detail="Недостаточно прав (запустите от Администратора/Root)")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# --- ФРОНТЕНД (Интерфейс) ---


@app.get("/", response_class=HTMLResponse)
def index():
    """Отдает простую HTML-страницу с Tailwind CSS и JavaScript для реального времени."""
    return """
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <link rel="icon" href="/favicon.ico" type="image/x-icon">
        <title>CPU Affinity Web Tool</title>
        <script src="https://cdn.jsdelivr.net/npm/@tailwindcss/browser@4"></script>
        <style>
            /* Кастомный тонкий скроллбар для кнопок ядер */
            .cores-scroll::-webkit-scrollbar {
                height: 6px;
            }
            .cores-scroll::-webkit-scrollbar-track {
                background: transparent;
            }
            .cores-scroll::-webkit-scrollbar-thumb {
                background-color: #4b5563;
                border-radius: 10px;
            }
            .cores-scroll::-webkit-scrollbar-thumb:hover {
                background-color: #6b7280;
            }
        </style>
    </head>
    <body class="bg-gray-900 text-gray-100 font-sans p-8">
        <div class="max-w-5xl mx-auto">
            <header class="mb-8 border-b border-gray-700 pb-4 flex justify-between items-center">
                <h1 class="text-3xl font-bold text-teal-400">⚡ CPU Affinity Dashboard</h1>
                <div class="text-sm text-gray-400">Всего ядер в системе: <span id="cores-count" class="font-bold text-white">...</span></div>
            </header>

            <div class="bg-gray-800 rounded-lg shadow-xl overflow-hidden">
                <table class="w-full text-left border-collapse">
                    <thead>
                        <tr class="bg-gray-700 text-teal-300 uppercase text-sm tracking-wider">
                            <th class="p-4 w-24">PID</th>
                            <th class="p-4">Имя процесса</th>
                            <th class="p-4 w-24">CPU %</th>
                            <th class="p-4 w-1/2">Привязка к ядрам (Affinity)</th>
                        </tr>
                    </thead>
                    <tbody id="process-table" class="divide-y divide-gray-700">
                        <tr>
                            <td colspan="4" class="p-4 text-center text-gray-500">Загрузка процессов...</td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </div>

        <script>
            let totalCores = 0;
            // Загружаем замороженные процессы из localStorage (сохранятся при перезагрузке страницы)
            let frozenNames = JSON.parse(localStorage.getItem('frozenProcesses')) || [];

            async function loadCores() {
                const res = await fetch('/api/cores');
                const data = await res.json();
                totalCores = data.total_cores;
                document.getElementById('cores-count').innerText = totalCores;
            }

            // Функция заморозки/разморозки
            function toggleFreeze(name) {
                if (frozenNames.includes(name)) {
                    frozenNames = frozenNames.filter(n => n !== name); // Убираем
                } else {
                    frozenNames.push(name); // Добавляем
                }
                localStorage.setItem('frozenProcesses', JSON.stringify(frozenNames));
                updateProcesses(); // Мгновенно перерисовываем
            }

            async function updateProcesses() {
                try {
                    const res = await fetch('/api/processes');
                    let processes = await res.json();

                    // СОРТИРОВКА: Замороженные процессы всегда наверху
                    processes.sort((a, b) => {
                        const aFrozen = frozenNames.includes(a.name);
                        const bFrozen = frozenNames.includes(b.name);
                        if (aFrozen && !bFrozen) return -1;
                        if (!aFrozen && bFrozen) return 1;
                        return b.cpu_percent - a.cpu_percent; // Если оба заморожены/незаморожены — сортируем по CPU
                    });

                    const tbody = document.getElementById('process-table');
                    tbody.innerHTML = '';

                    processes.forEach(p => {
                        const isFrozen = frozenNames.includes(p.name);
                        const tr = document.createElement('tr');

                        // Если процесс заморожен, подсвечиваем его строку и добавляем левую рамку
                        tr.className = isFrozen 
                            ? "bg-teal-900/20 hover:bg-teal-900/40 transition-colors border-l-4 border-teal-500" 
                            : "hover:bg-gray-750 transition-colors border-l-4 border-transparent";

                        // Обертка для кнопок ядер (гибкая строка без переносов со скроллом)
                        let coreCheckboxes = '<div class="cores-scroll flex flex-nowrap overflow-x-auto gap-1 pb-2" style="max-width: 450px;">';
                        for(let i = 0; i < totalCores; i++) {
                            const isChecked = p.cpu_affinity.includes(i) ? 'checked' : '';
                            coreCheckboxes += `
                                <label class="flex-none inline-flex items-center bg-gray-700 px-2 py-1 rounded text-xs cursor-pointer hover:bg-gray-600 transition-colors border border-gray-600">
                                    <input type="checkbox" data-pid="${p.pid}" data-core="${i}" ${isChecked} onchange="changeAffinity(this)" class="mr-1 accent-teal-400">
                                    <span>${i}</span>
                                </label>
                            `;
                        }
                        coreCheckboxes += '</div>';

                        // Кнопка заморозки ❄️
                        const freezeIcon = isFrozen ? '❄️ Открепить' : '📌 Закрепить';
                        const freezeBtnClass = isFrozen ? 'text-teal-400 font-bold hover:text-teal-300' : 'text-gray-500 hover:text-teal-400';
                        const freezeBtn = `<button onclick="toggleFreeze('${p.name}')" class="ml-3 text-xs ${freezeBtnClass} transition-colors uppercase tracking-wider">${freezeIcon}</button>`;

                        tr.innerHTML = `
                            <td class="p-4 font-mono text-gray-400">${p.pid}</td>
                            <td class="p-4 font-semibold text-white">
                                <div class="flex flex-col items-start gap-1">
                                    <span>${p.name}</span>
                                    ${freezeBtn}
                                </div>
                            </td>
                            <td class="p-4 font-mono text-teal-400">${p.cpu_percent}%</td>
                            <td class="p-4">${coreCheckboxes}</td>
                        `;
                        tbody.appendChild(tr);
                    });
                } catch (err) {
                    console.error("Ошибка обновления данных:", err);
                }
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

                const response = await fetch('/api/set_affinity', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ pid, cores })
                });

                if (!response.ok) {
                    const errorData = await response.json();
                    alert(`Ошибка: ${errorData.detail}`);
                    updateProcesses();
                }
            }

            // Инициализация
            loadCores().then(() => {
                updateProcesses();
                setInterval(updateProcesses, 3000);
            });
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