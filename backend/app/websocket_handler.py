"""
=============================================================================
KOMPITE - Manejador de WebSockets (Socket.IO)
=============================================================================
Comunicación bidireccional en tiempo real para partidas.
Implementa la FSM: MATCHMAKING -> LOCKED -> IN_PROGRESS -> VALIDATION

Ref: Documentación sección 2.1 - Ciclo de Vida de una Partida
=============================================================================
"""

import asyncio
import hashlib
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, TYPE_CHECKING
from uuid import UUID, uuid4

import socketio

from .jitter_detector import JitterDetector, jitter_detector
from .security import LKShield, PlayerSecurityProfile, lk_shield
from .game_engine import RakeCalculator, LKBot, TreasuryLedger, treasury_ledger

# Import condicional para evitar importación circular
if TYPE_CHECKING:
    from .ludo_engine import LudoEngine


# =============================================================================
# CONFIGURACIÓN DEL SOCKET
# =============================================================================

class SocketConfig:
    """Configuración del servidor de WebSockets."""
    
    HEARTBEAT_INTERVAL = 3           # Segundos entre heartbeats
    HEARTBEAT_TIMEOUT = 10           # Timeout para considerar desconexión
    GRACE_PERIOD = 45                # Segundos de gracia para reconexión
    MAX_PLAYERS_PER_MATCH = 2        # Máximo de jugadores por partida
    MATCHMAKING_TIMEOUT = 60         # Timeout para matchmaking
    LOCK_TIMEOUT = 30                # Timeout para confirmar LOCKED
    ESCROW_CONFIRMATION_TIMEOUT = 10 # Timeout para confirmar escrow
    ENABLE_BOT_MATCHMAKING = True    # Habilitar LK_Bot para pruebas


# =============================================================================
# ESTRUCTURAS DE DATOS
# =============================================================================

class MatchState(Enum):
    """Estados de la máquina de estados finita (FSM)."""
    MATCHMAKING = "MATCHMAKING"
    LOCKED = "LOCKED"
    IN_PROGRESS = "IN_PROGRESS"
    VALIDATION = "VALIDATION"
    SETTLEMENT = "SETTLEMENT"
    COMPLETED = "COMPLETED"
    DISPUTED = "DISPUTED"
    CANCELLED = "CANCELLED"


@dataclass
class PlayerConnection:
    """Información de conexión de un jugador."""
    user_id: UUID
    sid: str  # Socket ID
    connected_at: float
    last_heartbeat: float
    
    # Estado
    is_ready: bool = False
    is_connected: bool = True
    
    # Metadata
    ip_address: str = ""
    device_fingerprint: str = ""
    
    # Escrow
    escrow_confirmed: bool = False
    balance_at_lock: Decimal = Decimal("0")


@dataclass
class MatchRoom:
    """Sala de partida."""
    match_id: UUID
    game_type: str
    bet_amount: Decimal
    
    # Estado FSM
    state: MatchState = MatchState.MATCHMAKING
    
    # Jugadores
    players: Dict[UUID, PlayerConnection] = field(default_factory=dict)
    max_players: int = 2
    
    # Timing
    created_at: float = field(default_factory=time.time)
    locked_at: Optional[float] = None
    started_at: Optional[float] = None
    
    # Integridad
    session_id: str = field(default_factory=lambda: secrets.token_hex(32))
    initial_state_hash: str = ""
    
    # Log de movimientos
    moves_log: List[Dict] = field(default_factory=list)
    move_sequence: int = 0
    
    # Seeds para Provably Fair
    server_seed: str = field(default_factory=lambda: secrets.token_hex(32))
    client_seeds: Dict[UUID, str] = field(default_factory=dict)
    
    # Bot match flag
    is_bot_match: bool = False
    
    # Ledger entry ID para liquidación
    ledger_entry_id: Optional[str] = None
    
    def is_full(self) -> bool:
        return len(self.players) >= self.max_players
    
    def all_players_ready(self) -> bool:
        # Bot siempre está listo
        for uid, player in self.players.items():
            if LKBot.is_bot_user(str(uid)):
                continue
            if not player.is_ready:
                return False
        return True
    
    def all_escrow_confirmed(self) -> bool:
        # Bot siempre confirma escrow
        for uid, player in self.players.items():
            if LKBot.is_bot_user(str(uid)):
                continue
            if not player.escrow_confirmed:
                return False
        return True
    
    def get_room_name(self) -> str:
        return f"match_{self.match_id}"


@dataclass
class MatchmakingEntry:
    """Entrada en la cola de matchmaking."""
    user_id: UUID
    sid: str
    game_type: str
    bet_amount: Decimal
    security_profile: PlayerSecurityProfile
    queued_at: float = field(default_factory=time.time)


# =============================================================================
# GESTOR DE PARTIDAS
# =============================================================================

