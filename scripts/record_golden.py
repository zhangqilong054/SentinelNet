"""
T0.3 端点响应样本录制（golden files）
对全部 34 个业务端点 + 4 个页面路由录制真实请求/响应样本，
入库为 tests/contract/baseline/*.json
"""
import json
import os
import sys

# 确保项目根目录在 path 中
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import campus_ids.logging_config as lc
lc.setup_logging()
from campus_ids.web.app import app

OUT_DIR = os.path.join(os.path.dirname(__file__), '..', 'tests', 'contract', 'baseline')
os.makedirs(OUT_DIR, exist_ok=True)

client = app.test_client()

def save(name, resp):
    """保存响应样本"""
    data = {
        'status_code': resp.status_code,
        'headers': dict(resp.headers),
    }
    # 尝试解析 JSON
    try:
        data['json'] = resp.get_json()
    except Exception:
        data['json'] = None
    # 原始文本
    raw = resp.get_data(as_text=True)
    if data['json'] is None and raw:
        data['body'] = raw[:2000]  # 截断大文本
    path = os.path.join(OUT_DIR, f'{name}.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f'  {resp.status_code} {name}')

def get(name, url, **kw):
    resp = client.get(url, **kw)
    save(name, resp)
    return resp

def post(name, url, **kw):
    resp = client.post(url, **kw)
    save(name, resp)
    return resp

# ── 页面路由 ──────────────────────────────────────
print('=== 页面路由 ===')
get('page_index', '/')
get('page_apidocs', '/apidocs/')
get('page_login', '/login')
get('page_change_password', '/change-password')

# ── 公开端点 ──────────────────────────────────────
print('\n=== 公开端点 ===')
get('health', '/api/health')
get('check', '/api/check')
# SSE 端点在 test_client 中会阻塞，手动写入 golden
for sse_name in ['stream_alerts', 'stream_traffic']:
    path = os.path.join(OUT_DIR, f'{sse_name}.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({'status_code': 200, 'headers': {'Content-Type': 'text/event-stream'}, 'json': None, 'note': 'SSE endpoint - connection only, events not recorded'}, f, ensure_ascii=False, indent=2)
    print(f'  200 {sse_name} (SSE stub)')

# ── 流量与观测 ────────────────────────────────────
print('\n=== 流量与观测 ===')
get('traffic', '/api/traffic')
get('traffic_history', '/api/traffic/history')
get('alerts', '/api/alerts')
get('tls_stats', '/api/tls/stats')
get('tls_suspicious', '/api/tls/suspicious')

# ── 配置 ──────────────────────────────────────────
print('\n=== 配置 ===')
get('config_get', '/api/config')
post('config_post', '/api/config',
     json={'brute_force_threshold': 5},
     content_type='application/json')

# ── 抓包 ──────────────────────────────────────────
print('\n=== 抓包 ===')
get('capture_status', '/api/capture/status')
post('capture_start', '/api/capture/start',
     json={'interface': 'eth0', 'duration': 60},
     content_type='application/json')
post('capture_stop', '/api/capture/stop')
get('capture_enhanced_status', '/api/capture/enhanced-status')
post('capture_start_enhanced', '/api/capture/start-enhanced',
     json={'interface': 'eth0', 'duration': 60},
     content_type='application/json')
post('capture_stop_enhanced', '/api/capture/stop-enhanced')

# ── 检测 ──────────────────────────────────────────
print('\n=== 检测 ===')
get('detector_status', '/api/detector/status')
post('detector_start', '/api/detector/start')
post('detector_stop', '/api/detector/stop')

# ── 攻击模拟 ──────────────────────────────────────
print('\n=== 攻击模拟 ===')
get('attack_status', '/api/attack/status')
post('attack_start', '/api/attack/start',
     json={'attack_type': 'port_scan'},
     content_type='application/json')
post('attack_stop', '/api/attack/stop')

# ── 一键流程 ──────────────────────────────────────
print('\n=== 一键流程 ===')
get('auto_status', '/api/auto/status')
post('auto_start', '/api/auto/start',
     json={'duration': 60},
     content_type='application/json')

# ── 一键演示 ──────────────────────────────────────
print('\n=== 一键演示 ===')
post('demo_start', '/api/demo/start')

# ── ML 双引擎 ─────────────────────────────────────
print('\n=== ML 双引擎 ===')
get('dual_stats', '/api/dual/stats')
post('dual_load', '/api/dual/load')
post('dual_stop', '/api/dual/stop')

# ── 模型管理 ──────────────────────────────────────
print('\n=== 模型管理 ===')
get('model_list', '/api/model/list')
post('model_train', '/api/model/train',
     json={'data_source': 'synthetic_demo'},
     content_type='application/json')
get('model_train_status', '/api/model/train-status')

# ── 运维 ──────────────────────────────────────────
print('\n=== 运维 ===')
post('cleanup', '/api/cleanup',
     json={'days': 7},
     content_type='application/json')
post('save', '/api/save')

# ── 载荷送检 ──────────────────────────────────────
print('\n=== 载荷送检 ===')
post('payload_check', '/api/payload/check',
     json={'payload': 'test'},
     content_type='application/json')

# ── 认证 ──────────────────────────────────────────
print('\n=== 认证 ===')
post('login', '/api/login',
     json={'username': 'admin', 'password': 'wrong'},
     content_type='application/json')

print('\n=== 完成 ===')
print(f'golden files 已保存到 {os.path.abspath(OUT_DIR)}')