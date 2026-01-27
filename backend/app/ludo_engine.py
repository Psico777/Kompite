"""
=============================================================================
KOMPITE - Motor de Ludo (Servidor Autoritativo)
=============================================================================
Implementa la lógica completa del juego Ludo con dados generados en el
servidor para garantizar neutralidad algorítmica y prevenir manipulación.

Principios:
- Provably Fair: Los dados usan server_seed + client_seed + nonce
- Estado Autoritativo: Solo el servidor valida movimientos
- Determinístico: Dado el mismo seed, produce los mismos resultados
=============================================================================
"""

import hashlib
import json
import secrets
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID, uuid4


# =============================================================================
# CONFIGURACIÓN DEL JUEGO
# =============================================================================

class LudoConfig:
    """Constantes del juego Ludo."""
    
    # Tablero
    BOARD_SIZE = 52          # Casillas en el circuito principal
    HOME_STRETCH = 5         # Casillas antes de llegar a casa
    PIECES_PER_PLAYER = 4    # Fichas por jugador
    
    # Dados
    DICE_MIN = 1
    DICE_MAX = 6
    ROLL_AGAIN_ON = 6        # Sacar 6 permite tirar de nuevo
    MAX_CONSECUTIVE_SIXES = 3  # 3 seises seguidos = pierde turno
    
    # Reglas
    EXIT_ROLL = 6            # Se necesita 6 para salir de casa
    SAFE_POSITIONS = [0, 8, 13, 21, 26, 34, 39, 47]  # Casillas seguras
    
    # Tiempos
    TURN_TIMEOUT = 30        # Segundos máximos por turno
    GAME_TIMEOUT = 1800      # 30 minutos máximo por partida


# =============================================================================
# ENUMS
# =============================================================================

class PieceState(Enum):
    """Estado de una ficha."""
    HOME = "HOME"            # En la base (no ha salido)
    ACTIVE = "ACTIVE"        # En el tablero principal
    SAFE_ZONE = "SAFE_ZONE"  # En la zona de llegada
    FINISHED = "FINISHED"    # Ya llegó al centro


class LudoGameState(Enum):
    """Estados del juego."""
    WAITING = "WAITING"
    ROLLING = "ROLLING"
    MOVING = "MOVING"
    COMPLETED = "COMPLETED"
    ABANDONED = "ABANDONED"


class PlayerColor(Enum):
    """Colores de jugadores (para 2-4 jugadores)."""
    RED = "RED"       # Posición inicial 0
    BLUE = "BLUE"     # Posición inicial 13
    GREEN = "GREEN"   # Posición inicial 26
    YELLOW = "YELLOW" # Posición inicial 39


# =============================================================================
# ESTRUCTURAS DE DATOS
# =============================================================================

@dataclass
class LudoPiece:
    """Representa una ficha del Ludo."""
    piece_id: int              # 0-3 para cada jugador
    owner: PlayerColor
    state: PieceState = PieceState.HOME
    position: int = -1         # -1 = en casa, 0-51 = tablero, 52-56 = safe zone
    safe_zone_pos: int = -1    # Posición en la zona segura (0-5)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "piece_id": self.piece_id,
            "owner": self.owner.value,
            "state": self.state.value,
            "position": self.position,
            "safe_zone_pos": self.safe_zone_pos
        }


