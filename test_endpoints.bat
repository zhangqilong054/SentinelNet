@echo off
echo === Testing GET Endpoints ===
for %%e in ("/api/traffic" "/api/alerts" "/api/config" "/api/capture/status" "/api/capture/enhanced-status" "/api/tls/stats" "/api/tls/suspicious" "/api/dual/stats" "/api/attack/status" "/api/model/list" "/api/model/train-status" "/api/auto/status" "/api/check" "/api/health") do (
    curl -s -w "%%e HTTP:%%{http_code}\n" -o /dev/null http://127.0.0.1:5000%%e 2>&1
)
echo.
echo === Testing POST Endpoints ===
curl -s -w "/api/capture/start HTTP:%%{http_code}\n" -o /dev/null -X POST -H "Content-Type: application/json" -d "{\"duration\":3}" http://127.0.0.1:5000/api/capture/start 2>&1
curl -s -w "/api/capture/stop HTTP:%%{http_code}\n" -o /dev/null -X POST http://127.0.0.1:5000/api/capture/stop 2>&1
curl -s -w "/api/capture/start-enhanced HTTP:%%{http_code}\n" -o /dev/null -X POST -H "Content-Type: application/json" -d "{\"duration\":5}" http://127.0.0.1:5000/api/capture/start-enhanced 2>&1
curl -s -w "/api/capture/stop-enhanced HTTP:%%{http_code}\n" -o /dev/null -X POST http://127.0.0.1:5000/api/capture/stop-enhanced 2>&1
curl -s -w "/api/auto/start HTTP:%%{http_code}\n" -o /dev/null -X POST -H "Content-Type: application/json" -d "{\"duration\":5}" http://127.0.0.1:5000/api/auto/start 2>&1
curl -s -w "/api/dual/load HTTP:%%{http_code}\n" -o /dev/null -X POST http://127.0.0.1:5000/api/dual/load 2>&1
curl -s -w "/api/dual/stop HTTP:%%{http_code}\n" -o /dev/null -X POST http://127.0.0.1:5000/api/dual/stop 2>&1
curl -s -w "/api/config POST HTTP:%%{http_code}\n" -o /dev/null -X POST -H "Content-Type: application/json" -d "{\"ddos_threshold\":200}" http://127.0.0.1:5000/api/config 2>&1
echo.
echo === Done ===