class MatchManager:
    """
    Gestiona las partidas activas y el matchmaking.
    Implementa bloqueo distribuido para evitar race conditions.
    """
    
    def __init__(self):
        self.active_matches: Dict[UUID, MatchRoom] = {}
        self.player_to_match: Dict[UUID, UUID] = {}  # user_id -> match_id
        self.sid_to_user: Dict[str, UUID] = {}       # socket_id -> user_id
        
        # Cola de matchmaking por tipo de juego y monto
        self.matchmaking_queue: Dict[str, List[MatchmakingEntry]] = {}
        
        # Locks (en producción usar Redis)
        self._locks: Set[str] = set()
    
    def _get_queue_key(self, game_type: str, bet_amount: Decimal) -> str:
        """Genera clave única para la cola de matchmaking."""
        return f"{game_type}:{bet_amount}"
    
    async def acquire_lock(self, key: str, timeout: float = 5.0) -> bool:
        """Adquiere un lock distribuido (simplificado, usar Redis en prod)."""
        lock_key = f"lock:{key}"
        if lock_key in self._locks:
            return False
        self._locks.add(lock_key)
        return True
    
    async def release_lock(self, key: str):
        """Libera un lock distribuido."""
        lock_key = f"lock:{key}"
        self._locks.discard(lock_key)
    
    async def add_to_matchmaking(
        self,
        entry: MatchmakingEntry
    ) -> Optional[MatchRoom]:
        """
        Agrega un jugador a la cola de matchmaking.
        Si encuentra match, crea la sala y retorna.
        Si está habilitado el bot y no hay oponentes, matchea con LK_Bot.
        """
        queue_key = self._get_queue_key(entry.game_type, entry.bet_amount)
        
        # Intentar adquirir lock para esta cola
        if not await self.acquire_lock(queue_key):
            return None
        
        try:
            # Inicializar cola si no existe
            if queue_key not in self.matchmaking_queue:
                self.matchmaking_queue[queue_key] = []
            
            queue = self.matchmaking_queue[queue_key]
            
            # Buscar oponente compatible
            for i, opponent in enumerate(queue):
                # No matchear consigo mismo
                if opponent.user_id == entry.user_id:
                    continue
                
                # Crear partida
                match_room = await self._create_match(
                    game_type=entry.game_type,
                    bet_amount=entry.bet_amount,
                    players=[entry, opponent]
                )
                
                # Remover oponente de la cola
                queue.pop(i)
                
                return match_room
            
            # No hay oponente humano - intentar matchear con LK_Bot si está habilitado
            if SocketConfig.ENABLE_BOT_MATCHMAKING:
                bot_entry = self._create_bot_entry(entry.game_type, entry.bet_amount)
                if bot_entry:
                    match_room = await self._create_match(
                        game_type=entry.game_type,
                        bet_amount=entry.bet_amount,
                        players=[entry, bot_entry],
                        is_bot_match=True
                    )
                    print(f"[MATCHMAKING] Created bot match for {entry.user_id}")
                    return match_room
            
            # No hay oponente ni bot, agregar a la cola
            queue.append(entry)
            return None
            
        finally:
            await self.release_lock(queue_key)
    
    def _create_bot_entry(self, game_type: str, bet_amount: Decimal) -> Optional[MatchmakingEntry]:
        """
        Crea una entrada de matchmaking para el LK_Bot.
        """
        bot_profile = LKBot.get_bot_profile()
        
        # Crear perfil de seguridad para el bot
        security_profile = PlayerSecurityProfile(
            user_id=UUID(bot_profile["user_id"]),
            username=bot_profile["username"],
            current_ip="127.0.0.1",
            device_fingerprint="LK_BOT_DEVICE",
            trust_score=100,
            lkoins_balance=Decimal(bot_profile["balance"])
        )
        
        return MatchmakingEntry(
            user_id=UUID(bot_profile["user_id"]),
            sid=f"bot_{bot_profile['user_id']}",
            game_type=game_type,
            bet_amount=bet_amount,
            joined_at=time.time(),
            security_profile=security_profile
        )
    
    async def remove_from_matchmaking(self, user_id: UUID, game_type: str, bet_amount: Decimal):
        """Remueve un jugador de la cola de matchmaking."""
        queue_key = self._get_queue_key(game_type, bet_amount)
        
        if queue_key in self.matchmaking_queue:
            self.matchmaking_queue[queue_key] = [
                e for e in self.matchmaking_queue[queue_key]
                if e.user_id != user_id
            ]
    
    async def _create_match(
        self,
        game_type: str,
        bet_amount: Decimal,
        players: List[MatchmakingEntry],
        is_bot_match: bool = False
    ) -> MatchRoom:
        """Crea una nueva sala de partida."""
        match_id = uuid4()
        
        room = MatchRoom(
            match_id=match_id,
            game_type=game_type,
            bet_amount=bet_amount
        )
        
        # Guardar si es partida con bot
        room.is_bot_match = is_bot_match
        
        # Agregar jugadores
        for entry in players:
            connection = PlayerConnection(
                user_id=entry.user_id,
                sid=entry.sid,
                connected_at=time.time(),
                last_heartbeat=time.time(),
                ip_address=entry.security_profile.current_ip,
                device_fingerprint=entry.security_profile.device_fingerprint or ""
            )
            room.players[entry.user_id] = connection
            self.player_to_match[entry.user_id] = match_id
            self.sid_to_user[entry.sid] = entry.user_id
        
        self.active_matches[match_id] = room
        return room
    
    def get_match(self, match_id: UUID) -> Optional[MatchRoom]:
        """Obtiene una partida por ID."""
        return self.active_matches.get(match_id)
    
    def get_match_by_player(self, user_id: UUID) -> Optional[MatchRoom]:
        """Obtiene la partida de un jugador."""
        match_id = self.player_to_match.get(user_id)
        if match_id:
            return self.active_matches.get(match_id)
        return None
    
    def get_user_by_sid(self, sid: str) -> Optional[UUID]:
        """Obtiene el user_id por socket ID."""
        return self.sid_to_user.get(sid)
    
    async def transition_state(
        self,
        match_id: UUID,
        new_state: MatchState
    ) -> bool:
        """
        Transiciona el estado de una partida.
        Valida que la transición sea válida según la FSM.
        """
        room = self.active_matches.get(match_id)
        if not room:
            return False
        
        # Validar transiciones permitidas
        valid_transitions = {
            MatchState.MATCHMAKING: [MatchState.LOCKED, MatchState.CANCELLED],
            MatchState.LOCKED: [MatchState.IN_PROGRESS, MatchState.CANCELLED],
            MatchState.IN_PROGRESS: [MatchState.VALIDATION, MatchState.DISPUTED],
            MatchState.VALIDATION: [MatchState.SETTLEMENT, MatchState.DISPUTED],
            MatchState.SETTLEMENT: [MatchState.COMPLETED, MatchState.DISPUTED],
            MatchState.DISPUTED: [MatchState.COMPLETED, MatchState.CANCELLED],
            MatchState.COMPLETED: [],
            MatchState.CANCELLED: []
        }
        
        if new_state not in valid_transitions.get(room.state, []):
            return False
        
        room.state = new_state
        
        # Acciones específicas por estado
        if new_state == MatchState.LOCKED:
            room.locked_at = time.time()
        elif new_state == MatchState.IN_PROGRESS:
            room.started_at = time.time()
        
        return True
    
    async def generate_initial_hash(self, match_id: UUID) -> str:
        """Genera el hash del estado inicial (snapshot de fideicomiso)."""
        room = self.active_matches.get(match_id)
        if not room:
            return ""
        
        # Construir datos para el hash
        data = {
            "match_id": str(room.match_id),
            "session_id": room.session_id,
            "players": {
                str(uid): {
                    "balance_at_lock": str(conn.balance_at_lock),
                    "ip": conn.ip_address,
                    "device": conn.device_fingerprint
                }
                for uid, conn in room.players.items()
            },
            "bet_amount": str(room.bet_amount),
            "timestamp": room.locked_at
        }
        
        import json
        hash_input = json.dumps(data, sort_keys=True)
        room.initial_state_hash = hashlib.sha256(hash_input.encode()).hexdigest()
        
        return room.initial_state_hash
    
    async def record_move(
        self,
        match_id: UUID,
        user_id: UUID,
        move_data: Dict[str, Any]
    ) -> int:
        """
        Registra un movimiento en el log de la partida.
        Retorna el número de secuencia del movimiento.
        """
        room = self.active_matches.get(match_id)
        if not room:
            return -1
        
        room.move_sequence += 1
        
        move_entry = {
            "sequence": room.move_sequence,
            "player_id": str(user_id),
            "timestamp": time.time(),
            "data": move_data
        }
        
        room.moves_log.append(move_entry)
        return room.move_sequence
    
    async def cleanup_match(self, match_id: UUID):
        """Limpia una partida finalizada."""
        room = self.active_matches.get(match_id)
        if room:
            for user_id in room.players:
                self.player_to_match.pop(user_id, None)
            for player in room.players.values():
                self.sid_to_user.pop(player.sid, None)
            del self.active_matches[match_id]


