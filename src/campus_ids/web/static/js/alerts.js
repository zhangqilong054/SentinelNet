/* ============================================================
 * SentinelNet — alerts.js: 告警列表、筛选、SSE 告警处理
 * 依赖: api.js (SN 命名空间)
 * ============================================================ */
(function (SN) {
    'use strict';

    var LEVEL_LABELS = { high: '高危', medium: '中危', low: '低危' };
    SN.LEVEL_LABELS = LEVEL_LABELS;

    // ---------- 告警状态 ----------
    var lastAlerts = [];
    var seenAlertKeys = new Set();
    var alertFilter = 'all';
    var alertsFirstLoad = true;

    SN.getLastAlerts = function () { return lastAlerts; };
    SN.setAlertsFirstLoad = function (v) { alertsFirstLoad = v; };

    /** 初始化告警筛选按钮 */
    SN.initAlerts = function () {
        document.querySelectorAll('.filter-chip').forEach(function (chip) {
            chip.addEventListener('click', function () {
                document.querySelectorAll('.filter-chip').forEach(function (c) { c.classList.remove('active'); });
                chip.classList.add('active');
                alertFilter = chip.dataset.level;
                renderAlerts();
            });
        });
    };

    /** 从 /api/alerts 刷新告警列表（轮询模式） */
    SN.refreshAlerts = async function () {
        var data = await SN.apiGet('/api/alerts');
        lastAlerts = Array.isArray(data) ? data : (data.alerts || []);
        updateAlertCounts();
        renderAlerts();
        alertsFirstLoad = false;
    };

    /** 处理 SSE 推送的单条告警 */
    SN.handleAlertEvent = function (alertEntry) {
        if (SN.paused) return;
        lastAlerts.unshift(alertEntry);
        if (lastAlerts.length > 200) lastAlerts.length = 200;

        // Toast 通知
        var level = alertEntry.level || 'low';
        var toastType = level === 'high' ? 'error' : level === 'medium' ? 'warning' : 'info';
        var shortMsg = (alertEntry.message || '新告警').substring(0, 60);
        SN.showToast(shortMsg, toastType, 4000);

        updateAlertCounts();
        renderAlerts();
    };

    /** 更新告警计数 */
    function updateAlertCounts() {
        var counts = { all: lastAlerts.length, high: 0, medium: 0, low: 0 };
        lastAlerts.forEach(function (a) {
            var lv = a.level || 'low';
            if (counts[lv] !== undefined) counts[lv] += 1;
        });
        SN.setText('cnt-all', counts.all);
        SN.setText('cnt-high', counts.high);
        SN.setText('cnt-medium', counts.medium);
        SN.setText('cnt-low', counts.low);
    }

    /** 渲染告警列表 DOM */
    function renderAlerts() {
        var container = SN.$('alertsContainer');
        SN.clearNode(container);

        var list = lastAlerts.filter(function (a) {
            return alertFilter === 'all' || (a.level || 'low') === alertFilter;
        });

        if (list.length === 0) {
            container.appendChild(SN.el('p', 'no-alert',
                lastAlerts.length === 0 ? '系统运行正常，暂无安全警报...' : '当前级别筛选下暂无告警'));
            return;
        }

        list.forEach(function (alert) {
            var level = alert.level || 'low';
            var key = (alert.time || '') + '|' + (alert.message || '');
            var isNew = !alertsFirstLoad && !seenAlertKeys.has(key);

            var item = SN.el('div', 'alert-item level-' + level + (isNew ? ' is-new' : ''));
            var head = SN.el('div', 'alert-time');
            head.appendChild(SN.el('span', 'level-badge ' + level, LEVEL_LABELS[level] || '异常'));
            head.appendChild(document.createTextNode(alert.time || ''));
            item.appendChild(head);

            var message = alert.message || '';
            if (alert.attack_type) message += ' [' + alert.attack_type + ']';
            if (alert.ml_confidence) message += ' (置信度: ' + (alert.ml_confidence * 100).toFixed(1) + '%)';
            item.appendChild(SN.el('div', 'alert-message', message));
            container.appendChild(item);

            seenAlertKeys.add(key);
            if (isNew) setTimeout(function () { item.classList.remove('is-new'); }, 3000);
        });
    }

    SN.renderAlerts = renderAlerts;

})(window.SN || (window.SN = {}));