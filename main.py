import psutil  # Библиотека для получения информации о процессах и системе
import argparse  # Не используется в текущей версии (можно удалить)
import sys
import shlex  # Для безопасного разбора командной строки (учитывает кавычки)
import time  # Для паузы между замерами CPU


def print_header():
    """Выводит заголовок инструмента."""
    print("\n" + "╔" + "═" * 68 + "╗")
    print("║" + " CPU AFFINITY TOOL ".center(68) + "║")
    print("╚" + "═" * 68 + "╝")
    # psutil.cpu_count() возвращает количество логических ядер (с учётом Hyper-Threading/SMT)
    print(f"  Всего ядер доступно: {psutil.cpu_count()}")


def print_separator():
    """Печатает горизонтальную линию-разделитель в таблице."""
    print("╟" + "─" * 68 + "╢")


def get_processes(limit: int = 25):
    """
    Выводит топ-N процессов по загрузке CPU.

    Механизм:
    1. Первый проход - запускаем cpu_percent() с interval=None для инициализации.
    2. Небольшая пауза (time.sleep).
    3. Второй проход - получаем реальные значения CPU за интервал.
    Это стандартный приём psutil для корректного замера загрузки.
    """
    print_header()
    print(f"  {'PID':<8} {'NAME':<28} {'CPU%':<8} {'AFFINITY'}")
    print_separator()

    num_cores = psutil.cpu_count() or 1  # Защита от гипотетического случая 0 ядер
    proc_list = []

    # Первый проход: инициализация счётчиков CPU
    for proc in psutil.process_iter(['pid', 'name', 'cpu_affinity']):
        try:
            # interval=None - не блокирует, просто подготавливает внутренние счётчики
            proc.cpu_percent(interval=None)
            proc_list.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            # Процесс мог завершиться или у нас нет прав (обычно системные процессы)
            continue

    time.sleep(0.1)  # Короткая пауза для накопления статистики

    processes = []
    for proc in proc_list:
        try:
            raw_cpu = proc.cpu_percent(interval=None)
            info = proc.info
            # Нормализация: psutil возвращает процент от всех ядер.
            # Делим на количество ядер → получаем "среднюю" загрузку на одно ядро.
            info['cpu_percent'] = raw_cpu / num_cores
            processes.append(info)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    # Сортировка по убыванию загрузки CPU с помощью lambda-функции
    # lambda x: x.get('cpu_percent') or 0  - анонимная функция, которая:
    #   1. Берёт словарь процесса x
    #   2. Достаёт ключ 'cpu_percent' (метод .get() возвращает None, если ключа нет)
    #   3. or 0 - превращает None/False в 0 (защита от None)
    # reverse=True - сортировка по убыванию

    processes = sorted(processes, key=lambda x: x.get('cpu_percent') or 0, reverse=True)[:limit]

    for p in processes:
        aff = p.get('cpu_affinity')
        # cpu_affinity() возвращает список номеров ядер или None (если не ограничен)
        aff_str = str(aff) if aff is not None else "N/A"

        cpu = p.get('cpu_percent') or 0.0

        # Форматирование f-string:
        # :<8   - выравнивание по левому краю, ширина поля 8 символов
        # .1f   - число с плавающей точкой, 1 знак после запятой
        # [<28] - ширина 28 символов, обрезаем имя до 27 символов + пробел
        print(f"  {p['pid']:<8} {p['name'][:27]:<28} {cpu:<8.1f} {aff_str}")

    print_separator()


def set_affinity(pid: int, cores: list):
    """
    Привязывает процесс к указанным ядрам (CPU affinity).

    Важно:
    - Работает только если у пользователя есть права (обычно нужен администратор/root)
    - cores - список целых чисел [0, 1, 3] и т.д.
    """
    try:
        proc = psutil.Process(pid)  # Получаем объект процесса по PID
        proc.cpu_affinity(cores)  # Устанавливаем маску ядер
        print(f"\n[✔] Успешно: Процесс {proc.name()} (PID: {pid}) привязан к ядрам {cores}")
    except psutil.NoSuchProcess:
        print(f"\n[✘] Ошибка: Процесс с PID {pid} не найден")
    except psutil.AccessDenied:
        print(f"\n[✘] Ошибка: Недостаточно прав для изменения affinity процесса {pid}")
    except Exception as e:
        print(f"\n[✘] Ошибка: {e}")


def show_help():
    """Выводит справку по командам."""
    print("\nДоступные команды:")
    print("  list [N]          - Список процессов (топ N по CPU)")
    print("  set <PID> <cores> - Привязать процесс к ядрам (например: set 1234 0 1 3)")
    print("  help              - Показать это меню")
    print("  exit              - Выход из программы")


def main():
    print_header()
    print("Интерактивный режим запущен. Введите 'help' для списка команд.")

    while True:
        try:
            user_input = input("\nCPU-TOOL > ").strip()
            if not user_input:
                continue

            # shlex.split() - безопасно разбирает строку, учитывая кавычки
            # Пример: set 1234 "0 1 2" → ['set', '1234', '0 1 2'] (не разобьёт пробелы внутри кавычек)
            parts = shlex.split(user_input)
            cmd = parts[0].lower()

            if cmd == "exit":
                print("Завершение работы...")
                break
            elif cmd == "help":
                show_help()
            elif cmd == "list":
                # Тернарный оператор для удобного получения параметра
                n = int(parts[1]) if len(parts) > 1 else 25
                get_processes(n)
            elif cmd == "set":
                if len(parts) < 3:
                    print("Ошибка: недостаточно аргументов для set")
                else:
                    # Преобразуем все аргументы после PID в список целых чисел
                    set_affinity(int(parts[1]), [int(x) for x in parts[2:]])
            else:
                print("Неизвестная команда. Введите 'help'.")
        except ValueError as e:
            # Ошибки преобразования типов (например, невалидный PID)
            print(f"Ошибка ввода (некорректное число): {e}")
        except Exception as e:
            print(f"Неожиданная ошибка: {e}")


if __name__ == "__main__":
    # Запускаем main() только если файл запущен напрямую
    main()