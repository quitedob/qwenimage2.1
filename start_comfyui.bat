@echo off
REM ============================================================
REM  Qwen-Image 2.1 (INT8 ConvRot) + ComfyUI 0.37.0
REM  RTX 5060 Ti 16GB / torch 2.13.0+cu130 / Python 3.13
REM  Port 8199 is used because 8188 is occupied on this machine.
REM ============================================================
setlocal
set PY=E:\python\qwenimage\python_embeded\python.exe
set COMFY=E:\python\qwenimage\ComfyUI
set OUT=E:\python\qwenimage\output

"%PY%" "%COMFY%\main.py" ^
  --listen 127.0.0.1 ^
  --port 8199 ^
  --output-directory "%OUT%"

endlocal
pause
