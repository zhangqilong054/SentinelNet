/* ============================================================
 * SentinelNet 哨兵网络 — 监控面板前端逻辑
 * M3: SSE 实时推送 + 轮询降级 / 双引擎指标 / 告警筛选 / 阈值与抓包控制 / 载荷检测
 * 所有动态内容均通过 textContent 渲染，避免 HTML 注入。
 * ============================================================ */
(function () {
    'use strict';

    const $ = (id) => document.getElementById(id);
    const REFRESH_MS = 2000;       // 轮询降级时的刷新间隔
    const SSE_RECONNECT_MS = 5000; // SSE 断线重连间隔

    // ---------- 状态 ----------
    let paused = false;
    let connOnline = null;          // null=首次连接, true/false
    let bannerDanger = false;       // 安全状态横幅
    let alertFilter = 'all';
    let lastAlerts = [];
    const seenAlertKeys = new Set();
    let alertsFirstLoad = true;

    // SSE 连接状态
    let sseTraffic = null;          // EventSource for /api/stream/traffic
    let sseAlerts = null;           // EventSource for /api/stream/alerts
    let sseConnected = false;       // 是否有至少一个 SSE 连接活跃
    let pollTimer = null;           // 轮询降级定时器
    let sseReconnectTimer = null;   // SSE 重连定时器

    const thresholds = {ddos_threshold: 500, port_scan_threshold: 50,
                        syn_flood_threshold: 100, udp_flood_threshold: 200};
    let initialConfig = {};

    const LEVEL_LABELS = {high: '高危', medium: '中危', low: '低危'};
    const ATTACK_PALETTE = ['#ff4d4d', '#ff8c00', '#ffd700', '#9c27b0',
                            '#2196f3', '#e91e63', '#00bcd4', '#ff5722'];

    // ---------- DOM 辅助（安全渲染） ----------
    function setText(id, text) { $(id).textContent = text; }
    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined && text !== null) node.textContent = text;
        return node;
    }
    function clearNode(node) { while (node.firstChild) node.removeChild(node.firstChild); }

    // ---------- M5: Toast 通知组件 ----------
    let _toastContainer = null;

    function _ensureToastContainer() {
        if (!_toastContainer) {
            _toastContainer = el('div', 'toast-container');
            _toastContainer.id = 'toastContainer';
            document.body.appendChild(_toastContainer);
        }
        return _toastContainer;
    }

    /**
     * 显示 Toast 通知。
     * @param {string} message 通知文本
     * @param {'success'|'warning'|'error'|'info'} type 通知类型
     * @param {number} duration 显示时长（毫秒），默认 3000
     */
    function showToast(message, type = 'info', duration = 3000) {
        const container = _ensureToastContainer();
        const toast = el('div', 'toast toast-' + type, message);
        const closeBtn = el('span', 'toast-close', '×');
        closeBtn.addEventListener('click', () => {
            toast.classList.add('toast-exit');
            setTimeout(() => toast.remove(), 300);
        });
        toast.appendChild(closeBtn);
        container.appendChild(toast);
        // 自动消失
        setTimeout(() => {
            if (toast.parentNode) {
                toast.classList.add('toast-exit');
                setTimeout(() => toast.remove(), 300);
            }
        }, duration);
    }

    // ---------- M5: API 封装 + 拦截器 + 连接状态 ----------
    async function apiGet(path) {
        const r = await fetch(path);
        if (r.status === 401) {
            showToast('登录已过期，请重新登录', 'warning');
            setTimeout(() => { window.location.href = '/login'; }, 1500);
            throw new Error('Unauthorized');
        }
        if (r.status === 403) {
            showToast('权限不足，操作被拒绝', 'error');
            throw new Error('Forbidden');
        }
        if (r.status >= 500) {
            showToast('服务器错误，请稍后重试', 'error');
            throw new Error('GET ' + path + ' -> ' + r.status);
        }
        if (!r.ok) throw new Error('GET ' + path + ' -> ' + r.status);
        return r.json();
    }
    async function apiPost(path, body) {
        const r = await fetch(path, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: body === undefined ? undefined : JSON.stringify(body),
        });
        if (r.status === 401) {
            showToast('登录已过期，请重新登录', 'warning');
            setTimeout(() => { window.location.href = '/login'; }, 1500);
            throw new Error('Unauthorized');
        }
        if (r.status === 403) {
            showToast('权限不足，操作被拒绝', 'error');
            throw new Error('Forbidden');
        }
        if (r.status >= 500) {
            showToast('服务器错误，请稍后重试', 'error');
            throw new Error('POST ' + path + ' -> ' + r.status);
        }
        if (!r.ok) throw new Error('POST ' + path + ' -> ' + r.status);
        return r.json();
    }

    function markConnection(ok) {
        connOnline = ok;
        renderConn();
        renderBanner();
    }
    function renderConn() {
        const box = $('connStatus');
        const text = $('connText');
        box.classList.remove('online', 'offline', 'paused');
        if (paused) {
            box.classList.add('paused');
            text.textContent = '已暂停';
        } else if (sseConnected) {
            box.classList.add('online');
            text.textContent = 'SSE 实时推送';
        } else if (connOnline === true) {
            box.classList.add('online');
            text.textContent = '轮询模式';
        } else if (connOnline === false) {
            box.classList.add('offline');
            text.textContent = '连接中断，自动重连中…';
        } else {
            text.textContent = '连接中…';
        }
    }
    function renderBanner() {
        const banner = $('globalBanner');
        banner.classList.remove('show', 'safe', 'danger', 'offline');
        if (connOnline === false) {
            banner.textContent = '⚠️ 与检测服务连接中断，正在自动重连…';
            banner.classList.add('show', 'offline');
        } else if (bannerDanger) {
            banner.textContent = '🚨 检测到异常流量！请查看告警页签获取详情';
            banner.classList.add('show', 'danger');
        } else if (connOnline === true) {
            banner.textContent = '✓ 系统运行正常，未检测到异常流量';
            banner.classList.add('show', 'safe');
        }
    }

    // ---------- M3: SSE 实时推送管理 ----------
    function updateSseStatus() {
        const wasConnected = sseConnected;
        sseConnected = !!(sseTraffic || sseAlerts);
        if (sseConnected !== wasConnected) {
            markConnection(sseConnected);
        }
    }

    function handleTrafficEvent(data) {
        // 与 refreshTraffic() 相同的 DOM 更新逻辑，但数据来自 SSE
        if (paused) return;
        setText('qps', data.qps);
        setText('connections', data.connections);
        setText('packetCount', data.packet_count);
        setText('portCount', data.port_count);
        setText('srcIpCount', data.src_ip_count);
        setText('synPackets', data.syn_packets);
        setText('udpPackets', data.udp_packets);
        setText('dnsPackets', data.dns_packets);
        setText('updateTime', data.timestamp);

        bannerDanger = !!data.alert;
        const statusEl = $('status');
        if (data.alert) {
            statusEl.textContent = '⚠️ 异常';
            statusEl.className = 'card-value alert';
        } else {
            statusEl.textContent = '✓ 正常';
            statusEl.className = 'card-value normal';
        }

        const label = (data.timestamp || '').split(' ')[1] || data.timestamp;
        const ds = qpsChart.data.datasets;
        qpsChart.data.labels.push(label);
        ds[0].data.push(data.qps);
        ds[1].data.push(thresholds.ddos_threshold);
        if (qpsChart.data.labels.length > 30) {
            qpsChart.data.labels.shift();
            ds[0].data.shift();
            ds[1].data.shift();
        }
        qpsChart.update();
    }

    function handleAlertEvent(alertEntry) {
        // SSE 推送的单条告警
        if (paused) return;
        // 添加到 lastAlerts 头部
        lastAlerts.unshift(alertEntry);
        // 限制内存中保留数量
        if (lastAlerts.length > 200) lastAlerts.length = 200;

        // M5: Toast 通知新告警
        const level = alertEntry.level || 'low';
        const toastType = level === 'high' ? 'error' : level === 'medium' ? 'warning' : 'info';
        const shortMsg = (alertEntry.message || '新告警').substring(0, 60);
        showToast(shortMsg, toastType, 4000);

        // 更新计数
        const counts = {all: lastAlerts.length, high: 0, medium: 0, low: 0};
        lastAlerts.forEach((a) => {
            const lv = a.level || 'low';
            if (counts[lv] !== undefined) counts[lv] += 1;
        });
        setText('cnt-all', counts.all);
        setText('cnt-high', counts.high);
        setText('cnt-medium', counts.medium);
        setText('cnt-low', counts.low);

        renderAlerts();
    }

    function connectSSE() {
        // 关闭旧连接
        disconnectSSE();

        try {
            // 流量 SSE
            sseTraffic = new EventSource('/api/stream/traffic');
            sseTraffic.addEventListener('traffic', (e) => {
                try {
                    const data = JSON.parse(e.data);
                    handleTrafficEvent(data);
                    markConnection(true);
                } catch (err) {
                    console.error('SSE traffic parse error:', err);
                }
            });
            sseTraffic.onerror = () => {
                console.warn('SSE traffic 连接断开，将回退轮询');
                sseTraffic = null;
                updateSseStatus();
                startPollingFallback();
            };

            // 告警 SSE
            sseAlerts = new EventSource('/api/stream/alerts');
            sseAlerts.addEventListener('alert', (e) => {
                try {
                    const data = JSON.parse(e.data);
                    handleAlertEvent(data);
                    markConnection(true);
                } catch (err) {
                    console.error('SSE alert parse error:', err);
                }
            });
            sseAlerts.onerror = () => {
                console.warn('SSE alerts 连接断开，将回退轮询');
                sseAlerts = null;
                updateSseStatus();
                startPollingFallback();
            };

            updateSseStatus();
            stopPollingFallback();  // SSE 连接成功，停止轮询
        } catch (err) {
            console.error('SSE 初始化失败，回退轮询:', err);
            sseTraffic = null;
            sseAlerts = null;
            startPollingFallback();
        }
    }

    function disconnectSSE() {
        if (sseTraffic) { sseTraffic.close(); sseTraffic = null; }
        if (sseAlerts) { sseAlerts.close(); sseAlerts = null; }
        sseConnected = false;
    }

    // ---------- 轮询降级 ----------
    let _pollingActive = false;

    function startPollingFallback() {
        if (_pollingActive) return;
        _pollingActive = true;
        pollTimer = setInterval(refreshAll, REFRESH_MS);
        // 立即执行一次
        refreshAll();
    }

    function stopPollingFallback() {
        if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
        _pollingActive = false;
    }

    // 定期尝试恢复 SSE（当处于轮询降级模式时）
    function scheduleSseReconnect() {
        if (sseReconnectTimer) clearInterval(sseReconnectTimer);
        sseReconnectTimer = setInterval(() => {
            if (!sseTraffic && !sseAlerts && !paused) {
                console.info('尝试恢复 SSE 连接…');
                connectSSE();
            }
        }, SSE_RECONNECT_MS);
    }

    // ---------- Tab 切换 ----------
    document.querySelectorAll('.tab-btn').forEach((btn) => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.tab-btn').forEach((b) => b.classList.remove('active'));
            document.querySelectorAll('.tab-pane').forEach((p) => p.classList.remove('active'));
            btn.classList.add('active');
            $('tab-' + btn.dataset.tab).classList.add('active');
        });
    });

    // ---------- 暂停 / 继续 ----------
    $('pauseBtn').addEventListener('click', () => {
        paused = !paused;
        $('pauseBtn').textContent = paused ? '▶ 继续刷新' : '⏸ 暂停刷新';
        renderConn();
    });

    // ---------- 图表 ----------
    let qpsChart, attackPieChart;

    function initCharts() {
        qpsChart = new Chart($('qpsChart').getContext('2d'), {
            type: 'line',
            data: {
                labels: [],
                datasets: [
                    {
                        label: 'QPS',
                        data: [],
                        borderColor: '#4caf50',
                        backgroundColor: 'rgba(76,175,80,0.12)',
                        tension: 0.35,
                        fill: true,
                        pointRadius: 0,
                    },
                    {
                        label: 'DDoS 阈值',
                        data: [],
                        borderColor: 'rgba(255,77,77,0.75)',
                        borderDash: [6, 4],
                        pointRadius: 0,
                        fill: false,
                        tension: 0,
                    },
                ],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                animation: {duration: 250},
                scales: {
                    x: {display: false},
                    y: {beginAtZero: true, grid: {color: 'rgba(255,255,255,0.08)'}, ticks: {color: '#9a9ab0'}},
                },
                plugins: {legend: {labels: {color: '#9a9ab0', boxWidth: 12}}},
            },
        });

        attackPieChart = new Chart($('attackPieChart').getContext('2d'), {
            type: 'doughnut',
            data: {labels: ['Normal'], datasets: [{data: [1], backgroundColor: ['#4caf50'], borderColor: 'rgba(0,0,0,0.3)', borderWidth: 1}]},
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {legend: {display: false}},
            },
        });
    }

    // ---------- 总览刷新 ----------
    async function refreshTraffic() {
        const data = await apiGet('/api/traffic');
        setText('qps', data.qps);
        setText('connections', data.connections);
        setText('packetCount', data.packet_count);
        setText('portCount', data.port_count);
        setText('srcIpCount', data.src_ip_count);
        setText('synPackets', data.syn_packets);
        setText('udpPackets', data.udp_packets);
        setText('dnsPackets', data.dns_packets);
        setText('updateTime', data.timestamp);

        bannerDanger = !!data.alert;
        const statusEl = $('status');
        if (data.alert) {
            statusEl.textContent = '⚠️ 异常';
            statusEl.className = 'card-value alert';
        } else {
            statusEl.textContent = '✓ 正常';
            statusEl.className = 'card-value normal';
        }

        const label = (data.timestamp || '').split(' ')[1] || data.timestamp;
        const ds = qpsChart.data.datasets;
        qpsChart.data.labels.push(label);
        ds[0].data.push(data.qps);
        ds[1].data.push(thresholds.ddos_threshold);
        if (qpsChart.data.labels.length > 30) {
            qpsChart.data.labels.shift();
            ds[0].data.shift();
            ds[1].data.shift();
        }
        qpsChart.update();
    }

    // ---------- 告警刷新（筛选 + 新条目高亮） ----------
    async function refreshAlerts() {
        const data = await apiGet('/api/alerts');
        // 兼容新旧格式：新格式 {alerts, total}，旧格式直接是数组
        lastAlerts = Array.isArray(data) ? data : (data.alerts || []);

        const counts = {all: lastAlerts.length, high: 0, medium: 0, low: 0};
        lastAlerts.forEach((a) => {
            const lv = a.level || 'low';
            if (counts[lv] !== undefined) counts[lv] += 1;
        });
        setText('cnt-all', counts.all);
        setText('cnt-high', counts.high);
        setText('cnt-medium', counts.medium);
        setText('cnt-low', counts.low);

        renderAlerts();
        alertsFirstLoad = false;
    }

    function renderAlerts() {
        const container = $('alertsContainer');
        clearNode(container);

        const list = lastAlerts.filter((a) => alertFilter === 'all' || (a.level || 'low') === alertFilter);
        if (list.length === 0) {
            container.appendChild(el('p', 'no-alert',
                lastAlerts.length === 0 ? '系统运行正常，暂无安全警报...' : '当前级别筛选下暂无告警'));
            return;
        }

        list.forEach((alert) => {
            const level = alert.level || 'low';
            const key = (alert.time || '') + '|' + (alert.message || '');
            const isNew = !alertsFirstLoad && !seenAlertKeys.has(key);

            const item = el('div', 'alert-item level-' + level + (isNew ? ' is-new' : ''));
            const head = el('div', 'alert-time');
            head.appendChild(el('span', 'level-badge ' + level, LEVEL_LABELS[level] || '异常'));
            head.appendChild(document.createTextNode(alert.time || ''));
            item.appendChild(head);

            let message = alert.message || '';
            if (alert.attack_type) message += ' [' + alert.attack_type + ']';
            if (alert.ml_confidence) message += ' (置信度: ' + (alert.ml_confidence * 100).toFixed(1) + '%)';
            item.appendChild(el('div', 'alert-message', message));
            container.appendChild(item);

            seenAlertKeys.add(key);
            if (isNew) setTimeout(() => item.classList.remove('is-new'), 3000);
        });
    }

    document.querySelectorAll('.filter-chip').forEach((chip) => {
        chip.addEventListener('click', () => {
            document.querySelectorAll('.filter-chip').forEach((c) => c.classList.remove('active'));
            chip.classList.add('active');
            alertFilter = chip.dataset.level;
            renderAlerts();
        });
    });

    // ---------- TLS 刷新 ----------
    async function refreshTls() {
        const data = await apiGet('/api/tls/stats');
        setText('tlsFlows', data.total_tls_flows);
        setText('ja3Count', data.unique_ja3_fingerprints);
        const susEl = $('suspiciousTls');
        susEl.textContent = data.suspicious_tls_flows;
        susEl.className = 'card-value ' + (data.suspicious_tls_flows > 0 ? 'alert' : 'normal');

        const vDist = data.tls_version_distribution || {};
        const vBlock = $('tlsVersionDist');
        clearNode(vBlock);
        const vEntries = Object.entries(vDist);
        if (vEntries.length === 0) {
            vBlock.textContent = '暂无数据';
        } else {
            vEntries.forEach(([k, v], i) => {
                if (i > 0) vBlock.appendChild(document.createElement('br'));
                vBlock.appendChild(document.createTextNode(k + ': '));
                vBlock.appendChild(el('b', '', String(v)));
            });
        }

        const sniBlock = $('suspiciousSni');
        clearNode(sniBlock);
        const snis = data.suspicious_sni_list || [];
        if (snis.length === 0) {
            sniBlock.appendChild(el('span', 'tag-ok', '无异常 SNI'));
        } else {
            snis.forEach((s, i) => {
                if (i > 0) sniBlock.appendChild(document.createElement('br'));
                sniBlock.appendChild(el('span', 'tag-bad', '⚠️ ' + s));
            });
        }

        const ja3Block = $('ja3Top');
        clearNode(ja3Block);
        const ja3Top = data.ja3_top_fingerprints || [];
        if (ja3Top.length === 0) {
            ja3Block.textContent = '暂无数据';
        } else {
            ja3Top.slice(0, 5).forEach((j, i) => {
                if (i > 0) ja3Block.appendChild(document.createElement('br'));
                const hash = String(j.hash || '');
                ja3Block.appendChild(el('span', j.is_benign ? 'tag-ok' : 'tag-bad',
                    hash.slice(0, 8) + '… (' + j.count + ')'));
            });
        }
    }

    // ---------- 双引擎刷新 ----------
    async function refreshDual() {
        const data = await apiGet('/api/dual/stats');

        const mlStatus = $('mlStatus');
        if (data.model_loaded) {
            mlStatus.textContent = '✓ 已加载';
            mlStatus.className = 'card-value normal';
        } else {
            mlStatus.textContent = '✗ 未加载';
            mlStatus.className = 'card-value alert';
        }

        const predEl = $('mlPrediction');
        predEl.textContent = data.last_ml_prediction || '--';
        predEl.className = 'card-value ' + ((data.last_ml_prediction && data.last_ml_prediction !== 'Normal') ? 'alert' : 'normal');

        const confEl = $('mlConfidence');
        if (data.last_ml_confidence > 0) {
            confEl.textContent = (data.last_ml_confidence * 100).toFixed(1) + '%';
            confEl.className = 'card-value ' + (data.last_ml_confidence < 0.6 ? 'warning' : 'normal');
        } else {
            confEl.textContent = '--';
            confEl.className = 'card-value';
        }

        setText('ruleAlertCount', data.rule_alert_count || 0);
        setText('mlPredictCount', data.ml_predict_count || 0);
        setText('mlAttackCount', data.ml_attack_count || 0);

        const dist = data.attack_type_distribution || {};
        const entries = Object.entries(dist).sort((a, b) => b[1] - a[1]);
        if (entries.length > 0) {
            // 按计数排序取 Top N，其余类别合并为“其他”，限制饼图与图例长度
            const shown = entries.slice(0, MAX_PIE_TYPES).map(([name, count]) => ({name, count}));
            const rest = entries.slice(MAX_PIE_TYPES);
            if (rest.length > 0) {
                shown.push({
                    name: '其他（' + rest.length + ' 类）',
                    count: rest.reduce((sum, [, c]) => sum + c, 0),
                });
            }
            const labels = shown.map((x) => x.name);
            const colors = labels.map((l, i) => {
                if (l === 'Normal') return '#4caf50';
                if (l.indexOf('其他') === 0) return OTHER_COLOR;
                return ATTACK_PALETTE[i % ATTACK_PALETTE.length];
            });
            attackPieChart.data.labels = labels;
            attackPieChart.data.datasets[0].data = shown.map((x) => x.count);
            attackPieChart.data.datasets[0].backgroundColor = colors;
            attackPieChart.update();

            const legend = $('attackTypeList');
            clearNode(legend);
            shown.forEach((x, i) => {
                const row = el('div', 'legend-item');
                row.title = x.name + ': ' + x.count;
                const dot = el('span', 'legend-dot');
                dot.style.background = colors[i];
                row.appendChild(dot);
                row.appendChild(el('span', 'legend-name', x.name));
                row.appendChild(el('b', 'legend-count', String(x.count)));
                legend.appendChild(row);
            });
        }
    }

    // ---------- 统一轮询（降级模式使用，或 SSE 模式下仅刷新 TLS/双引擎） ----------
    async function refreshAll() {
        if (paused) return;
        if (sseConnected) {
            // SSE 模式：流量和告警由 SSE 推送，仅轮询 TLS 和双引擎
            const tasks = [refreshTls(), refreshDual()];
            const results = await Promise.allSettled(tasks);
            markConnection(results.some((r) => r.status === 'fulfilled') && results.every((r) => r.status === 'fulfilled'));
        } else {
            // 轮询降级模式：全部刷新
            const tasks = [refreshTraffic(), refreshAlerts(), refreshTls(), refreshDual()];
            const results = await Promise.allSettled(tasks);
            markConnection(results.some((r) => r.status === 'fulfilled') && results.every((r) => r.status === 'fulfilled'));
        }
    }

    // ---------- 阈值配置 ----------
    const CFG_FIELDS = [
        ['cfg_ddos', 'ddos_threshold'],
        ['cfg_scan', 'port_scan_threshold'],
        ['cfg_syn', 'syn_flood_threshold'],
        ['cfg_udp', 'udp_flood_threshold'],
        ['cfg_bf', 'brute_force_threshold'],
        ['cfg_bf_win', 'brute_force_window'],
        ['cfg_lateral', 'lateral_movement_threshold'],
    ];
    CFG_FIELDS.forEach(([inputId]) => {
        $(inputId).addEventListener('input', () => $(inputId).classList.remove('input-error'));
    });

    function fillConfigForm(cfg) {
        CFG_FIELDS.forEach(([inputId, key]) => {
            if (cfg[key] !== undefined) $(inputId).value = cfg[key];
        });
    }

    async function loadConfig() {
        const cfg = await apiGet('/api/config');
        initialConfig = cfg;
        Object.assign(thresholds, cfg);
        fillConfigForm(cfg);
    }

    function setFormMsg(id, text, cls) {
        const node = $(id);
        node.textContent = text;
        node.className = 'form-msg' + (cls ? ' ' + cls : '');
    }

    $('btnSaveConfig').addEventListener('click', async () => {
        const payload = {};
        let valid = true;
        CFG_FIELDS.forEach(([inputId, key]) => {
            const v = Number($(inputId).value);
            if (!Number.isInteger(v) || v <= 0) {
                $(inputId).classList.add('input-error');
                valid = false;
            } else {
                payload[key] = v;
            }
        });
        if (!valid) {
            setFormMsg('configMsg', '✗ 阈值必须为正整数', 'error');
            return;
        }
        const btn = $('btnSaveConfig');
        btn.disabled = true;
        setFormMsg('configMsg', '保存中…', '');
        try {
            const data = await apiPost('/api/config', payload);
            if (data.status === 'success') {
                Object.assign(thresholds, data.config || payload);
                setFormMsg('configMsg', '✓ 阈值已更新并立即生效', 'success');
            } else {
                setFormMsg('configMsg', '✗ 保存失败', 'error');
            }
        } catch (e) {
            setFormMsg('configMsg', '✗ 保存失败：' + e.message, 'error');
        } finally {
            btn.disabled = false;
            setTimeout(() => { if (!$('configMsg').classList.contains('error')) setFormMsg('configMsg', ''); }, 3000);
        }
    });

    $('btnResetConfig').addEventListener('click', () => {
        fillConfigForm(initialConfig);
        CFG_FIELDS.forEach(([inputId]) => $(inputId).classList.remove('input-error'));
        setFormMsg('configMsg', '已恢复为服务端当前值（仍需保存才生效）', '');
    });

    // ---------- 抓包控制 ----------
    async function actionOnce(btn, fn) {
        btn.disabled = true;
        try { await fn(); } finally { btn.disabled = false; }
    }

    $('btnStartCapture').addEventListener('click', () => actionOnce($('btnStartCapture'), async () => {
        await apiPost('/api/capture/start');
        const s = $('captureStatus');
        s.textContent = '🟢 抓包中（真实流量数据）';
        s.style.color = 'var(--safe)';
    }));

    $('btnStopCapture').addEventListener('click', () => actionOnce($('btnStopCapture'), async () => {
        await apiPost('/api/capture/stop');
        const s = $('captureStatus');
        s.textContent = '未启动（当前为模拟数据）';
        s.style.color = 'var(--text-dim)';
    }));

    $('btnSaveCsv').addEventListener('click', () => actionOnce($('btnSaveCsv'), async () => {
        try {
            await apiGet('/api/save');
            setFormMsg('saveMsg', '✓ 已保存到 traffic_stats.csv', 'success');
        } catch (e) {
            setFormMsg('saveMsg', '✗ 保存失败：' + e.message, 'error');
        }
        setTimeout(() => setFormMsg('saveMsg', ''), 3000);
    }));

    // ---------- ML 模型控制 ----------
    $('btnLoadML').addEventListener('click', () => actionOnce($('btnLoadML'), async () => {
        const s = $('mlLoadStatus');
        s.textContent = '加载中…';
        s.style.color = 'var(--yellow)';
        try {
            const data = await apiPost('/api/dual/load', {});
            if (data.model_loaded) {
                s.textContent = '✓ 模型已加载，ML 预测运行中';
                s.style.color = 'var(--safe)';
            } else {
                s.textContent = '✗ 模型加载失败（请确认 model.pkl 存在）';
                s.style.color = 'var(--danger)';
            }
        } catch (e) {
            s.textContent = '✗ 加载异常：' + e.message;
            s.style.color = 'var(--danger)';
        }
    }));

    $('btnStopML').addEventListener('click', () => actionOnce($('btnStopML'), async () => {
        await apiPost('/api/dual/stop');
        const s = $('mlLoadStatus');
        s.textContent = 'ML 预测已停止';
        s.style.color = 'var(--text-dim)';
    }));

    // ---------- 载荷检测台 ----------
    const SAMPLES = {
        sqli: "GET /login?username=admin' OR 1=1--&password=123 HTTP/1.1",
        xss: "<script>alert(document.cookie)</script>",
        normal: 'GET /index.html?page=home&lang=zh HTTP/1.1',
    };
    document.querySelectorAll('[data-sample]').forEach((btn) => {
        btn.addEventListener('click', () => { $('payloadInput').value = SAMPLES[btn.dataset.sample] || ''; });
    });

    $('btnPayloadCheck').addEventListener('click', () => actionOnce($('btnPayloadCheck'), async () => {
        const payload = $('payloadInput').value;
        const result = $('payloadResult');
        clearNode(result);
        if (!payload.trim()) {
            result.className = 'payload-result show danger';
            result.textContent = '请先输入或选择待检测的载荷';
            return;
        }
        try {
            const data = await apiPost('/api/payload/check', {payload});
            if (data.is_anomaly) {
                result.className = 'payload-result show danger';
                result.appendChild(el('div', '', '🚨 检测到 ' + data.alerts.length + ' 条攻击特征：'));
                const ul = el('ul');
                data.alerts.forEach((msg) => ul.appendChild(el('li', '', msg)));
                result.appendChild(ul);
            } else {
                result.className = 'payload-result show safe';
                result.textContent = '✓ 未发现 SQL 注入 / XSS 攻击特征';
            }
            result.appendChild(el('div', 'payload-meta',
                '载荷长度: ' + (data.payload_length !== undefined ? data.payload_length : payload.length) + ' 字符'));
        } catch (e) {
            result.className = 'payload-result show danger';
            result.textContent = '✗ 检测请求失败：' + e.message;
        }
    }));

    // ---------- 攻击模拟控制 ----------
    let atkPollTimer = null;

    function updateAtkStatus() {
        apiGet('/api/attack/status').then((data) => {
            const s = $('atkStatus');
            if (data.running) {
                s.textContent = '🔴 ' + (data.type || 'all') + ' 运行中 (' + data.elapsed_seconds + 's)';
                s.style.color = 'var(--danger)';
            } else {
                s.textContent = '未启动';
                s.style.color = 'var(--text-dim)';
                if (atkPollTimer) { clearInterval(atkPollTimer); atkPollTimer = null; }
            }
        }).catch(() => {});
    }

    $('btnAtkStart').addEventListener('click', () => actionOnce($('btnAtkStart'), async () => {
        const type = $('atk_type').value;
        const duration = Number($('atk_duration').value) || 30;
        try {
            const data = await apiPost('/api/attack/start', {type, duration});
            if (data.status === 'success') {
                $('atkStatus').textContent = '🔴 ' + type + ' 启动中...';
                $('atkStatus').style.color = 'var(--danger)';
                atkPollTimer = setInterval(updateAtkStatus, 2000);
            } else {
                $('atkStatus').textContent = '✗ ' + (data.message || '启动失败');
                $('atkStatus').style.color = 'var(--danger)';
            }
        } catch (e) {
            $('atkStatus').textContent = '✗ 错误: ' + e.message;
            $('atkStatus').style.color = 'var(--danger)';
        }
    }));

    $('btnAtkStop').addEventListener('click', () => actionOnce($('btnAtkStop'), async () => {
        await apiPost('/api/attack/stop');
        $('atkStatus').textContent = '已停止';
        $('atkStatus').style.color = 'var(--text-dim)';
        if (atkPollTimer) { clearInterval(atkPollTimer); atkPollTimer = null; }
    }));

    // ---------- 模型管理 ----------
    let trainPollTimer = null;

    function loadModelList() {
        apiGet('/api/model/list').then((data) => {
            const container = $('modelListContent');
            clearNode(container);
            const runs = data.runs || [];
            if (runs.length === 0) {
                container.textContent = '暂无已训练模型';
                return;
            }
            const table = el('table', 'model-table');
            const thead = el('thead');
            const headerRow = el('tr');
            ['版本ID', '类型', '特征数', 'F1', '最佳'].forEach((h) => headerRow.appendChild(el('th', '', h)));
            thead.appendChild(headerRow);
            table.appendChild(thead);

            const tbody = el('tbody');
            runs.forEach((run) => {
                const row = el('tr');
                const shortId = (run.run_id || '').slice(0, 16) + '…';
                row.appendChild(el('td', '', shortId));
                row.appendChild(el('td', '', run.model_type || '--'));
                row.appendChild(el('td', '', String(run.n_features || '--')));
                const f1 = run.metrics && run.metrics.f1_score ? (run.metrics.f1_score * 100).toFixed(2) + '%' : '--';
                row.appendChild(el('td', '', f1));
                const bestTag = run.is_best ? el('span', 'tag-best', '★ 最佳') : el('span', '', '');
                row.appendChild(el('td', '', ''));
                row.lastChild.appendChild(bestTag);
                tbody.appendChild(row);
            });
            table.appendChild(tbody);
            container.appendChild(table);
        }).catch((e) => {
            $('modelListContent').textContent = '加载失败: ' + e.message;
        });
    }

    function updateTrainStatus() {
        apiGet('/api/model/train-status').then((data) => {
            const s = $('trainStatus');
            const progress = $('trainProgress');
            const bar = $('trainProgressBar');
            const text = $('trainProgressText');
            if (data.running) {
                s.textContent = '⏳ ' + (data.progress || '训练中...');
                s.style.color = 'var(--yellow)';
                progress.style.display = 'flex';
                // 模拟进度（后端无精确进度，仅显示动画）
                bar.style.width = '100%';
                bar.style.animation = 'progressPulse 1.5s ease-in-out infinite';
                text.textContent = data.progress || '训练中...';
            } else {
                progress.style.display = 'none';
                bar.style.animation = 'none';
                if (data.error) {
                    s.textContent = '✗ ' + data.error;
                    s.style.color = 'var(--danger)';
                } else if (data.result) {
                    s.textContent = '✓ 训练完成';
                    s.style.color = 'var(--safe)';
                    loadModelList(); // 刷新模型列表
                } else {
                    s.textContent = '就绪';
                    s.style.color = 'var(--text-dim)';
                }
                if (trainPollTimer) { clearInterval(trainPollTimer); trainPollTimer = null; }
            }
        }).catch(() => {});
    }

    $('btnTrainStart').addEventListener('click', () => actionOnce($('btnTrainStart'), async () => {
        const dataset_type = $('train_dataset').value;
        const balance_method = $('train_balance').value;
        const quick = $('train_quick').checked;
        try {
            const data = await apiPost('/api/model/train', {dataset_type, balance_method, quick});
            if (data.status === 'success') {
                $('trainStatus').textContent = '⏳ 训练已启动...';
                $('trainStatus').style.color = 'var(--yellow)';
                trainPollTimer = setInterval(updateTrainStatus, 3000);
            } else {
                $('trainStatus').textContent = '✗ ' + (data.message || '启动失败');
                $('trainStatus').style.color = 'var(--danger)';
            }
        } catch (e) {
            $('trainStatus').textContent = '✗ 错误: ' + e.message;
            $('trainStatus').style.color = 'var(--danger)';
        }
    }));

    // ---------- 演示模式 ----------
    let demoPollTimer = null;

    $('btnDemoStart').addEventListener('click', () => actionOnce($('btnDemoStart'), async () => {
        const duration = Number($('demo_duration').value) || 30;
        try {
            const data = await apiPost('/api/demo/start', {duration});
            if (data.status === 'success') {
                $('demoStatus').textContent = '🎬 演示运行中 (' + duration + 's)';
                $('demoStatus').style.color = 'var(--safe)';
                $('captureStatus').textContent = '🟢 抓包中（真实流量 + 攻击模拟）';
                $('captureStatus').style.color = 'var(--safe)';
                // 同时轮询攻击状态
                atkPollTimer = setInterval(updateAtkStatus, 2000);
                // 设置自动停止提示
                setTimeout(() => {
                    $('demoStatus').textContent = '演示攻击阶段已结束';
                    $('demoStatus').style.color = 'var(--text-dim)';
                }, duration * 1000);
            } else {
                $('demoStatus').textContent = '✗ ' + (data.message || '启动失败');
                $('demoStatus').style.color = 'var(--danger)';
            }
        } catch (e) {
            $('demoStatus').textContent = '✗ 错误: ' + e.message;
            $('demoStatus').style.color = 'var(--danger)';
        }
    }));

    $('btnDemoStop').addEventListener('click', () => actionOnce($('btnDemoStop'), async () => {
        await apiPost('/api/attack/stop');
        $('demoStatus').textContent = '已停止';
        $('demoStatus').style.color = 'var(--text-dim)';
        if (atkPollTimer) { clearInterval(atkPollTimer); atkPollTimer = null; }
        if (demoPollTimer) { clearInterval(demoPollTimer); demoPollTimer = null; }
    }));

    // ---------- 启动 ----------
    initCharts();
    loadConfig().catch((e) => console.error('加载配置失败:', e));
    loadModelList();

    // M3: 优先使用 SSE 实时推送，失败时自动降级轮询
    connectSSE();
    // TLS 和双引擎指标始终通过轮询获取（无 SSE 端点）
    setInterval(refreshAll, REFRESH_MS);
    // 定期尝试恢复 SSE 连接
    scheduleSseReconnect();

    // 页面可见性变化时重连 SSE
    document.addEventListener('visibilitychange', () => {
        if (!document.hidden && !sseConnected && !paused) {
            console.info('页面恢复可见，重连 SSE…');
            connectSSE();
        }
    });
})();
