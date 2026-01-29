/**
 * =============================================================================
 * KOMPITE - Aplicación Principal (JavaScript)
 * =============================================================================
 * Cliente SPA para la Infraestructura de Arbitraje.
 * Maneja conexión WebSocket, navegación y estados de partida.
 * =============================================================================
 */

// =============================================================================
// CONFIGURACIÓN
// =============================================================================

// Detectar automáticamente el host del backend
// En desarrollo local: localhost:8000
// En Codespaces/DevContainers: usa el mismo host pero puerto 8000
function getBackendHost() {
    const hostname = window.location.hostname;
    const protocol = window.location.protocol;
    
    // Local development
    if (hostname === 'localhost' || hostname === '127.0.0.1') {
        return 'http://localhost:8000';
    }
    
    // GitHub Codespaces: formato xxx-3000.xxx.github.dev -> xxx-8000.xxx.github.dev
    if (hostname.includes('.app.github.dev') || hostname.includes('.github.dev')) {
        // Reemplazar el puerto en la URL del codespace
        const newHostname = hostname.replace(/-3000\./, '-8000.');
        return `${protocol}//${newHostname}`;
    }
    
    // Otros entornos: intentar reemplazar el puerto
    return `${protocol}//${hostname.replace('-3000', '-8000').replace(':3000', ':8000')}`;
}

const BACKEND_HOST = getBackendHost();

// Log para debugging
console.log('[CONFIG] Backend Host:', BACKEND_HOST);
console.log('[CONFIG] Frontend Host:', window.location.origin);

const CONFIG = {
    API_URL: BACKEND_HOST,
    WS_URL: BACKEND_HOST,
    HEARTBEAT_INTERVAL: 3000,  // 3 segundos
    RECONNECT_DELAY: 2000,
    LKOIN_TO_SOLES: 0.20,  // 1 LKoin = 0.20 soles (5:1)
};

// =============================================================================
// ESTADO GLOBAL
// =============================================================================

const state = {
    // Usuario
    user: {
        id: null,
        balance: 0,
        trustScore: 100,
        trustLevel: 'GREEN',
        kycStatus: 'PENDING',
    },
    
    // Conexión
    socket: null,
    isConnected: false,
    reconnectAttempts: 0,
    
    // Matchmaking
    isSearching: false,
    searchStartTime: null,
    selectedGame: null,
    selectedBet: 20,
    
    // Partida actual
    currentMatch: null,
    matchState: null,
    
    // Heartbeat
    heartbeatInterval: null,
    heartbeatSequence: 0,
    lastPing: 0,
};

// =============================================================================
// UTILIDADES
// =============================================================================

/**
 * Genera un UUID v4
 */
function generateUUID() {
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
        const r = Math.random() * 16 | 0;
        const v = c === 'x' ? r : (r & 0x3 | 0x8);
        return v.toString(16);
    });
}

/**
 * Formatea un número como moneda
 */
function formatCurrency(amount, decimals = 2) {
    return parseFloat(amount).toFixed(decimals);
}

/**
 * Formatea tiempo en MM:SS
 */
function formatTime(seconds) {
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
}

/**
 * Muestra una notificación toast
 */
