/**
 * =============================================================================
 * KOMPITE - Motor de Ludo (Cliente)
 * =============================================================================
 * Renderizado del tablero de Ludo en Canvas con comunicación WebSocket.
 * La lógica del juego es autoritativa en el servidor.
 * 
 * Paleta Cyber-Luxury:
 * - Fondo: #121212
 * - Dorado: #D4AF37
 * - Cyan: #00F3FF
 * - Rojo: #FF3366
 * - Verde: #00FF88
 * =============================================================================
 */

// =============================================================================
// CONFIGURACIÓN
// =============================================================================

const LudoConfig = {
    // Colores del tablero (Cyber-Luxury)
    COLORS: {
        BOARD_BG: '#121212',
        BOARD_BORDER: '#333333',
        CELL_LIGHT: '#1a1a1a',
        CELL_DARK: '#252525',
        SAFE_ZONE: '#2a2a2a',
        
        // Colores de jugadores (neón)
        RED: '#FF3366',
        BLUE: '#00F3FF',
        GREEN: '#00FF88',
        YELLOW: '#D4AF37',
        
        // UI
        HIGHLIGHT: '#00F3FF',
        GOLD: '#D4AF37',
        TEXT: '#FFFFFF',
        TEXT_DIM: '#666666',
    },
    
    // Dimensiones
    CELL_SIZE: 40,
    PIECE_RADIUS: 15,
    BOARD_PADDING: 20,
    
    // Animación
    ANIMATION_SPEED: 300,    // ms por casilla
    DICE_SPIN_TIME: 1000,    // ms de animación de dado
};


// =============================================================================
// CLASE: TABLERO DE LUDO
// =============================================================================

class LudoBoard {
    /**
     * Constructor del tablero
     * @param {string|HTMLCanvasElement} canvasOrId - ID del canvas o elemento canvas
     */
    constructor(canvasOrId) {
        // Aceptar tanto un ID (string) como un elemento canvas directamente
        if (typeof canvasOrId === 'string') {
            this.canvas = document.getElementById(canvasOrId);
        } else {
            this.canvas = canvasOrId;
        }
        
        if (!this.canvas) {
            throw new Error('Canvas no encontrado');
        }
        
        this.ctx = this.canvas.getContext('2d');
        
        // Estado del juego (sincronizado con servidor)
        this.gameState = null;
        this.myColor = null;
        this.myUserId = null;
        
        // Animaciones
        this.animating = false;
        this.diceAnimating = false;
        this.currentDiceValue = null;
        
        // Piezas seleccionables
        this.selectablePieces = [];
        this.hoveredPiece = null;
        
        // Dimensiones calculadas
        this.boardSize = 0;
        this.cellSize = 0;
        
        // Posiciones del tablero (52 casillas + zonas seguras)
        this.cellPositions = [];
        this.homePositions = {};      // Posiciones de las casas (bases)
        this.safeZonePositions = {};  // Zonas de llegada
        
        this._initDimensions();
        this._calculatePositions();
        this._setupEventListeners();
    }
    
    // -------------------------------------------------------------------------
    // INICIALIZACIÓN
    // -------------------------------------------------------------------------
    
    _initDimensions() {
        // Hacer el canvas responsivo
        const container = this.canvas.parentElement;
        const size = Math.min(container.clientWidth, container.clientHeight, 600);
        
        this.canvas.width = size;
        this.canvas.height = size;
        
        this.boardSize = size - LudoConfig.BOARD_PADDING * 2;
        this.cellSize = this.boardSize / 15; // Tablero de 15x15
    }
    