# =============================================================================
# SERVIDOR SOCKET.IO
# =============================================================================

# Crear instancia de Socket.IO con modo async
sio = socketio.AsyncServer(
    async_mode='asgi',
    cors_allowed_origins='*',
    ping_timeout=SocketConfig.HEARTBEAT_TIMEOUT,
    ping_interval=SocketConfig.HEARTBEAT_INTERVAL
)

# Instancia global del gestor de partidas
match_manager = MatchManager()


# =============================================================================
# HANDLERS DE EVENTOS
# =============================================================================

@sio.event
async def connect(sid: str, environ: dict, auth: dict = None):
    """
    Maneja nueva conexión de cliente.
    Requiere autenticación con token JWT.
    """
    print(f"[WS] Nueva conexión: {sid}")
    
    # TODO: Validar token JWT del auth
    # Por ahora, aceptamos todas las conexiones
    
    await sio.emit('connected', {
        'sid': sid,
        'message': 'Conectado a Kompite',
        'server_time': time.time()
    }, room=sid)


@sio.event
async def disconnect(sid: str):
    """
    Maneja desconexión de cliente.
    Si está en partida, aplica lógica de gracia o penalización.
    """
    print(f"[WS] Desconexión: {sid}")
    
    user_id = match_manager.get_user_by_sid(sid)
    if not user_id:
        return
    
    room = match_manager.get_match_by_player(user_id)
    if room and room.state in [MatchState.LOCKED, MatchState.IN_PROGRESS]:
        # Marcar jugador como desconectado
        if user_id in room.players:
            room.players[user_id].is_connected = False
        
        # Notificar a otros jugadores
        await sio.emit('player_disconnected', {
            'player_id': str(user_id),
            'grace_period': SocketConfig.GRACE_PERIOD
        }, room=room.get_room_name(), skip_sid=sid)
        
        # TODO: Iniciar timer de gracia
        # Si no reconecta en GRACE_PERIOD, declarar abandono


