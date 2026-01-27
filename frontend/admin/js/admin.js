/**
 * =============================================================================
 * KOMPITE ADMIN PANEL - JavaScript
 * =============================================================================
 * Panel de administración con Socket.IO para actualizaciones en tiempo real.
 * Gestión de depósitos, usuarios, partidas y webhooks.
 * =============================================================================
 */

// =============================================================================
// CONFIGURACIÓN
// =============================================================================
const CONFIG = {
    API_URL: window.location.origin + '/api/v1',
    WS_URL: window.location.origin,
    REFRESH_INTERVAL: 30000, // 30 segundos
};

// =============================================================================
// ESTADO GLOBAL
// =============================================================================
const state = {
    socket: null,
    currentView: 'dashboard',
    deposits: [],
    users: [],
    matches: [],
    transactions: [],
    selectedDeposit: null,
    isConnected: false,
    adminToken: localStorage.getItem('admin_token') || null,
};

// =============================================================================
// UTILIDADES
// =============================================================================
const Utils = {
    formatCurrency(amount, currency = 'LKC') {
        const num = parseFloat(amount);
        if (currency === 'PEN') {
            return `S/ ${num.toFixed(2)}`;
        }
        return `${num.toFixed(4)} LKC`;
    },

    formatDate(dateStr) {
        const date = new Date(dateStr);
        return date.toLocaleDateString('es-PE', {
            day: '2-digit',
            month: '2-digit',
            year: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
        });
    },

    formatShortDate(dateStr) {
        const date = new Date(dateStr);
        return date.toLocaleDateString('es-PE', {
            day: '2-digit',
            month: 'short',
            hour: '2-digit',
            minute: '2-digit',
        });
    },

    truncateHash(hash, length = 8) {
        if (!hash) return '-';
        return `${hash.substring(0, length)}...${hash.substring(hash.length - 4)}`;
    },

    getStatusBadge(status) {
        const badges = {
            PENDING: '<span class="badge badge-pending">Pendiente</span>',
            APPROVED: '<span class="badge badge-approved">Aprobado</span>',
            REJECTED: '<span class="badge badge-rejected">Rechazado</span>',
            GREEN: '<span class="badge badge-green">🟢 Verde</span>',
            YELLOW: '<span class="badge badge-yellow">🟡 Amarillo</span>',
            RED: '<span class="badge badge-red">🔴 Rojo</span>',
        };
        return badges[status] || `<span class="badge">${status}</span>`;
    },

    async apiRequest(endpoint, options = {}) {
        const headers = {
            'Content-Type': 'application/json',
            ...options.headers,
        };

        if (state.adminToken) {
            headers['Authorization'] = `Bearer ${state.adminToken}`;
        }

        try {
            const response = await fetch(`${CONFIG.API_URL}${endpoint}`, {
                ...options,
                headers,
            });

            if (!response.ok) {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }

            return await response.json();
        } catch (error) {
            console.error('API Error:', error);
            Toast.error('Error de API', error.message);
            throw error;
        }
    },
};

// =============================================================================
// TOAST NOTIFICATIONS
// =============================================================================
const Toast = {
    container: null,

    init() {
        this.container = document.getElementById('toast-container');
    },

    show(type, title, message) {
        const icons = {
            success: '✅',
            error: '❌',
            warning: '⚠️',
            info: 'ℹ️',
        };

        const toast = document.createElement('div');
        toast.className = `toast ${type}`;
        toast.innerHTML = `
            <span class="toast-icon">${icons[type]}</span>
            <div class="toast-content">
                <div class="toast-title">${title}</div>
                <div class="toast-message">${message}</div>
            </div>
        `;

        this.container.appendChild(toast);

        setTimeout(() => {
            toast.style.animation = 'slideIn 0.3s ease reverse';
            setTimeout(() => toast.remove(), 300);
        }, 4000);
    },

    success(title, message) { this.show('success', title, message); },
    error(title, message) { this.show('error', title, message); },
    warning(title, message) { this.show('warning', title, message); },
    info(title, message) { this.show('info', title, message); },
};

