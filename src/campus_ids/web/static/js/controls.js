/* ============================================================
 * SentinelNet — controls.js: 控制面板交互、SSE 管理、轮询、启动
 * 依赖: api.js, charts.js, alerts.js (SN 命名空间)
 * ============================================================ */
(function (SN) {
    'use strict';

    // ---------- 阈值配置 ----------
    // T-21: 初始值为 config.py 默认值的镜像，首屏 loadConfig() 后由 /api/config 下发值覆盖
    var thresholds = {
        ddos_threshold: 500, port_scan_threshold: 50,
        syn_flood_threshold: 100, udp_flood_threshold: 200,
    };
    var initialConfig = {};
    SN.thresholds = thresholds;

    var CFG_FIELDS = [
        ['cfg_ddos', 'ddos_threshold'],
        ['cfg_scan', 'port_scan_threshold'],
        ['cfg_syn', 'syn_flood_threshold'],
        ['cfg_udp', 'udp_flood_threshold'],
        ['cfg_bf', 'brute_force_threshold'],
        ['cfg_bf_win', 'brute_force_window'],
        ['cfg_lateral', 'lateral_movement_threshold'],
    ];

    // ---------- SSE 连接管理 ----------
    var sseTraffic = null;
    var sseAlerts = null;
    var pollTimer = null;
    var sseReconnectTimer = null;
    var _pollingActive = false;

    function updateSseStatus() {
        var wasConnected = SN.sseConnected;
        SN.sseConnected = !!(sseTraffic || sseAlerts);
        if (SN.sseConnected !== wasConnected) {
            SN.markConnection(SN.sseConnected);
        }
    }

    function handleTrafficEvent(data) {
        if (SN.paused) return;
        SN.setText('qps', data.qps);
        SN.setText('connections', data.connections);
        SN.setText('packetCount', data.packet_count);
        SN.setText('portCount', data.port_count);
        SN.setText('srcIpCount', data.src_ip_count);
        SN.setText('synPackets', data.syn_packets);
        SN.setText('udpPackets', data.udp_packets);
        SN.setText('dnsPackets', data.dns_packets);
        SN.setText('updateTime', data.timestamp);

        SN.bannerDanger = !!data.alert;
        var statusEl = SN.$('status');
        if (data.alert) {
            statusEl.textContent = '\u26a0\ufe0f 异常';
            statusEl.className = 'card-value alert';
        } else {
            statusEl.textContent = '\u2713 正常';
            statusEl.className = 'card-value normal';
        }
        SN.updateQpsChart(data, thresholds);

        // 隐藏骨架屏
        SN.hideSkeleton('overview');
    }

    function connectSSE() {
        disconnectSSE();
        // O-08: SSE URL 拼 token 参数（EventSource 不支持自定义头）
        var sseToken = localStorage.getItem('api_token') || '';
        var sseSuffix = sseToken ? '?token=' + encodeURIComponent(sseToken) : '';
        try {
            sseTraffic = new EventSource('/api/stream/traffic' + sseSuffix);
            sseTraffic.addEventListener('traffic', function (e) {
                try {
                    var data = JSON.parse(e.data);
                    handleTrafficEvent(data);
                    SN.markConnection(true);
                } catch (err) {
                    console.error('SSE traffic parse error:', err);
                }
            });
            sseTraffic.onerror = function () {
                console.warn('SSE traffic 连接断开，将回退轮询');
                sseTraffic = null;
                updateSseStatus();
                startPollingFallback();
            };

            sseAlerts = new EventSource('/api/stream/alerts' + sseSuffix);
            sseAlerts.addEventListener('alert', function (e) {
                try {
                    var data = JSON.parse(e.data);
                    SN.handleAlertEvent(data);
                    SN.markConnection(true);
                } catch (err) {
                    console.error('SSE alert parse error:', err);
                }
            });
            sseAlerts.onerror = function () {
                console.warn('SSE alerts 连接断开，将回退轮询');
                sseAlerts = null;
                updateSseStatus();
                startPollingFallback();
            };

            updateSseStatus();
            stopPollingFallback();
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
        SN.sseConnected = false;
    }

    // ---------- 轮询降级 ----------
    function startPollingFallback() {
        if (_pollingActive) return;
        _pollingActive = true;
        // 不再新建 interval，init 的唯一 pollTimer 已在运行
        refreshAll();
    }

    function stopPollingFallback() {
        // O-02: 不再清除 pollTimer — 它是常驻定时器，refreshAll 内部按 sseConnected 分叉决定刷新范围
        _pollingActive = false;
    }

    function scheduleSseReconnect() {
        if (sseReconnectTimer) clearInterval(sseReconnectTimer);
        sseReconnectTimer = setInterval(function () {
            if (!sseTraffic && !sseAlerts && !SN.paused) {
                console.info('尝试恢复 SSE 连接…');
                connectSSE();
            }
        }, SN.SSE_RECONNECT_MS);
    }

    // ---------- 总览刷新（轮询模式） ----------
    async function refreshTraffic() {
        var data = await SN.apiGet('/api/traffic');
        SN.setText('qps', data.qps);
        SN.setText('connections', data.connections);
        SN.setText('packetCount', data.packet_count);
        SN.setText('portCount', data.port_count);
        SN.setText('srcIpCount', data.src_ip_count);
        SN.setText('synPackets', data.syn_packets);
        SN.setText('udpPackets', data.udp_packets);
        SN.setText('dnsPackets', data.dns_packets);
        SN.setText('updateTime', data.timestamp);

        SN.bannerDanger = !!data.alert;
        var statusEl = SN.$('status');
        if (data.alert) {
            statusEl.textContent = '\u26a0\ufe0f 异常';
            statusEl.className = 'card-value alert';
        } else {
            statusEl.textContent = '\u2713 正常';
            statusEl.className = 'card-value normal';
        }
        SN.updateQpsChart(data, thresholds);
        SN.hideSkeleton('overview');
    }

    // ---------- TLS 刷新 ----------
    async function refreshTls() {
        var data = await SN.apiGet('/api/tls/stats');
        SN.setText('tlsFlows', data.total_tls_flows);
        SN.setText('ja3Count', data.unique_ja3_fingerprints);
        var susEl = SN.$('suspiciousTls');
        susEl.textContent = data.suspicious_tls_flows;
        susEl.className = 'card-value ' + (data.suspicious_tls_flows > 0 ? 'alert' : 'normal');

        var vDist = data.tls_version_distribution || {};
        var vBlock = SN.$('tlsVersionDist');
        SN.clearNode(vBlock);
        var vEntries = Object.entries(vDist);
        if (vEntries.length === 0) {
            vBlock.textContent = '暂无数据';
        } else {
            vEntries.forEach(function (pair, i) {
                if (i > 0) vBlock.appendChild(document.createElement('br'));
                vBlock.appendChild(document.createTextNode(pair[0] + ': '));
                vBlock.appendChild(SN.el('b', '', String(pair[1])));
            });
        }

        var sniBlock = SN.$('suspiciousSni');
        SN.clearNode(sniBlock);
        var snis = data.suspicious_sni_list || [];
        if (snis.length === 0) {
            sniBlock.appendChild(SN.el('span', 'tag-ok', '无异常 SNI'));
        } else {
            snis.forEach(function (s, i) {
                if (i > 0) sniBlock.appendChild(document.createElement('br'));
                sniBlock.appendChild(SN.el('span', 'tag-bad', '\u26a0\ufe0f ' + s));
            });
        }

        var ja3Block = SN.$('ja3Top');
        SN.clearNode(ja3Block);
        var ja3Top = data.ja3_top_fingerprints || [];
        if (ja3Top.length === 0) {
            ja3Block.textContent = '暂无数据';
        } else {
            ja3Top.slice(0, 5).forEach(function (j, i) {
                if (i > 0) ja3Block.appendChild(document.createElement('br'));
                var hash = String(j.hash || '');
                ja3Block.appendChild(SN.el('span', j.is_benign ? 'tag-ok' : 'tag-bad',
                    hash.slice(0, 8) + '… (' + j.count + ')'));
            });
        }
        SN.hideSkeleton('tls');
    }

    // ---------- 双引擎刷新 ----------
    async function refreshDual() {
        var data = await SN.apiGet('/api/dual/stats');

        var mlStatus = SN.$('mlStatus');
        if (data.model_loaded) {
            mlStatus.textContent = '\u2713 已加载';
            mlStatus.className = 'card-value normal';
        } else {
            mlStatus.textContent = '\u2717 未加载';
            mlStatus.className = 'card-value alert';
        }

        var predEl = SN.$('mlPrediction');
        predEl.textContent = data.last_ml_prediction || '--';
        predEl.className = 'card-value ' + ((data.last_ml_prediction && data.last_ml_prediction !== 'Normal') ? 'alert' : 'normal');

        var confEl = SN.$('mlConfidence');
        if (data.last_ml_confidence > 0) {
            confEl.textContent = (data.last_ml_confidence * 100).toFixed(1) + '%';
            confEl.className = 'card-value ' + (data.last_ml_confidence < 0.6 ? 'warning' : 'normal');
        } else {
            confEl.textContent = '--';
            confEl.className = 'card-value';
        }

        SN.setText('ruleAlertCount', data.rule_alert_count || 0);
        SN.setText('mlPredictCount', data.ml_predict_count || 0);
        SN.setText('mlAttackCount', data.ml_attack_count || 0);

        var dist = data.attack_type_distribution || {};
        SN.updateAttackPie(dist);
        SN.hideSkeleton('dual');
    }

    // ---------- 统一轮询 ----------
    async function refreshAll() {
        if (SN.paused) return;
        if (SN.sseConnected) {
            var tasks = [refreshTls(), refreshDual()];
            var results = await Promise.allSettled(tasks);
            SN.markConnection(results.every(function (r) { return r.status === 'fulfilled'; }));
        } else {
            var tasks2 = [refreshTraffic(), SN.refreshAlerts(), refreshTls(), refreshDual()];
            var results2 = await Promise.allSettled(tasks2);
            SN.markConnection(results2.every(function (r) { return r.status === 'fulfilled'; }));
        }
    }

    // ---------- Tab 切换 ----------
    document.querySelectorAll('.tab-btn').forEach(function (btn) {
        btn.addEventListener('click', function () {
            document.querySelectorAll('.tab-btn').forEach(function (b) { b.classList.remove('active'); });
            document.querySelectorAll('.tab-pane').forEach(function (p) { p.classList.remove('active'); });
            btn.classList.add('active');
            SN.$('tab-' + btn.dataset.tab).classList.add('active');
        });
    });

    // ---------- 暂停 / 继续 ----------
    SN.$('pauseBtn').addEventListener('click', function () {
        SN.paused = !SN.paused;
        SN.$('pauseBtn').textContent = SN.paused ? '\u25b6 继续刷新' : '\u23f8 暂停刷新';
        SN.renderConn();
    });

    // ---------- 阈值配置 ----------
    CFG_FIELDS.forEach(function (pair) {
        SN.$(pair[0]).addEventListener('input', function () {
            SN.$(pair[0]).classList.remove('input-error');
        });
    });

    function fillConfigForm(cfg) {
        CFG_FIELDS.forEach(function (pair) {
            if (cfg[pair[1]] !== undefined) SN.$(pair[0]).value = cfg[pair[1]];
        });
    }

    async function loadConfig() {
        var cfg = await SN.apiGet('/api/config');
        initialConfig = cfg;
        Object.assign(thresholds, cfg);
        fillConfigForm(cfg);
    }

    function setFormMsg(id, text, cls) {
        var node = SN.$(id);
        node.textContent = text;
        node.className = 'form-msg' + (cls ? ' ' + cls : '');
    }

    SN.$('btnSaveConfig').addEventListener('click', async function () {
        var payload = {};
        var valid = true;
        CFG_FIELDS.forEach(function (pair) {
            var v = Number(SN.$(pair[0]).value);
            if (!Number.isInteger(v) || v <= 0) {
                SN.$(pair[0]).classList.add('input-error');
                valid = false;
            } else {
                payload[pair[1]] = v;
            }
        });
        if (!valid) {
            setFormMsg('configMsg', '\u2717 阈值必须为正整数', 'error');
            return;
        }
        var btn = SN.$('btnSaveConfig');
        btn.disabled = true;
        setFormMsg('configMsg', '保存中…', '');
        try {
            var data = await SN.apiPost('/api/config', payload);
            if (data.status === 'success') {
                Object.assign(thresholds, data.config || payload);
                setFormMsg('configMsg', '\u2713 阈值已更新并立即生效', 'success');
            } else {
                setFormMsg('configMsg', '\u2717 保存失败', 'error');
            }
        } catch (e) {
            setFormMsg('configMsg', '\u2717 保存失败：' + e.message, 'error');
        } finally {
            btn.disabled = false;
            setTimeout(function () { if (!SN.$('configMsg').classList.contains('error')) setFormMsg('configMsg', ''); }, 3000);
        }
    });

    SN.$('btnResetConfig').addEventListener('click', function () {
        fillConfigForm(initialConfig);
        CFG_FIELDS.forEach(function (pair) { SN.$(pair[0]).classList.remove('input-error'); });
        setFormMsg('configMsg', '已恢复为服务端当前值（仍需保存才生效）', '');
    });

    // ---------- 抓包控制 ----------
    async function actionOnce(btn, fn) {
        btn.disabled = true;
        try { await fn(); } finally { btn.disabled = false; }
    }

    SN.$('btnStartCapture').addEventListener('click', function () {
        actionOnce(SN.$('btnStartCapture'), async function () {
            await SN.apiPost('/api/capture/start');
            var s = SN.$('captureStatus');
            s.textContent = '\ud83d\udfe2 抓包中（真实流量数据）';
            s.style.color = 'var(--safe)';
        });
    });

    SN.$('btnStopCapture').addEventListener('click', function () {
        actionOnce(SN.$('btnStopCapture'), async function () {
            await SN.apiPost('/api/capture/stop');
            var s = SN.$('captureStatus');
            s.textContent = '未启动（当前为模拟数据）';
            s.style.color = 'var(--text-dim)';
        });
    });

    SN.$('btnSaveCsv').addEventListener('click', function () {
        actionOnce(SN.$('btnSaveCsv'), async function () {
            try {
                await SN.apiGet('/api/save');
                setFormMsg('saveMsg', '\u2713 已保存到 traffic_stats.csv', 'success');
            } catch (e) {
                setFormMsg('saveMsg', '\u2717 保存失败：' + e.message, 'error');
            }
            setTimeout(function () { setFormMsg('saveMsg', ''); }, 3000);
        });
    });

    // ---------- ML 模型控制 ----------
    SN.$('btnLoadML').addEventListener('click', function () {
        actionOnce(SN.$('btnLoadML'), async function () {
            var s = SN.$('mlLoadStatus');
            s.textContent = '加载中…';
            s.style.color = 'var(--yellow)';
            try {
                var data = await SN.apiPost('/api/dual/load', {});
                if (data.model_loaded) {
                    s.textContent = '\u2713 模型已加载，ML 预测运行中';
                    s.style.color = 'var(--safe)';
                } else {
                    s.textContent = '\u2717 模型加载失败（请确认 model.pkl 存在）';
                    s.style.color = 'var(--danger)';
                }
            } catch (e) {
                s.textContent = '\u2717 加载异常：' + e.message;
                s.style.color = 'var(--danger)';
            }
        });
    });

    SN.$('btnStopML').addEventListener('click', function () {
        actionOnce(SN.$('btnStopML'), async function () {
            await SN.apiPost('/api/dual/stop');
            var s = SN.$('mlLoadStatus');
            s.textContent = 'ML 预测已停止';
            s.style.color = 'var(--text-dim)';
        });
    });

    // ---------- 载荷检测台 ----------
    var SAMPLES = {
        sqli: "GET /login?username=admin' OR 1=1--&password=123 HTTP/1.1",
        xss: "<script>alert(document.cookie)</script>",
        normal: 'GET /index.html?page=home&lang=zh HTTP/1.1',
    };
    document.querySelectorAll('[data-sample]').forEach(function (btn) {
        btn.addEventListener('click', function () { SN.$('payloadInput').value = SAMPLES[btn.dataset.sample] || ''; });
    });

    SN.$('btnPayloadCheck').addEventListener('click', function () {
        actionOnce(SN.$('btnPayloadCheck'), async function () {
            var payload = SN.$('payloadInput').value;
            var result = SN.$('payloadResult');
            SN.clearNode(result);
            if (!payload.trim()) {
                result.className = 'payload-result show danger';
                result.textContent = '请先输入或选择待检测的载荷';
                return;
            }
            try {
                var data = await SN.apiPost('/api/payload/check', { payload: payload });
                if (data.is_anomaly) {
                    result.className = 'payload-result show danger';
                    result.appendChild(SN.el('div', '', '\ud83d\udea8 检测到 ' + data.alerts.length + ' 条攻击特征：'));
                    var ul = SN.el('ul');
                    data.alerts.forEach(function (msg) { ul.appendChild(SN.el('li', '', msg)); });
                    result.appendChild(ul);
                } else {
                    result.className = 'payload-result show safe';
                    result.textContent = '\u2713 未发现 SQL 注入 / XSS 攻击特征';
                }
                result.appendChild(SN.el('div', 'payload-meta',
                    '载荷长度: ' + (data.payload_length !== undefined ? data.payload_length : payload.length) + ' 字符'));
            } catch (e) {
                result.className = 'payload-result show danger';
                result.textContent = '\u2717 检测请求失败：' + e.message;
            }
        });
    });

    // ---------- 攻击模拟控制 ----------
    var atkPollTimer = null;

    function updateAtkStatus() {
        SN.apiGet('/api/attack/status').then(function (data) {
            var s = SN.$('atkStatus');
            if (data.running) {
                s.textContent = '\ud83d\udd34 ' + (data.type || 'all') + ' 运行中 (' + data.elapsed_seconds + 's)';
                s.style.color = 'var(--danger)';
            } else {
                s.textContent = '未启动';
                s.style.color = 'var(--text-dim)';
                if (atkPollTimer) { clearInterval(atkPollTimer); atkPollTimer = null; }
            }
        }).catch(function () {});
    }

    SN.$('btnAtkStart').addEventListener('click', function () {
        actionOnce(SN.$('btnAtkStart'), async function () {
            var type = SN.$('atk_type').value;
            var duration = Number(SN.$('atk_duration').value) || 30;
            try {
                var data = await SN.apiPost('/api/attack/start', { type: type, duration: duration });
                if (data.status === 'success') {
                    SN.$('atkStatus').textContent = '\ud83d\udd34 ' + type + ' 启动中...';
                    SN.$('atkStatus').style.color = 'var(--danger)';
                    atkPollTimer = setInterval(updateAtkStatus, 2000);
                } else {
                    SN.$('atkStatus').textContent = '\u2717 ' + (data.message || '启动失败');
                    SN.$('atkStatus').style.color = 'var(--danger)';
                }
            } catch (e) {
                SN.$('atkStatus').textContent = '\u2717 错误: ' + e.message;
                SN.$('atkStatus').style.color = 'var(--danger)';
            }
        });
    });

    SN.$('btnAtkStop').addEventListener('click', function () {
        actionOnce(SN.$('btnAtkStop'), async function () {
            await SN.apiPost('/api/attack/stop');
            SN.$('atkStatus').textContent = '已停止';
            SN.$('atkStatus').style.color = 'var(--text-dim)';
            if (atkPollTimer) { clearInterval(atkPollTimer); atkPollTimer = null; }
        });
    });

    // ---------- 模型管理 ----------
    var trainPollTimer = null;

    function loadModelList() {
        SN.apiGet('/api/model/list').then(function (data) {
            var container = SN.$('modelListContent');
            SN.clearNode(container);
            var runs = data.runs || [];
            if (runs.length === 0) {
                container.textContent = '暂无已训练模型';
                return;
            }
            var table = SN.el('table', 'model-table');
            var thead = SN.el('thead');
            var headerRow = SN.el('tr');
            ['版本ID', '类型', '特征数', 'F1', '最佳'].forEach(function (h) { headerRow.appendChild(SN.el('th', '', h)); });
            thead.appendChild(headerRow);
            table.appendChild(thead);

            var tbody = SN.el('tbody');
            runs.forEach(function (run) {
                var row = SN.el('tr');
                var shortId = (run.run_id || '').slice(0, 16) + '…';
                row.appendChild(SN.el('td', '', shortId));
                row.appendChild(SN.el('td', '', run.model_type || '--'));
                row.appendChild(SN.el('td', '', String(run.n_features || '--')));
                var f1 = run.metrics && run.metrics.f1_score ? (run.metrics.f1_score * 100).toFixed(2) + '%' : '--';
                row.appendChild(SN.el('td', '', f1));
                var bestTag = run.is_best ? SN.el('span', 'tag-best', '\u2605 最佳') : SN.el('span', '', '');
                row.appendChild(SN.el('td', '', ''));
                row.lastChild.appendChild(bestTag);
                tbody.appendChild(row);
            });
            table.appendChild(tbody);
            container.appendChild(table);
        }).catch(function (e) {
            SN.$('modelListContent').textContent = '加载失败: ' + e.message;
        });
    }

    function updateTrainStatus() {
        SN.apiGet('/api/model/train-status').then(function (data) {
            var s = SN.$('trainStatus');
            var progress = SN.$('trainProgress');
            var bar = SN.$('trainProgressBar');
            var text = SN.$('trainProgressText');
            if (data.running) {
                s.textContent = '\u23f3 ' + (data.progress || '训练中...');
                s.style.color = 'var(--yellow)';
                progress.style.display = 'flex';
                bar.style.width = '100%';
                bar.style.animation = 'progressPulse 1.5s ease-in-out infinite';
                text.textContent = data.progress || '训练中...';
            } else {
                progress.style.display = 'none';
                bar.style.animation = 'none';
                if (data.error) {
                    s.textContent = '\u2717 ' + data.error;
                    s.style.color = 'var(--danger)';
                } else if (data.result) {
                    s.textContent = '\u2713 训练完成';
                    s.style.color = 'var(--safe)';
                    loadModelList();
                } else {
                    s.textContent = '就绪';
                    s.style.color = 'var(--text-dim)';
                }
                if (trainPollTimer) { clearInterval(trainPollTimer); trainPollTimer = null; }
            }
        }).catch(function () {});
    }

    SN.$('btnTrainStart').addEventListener('click', function () {
        actionOnce(SN.$('btnTrainStart'), async function () {
            var dataset_type = SN.$('train_dataset').value;
            var balance_method = SN.$('train_balance').value;
            var quick = SN.$('train_quick').checked;
            try {
                var data = await SN.apiPost('/api/model/train', { dataset_type: dataset_type, balance_method: balance_method, quick: quick });
                if (data.status === 'success') {
                    SN.$('trainStatus').textContent = '\u23f3 训练已启动...';
                    SN.$('trainStatus').style.color = 'var(--yellow)';
                    trainPollTimer = setInterval(updateTrainStatus, 3000);
                } else {
                    SN.$('trainStatus').textContent = '\u2717 ' + (data.message || '启动失败');
                    SN.$('trainStatus').style.color = 'var(--danger)';
                }
            } catch (e) {
                SN.$('trainStatus').textContent = '\u2717 错误: ' + e.message;
                SN.$('trainStatus').style.color = 'var(--danger)';
            }
        });
    });

    // ---------- 增强抓包控制 ----------
    var enhancedPollTimer = null;

    function updateEnhancedStatus() {
        SN.apiGet('/api/capture/enhanced-status').then(function (data) {
            var s = SN.$('enhancedStatus');
            var resultEl = SN.$('enhancedResult');
            var resultContent = SN.$('enhancedResultContent');
            if (data.running) {
                s.textContent = '🔬 增强抓包中 (' + data.duration + 's)...';
                s.style.color = 'var(--safe)';
                if (!enhancedPollTimer) {
                    enhancedPollTimer = setInterval(updateEnhancedStatus, 3000);
                }
            } else {
                if (data.status === 'completed') {
                    s.textContent = '✓ 增强抓包完成：' + data.packets + ' 包 / ' + data.flows + ' 条流';
                    s.style.color = 'var(--safe)';
                    resultEl.style.display = 'block';
                    SN.clearNode(resultContent);
                    resultContent.appendChild(SN.el('div', '', '捕获 ' + data.packets + ' 个数据包，聚合为 ' + data.flows + ' 条流特征'));
                    resultContent.appendChild(SN.el('div', '', '数据已保存到 traffic_data.csv'));
                } else if (data.status === 'error') {
                    s.textContent = '✗ 增强抓包失败：' + (data.error || '未知错误');
                    s.style.color = 'var(--danger)';
                } else {
                    s.textContent = '就绪';
                    s.style.color = 'var(--text-dim)';
                }
                if (enhancedPollTimer) { clearInterval(enhancedPollTimer); enhancedPollTimer = null; }
            }
        }).catch(function () {});
    }

    SN.$('btnEnhancedStart').addEventListener('click', function () {
        actionOnce(SN.$('btnEnhancedStart'), async function () {
            var duration = Number(SN.$('enhanced_duration').value) || 60;
            try {
                var data = await SN.apiPost('/api/capture/start-enhanced', { duration: duration });
                if (data.status === 'success') {
                    SN.$('enhancedStatus').textContent = '🔬 增强抓包中 (' + duration + 's)...';
                    SN.$('enhancedStatus').style.color = 'var(--safe)';
                    SN.$('enhancedResult').style.display = 'none';
                    enhancedPollTimer = setInterval(updateEnhancedStatus, 3000);
                } else {
                    SN.$('enhancedStatus').textContent = '✗ ' + (data.message || '启动失败');
                    SN.$('enhancedStatus').style.color = 'var(--danger)';
                }
            } catch (e) {
                SN.$('enhancedStatus').textContent = '✗ 错误: ' + e.message;
                SN.$('enhancedStatus').style.color = 'var(--danger)';
            }
        });
    });

    SN.$('btnEnhancedStop').addEventListener('click', function () {
        actionOnce(SN.$('btnEnhancedStop'), async function () {
            await SN.apiPost('/api/capture/stop-enhanced');
            SN.$('enhancedStatus').textContent = '已停止';
            SN.$('enhancedStatus').style.color = 'var(--text-dim)';
            if (enhancedPollTimer) { clearInterval(enhancedPollTimer); enhancedPollTimer = null; }
        });
    });

    // ---------- 一键全流程 ----------
    var autoPollTimer = null;

    function updateAutoStatus() {
        SN.apiGet('/api/auto/status').then(function (data) {
            var s = SN.$('autoStatus');
            var progress = SN.$('autoProgress');
            var bar = SN.$('autoProgressBar');
            var text = SN.$('autoProgressText');
            if (data.running) {
                s.textContent = '⏳ [' + data.step + '/' + data.total_steps + '] ' + data.step_name + ' — ' + data.message;
                s.style.color = 'var(--yellow)';
                progress.style.display = 'flex';
                var pct = Math.round((data.step / data.total_steps) * 100);
                bar.style.width = pct + '%';
                bar.style.animation = 'progressPulse 1.5s ease-in-out infinite';
                text.textContent = data.step_name + '...';
            } else {
                progress.style.display = 'none';
                bar.style.animation = 'none';
                if (data.error) {
                    s.textContent = '✗ ' + data.error;
                    s.style.color = 'var(--danger)';
                } else if (data.result) {
                    s.textContent = '✓ 全流程完成';
                    s.style.color = 'var(--safe)';
                } else {
                    s.textContent = '就绪';
                    s.style.color = 'var(--text-dim)';
                }
                if (autoPollTimer) { clearInterval(autoPollTimer); autoPollTimer = null; }
            }
        }).catch(function () {});
    }

    SN.$('btnAutoStart').addEventListener('click', function () {
        actionOnce(SN.$('btnAutoStart'), async function () {
            var duration = Number(SN.$('auto_duration').value) || 30;
            try {
                var data = await SN.apiPost('/api/auto/start', { duration: duration });
                if (data.status === 'success') {
                    SN.$('autoStatus').textContent = '⏳ 全流程启动中...';
                    SN.$('autoStatus').style.color = 'var(--yellow)';
                    autoPollTimer = setInterval(updateAutoStatus, 3000);
                } else {
                    SN.$('autoStatus').textContent = '✗ ' + (data.message || '启动失败');
                    SN.$('autoStatus').style.color = 'var(--danger)';
                }
            } catch (e) {
                SN.$('autoStatus').textContent = '✗ 错误: ' + e.message;
                SN.$('autoStatus').style.color = 'var(--danger)';
            }
        });
    });

    // ---------- 环境自检 ----------
    SN.$('btnCheck').addEventListener('click', function () {
        actionOnce(SN.$('btnCheck'), async function () {
            var s = SN.$('checkStatus');
            var resultEl = SN.$('checkResult');
            var resultContent = SN.$('checkResultContent');
            s.textContent = '检查中...';
            s.style.color = 'var(--yellow)';
            resultEl.style.display = 'none';
            try {
                var data = await SN.apiGet('/api/check');
                resultEl.style.display = 'block';
                SN.clearNode(resultContent);

                var overall = data.ok ? '✓ 环境检查通过' : '✗ 环境存在问题';
                s.textContent = overall;
                s.style.color = data.ok ? 'var(--safe)' : 'var(--danger)';

                // Python
                var py = data.python || {};
                var pyStatus = py.ok ? '✓' : '✗';
                resultContent.appendChild(SN.el('div', 'panel-subtitle',
                    pyStatus + ' Python ' + py.version + ' (需要 ' + py.required + ')'));

                // Dependencies
                var deps = data.dependencies || {};
                resultContent.appendChild(SN.el('div', 'panel-subtitle', '依赖包'));
                var depList = SN.el('ul', '');
                (deps.required || []).forEach(function (d) {
                    depList.appendChild(SN.el('li', '',
                        (d.installed ? '✓ ' : '✗ ') + d.package + (d.installed ? '' : ' (未安装)')));
                });
                (deps.optional || []).forEach(function (d) {
                    depList.appendChild(SN.el('li', '',
                        (d.installed ? '✓ ' : '- ') + d.package + (d.installed ? '' : ' (可选，未安装)')));
                });
                resultContent.appendChild(depList);

                // Capture
                var cap = data.capture || {};
                resultContent.appendChild(SN.el('div', 'panel-subtitle',
                    (cap.ok ? '✓ ' : '⚠ ') + cap.message));

                // Model files
                resultContent.appendChild(SN.el('div', 'panel-subtitle', '模型文件'));
                var mfList = SN.el('ul', '');
                (data.model_files || []).forEach(function (f) {
                    mfList.appendChild(SN.el('li', '',
                        (f.exists ? '✓ ' : '- ') + f.name +
                        (f.exists ? ' (' + f.size_kb + ' KB)' : ' (不存在)')));
                });
                resultContent.appendChild(mfList);

                // Data files
                resultContent.appendChild(SN.el('div', 'panel-subtitle', '数据文件'));
                var dfList = SN.el('ul', '');
                (data.data_files || []).forEach(function (f) {
                    dfList.appendChild(SN.el('li', '',
                        (f.exists ? '✓ ' : '- ') + f.name +
                        (f.exists ? ' (' + f.size_kb + ' KB)' : ' (不存在)')));
                });
                resultContent.appendChild(dfList);

            } catch (e) {
                s.textContent = '✗ 检查失败: ' + e.message;
                s.style.color = 'var(--danger)';
            }
        });
    });

    // ---------- 演示模式 ----------
    var demoPollTimer = null;

    SN.$('btnDemoStart').addEventListener('click', function () {
        actionOnce(SN.$('btnDemoStart'), async function () {
            var duration = Number(SN.$('demo_duration').value) || 30;
            try {
                var data = await SN.apiPost('/api/demo/start', { duration: duration });
                if (data.status === 'success') {
                    SN.$('demoStatus').textContent = '\ud83c\udfac 演示运行中 (' + duration + 's)';
                    SN.$('demoStatus').style.color = 'var(--safe)';
                    SN.$('captureStatus').textContent = '\ud83d\udfe2 抓包中（真实流量 + 攻击模拟）';
                    SN.$('captureStatus').style.color = 'var(--safe)';
                    atkPollTimer = setInterval(updateAtkStatus, 2000);
                    setTimeout(function () {
                        SN.$('demoStatus').textContent = '演示攻击阶段已结束';
                        SN.$('demoStatus').style.color = 'var(--text-dim)';
                    }, duration * 1000);
                } else {
                    SN.$('demoStatus').textContent = '\u2717 ' + (data.message || '启动失败');
                    SN.$('demoStatus').style.color = 'var(--danger)';
                }
            } catch (e) {
                SN.$('demoStatus').textContent = '\u2717 错误: ' + e.message;
                SN.$('demoStatus').style.color = 'var(--danger)';
            }
        });
    });

    SN.$('btnDemoStop').addEventListener('click', function () {
        actionOnce(SN.$('btnDemoStop'), async function () {
            await SN.apiPost('/api/attack/stop');
            SN.$('demoStatus').textContent = '已停止';
            SN.$('demoStatus').style.color = 'var(--text-dim)';
            if (atkPollTimer) { clearInterval(atkPollTimer); atkPollTimer = null; }
            if (demoPollTimer) { clearInterval(demoPollTimer); demoPollTimer = null; }
        });
    });

    // ---------- 骨架屏管理 ----------
    SN.hideSkeleton = function (section) {
        var skeletons = document.querySelectorAll('.skeleton[data-section="' + section + '"]');
        skeletons.forEach(function (el) {
            el.classList.remove('skeleton');
            el.classList.add('skeleton-loaded');
        });
    };

    // ---------- 启动 ----------
    SN.initControls = function () {
        SN.initCharts();
        SN.initAlerts();
        loadConfig().catch(function (e) { console.error('加载配置失败:', e); });
        loadModelList();

        // 优先 SSE，失败降级轮询
        connectSSE();
        // 统一轮询 interval（TLS/双引擎等始终需要），SSE 生效时 refreshAll 内部跳过已有数据
        pollTimer = setInterval(refreshAll, SN.REFRESH_MS);
        // 定期尝试恢复 SSE
        scheduleSseReconnect();

        // 页面可见性变化时重连 SSE
        document.addEventListener('visibilitychange', function () {
            if (!document.hidden && !SN.sseConnected && !SN.paused) {
                console.info('页面恢复可见，重连 SSE…');
                connectSSE();
            }
        });
    };

})(window.SN || (window.SN = {}));