@sio.event
async def join_matchmaking(sid: str, data: dict):
    """
    Jugador solicita unirse a matchmaking.
    
    data = {
        'user_id': str,
        'game_type': str,
        'bet_amount': float,
        'security_profile': dict
    }
    """
    try:
        user_id = UUID(data['user_id'])
        game_type = data['game_type']
        bet_amount = Decimal(str(data['bet_amount']))
        
        # Construir perfil de seguridad
        profile_data = data.get('security_profile', {})
        security_profile = PlayerSecurityProfile(
            user_id=user_id,
            trust_score=profile_data.get('trust_score', 100),
            trust_level=profile_data.get('trust_level', 'GREEN'),
            kyc_status=profile_data.get('kyc_status', 'PENDING'),
            is_frozen=profile_data.get('is_frozen', False),
            device_fingerprint=profile_data.get('device_fingerprint'),
            current_ip=profile_data.get('current_ip', ''),
            lkoins_balance=Decimal(str(profile_data.get('lkoins_balance', 0)))
        )
        
        # Verificar con LK-SHIELD
        shield_result = await lk_shield.verify_player(security_profile, bet_amount)
        
        if not shield_result.can_proceed:
            await sio.emit('matchmaking_denied', {
                'reason': shield_result.reason,
                'verdict': shield_result.verdict.value,
                'retry_after': shield_result.retry_after
            }, room=sid)
            return
        
        # Registrar para detección de jitter
        jitter_detector.register_player(user_id)
        
        # Crear entrada de matchmaking
        entry = MatchmakingEntry(
            user_id=user_id,
            sid=sid,
            game_type=game_type,
            bet_amount=bet_amount,
            security_profile=security_profile
        )
        
        # Intentar encontrar match
        room = await match_manager.add_to_matchmaking(entry)
        
        if room:
            # Match encontrado
            await _handle_match_found(room)
        else:
            # En cola de espera
            await sio.emit('matchmaking_queued', {
                'game_type': game_type,
                'bet_amount': str(bet_amount),
                'position': 1  # Simplificado
            }, room=sid)
            
    except Exception as e:
        print(f"[WS] Error en matchmaking: {e}")
        await sio.emit('error', {'message': str(e)}, room=sid)


async def _handle_match_found(room: MatchRoom):
    """Procesa cuando se encuentra un match."""
    # Unir jugadores a la sala
    for player in room.players.values():
        await sio.enter_room(player.sid, room.get_room_name())
    
    # Verificar colusión
    players_list = list(room.players.values())
    if len(players_list) == 2:
        profile1 = PlayerSecurityProfile(
            user_id=players_list[0].user_id,
            trust_score=100,
            trust_level='GREEN',
            kyc_status='VERIFIED',
            is_frozen=False,
            device_fingerprint=players_list[0].device_fingerprint,
            current_ip=players_list[0].ip_address,
            lkoins_balance=players_list[0].balance_at_lock
        )
        profile2 = PlayerSecurityProfile(
            user_id=players_list[1].user_id,
            trust_score=100,
            trust_level='GREEN',
            kyc_status='VERIFIED',
            is_frozen=False,
            device_fingerprint=players_list[1].device_fingerprint,
            current_ip=players_list[1].ip_address,
            lkoins_balance=players_list[1].balance_at_lock
        )
        
        collusion_check = await lk_shield.check_collusion(profile1, profile2)
        
        if collusion_check.is_suspicious and collusion_check.risk_level in ['HIGH', 'CRITICAL']:
            # Cancelar partida por sospecha de colusión
            await sio.emit('match_cancelled', {
                'reason': 'Security check failed',
                'code': 'COLLUSION_SUSPECTED'
            }, room=room.get_room_name())
            await match_manager.cleanup_match(room.match_id)
            return
    
    # Notificar match encontrado
    await sio.emit('match_found', {
        'match_id': str(room.match_id),
        'session_id': room.session_id,
        'game_type': room.game_type,
        'bet_amount': str(room.bet_amount),
        'players': [
            {'user_id': str(uid), 'ready': False}
            for uid in room.players.keys()
        ],
        'server_seed_hash': hashlib.sha256(room.server_seed.encode()).hexdigest()
    }, room=room.get_room_name())