@dataclass
class LudoPlayer:
    """Jugador de Ludo."""
    user_id: UUID
    color: PlayerColor
    pieces: List[LudoPiece] = field(default_factory=list)
    
    # Stats del juego
    pieces_finished: int = 0
    captures_made: int = 0
    sixes_rolled: int = 0
    
    # Estado
    is_connected: bool = True
    last_action: float = field(default_factory=time.time)
    
    def __post_init__(self):
        if not self.pieces:
            self.pieces = [
                LudoPiece(piece_id=i, owner=self.color)
                for i in range(LudoConfig.PIECES_PER_PLAYER)
            ]
    
    def all_finished(self) -> bool:
        """Verifica si todas las fichas llegaron al final."""
        return self.pieces_finished >= LudoConfig.PIECES_PER_PLAYER
    
    def has_active_piece(self) -> bool:
        """Verifica si tiene alguna ficha en juego."""
        return any(p.state == PieceState.ACTIVE for p in self.pieces)
    
    def can_exit_piece(self, dice_value: int) -> bool:
        """Verifica si puede sacar una ficha de casa."""
        return (
            dice_value == LudoConfig.EXIT_ROLL and
            any(p.state == PieceState.HOME for p in self.pieces)
        )
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_id": str(self.user_id),
            "color": self.color.value,
            "pieces": [p.to_dict() for p in self.pieces],
            "pieces_finished": self.pieces_finished,
            "captures_made": self.captures_made,
            "is_connected": self.is_connected
        }


@dataclass
class DiceRoll:
    """Resultado de un lanzamiento de dado."""
    value: int
    server_seed: str
    client_seed: str
    nonce: int
    timestamp: float
    hash_proof: str  # Para verificación Provably Fair
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "value": self.value,
            "nonce": self.nonce,
            "hash_proof": self.hash_proof,
            "timestamp": self.timestamp
        }


@dataclass
class LudoMove:
    """Representa un movimiento en el juego."""
    move_id: int
    player: PlayerColor
    piece_id: int
    dice_roll: DiceRoll
    from_position: int
    to_position: int
    captured_piece: Optional[Tuple[PlayerColor, int]] = None
    entered_safe_zone: bool = False
    finished: bool = False
    timestamp: float = field(default_factory=time.time)
    
    def to_dict(self) -> Dict[str, Any]:
        result = {
            "move_id": self.move_id,
            "player": self.player.value,
            "piece_id": self.piece_id,
            "dice": self.dice_roll.value,
            "from": self.from_position,
            "to": self.to_position,
            "timestamp": self.timestamp
        }
        if self.captured_piece:
            result["capture"] = {
                "player": self.captured_piece[0].value,
                "piece": self.captured_piece[1]
            }
        if self.entered_safe_zone:
            result["entered_safe_zone"] = True
        if self.finished:
            result["finished"] = True
        return result


# =============================================================================
# MOTOR DE DADOS (PROVABLY FAIR)
# =============================================================================

class ProvablyFairDice:
    """
    Generador de dados con prueba de equidad verificable.
    
    Algoritmo:
    1. El servidor genera server_seed al inicio (oculto)
    2. El cliente envía su client_seed (hash mostrado al inicio)
    3. Cada tirada usa: hash(server_seed + client_seed + nonce)
    4. Al final se revela server_seed para verificación
    """
    
    def __init__(self, server_seed: Optional[str] = None):
        self.server_seed = server_seed or secrets.token_hex(32)
        self.server_seed_hash = hashlib.sha256(self.server_seed.encode()).hexdigest()
        self.client_seeds: Dict[str, str] = {}  # player_id -> seed
        self.nonce = 0
    
    def set_client_seed(self, player_id: str, seed: str):
        """Registra el seed del cliente."""
        self.client_seeds[player_id] = seed
    
    def roll(self, player_id: str) -> DiceRoll:
        """
        Genera un valor de dado de forma verificable.
        
        El resultado es determinístico basado en los seeds y nonce.
        """
        self.nonce += 1
        client_seed = self.client_seeds.get(player_id, "default")
        
        # Combinar seeds con nonce
        combined = f"{self.server_seed}:{client_seed}:{self.nonce}"
        hash_result = hashlib.sha256(combined.encode()).hexdigest()
        
        # Convertir los primeros 8 caracteres hex a número
        # y mapear a rango 1-6
        numeric_value = int(hash_result[:8], 16)
        dice_value = (numeric_value % 6) + 1
        
        return DiceRoll(
            value=dice_value,
            server_seed=self.server_seed_hash,  # Solo el hash público
            client_seed=client_seed,
            nonce=self.nonce,
            timestamp=time.time(),
            hash_proof=hash_result[:16]
        )
    
    def verify_roll(self, roll: DiceRoll, revealed_server_seed: str) -> bool:
        """Verifica que un lanzamiento fue justo (post-game)."""
        combined = f"{revealed_server_seed}:{roll.client_seed}:{roll.nonce}"
        hash_result = hashlib.sha256(combined.encode()).hexdigest()
        
        numeric_value = int(hash_result[:8], 16)
        expected_value = (numeric_value % 6) + 1
        
        return (
            expected_value == roll.value and
            hash_result[:16] == roll.hash_proof
        )
    
    def get_verification_data(self) -> Dict[str, Any]:
        """
        Retorna datos para verificación post-partida.
        SOLO llamar cuando la partida haya terminado.
        """
        return {
            "server_seed": self.server_seed,  # Se revela al final
            "server_seed_hash": self.server_seed_hash,
            "client_seeds": self.client_seeds,
            "total_rolls": self.nonce
        }


