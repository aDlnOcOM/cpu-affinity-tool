import psutil
import argparse
import sys
import shlex


# Улучшенная псевдографика
def print_header():
    print("\n" + "╔" + "═" * 68 + "╗")
    print("║" + " CPU AFFINITY TOOL ".center(68) + "║")
    print("╚" + "═" * 68 + "╝")
    print(f"  Всего ядер доступно: {psutil.cpu_count()}")


def print_separator():
    print("╟" + "─" * 68 + "╢")


def get_processes(limit: int = 25):
    print_header()
    print(f"  {'PID':<8} {'NAME':<28} {'CPU%':<8} {'AFFINITY'}")
    print_separator()

    processes = []
    for proc in psutil.process_iter(['pid', 'name', 'cpu_percent', 'cpu_affinity']):
        try:
            info = proc.info
            if info['name']:
                processes.append(info)
        except:
            pass

    processes = sorted(processes, key=lambda x: x.get('cpu_percent') or 0, reverse=True)[:limit]

    for p in processes:
        aff = p.get('cpu_affinity')
        aff_str = str(aff) if aff is not None else "N/A"
        cpu = p.get('cpu_percent') or 0.0
        print(f"  {p['pid']:<8} {p['name'][:27]:<28} {cpu:<8.1f} {aff_str}")

    print_separator()


def set_affinity(pid: int, cores: list):
    try:
        proc = psutil.Process(pid)
        proc.cpu_affinity(cores)
        print(f"\n[✔] Успешно: Процесс {proc.name()} (PID: {pid}) привязан к ядрам {cores}")
    except Exception as e:
        print(f"\n[✘] Ошибка: {e}")


def show_help():
    print("\nДоступные команды:")
    print("  list [N]          - Список процессов (топ N)")
    print("  set <PID> <cores> - Привязать процесс к ядрам (например: set 1234 0 1)")
    print("  help              - Показать это меню")
    print("  exit              - Выход из программы")


def main():
    print_header()
    print("Интерактивный режим запущен. Введите 'help' для списка команд.")

    while True:
        try:
            user_input = input("\nCPU-TOOL > ").strip()
            if not user_input: continue

            parts = shlex.split(user_input)
            cmd = parts[0].lower()

            if cmd == "exit":
                print("Завершение работы...")
                break
            elif cmd == "help":
                show_help()
            elif cmd == "list":
                n = int(parts[1]) if len(parts) > 1 else 25
                get_processes(n)
            elif cmd == "set":
                if len(parts) < 3:
                    print("Ошибка: недостаточно аргументов для set")
                else:
                    set_affinity(int(parts[1]), [int(x) for x in parts[2:]])
            else:
                print("Неизвестная команда. Введите 'help'.")
        except Exception as e:
            print(f"Ошибка ввода: {e}")


if __name__ == "__main__":
    main()