    _calculatePositions() {
        const cs = this.cellSize;
        const offset = LudoConfig.BOARD_PADDING;
        
        // El tablero de Ludo tiene 52 casillas en el circuito principal
        // Las casas ocupan 6x6 celdas en cada esquina
        // El camino tiene 3 filas/columnas de ancho en el medio
        
        // Generar posiciones del camino principal
        // Usando un enfoque más simple: definir las 52 posiciones manualmente
        this.cellPositions = [];
        
        // El camino rodea el tablero en forma de cruz
        // Empezamos en la posición de salida del ROJO (columna 6, fila 1)
        
        const pathCoords = [];
        
        // Segmento 1: Columna 6, filas 0-5 (brazo superior, lado izquierdo)
        for (let i = 0; i < 6; i++) pathCoords.push({x: 6, y: i});
        // Segmento 2: Fila 6, columnas 5-0 (brazo izquierdo, lado superior)  
        for (let i = 5; i >= 0; i--) pathCoords.push({x: i, y: 6});
        // Segmento 3: Columna 0-5, fila 7 (entrada verde)
        pathCoords.push({x: 0, y: 7});
        // Segmento 4: Fila 8, columnas 0-5 (brazo izquierdo, lado inferior)
        for (let i = 0; i < 6; i++) pathCoords.push({x: i, y: 8});
        // Segmento 5: Columna 6, filas 9-14 (brazo inferior, lado izquierdo)
        for (let i = 9; i < 15; i++) pathCoords.push({x: 6, y: i});
        // Segmento 6: Fila 14, entrada amarilla
        pathCoords.push({x: 7, y: 14});
        // Segmento 7: Columna 8, filas 14-9 (brazo inferior, lado derecho)
        for (let i = 14; i >= 9; i--) pathCoords.push({x: 8, y: i});
        // Segmento 8: Fila 8, columnas 9-14 (brazo derecho, lado inferior)
        for (let i = 9; i < 15; i++) pathCoords.push({x: i, y: 8});
        // Segmento 9: Columna 14, entrada azul
        pathCoords.push({x: 14, y: 7});
        // Segmento 10: Fila 6, columnas 14-9 (brazo derecho, lado superior)
        for (let i = 14; i >= 9; i--) pathCoords.push({x: i, y: 6});
        // Segmento 11: Columna 8, filas 5-0 (brazo superior, lado derecho)
        for (let i = 5; i >= 0; i--) pathCoords.push({x: 8, y: i});
        // Segmento 12: Fila 0, entrada roja (vuelve al inicio)
        pathCoords.push({x: 7, y: 0});
        
        // Convertir a posiciones de canvas (solo las primeras 52)
        this.cellPositions = pathCoords.slice(0, 52).map((pos, i) => ({
            x: offset + pos.x * cs + cs / 2,
            y: offset + pos.y * cs + cs / 2,
            index: i
        }));
        
        // Posiciones de las casas (esquinas) - cada casa ocupa 6x6 celdas
        const homeSize = cs * 6;
        this.homePositions = {
            RED: { x: offset, y: offset, color: LudoConfig.COLORS.RED },
            BLUE: { x: offset + cs * 9, y: offset, color: LudoConfig.COLORS.BLUE },
            GREEN: { x: offset, y: offset + cs * 9, color: LudoConfig.COLORS.GREEN },
            YELLOW: { x: offset + cs * 9, y: offset + cs * 9, color: LudoConfig.COLORS.YELLOW },
        };
        
        // Posiciones de las zonas de llegada (centro)
        this.safeZonePositions = {
            RED: [],
            BLUE: [],
            YELLOW: [],
            GREEN: [],
        };
        
        // Generar posiciones de zona segura para cada color
        for (let i = 0; i < 6; i++) {
            this.safeZonePositions.RED.push({
                x: offset + cs * 7.5,
                y: offset + cs * (1 + i),
            });
            this.safeZonePositions.BLUE.push({
                x: offset + cs * (13 - i),
                y: offset + cs * 7.5,
            });
            this.safeZonePositions.YELLOW.push({
                x: offset + cs * 7.5,
                y: offset + cs * (13 - i),
            });
            this.safeZonePositions.GREEN.push({
                x: offset + cs * (1 + i),
                y: offset + cs * 7.5,
            });
        }
    }
    
    _setupEventListeners() {
        this.canvas.addEventListener('click', (e) => this._handleClick(e));
        this.canvas.addEventListener('mousemove', (e) => this._handleMouseMove(e));
        
        window.addEventListener('resize', () => {
            this._initDimensions();
            this._calculatePositions();
            this.render();
        });
    }
    
