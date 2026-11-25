@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 动作广场本地预览：http://127.0.0.1:8780/  （关掉这个窗口就停）
start "" http://127.0.0.1:8780/
python -m http.server 8780 --bind 127.0.0.1