@sio.event
async def player_ready(sid: str, data: dict):
    """
    Jugador confirma que está listo.
    Cuando todos están listos, transiciona a LOCKED.
    
    data = {
        'match_id': str,
        'client_seed': str  # Para Provably Fair
    }
    """
    user_id = match_manager.get_user_by_sid(sid)
    if not user_id:
        return
    
    match_id = UUID(data['match_id'])
    room = match_manager.get_match(match_id)
    
    if not room or user_id not in room.players:
        await sio.emit('error', {'message': 'Match no encontrado'}, room=sid)
        return
    
    # Marcar como listo
    room.players[user_id].is_ready = True
    room.client_seeds[user_id] = data.get('client_seed', secrets.token_hex(16))
    
    # Notificar a todos
    await sio.emit('player_ready_update', {
        'player_id': str(user_id),
        'all_ready': room.all_players_ready()
    }, room=room.get_room_name())
    
    # Si todos están listos, transicionar a LOCKED
    if room.all_players_ready():
        await _transition_to_locked(room)


async def _transition_to_locked(room: MatchRoom):
    """Transiciona la partida al estado LOCKED (Escrow)."""
    success = await match_manager.transition_state(room.match_id, MatchState.LOCKED)
    
    if not success:
        await sio.emit('error', {
            'message': 'No se pudo bloquear la partida'
        }, room=room.get_room_name())
        return
    
    # Ejecutar Soft Lock de fondos para cada jugador
    soft_lock_success = await _execute_soft_lock(room)
    
    if not soft_lock_success:
        # Revertir si el soft lock falla
        await match_manager.transition_state(room.match_id, MatchState.CANCELLED)
        await sio.emit('error', {
            'message': 'Error al bloquear fondos. La partida ha sido cancelada.',
            'code': 'SOFT_LOCK_FAILED'
        }, room=room.get_room_name())
        return
    
    # Generar hash del estado inicial
    initial_hash = await match_manager.generate_initial_hash(room.match_id)
    
    # Notificar transición a LOCKED
    await sio.emit('match_locked', {
        'match_id': str(room.match_id),
        'state': MatchState.LOCKED.value,
        'initial_state_hash': initial_hash,
        'escrow_required': str(room.bet_amount),
        'confirm_timeout': SocketConfig.ESCROW_CONFIRMATION_TIMEOUT
    }, room=room.get_room_name())


async def _execute_soft_lock(room: MatchRoom) -> bool:
    """
    Ejecuta el Soft Lock atómico para todos los jugadores.
    
    Soft Lock:
    1. Verifica que todos los jugadores tengan saldo suficiente
    2. Mueve el bet_amount de 'available' a 'escrow_match' para cada jugador
    3. Si alguno falla, revierte todos los bloqueos
    
    Returns:
        bool: True si el soft lock fue exitoso para todos
    """
    locked_users = []
    
    try:
        for user_id, player in room.players.items():
            # Obtener balance actual del usuario (simulado - en prod va a la DB)
            user_balance = await _get_user_balance(user_id)
            
            if user_balance < room.bet_amount:
                # Saldo insuficiente
                await sio.emit('error', {
                    'message': f'Saldo insuficiente para la apuesta',
                    'code': 'INSUFFICIENT_BALANCE'
                }, to=player.sid)
                raise ValueError(f"User {user_id} has insufficient balance")
            
            # Ejecutar soft lock
            success = await _lock_user_balance(user_id, room.bet_amount, room.match_id)
            
            if not success:
                raise ValueError(f"Failed to lock balance for user {user_id}")
            
            # Registrar el balance al momento del lock
            player.balance_at_lock = user_balance
            locked_users.append(user_id)
        
        return True
        
    except Exception as e:
        # Revertir todos los locks ejecutados
        for user_id in locked_users:
            await _unlock_user_balance(user_id, room.bet_amount, room.match_id)
        
        print(f"[SOFT_LOCK] Failed: {e}")
        return False


async def _get_user_balance(user_id: UUID) -> Decimal:
    """
    Obtiene el balance disponible de un usuario.
    TODO: Conectar con base de datos real.
    """
    # Simulación - en producción, consultar DB
    # Por ahora retorna un balance ficticio para pruebas
    return Decimal("100.00")


