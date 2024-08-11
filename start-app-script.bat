@echo off
echo Starting Application...
start cmd /k "echo Starting Flask server... && python backend.py"
timeout /t 1
start http://localhost:7474