    // -------------------------------------------------------------------------
    // RENDERING
    // -------------------------------------------------------------------------
    
    render() {
        const ctx = this.ctx;
        const cs = this.cellSize;
        const offset = LudoConfig.BOARD_PADDING;
        
        // Limpiar canvas
        ctx.fillStyle = LudoConfig.COLORS.BOARD_BG;
        ctx.fillRect(0, 0, this.canvas.width, this.canvas.height);
        
        // Dibujar casas (esquinas)
        this._drawHomes();
        
        // Dibujar zona central
        this._drawCenter();
        
        // Dibujar camino principal
        this._drawPath();
        
        // Dibujar zonas de llegada
        this._drawSafeZones();
        
        // Dibujar piezas
        if (this.gameState) {
            this._drawPieces();
        }
        
        // Dibujar dado (si hay valor)
        if (this.currentDiceValue) {
            this._drawDice();
        }
        
        // Dibujar indicador de turno
        this._drawTurnIndicator();
    }
    
    _drawHomes() {
        const ctx = this.ctx;
        const cs = this.cellSize;
        const homeSize = cs * 6;
        
        Object.entries(this.homePositions).forEach(([color, pos]) => {
            // Fondo de la casa
            ctx.fillStyle = pos.color + '20'; // Color con opacidad
            ctx.fillRect(pos.x, pos.y, homeSize, homeSize);
            
            // Borde
            ctx.strokeStyle = pos.color;
            ctx.lineWidth = 2;
            ctx.strokeRect(pos.x, pos.y, homeSize, homeSize);
            
            // Área de piezas (círculo interior)
            const centerX = pos.x + homeSize / 2;
            const centerY = pos.y + homeSize / 2;
            const radius = homeSize / 3;
            
            ctx.beginPath();
            ctx.arc(centerX, centerY, radius, 0, Math.PI * 2);
            ctx.fillStyle = pos.color + '40';
            ctx.fill();
            ctx.strokeStyle = pos.color;
            ctx.stroke();
        });
    }
    
    _drawCenter() {
        const ctx = this.ctx;
        const cs = this.cellSize;
        const offset = LudoConfig.BOARD_PADDING;
        const centerX = offset + cs * 7.5;
        const centerY = offset + cs * 7.5;
        const size = cs * 3;
        
        // Triángulos del centro (zona de llegada)
        const colors = [
            LudoConfig.COLORS.RED,
            LudoConfig.COLORS.BLUE,
            LudoConfig.COLORS.YELLOW,
            LudoConfig.COLORS.GREEN,
        ];
        
        colors.forEach((color, i) => {
            ctx.save();
            ctx.translate(centerX, centerY);
            ctx.rotate((i * Math.PI) / 2);
            
            ctx.beginPath();
            ctx.moveTo(0, 0);
            ctx.lineTo(-size / 2, -size);
            ctx.lineTo(size / 2, -size);
            ctx.closePath();
            
            ctx.fillStyle = color + '60';
            ctx.fill();
            ctx.strokeStyle = color;
            ctx.lineWidth = 1;
            ctx.stroke();
            
            ctx.restore();
        });
    }
    
    _drawPath() {
        const ctx = this.ctx;
        
        // Dibujar casillas del camino principal
        this.cellPositions.forEach((pos, i) => {
            const isSafe = LudoConfig.SAFE_POSITIONS?.includes(i);
            
            ctx.beginPath();
            ctx.arc(pos.x, pos.y, this.cellSize / 2.5, 0, Math.PI * 2);
            
            if (isSafe) {
                ctx.fillStyle = LudoConfig.COLORS.GOLD + '40';
                ctx.strokeStyle = LudoConfig.COLORS.GOLD;
            } else {
                ctx.fillStyle = LudoConfig.COLORS.CELL_DARK;
                ctx.strokeStyle = LudoConfig.COLORS.BOARD_BORDER;
            }
            
            ctx.fill();
            ctx.lineWidth = 1;
            ctx.stroke();
        });
    }
    