async def _lock_user_balance(user_id: UUID, amount: Decimal, match_id: UUID) -> bool:
    """
    Bloquea fondos moviendo de 'available' a 'escrow_match'.
    
    Transacción atómica:
    UPDATE users SET 
        balance_available = balance_available - amount,
        balance_escrow_match = balance_escrow_match + amount
    WHERE id = user_id AND balance_available >= amount;
    
    TODO: Implementar con DB real usando transacciones.
    """
    print(f"[SOFT_LOCK] Locked {amount} LK from user {user_id} for match {match_id}")
    return True


async def _unlock_user_balance(user_id: UUID, amount: Decimal, match_id: UUID) -> bool:
    """
    Desbloquea fondos moviendo de 'escrow_match' a 'available'.
    Se usa cuando la partida es cancelada o hay error.
    
    TODO: Implementar con DB real.
    """
    print(f"[SOFT_LOCK] Unlocked {amount} LK for user {user_id} from match {match_id}")
    return True


async def _settle_escrow(
    match_id: UUID, 
    winner_id: UUID, 
    loser_id: UUID, 
    prize: Decimal,
    fee: Decimal
) -> bool:
    """
    Liquida el escrow al finalizar la partida.
    
    1. Mover escrow_match del perdedor al ganador
    2. Descontar fee de la plataforma
    3. Actualizar balances finales
    
    Returns:
        bool: True si la liquidación fue exitosa
    """
    print(f"[ESCROW] Settling match {match_id}:")
    print(f"  - Winner {winner_id} receives {prize} LK")
    print(f"  - Loser {loser_id} loses their escrow")
    print(f"  - Platform fee: {fee} LK")
    
    # TODO: Transacción atómica en DB
    # BEGIN;
    # UPDATE users SET balance_escrow_match = 0, balance_available = balance_available + prize WHERE id = winner_id;
    # UPDATE users SET balance_escrow_match = 0 WHERE id = loser_id;
    # INSERT INTO platform_revenue (amount, match_id) VALUES (fee * 2, match_id);
    # COMMIT;
    
    return True


@sio.event
async def confirm_escrow(sid: str, data: dict):
    """
    Jugador confirma que el escrow se realizó.
    Cuando todos confirman, transiciona a IN_PROGRESS.
    
    data = {
        'match_id': str,
        'transaction_hash': str  # Hash de la transacción de escrow
    }
    """
    user_id = match_manager.get_user_by_sid(sid)
    if not user_id:
        return
    
    match_id = UUID(data['match_id'])
    room = match_manager.get_match(match_id)
    
    if not room or room.state != MatchState.LOCKED:
        return
    
    # Marcar escrow confirmado
    room.players[user_id].escrow_confirmed = True
    
    # Verificar si todos confirmaron
    if room.all_escrow_confirmed():
        await _start_match(room)


async def _start_match(room: MatchRoom):
    """Inicia la partida (transición a IN_PROGRESS)."""
    success = await match_manager.transition_state(room.match_id, MatchState.IN_PROGRESS)
    
    if not success:
        return
    
    # Revelar server seed hash (para Provably Fair)
    await sio.emit('match_started', {
        'match_id': str(room.match_id),
        'state': MatchState.IN_PROGRESS.value,
        'started_at': room.started_at,
        'server_seed_hash': hashlib.sha256(room.server_seed.encode()).hexdigest()
    }, room=room.get_room_name())


@sio.event
async def heartbeat(sid: str, data: dict):
    """
    Procesa heartbeat del cliente.
    Analiza jitter para detectar lag switching.
    
    data = {
        'client_timestamp': float,
        'sequence': int,
        'game_state': str (opcional)
    }
    """
    user_id = match_manager.get_user_by_sid(sid)
    if not user_id:
        return
    
    # Actualizar último heartbeat
    room = match_manager.get_match_by_player(user_id)
    if room and user_id in room.players:
        room.players[user_id].last_heartbeat = time.time()
    
    # Analizar jitter
    analysis = jitter_detector.process_heartbeat(
        user_id=user_id,
        client_timestamp=data.get('client_timestamp', time.time()),
        sequence_number=data.get('sequence', 0),
        game_state=data.get('game_state', '')
    )
    
    # Responder con ACK y timestamp del servidor
    response = {
        'server_timestamp': time.time(),
        'sequence': data.get('sequence', 0),
        'connection_quality': analysis.details.get('connection_quality', 'UNKNOWN')
    }
    
    # Si hay sospecha, agregar advertencia
    if analysis.is_suspicious:
        response['warning'] = 'Connection quality under review'
    
    await sio.emit('heartbeat_ack', response, room=sid)