// =============================================================================
// SOCKET.IO CONNECTION
// =============================================================================
const SocketManager = {
    init() {
        if (typeof io === 'undefined') {
            console.warn('Socket.IO not loaded');
            return;
        }

        state.socket = io(CONFIG.WS_URL, {
            path: '/socket.io/',
            transports: ['websocket', 'polling'],
            auth: {
                token: state.adminToken,
                role: 'admin',
            },
        });

        this.setupEventListeners();
    },

    setupEventListeners() {
        state.socket.on('connect', () => {
            state.isConnected = true;
            this.updateConnectionStatus(true);
            console.log('🔌 Admin conectado al WebSocket');
            
            // Suscribirse a eventos admin
            state.socket.emit('admin:subscribe');
        });

        state.socket.on('disconnect', () => {
            state.isConnected = false;
            this.updateConnectionStatus(false);
            console.log('🔌 Desconectado del WebSocket');
        });

        state.socket.on('connect_error', (error) => {
            console.error('Socket error:', error);
            this.updateConnectionStatus(false);
        });

        // Eventos de tiempo real
        state.socket.on('admin:new_deposit', (data) => {
            Toast.info('Nuevo Depósito', `${data.user_name} solicitó ${Utils.formatCurrency(data.amount, 'PEN')}`);
            this.updatePendingCount();
            if (state.currentView === 'deposits') {
                DepositsView.refresh();
            }
        });

        state.socket.on('admin:deposit_approved', (data) => {
            Toast.success('Depósito Aprobado', `ID: ${Utils.truncateHash(data.deposit_id)}`);
            if (state.currentView === 'deposits') {
                DepositsView.refresh();
            }
        });

        state.socket.on('admin:stats_update', (data) => {
            this.updateRealtimeStats(data);
        });
    },

    updateConnectionStatus(connected) {
        const statusEl = document.getElementById('connection-status');
        const textEl = statusEl.querySelector('.status-text');
        
        statusEl.classList.remove('connected', 'disconnected');
        statusEl.classList.add(connected ? 'connected' : 'disconnected');
        textEl.textContent = connected ? 'Conectado' : 'Desconectado';
    },

    updatePendingCount() {
        const badge = document.querySelector('.pending-count');
        const count = state.deposits.filter(d => d.status === 'PENDING').length;
        badge.textContent = count > 0 ? count : '';
    },

    updateRealtimeStats(data) {
        if (data.online_users !== undefined) {
            document.getElementById('online-users').textContent = data.online_users;
        }
        if (data.active_matches !== undefined) {
            document.getElementById('active-matches').textContent = data.active_matches;
        }
    },
};

// =============================================================================
// NAVEGACIÓN
// =============================================================================
const Navigation = {
    init() {
        // Navegación del sidebar
        document.querySelectorAll('.nav-item').forEach(item => {
            item.addEventListener('click', (e) => {
                e.preventDefault();
                const view = item.dataset.view;
                if (view) this.showView(view);
            });
        });

        // Links en cards
        document.querySelectorAll('.card-link').forEach(link => {
            link.addEventListener('click', (e) => {
                e.preventDefault();
                const view = link.dataset.view;
                if (view) this.showView(view);
            });
        });
    },

    showView(viewName) {
        // Actualizar estado
        state.currentView = viewName;

        // Ocultar todas las vistas
        document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
        
        // Mostrar vista seleccionada
        const view = document.getElementById(`view-${viewName}`);
        if (view) {
            view.classList.add('active');
        }

        // Actualizar navegación
        document.querySelectorAll('.nav-item').forEach(item => {
            item.classList.toggle('active', item.dataset.view === viewName);
        });

        // Actualizar título
        const titles = {
            dashboard: 'Dashboard',
            deposits: 'Gestión de Depósitos',
            users: 'Usuarios',
            matches: 'Partidas',
            transactions: 'Ledger de Transacciones',
            webhooks: 'Configuración de Webhooks',
        };
        document.getElementById('view-title').textContent = titles[viewName] || viewName;

        // Cargar datos de la vista
        this.loadViewData(viewName);
    },

    loadViewData(viewName) {
        switch (viewName) {
            case 'dashboard':
                DashboardView.refresh();
                break;
            case 'deposits':
                DepositsView.refresh();
                break;
            case 'users':
                UsersView.refresh();
                break;
            case 'matches':
                MatchesView.refresh();
                break;
            case 'transactions':
                TransactionsView.refresh();
                break;
            case 'webhooks':
                WebhooksView.refresh();
                break;
        }
    },
};

