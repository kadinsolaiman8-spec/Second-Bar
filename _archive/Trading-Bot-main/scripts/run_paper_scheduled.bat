@echo off
REM Daily paper trading check — called by Windows Task Scheduler.
REM Logs stdout+stderr to data\paper_trading.log with timestamp.

cd /d "c:\Users\kadin\DiscordTradingBot"

echo. >> data\paper_trading.log
echo ============================================================ >> data\paper_trading.log
echo %date% %time% >> data\paper_trading.log
echo ============================================================ >> data\paper_trading.log

"C:\Users\kadin\AppData\Local\Programs\Python\Python313\python.exe" scripts\run_paper.py >> data\paper_trading.log 2>&1