    _drawSafeZones() {
        const ctx = this.ctx;
        
        Object.entries(this.safeZonePositions).forEach(([color, positions]) => {
            const colorHex = LudoConfig.COLORS[color];
            
            positions.forEach((pos, i) => {
                ctx.beginPath();
                ctx.arc(pos.x, pos.y, this.cellSize / 2.5, 0, Math.PI * 2);
                ctx.fillStyle = colorHex + '40';
                ctx.fill();
                ctx.strokeStyle = colorHex;
                ctx.lineWidth = 1;
                ctx.stroke();
            });
        });
    }
    
    _drawPieces() {
        if (!this.gameState || !this.gameState.players) return;
        
        const ctx = this.ctx;
        
        Object.values(this.gameState.players).forEach(player => {
            const colorHex = LudoConfig.COLORS[player.color];
            
            player.pieces.forEach(piece => {
                const pos = this._getPiecePosition(piece);
                if (!pos) return;
                
                const isSelectable = this.selectablePieces.some(
                    p => p.piece_id === piece.piece_id && p.owner === piece.owner
                );
                const isHovered = this.hoveredPiece?.piece_id === piece.piece_id &&
                                  this.hoveredPiece?.owner === piece.owner;
                
                // Dibujar pieza
                ctx.beginPath();
                ctx.arc(pos.x, pos.y, LudoConfig.PIECE_RADIUS, 0, Math.PI * 2);
                
                // Gradiente para efecto 3D
                const gradient = ctx.createRadialGradient(
                    pos.x - 5, pos.y - 5, 0,
                    pos.x, pos.y, LudoConfig.PIECE_RADIUS
                );
                gradient.addColorStop(0, colorHex);
                gradient.addColorStop(1, this._darkenColor(colorHex, 0.3));
                
                ctx.fillStyle = gradient;
                ctx.fill();
                
                // Borde y glow si es seleccionable
                if (isSelectable) {
                    ctx.shadowColor = LudoConfig.COLORS.HIGHLIGHT;
                    ctx.shadowBlur = isHovered ? 20 : 10;
                    ctx.strokeStyle = LudoConfig.COLORS.HIGHLIGHT;
                    ctx.lineWidth = 3;
                } else {
                    ctx.shadowBlur = 0;
                    ctx.strokeStyle = '#FFFFFF40';
                    ctx.lineWidth = 2;
                }
                ctx.stroke();
                ctx.shadowBlur = 0;
                
                // Número de pieza
                ctx.fillStyle = '#FFFFFF';
                ctx.font = 'bold 12px Orbitron';
                ctx.textAlign = 'center';
                ctx.textBaseline = 'middle';
                ctx.fillText(piece.piece_id + 1, pos.x, pos.y);
            });
        });
    }
    
    _drawDice() {
        const ctx = this.ctx;
        const x = this.canvas.width / 2;
        const y = this.canvas.height / 2;
        const size = 60;
        
        if (this.diceAnimating) {
            // Animación de dado girando
            const randomValue = Math.floor(Math.random() * 6) + 1;
            this._drawDiceFace(x, y, size, randomValue);
        } else if (this.currentDiceValue) {
            this._drawDiceFace(x, y, size, this.currentDiceValue);
        }
    }
    
    _drawDiceFace(x, y, size, value) {
        const ctx = this.ctx;
        
        // Fondo del dado
        ctx.fillStyle = '#1a1a1a';
        ctx.strokeStyle = LudoConfig.COLORS.GOLD;
        ctx.lineWidth = 3;
        
        // Dado redondeado
        const radius = 10;
        ctx.beginPath();
        ctx.roundRect(x - size/2, y - size/2, size, size, radius);
        ctx.fill();
        ctx.stroke();
        
        // Puntos del dado
        ctx.fillStyle = LudoConfig.COLORS.GOLD;
        const dotRadius = 6;
        const positions = this._getDotPositions(value, x, y, size);
        
        positions.forEach(pos => {
            ctx.beginPath();
            ctx.arc(pos.x, pos.y, dotRadius, 0, Math.PI * 2);
            ctx.fill();
        });
    }
    