@sio.event
async def game_move(sid: str, data: dict):
    """
    Procesa un movimiento del juego.
    Registra en el log y notifica al oponente.
    
    data = {
        'match_id': str,
        'move_type': str,
        'move_data': dict
    }
    """
    user_id = match_manager.get_user_by_sid(sid)
    if not user_id:
        return
    
    match_id = UUID(data['match_id'])
    room = match_manager.get_match(match_id)
    
    if not room or room.state != MatchState.IN_PROGRESS:
        await sio.emit('error', {'message': 'Partida no activa'}, room=sid)
        return
    
    # Registrar movimiento
    sequence = await match_manager.record_move(
        match_id=match_id,
        user_id=user_id,
        move_data={
            'type': data.get('move_type'),
            'data': data.get('move_data', {})
        }
    )
    
    # Notificar a todos los jugadores
    await sio.emit('move_received', {
        'player_id': str(user_id),
        'sequence': sequence,
        'move_type': data.get('move_type'),
        'move_data': data.get('move_data', {}),
        'server_timestamp': time.time()
    }, room=room.get_room_name())


@sio.event
async def submit_game_result(sid: str, data: dict):
    """
    Cliente envía el resultado del juego para validación.
    Transiciona a VALIDATION para Shadow Simulation.
    
    data = {
        'match_id': str,
        'claimed_winner': str,
        'final_state': dict,
        'client_hash': str
    }
    """
    user_id = match_manager.get_user_by_sid(sid)
    if not user_id:
        return
    
    match_id = UUID(data['match_id'])
    room = match_manager.get_match(match_id)
    
    if not room or room.state != MatchState.IN_PROGRESS:
        return
    
    # Transicionar a VALIDATION
    await match_manager.transition_state(match_id, MatchState.VALIDATION)
    
    # Notificar que está en validación
    await sio.emit('match_validating', {
        'match_id': str(match_id),
        'state': MatchState.VALIDATION.value,
        'message': 'Validando resultado...'
    }, room=room.get_room_name())
    
    # TODO: Ejecutar Shadow Simulation aquí
    # Comparar resultado del cliente con simulación del servidor
    # Si coincide: SETTLEMENT
    # Si no coincide: DISPUTED


@sio.event
async def cancel_matchmaking(sid: str, data: dict):
    """Cancela la búsqueda de partida."""
    user_id = match_manager.get_user_by_sid(sid)
    if not user_id:
        return
    
    game_type = data.get('game_type')
    bet_amount = Decimal(str(data.get('bet_amount', 0)))
    
    await match_manager.remove_from_matchmaking(user_id, game_type, bet_amount)
    
    await sio.emit('matchmaking_cancelled', {
        'message': 'Búsqueda cancelada'
    }, room=sid)


# =============================================================================
# HANDLERS DE LUDO
# =============================================================================

# Almacenamiento de instancias de juego Ludo activas
ludo_games: Dict[UUID, 'LudoEngine'] = {}


@sio.event
async def ludo_start_game(sid: str, data: dict):
    """
    Inicia una partida de Ludo.
    Solo se llama cuando todos los jugadores están listos.
    """
    from .ludo_engine import LudoEngine
    
    user_id = match_manager.get_user_by_sid(sid)
    if not user_id:
        return
    
    match_id = UUID(data['match_id'])
    room = match_manager.get_match(match_id)
    
    if not room or room.state != MatchState.IN_PROGRESS:
        await sio.emit('error', {'message': 'Match no válido'}, room=sid)
        return
    
    # Crear instancia del motor de Ludo
    player_ids = list(room.players.keys())
    ludo = LudoEngine(match_id, player_ids)
    ludo_games[match_id] = ludo
    
    # Iniciar juego
    result = ludo.start_game()
    
    # Enviar a cada jugador su información
    for uid, player in room.players.items():
        ludo_player = ludo.players.get(uid)
        await sio.emit('ludo:game_started', {
            **result,
            'your_user_id': str(uid),
            'your_color': ludo_player.color.value if ludo_player else None,
            'state': ludo.get_state()
        }, room=player.sid)


@sio.event
async def ludo_roll_dice(sid: str, data: dict):
    """
    Jugador lanza los dados en Ludo.
    
    data = {
        'match_id': str,
        'client_seed': str (opcional, para Provably Fair)
    }
    """
    user_id = match_manager.get_user_by_sid(sid)
    if not user_id:
        return
    
    match_id = UUID(data['match_id'])
    
    if match_id not in ludo_games:
        await sio.emit('error', {'message': 'Juego de Ludo no encontrado'}, room=sid)
        return
    
    ludo = ludo_games[match_id]
    room = match_manager.get_match(match_id)
    
    if not room:
        return
    
    # Lanzar dado
    result = ludo.roll_dice(user_id, data.get('client_seed'))
    
    if 'error' in result:
        await sio.emit('error', {'message': result['error']}, room=sid)
        return
    
    # Enviar resultado a todos los jugadores
    await sio.emit('ludo:dice_rolled', {
        **result,
        'player': str(user_id),
        'state': ludo.get_state()
    }, room=room.get_room_name())