# =============================================================================
# MOTOR DE LUDO
# =============================================================================

class LudoEngine:
    """
    Motor del juego Ludo con estado autoritativo.
    
    Todas las decisiones de juego se toman en el servidor.
    El cliente solo envía intenciones, el servidor valida y ejecuta.
    """
    
    def __init__(self, match_id: UUID, players: List[UUID]):
        self.match_id = match_id
        self.state = LudoGameState.WAITING
        
        # Configurar jugadores
        colors = [PlayerColor.RED, PlayerColor.BLUE, PlayerColor.GREEN, PlayerColor.YELLOW]
        self.players: Dict[UUID, LudoPlayer] = {}
        self.player_order: List[UUID] = []
        
        for i, user_id in enumerate(players[:4]):
            color = colors[i]
            self.players[user_id] = LudoPlayer(user_id=user_id, color=color)
            self.player_order.append(user_id)
        
        # Sistema de dados
        self.dice = ProvablyFairDice()
        
        # Control de turnos
        self.current_player_index = 0
        self.current_roll: Optional[DiceRoll] = None
        self.consecutive_sixes = 0
        self.can_roll_again = False
        
        # Histórico
        self.moves_history: List[LudoMove] = []
        self.move_counter = 0
        
        # Timing
        self.started_at = time.time()
        self.turn_started_at = time.time()
        
        # Resultado
        self.winner: Optional[UUID] = None
        self.rankings: List[UUID] = []
    
    # -------------------------------------------------------------------------
    # PROPIEDADES
    # -------------------------------------------------------------------------
    
    @property
    def current_player(self) -> LudoPlayer:
        """Retorna el jugador actual."""
        user_id = self.player_order[self.current_player_index]
        return self.players[user_id]
    
    @property
    def current_user_id(self) -> UUID:
        """Retorna el ID del usuario actual."""
        return self.player_order[self.current_player_index]
    
    def get_start_position(self, color: PlayerColor) -> int:
        """Retorna la posición de salida para un color."""
        positions = {
            PlayerColor.RED: 0,
            PlayerColor.BLUE: 13,
            PlayerColor.GREEN: 26,
            PlayerColor.YELLOW: 39
        }
        return positions[color]
    
    def get_safe_zone_entry(self, color: PlayerColor) -> int:
        """Retorna la posición donde se entra a la zona segura."""
        start = self.get_start_position(color)
        return (start + LudoConfig.BOARD_SIZE - 1) % LudoConfig.BOARD_SIZE
    
    # -------------------------------------------------------------------------
    # ACCIONES DEL JUEGO
    # -------------------------------------------------------------------------
    
    def start_game(self) -> Dict[str, Any]:
        """Inicia la partida."""
        if self.state != LudoGameState.WAITING:
            return {"error": "Game already started"}
        
        self.state = LudoGameState.ROLLING
        self.started_at = time.time()
        self.turn_started_at = time.time()
        
        return {
            "event": "game_started",
            "first_player": str(self.current_user_id),
            "first_color": self.current_player.color.value,
            "server_seed_hash": self.dice.server_seed_hash
        }
    
    def roll_dice(self, user_id: UUID, client_seed: Optional[str] = None) -> Dict[str, Any]:
        """
        Lanza los dados para el jugador actual.
        
        Args:
            user_id: ID del usuario que solicita el lanzamiento
            client_seed: Seed opcional del cliente para Provably Fair
        
        Returns:
            Resultado del lanzamiento y movimientos disponibles
        """
        # Validaciones
        if self.state not in [LudoGameState.ROLLING]:
            return {"error": "Cannot roll in current state", "state": self.state.value}
        
        if user_id != self.current_user_id:
            return {"error": "Not your turn"}
        
        # Registrar client seed si se proporciona
        if client_seed:
            self.dice.set_client_seed(str(user_id), client_seed)
        
        # Lanzar dado
        roll = self.dice.roll(str(user_id))
        self.current_roll = roll
        
        # Verificar tres seises consecutivos
        if roll.value == 6:
            self.consecutive_sixes += 1
            if self.consecutive_sixes >= LudoConfig.MAX_CONSECUTIVE_SIXES:
                # Pierde turno, pasa al siguiente
                self._next_turn()
                return {
                    "event": "dice_rolled",
                    "roll": roll.to_dict(),
                    "penalty": "three_sixes",
                    "next_player": str(self.current_user_id)
                }
            self.can_roll_again = True
        else:
            self.consecutive_sixes = 0
            self.can_roll_again = False
        
        # Calcular movimientos disponibles
        available_moves = self._get_available_moves(roll.value)
        
        if not available_moves:
            # No hay movimientos, pasa turno
            self._next_turn()
            return {
                "event": "dice_rolled",
                "roll": roll.to_dict(),
                "no_moves": True,
                "next_player": str(self.current_user_id)
            }
        
        self.state = LudoGameState.MOVING
        
        return {
            "event": "dice_rolled",
            "roll": roll.to_dict(),
            "available_moves": available_moves,
            "can_roll_again": self.can_roll_again
        }
    
    def move_piece(self, user_id: UUID, piece_id: int) -> Dict[str, Any]:
        """
        Mueve una ficha del jugador.
        
        Args:
            user_id: ID del usuario
            piece_id: ID de la ficha a mover (0-3)
        
        Returns:
            Resultado del movimiento
        """
        if self.state != LudoGameState.MOVING:
            return {"error": "Cannot move in current state"}
        
        if user_id != self.current_user_id:
            return {"error": "Not your turn"}
        
        if not self.current_roll:
            return {"error": "No dice roll available"}
        
        player = self.current_player
        if piece_id < 0 or piece_id >= len(player.pieces):
            return {"error": "Invalid piece ID"}
        
        piece = player.pieces[piece_id]
        dice_value = self.current_roll.value
        
        # Validar el movimiento
        move_result = self._validate_and_execute_move(piece, dice_value)
        
        if "error" in move_result:
            return move_result
        
        # Registrar movimiento
        self.move_counter += 1
        move = LudoMove(
            move_id=self.move_counter,
            player=player.color,
            piece_id=piece_id,
            dice_roll=self.current_roll,
            from_position=move_result["from"],
            to_position=move_result["to"],
            captured_piece=move_result.get("capture"),
            entered_safe_zone=move_result.get("entered_safe_zone", False),
            finished=move_result.get("finished", False)
        )
        self.moves_history.append(move)
        
        # Verificar si ganó
        if player.all_finished():
            return self._handle_victory(player)
        
        # Determinar siguiente acción
        # Si capturó o llegó a casa, tira de nuevo
        extra_turn = move_result.get("capture") or move_result.get("finished")
        
        if self.can_roll_again or extra_turn:
            self.state = LudoGameState.ROLLING
            self.current_roll = None
            return {
                "event": "piece_moved",
                "move": move.to_dict(),
                "roll_again": True
            }
        
        # Siguiente turno
        self._next_turn()
        
        return {
            "event": "piece_moved",
            "move": move.to_dict(),
            "next_player": str(self.current_user_id)
        }
    
    # -------------------------------------------------------------------------
    # LÓGICA INTERNA
    # -------------------------------------------------------------------------
    
    def _get_available_moves(self, dice_value: int) -> List[Dict[str, Any]]:
        """Calcula movimientos disponibles para el jugador actual."""
        player = self.current_player
        moves = []
        
        for piece in player.pieces:
            move_info = self._can_move_piece(piece, dice_value)
            if move_info:
                moves.append({
                    "piece_id": piece.piece_id,
                    "from": piece.position,
                    "to": move_info["to"],
                    "action": move_info["action"]
                })
        
        return moves
    
    def _can_move_piece(self, piece: LudoPiece, dice_value: int) -> Optional[Dict[str, Any]]:
        """Verifica si una ficha puede moverse con el valor del dado."""
        player = self.players[self.current_user_id]
        
        if piece.state == PieceState.HOME:
            # Solo puede salir con 6
            if dice_value == LudoConfig.EXIT_ROLL:
                start_pos = self.get_start_position(piece.owner)
                return {"to": start_pos, "action": "exit"}
            return None
        
        if piece.state == PieceState.FINISHED:
            return None
        
        if piece.state == PieceState.SAFE_ZONE:
            # Moverse dentro de la zona segura
            new_safe_pos = piece.safe_zone_pos + dice_value
            if new_safe_pos == LudoConfig.HOME_STRETCH + 1:
                return {"to": -2, "action": "finish"}  # -2 = llegó a casa
            elif new_safe_pos > LudoConfig.HOME_STRETCH + 1:
                return None  # No puede pasar de la casa
            return {"to": new_safe_pos, "action": "safe_move"}
        
        # Ficha activa en tablero
        new_pos = (piece.position + dice_value) % LudoConfig.BOARD_SIZE
        safe_zone_entry = self.get_safe_zone_entry(piece.owner)
        
        # Verificar si debe entrar a zona segura
        start = piece.position
        steps_to_entry = (safe_zone_entry - start) % LudoConfig.BOARD_SIZE
        
        if steps_to_entry < dice_value and steps_to_entry >= 0:
            # Pasa por la entrada a zona segura
            remaining = dice_value - steps_to_entry - 1
            if remaining <= LudoConfig.HOME_STRETCH:
                if remaining == LudoConfig.HOME_STRETCH:
                    return {"to": -2, "action": "finish"}
                return {"to": remaining, "action": "enter_safe"}
            return None  # Se pasa de la casa
        
        return {"to": new_pos, "action": "move"}
    
    def _validate_and_execute_move(
        self,
        piece: LudoPiece,
        dice_value: int
    ) -> Dict[str, Any]:
        """Valida y ejecuta un movimiento."""
        move_info = self._can_move_piece(piece, dice_value)
        
        if not move_info:
            return {"error": "Invalid move for this piece"}
        
        player = self.current_player
        from_pos = piece.position
        to_pos = move_info["to"]
        action = move_info["action"]
        result = {"from": from_pos, "to": to_pos}
        
        if action == "exit":
            # Sacar ficha de casa
            piece.state = PieceState.ACTIVE
            piece.position = to_pos
            # Verificar si captura al salir
            capture = self._check_capture(to_pos, player.color)
            if capture:
                result["capture"] = capture
        
        elif action == "move":
            piece.position = to_pos
            # Verificar captura
            capture = self._check_capture(to_pos, player.color)
            if capture:
                result["capture"] = capture
        
        elif action == "enter_safe":
            piece.state = PieceState.SAFE_ZONE
            piece.position = -1
            piece.safe_zone_pos = to_pos
            result["entered_safe_zone"] = True
        
        elif action == "safe_move":
            piece.safe_zone_pos = to_pos
        
        elif action == "finish":
            piece.state = PieceState.FINISHED
            piece.position = -2
            piece.safe_zone_pos = -1
            player.pieces_finished += 1
            result["finished"] = True
        
        return result
    
    def _check_capture(self, position: int, attacker_color: PlayerColor) -> Optional[Tuple[PlayerColor, int]]:
        """Verifica y ejecuta captura en una posición."""
        # Las casillas seguras no permiten captura
        if position in LudoConfig.SAFE_POSITIONS:
            return None
        
        for user_id, player in self.players.items():
            if player.color == attacker_color:
                continue
            
            for piece in player.pieces:
                if piece.state == PieceState.ACTIVE and piece.position == position:
                    # ¡Captura!
                    piece.state = PieceState.HOME
                    piece.position = -1
                    self.current_player.captures_made += 1
                    return (player.color, piece.piece_id)
        
        return None
    
    def _next_turn(self):
        """Avanza al siguiente turno."""
        self.current_player_index = (self.current_player_index + 1) % len(self.player_order)
        self.current_roll = None
        self.consecutive_sixes = 0
        self.can_roll_again = False
        self.state = LudoGameState.ROLLING
        self.turn_started_at = time.time()
    
    def _handle_victory(self, winner: LudoPlayer) -> Dict[str, Any]:
        """Procesa la victoria de un jugador."""
        self.state = LudoGameState.COMPLETED
        self.winner = winner.user_id
        self.rankings.append(winner.user_id)
        
        # Agregar resto de jugadores al ranking
        for user_id in self.player_order:
            if user_id not in self.rankings:
                self.rankings.append(user_id)
        
        return {
            "event": "game_over",
            "winner": str(winner.user_id),
            "winner_color": winner.color.value,
            "rankings": [str(uid) for uid in self.rankings],
            "verification": self.dice.get_verification_data(),
            "total_moves": len(self.moves_history),
            "duration_seconds": time.time() - self.started_at
        }
    
    # -------------------------------------------------------------------------
    # ESTADO Y SERIALIZACIÓN
    # -------------------------------------------------------------------------
    
    def get_state(self) -> Dict[str, Any]:
        """Retorna el estado completo del juego."""
        return {
            "match_id": str(self.match_id),
            "state": self.state.value,
            "players": {str(uid): p.to_dict() for uid, p in self.players.items()},
            "player_order": [str(uid) for uid in self.player_order],
            "current_player": str(self.current_user_id),
            "current_roll": self.current_roll.to_dict() if self.current_roll else None,
            "can_roll_again": self.can_roll_again,
            "moves_count": len(self.moves_history),
            "started_at": self.started_at,
            "server_seed_hash": self.dice.server_seed_hash
        }
    
    def get_board_state(self) -> Dict[str, Any]:
        """Retorna el estado del tablero para renderizar."""
        pieces = []
        for player in self.players.values():
            for piece in player.pieces:
                pieces.append(piece.to_dict())
        
        return {
            "pieces": pieces,
            "safe_positions": LudoConfig.SAFE_POSITIONS,
            "current_player_color": self.current_player.color.value
        }
    
    def handle_disconnect(self, user_id: UUID):
        """Maneja desconexión de un jugador."""
        if user_id in self.players:
            self.players[user_id].is_connected = False
    
    def handle_reconnect(self, user_id: UUID):
        """Maneja reconexión de un jugador."""
        if user_id in self.players:
            self.players[user_id].is_connected = True
            self.players[user_id].last_action = time.time()


# =============================================================================
# REGISTRO EN FACTORY
# =============================================================================

def register_ludo_simulator():
    """Registra el motor de Ludo en la factory de juegos."""
    from .game_engine import GameEngineFactory
    GameEngineFactory.register_simulator("LUDO", LudoEngine)