    _getDotPositions(value, cx, cy, size) {
        const offset = size / 4;
        const positions = [];
        
        const left = cx - offset;
        const right = cx + offset;
        const top = cy - offset;
        const bottom = cy + offset;
        
        switch (value) {
            case 1:
                positions.push({ x: cx, y: cy });
                break;
            case 2:
                positions.push({ x: left, y: top }, { x: right, y: bottom });
                break;
            case 3:
                positions.push({ x: left, y: top }, { x: cx, y: cy }, { x: right, y: bottom });
                break;
            case 4:
                positions.push(
                    { x: left, y: top }, { x: right, y: top },
                    { x: left, y: bottom }, { x: right, y: bottom }
                );
                break;
            case 5:
                positions.push(
                    { x: left, y: top }, { x: right, y: top },
                    { x: cx, y: cy },
                    { x: left, y: bottom }, { x: right, y: bottom }
                );
                break;
            case 6:
                positions.push(
                    { x: left, y: top }, { x: right, y: top },
                    { x: left, y: cy }, { x: right, y: cy },
                    { x: left, y: bottom }, { x: right, y: bottom }
                );
                break;
        }
        
        return positions;
    }
    
    _drawTurnIndicator() {
        if (!this.gameState) return;
        
        const ctx = this.ctx;
        const isMyTurn = this.gameState.current_player === this.myUserId;
        
        // Indicador en la parte superior
        const text = isMyTurn ? '¡Tu turno!' : 'Turno del oponente';
        const color = isMyTurn ? LudoConfig.COLORS.GOLD : LudoConfig.COLORS.TEXT_DIM;
        
        ctx.fillStyle = color;
        ctx.font = 'bold 16px Orbitron';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'top';
        ctx.fillText(text, this.canvas.width / 2, 5);
    }
    
    // -------------------------------------------------------------------------
    // POSICIONAMIENTO
    // -------------------------------------------------------------------------
    
    _getPiecePosition(piece) {
        if (piece.state === 'HOME') {
            // En la base
            return this._getHomePosition(piece.owner, piece.piece_id);
        } else if (piece.state === 'ACTIVE') {
            // En el tablero
            return this.cellPositions[piece.position];
        } else if (piece.state === 'SAFE_ZONE') {
            // En zona de llegada
            return this.safeZonePositions[piece.owner][piece.safe_zone_pos];
        } else if (piece.state === 'FINISHED') {
            // En el centro
            return this._getFinishedPosition(piece.owner, piece.piece_id);
        }
        return null;
    }
    
    _getHomePosition(color, pieceId) {
        const home = this.homePositions[color];
        const size = this.cellSize * 6;
        const centerX = home.x + size / 2;
        const centerY = home.y + size / 2;
        const radius = size / 4;
        
        // Distribuir en círculo
        const angle = (pieceId * Math.PI / 2) + Math.PI / 4;
        return {
            x: centerX + Math.cos(angle) * radius,
            y: centerY + Math.sin(angle) * radius
        };
    }
    
    _getFinishedPosition(color, pieceId) {
        const offset = LudoConfig.BOARD_PADDING;
        const cs = this.cellSize;
        const centerX = offset + cs * 7.5;
        const centerY = offset + cs * 7.5;
        
        // Pequeño offset por pieza
        const angle = pieceId * (Math.PI / 2);
        const radius = 15;
        return {
            x: centerX + Math.cos(angle) * radius,
            y: centerY + Math.sin(angle) * radius
        };
    }
    
    // -------------------------------------------------------------------------
    // INTERACCIÓN
    // -------------------------------------------------------------------------
    
    _handleClick(e) {
        const rect = this.canvas.getBoundingClientRect();
        const x = e.clientX - rect.left;
        const y = e.clientY - rect.top;
        
        // Verificar si se hizo clic en una pieza seleccionable
        for (const piece of this.selectablePieces) {
            const pos = this._getPiecePosition(piece);
            if (!pos) continue;
            
            const dist = Math.sqrt((x - pos.x) ** 2 + (y - pos.y) ** 2);
            if (dist <= LudoConfig.PIECE_RADIUS + 5) {
                this._onPieceSelected(piece);
                return;
            }
        }
    }
    