@sio.event
async def ludo_move_piece(sid: str, data: dict):
    """
    Jugador mueve una pieza en Ludo.
    
    data = {
        'match_id': str,
        'piece_id': int (0-3)
    }
    """
    user_id = match_manager.get_user_by_sid(sid)
    if not user_id:
        return
    
    match_id = UUID(data['match_id'])
    piece_id = int(data['piece_id'])
    
    if match_id not in ludo_games:
        await sio.emit('error', {'message': 'Juego de Ludo no encontrado'}, room=sid)
        return
    
    ludo = ludo_games[match_id]
    room = match_manager.get_match(match_id)
    
    if not room:
        return
    
    # Mover pieza
    result = ludo.move_piece(user_id, piece_id)
    
    if 'error' in result:
        await sio.emit('error', {'message': result['error']}, room=sid)
        return
    
    # Enviar resultado a todos los jugadores
    await sio.emit('ludo:piece_moved', {
        **result,
        'player': str(user_id),
        'state': ludo.get_state(),
        'board': ludo.get_board_state()
    }, room=room.get_room_name())
    
    # Si el juego terminó, limpiar
    if result.get('event') == 'game_over':
        await _handle_ludo_game_over(match_id, room, result)


async def _handle_ludo_game_over(match_id: UUID, room: MatchRoom, result: dict):
    """
    Maneja el fin de una partida de Ludo.
    Procesa la liquidación de apuestas usando el sistema de Rake profesional
    y registra en el Treasury Ledger con Triple Entrada.
    """
    winner_id = UUID(result['winner']) if result.get('winner') else None
    
    # Transicionar a SETTLEMENT
    await match_manager.transition_state(match_id, MatchState.SETTLEMENT)
    
    # Calcular liquidación usando RakeCalculator profesional
    rake_result = RakeCalculator.calculate_fee(room.bet_amount)
    
    pot = rake_result["total_pot"]
    total_fee = rake_result["total_fee"]
    prize = rake_result["winner_prize"]
    rake_level = rake_result["rake_level"]
    rake_rate = rake_result["rake_rate"]
    
    print(f"[SETTLEMENT] Match {match_id}")
    print(f"  Nivel: {rake_level} ({rake_rate * 100}%)")
    print(RakeCalculator.calculate_fee_breakdown(room.bet_amount))
    
    # Determinar ganador y perdedor
    player_ids = list(room.players.keys())
    loser_id = None
    
    if winner_id and len(player_ids) == 2:
        loser_id = player_ids[0] if player_ids[1] == winner_id else player_ids[1]
        
        # Obtener balances actuales (simulado - en prod va a DB)
        winner_balance = room.players[winner_id].balance_at_lock
        loser_balance = room.players[loser_id].balance_at_lock
        
        # Crear entrada en el Ledger con Triple Entrada
        try:
            ledger_entry = treasury_ledger.create_match_settlement(
                match_id=str(match_id),
                winner_id=str(winner_id),
                loser_id=str(loser_id),
                bet_amount=room.bet_amount,
                winner_balance_before=winner_balance,
                loser_balance_before=loser_balance
            )
            
            room.ledger_entry_id = ledger_entry.entry_id
            
            # Ejecutar liquidación atómica del escrow
            settlement_success = await _settle_escrow(
                match_id=match_id,
                winner_id=winner_id,
                loser_id=loser_id,
                prize=prize,
                fee=rake_result["fee_per_player"]
            )
            
            if settlement_success:
                # Confirmar entrada en el ledger
                treasury_ledger.commit_entry(ledger_entry.entry_id)
            else:
                # Revertir entrada
                treasury_ledger.rollback_entry(ledger_entry.entry_id)
                raise Exception("Settlement failed")
                
        except Exception as e:
            print(f"[SETTLEMENT ERROR] {e}")
            await sio.emit('error', {
                'message': 'Error en liquidación. Contacte soporte.',
                'code': 'SETTLEMENT_ERROR',
                'match_id': str(match_id)
            }, room=room.get_room_name())
    
    # Enviar resultado final con detalles del rake
    await sio.emit('ludo:game_over', {
        **result,
        'pot': str(pot),
        'fee': str(total_fee),
        'prize': str(prize),
        'winner_receives': str(prize),
        'rake_level': rake_level,
        'rake_rate': str(rake_rate * 100) + '%',
        'ledger_entry': room.ledger_entry_id,
        'treasury_summary': treasury_ledger.get_treasury_summary()
    }, room=room.get_room_name())
    
    # Limpiar juego
    if match_id in ludo_games:
        del ludo_games[match_id]
    
    # Transicionar a COMPLETED
    await match_manager.transition_state(match_id, MatchState.COMPLETED)
    await match_manager.cleanup_match(match_id)


# =============================================================================
# APLICACIÓN ASGI
# =============================================================================

def create_socket_app():
    """Crea la aplicación ASGI para Socket.IO."""
    return socketio.ASGIApp(sio)