// =============================================================================
// VISTA: DASHBOARD
// =============================================================================
const DashboardView = {
    async refresh() {
        try {
            const stats = await Utils.apiRequest('/admin/stats');
            
            document.getElementById('total-lkoins').textContent = 
                Utils.formatCurrency(stats.total_lkoins_circulation || 0);
            document.getElementById('pending-deposits').textContent = 
                stats.pending_deposits || 0;
            document.getElementById('today-approved').textContent = 
                stats.today_approved || 0;
            document.getElementById('flagged-users').textContent = 
                stats.flagged_users || 0;

            // Actualizar tabla de depósitos recientes
            this.updateRecentDeposits(stats.recent_deposits || []);
            
            // Actualizar salud del sistema
            this.updateSystemHealth(stats.system_health || {});
        } catch (error) {
            console.error('Error loading dashboard:', error);
        }
    },

    updateRecentDeposits(deposits) {
        const tbody = document.getElementById('recent-deposits-table');
        
        if (deposits.length === 0) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="4" style="text-align: center; color: var(--text-muted);">
                        No hay depósitos recientes
                    </td>
                </tr>
            `;
            return;
        }

        tbody.innerHTML = deposits.map(d => `
            <tr>
                <td>${d.user_name || d.user_phone}</td>
                <td style="color: var(--gold);">${Utils.formatCurrency(d.amount_pen, 'PEN')}</td>
                <td>${Utils.getStatusBadge(d.status)}</td>
                <td>${Utils.formatShortDate(d.created_at)}</td>
            </tr>
        `).join('');
    },

    updateSystemHealth(health) {
        const services = ['postgres', 'redis', 'websocket', 'celery'];
        services.forEach(service => {
            const el = document.getElementById(`health-${service}`);
            if (el) {
                const status = health[service] || 'unknown';
                el.className = `health-status ${status === 'healthy' ? 'healthy' : 'error'}`;
            }
        });
    },
};

// =============================================================================
// VISTA: DEPÓSITOS
// =============================================================================
const DepositsView = {
    init() {
        // Filtros
        document.getElementById('filter-deposit-status').addEventListener('change', () => this.refresh());
        document.getElementById('filter-deposit-date').addEventListener('change', () => this.refresh());
        document.getElementById('btn-refresh-deposits').addEventListener('click', () => this.refresh());
    },

    async refresh() {
        const status = document.getElementById('filter-deposit-status').value;
        const date = document.getElementById('filter-deposit-date').value;
        
        try {
            const params = new URLSearchParams();
            if (status !== 'ALL') params.append('status', status);
            if (date) params.append('date', date);
            
            const response = await Utils.apiRequest(`/admin/deposits?${params}`);
            state.deposits = response.deposits || [];
            this.render();
        } catch (error) {
            console.error('Error loading deposits:', error);
        }
    },

    render() {
        const container = document.getElementById('deposits-list');
        
        if (state.deposits.length === 0) {
            container.innerHTML = `
                <div class="card" style="text-align: center; padding: 3rem;">
                    <p style="color: var(--text-muted);">No hay depósitos para mostrar</p>
                </div>
            `;
            return;
        }

        container.innerHTML = state.deposits.map(d => `
            <div class="deposit-card ${d.status.toLowerCase()}" data-deposit-id="${d.id}">
                <img class="deposit-thumbnail" 
                     src="${d.evidence_url}" 
                     alt="Evidencia"
                     onerror="this.src='data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22><rect fill=%22%23252525%22 width=%22100%22 height=%2270%22/><text x=%2250%%22 y=%2250%%22 fill=%22%23666%22 text-anchor=%22middle%22 dy=%22.3em%22>No img</text></svg>'">
                <div class="deposit-info-preview">
                    <span class="deposit-user-name">${d.user_name || d.user_phone}</span>
                    <span class="deposit-amount-preview">${Utils.formatCurrency(d.amount_pen, 'PEN')}</span>
                    <span class="deposit-reference-preview">${d.transaction_reference}</span>
                </div>
                <div class="deposit-meta">
                    ${Utils.getStatusBadge(d.status)}
                    <span class="deposit-date-preview">${Utils.formatShortDate(d.created_at)}</span>
                </div>
            </div>
        `).join('');

        // Agregar event listeners
        container.querySelectorAll('.deposit-card').forEach(card => {
            card.addEventListener('click', () => {
                const depositId = card.dataset.depositId;
                const deposit = state.deposits.find(d => d.id === depositId);
                if (deposit) this.openDetailModal(deposit);
            });
        });

        // Actualizar badge de pendientes
        SocketManager.updatePendingCount();
    },

    openDetailModal(deposit) {
        state.selectedDeposit = deposit;
        
        document.getElementById('deposit-evidence-img').src = deposit.evidence_url;
        document.getElementById('deposit-user').textContent = deposit.user_name || deposit.user_phone;
        document.getElementById('deposit-phone').textContent = deposit.user_phone;
        document.getElementById('deposit-amount').textContent = Utils.formatCurrency(deposit.amount_pen, 'PEN');
        document.getElementById('deposit-reference').textContent = deposit.transaction_reference;
        document.getElementById('deposit-date').textContent = Utils.formatDate(deposit.created_at);
        document.getElementById('deposit-current-balance').textContent = Utils.formatCurrency(deposit.user_balance || 0);
        document.getElementById('deposit-balance-hash').textContent = deposit.user_balance_hash || '-';

        // Mostrar/ocultar botones según estado
        const approveBtn = document.getElementById('btn-approve-deposit');
        const rejectBtn = document.getElementById('btn-reject-deposit');
        const rejectionInput = document.getElementById('rejection-input');
        
        if (deposit.status === 'PENDING') {
            approveBtn.style.display = 'inline-flex';
            rejectBtn.style.display = 'inline-flex';
            rejectionInput.style.display = 'none';
        } else {
            approveBtn.style.display = 'none';
            rejectBtn.style.display = 'none';
        }

        Modal.open('modal-deposit-detail');
    },

    async approveDeposit() {
        if (!state.selectedDeposit) return;

        try {
            await Utils.apiRequest(`/admin/deposits/${state.selectedDeposit.id}/approve`, {
                method: 'POST',
            });

            Toast.success('Depósito Aprobado', 
                `${Utils.formatCurrency(state.selectedDeposit.amount_pen, 'PEN')} acreditados`);
            Modal.close('modal-deposit-detail');
            this.refresh();
        } catch (error) {
            Toast.error('Error', 'No se pudo aprobar el depósito');
        }
    },

    async rejectDeposit() {
        if (!state.selectedDeposit) return;

        const rejectionInput = document.getElementById('rejection-input');
        const reasonInput = document.getElementById('rejection-reason');
        
        // Mostrar input de razón si está oculto
        if (rejectionInput.style.display === 'none') {
            rejectionInput.style.display = 'block';
            reasonInput.focus();
            return;
        }

        const reason = reasonInput.value.trim();
        if (!reason) {
            Toast.warning('Atención', 'Ingresa el motivo del rechazo');
            return;
        }

        try {
            await Utils.apiRequest(`/admin/deposits/${state.selectedDeposit.id}/reject`, {
                method: 'POST',
                body: JSON.stringify({ reason }),
            });

            Toast.success('Depósito Rechazado', 'Se notificó al usuario');
            Modal.close('modal-deposit-detail');
            reasonInput.value = '';
            rejectionInput.style.display = 'none';
            this.refresh();
        } catch (error) {
            Toast.error('Error', 'No se pudo rechazar el depósito');
        }
    },
};

// =============================================================================
// MÓDULO: ANÁLISIS FORENSE
// =============================================================================
const ForensicAnalyzer = {
    isAnalyzing: false,
    
    init() {
        document.getElementById('btn-scan-forensic')?.addEventListener('click', () => {
            this.runAnalysis();
        });
    },
    
    resetTerminal() {
        const terminal = document.getElementById('terminal-output');
        const verdict = document.getElementById('forensic-verdict');
        
        terminal.innerHTML = `
            <div class="terminal-prompt">
                <span class="prompt-symbol">$</span>
                <span class="prompt-text">Esperando análisis...</span>
            </div>
        `;
        verdict.style.display = 'none';
    },
    
    async runAnalysis() {
        if (this.isAnalyzing || !state.selectedDeposit) return;
        
        this.isAnalyzing = true;
        const btn = document.getElementById('btn-scan-forensic');
        const terminal = document.getElementById('terminal-output');
        const verdict = document.getElementById('forensic-verdict');
        
        // UI: Estado de carga
        btn.disabled = true;
        btn.innerHTML = '<span class="scan-icon spinning">⚡</span> Analizando...';
        verdict.style.display = 'none';
        
        // Limpiar terminal y mostrar inicio
        terminal.innerHTML = '';
        this.appendLine('$ kompite-forensic analyze --image=' + state.selectedDeposit.evidence_url.split('/').pop(), 'command');
        this.appendLine('', 'blank');
        this.appendLine('▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓', 'header');
        this.appendLine('  KOMPITE FORENSIC ANALYZER v1.7.0', 'header');
        this.appendLine('  Inteligencia de Entrada - Mock OCR', 'header');
        this.appendLine('▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓', 'header');
        this.appendLine('', 'blank');
        
        // Simular progreso de análisis
        await this.simulateProgress();
        
        try {
            // Crear FormData para enviar la imagen (usando URL)
            const formData = new FormData();
            
            // Obtener la imagen del depósito y convertir a blob
            const imageResponse = await fetch(state.selectedDeposit.evidence_url);
            const blob = await imageResponse.blob();
            formData.append('file', blob, 'evidence.jpg');
            
            // Llamar al endpoint de análisis
            const response = await fetch(`${CONFIG.API_URL}/admin/deposits/analyze`, {
                method: 'POST',
                headers: state.adminToken ? { 'Authorization': `Bearer ${state.adminToken}` } : {},
                body: formData,
            });
            
            if (!response.ok) throw new Error('Error en análisis');
            
            const result = await response.json();
            
            // Mostrar resultado en terminal
            this.displayTerminalResult(result);
            
            // Mostrar veredicto
            this.showVerdict(result);
            
        } catch (error) {
            console.error('Forensic analysis error:', error);
            this.appendLine('', 'blank');
            this.appendLine('[ERROR] No se pudo completar el análisis', 'error');
            this.appendLine('Razón: ' + error.message, 'error');
        } finally {
            this.isAnalyzing = false;
            btn.disabled = false;
            btn.innerHTML = '<span class="scan-icon">⚡</span> Ejecutar Análisis';
        }
    },
    
    async simulateProgress() {
        const steps = [
            '[1/5] Extrayendo metadatos de imagen...',
            '[2/5] Analizando patrones de color...',
            '[3/5] Detectando proveedor de pago...',
            '[4/5] Ejecutando análisis forense...',
            '[5/5] Calculando puntuación de confianza...',
        ];
        
        for (const step of steps) {
            this.appendLine(step, 'progress');
            await new Promise(r => setTimeout(r, 300));
        }
        
        this.appendLine('', 'blank');
    },
    
    displayTerminalResult(result) {
        const data = result.extracted_data || {};
        const forensic = result.forensic_analysis || {};
        const flags = forensic.flags || [];
        
        // Datos extraídos
        this.appendLine('┌─ DATOS EXTRAÍDOS ──────────────────────┐', 'section');
        this.appendLine(`│ Proveedor:  ${this.pad(result.provider || 'UNKNOWN', 27)}│`, 'data');
        this.appendLine(`│ Monto:      ${this.pad(data.amount ? 'S/ ' + data.amount.toFixed(2) : 'No detectado', 27)}│`, 'data');
        this.appendLine(`│ Referencia: ${this.pad(data.reference || 'No detectado', 27)}│`, 'data');
        this.appendLine(`│ Fecha:      ${this.pad(data.date || 'No detectado', 27)}│`, 'data');
        this.appendLine('└─────────────────────────────────────────┘', 'section');
        this.appendLine('', 'blank');
        
        // Análisis forense
        this.appendLine('┌─ ANÁLISIS FORENSE ─────────────────────┐', 'section');
        this.appendLine(`│ Software:   ${this.pad(forensic.software_detected || 'Ninguno', 27)}│`, 'data');
        this.appendLine(`│ Timestamp:  ${this.pad(forensic.image_timestamp || 'No disponible', 27)}│`, 'data');
        this.appendLine(`│ Integridad: ${this.pad(forensic.is_original ? '✓ Original' : '⚠ Modificado', 27)}│`, forensic.is_original ? 'success' : 'warning');
        this.appendLine('└─────────────────────────────────────────┘', 'section');
        this.appendLine('', 'blank');
        
        // Flags
        if (flags.length > 0) {
            this.appendLine('┌─ FLAGS DE SEGURIDAD ───────────────────┐', 'section');
            flags.forEach(flag => {
                const severity = this.getFlagSeverity(flag);
                this.appendLine(`│ ${severity} ${this.pad(flag, 37)}│`, 'warning');
            });
            this.appendLine('└─────────────────────────────────────────┘', 'section');
            this.appendLine('', 'blank');
        }
        
        // Veredicto
        const confidence = (result.confidence * 100).toFixed(1);
        const verdictText = result.verdict || 'UNKNOWN';
        const verdictClass = this.getVerdictClass(verdictText);
        
        this.appendLine('═══════════════════════════════════════════', 'divider');
        this.appendLine(`  VEREDICTO: ${verdictText}`, verdictClass);
        this.appendLine(`  CONFIANZA: ${confidence}%`, 'data');
        this.appendLine('═══════════════════════════════════════════', 'divider');
    },
    
    showVerdict(result) {
        const verdict = document.getElementById('forensic-verdict');
        const icon = document.getElementById('verdict-icon');
        const label = document.getElementById('verdict-label');
        const confidence = document.getElementById('verdict-confidence');
        
        const verdictText = result.verdict || 'UNKNOWN';
        const confidenceVal = (result.confidence * 100).toFixed(1);
        
        const verdictConfig = {
            'APPROVED': { icon: '✓', class: 'approved', text: 'APROBADO' },
            'NEEDS_REVIEW': { icon: '⚠', class: 'review', text: 'REQUIERE REVISIÓN' },
            'REJECTED': { icon: '✗', class: 'rejected', text: 'RECHAZADO' },
            'SUSPICIOUS': { icon: '🚨', class: 'suspicious', text: 'SOSPECHOSO' },
        };
        
        const config = verdictConfig[verdictText] || verdictConfig['NEEDS_REVIEW'];
        
        verdict.className = 'forensic-verdict ' + config.class;
        icon.textContent = config.icon;
        label.textContent = config.text;
        confidence.textContent = `Confianza: ${confidenceVal}%`;
        
        verdict.style.display = 'flex';
    },
    
    appendLine(text, className = '') {
        const terminal = document.getElementById('terminal-output');
        const line = document.createElement('div');
        line.className = 'terminal-line ' + className;
        line.textContent = text;
        terminal.appendChild(line);
        terminal.scrollTop = terminal.scrollHeight;
    },
    
    pad(str, length) {
        return str.toString().substring(0, length).padEnd(length, ' ');
    },
    
    getFlagSeverity(flag) {
        if (flag.includes('EDITED') || flag.includes('PHOTOSHOP') || flag.includes('DUPLICATE')) {
            return '🔴';
        }
        if (flag.includes('OLD') || flag.includes('MISMATCH')) {
            return '🟡';
        }
        return '⚪';
    },
    
    getVerdictClass(verdict) {
        const classes = {
            'APPROVED': 'success',
            'NEEDS_REVIEW': 'warning',
            'REJECTED': 'error',
            'SUSPICIOUS': 'error',
        };
        return classes[verdict] || 'data';
    },
};

// =============================================================================
// VISTA: USUARIOS
// =============================================================================
const UsersView = {
    init() {
        document.getElementById('search-users').addEventListener('input', 
            Utils.debounce(() => this.refresh(), 300));
        document.getElementById('filter-trust-level').addEventListener('change', () => this.refresh());
    },

    async refresh() {
        const search = document.getElementById('search-users').value;
        const trustLevel = document.getElementById('filter-trust-level').value;

        try {
            const params = new URLSearchParams();
            if (search) params.append('search', search);
            if (trustLevel !== 'ALL') params.append('trust_level', trustLevel);

            const response = await Utils.apiRequest(`/admin/users?${params}`);
            state.users = response.users || [];
            this.render();
        } catch (error) {
            console.error('Error loading users:', error);
        }
    },

    render() {
        const tbody = document.getElementById('users-table');

        if (state.users.length === 0) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="6" style="text-align: center; color: var(--text-muted);">
                        No se encontraron usuarios
                    </td>
                </tr>
            `;
            return;
        }

        tbody.innerHTML = state.users.map(u => `
            <tr>
                <td>
                    <div style="display: flex; flex-direction: column;">
                        <span style="font-weight: 600;">${u.full_name || 'Sin nombre'}</span>
                        <span style="font-size: 0.8rem; color: var(--text-muted);">${Utils.truncateHash(u.id)}</span>
                    </div>
                </td>
                <td>${u.phone_number}</td>
                <td style="color: var(--gold); font-family: var(--font-display);">
                    ${Utils.formatCurrency(u.lkoins_balance)}
                </td>
                <td>${Utils.getStatusBadge(u.trust_level)}</td>
                <td>${Utils.getStatusBadge(u.kyc_status)}</td>
                <td>
                    <button class="btn btn-secondary" onclick="UsersView.viewUser('${u.id}')">
                        👁
                    </button>
                </td>
            </tr>
        `).join('');
    },

    viewUser(userId) {
        Toast.info('Usuario', `Ver detalles: ${Utils.truncateHash(userId)}`);
        // TODO: Implementar modal de detalle de usuario
    },
};