    _handleMouseMove(e) {
        const rect = this.canvas.getBoundingClientRect();
        const x = e.clientX - rect.left;
        const y = e.clientY - rect.top;
        
        let newHovered = null;
        
        for (const piece of this.selectablePieces) {
            const pos = this._getPiecePosition(piece);
            if (!pos) continue;
            
            const dist = Math.sqrt((x - pos.x) ** 2 + (y - pos.y) ** 2);
            if (dist <= LudoConfig.PIECE_RADIUS + 5) {
                newHovered = piece;
                break;
            }
        }
        
        if (newHovered !== this.hoveredPiece) {
            this.hoveredPiece = newHovered;
            this.canvas.style.cursor = newHovered ? 'pointer' : 'default';
            this.render();
        }
    }
    
    _onPieceSelected(piece) {
        console.log('Pieza seleccionada:', piece);
        // Emitir evento al controlador del juego
        if (this.onPieceSelect) {
            this.onPieceSelect(piece.piece_id);
        }
    }
    
    // -------------------------------------------------------------------------
    // ANIMACIONES
    // -------------------------------------------------------------------------
    
    async animateDiceRoll(finalValue) {
        this.diceAnimating = true;
        const startTime = Date.now();
        
        const animate = () => {
            const elapsed = Date.now() - startTime;
            
            if (elapsed < LudoConfig.DICE_SPIN_TIME) {
                this.render();
                requestAnimationFrame(animate);
            } else {
                this.diceAnimating = false;
                this.currentDiceValue = finalValue;
                this.render();
            }
        };
        
        animate();
    }
    
    async animatePieceMove(piece, fromPos, toPos) {
        // Animación suave de movimiento
        return new Promise(resolve => {
            const startPos = this._getPiecePosition({ ...piece, position: fromPos });
            const endPos = this._getPiecePosition({ ...piece, position: toPos });
            
            if (!startPos || !endPos) {
                resolve();
                return;
            }
            
            const duration = LudoConfig.ANIMATION_SPEED;
            const startTime = Date.now();
            
            const animate = () => {
                const elapsed = Date.now() - startTime;
                const progress = Math.min(elapsed / duration, 1);
                
                // Easing
                const ease = 1 - Math.pow(1 - progress, 3);
                
                // TODO: Actualizar posición temporal de la pieza
                
                if (progress < 1) {
                    this.render();
                    requestAnimationFrame(animate);
                } else {
                    this.render();
                    resolve();
                }
            };
            
            animate();
        });
    }
    
    // -------------------------------------------------------------------------
    // UTILIDADES
    // -------------------------------------------------------------------------
    
    _darkenColor(hex, factor) {
        const r = parseInt(hex.slice(1, 3), 16);
        const g = parseInt(hex.slice(3, 5), 16);
        const b = parseInt(hex.slice(5, 7), 16);
        
        return `rgb(${Math.floor(r * (1 - factor))}, ${Math.floor(g * (1 - factor))}, ${Math.floor(b * (1 - factor))})`;
    }
    
    // -------------------------------------------------------------------------
    // API PÚBLICA
    // -------------------------------------------------------------------------
    
    setGameState(state) {
        this.gameState = state;
        this.render();
    }
    
    setMyInfo(userId, color) {
        this.myUserId = userId;
        this.myColor = color;
    }
    
    setSelectablePieces(pieces) {
        this.selectablePieces = pieces || [];
        this.render();
    }
    
    showDice(value) {
        this.currentDiceValue = value;
        this.render();
    }
    
    hideDice() {
        this.currentDiceValue = null;
        this.render();
    }
}


// =============================================================================
// CONTROLADOR DE JUEGO (WEBSOCKET)
// =============================================================================

