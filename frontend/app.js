/**
 * DijkFood A2 — Dashboard Application Logic
 * Conecta ao backend (ALB) para dados em tempo real e renderiza charts.
 */

// ============================================================
// CONFIG
// ============================================================
// API_BASE é injetado pelo config.js (gerado pelo `make frontend` com a URL do ALB).
// Em dev local, config.js usa window.location.origin como fallback.
const API_BASE = window.API_BASE || window.location.origin;

// Dashboard Analytics API base (mesma origin em prod, porta diferente em dev)
const DASHBOARD_API = API_BASE;

const CHART_COLORS = {
    primary: 'rgba(99, 102, 241, 0.8)',
    primaryFill: 'rgba(99, 102, 241, 0.15)',
    secondary: 'rgba(167, 139, 250, 0.8)',
    secondaryFill: 'rgba(167, 139, 250, 0.15)',
    success: 'rgba(34, 197, 94, 0.8)',
    warning: 'rgba(245, 158, 11, 0.8)',
    danger: 'rgba(239, 68, 68, 0.8)',
    info: 'rgba(6, 182, 212, 0.8)',
    palette: [
        'rgba(99, 102, 241, 0.8)',
        'rgba(167, 139, 250, 0.8)',
        'rgba(34, 197, 94, 0.8)',
        'rgba(245, 158, 11, 0.8)',
        'rgba(6, 182, 212, 0.8)',
        'rgba(239, 68, 68, 0.8)',
        'rgba(236, 72, 153, 0.8)',
        'rgba(59, 130, 246, 0.8)',
        'rgba(16, 185, 129, 0.8)',
        'rgba(251, 146, 60, 0.8)',
    ],
};

// Chart.js default styling
Chart.defaults.color = '#8e8ea0';
Chart.defaults.borderColor = 'rgba(255,255,255,0.06)';
Chart.defaults.font.family = "'Inter', sans-serif";

// Plugin global: exibe "Sem dados disponíveis" em charts vazios
const emptyStatePlugin = {
    id: 'emptyState',
    afterDraw(chart) {
        const hasData = chart.data.datasets.some(
            d => d.data && d.data.some(v => (typeof v === 'object' ? v !== null : v > 0))
        );
        if (!hasData) {
            const { ctx, width, height } = chart;
            ctx.save();
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillStyle = '#5a5a6e';
            ctx.font = '13px Inter, sans-serif';
            ctx.fillText('Sem dados disponíveis', width / 2, height / 2);
            ctx.restore();
        }
    },
};
Chart.register(emptyStatePlugin);

let chatHistory = [];

// ============================================================
// TAB NAVIGATION
// ============================================================
document.querySelectorAll('.nav-item').forEach(item => {
    item.addEventListener('click', (e) => {
        e.preventDefault();
        const tab = item.dataset.tab;

        document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
        item.classList.add('active');

        document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
        document.getElementById(`tab-${tab}`).classList.add('active');

        const titles = {
            dashboard: 'Dashboard Operacional',
            orders: 'Gestão de Pedidos',
            couriers: 'Entregadores',
            analytics: 'Painel Analítico',
            chat: 'Assistente IA',
        };
        document.getElementById('page-title').textContent = titles[tab] || tab;
    });
});

// ============================================================
// CLOCK
// ============================================================
function updateClock() {
    const now = new Date();
    document.getElementById('clock').textContent = now.toLocaleTimeString('pt-BR');
}
setInterval(updateClock, 1000);
updateClock();

// ============================================================
// API HELPERS
// ============================================================
async function apiGet(path) {
    try {
        const resp = await fetch(`${API_BASE}${path}`);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        return await resp.json();
    } catch (e) {
        console.warn(`API GET ${path} failed:`, e);
        return null;
    }
}

async function dashboardGet(path) {
    try {
        const resp = await fetch(`${DASHBOARD_API}${path}`);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        return await resp.json();
    } catch (e) {
        console.warn(`Dashboard GET ${path} failed:`, e);
        return null;
    }
}