// =============================================================================
// VISTA: PARTIDAS
// =============================================================================
const MatchesView = {
    init() {
        document.getElementById('filter-match-status').addEventListener('change', () => this.refresh());
        document.getElementById('filter-game-type').addEventListener('change', () => this.refresh());
    },

    async refresh() {
        const status = document.getElementById('filter-match-status').value;
        const gameType = document.getElementById('filter-game-type').value;

        try {
            const params = new URLSearchParams();
            if (status !== 'ALL') params.append('status', status);
            if (gameType !== 'ALL') params.append('game_type', gameType);

            const response = await Utils.apiRequest(`/admin/matches?${params}`);
            state.matches = response.matches || [];
            this.render();
        } catch (error) {
            console.error('Error loading matches:', error);
        }
    },

    render() {
        const tbody = document.getElementById('matches-table');

        if (state.matches.length === 0) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="7" style="text-align: center; color: var(--text-muted);">
                        No hay partidas para mostrar
                    </td>
                </tr>
            `;
            return;
        }

        tbody.innerHTML = state.matches.map(m => `
            <tr>
                <td style="font-family: monospace;">${Utils.truncateHash(m.id)}</td>
                <td>${m.game_type}</td>
                <td>
                    ${m.players.map(p => p.name || Utils.truncateHash(p.id, 6)).join(' vs ')}
                </td>
                <td style="color: var(--gold);">${Utils.formatCurrency(m.bet_amount)}</td>
                <td>${Utils.getStatusBadge(m.status)}</td>
                <td>${Utils.formatShortDate(m.created_at)}</td>
                <td>
                    <button class="btn btn-secondary" onclick="MatchesView.viewMatch('${m.id}')">
                        👁
                    </button>
                </td>
            </tr>
        `).join('');
    },

    viewMatch(matchId) {
        Toast.info('Partida', `Ver detalles: ${Utils.truncateHash(matchId)}`);
        // TODO: Implementar modal de detalle de partida
    },
};

// =============================================================================
// VISTA: TRANSACCIONES (LEDGER)
// =============================================================================
const TransactionsView = {
    init() {
        document.getElementById('filter-tx-type').addEventListener('change', () => this.refresh());
        document.getElementById('filter-tx-user').addEventListener('input', 
            Utils.debounce(() => this.refresh(), 300));
    },

    async refresh() {
        const type = document.getElementById('filter-tx-type').value;
        const userId = document.getElementById('filter-tx-user').value;

        try {
            const params = new URLSearchParams();
            if (type !== 'ALL') params.append('type', type);
            if (userId) params.append('user_id', userId);

            const response = await Utils.apiRequest(`/admin/transactions?${params}`);
            state.transactions = response.transactions || [];
            this.render();
        } catch (error) {
            console.error('Error loading transactions:', error);
        }
    },

    render() {
        const tbody = document.getElementById('transactions-table');

        if (state.transactions.length === 0) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="6" style="text-align: center; color: var(--text-muted);">
                        No hay transacciones para mostrar
                    </td>
                </tr>
            `;
            return;
        }

        tbody.innerHTML = state.transactions.map(tx => `
            <tr>
                <td>${Utils.truncateHash(tx.transaction_hash)}</td>
                <td>${tx.user_name || Utils.truncateHash(tx.user_id, 6)}</td>
                <td>
                    <span class="badge badge-${tx.transaction_type.toLowerCase().replace('_', '-')}">
                        ${tx.transaction_type}
                    </span>
                </td>
                <td style="color: ${tx.transaction_type.includes('CREDIT') || tx.transaction_type === 'DEPOSIT' ? 'var(--success)' : 'var(--error)'};">
                    ${tx.transaction_type.includes('CREDIT') || tx.transaction_type === 'DEPOSIT' ? '+' : '-'}${Utils.formatCurrency(tx.amount)}
                </td>
                <td style="color: var(--gold);">${Utils.formatCurrency(tx.balance_after)}</td>
                <td>${Utils.formatShortDate(tx.created_at)}</td>
            </tr>
        `).join('');
    },
};

