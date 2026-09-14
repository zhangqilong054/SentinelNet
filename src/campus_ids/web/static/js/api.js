/* ============================================================
 * SentinelNet — api.js: API 封装、Toast 通知、连接状态管理
 * 供 charts.js / alerts.js / controls.js 共享的基础模块
 * ============================================================ */
(function (SN) {
    'use strict';

    // ---------- DOM 辅助（安全渲染） ----------
    SN.$ = (id) => document.getElementById(id);
    SN.setText = (id, text) => { SN.$(id).textContent = text; };
    SN.el = (tag, className, text) => {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined && text !== null) node.textContent = text;
        return node;
    };
    SN.clearNode = (node) => { while (node.firstChild) node.removeChild(node.firstChild); };

    // ---------- 共享状态 ----------
    SN.paused = false;
    SN.connOnline = null;       // null=首次, true/false
    SN.bannerDanger = false;
    SN.REFRESH_MS = 2000;
    SN.SSE_RECONNECT_MS = 5000;

    // ---------- Toast 通知组件 ----------
    let _toastContainer = null;

    function _ensureToastContainer() {
        if (!_toastContainer) {
            _toastContainer = SN.el('div', 'toast-container');
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
    SN.showToast = function (message, type, duration) {
        type = type || 'info';
        duration = duration || 3000;
        const container = _ensureToastContainer();
        const toast = SN.el('div', 'toast toast-' + type, message);
        const closeBtn = SN.el('span', 'toast-close', '\u00d7');
        closeBtn.addEventListener('click', function () {
            toast.classList.add('toast-exit');
            setTimeout(function () { toast.remove(); }, 300);
        });
        toast.appendChild(closeBtn);
        container.appendChild(toast);
        setTimeout(function () {
            if (toast.parentNode) {
                toast.classList.add('toast-exit');
                setTimeout(function () { toast.remove(); }, 300);
            }
        }, duration);
    };

    // ---------- API 封装 + 拦截器 ----------
    SN.apiGet = async function (path) {
        var r = await fetch(path);
        if (r.status === 401) {
            SN.showToast('登录已过期，请重新登录', 'warning');
            setTimeout(function () { window.location.href = '/login'; }, 1500);
            throw new Error('Unauthorized');
        }
        if (r.status === 403) {
            SN.showToast('权限不足，操作被拒绝', 'error');
            throw new Error('Forbidden');
        }
        if (r.status >= 500) {
            SN.showToast('服务器错误，请稍后重试', 'error');
            throw new Error('GET ' + path + ' -> ' + r.status);
        }
        if (!r.ok) throw new Error('GET ' + path + ' -> ' + r.status);
        return r.json();
    };

    SN.apiPost = async function (path, body) {
        var r = await fetch(path, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: body === undefined ? undefined : JSON.stringify(body),
        });
        if (r.status === 401) {
            SN.showToast('登录已过期，请重新登录', 'warning');
            setTimeout(function () { window.location.href = '/login'; }, 1500);
            throw new Error('Unauthorized');
        }
        if (r.status === 403) {
            SN.showToast('权限不足，操作被拒绝', 'error');
            throw new Error('Forbidden');
        }
        if (r.status >= 500) {
            SN.showToast('服务器错误，请稍后重试', 'error');
            throw new Error('POST ' + path + ' -> ' + r.status);
        }
        if (!r.ok) throw new Error('POST ' + path + ' -> ' + r.status);
        return r.json();
    };

    // ---------- 连接状态 ----------
    SN.markConnection = function (ok) {
        SN.connOnline = ok;
        SN.renderConn();
        SN.renderBanner();
    };

    SN.renderConn = function () {
        var box = SN.$('connStatus');
        var text = SN.$('connText');
        box.classList.remove('online', 'offline', 'paused');
        if (SN.paused) {
            box.classList.add('paused');
            text.textContent = '已暂停';
        } else if (SN.sseConnected) {
            box.classList.add('online');
            text.textContent = 'SSE 实时推送';
        } else if (SN.connOnline === true) {
            box.classList.add('online');
            text.textContent = '轮询模式';
        } else if (SN.connOnline === false) {
            box.classList.add('offline');
            text.textContent = '连接中断，自动重连中…';
        } else {
            text.textContent = '连接中…';
        }
    };

    SN.renderBanner = function () {
        var banner = SN.$('globalBanner');
        banner.classList.remove('show', 'safe', 'danger', 'offline');
        if (SN.connOnline === false) {
            banner.textContent = '\u26a0\ufe0f 与检测服务连接中断，正在自动重连…';
            banner.classList.add('show', 'offline');
        } else if (SN.bannerDanger) {
            banner.textContent = '\ud83d\udea8 检测到异常流量！请查看告警页签获取详情';
            banner.classList.add('show', 'danger');
        } else if (SN.connOnline === true) {
            banner.textContent = '\u2713 系统运行正常，未检测到异常流量';
            banner.classList.add('show', 'safe');
        }
    };

    // SSE 连接状态（由 controls.js 管理）
    SN.sseConnected = false;

})(window.SN || (window.SN = {}));