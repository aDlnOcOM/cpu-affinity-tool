@echo off
echo === Requirements installement ===
pip install -r requirements.txt pyinstaller

echo === EXE building ===
pyinstaller --clean --onefile ^
  --name cpu_affinity_tool ^
  --hidden-import=fastapi ^
  --hidden-import=starlette ^
  --hidden-import=pydantic ^
  --hidden-import=uvicorn ^
  --hidden-import=anyio ^
  --hidden-import=httpx ^
  --hidden-import=jinja2 ^
  --hidden-import=psutil ^
  --additional-hooks-dir=hooks ^
  --uac-admin ^
  main.py

echo.
echo === Build complete! ===
echo File in dist\
pause