// =============================================================================
// VISTA: WEBHOOKS
// =============================================================================
const WebhooksView = {
    init() {
        document.getElementById('btn-copy-url').addEventListener('click', () => {
            this.copyToClipboard(document.getElementById('webhook-url').value);
        });

        document.getElementById('btn-copy-key').addEventListener('click', () => {
            const keyEl = document.getElementById('webhook-api-key');
            if (keyEl.type === 'text') {
                this.copyToClipboard(keyEl.value);
            } else {
                Toast.warning('Atención', 'Revela la API Key primero');
            }
        });

        document.getElementById('btn-reveal-key').addEventListener('click', () => {
            const keyEl = document.getElementById('webhook-api-key');
            keyEl.type = keyEl.type === 'password' ? 'text' : 'password';
        });

        document.getElementById('btn-regenerate-key').addEventListener('click', () => {
            this.regenerateApiKey();
        });
    },

    async refresh() {
        try {
            const response = await Utils.apiRequest('/admin/webhook/config');
            document.getElementById('webhook-api-key').value = response.api_key || '●●●●●●●●●●●●●●●●';
            this.renderLogs(response.recent_logs || []);
        } catch (error) {
            console.error('Error loading webhook config:', error);
        }
    },

    renderLogs(logs) {
        const container = document.getElementById('webhook-logs');
        
        if (logs.length === 0) {
            container.innerHTML = '<p style="color: var(--text-muted);">No hay logs recientes</p>';
            return;
        }

        container.innerHTML = logs.map(log => `
            <div class="log-entry">
                <span class="log-time">${Utils.formatShortDate(log.timestamp)}</span>
                <span class="${log.success ? 'log-success' : 'log-error'}">
                    ${log.success ? '✓' : '✗'} ${log.message}
                </span>
            </div>
        `).join('');
    },

    copyToClipboard(text) {
        navigator.clipboard.writeText(text).then(() => {
            Toast.success('Copiado', 'Texto copiado al portapapeles');
        }).catch(() => {
            Toast.error('Error', 'No se pudo copiar');
        });
    },

    async regenerateApiKey() {
        if (!confirm('¿Estás seguro? La API Key actual dejará de funcionar.')) return;

        try {
            const response = await Utils.apiRequest('/admin/webhook/regenerate-key', {
                method: 'POST',
            });
            document.getElementById('webhook-api-key').value = response.api_key;
            document.getElementById('webhook-api-key').type = 'text';
            Toast.success('API Key Regenerada', 'Actualiza tus integraciones');
        } catch (error) {
            Toast.error('Error', 'No se pudo regenerar la API Key');
        }
    },
};

