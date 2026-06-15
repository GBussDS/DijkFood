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

let chatHistory = [];

// ============================================================
// TAB NAVIGATION
// ============================================================
document.querySelectorAll('.nav-item').forEach(item => {
    item.addEventListener('click', (e) => {
        e.preventDefault();
        const tab = item.dataset.tab;

        // Update nav
        document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
        item.classList.add('active');

        // Update content
        document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
        document.getElementById(`tab-${tab}`).classList.add('active');

        // Update title
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

    const health = await apiGet('/health');
    if (health && health.status === 'ok') {
        dot.classList.add('online');
        text.textContent = 'Sistema Online';
    } else {
        dot.classList.remove('online');
        text.textContent = 'Modo Demonstração';
    }
}

// ============================================================
// KPI DATA (real data with demo fallback)
// ============================================================
async function loadKPIs() {
    // Try dashboard-analytics service first
    const data = await dashboardGet('/api/dashboard/summary');

    if (data && (data.orders_today > 0 || data.active_orders > 0 || data.available_couriers > 0)) {
        animateKPI('kpi-orders-value', data.orders_today);
        animateKPI('kpi-active-value', data.active_orders);
        document.getElementById('kpi-time-value').textContent =
            data.avg_delivery_time > 0 ? data.avg_delivery_time.toFixed(1) : '—';
        animateKPI('kpi-couriers-value', data.available_couriers);
        return;
    }

    // Demo data fallback
    const demoData = {
        ordersToday: Math.floor(Math.random() * 500 + 200),
        activeOrders: Math.floor(Math.random() * 50 + 10),
        avgTime: (Math.random() * 20 + 15).toFixed(1),
        availableCouriers: Math.floor(Math.random() * 80 + 30),
    };

    animateKPI('kpi-orders-value', demoData.ordersToday);
    animateKPI('kpi-active-value', demoData.activeOrders);
    document.getElementById('kpi-time-value').textContent = demoData.avgTime;
    animateKPI('kpi-couriers-value', demoData.availableCouriers);
}

function animateKPI(elementId, targetValue) {
    const el = document.getElementById(elementId);
    const duration = 1000;
    const start = 0;
    const startTime = performance.now();

    function update(currentTime) {
        const elapsed = currentTime - startTime;
        const progress = Math.min(elapsed / duration, 1);
        const eased = 1 - Math.pow(1 - progress, 3); // ease-out cubic
        const current = Math.floor(start + (targetValue - start) * eased);
        el.textContent = current.toLocaleString('pt-BR');
        if (progress < 1) requestAnimationFrame(update);
    }

    requestAnimationFrame(update);
}

// ============================================================
// DEMO DATA GENERATORS (fallback)
// ============================================================
function demoOrdersPerHour() {
    return Array.from({length: 24}, (_, i) => {
        if (i >= 11 && i <= 14) return Math.floor(Math.random() * 30 + 40);
        if (i >= 18 && i <= 21) return Math.floor(Math.random() * 40 + 50);
        if (i >= 7 && i <= 9) return Math.floor(Math.random() * 15 + 10);
        return Math.floor(Math.random() * 8 + 2);
    });
}

function demoStatusTimes() {
    return {
        labels: ['PREPARING', 'READY_FOR_PICKUP', 'PICKED_UP', 'IN_TRANSIT'],
        data: [8.5, 3.2, 2.1, 18.7],
    };
}

function demoTopRestaurants() {
    const names = Array.from({length: 10}, (_, i) => `Restaurant_${i}`);
    const values = names.map(() => Math.floor(Math.random() * 80 + 20)).sort((a, b) => b - a);
    return { labels: names, data: values };
}

function demoDeliveryHistogram() {
    return {
        labels: ['5-10', '10-15', '15-20', '20-25', '25-30', '30-35', '35-40', '40-45', '45-50', '50+'],
        data: [5, 15, 35, 55, 42, 28, 18, 10, 5, 3],
    };
}

function demoDemandHeatmap() {
    const data = [];
    for (let d = 0; d < 7; d++) {
        for (let h = 0; h < 24; h++) {
            let intensity = 5;
            if (h >= 11 && h <= 14) intensity = 30 + Math.random() * 20;
            else if (h >= 18 && h <= 21) intensity = 40 + Math.random() * 25;
            else if (h >= 7 && h <= 9) intensity = 15 + Math.random() * 10;
            if (d >= 5) intensity *= 1.3;
            data.push({ x: h, y: d, r: Math.sqrt(intensity) * 2 });
        }
    }
    return data;
}

function demoRegionDistribution() {
    return {
        labels: ['Centro', 'Zona Sul', 'Zona Norte', 'Zona Leste', 'Zona Oeste'],
        data: [85, 65, 45, 55, 35],
    };
}

// ============================================================
// CHARTS
// ============================================================
let charts = {};

async function initCharts() {
    // Fetch all data in parallel (with null fallback)
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
        : demoOrdersPerHour();

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
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            plugins: { legend: { display: false } },
            scales: {
                y: { beginAtZero: true, grid: { color: 'rgba(255,255,255,0.03)' } },
                x: { grid: { display: false } }
            }
        }
    });

    // 2. Tempo Médio por Status
    const statusInfo = (statusData && statusData.labels && statusData.labels.length > 0)
        ? statusData
        : demoStatusTimes();

    charts.statusTimes = new Chart(document.getElementById('chart-status-times'), {
        type: 'bar',
        data: {
            labels: statusInfo.labels,
            datasets: [{
                label: 'Tempo médio (min)',
                data: statusInfo.data,
                backgroundColor: [
                    CHART_COLORS.warning,
                    CHART_COLORS.info,
                    CHART_COLORS.secondary,
                    CHART_COLORS.success,
                    CHART_COLORS.primary,
                    CHART_COLORS.danger,
                ],
                borderRadius: 4,
            }]
        },
        options: {
            indexAxis: 'y',
            responsive: true,
            maintainAspectRatio: true,
            plugins: { legend: { display: false } },
            scales: {
                x: { beginAtZero: true, grid: { color: 'rgba(255,255,255,0.03)' } },
                y: { grid: { display: false } }
            }
        }
    });

    // 3. Top Restaurantes
    const restInfo = (restaurantsData && restaurantsData.labels && restaurantsData.labels.length > 0)
        ? restaurantsData
        : demoTopRestaurants();

    charts.topRestaurants = new Chart(document.getElementById('chart-top-restaurants'), {
        type: 'bar',
        data: {
            labels: restInfo.labels,
            datasets: [{
                label: 'Pedidos',
                data: restInfo.data,
                backgroundColor: CHART_COLORS.palette,
                borderRadius: 4,
            }]
        },
        options: {
            indexAxis: 'y',
            responsive: true,
            maintainAspectRatio: true,
            plugins: { legend: { display: false } },
            scales: {
                x: { beginAtZero: true, grid: { color: 'rgba(255,255,255,0.03)' } },
                y: { grid: { display: false } }
            }
        }
    });

    // 4. Histograma de Tempo de Entrega
    const histInfo = (histogramData && histogramData.labels && histogramData.labels.length > 0)
        ? histogramData
        : demoDeliveryHistogram();

    charts.deliveryHistogram = new Chart(document.getElementById('chart-delivery-histogram'), {
        type: 'bar',
        data: {
            labels: histInfo.labels,
            datasets: [{
                label: 'Entregas',
                data: histInfo.data,
                backgroundColor: CHART_COLORS.secondaryFill,
                borderColor: CHART_COLORS.secondary,
                borderWidth: 1.5,
                borderRadius: 4,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            plugins: { legend: { display: false } },
            scales: {
                y: { beginAtZero: true, grid: { color: 'rgba(255,255,255,0.03)' } },
                x: { grid: { display: false }, title: { display: true, text: 'Tempo (min)' } }
            }
        }
    });

    // 5. Heatmap de Demanda (Analytics tab) — using scatter chart
    const days = ['Dom', 'Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb'];
    let heatmapPoints;
    if (heatmapData && heatmapData.data && heatmapData.data.length > 0) {
        heatmapPoints = heatmapData.data.map(p => ({
            x: p.x,
            y: p.y,
            r: Math.sqrt(p.v || 1) * 2,
        }));
    } else {
        heatmapPoints = demoDemandHeatmap();
    }

    charts.demandHeatmap = new Chart(document.getElementById('chart-demand-heatmap'), {
        type: 'bubble',
        data: {
            datasets: [{
                label: 'Demanda',
                data: heatmapPoints,
                backgroundColor: 'rgba(99, 102, 241, 0.4)',
                borderColor: 'rgba(99, 102, 241, 0.6)',
                borderWidth: 1,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            plugins: { legend: { display: false } },
            scales: {
                x: {
                    min: 0, max: 23,
                    title: { display: true, text: 'Hora do dia' },
                    grid: { color: 'rgba(255,255,255,0.03)' }
                },
                y: {
                    min: -0.5, max: 6.5,
                    ticks: { callback: (v) => days[Math.round(v)] || '' },
                    grid: { color: 'rgba(255,255,255,0.03)' }
                }
            }
        }
    });

    // 6. Pedidos por Região
    const regInfo = (regionData && regionData.labels && regionData.labels.length > 0)
        ? regionData
        : demoRegionDistribution();

    charts.regionDist = new Chart(document.getElementById('chart-region-dist'), {
        type: 'doughnut',
        data: {
            labels: regInfo.labels,
            datasets: [{
                data: regInfo.data,
                backgroundColor: CHART_COLORS.palette.slice(0, regInfo.labels.length),
                borderWidth: 0,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            plugins: {
                legend: { position: 'bottom', labels: { padding: 16 } }
            },
            cutout: '60%',
        }
    });

    // 7. Predição de Demanda (agora integrado com ml-inference)
    const nextHours = Array.from({length: 8}, (_, i) => {
        const d = new Date();
        d.setHours(d.getHours() + i + 1);
        return {
            label: `${d.getHours()}h`,
            hour: d.getHours(),
            day: d.getDay()
        };
    });

    let predictions = [];
    try {
        // Tentar obter predições reais do ml-inference para o centro de SP
        const promises = nextHours.map(h => 
            apiPost('/api/predictions/demand', {
                region_lat: -23.55,
                region_lon: -46.63,
                hour: h.hour,
                day_of_week: h.day
            })
        );
        const results = await Promise.all(promises);
        predictions = results.map(r => r ? r.predicted_orders_per_hour : Math.floor(Math.random() * 40 + 10));
    } catch (e) {
        console.warn("Falha ao obter predições reais, usando fallback");
        predictions = nextHours.map(() => Math.floor(Math.random() * 40 + 10));
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
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            plugins: { legend: { display: false } },
            scales: {
                y: { beginAtZero: true, grid: { color: 'rgba(255,255,255,0.03)' } },
                x: { grid: { display: false } }
            }
        }
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

    // Add user message to UI
    appendChatMessage(message, 'user');
    chatInput.value = '';
    chatSendBtn.disabled = true;

    // Add to history
    chatHistory.push({ role: 'user', content: message });

    // Show typing indicator
    const typingEl = appendChatMessage('...', 'bot', true);

    try {
        const response = await apiPost('/api/chat', {
            message: message,
            history: chatHistory.slice(-10), // últimos 10 turnos
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

// Suggestion chips
document.querySelectorAll('.suggestion-chip').forEach(chip => {
    chip.addEventListener('click', () => {
        const msg = chip.dataset.msg;
        chatInput.value = msg;
        sendChatMessage(msg);
    });
});

// ============================================================
// ORDERS TABLE
// ============================================================
async function loadOrders() {
    const tbody = document.getElementById('orders-tbody');
    const data = await apiGet('/api/orders?limit=20');
    const rows = [];

    if (data && Array.isArray(data) && data.length > 0) {
        // Dados reais do backend
        for (const order of data) {
            const statusClass = order.status.toLowerCase().replace(/_/g, '_');
            const time = order.estimated_time ? `${Math.round(order.estimated_time)} min` : '—';
            const date = order.created_at ? new Date(order.created_at).toLocaleTimeString('pt-BR') : '—';
            const customer = order.customer_name || `ID: ${order.id.slice(0, 8)}`;
            const restaurant = order.restaurant_name || 'Desconhecido';
            const courier = order.courier_name || '—';

            rows.push(`
                <tr>
                    <td><code>${order.id.slice(0, 8)}...</code></td>
                    <td><span class="status-badge ${statusClass}">${order.status}</span></td>
                    <td>${escapeHtml(customer)}</td>
                    <td>${escapeHtml(restaurant)}</td>
                    <td>${escapeHtml(courier)}</td>
                    <td>${time}</td>
                    <td>${date}</td>
                </tr>
            `);
        }
    } else {
        // Fallback demo
        const statuses = ['CONFIRMED', 'PREPARING', 'READY_FOR_PICKUP', 'PICKED_UP', 'IN_TRANSIT', 'DELIVERED'];
        for (let i = 0; i < 20; i++) {
            const status = statuses[Math.floor(Math.random() * statuses.length)];
            const statusClass = status.toLowerCase().replace(/_/g, '_');
            const time = (Math.random() * 30 + 10).toFixed(1);
            const date = new Date(Date.now() - Math.random() * 86400000);

            rows.push(`
                <tr>
                    <td><code>${crypto.randomUUID().slice(0, 8)}...</code></td>
                    <td><span class="status-badge ${statusClass}">${status}</span></td>
                    <td>Customer_${Math.floor(Math.random() * 50)}</td>
                    <td>Restaurant_${Math.floor(Math.random() * 20)}</td>
                    <td>Courier_${Math.floor(Math.random() * 150)}</td>
                    <td>${time} min</td>
                    <td>${date.toLocaleTimeString('pt-BR')}</td>
                </tr>
            `);
        }
    }

    tbody.innerHTML = rows.join('');
}

// ============================================================
// COURIERS GRID
// ============================================================
async function loadCouriers() {
    const grid = document.getElementById('couriers-grid');
    const data = await apiGet('/api/couriers');
    const cards = [];

    if (data && Array.isArray(data) && data.length > 0) {
        // Dados reais do backend
        for (const courier of data) {
            const vehicleEmoji = courier.vehicle_type === 'moto' ? '🏍️' : '🚲';
            cards.push(`
                <div class="courier-card">
                    <div class="courier-avatar">${vehicleEmoji}</div>
                    <div class="courier-info">
                        <div class="courier-name">${escapeHtml(courier.name)}</div>
                        <div class="courier-vehicle">${escapeHtml(courier.vehicle_type)}</div>
                    </div>
                    <span class="courier-status ${courier.status.toLowerCase()}">${courier.status}</span>
                </div>
            `);
        }
    } else {
        // Fallback demo
        for (let i = 0; i < 24; i++) {
            const status = Math.random() > 0.4 ? 'AVAILABLE' : 'BUSY';
            const vehicle = Math.random() > 0.5 ? 'moto' : 'bicicleta';
            const vehicleEmoji = vehicle === 'moto' ? '🏍️' : '🚲';

            cards.push(`
                <div class="courier-card">
                    <div class="courier-avatar">${vehicleEmoji}</div>
                    <div class="courier-info">
                        <div class="courier-name">Courier_${i}</div>
                        <div class="courier-vehicle">${vehicle}</div>
                    </div>
                    <span class="courier-status ${status.toLowerCase()}">${status}</span>
                </div>
            `);
        }
    }

    grid.innerHTML = cards.join('');
}

// ============================================================
// REFRESH
// ============================================================
document.getElementById('btn-refresh').addEventListener('click', async () => {
    loadKPIs();
    loadOrders();
    loadCouriers();
    loadAnomalies();

    // Refresh chart data
    const [ordersData, statusData, restaurantsData, histogramData] =
        await Promise.all([
            dashboardGet('/api/dashboard/orders-per-hour'),
            dashboardGet('/api/dashboard/status-times'),
            dashboardGet('/api/dashboard/top-restaurants'),
            dashboardGet('/api/dashboard/delivery-histogram'),
        ]);

    if (ordersData && ordersData.data && charts.ordersVolume) {
        charts.ordersVolume.data.datasets[0].data = ordersData.data;
        charts.ordersVolume.update();
    }
    if (statusData && statusData.labels && charts.statusTimes) {
        charts.statusTimes.data.labels = statusData.labels;
        charts.statusTimes.data.datasets[0].data = statusData.data;
        charts.statusTimes.update();
    }
    if (restaurantsData && restaurantsData.labels && charts.topRestaurants) {
        charts.topRestaurants.data.labels = restaurantsData.labels;
        charts.topRestaurants.data.datasets[0].data = restaurantsData.data;
        charts.topRestaurants.update();
    }
    if (histogramData && histogramData.labels && charts.deliveryHistogram) {
        charts.deliveryHistogram.data.labels = histogramData.labels;
        charts.deliveryHistogram.data.datasets[0].data = histogramData.data;
        charts.deliveryHistogram.update();
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

    // Auto-refresh every 30s
    setInterval(() => {
        checkSystemStatus();
        loadKPIs();
        loadAnomalies();
    }, 30000);
});
