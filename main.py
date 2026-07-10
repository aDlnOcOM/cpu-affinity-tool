import psutil
import webbrowser
import threading
import time
from typing import List
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import uvicorn

app = FastAPI(title="CPU Affinity Management API")


# Модель данных для изменения affinity
class AffinityRequest(BaseModel):
    pid: int
    cores: List[int]
def get_cores_count():
    return psutil.cpu_count() or 1


# --- API ЭНДПОИНТЫ ---

@app.get("/api/cores")
def get_total_cores():
    """Возвращает количество доступных логических ядер."""
    return {"total_cores": get_cores_count()}


@app.get("/api/processes")
def get_processes(limit: int = 25):
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
        <title>CPU Affinity Web Tool</title>
        <script src="https://cdn.jsdelivr.net/npm/@tailwindcss/browser@4"></script>
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
                            <th class="p-4">PID</th>
                            <th class="p-4">Имя процесса</th>
                            <th class="p-4">CPU %</th>
                            <th class="p-4">Привязка к ядрам (Affinity)</th>
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

            // Получаем количество ядер при загрузке
            async function loadCores() {
                const res = await fetch('/api/cores');
                const data = await res.json();
                totalCores = data.total_cores;
                document.getElementById('cores-count').innerText = totalCores;
            }

            // Обновление списка процессов в реальном времени
            async function updateProcesses() {
                try {
                    const res = await fetch('/api/processes');
                    const processes = await res.json();
                    const tbody = document.getElementById('process-table');
                    tbody.innerHTML = '';

                    processes.forEach(p => {
                        const tr = document.createElement('tr');
                        tr.className = "hover:bg-gray-750 transition-colors";

                        // Создаем чекбоксы для каждого ядра
                        let coreCheckboxes = '';
                        for(let i = 0; i < totalCores; i++) {
                            const isChecked = p.cpu_affinity.includes(i) ? 'checked' : '';
                            coreCheckboxes += `
                                <label class="inline-flex items-center mr-2 bg-gray-700 px-2 py-1 rounded text-xs cursor-pointer hover:bg-gray-600">
                                    <input type="checkbox" data-pid="${p.pid}" data-core="${i}" ${isChecked} onchange="changeAffinity(this)" class="mr-1 accent-teal-400">
                                    <span>${i}</span>
                                </label>
                            `;
                        }

                        tr.innerHTML = `
                            <td class="p-4 font-mono text-gray-400">${p.pid}</td>
                            <td class="p-4 font-semibold text-white">${p.name}</td>
                            <td class="p-4 font-mono text-teal-400">${p.cpu_percent}%</td>
                            <td class="p-4">${coreCheckboxes}</td>
                        `;
                        tbody.appendChild(tr);
                    });
                } catch (err) {
                    console.error("Ошибка обновления данных:", err);
                }
            }

            // Отправка запроса на изменение Affinity
            async function changeAffinity(checkbox) {
                const pid = parseInt(checkbox.getAttribute('data-pid'));

                // Собираем все выбранные чекбоксы для конкретного PID
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
                    // Перезагружаем список, чтобы вернуть чекбоксы в исходное состояние
                    updateProcesses();
                }
            }

            // Инициализация
            loadCores().then(() => {
                updateProcesses();
                // Автоматическое обновление каждые 3 секунды (Реалтайм)
                setInterval(updateProcesses, 3000);
            });
        </script>
    </body>
    </html>
    """


def open_browser():
    time.sleep(2)  # даём серверу запуститься
    webbrowser.open("http://127.0.0.1:8000")


if __name__ == "__main__":
    threading.Thread(target=open_browser, daemon=True).start()
    uvicorn.run("main:app", host="127.0.0.1", port=8000)