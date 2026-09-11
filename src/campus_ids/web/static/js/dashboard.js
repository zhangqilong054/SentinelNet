/* ============================================================
 * AI-NIDS 监控面板前端逻辑
 * 轮询调度 / 双引擎指标 / 告警筛选 / 阈值与抓包控制 / 载荷检测
 * 所有动态内容均通过 textContent 渲染，避免 HTML 注入。
 * ============================================================ */
(function () {
    'use strict';

    const $ = (id) => document.getElementById(id);
    const REFRESH_MS = 2000;

    // ---------- 状态 ----------
    let paused = false;
    let connOnline = null;          // null=首次连接, true/false
    let bannerDanger = false;       // 安全状态横幅
    let alertFilter = 'all';
    let lastAlerts = [];
    const seenAlertKeys = new Set();
    let alertsFirstLoad = true;

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

    // ---------- API 封装 + 真实连接状态 ----------
    async function apiGet(path) {
        const r = await fetch(path);
        if (!r.ok) throw new Error('GET ' + path + ' -> ' + r.status);
        return r.json();
    }
    async function apiPost(path, body) {
        const r = await fetch(path, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: body === undefined ? undefined : JSON.stringify(body),
        });
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
        } else if (connOnline === true) {
            box.classList.add('online');
            text.textContent = '实时连接';
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
        lastAlerts = Array.isArray(data) ? data : [];

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

    // ---------- 统一轮询 ----------
    async function refreshAll() {
        if (paused) return;
        const tasks = [refreshTraffic(), refreshAlerts(), refreshTls(), refreshDual()];
        const results = await Promise.allSettled(tasks);
        markConnection(results.some((r) => r.status === 'fulfilled') && results.every((r) => r.status === 'fulfilled'));
    }

    // ---------- 阈值配置 ----------
    const CFG_FIELDS = [
        ['cfg_ddos', 'ddos_threshold'],
        ['cfg_scan', 'port_scan_threshold'],
        ['cfg_syn', 'syn_flood_threshold'],
        ['cfg_udp', 'udp_flood_threshold'],
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

    // ---------- 启动 ----------
    initCharts();
    loadConfig().catch((e) => console.error('加载配置失败:', e));
    refreshAll();
    setInterval(refreshAll, REFRESH_MS);
})();