function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.innerHTML = `
        <div class="toast-content">
            <span class="toast-message">${message}</span>
        </div>
    `;
    container.appendChild(toast);
    
    // Remover después de 4 segundos
    setTimeout(() => {
        toast.style.animation = 'toastSlideIn 0.3s ease reverse';
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}

// =============================================================================
// NAVEGACIÓN
// =============================================================================

/**
 * Cambia la vista activa
 */
function navigateTo(viewName) {
    // Desactivar todas las vistas
    document.querySelectorAll('.view').forEach(view => {
        view.classList.remove('active');
    });
    
    // Activar la vista seleccionada
    const targetView = document.getElementById(`view-${viewName}`);
    if (targetView) {
        targetView.classList.add('active');
    }
    
    // Actualizar navegación
    document.querySelectorAll('.nav-link').forEach(link => {
        link.classList.remove('active');
        if (link.dataset.view === viewName) {
            link.classList.add('active');
        }
    });
}

// =============================================================================
// CONEXIÓN WEBSOCKET
// =============================================================================

/**
 * Inicializa la conexión WebSocket
 */
function initSocket() {
    if (state.socket) {
        state.socket.disconnect();
    }
    
    // Generar ID de usuario si no existe
    if (!state.user.id) {
        state.user.id = generateUUID();
    }
    
    state.socket = io(CONFIG.WS_URL, {
        path: '/socket.io/',
        transports: ['polling', 'websocket'],
        reconnection: true,
        reconnectionDelay: CONFIG.RECONNECT_DELAY,
        reconnectionAttempts: 5,
        upgrade: true,
    });
    
    // Eventos de conexión
    state.socket.on('connect', onConnect);
    state.socket.on('disconnect', onDisconnect);
    state.socket.on('connect_error', onConnectError);
    
    // Eventos de matchmaking
    state.socket.on('matchmaking_queued', onMatchmakingQueued);
    state.socket.on('matchmaking_denied', onMatchmakingDenied);
    state.socket.on('matchmaking_cancelled', onMatchmakingCancelled);
    state.socket.on('match_found', onMatchFound);
    
    // Eventos de partida
    state.socket.on('player_ready_update', onPlayerReadyUpdate);
    state.socket.on('match_locked', onMatchLocked);
    state.socket.on('match_started', onMatchStarted);
    state.socket.on('move_received', onMoveReceived);
    state.socket.on('match_validating', onMatchValidating);
    state.socket.on('player_disconnected', onPlayerDisconnected);
    
    // Heartbeat
    state.socket.on('heartbeat_ack', onHeartbeatAck);
    
    // Errores
    state.socket.on('error', onError);
}

function onConnect() {
    console.log('[WS] Conectado');
    state.isConnected = true;
    state.reconnectAttempts = 0;
    updateConnectionStatus('connected');
    showToast('Conectado al servidor', 'success');
    
    // Iniciar heartbeat
    startHeartbeat();
}

function onDisconnect(reason) {
    console.log('[WS] Desconectado:', reason);
    state.isConnected = false;
    updateConnectionStatus('disconnected');
    stopHeartbeat();
    
    if (reason !== 'io client disconnect') {
        showToast('Conexión perdida. Reconectando...', 'warning');
    }
}

function onConnectError(error) {
    console.error('[WS] Error de conexión:', error);
    state.reconnectAttempts++;
    updateConnectionStatus('disconnected');
}

function onError(data) {
    console.error('[WS] Error:', data);
    showToast(data.message || 'Error del servidor', 'error');
}

/**
 * Actualiza el indicador de estado de conexión
 */
function updateConnectionStatus(status) {
    const statusElement = document.getElementById('connection-status');
    const textElement = statusElement.querySelector('.status-text');
    
    statusElement.className = `connection-status ${status}`;
    
    switch (status) {
        case 'connected':
            textElement.textContent = 'Conectado';
            break;
        case 'disconnected':
            textElement.textContent = 'Desconectado';
            break;
        default:
            textElement.textContent = 'Conectando';
    }
}

// =============================================================================
// HEARTBEAT
// =============================================================================

function startHeartbeat() {
    if (state.heartbeatInterval) {
        clearInterval(state.heartbeatInterval);
    }
    
    state.heartbeatInterval = setInterval(() => {
        if (state.isConnected) {
            state.heartbeatSequence++;
            const timestamp = Date.now() / 1000;
            
            state.socket.emit('heartbeat', {
                client_timestamp: timestamp,
                sequence: state.heartbeatSequence,
                game_state: state.matchState || ''
            });
            
            state.lastPing = Date.now();
        }
    }, CONFIG.HEARTBEAT_INTERVAL);
}

function stopHeartbeat() {
    if (state.heartbeatInterval) {
        clearInterval(state.heartbeatInterval);
        state.heartbeatInterval = null;
    }
}

function onHeartbeatAck(data) {
    const ping = Date.now() - state.lastPing;
    updatePingDisplay(ping, data.connection_quality);
    
    if (data.warning) {
        console.warn('[JITTER]', data.warning);
    }
}

function updatePingDisplay(ping, quality) {
    const pingElement = document.getElementById('match-ping');
    if (pingElement) {
        pingElement.textContent = ping;
        pingElement.className = 'ping-value';
        
        if (ping > 200) {
            pingElement.classList.add('critical');
        } else if (ping > 100) {
            pingElement.classList.add('warning');
        }
    }
}

// =============================================================================
// MATCHMAKING
// =============================================================================

function startMatchmaking(gameType, betAmount) {
    if (!state.isConnected) {
        showToast('No hay conexión al servidor', 'error');
        return;
    }
    
    state.isSearching = true;
    state.selectedGame = gameType;
    state.selectedBet = betAmount;
    state.searchStartTime = Date.now();
    
    // Cambiar a vista de matchmaking
    navigateTo('matchmaking');
    document.getElementById('matchmaking-game').textContent = getGameName(gameType);
    document.getElementById('matchmaking-bet').textContent = `Apuesta: ${betAmount} LK`;
    
    // Iniciar contador de tiempo
    updateMatchmakingTimer();
    
    // Enviar solicitud al servidor
    state.socket.emit('join_matchmaking', {
        user_id: state.user.id,
        game_type: gameType,
        bet_amount: betAmount,
        security_profile: {
            trust_score: state.user.trustScore,
            trust_level: state.user.trustLevel,
            kyc_status: state.user.kycStatus,
            is_frozen: false,
            device_fingerprint: getDeviceFingerprint(),
            current_ip: '', // Se obtiene en el servidor
            lkoins_balance: state.user.balance
        }
    });
}

function cancelMatchmaking() {
    if (!state.isSearching) return;
    
    state.socket.emit('cancel_matchmaking', {
        game_type: state.selectedGame,
        bet_amount: state.selectedBet
    });
    
    state.isSearching = false;
    navigateTo('lobby');
}

function onMatchmakingQueued(data) {
    showToast('Buscando oponente...', 'info');
}

function onMatchmakingDenied(data) {
    state.isSearching = false;
    navigateTo('lobby');
    showToast(data.reason, 'error');
}

function onMatchmakingCancelled(data) {
    state.isSearching = false;
    showToast('Búsqueda cancelada', 'info');
}

function onMatchFound(data) {
    state.isSearching = false;
    state.currentMatch = data;
    state.matchState = 'PREPARING';
    
    showToast('¡Oponente encontrado!', 'success');
    
    // Cambiar a vista de partida
    navigateTo('match');
    updateMatchUI(data);
    
    // Confirmar que estamos listos
    setTimeout(() => {
        state.socket.emit('player_ready', {
            match_id: data.match_id,
            client_seed: generateUUID()  // Seed para Provably Fair
        });
    }, 1000);
}

function updateMatchmakingTimer() {
    if (!state.isSearching) return;
    
    const elapsed = Math.floor((Date.now() - state.searchStartTime) / 1000);
    document.getElementById('matchmaking-time').textContent = formatTime(elapsed);
    
    requestAnimationFrame(() => setTimeout(updateMatchmakingTimer, 1000));
}

// =============================================================================
// PARTIDA
// =============================================================================

function updateMatchUI(matchData) {
    document.getElementById('match-bet-amount').textContent = matchData.bet_amount;
    document.getElementById('match-status').textContent = 'PREPARANDO';
    
    // Actualizar nombres de jugadores
    const players = matchData.players || [];
    if (players.length >= 1) {
        document.getElementById('match-player1-name').textContent = 'Tú';
    }
    if (players.length >= 2) {
        document.getElementById('match-player2-name').textContent = 'Oponente';
    }
}

function onPlayerReadyUpdate(data) {
    console.log('[MATCH] Player ready:', data);
    if (data.all_ready) {
        document.getElementById('match-status').textContent = 'BLOQUEANDO FONDOS';
    }
}

function onMatchLocked(data) {
    state.matchState = 'LOCKED';
    document.getElementById('match-status').textContent = 'FONDOS BLOQUEADOS';
    
    // Confirmar escrow (simulado)
    setTimeout(() => {
        state.socket.emit('confirm_escrow', {
            match_id: data.match_id,
            transaction_hash: generateUUID()
        });
    }, 1000);
}

function onMatchStarted(data) {
    // Evitar procesamiento duplicado
    if (state.matchState === 'IN_PROGRESS') {
        console.log('[MATCH] Match already in progress, ignoring duplicate event');
        return;
    }
    
    state.matchState = 'IN_PROGRESS';
    
    // Primero navegar a la vista de match para que el DOM esté disponible
    navigateTo('match');
    
    // Actualizar UI
    document.getElementById('match-status').textContent = 'EN PROGRESO';
    showToast('¡La partida ha comenzado!', 'success');
    
    // Esperar un frame para que la vista sea visible, luego inicializar el juego
    requestAnimationFrame(() => {
        initGameArea(state.selectedGame);
    });
}

function onMoveReceived(data) {
    console.log('[MATCH] Move received:', data);
    // Procesar movimiento del oponente
}

function onMatchValidating(data) {
    state.matchState = 'VALIDATION';
    document.getElementById('match-status').textContent = 'VALIDANDO';
    showToast('Validando resultado...', 'info');
}

function onPlayerDisconnected(data) {
    showToast(`Oponente desconectado. Espera de gracia: ${data.grace_period}s`, 'warning');
}

/**
 * Inicializa el área de juego según el tipo
 */
function initGameArea(gameType) {
    const gameArea = document.getElementById('game-area');
    
    // Limpiar contenido anterior
    gameArea.innerHTML = '';
    
    switch(gameType) {
        case 'LUDO':
            initLudoGame(gameArea);
            break;
        default:
            // Placeholder para otros juegos
            gameArea.innerHTML = `
                <div class="game-placeholder">
                    <h2>🎮 ${getGameName(gameType)}</h2>
                    <p>El juego se cargará aquí</p>
                    <p class="hint">Los juegos serán implementados en fases posteriores</p>
                </div>
            `;
    }
}

function initLudoGame(gameArea) {
    console.log('[LUDO] Inicializando área de juego...');
    console.log('[LUDO] gameArea:', gameArea);
    
    // Evitar inicialización duplicada
    if (window.ludoGame && window.ludoGame.matchId === state.currentMatch?.match_id) {
        console.log('[LUDO] Juego ya inicializado para este match');
        return;
    }
    
    // Crear estructura HTML para Ludo
    gameArea.innerHTML = `
        <div class="ludo-game-container">
            <div class="ludo-board-wrapper">
                <canvas id="ludo-canvas" width="480" height="480"></canvas>
            </div>
            
            <div class="ludo-controls">
                <div class="ludo-turn-indicator">
                    <span class="turn-label">Turno</span>
                    <div class="turn-player">
                        <span class="turn-color" id="ludo-turn-color"></span>
                        <span class="turn-name" id="ludo-turn-name">Esperando...</span>
                    </div>
                </div>
                
                <div class="ludo-dice-container">
                    <div class="ludo-dice" id="ludo-dice">?</div>
                    <button class="btn-roll-dice" id="btn-roll-dice" disabled>
                        Lanzar Dado
                    </button>
                </div>
                
                <div class="ludo-status" id="ludo-status">
                    Conectando al servidor...
                </div>
                
                <div class="ludo-provably-fair">
                    <span class="icon">🔒</span>
                    <span>Provably Fair - <a href="#" id="ludo-verify-link">Verificar</a></span>
                </div>
            </div>
        </div>
    `;
    
    // Usar setTimeout para dar tiempo al DOM de renderizar
    setTimeout(() => {
        const canvas = document.getElementById('ludo-canvas');
        console.log('[LUDO] Canvas encontrado:', canvas);
        
        if (!canvas) {
            console.error('[LUDO] Canvas no encontrado después de crear HTML');
            console.error('[LUDO] gameArea.innerHTML:', gameArea.innerHTML.substring(0, 200));
            return;
        }
        
        if (typeof LudoGameController === 'undefined') {
            console.error('[LUDO] LudoGameController no está definido');
            return;
        }
        
        try {
            window.ludoGame = new LudoGameController(
                'ludo-canvas',
                state.socket,
                state.currentMatch?.match_id
            );
            console.log('[LUDO] Controlador creado exitosamente');
            
            // Configurar eventos de UI
            document.getElementById('btn-roll-dice')?.addEventListener('click', () => {
                window.ludoGame?.requestDiceRoll();
            });
            
            // Emitir evento de inicio
            state.socket.emit('ludo_start_game', {
                match_id: state.currentMatch?.match_id
            });
        } catch (err) {
            console.error('[LUDO] Error al crear controlador:', err);
        }
    }, 100); // 100ms de delay para asegurar render
}

// =============================================================================
// JUEGOS
// =============================================================================

const GAMES = [
    {
        type: 'PENALTY_KICKS',
        name: 'Penales',
        icon: '⚽',
        description: 'Tiro de penales 1v1 con física realista',
        minBet: 1,
        maxBet: 1000,
        playersOnline: 0
    },
    {
        type: 'BASKETBALL',
        name: 'Tiro Libre',
        icon: '🏀',
        description: 'Competencia de tiros libres',
        minBet: 1,
        maxBet: 1000,
        playersOnline: 0
    },
    {
        type: 'ROCK_PAPER_SCISSORS',
        name: 'Piedra, Papel, Tijera',
        icon: '✊',
        description: 'Clásico juego de estrategia mental',
        minBet: 0.5,
        maxBet: 100,
        playersOnline: 0
    },
    {
        type: 'MEMORY',
        name: 'Memoria',
        icon: '🧠',
        description: 'Encuentra las parejas antes que tu rival',
        minBet: 1,
        maxBet: 500,
        playersOnline: 0
    },
    {
        type: 'LUDO',
        name: 'Ludo',
        icon: '🎲',
        description: 'Clásico juego de mesa 1v1',
        minBet: 1,
        maxBet: 500,
        playersOnline: 0
    },
    {
        type: 'AIR_HOCKEY',
        name: 'Air Hockey',
        icon: '🏒',
        description: 'Hockey de mesa con física realista',
        minBet: 1,
        maxBet: 1000,
        playersOnline: 0
    }
];

function getGameName(gameType) {
    const game = GAMES.find(g => g.type === gameType);
    return game ? game.name : gameType;
}

function renderGames() {
    const grid = document.getElementById('games-grid');
    
    grid.innerHTML = GAMES.map(game => `
        <div class="game-card" data-game-type="${game.type}" onclick="openBetModal('${game.type}')">
            <div class="game-card-icon">${game.icon}</div>
            <h3 class="game-card-title">${game.name}</h3>
            <p class="game-card-description">${game.description}</p>
            <div class="game-card-meta">
                <span class="game-card-players">
                    <span class="dot connected"></span>
                    ${game.playersOnline} online
                </span>
                <span class="game-card-bet">${game.minBet} - ${game.maxBet} LK</span>
            </div>
        </div>
    `).join('');
}

// =============================================================================
// MODAL DE APUESTA
// =============================================================================

function openBetModal(gameType) {
    const game = GAMES.find(g => g.type === gameType);
    if (!game) return;
    
    state.selectedGame = gameType;
    
    // Actualizar modal
    document.getElementById('modal-game-preview').innerHTML = `
        <div class="game-icon">${game.icon}</div>
        <h3 class="game-name">${game.name}</h3>
    `;
    
    // Mostrar modal
    document.getElementById('modal-bet').classList.add('active');
    
    // Seleccionar apuesta por defecto
    selectBetAmount(20);
}

function closeBetModal() {
    document.getElementById('modal-bet').classList.remove('active');
}

function selectBetAmount(amount) {
    state.selectedBet = amount;
    
    // Actualizar botones
    document.querySelectorAll('.bet-option').forEach(btn => {
        btn.classList.toggle('active', parseInt(btn.dataset.amount) === amount);
    });
    
    // Actualizar resumen
    const rake = amount * 0.05;  // 5% comisión
    const prize = (amount * 2) - (rake * 2);
    
    document.getElementById('bet-your-amount').textContent = `${amount} LK`;
    document.getElementById('bet-potential-prize').textContent = `${prize.toFixed(2)} LK`;
    document.getElementById('bet-fee').textContent = `${rake.toFixed(2)} LK`;
}

// =============================================================================
// WALLET
// =============================================================================

function updateWallet() {
    document.getElementById('user-balance').textContent = formatCurrency(state.user.balance);
    document.getElementById('wallet-balance').textContent = formatCurrency(state.user.balance);
    document.getElementById('wallet-fiat').textContent = formatCurrency(state.user.balance * CONFIG.LKOIN_TO_SOLES);
}

// =============================================================================
// DEVICE FINGERPRINT
// =============================================================================

function getDeviceFingerprint() {
    // Fingerprint simplificado (en producción usar librería como FingerprintJS)
    const canvas = document.createElement('canvas');
    const gl = canvas.getContext('webgl');
    const debugInfo = gl?.getExtension('WEBGL_debug_renderer_info');
    const renderer = debugInfo ? gl.getParameter(debugInfo.UNMASKED_RENDERER_WEBGL) : 'unknown';
    
    const data = [
        navigator.userAgent,
        navigator.language,
        screen.width,
        screen.height,
        screen.colorDepth,
        new Date().getTimezoneOffset(),
        renderer
    ].join('|');
    
    // Simple hash
    let hash = 0;
    for (let i = 0; i < data.length; i++) {
        const char = data.charCodeAt(i);
        hash = ((hash << 5) - hash) + char;
        hash = hash & hash;
    }
    
    return Math.abs(hash).toString(16);
}

// =============================================================================
// INICIALIZACIÓN
// =============================================================================

document.addEventListener('DOMContentLoaded', () => {
    console.log('[KOMPITE] Inicializando...');
    
    // Renderizar juegos
    renderGames();
    
    // Configurar navegación
    document.querySelectorAll('.nav-link').forEach(link => {
        link.addEventListener('click', (e) => {
            e.preventDefault();
            navigateTo(link.dataset.view);
        });
    });
    
    // Configurar modal de apuesta
    document.getElementById('modal-bet-close').addEventListener('click', closeBetModal);
    document.getElementById('modal-bet').addEventListener('click', (e) => {
        if (e.target.id === 'modal-bet') closeBetModal();
    });
    
    // Configurar opciones de apuesta
    document.querySelectorAll('.bet-option').forEach(btn => {
        btn.addEventListener('click', () => {
            selectBetAmount(parseInt(btn.dataset.amount));
        });
    });
    
    // Input personalizado de apuesta
    document.getElementById('bet-custom-input').addEventListener('input', (e) => {
        const amount = parseFloat(e.target.value) || 0;
        if (amount > 0) {
            selectBetAmount(amount);
        }
    });
    
    // Botón de iniciar matchmaking
    document.getElementById('btn-start-matchmaking').addEventListener('click', () => {
        closeBetModal();
        startMatchmaking(state.selectedGame, state.selectedBet);
    });
    
    // Botón de cancelar matchmaking
    document.getElementById('btn-cancel-matchmaking').addEventListener('click', cancelMatchmaking);
    
    // Botones de resultado
    document.getElementById('btn-play-again')?.addEventListener('click', () => {
        openBetModal(state.selectedGame);
    });
    
    document.getElementById('btn-back-lobby')?.addEventListener('click', () => {
        navigateTo('lobby');
    });
    
    // Simular balance inicial
    state.user.balance = 100.00;
    updateWallet();
    
    // Inicializar conexión WebSocket
    initSocket();
    
    // Inicializar escáner de depósitos
    DepositScanner.init();
    
    // Ocultar loader
    setTimeout(() => {
        document.getElementById('loader').classList.add('hidden');
    }, 1500);
    
    console.log('[KOMPITE] Inicialización completa');
});


// =============================================================================
// MÓDULO: ESCÁNER DE DEPÓSITOS (OCR)
// =============================================================================

const DepositScanner = {
    currentFile: null,
    scanResult: null,
    
    init() {
        // Botón de ir a depósito
        document.getElementById('btn-deposit')?.addEventListener('click', () => {
            navigateTo('deposit');
        });
        
        // Botón volver de depósito
        document.getElementById('btn-back-from-deposit')?.addEventListener('click', () => {
            navigateTo('wallet');
            this.reset();
        });
        
        // Input de archivo
        const fileInput = document.getElementById('receipt-input');
        const selectBtn = document.getElementById('btn-select-image');
        const dropzone = document.getElementById('scanner-dropzone');
        
        if (selectBtn) {
            selectBtn.addEventListener('click', () => fileInput?.click());
        }
        
        if (fileInput) {
            fileInput.addEventListener('change', (e) => {
                if (e.target.files?.[0]) {
                    this.handleFile(e.target.files[0]);
                }
            });
        }
        
        // Drag & Drop
        if (dropzone) {
            dropzone.addEventListener('dragover', (e) => {
                e.preventDefault();
                dropzone.classList.add('dragover');
            });
            
            dropzone.addEventListener('dragleave', () => {
                dropzone.classList.remove('dragover');
            });
            
            dropzone.addEventListener('drop', (e) => {
                e.preventDefault();
                dropzone.classList.remove('dragover');
                if (e.dataTransfer.files?.[0]) {
                    this.handleFile(e.dataTransfer.files[0]);
                }
            });
        }
        
        // Botones de retry
        document.getElementById('btn-rescan')?.addEventListener('click', () => this.reset());
        document.getElementById('btn-retry-scan')?.addEventListener('click', () => this.reset());
        document.getElementById('btn-change-receipt')?.addEventListener('click', () => {
            document.getElementById('receipt-input')?.click();
        });
        
        // Copiar número de cuenta
        document.querySelectorAll('.btn-copy-number').forEach(btn => {
            btn.addEventListener('click', () => {
                const number = btn.dataset.copy;
                navigator.clipboard.writeText(number).then(() => {
                    showToast('Número copiado', 'success');
                });
            });
        });
        
        // Formulario de depósito
        document.getElementById('deposit-form')?.addEventListener('submit', (e) => {
            e.preventDefault();
            this.submitDeposit();
        });
        
        // Actualizar LKoins al cambiar monto
        document.getElementById('deposit-amount')?.addEventListener('input', (e) => {
            const amount = parseFloat(e.target.value) || 0;
            document.getElementById('deposit-lkoins').textContent = 
                `${amount.toFixed(4)} LKC`;
        });
        
        // Botón finalizar
        document.getElementById('btn-deposit-done')?.addEventListener('click', () => {
            navigateTo('wallet');
            this.reset();
        });
    },
    
    async handleFile(file) {
        // Validar tipo
        if (!file.type.startsWith('image/')) {
            showToast('Solo se permiten imágenes', 'error');
            return;
        }
        
        // Validar tamaño (10MB)
        if (file.size > 10 * 1024 * 1024) {
            showToast('La imagen es demasiado grande (máx 10MB)', 'error');
            return;
        }
        
        this.currentFile = file;
        
        // Mostrar preview y estado de escaneo
        this.showState('scanning');
        
        // Crear preview
        const reader = new FileReader();
        reader.onload = (e) => {
            document.getElementById('scanner-preview').src = e.target.result;
            document.getElementById('receipt-preview-img').src = e.target.result;
        };
        reader.readAsDataURL(file);
        
        // Simular progreso
        const progressTexts = [
            'Detectando proveedor...',
            'Extrayendo monto...',
            'Validando referencia...',
            'Analizando integridad...'
        ];
        
        let progressIdx = 0;
        const progressInterval = setInterval(() => {
            progressIdx = (progressIdx + 1) % progressTexts.length;
            const textEl = document.querySelector('.progress-text');
            if (textEl) textEl.textContent = progressTexts[progressIdx];
        }, 500);
        
        try {
            // Enviar al backend
            const formData = new FormData();
            formData.append('file', file);
            
            const response = await fetch(`${CONFIG.API_URL}/api/v1/wallet/scan-receipt`, {
                method: 'POST',
                body: formData
            });
            
            clearInterval(progressInterval);
            
            if (!response.ok) {
                throw new Error(`Error ${response.status}`);
            }
            
            const result = await response.json();
            this.scanResult = result;
            
            // Manejar resultado
            if (result.can_prefill) {
                this.showSuccess(result);
            } else if (result.needs_manual_input) {
                this.showWarning(result);
            } else {
                this.showError(result);
            }
            
        } catch (error) {
            clearInterval(progressInterval);
            console.error('Scan error:', error);
            this.showError({
                message: 'Error de conexión. Por favor, complete los datos manualmente.',
                message_type: 'error'
            });
        }
    },
    
    showState(stateName) {
        const states = ['idle', 'scanning', 'success', 'warning', 'error'];
        states.forEach(s => {
            const el = document.getElementById(`scanner-${s}`);
            if (el) el.classList.toggle('hidden', s !== stateName);
        });
    },
    
    showSuccess(result) {
        this.showState('success');
        
        // Llenar datos extraídos
        const container = document.getElementById('scanner-extracted');
        const data = result.prefill_data;
        
        // Determinar clase del provider
        const providerLower = (data?.provider || '').toLowerCase();
        let providerClass = 'yape';
        if (providerLower.includes('plin')) providerClass = 'plin';
        if (providerLower.includes('bcp')) providerClass = 'bcp';
        if (providerLower.includes('bbva')) providerClass = 'bbva';
        
        container.innerHTML = `
            <div class="extracted-row">
                <span class="extracted-label">Proveedor</span>
                <span class="extracted-value provider">
                    <span class="provider-badge ${providerClass}">${data?.provider_display || 'Desconocido'}</span>
                </span>
            </div>
            <div class="extracted-row">
                <span class="extracted-label">Monto</span>
                <span class="extracted-value gold">S/ ${data?.amount || '0.00'}</span>
            </div>
            <div class="extracted-row">
                <span class="extracted-label">Nro. Operación</span>
                <span class="extracted-value">${data?.transaction_reference || 'No detectado'}</span>
            </div>
            <div class="extracted-row">
                <span class="extracted-label">Confianza</span>
                <span class="extracted-value">${Math.round((result.confidence || 0) * 100)}%</span>
            </div>
        `;
        
        // Pre-llenar formulario
        if (data?.amount) {
            document.getElementById('deposit-amount').value = data.amount;
            document.getElementById('deposit-lkoins').textContent = 
                `${parseFloat(data.amount).toFixed(4)} LKC`;
        }
        if (data?.transaction_reference) {
            document.getElementById('deposit-reference').value = data.transaction_reference;
        }
        if (data?.provider) {
            document.getElementById('deposit-provider').value = data.provider;
        }
        
        // Mostrar formulario
        document.getElementById('deposit-form').classList.remove('hidden');
        
        showToast(result.message, 'success');
    },
    
    showWarning(result) {
        this.showState('warning');
        
        document.getElementById('scanner-warning-title').textContent = 'Atención';
        document.getElementById('scanner-warning-msg').textContent = 
            result.message || 'Por favor, verifique los datos.';
        
        // Mostrar formulario para llenado manual
        document.getElementById('deposit-form').classList.remove('hidden');
        
        showToast(result.message, 'warning');
    },
    
    showError(result) {
        this.showState('error');
        
        document.getElementById('scanner-error-msg').textContent = 
            result.message || 'No se pudo procesar la imagen.';
        
        showToast(result.message, 'error');
    },
    
    reset() {
        this.currentFile = null;
        this.scanResult = null;
        
        this.showState('idle');
        
        // Limpiar formulario
        document.getElementById('deposit-form')?.classList.add('hidden');
        document.getElementById('deposit-amount').value = '';
        document.getElementById('deposit-reference').value = '';
        document.getElementById('deposit-lkoins').textContent = '0.00 LKC';
        
        // Limpiar input
        const fileInput = document.getElementById('receipt-input');
        if (fileInput) fileInput.value = '';
        
        // Ocultar éxito
        document.getElementById('deposit-success')?.classList.add('hidden');
    },
    
    async submitDeposit() {
        const amount = document.getElementById('deposit-amount').value;
        const reference = document.getElementById('deposit-reference').value;
        const provider = document.getElementById('deposit-provider').value;
        
        if (!amount || !reference) {
            showToast('Complete todos los campos', 'warning');
            return;
        }
        
        if (!this.currentFile) {
            showToast('Debe subir un comprobante', 'warning');
            return;
        }
        
        // TODO: Enviar al backend
        // const formData = new FormData();
        // formData.append('file', this.currentFile);
        // formData.append('amount_pen', amount);
        // formData.append('transaction_reference', reference);
        // formData.append('provider', provider);
        // await fetch(`${CONFIG.API_URL}/api/v1/deposits`, { method: 'POST', body: formData });
        
        // Simular éxito
        document.getElementById('deposit-form').classList.add('hidden');
        document.getElementById('success-ref-number').textContent = reference;
        document.getElementById('deposit-success').classList.remove('hidden');
        
        showToast('Solicitud de depósito enviada', 'success');
    }
};


// =============================================================================
// MÓDULO: RETIROS
// =============================================================================

const WithdrawModule = {
    maxBalance: 0,
    
    init() {
        // Botón de retiro
        document.getElementById('btn-withdraw')?.addEventListener('click', () => {
            navigateTo('withdraw');
            this.loadBalance();
        });
        
        // Botón volver
        document.getElementById('btn-back-from-withdraw')?.addEventListener('click', () => {
            navigateTo('wallet');
        });
        
        // Botón MAX
        document.getElementById('btn-withdraw-max')?.addEventListener('click', () => {
            document.getElementById('withdraw-amount').value = this.maxBalance;
            this.updateSummary();
        });
        
        // Cambio de monto
        document.getElementById('withdraw-amount')?.addEventListener('input', () => {
            this.updateSummary();
        });
        
        // Cambio de método
        document.getElementById('withdraw-method')?.addEventListener('change', (e) => {
            this.toggleMethodFields(e.target.value);
        });
        
        // Formulario
        document.getElementById('withdraw-form')?.addEventListener('submit', (e) => {
            e.preventDefault();
            this.submitWithdraw();
        });
        
        // Botón done
        document.getElementById('btn-withdraw-done')?.addEventListener('click', () => {
            this.reset();
            navigateTo('wallet');
        });
    },
    
    async loadBalance() {
        try {
            const response = await fetch(`${CONFIG.API_URL}/api/v1/wallet/balance`);
            const data = await response.json();
            
            this.maxBalance = parseFloat(data.available);
            document.getElementById('withdraw-available').textContent = 
                `${formatCurrency(this.maxBalance, 4)} LK`;
            
            if (parseFloat(data.escrow_out) > 0) {
                document.getElementById('balance-locked-section').style.display = 'flex';
                document.getElementById('withdraw-locked').textContent = 
                    `${formatCurrency(data.escrow_out, 4)} LK`;
            }
            
            this.updateSummary();
        } catch (error) {
            console.error('Error loading balance:', error);
            // Usar balance del estado
            this.maxBalance = state.user.balance;
            document.getElementById('withdraw-available').textContent = 
                `${formatCurrency(this.maxBalance, 4)} LK`;
        }
    },
    
    updateSummary() {
        const amount = parseFloat(document.getElementById('withdraw-amount')?.value) || 0;
        const fiat = amount;  // 1 LK = 1 PEN
        const newBalance = Math.max(0, this.maxBalance - amount);
        
        document.getElementById('summary-withdraw-amount').textContent = 
            `${formatCurrency(amount, 2)} LK`;
        document.getElementById('summary-withdraw-fiat').textContent = 
            `S/ ${formatCurrency(fiat, 2)}`;
        document.getElementById('summary-new-balance').textContent = 
            `${formatCurrency(newBalance, 4)} LK`;
        document.getElementById('withdraw-fiat-preview').textContent = 
            formatCurrency(fiat, 2);
        
        // Habilitar/deshabilitar botón
        const btn = document.getElementById('btn-submit-withdraw');
        const method = document.getElementById('withdraw-method')?.value;
        const isValid = amount >= 5 && amount <= this.maxBalance && method;
        
        btn.disabled = !isValid;
    },
    
    toggleMethodFields(method) {
        const phoneGroup = document.getElementById('withdraw-phone-group');
        const bankGroup = document.getElementById('withdraw-bank-group');
        
        if (method === 'BANK_TRANSFER') {
            phoneGroup.style.display = 'none';
            bankGroup.style.display = 'block';
        } else {
            phoneGroup.style.display = 'block';
            bankGroup.style.display = 'none';
        }
        
        this.updateSummary();
    },
    
    async submitWithdraw() {
        const amount = parseFloat(document.getElementById('withdraw-amount').value);
        const method = document.getElementById('withdraw-method').value;
        let destination = '';
        
        if (method === 'BANK_TRANSFER') {
            destination = document.getElementById('withdraw-cci').value;
        } else {
            destination = document.getElementById('withdraw-phone').value;
        }
        
        if (!destination) {
            showToast('Ingrese el número de destino', 'warning');
            return;
        }
        
        // Mostrar estado procesando
        document.getElementById('withdraw-form').classList.add('hidden');
        document.getElementById('withdraw-processing').classList.remove('hidden');
        
        try {
            const response = await fetch(`${CONFIG.API_URL}/api/v1/wallet/withdraw`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ amount, method, destination })
            });
            
            const data = await response.json();
            
            if (data.success) {
                document.getElementById('withdraw-processing').classList.add('hidden');
                document.getElementById('withdraw-success').classList.remove('hidden');
                
                document.getElementById('withdraw-ticket-number').textContent = data.ticket_id;
                document.getElementById('escrow-amount').textContent = `${data.amount} LK`;
                
                // Actualizar balance en el estado
                state.user.balance = parseFloat(data.available_balance);
                updateBalanceDisplay();
                
                showToast('Solicitud de retiro enviada', 'success');
            } else {
                throw new Error(data.detail || 'Error al procesar retiro');
            }
        } catch (error) {
            document.getElementById('withdraw-processing').classList.add('hidden');
            document.getElementById('withdraw-form').classList.remove('hidden');
            showToast(error.message, 'error');
        }
    },
    
    reset() {
        document.getElementById('withdraw-form').classList.remove('hidden');
        document.getElementById('withdraw-processing').classList.add('hidden');
        document.getElementById('withdraw-success').classList.add('hidden');
        
        document.getElementById('withdraw-amount').value = '';
        document.getElementById('withdraw-method').value = '';
        document.getElementById('withdraw-phone').value = '';
        document.getElementById('withdraw-cci').value = '';
        
        this.updateSummary();
    }
};


