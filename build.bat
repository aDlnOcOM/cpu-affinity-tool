@echo off
echo === Requirements installement ===
pip install -r requirements.txt pyinstaller

echo === EXE building ===
pyinstaller --onefile --add-data "favicon.ico;." --icon=favicon.ico --hidden-import=uvicorn.logging --hidden-import=uvicorn.loops --hidden-import=uvicorn.loops.auto --hidden-import=uvicorn.protocols --hidden-import=uvicorn.protocols.http --hidden-import=uvicorn.protocols.http.auto --hidden-import=uvicorn.protocols.websockets --hidden-import=uvicorn.protocols.websockets.auto --hidden-import=uvicorn.lifespan --hidden-import=uvicorn.lifespan.on main.py

echo.
echo === Build complete! ===
echo File in dist\
pause