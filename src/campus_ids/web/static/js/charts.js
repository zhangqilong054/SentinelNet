/* ============================================================
 * SentinelNet — charts.js: 图表初始化与更新逻辑
 * 依赖: api.js (SN 命名空间), Chart.js
 * ============================================================ */
(function (SN) {
    'use strict';

    var ATTACK_PALETTE = ['#ff4d4d', '#ff8c00', '#ffd700', '#9c27b0',
                          '#2196f3', '#e91e63', '#00bcd4', '#ff5722'];
    var OTHER_COLOR = '#607d8b';
    var MAX_PIE_TYPES = 8;

    SN.ATTACK_PALETTE = ATTACK_PALETTE;
    SN.OTHER_COLOR = OTHER_COLOR;
    SN.MAX_PIE_TYPES = MAX_PIE_TYPES;

    var qpsChart, attackPieChart;

    /** 初始化 QPS 折线图和攻击类型饼图 */
    SN.initCharts = function () {
        qpsChart = new Chart(SN.$('qpsChart').getContext('2d'), {
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
                animation: { duration: 250 },
                scales: {
                    x: { display: false },
                    y: { beginAtZero: true, grid: { color: 'rgba(255,255,255,0.08)' }, ticks: { color: '#9a9ab0' } },
                },
                plugins: { legend: { labels: { color: '#9a9ab0', boxWidth: 12 } } },
            },
        });

        attackPieChart = new Chart(SN.$('attackPieChart').getContext('2d'), {
            type: 'doughnut',
            data: { labels: ['Normal'], datasets: [{ data: [1], backgroundColor: ['#4caf50'], borderColor: 'rgba(0,0,0,0.3)', borderWidth: 1 }] },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
            },
        });

        SN._qpsChart = qpsChart;
        SN._attackPieChart = attackPieChart;
    };

    /** 更新 QPS 折线图（流量数据到达时调用） */
    SN.updateQpsChart = function (data, thresholds) {
        var label = (data.timestamp || '').split(' ')[1] || data.timestamp;
        var ds = qpsChart.data.datasets;
        qpsChart.data.labels.push(label);
        ds[0].data.push(data.qps);
        ds[1].data.push(thresholds.ddos_threshold);
        if (qpsChart.data.labels.length > 30) {
            qpsChart.data.labels.shift();
            ds[0].data.shift();
            ds[1].data.shift();
        }
        qpsChart.update();
    };

    /** 更新攻击类型饼图（双引擎统计到达时调用） */
    SN.updateAttackPie = function (dist) {
        var entries = Object.entries(dist).sort(function (a, b) { return b[1] - a[1]; });
        if (entries.length === 0) return;

        var shown = entries.slice(0, MAX_PIE_TYPES).map(function (e) { return { name: e[0], count: e[1] }; });
        var rest = entries.slice(MAX_PIE_TYPES);
        if (rest.length > 0) {
            shown.push({
                name: '其他（' + rest.length + ' 类）',
                count: rest.reduce(function (sum, e) { return sum + e[1]; }, 0),
            });
        }
        var labels = shown.map(function (x) { return x.name; });
        var colors = labels.map(function (l, i) {
            if (l === 'Normal') return '#4caf50';
            if (l.indexOf('其他') === 0) return OTHER_COLOR;
            return ATTACK_PALETTE[i % ATTACK_PALETTE.length];
        });
        attackPieChart.data.labels = labels;
        attackPieChart.data.datasets[0].data = shown.map(function (x) { return x.count; });
        attackPieChart.data.datasets[0].backgroundColor = colors;
        attackPieChart.update();

        // 更新图例
        var legend = SN.$('attackTypeList');
        SN.clearNode(legend);
        shown.forEach(function (x, i) {
            var row = SN.el('div', 'legend-item');
            row.title = x.name + ': ' + x.count;
            var dot = SN.el('span', 'legend-dot');
            dot.style.background = colors[i];
            row.appendChild(dot);
            row.appendChild(SN.el('span', 'legend-name', x.name));
            row.appendChild(SN.el('b', 'legend-count', String(x.count)));
            legend.appendChild(row);
        });
    };

})(window.SN || (window.SN = {}));