// =============================================================================
// MÓDULO: COPIAR AL PORTAPAPELES
// =============================================================================

const CopyModule = {
    init() {
        document.querySelectorAll('.btn-copy-number').forEach(btn => {
            btn.addEventListener('click', async () => {
                const text = btn.dataset.copy;
                try {
                    await navigator.clipboard.writeText(text);
                    const originalText = btn.textContent;
                    btn.textContent = '✓';
                    btn.style.color = 'var(--success)';
                    setTimeout(() => {
                        btn.textContent = originalText;
                        btn.style.color = '';
                    }, 1500);
                    showToast('Copiado al portapapeles', 'success');
                } catch (err) {
                    showToast('Error al copiar', 'error');
                }
            });
        });
    }
};


// =============================================================================
// INICIALIZACIÓN (ACTUALIZADA)
// =============================================================================

// Funciones de inicialización adicionales para compatibilidad
function initGames() {
    renderGames();
}

function initBetModal() {
    // Configurar modal de apuesta
    document.getElementById('modal-bet-close')?.addEventListener('click', closeBetModal);
    document.getElementById('modal-bet')?.addEventListener('click', (e) => {
        if (e.target.id === 'modal-bet') closeBetModal();
    });
    
    // Configurar opciones de apuesta
    document.querySelectorAll('.bet-option').forEach(btn => {
        btn.addEventListener('click', () => {
            selectBetAmount(parseInt(btn.dataset.amount));
        });
    });
    
    // Input personalizado de apuesta
    document.getElementById('bet-custom-input')?.addEventListener('input', (e) => {
        const amount = parseFloat(e.target.value) || 0;
        if (amount > 0) {
            selectBetAmount(amount);
        }
    });
    
    // Botón de iniciar matchmaking
    document.getElementById('btn-start-matchmaking')?.addEventListener('click', () => {
        closeBetModal();
        startMatchmaking(state.selectedGame, state.selectedBet);
    });
}

function initMatchmaking() {
    // Botón de cancelar matchmaking
    document.getElementById('btn-cancel-matchmaking')?.addEventListener('click', cancelMatchmaking);
    
    // Botones de resultado
    document.getElementById('btn-play-again')?.addEventListener('click', () => {
        openBetModal(state.selectedGame);
    });
    
    document.getElementById('btn-back-lobby')?.addEventListener('click', () => {
        navigateTo('lobby');
    });
}

function updateBalanceDisplay() {
    updateWallet();
}

document.addEventListener('DOMContentLoaded', () => {
    // Ocultar loader
    setTimeout(() => {
        document.getElementById('loader').style.display = 'none';
    }, 500);
    
    // Configurar navegación
    document.querySelectorAll('.nav-link').forEach(link => {
        link.addEventListener('click', (e) => {
            e.preventDefault();
            const view = link.dataset.view;
            if (view) navigateTo(view);
        });
    });
    
    // Inicializar módulos
    initSocket();
    initGames();
    initBetModal();
    initMatchmaking();
    DepositScanner.init();
    WithdrawModule.init();
    CopyModule.init();
    
    // Actualizar balance inicial
    updateBalanceDisplay();
    
    console.log('🚀 Kompite App inicializada');
});
