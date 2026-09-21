/* SentinelNet 控制台壳脚本（T2.17）
 *
 * 只做两件事：拉 /api/health 渲染健康摘要，拉 /api/tasks 渲染任务表。
 * 刻意**不发任何写请求** —— 页面壳不该成为"点一下就开始真训练"的入口。
 * 写操作的 CSRF 取得方式见下方 csrfToken()，阶段 3 的前端会复用。
 */
(function () {
    'use strict';

    var csrfMeta = document.querySelector('meta[name="csrf-token"]');

    /** 读出服务端注入的 CSRF token（双提交 cookie 已由页面响应设置）。 */
    function csrfToken() {
        return csrfMeta ? csrfMeta.getAttribute('content') : '';
    }

    /** 带 CSRF 的写请求封装（阶段 3 前端复用同一契约）。 */
    function apiWrite(method, url, body) {
        return fetch(url, {
            method: method,
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken()
            },
            credentials: 'same-origin',
            body: body === undefined ? undefined : JSON.stringify(body)
        });
    }

    function apiGet(url) {
        return fetch(url, { credentials: 'same-origin' });
    }

    function el(tag, className, text) {
        var node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    }

    function badge(text, kind) {
        return el('span', 'badge badge--' + kind, text);
    }

    function healthKind(component) {
        var status = (component && component.status) || '';
        if (status === 'ok' || status === 'running') return 'ok';
        if (status === 'stopped') return 'dim';
        if (status === 'error' || status === 'degraded') return 'bad';
        return 'warn';
    }

    function renderHealth(payload) {
        var body = document.getElementById('health-body');
        if (!body) return;
        body.textContent = '';

        var overall = payload.status === 'healthy' ? 'ok'
            : payload.status === 'degraded' ? 'warn' : 'bad';
        var head = el('div', 'kv');
        head.appendChild(el('span', 'kv__k', '总体状态'));
        head.appendChild(badge(payload.status || 'unknown', overall));
        body.appendChild(head);

        var uptime = el('div', 'kv');
        uptime.appendChild(el('span', 'kv__k', '运行时长'));
        uptime.appendChild(el('span', null, (payload.uptime_seconds || 0) + ' s'));
        body.appendChild(uptime);

        var components = payload.components || {};
        Object.keys(components).forEach(function (name) {
            var comp = components[name] || {};
            var row = el('div', 'kv');
            row.appendChild(el('span', 'kv__k', name));
            if (name === 'capture' && comp.queue_size !== undefined) {
                row.appendChild(el('span', null, comp.status + ' / 队列 ' + comp.queue_size));
            } else if (name === 'ml_model') {
                row.appendChild(el('span', null,
                    'loaded=' + (comp.model_loaded ? 'yes' : 'no') + ' / 预测 ' + (comp.predict_count || 0)));
            } else if (name === 'sse') {
                row.appendChild(el('span', null, '订阅者 ' + (comp.subscribers || 0)));
            } else {
                row.appendChild(badge(comp.status || JSON.stringify(comp).slice(0, 40), healthKind(comp)));
            }
            body.appendChild(row);
        });
    }

    function renderTasks(payload) {
        var tbody = document.querySelector('#tasks-table tbody');
        if (!tbody) return;
        tbody.textContent = '';

        var tasks = payload.tasks || [];
        if (!tasks.length) {
            var empty = el('tr');
            var cell = el('td', 'muted', '未注册任何任务');
            cell.colSpan = 4;
            empty.appendChild(cell);
            tbody.appendChild(empty);
            return;
        }

        tasks.forEach(function (task) {
            var row = el('tr');
            row.appendChild(el('td', null, task.name || ''));
            row.appendChild(el('td', 'muted', task.kind || ''));
            var statusCell = el('td');
            var kind = task.status === 'running' ? 'ok'
                : task.status === 'error' ? 'bad' : 'dim';
            statusCell.appendChild(badge(task.status || 'unknown', kind));
            row.appendChild(statusCell);
            row.appendChild(el('td', 'muted', task.description || ''));
            tbody.appendChild(row);
        });
    }

    function fail(selector, message) {
        var node = document.querySelector(selector);
        if (node) {
            node.textContent = '';
            node.appendChild(el('p', 'muted', message));
        }
    }

    apiGet('/api/health')
        .then(function (r) { return r.json(); })
        .then(renderHealth)
        .catch(function () { fail('#health-body', '健康检查不可用（需登录或服务未就绪）'); });

    apiGet('/api/tasks')
        .then(function (r) { return r.json(); })
        .then(renderTasks)
        .catch(function () { fail('#tasks-table tbody', '任务列表不可用（需登录或服务未就绪）'); });

    window.SN = { apiGet: apiGet, apiWrite: apiWrite, csrfToken: csrfToken };
})();