class LudoGameController {
    /**
     * Constructor del controlador de Ludo
     * @param {string} canvasId - ID del elemento canvas
     * @param {object} socket - Instancia de Socket.IO
     * @param {string} matchId - ID de la partida (opcional)
     */
    constructor(canvasId, socket, matchId = null) {
        // Verificar que el socket es válido
        if (!socket || typeof socket.on !== 'function') {
            console.error('[LUDO] Socket inválido recibido:', socket);
            throw new Error('Socket.IO inválido');
        }
        
        this.socket = socket;
        this.matchId = matchId;
        this.myUserId = null;
        
        // Crear el board a partir del canvas ID
        const canvas = document.getElementById(canvasId);
        if (!canvas) {
            throw new Error(`Canvas con ID "${canvasId}" no encontrado`);
        }
        this.board = new LudoBoard(canvas);
        
        this._setupSocketListeners();
        this._setupBoardCallbacks();
        
        console.log('[LUDO] Controlador inicializado para partida:', matchId);
    }
    
    _setupSocketListeners() {
        // Partida encontrada
        this.socket.on('match:ready', (data) => {
            this.matchId = data.match_id;
            this.myUserId = data.your_user_id;
            this.board.setMyInfo(data.your_user_id, data.your_color);
            console.log('Match ready:', data);
        });
        
        // Juego iniciado
        this.socket.on('ludo:game_started', (data) => {
            console.log('Game started:', data);
            if (data.first_player === this.myUserId) {
                this._showMessage('¡Tú empiezas! Tira el dado.');
            } else {
                this._showMessage('Tu oponente empieza.');
            }
        });
        
        // Estado actualizado
        this.socket.on('ludo:state_update', (data) => {
            this.board.setGameState(data.state);
        });
        
        // Dado lanzado
        this.socket.on('ludo:dice_rolled', (data) => {
            this.board.animateDiceRoll(data.roll.value).then(() => {
                if (data.available_moves && data.available_moves.length > 0) {
                    // Convertir a formato de piezas seleccionables
                    const pieces = data.available_moves.map(m => ({
                        piece_id: m.piece_id,
                        owner: this.board.myColor,
                        position: m.from
                    }));
                    this.board.setSelectablePieces(pieces);
                } else {
                    this.board.setSelectablePieces([]);
                }
            });
        });
        
        // Pieza movida
        this.socket.on('ludo:piece_moved', (data) => {
            this.board.setSelectablePieces([]);
            this.board.hideDice();
            
            if (data.move.capture) {
                this._showMessage(`¡Captura! ${data.move.capture.player}`);
            }
            if (data.move.finished) {
                this._showMessage('¡Pieza llegó a casa!');
            }
            if (data.roll_again) {
                this._showMessage('¡Tira de nuevo!');
            }
        });
        
        // Juego terminado
        this.socket.on('ludo:game_over', (data) => {
            const isWinner = data.winner === this.myUserId;
            this._showResult(isWinner, data);
        });
    }
    
    _setupBoardCallbacks() {
        this.board.onPieceSelect = (pieceId) => {
            this.movePiece(pieceId);
        };
    }
    
    // -------------------------------------------------------------------------
    // ACCIONES DEL JUGADOR
    // -------------------------------------------------------------------------
    
    rollDice() {
        // Generar client seed para Provably Fair
        const clientSeed = this._generateClientSeed();
        
        this.socket.emit('ludo:roll_dice', {
            match_id: this.matchId,
            client_seed: clientSeed
        });
    }
    
    // Alias para compatibilidad con app.js
    requestDiceRoll() {
        this.rollDice();
    }
    
    movePiece(pieceId) {
        this.socket.emit('ludo:move_piece', {
            match_id: this.matchId,
            piece_id: pieceId
        });
    }
    
    _generateClientSeed() {
        const array = new Uint8Array(16);
        crypto.getRandomValues(array);
        return Array.from(array, b => b.toString(16).padStart(2, '0')).join('');
    }
    
    // -------------------------------------------------------------------------
    // UI
    // -------------------------------------------------------------------------
    
    _showMessage(msg) {
        // TODO: Implementar notificación visual
        console.log('Game message:', msg);
    }
    
    _showResult(isWinner, data) {
        // TODO: Navegar a vista de resultado
        console.log('Game over:', isWinner ? 'YOU WIN!' : 'You lost', data);
    }
}


// =============================================================================
// EXPORTAR PARA USO GLOBAL
// =============================================================================

window.LudoBoard = LudoBoard;
window.LudoGameController = LudoGameController;