// =============================================================================
// MODAL MANAGER
// =============================================================================
const Modal = {
    open(modalId) {
        const modal = document.getElementById(modalId);
        if (modal) {
            modal.classList.add('active');
            document.body.style.overflow = 'hidden';
        }
    },

    close(modalId) {
        const modal = document.getElementById(modalId);
        if (modal) {
            modal.classList.remove('active');
            document.body.style.overflow = '';
        }
    },

    init() {
        // Cerrar con backdrop
        document.querySelectorAll('.modal-backdrop').forEach(backdrop => {
            backdrop.addEventListener('click', () => {
                backdrop.closest('.modal').classList.remove('active');
                document.body.style.overflow = '';
            });
        });

        // Cerrar con botón
        document.querySelectorAll('[data-close-modal]').forEach(btn => {
            btn.addEventListener('click', () => {
                btn.closest('.modal').classList.remove('active');
                document.body.style.overflow = '';
            });
        });

        // Botones de acción de depósitos
        document.getElementById('btn-approve-deposit').addEventListener('click', () => {
            DepositsView.approveDeposit();
        });

        document.getElementById('btn-reject-deposit').addEventListener('click', () => {
            DepositsView.rejectDeposit();
        });
    },
};

// =============================================================================
// UTILIDADES ADICIONALES
// =============================================================================
Utils.debounce = function(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
};

// =============================================================================
// INICIALIZACIÓN
// =============================================================================
document.addEventListener('DOMContentLoaded', () => {
    Toast.init();
    Modal.init();
    Navigation.init();
    DepositsView.init();
    UsersView.init();
    MatchesView.init();
    TransactionsView.init();
    WebhooksView.init();
    ForensicAnalyzer.init();
    SocketManager.init();

    // Cargar vista inicial
    Navigation.showView('dashboard');

    // Refrescar periódicamente
    setInterval(() => {
        if (state.currentView === 'dashboard') {
            DashboardView.refresh();
        }
    }, CONFIG.REFRESH_INTERVAL);

    console.log('🚀 Kompite Admin Panel inicializado');
});
