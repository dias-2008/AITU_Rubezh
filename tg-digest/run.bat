@echo off
cd /d "%~dp0"
echo ========== %DATE% %TIME% ========== >> digest.log
python digest.py run >> digest.log 2>&1
