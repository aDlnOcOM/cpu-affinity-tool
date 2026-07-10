import psutil
import argparse
import sys
from typing import List


def get_processes(limit: int = 30):
    """Выводит список процессов"""
    print(f"{'PID':<8} {'Name':<25} {'CPU%':<8} {'Affinity'}")
    print("-" * 70)

    processes = []
    for proc in psutil.process_iter(['pid', 'name', 'cpu_percent', 'cpu_affinity']):
        try:
            info = proc.info
            processes.append(info)
        except:
            pass

    processes = sorted(processes, key=lambda x: x.get('cpu_percent') or 0, reverse=True)[:limit]

    for p in processes:
        aff = p.get('cpu_affinity') or "N/A"
        print(f"{p['pid']:<8} {p['name'][:24]:<25} {p.get('cpu_percent') or 0:<8.1f} {aff}")


def set_affinity(pid: int, cores: List[int]):
    """Устанавливает affinity для процесса"""
    try:
        proc = psutil.Process(pid)
        old_aff = proc.cpu_affinity()
        proc.cpu_affinity(cores)
        new_aff = proc.cpu_affinity()

        print(f"[+] Успешно!")
        print(f"Процесс: {proc.name()} (PID: {pid})")
        print(f"Было: {old_aff}")
        print(f"Стало: {new_aff}")
    except psutil.NoSuchProcess:
        print(f"[-] Процесс с PID {pid} не найден")
    except psutil.AccessDenied:
        print("[-]Нет прав доступа. Запустите от имени администратора.")
    except Exception as e:
        print(f"[-] Ошибка: {e}")


def main():
    parser = argparse.ArgumentParser(description="CPU Affinity Tool")
    subparsers = parser.add_subparsers(dest="command", help="Доступные команды")

    # Команда 1: список процессов
    list_parser = subparsers.add_parser("list", help="Показать процессы")
    list_parser.add_argument("-n", "--number", type=int, default=30, help="Количество процессов")

    # Команда 2: установить affinity
    set_parser = subparsers.add_parser("set", help="Установить affinity")
    set_parser.add_argument("pid", type=int, help="PID процесса")
    set_parser.add_argument("cores", type=int, nargs="+", help="Номера ядер (например: 0 1 2)")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    if args.command == "list":
        get_processes(args.number)
    elif args.command == "set":
        set_affinity(args.pid, args.cores)


if __name__ == "__main__":
    main()