async function apiPost(path, body) {
    try {
        const resp = await fetch(`${API_BASE}${path}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        return await resp.json();
    } catch (e) {
        console.warn(`API POST ${path} failed:`, e);
        return null;
    }
}

// ============================================================
// SYSTEM STATUS
// ============================================================
async function checkSystemStatus() {
    const dot = document.getElementById('system-status-dot');
    const text = document.getElementById('system-status-text');

    const health = await dashboardGet('/api/dashboard/summary');
    if (health !== null) {
        dot.classList.add('online');
        text.textContent = 'Sistema Online';
    } else {
        dot.classList.remove('online');
        text.textContent = 'Sistema Offline';
    }
}

// ============================================================
// KPI DATA — apenas dados reais
// ============================================================
async function loadKPIs() {
    const data = await dashboardGet('/api/dashboard/summary');

    if (data) {
        const ordersEl = document.getElementById('kpi-orders-value');
        const activeEl = document.getElementById('kpi-active-value');
        const timeEl = document.getElementById('kpi-time-value');
        const couriersEl = document.getElementById('kpi-couriers-value');

        if (data.orders_today > 0) {
            animateKPI('kpi-orders-value', data.orders_today);
        } else {
            ordersEl.textContent = '0';
        }

        if (data.active_orders > 0) {
            animateKPI('kpi-active-value', data.active_orders);
        } else {
            activeEl.textContent = '0';
        }

        timeEl.textContent = data.avg_delivery_time > 0
            ? data.avg_delivery_time.toFixed(1)
            : '—';

        if (data.available_couriers > 0) {
            animateKPI('kpi-couriers-value', data.available_couriers);
        } else {
            couriersEl.textContent = '0';
        }
    } else {
        ['kpi-orders-value', 'kpi-active-value', 'kpi-couriers-value'].forEach(id => {
            document.getElementById(id).textContent = '—';
        });
        document.getElementById('kpi-time-value').textContent = '—';
    }
}

function animateKPI(elementId, targetValue) {
    const el = document.getElementById(elementId);
    const duration = 1000;
    const startTime = performance.now();

    function update(currentTime) {
        const elapsed = currentTime - startTime;
        const progress = Math.min(elapsed / duration, 1);
        const eased = 1 - Math.pow(1 - progress, 3);
        el.textContent = Math.floor(targetValue * eased).toLocaleString('pt-BR');
        if (progress < 1) requestAnimationFrame(update);
    }

    requestAnimationFrame(update);
}

// ============================================================
// CHARTS — apenas dados reais; vazios mostram "Sem dados"
// ============================================================
let charts = {};

async function initCharts() {
    const [ordersData, statusData, restaurantsData, histogramData, heatmapData, regionData] =
        await Promise.all([
            dashboardGet('/api/dashboard/orders-per-hour'),
            dashboardGet('/api/dashboard/status-times'),
            dashboardGet('/api/dashboard/top-restaurants'),
            dashboardGet('/api/dashboard/delivery-histogram'),
            dashboardGet('/api/dashboard/demand-heatmap'),
            dashboardGet('/api/dashboard/region-distribution'),
        ]);

    // 1. Volume de Pedidos por Hora
    const hours = Array.from({length: 24}, (_, i) => `${i}h`);
    const orderVolume = (ordersData && ordersData.data && ordersData.data.some(v => v > 0))
        ? ordersData.data
        : Array(24).fill(0);

    charts.ordersVolume = new Chart(document.getElementById('chart-orders-volume'), {
        type: 'bar',
        data: {
            labels: hours,
            datasets: [{
                label: 'Pedidos',
                data: orderVolume,
                backgroundColor: CHART_COLORS.primaryFill,
                borderColor: CHART_COLORS.primary,
                borderWidth: 1.5,
                borderRadius: 4,
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            plugins: { legend: { display: false } },
            scales: {
                y: { beginAtZero: true, grid: { color: 'rgba(255,255,255,0.03)' } },
                x: { grid: { display: false } },
            },
        },
    });

    // 2. Tempo Médio por Status
    const statusLabels = (statusData && statusData.labels && statusData.labels.length > 0)
        ? statusData.labels : [];
    const statusValues = (statusData && statusData.data && statusData.data.length > 0)
        ? statusData.data : [];

    charts.statusTimes = new Chart(document.getElementById('chart-status-times'), {
        type: 'bar',
        data: {
            labels: statusLabels,
            datasets: [{
                label: 'Tempo médio (min)',
                data: statusValues,
                backgroundColor: [
                    CHART_COLORS.warning,
                    CHART_COLORS.info,
                    CHART_COLORS.secondary,
                    CHART_COLORS.success,
                    CHART_COLORS.primary,
                    CHART_COLORS.danger,
                ],
                borderRadius: 4,
            }],
        },
        options: {
            indexAxis: 'y',
            responsive: true,
            maintainAspectRatio: true,
            plugins: { legend: { display: false } },
            scales: {
                x: { beginAtZero: true, grid: { color: 'rgba(255,255,255,0.03)' } },
                y: { grid: { display: false } },
            },
        },
    });

    // 3. Top Restaurantes
    const restLabels = (restaurantsData && restaurantsData.labels && restaurantsData.labels.length > 0)
        ? restaurantsData.labels : [];
    const restValues = (restaurantsData && restaurantsData.data && restaurantsData.data.length > 0)
        ? restaurantsData.data : [];

    charts.topRestaurants = new Chart(document.getElementById('chart-top-restaurants'), {
        type: 'bar',
        data: {
            labels: restLabels,
            datasets: [{
                label: 'Pedidos',
                data: restValues,
                backgroundColor: CHART_COLORS.palette,
                borderRadius: 4,
            }],
        },
        options: {
            indexAxis: 'y',
            responsive: true,
            maintainAspectRatio: true,
            plugins: { legend: { display: false } },
            scales: {
                x: { beginAtZero: true, grid: { color: 'rgba(255,255,255,0.03)' } },
                y: { grid: { display: false } },
            },
        },
    });

    // 4. Histograma de Tempo de Entrega
    const histLabels = (histogramData && histogramData.labels && histogramData.labels.length > 0)
        ? histogramData.labels : [];
    const histValues = (histogramData && histogramData.data && histogramData.data.length > 0)
        ? histogramData.data : [];

    charts.deliveryHistogram = new Chart(document.getElementById('chart-delivery-histogram'), {
        type: 'bar',
        data: {
            labels: histLabels,
            datasets: [{
                label: 'Entregas',
                data: histValues,
                backgroundColor: CHART_COLORS.secondaryFill,
                borderColor: CHART_COLORS.secondary,
                borderWidth: 1.5,
                borderRadius: 4,
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            plugins: { legend: { display: false } },
            scales: {
                y: { beginAtZero: true, grid: { color: 'rgba(255,255,255,0.03)' } },
                x: { grid: { display: false }, title: { display: true, text: 'Tempo (min)' } },
            },
        },
    });

    // 5. Heatmap de Demanda (aba Analytics)
    const days = ['Dom', 'Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb'];
    const heatmapPoints = (heatmapData && heatmapData.data && heatmapData.data.length > 0)
        ? heatmapData.data.map(p => ({ x: p.x, y: p.y, r: Math.sqrt(p.v || 1) * 2 }))
        : [];

    charts.demandHeatmap = new Chart(document.getElementById('chart-demand-heatmap'), {
        type: 'bubble',
        data: {
            datasets: [{
                label: 'Demanda',
                data: heatmapPoints,
                backgroundColor: 'rgba(99, 102, 241, 0.4)',
                borderColor: 'rgba(99, 102, 241, 0.6)',
                borderWidth: 1,
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            plugins: { legend: { display: false } },
            scales: {
                x: {
                    min: 0, max: 23,
                    title: { display: true, text: 'Hora do dia' },
                    grid: { color: 'rgba(255,255,255,0.03)' },
                },
                y: {
                    min: -0.5, max: 6.5,
                    ticks: { callback: (v) => days[Math.round(v)] || '' },
                    grid: { color: 'rgba(255,255,255,0.03)' },
                },
            },
        },
    });

    // 6. Pedidos por Região
    const regLabels = (regionData && regionData.labels && regionData.labels.length > 0)
        ? regionData.labels : [];
    const regValues = (regionData && regionData.data && regionData.data.length > 0)
        ? regionData.data : [];

    charts.regionDist = new Chart(document.getElementById('chart-region-dist'), {
        type: 'doughnut',
        data: {
            labels: regLabels,
            datasets: [{
                data: regValues,
                backgroundColor: CHART_COLORS.palette.slice(0, Math.max(regLabels.length, 1)),
                borderWidth: 0,
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            plugins: { legend: { position: 'bottom', labels: { padding: 16 } } },
            cutout: '60%',
        },
    });

    // 7. Predição de Demanda — apenas dados reais do ml-inference
    const nextHours = Array.from({length: 8}, (_, i) => {
        const d = new Date();
        d.setHours(d.getHours() + i + 1);
        return { label: `${d.getHours()}h`, hour: d.getHours(), day: d.getDay() };
    });

    let predictions = Array(8).fill(0);
    try {
        const results = await Promise.all(
            nextHours.map(h => apiPost('/api/predictions/demand', {
                region_lat: -23.55,
                region_lon: -46.63,
                hour: h.hour,
                day_of_week: h.day,
            }))
        );
        if (results.some(r => r && r.predicted_orders_per_hour > 0)) {
            predictions = results.map(r => (r ? r.predicted_orders_per_hour : 0));
        }
    } catch (e) {
        console.warn('ML Inference indisponível:', e);
    }

    charts.demandPrediction = new Chart(document.getElementById('chart-demand-prediction'), {
        type: 'line',
        data: {
            labels: nextHours.map(h => h.label),
            datasets: [{
                label: 'Pedidos previstos/hora',
                data: predictions,
                borderColor: CHART_COLORS.primary,
                backgroundColor: CHART_COLORS.primaryFill,
                fill: true,
                tension: 0.4,
                pointRadius: 4,
                pointBackgroundColor: CHART_COLORS.primary,
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            plugins: { legend: { display: false } },
            scales: {
                y: { beginAtZero: true, grid: { color: 'rgba(255,255,255,0.03)' } },
                x: { grid: { display: false } },
            },
        },
    });
}

// ============================================================
// ANOMALIES
// ============================================================
async function loadAnomalies() {
    const data = await dashboardGet('/api/dashboard/anomalies');
    const container = document.getElementById('anomalies-list');

    if (data && data.anomalies && data.anomalies.length > 0) {
        container.innerHTML = data.anomalies.map(a => {
            const severityClass = a.severity === 'high' ? 'anomaly-high'
                : a.severity === 'medium' ? 'anomaly-medium'
                : 'anomaly-low';
            return `
                <div class="anomaly-item ${severityClass}">
                    <span class="anomaly-type">${escapeHtml(a.type)}</span>
                    <span class="anomaly-desc">${escapeHtml(a.description)}</span>
                    <span class="anomaly-time">${a.detected_at || ''}</span>
                </div>
            `;
        }).join('');
    } else {
        container.innerHTML = '<div class="anomaly-empty">Nenhuma anomalia detectada</div>';
    }
}

// ============================================================
// CHAT
// ============================================================
const chatInput = document.getElementById('chat-input');
const chatSendBtn = document.getElementById('chat-send');
const chatMessages = document.getElementById('chat-messages');

async function sendChatMessage(message) {
    if (!message.trim()) return;

    appendChatMessage(message, 'user');
    chatInput.value = '';
    chatSendBtn.disabled = true;

    chatHistory.push({ role: 'user', content: message });

    const typingEl = appendChatMessage('...', 'bot', true);

    try {
        const response = await apiPost('/api/chat', {
            message: message,
            history: chatHistory.slice(-10),
        });

        typingEl.remove();

        const botResponse = response?.response || 'Desculpe, não consegui processar sua pergunta. Verifique se o backend está online.';
        appendChatMessage(botResponse, 'bot');
        chatHistory.push({ role: 'assistant', content: botResponse });

    } catch (e) {
        typingEl.remove();
        appendChatMessage('Erro de conexão com o servidor. Verifique se o backend está rodando.', 'bot');
    }

    chatSendBtn.disabled = false;
    chatInput.focus();
}

function appendChatMessage(content, type, isTyping = false) {
    const div = document.createElement('div');
    div.className = `chat-message ${type}`;

    const avatar = type === 'bot' ? '🤖' : '👤';

    div.innerHTML = `
        <div class="message-avatar">${avatar}</div>
        <div class="message-content">
            <p>${isTyping ? '<span class="loading-spinner"></span>' : escapeHtml(content)}</p>
        </div>
    `;

    chatMessages.appendChild(div);
    chatMessages.scrollTop = chatMessages.scrollHeight;
    return div;
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

chatSendBtn.addEventListener('click', () => sendChatMessage(chatInput.value));
chatInput.addEventListener('keypress', (e) => {
    if (e.key === 'Enter') sendChatMessage(chatInput.value);
});

// ============================================================
// ORDERS FILTER / SEARCH
// ============================================================
document.getElementById('order-filter').addEventListener('change', loadOrders);

let _searchDebounce = null;
document.getElementById('order-search').addEventListener('input', () => {
    clearTimeout(_searchDebounce);
    _searchDebounce = setTimeout(loadOrders, 350);
});

document.querySelectorAll('.suggestion-chip').forEach(chip => {
    chip.addEventListener('click', () => {
        const msg = chip.dataset.msg;
        chatInput.value = msg;
        sendChatMessage(msg);
    });
});

// ============================================================
// ORDERS TABLE — apenas dados reais
// ============================================================
async function loadOrders() {
    const tbody = document.getElementById('orders-tbody');
    const statusFilter = document.getElementById('order-filter').value;
    const searchTerm = document.getElementById('order-search').value.trim();

    let path = '/api/orders?limit=50';
    if (statusFilter) path += `&status=${encodeURIComponent(statusFilter)}`;
    if (searchTerm) path += `&search=${encodeURIComponent(searchTerm)}`;

    const data = await apiGet(path);

    if (data && Array.isArray(data) && data.length > 0) {
        tbody.innerHTML = data.map(order => {
            const statusClass = order.status.toLowerCase().replace(/_/g, '_');
            const time = order.estimated_time ? `${Math.round(order.estimated_time)} min` : '—';
            const date = order.created_at ? new Date(order.created_at).toLocaleTimeString('pt-BR') : '—';
            const customer = order.customer_name || `ID: ${order.id.slice(0, 8)}`;
            const restaurant = order.restaurant_name || 'Desconhecido';
            const courier = order.courier_name || '—';

            return `
                <tr>
                    <td><code>${order.id.slice(0, 8)}...</code></td>
                    <td><span class="status-badge ${statusClass}">${order.status}</span></td>
                    <td>${escapeHtml(customer)}</td>
                    <td>${escapeHtml(restaurant)}</td>
                    <td>${escapeHtml(courier)}</td>
                    <td>${time}</td>
                    <td>${date}</td>
                </tr>
            `;
        }).join('');
    } else {
        tbody.innerHTML = `
            <tr>
                <td colspan="7" style="text-align:center; padding:40px; color:var(--text-muted)">
                    Nenhum pedido encontrado. Execute o simulador ou crie pedidos via API.
                </td>
            </tr>
        `;
    }
}

// ============================================================
// COURIERS GRID — apenas dados reais
// ============================================================
async function loadCouriers() {
    const grid = document.getElementById('couriers-grid');
    const data = await apiGet('/api/couriers');

    if (data && Array.isArray(data) && data.length > 0) {
        grid.innerHTML = data.map(courier => {
            const v = (courier.vehicle_type || '').toLowerCase();
            const vehicleEmoji = v === 'moto' ? '🏍️' : v === 'carro' ? '🚗' : '🚲';
            return `
                <div class="courier-card">
                    <div class="courier-avatar">${vehicleEmoji}</div>
                    <div class="courier-info">
                        <div class="courier-name">${escapeHtml(courier.name)}</div>
                        <div class="courier-vehicle">${escapeHtml(courier.vehicle_type)}</div>
                    </div>
                    <span class="courier-status ${courier.status.toLowerCase()}">${courier.status}</span>
                </div>
            `;
        }).join('');
    } else {
        grid.innerHTML = `
            <p class="empty-state">
                Nenhum entregador cadastrado. Execute o simulador ou registre entregadores via API.
            </p>
        `;
    }
}

// ============================================================
// REFRESH
// ============================================================
document.getElementById('btn-refresh').addEventListener('click', async () => {
    loadKPIs();
    loadOrders();
    loadCouriers();
    loadAnomalies();

    const [ordersData, statusData, restaurantsData, histogramData, heatmapData, regionData] =
        await Promise.all([
            dashboardGet('/api/dashboard/orders-per-hour'),
            dashboardGet('/api/dashboard/status-times'),
            dashboardGet('/api/dashboard/top-restaurants'),
            dashboardGet('/api/dashboard/delivery-histogram'),
            dashboardGet('/api/dashboard/demand-heatmap'),
            dashboardGet('/api/dashboard/region-distribution'),
        ]);

    if (charts.ordersVolume) {
        charts.ordersVolume.data.datasets[0].data =
            (ordersData && ordersData.data) ? ordersData.data : Array(24).fill(0);
        charts.ordersVolume.update();
    }
    if (charts.statusTimes && statusData && statusData.labels) {
        charts.statusTimes.data.labels = statusData.labels;
        charts.statusTimes.data.datasets[0].data = statusData.data;
        charts.statusTimes.update();
    }
    if (charts.topRestaurants && restaurantsData && restaurantsData.labels) {
        charts.topRestaurants.data.labels = restaurantsData.labels;
        charts.topRestaurants.data.datasets[0].data = restaurantsData.data;
        charts.topRestaurants.update();
    }
    if (charts.deliveryHistogram && histogramData && histogramData.labels) {
        charts.deliveryHistogram.data.labels = histogramData.labels;
        charts.deliveryHistogram.data.datasets[0].data = histogramData.data;
        charts.deliveryHistogram.update();
    }
    if (charts.demandHeatmap && heatmapData && heatmapData.data) {
        charts.demandHeatmap.data.datasets[0].data =
            heatmapData.data.map(p => ({ x: p.x, y: p.y, r: Math.sqrt(p.v || 1) * 2 }));
        charts.demandHeatmap.update();
    }
    if (charts.regionDist && regionData && regionData.labels) {
        charts.regionDist.data.labels = regionData.labels;
        charts.regionDist.data.datasets[0].data = regionData.data;
        charts.regionDist.update();
    }
});

// ============================================================
// INIT
// ============================================================
document.addEventListener('DOMContentLoaded', () => {
    checkSystemStatus();
    loadKPIs();
    initCharts();
    loadOrders();
    loadCouriers();
    loadAnomalies();

    // Auto-refresh a cada 30s
    setInterval(() => {
        checkSystemStatus();
        loadKPIs();
        loadAnomalies();
    }, 30000);
});
