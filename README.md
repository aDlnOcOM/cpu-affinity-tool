# CPU Affinity Tool

Простая консольная утилита для управления привязкой процессов к ядрам процессора (CPU Affinity).

**Учебный проект** — минималистично и просто.

## Возможности

- Просмотр списка запущенных процессов (с загрузкой CPU и текущей привязкой)
- Установка привязки процесса к конкретным ядрам процессора
- Работает на Windows и Linux

## Установка

### Вариант 1: Через Python

```bash
# Клонируйте или скачайте проект
pip install -r requirements.txt
```

## Использование
#### 1. Просмотр процессов
```bash 
Bashpython main.py list
```

Или с указанием количества:
```bash 
Bashpython main.py list --number 50
```


#### 2. Установка affinity
```bash
# Привязать процесс к одному ядру
python main.py set 1234 0

# Привязать к нескольким ядрам
python main.py set 5678 0 1 3

# Пример с PID explorer.exe
python main.py set 2345 1
```


Рекомендуемая команда для чистого EXE:

```bash
pyinstaller --onefile --console --clean --name "CPU_Affinity_Tool" main.py
```