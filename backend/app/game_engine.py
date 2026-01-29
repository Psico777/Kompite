"""
=============================================================================
KOMPITE - Motor de Simulación de Sombra (Shadow Simulation)
=============================================================================
Implementa la validación física independiente del servidor para garantizar
la Neutralidad Operativa. El servidor NO confía en los datos del cliente;
recalcula la física y compara resultados.

Principios:
- Verificación Independiente: El servidor simula la física por separado
- Tolerancia a Errores: Margen de error para latencia de red
- Detección de Fraude: Discrepancias significativas = bandera roja
=============================================================================
"""

import math
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Optional, List, Dict, Tuple, Any
from uuid import UUID
import hashlib
import json
import time


# =============================================================================
# CONSTANTES FÍSICAS DEL MOTOR
# =============================================================================

class PhysicsConstants:
    """Constantes físicas para simulación de trayectorias."""
    
    # Gravedad (unidades del juego por segundo²)
    GRAVITY = 9.81
    
    # Coeficientes de fricción por superficie
    FRICTION_GRASS = 0.3      # Penales
    FRICTION_COURT = 0.2      # Básquet
    FRICTION_ICE = 0.05       # Air Hockey
    
    # Coeficientes de rebote
    BOUNCE_BALL = 0.7
    BOUNCE_PUCK = 0.9
    
    # Tolerancias de validación (margen de error aceptable)
    POSITION_TOLERANCE = 5.0       # Unidades de distancia
    VELOCITY_TOLERANCE = 2.0       # Unidades de velocidad
    ANGLE_TOLERANCE = 3.0          # Grados
    TIME_TOLERANCE = 0.1           # Segundos
    
    # Dimensiones del campo (normalizadas)
    PENALTY_GOAL_WIDTH = 7.32      # Metros (escala real)
    PENALTY_GOAL_HEIGHT = 2.44     # Metros
    PENALTY_DISTANCE = 11.0        # Metros desde el arco
    
    BASKET_HOOP_HEIGHT = 3.05      # Metros
    BASKET_HOOP_RADIUS = 0.23      # Metros
    FREE_THROW_DISTANCE = 4.57    # Metros


class GameResult(Enum):
    """Resultados posibles de una jugada."""
    GOAL = "GOAL"
    SAVED = "SAVED"
    MISS = "MISS"
    SCORE = "SCORE"  # Para básquet
    INVALID = "INVALID"
    TIMEOUT = "TIMEOUT"


class ValidationResult(Enum):
    """Resultado de la validación del servidor."""
    VALID = "VALID"                    # Datos del cliente coinciden
    MINOR_DISCREPANCY = "MINOR"        # Pequeña diferencia (latencia)
    MAJOR_DISCREPANCY = "MAJOR"        # Diferencia significativa (sospechoso)
    FRAUD_DETECTED = "FRAUD"           # Datos imposibles físicamente
    SIMULATION_ERROR = "SIM_ERROR"     # Error en la simulación del servidor


# =============================================================================
# ESTRUCTURAS DE DATOS
# =============================================================================

@dataclass
class Vector2D:
    """Vector 2D para posiciones y velocidades."""
    x: float
    y: float
    
    def magnitude(self) -> float:
        """Calcula la magnitud del vector."""
        return math.sqrt(self.x ** 2 + self.y ** 2)
    
    def normalize(self) -> 'Vector2D':
        """Retorna el vector normalizado."""
        mag = self.magnitude()
        if mag == 0:
            return Vector2D(0, 0)
        return Vector2D(self.x / mag, self.y / mag)
    
    def __add__(self, other: 'Vector2D') -> 'Vector2D':
        return Vector2D(self.x + other.x, self.y + other.y)
    
    def __sub__(self, other: 'Vector2D') -> 'Vector2D':
        return Vector2D(self.x - other.x, self.y - other.y)
    
    def __mul__(self, scalar: float) -> 'Vector2D':
        return Vector2D(self.x * scalar, self.y * scalar)
    
    def to_dict(self) -> Dict[str, float]:
        return {"x": self.x, "y": self.y}


@dataclass
class Vector3D:
    """Vector 3D para física con altura (básquet, trayectorias aéreas)."""
    x: float
    y: float
    z: float  # Altura
    
    def magnitude(self) -> float:
        return math.sqrt(self.x ** 2 + self.y ** 2 + self.z ** 2)
    
    def horizontal_distance(self) -> float:
        """Distancia horizontal ignorando altura."""
        return math.sqrt(self.x ** 2 + self.y ** 2)
    
    def __add__(self, other: 'Vector3D') -> 'Vector3D':
        return Vector3D(self.x + other.x, self.y + other.y, self.z + other.z)
    
    def __mul__(self, scalar: float) -> 'Vector3D':
        return Vector3D(self.x * scalar, self.y * scalar, self.z * scalar)
    
    def to_dict(self) -> Dict[str, float]:
        return {"x": self.x, "y": self.y, "z": self.z}


@dataclass
class ShotInput:
    """
    Entrada de tiro del cliente.
    Representa los parámetros enviados por el cliente para un disparo.
    """
    # Posición inicial del balón/objeto
    start_position: Vector3D
    
    # Dirección y fuerza
    angle_horizontal: float   # Grados (0-360)
    angle_vertical: float     # Grados (0-90) - elevación
    power: float              # 0.0 - 1.0 (normalizado)
    
    # Efectos (spin)
    spin_x: float = 0.0       # Efecto lateral
    spin_y: float = 0.0       # Efecto vertical (top/back spin)
    
    # Timestamp del cliente
    client_timestamp: float = 0.0
    
    # Hash de integridad del cliente
    client_hash: Optional[str] = None


@dataclass
class ShotResult:
    """Resultado calculado por el servidor."""
    # Posición final del objeto
    final_position: Vector3D
    
    # Resultado del tiro
    result: GameResult
    
    # Trayectoria completa (para replay/verificación)
    trajectory: List[Vector3D] = field(default_factory=list)
    
    # Tiempo total de simulación
    simulation_time: float = 0.0
    
    # Hash del resultado para auditoría
    result_hash: str = ""
    
    def compute_hash(self, match_id: str, shot_index: int) -> str:
        """Calcula hash del resultado para inmutabilidad."""
        data = f"{match_id}:{shot_index}:{self.final_position.to_dict()}:{self.result.value}"
        return hashlib.sha256(data.encode()).hexdigest()


@dataclass
class ValidationReport:
    """Reporte de validación entre cliente y servidor."""
    validation_result: ValidationResult
    server_result: ShotResult
    client_result: Optional[GameResult]
    
    # Métricas de discrepancia
    position_delta: float = 0.0
    time_delta: float = 0.0
    
    # Detalles para auditoría
    details: Dict[str, Any] = field(default_factory=dict)
    
    # ¿Requiere revisión manual?
    requires_review: bool = False


# =============================================================================
# MOTOR DE FÍSICA - PENALES
# =============================================================================

class PenaltyKickSimulator:
    """
    Simulador de penales con física realista.
    Calcula trayectoria del balón considerando gravedad, fricción y spin.
    """
    
    def __init__(self):
        self.goal_width = PhysicsConstants.PENALTY_GOAL_WIDTH
        self.goal_height = PhysicsConstants.PENALTY_GOAL_HEIGHT
        self.penalty_distance = PhysicsConstants.PENALTY_DISTANCE
        self.max_power = 35.0  # Velocidad máxima en m/s
        self.dt = 0.016  # Delta time (60 FPS)
    
    def simulate_shot(self, shot: ShotInput) -> ShotResult:
        """
        Simula un tiro de penal y retorna el resultado.
        
        Args:
            shot: Parámetros del tiro del jugador
            
        Returns:
            ShotResult con trayectoria y resultado
        """
        # Convertir ángulos a radianes
        h_angle = math.radians(shot.angle_horizontal)
        v_angle = math.radians(shot.angle_vertical)
        
        # Calcular velocidad inicial
        speed = shot.power * self.max_power
        
        # Componentes de velocidad
        vx = speed * math.cos(v_angle) * math.sin(h_angle)  # Lateral
        vy = speed * math.cos(v_angle) * math.cos(h_angle)  # Hacia el arco
        vz = speed * math.sin(v_angle)  # Altura
        
        velocity = Vector3D(vx, vy, vz)
        position = Vector3D(
            shot.start_position.x,
            shot.start_position.y,
            shot.start_position.z
        )
        
        trajectory = [Vector3D(position.x, position.y, position.z)]
        simulation_time = 0.0
        max_iterations = 500  # Prevenir loops infinitos
        
        # Simular física
        for _ in range(max_iterations):
            # Aplicar gravedad
            velocity.z -= PhysicsConstants.GRAVITY * self.dt
            
            # Aplicar fricción del aire (simplificada)
            air_resistance = 0.01
            velocity.x *= (1 - air_resistance)
            velocity.y *= (1 - air_resistance)
            
            # Aplicar spin (efecto Magnus simplificado)
            if shot.spin_x != 0:
                velocity.x += shot.spin_x * 0.1 * self.dt
            if shot.spin_y != 0:
                velocity.z += shot.spin_y * 0.05 * self.dt
            
            # Actualizar posición
            position.x += velocity.x * self.dt
            position.y += velocity.y * self.dt
            position.z += velocity.z * self.dt
            
            simulation_time += self.dt
            trajectory.append(Vector3D(position.x, position.y, position.z))
            
            # Verificar si llegó a la línea de gol
            if position.y >= self.penalty_distance:
                break
            
            # Verificar si tocó el suelo (rebote o fin)
            if position.z <= 0:
                position.z = 0
                velocity.z = -velocity.z * PhysicsConstants.BOUNCE_BALL
                if abs(velocity.z) < 0.5:  # Muy poco rebote = parada
                    break
        
        # Determinar resultado
        result = self._evaluate_shot(position)
        
        shot_result = ShotResult(
            final_position=position,
            result=result,
            trajectory=trajectory,
            simulation_time=simulation_time
        )
        
        return shot_result
    
    def _evaluate_shot(self, final_position: Vector3D) -> GameResult:
        """Evalúa si el tiro fue gol, atajada o fuera."""
        # Verificar si está dentro del arco
        half_width = self.goal_width / 2
        
        if final_position.y < self.penalty_distance:
            return GameResult.MISS  # No llegó al arco
        
        if abs(final_position.x) > half_width:
            return GameResult.MISS  # Fuera por los lados
        
        if final_position.z > self.goal_height:
            return GameResult.MISS  # Por arriba del travesaño
        
        if final_position.z < 0:
            return GameResult.MISS  # Por debajo (imposible pero por seguridad)
        
        # TODO: Integrar lógica del arquero (posición, reacción, etc.)
        # Por ahora, si está dentro del marco = GOL
        return GameResult.GOAL


# =============================================================================
# MOTOR DE FÍSICA - BÁSQUET (TIRO LIBRE)
# =============================================================================

class BasketballSimulator:
    """
    Simulador de tiros de básquet con física parabólica.
    Considera el aro como un cilindro con tolerancia de entrada.
    """
    
    def __init__(self):
        self.hoop_height = PhysicsConstants.BASKET_HOOP_HEIGHT
        self.hoop_radius = PhysicsConstants.BASKET_HOOP_RADIUS
        self.throw_distance = PhysicsConstants.FREE_THROW_DISTANCE
        self.max_power = 15.0  # Velocidad máxima para tiro libre
        self.dt = 0.016
    
    def simulate_shot(self, shot: ShotInput) -> ShotResult:
        """
        Simula un tiro de básquet.
        
        El éxito depende de que el balón pase por el aro con el ángulo correcto.
        """
        h_angle = math.radians(shot.angle_horizontal)
        v_angle = math.radians(shot.angle_vertical)
        
        speed = shot.power * self.max_power
        
        vx = speed * math.cos(v_angle) * math.sin(h_angle)
        vy = speed * math.cos(v_angle) * math.cos(h_angle)
        vz = speed * math.sin(v_angle)
        
        velocity = Vector3D(vx, vy, vz)
        position = Vector3D(
            shot.start_position.x,
            shot.start_position.y,
            shot.start_position.z
        )
        
        trajectory = [Vector3D(position.x, position.y, position.z)]
        simulation_time = 0.0
        max_iterations = 300
        
        passed_through_hoop = False
        closest_to_hoop = float('inf')
        
        for _ in range(max_iterations):
            # Gravedad
            velocity.z -= PhysicsConstants.GRAVITY * self.dt
            
            # Resistencia del aire
            velocity.x *= 0.995
            velocity.y *= 0.995
            
            # Backspin effect (tiro típico de básquet)
            if shot.spin_y < 0:  # Backspin
                velocity.z += abs(shot.spin_y) * 0.02 * self.dt
            
            # Actualizar posición
            prev_z = position.z
            position.x += velocity.x * self.dt
            position.y += velocity.y * self.dt
            position.z += velocity.z * self.dt
            
            simulation_time += self.dt
            trajectory.append(Vector3D(position.x, position.y, position.z))
            
            # Verificar paso por el aro
            if position.y >= self.throw_distance - 0.2 and position.y <= self.throw_distance + 0.2:
                # Cerca del aro horizontalmente
                distance_to_center = math.sqrt(position.x ** 2)
                height_diff = abs(position.z - self.hoop_height)
                
                # Actualizar distancia más cercana
                total_distance = math.sqrt(distance_to_center ** 2 + height_diff ** 2)
                closest_to_hoop = min(closest_to_hoop, total_distance)
                
                # ¿Pasó por el aro?
                if distance_to_center < self.hoop_radius and height_diff < 0.15:
                    if prev_z > position.z:  # Bajando (trayectoria correcta)
                        passed_through_hoop = True
            
            # Verificar si tocó el suelo
            if position.z <= 0:
                break
            
            # Verificar si pasó muy lejos
            if position.y > self.throw_distance + 2:
                break
        
        # Determinar resultado
        if passed_through_hoop:
            result = GameResult.SCORE
        elif closest_to_hoop < self.hoop_radius * 1.5:
            result = GameResult.MISS  # Rozó el aro
        else:
            result = GameResult.MISS  # Air ball
        
        return ShotResult(
            final_position=position,
            result=result,
            trajectory=trajectory,
            simulation_time=simulation_time
        )


# =============================================================================
# VALIDADOR PRINCIPAL (SHADOW SIMULATION)
# =============================================================================

class ShadowSimulationValidator:
    """
    Motor de validación de Shadow Simulation.
    Compara los resultados del cliente con la simulación del servidor.
    
    PRINCIPIO: Nunca confiar en el cliente. El servidor es la fuente de verdad.
    """
    
    def __init__(self):
        self.penalty_simulator = PenaltyKickSimulator()
        self.basketball_simulator = BasketballSimulator()
        self.validation_log: List[ValidationReport] = []
    
    def validate_penalty_shot(
        self,
        shot: ShotInput,
        client_result: GameResult,
        client_final_position: Optional[Vector3D] = None,
        match_id: str = "",
        shot_index: int = 0
    ) -> ValidationReport:
        """
        Valida un tiro de penal comparando cliente vs servidor.
        """
        # Ejecutar simulación del servidor
        server_result = self.penalty_simulator.simulate_shot(shot)
        server_result.result_hash = server_result.compute_hash(match_id, shot_index)
        
        return self._compare_results(
            server_result=server_result,
            client_result=client_result,
            client_final_position=client_final_position,
            game_type="PENALTY"
        )
    
    def validate_basketball_shot(
        self,
        shot: ShotInput,
        client_result: GameResult,
        client_final_position: Optional[Vector3D] = None,
        match_id: str = "",
        shot_index: int = 0
    ) -> ValidationReport:
        """
        Valida un tiro de básquet comparando cliente vs servidor.
        """
        server_result = self.basketball_simulator.simulate_shot(shot)
        server_result.result_hash = server_result.compute_hash(match_id, shot_index)
        
        return self._compare_results(
            server_result=server_result,
            client_result=client_result,
            client_final_position=client_final_position,
            game_type="BASKETBALL"
        )
    
    def _compare_results(
        self,
        server_result: ShotResult,
        client_result: GameResult,
        client_final_position: Optional[Vector3D],
        game_type: str
    ) -> ValidationReport:
        """
        Compara resultados y genera reporte de validación.
        """
        position_delta = 0.0
        requires_review = False
        details: Dict[str, Any] = {
            "game_type": game_type,
            "server_trajectory_points": len(server_result.trajectory),
            "simulation_time": server_result.simulation_time
        }
        
        # Calcular delta de posición si el cliente envió su resultado
        if client_final_position:
            position_delta = math.sqrt(
                (server_result.final_position.x - client_final_position.x) ** 2 +
                (server_result.final_position.y - client_final_position.y) ** 2 +
                (server_result.final_position.z - client_final_position.z) ** 2
            )
            details["position_delta"] = position_delta
        
        # Determinar resultado de validación
        if server_result.result == client_result:
            # Resultados coinciden
            if position_delta <= PhysicsConstants.POSITION_TOLERANCE:
                validation_result = ValidationResult.VALID
            else:
                validation_result = ValidationResult.MINOR_DISCREPANCY
                details["warning"] = "Posición final difiere pero resultado coincide"
        else:
            # Resultados NO coinciden - SOSPECHOSO
            if position_delta > PhysicsConstants.POSITION_TOLERANCE * 3:
                validation_result = ValidationResult.FRAUD_DETECTED
                requires_review = True
                details["alert"] = "Posible manipulación de resultados"
            else:
                validation_result = ValidationResult.MAJOR_DISCREPANCY
                requires_review = True
                details["warning"] = "Resultado del cliente no coincide con simulación"
        
        report = ValidationReport(
            validation_result=validation_result,
            server_result=server_result,
            client_result=client_result,
            position_delta=position_delta,
            details=details,
            requires_review=requires_review
        )
        
        self.validation_log.append(report)
        return report
    
    def get_validation_summary(self) -> Dict[str, Any]:
        """Retorna resumen de todas las validaciones realizadas."""
        total = len(self.validation_log)
        if total == 0:
            return {"total": 0, "valid_rate": 0}
        
        valid = sum(1 for r in self.validation_log if r.validation_result == ValidationResult.VALID)
        minor = sum(1 for r in self.validation_log if r.validation_result == ValidationResult.MINOR_DISCREPANCY)
        major = sum(1 for r in self.validation_log if r.validation_result == ValidationResult.MAJOR_DISCREPANCY)
        fraud = sum(1 for r in self.validation_log if r.validation_result == ValidationResult.FRAUD_DETECTED)
        
        return {
            "total": total,
            "valid": valid,
            "minor_discrepancies": minor,
            "major_discrepancies": major,
            "fraud_detected": fraud,
            "valid_rate": valid / total,
            "requires_manual_review": sum(1 for r in self.validation_log if r.requires_review)
        }


# =============================================================================
# FACTORY PARA OBTENER SIMULADOR SEGÚN TIPO DE JUEGO
# =============================================================================

class GameEngineFactory:
    """Factory para instanciar el motor de física correcto según el juego."""
    
    _simulators = {
        "PENALTY_KICKS": PenaltyKickSimulator,
        "BASKETBALL": BasketballSimulator,
    }
    
    @classmethod
    def get_simulator(cls, game_type: str):
        """
        Retorna el simulador correspondiente al tipo de juego.
        
        Args:
            game_type: Tipo de juego (debe coincidir con GameType enum)
            
        Returns:
            Instancia del simulador correspondiente
            
        Raises:
            ValueError: Si el tipo de juego no tiene simulador implementado
        """
        simulator_class = cls._simulators.get(game_type)
        if not simulator_class:
            raise ValueError(f"No hay simulador implementado para: {game_type}")
        return simulator_class()
    
    @classmethod
    def get_validator(cls) -> ShadowSimulationValidator:
        """Retorna el validador de Shadow Simulation."""
        return ShadowSimulationValidator()
    
    @classmethod
    def register_simulator(cls, game_type: str, simulator_class):
        """
        Registra un nuevo simulador para un tipo de juego.
        Permite que futuros repositorios de juegos se "enchufen" de forma segura.
        """
        cls._simulators[game_type] = simulator_class


# =============================================================================
# SISTEMA DE RAKE (COMISIONES) - v1.0.0
# =============================================================================

class RakeLevel(Enum):
    """Niveles de rake según monto de apuesta."""
    SEMILLA = "SEMILLA"       # 1-10 LKC: 8%
    COMPETIDOR = "COMPETIDOR" # 11-50 LKC: 6%
    PRO = "PRO"               # 51+ LKC: 5%


@dataclass
class RakeConfig:
    """Configuración de comisiones por nivel."""
    level: RakeLevel
    min_bet: Decimal
    max_bet: Decimal
    rate: Decimal  # Porcentaje como decimal (0.08 = 8%)
    
    def applies_to(self, bet_amount: Decimal) -> bool:
        """Verifica si esta configuración aplica al monto dado."""
        return self.min_bet <= bet_amount <= self.max_bet


class RakeCalculator:
    """
    Calculadora de comisiones (Rake) por partida.
    
    Niveles:
    - Semilla (1-10 LKC): 8% por jugador
    - Competidor (11-50 LKC): 6% por jugador
    - Pro (51+ LKC): 5% por jugador
    
    El rake se aplica POR JUGADOR, no sobre el pot total.
    """
    
    RAKE_TIERS = [
        RakeConfig(
            level=RakeLevel.SEMILLA,
            min_bet=Decimal("1"),
            max_bet=Decimal("10"),
            rate=Decimal("0.08")  # 8%
        ),
        RakeConfig(
            level=RakeLevel.COMPETIDOR,
            min_bet=Decimal("11"),
            max_bet=Decimal("50"),
            rate=Decimal("0.06")  # 6%
        ),
        RakeConfig(
            level=RakeLevel.PRO,
            min_bet=Decimal("51"),
            max_bet=Decimal("999999"),  # Sin límite superior práctico
            rate=Decimal("0.05")  # 5%
        ),
    ]
    
    @classmethod
    def get_rake_tier(cls, bet_amount: Decimal) -> RakeConfig:
        """
        Obtiene el tier de rake correspondiente al monto de apuesta.
        
        Args:
            bet_amount: Monto de apuesta por jugador en LKC
            
        Returns:
            RakeConfig del tier correspondiente
        """
        for tier in cls.RAKE_TIERS:
            if tier.applies_to(bet_amount):
                return tier
        
        # Default al tier Pro para montos muy altos
        return cls.RAKE_TIERS[-1]
    
    @classmethod
    def calculate_fee(
        cls,
        bet_amount: Decimal,
        num_players: int = 2
    ) -> Dict[str, Decimal]:
        """
        Calcula la comisión total y el premio neto.
        
        Args:
            bet_amount: Apuesta por jugador en LKC
            num_players: Número de jugadores (default 2)
            
        Returns:
            Dict con:
            - bet_per_player: Apuesta de cada jugador
            - fee_per_player: Comisión por jugador
            - total_pot: Pot total (apuestas de todos)
            - total_fee: Comisión total para LK_Treasury
            - winner_prize: Premio neto para el ganador
            - rake_rate: Tasa de rake aplicada
            - rake_level: Nivel de rake (SEMILLA/COMPETIDOR/PRO)
            
        Ejemplo Mesa 5 LKC (Nivel Semilla 8%):
            - fee_per_player: 0.40 LKC
            - total_pot: 10.00 LKC
            - total_fee: 0.80 LKC
            - winner_prize: 9.20 LKC
            
        Ejemplo Mesa 25 LKC (Nivel Competidor 6%):
            - fee_per_player: 1.50 LKC
            - total_pot: 50.00 LKC
            - total_fee: 3.00 LKC
            - winner_prize: 47.00 LKC
            
        Ejemplo Mesa 100 LKC (Nivel Pro 5%):
            - fee_per_player: 5.00 LKC
            - total_pot: 200.00 LKC
            - total_fee: 10.00 LKC
            - winner_prize: 190.00 LKC
        """
        tier = cls.get_rake_tier(bet_amount)
        
        fee_per_player = (bet_amount * tier.rate).quantize(Decimal("0.01"))
        total_pot = bet_amount * num_players
        total_fee = fee_per_player * num_players
        winner_prize = total_pot - total_fee
        
        return {
            "bet_per_player": bet_amount,
            "fee_per_player": fee_per_player,
            "total_pot": total_pot,
            "total_fee": total_fee,
            "winner_prize": winner_prize,
            "rake_rate": tier.rate,
            "rake_level": tier.level.value,
        }
    
    @classmethod
    def calculate_fee_breakdown(
        cls,
        bet_amount: Decimal
    ) -> str:
        """
        Genera un breakdown legible del cálculo de comisiones.
        Útil para UI y logs.
        """
        result = cls.calculate_fee(bet_amount)
        return (
            f"Mesa {bet_amount} LKC | Nivel: {result['rake_level']} ({result['rake_rate']*100}%)\n"
            f"  - Fee por jugador: -{result['fee_per_player']} LKC\n"
            f"  - Pot total: {result['total_pot']} LKC\n"
            f"  - Fee total → LK_Treasury: {result['total_fee']} LKC\n"
            f"  - Premio ganador: {result['winner_prize']} LKC"
        )


# =============================================================================
# LK_BOT - OPONENTE UNIVERSAL PARA PRUEBAS
# =============================================================================

@dataclass
class LKBotConfig:
    """Configuración del bot de pruebas."""
    BOT_USER_ID: str = "00000000-0000-0000-0000-000000000001"
    BOT_USERNAME: str = "LK_Bot"
    BOT_DISPLAY_NAME: str = "🤖 LK Bot"
    INFINITE_BALANCE: Decimal = Decimal("999999999.99")
    
    # Comportamiento en juegos
    AUTO_ACCEPT_MATCH: bool = True
    RESPONSE_DELAY_MS: int = 500  # Simula latencia humana
    
    # Probabilidad de victoria (para pruebas controladas)
    WIN_PROBABILITY: float = 0.5  # 50% - juego justo


class LKBot:
    """
    Bot oponente universal para pruebas de flujo.
    
    Características:
    - Saldo infinito para cualquier apuesta
    - Acepta automáticamente cualquier match
    - Permite testear el flujo LOCKED -> SETTLEMENT rápidamente
    - No afecta el Treasury en modo test
    """
    
    config = LKBotConfig()
    
    @classmethod
    def get_bot_profile(cls) -> Dict[str, Any]:
        """Retorna el perfil del bot para matchmaking."""
        return {
            "user_id": cls.config.BOT_USER_ID,
            "username": cls.config.BOT_USERNAME,
            "display_name": cls.config.BOT_DISPLAY_NAME,
            "balance": str(cls.config.INFINITE_BALANCE),
            "is_bot": True,
            "kyc_status": "VERIFIED",
            "trust_score": 100,
        }
    
    @classmethod
    def can_afford_bet(cls, bet_amount: Decimal) -> bool:
        """El bot siempre puede pagar cualquier apuesta."""
        return True
    
    @classmethod
    def should_accept_match(cls, game_type: str, bet_amount: Decimal) -> bool:
        """El bot acepta automáticamente cualquier match."""
        return cls.config.AUTO_ACCEPT_MATCH
    
    @classmethod
    def get_response_delay(cls) -> float:
        """Retorna delay para simular comportamiento humano."""
        import random
        base = cls.config.RESPONSE_DELAY_MS / 1000
        jitter = random.uniform(-0.1, 0.3)
        return max(0.1, base + jitter)
    
    @classmethod
    def decide_move(cls, game_state: Dict[str, Any]) -> Dict[str, Any]:
        """
        Decide el próximo movimiento del bot.
        Para Ludo: Selecciona una pieza válida al azar.
        """
        import random
        
        valid_moves = game_state.get("valid_moves", [])
        if not valid_moves:
            return {"action": "pass"}
        
        selected = random.choice(valid_moves)
        return {
            "action": "move",
            "piece_index": selected.get("piece_index", 0),
            "target_position": selected.get("target_position"),
        }
    
    @classmethod
    def is_bot_user(cls, user_id: str) -> bool:
        """Verifica si un user_id corresponde al bot."""
        return str(user_id) == cls.config.BOT_USER_ID


# =============================================================================
# AUDITORÍA LEDGER - TRIPLE ENTRADA
# =============================================================================

@dataclass
class LedgerEntry:
    """
    Entrada del libro mayor con triple entrada.
    
    Triple Entry Accounting:
    1. Débito: De dónde sale el dinero (perdedor o plataforma)
    2. Crédito: A dónde va el dinero (ganador o Treasury)
    3. Rake: Comisión que va al Treasury
    """
    entry_id: str
    match_id: str
    timestamp: float
    
    # Partes involucradas
    debitor_id: str         # Quien pierde/paga
    creditor_id: str        # Quien gana/recibe
    
    # Montos (requeridos, sin valor por defecto)
    debit_amount: Decimal   # Monto debitado del perdedor
    credit_amount: Decimal  # Monto acreditado al ganador (después de rake)
    rake_amount: Decimal    # Monto de comisión al Treasury
    
    # Treasury con valor por defecto
    treasury_id: str = "LK_TREASURY"
    
    # Hashes de integridad
    debitor_balance_hash_before: str = ""
    debitor_balance_hash_after: str = ""
    creditor_balance_hash_before: str = ""
    creditor_balance_hash_after: str = ""
    
    # Estado
    status: str = "PENDING"  # PENDING, COMMITTED, ROLLED_BACK
    
    def compute_entry_hash(self) -> str:
        """Calcula hash de la entrada para inmutabilidad."""
        data = {
            "entry_id": self.entry_id,
            "match_id": self.match_id,
            "timestamp": self.timestamp,
            "debitor_id": self.debitor_id,
            "creditor_id": self.creditor_id,
            "debit_amount": str(self.debit_amount),
            "credit_amount": str(self.credit_amount),
            "rake_amount": str(self.rake_amount),
        }
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    
    def validate_balance_equation(self) -> bool:
        """
        Valida que la ecuación de balance sea correcta:
        Débito = Crédito + Rake
        """
        return self.debit_amount == (self.credit_amount + self.rake_amount)


class TreasuryLedger:
    """
    Libro Mayor del Treasury con auditoría de triple entrada.
    
    Cada partida genera:
    1. Débito del perdedor (su apuesta completa)
    2. Crédito al ganador (pot - rake)
    3. Rake al LK_Treasury
    """
    
    TREASURY_ACCOUNT_ID = "LK_TREASURY"
    
    def __init__(self):
        self.entries: List[LedgerEntry] = []
        self.treasury_balance: Decimal = Decimal("0")
        self.pending_entries: Dict[str, LedgerEntry] = {}
    
    def create_match_settlement(
        self,
        match_id: str,
        winner_id: str,
        loser_id: str,
        bet_amount: Decimal,
        winner_balance_before: Decimal,
        loser_balance_before: Decimal
    ) -> LedgerEntry:
        """
        Crea la entrada de liquidación para una partida.
        
        Args:
            match_id: ID de la partida
            winner_id: ID del ganador
            loser_id: ID del perdedor
            bet_amount: Monto de apuesta por jugador
            winner_balance_before: Balance del ganador antes
            loser_balance_before: Balance del perdedor antes
            
        Returns:
            LedgerEntry con todos los cálculos
        """
        # Calcular rake usando el sistema profesional
        rake_result = RakeCalculator.calculate_fee(bet_amount)
        
        entry_id = f"LED-{match_id[:8]}-{int(time.time())}"
        
        # El perdedor pierde su apuesta completa
        debit_amount = bet_amount
        
        # El ganador recibe el pot menos el rake total
        credit_amount = rake_result["winner_prize"]
        
        # El rake va al Treasury
        rake_amount = rake_result["total_fee"]
        
        # Calcular nuevos balances
        loser_balance_after = loser_balance_before - debit_amount
        winner_balance_after = winner_balance_before + credit_amount
        
        entry = LedgerEntry(
            entry_id=entry_id,
            match_id=match_id,
            timestamp=time.time(),
            debitor_id=loser_id,
            creditor_id=winner_id,
            debit_amount=debit_amount,
            credit_amount=credit_amount,
            rake_amount=rake_amount,
            debitor_balance_hash_before=self._compute_balance_hash(loser_id, loser_balance_before),
            debitor_balance_hash_after=self._compute_balance_hash(loser_id, loser_balance_after),
            creditor_balance_hash_before=self._compute_balance_hash(winner_id, winner_balance_before),
            creditor_balance_hash_after=self._compute_balance_hash(winner_id, winner_balance_after),
            status="PENDING"
        )
        
        # Validar ecuación antes de guardar
        if not entry.validate_balance_equation():
            raise ValueError(f"Balance equation failed for entry {entry_id}")
        
        self.pending_entries[entry_id] = entry
        return entry
    
    def commit_entry(self, entry_id: str) -> bool:
        """
        Confirma una entrada pendiente y actualiza el Treasury.
        """
        entry = self.pending_entries.get(entry_id)
        if not entry:
            return False
        
        entry.status = "COMMITTED"
        self.entries.append(entry)
        self.treasury_balance += entry.rake_amount
        del self.pending_entries[entry_id]
        
        print(f"[LEDGER] Committed {entry_id}:")
        print(f"  - Loser {entry.debitor_id}: -{entry.debit_amount} LKC")
        print(f"  - Winner {entry.creditor_id}: +{entry.credit_amount} LKC")
        print(f"  - Treasury: +{entry.rake_amount} LKC (Total: {self.treasury_balance})")
        
        return True
    
    def rollback_entry(self, entry_id: str) -> bool:
        """
        Revierte una entrada pendiente.
        """
        entry = self.pending_entries.get(entry_id)
        if not entry:
            return False
        
        entry.status = "ROLLED_BACK"
        del self.pending_entries[entry_id]
        
        print(f"[LEDGER] Rolled back {entry_id}")
        return True
    
    def _compute_balance_hash(self, user_id: str, balance: Decimal) -> str:
        """Calcula el hash de balance para un usuario."""
        data = f"{user_id}:{balance}"
        return hashlib.sha256(data.encode()).hexdigest()
    
    def get_treasury_summary(self) -> Dict[str, Any]:
        """Retorna resumen del Treasury."""
        return {
            "total_balance": str(self.treasury_balance),
            "total_entries": len(self.entries),
            "pending_entries": len(self.pending_entries),
            "last_entry": self.entries[-1].entry_id if self.entries else None,
        }
    
    def verify_all_entries(self) -> Dict[str, Any]:
        """
        Verifica la integridad de todas las entradas.
        Recalcula el balance del Treasury y detecta anomalías.
        """
        calculated_balance = Decimal("0")
        invalid_entries = []
        
        for entry in self.entries:
            if not entry.validate_balance_equation():
                invalid_entries.append(entry.entry_id)
            calculated_balance += entry.rake_amount
        
        drift = self.treasury_balance - calculated_balance
        
        return {
            "total_entries_verified": len(self.entries),
            "invalid_entries": invalid_entries,
            "calculated_balance": str(calculated_balance),
            "recorded_balance": str(self.treasury_balance),
            "drift": str(drift),
            "integrity_status": "OK" if drift == 0 and not invalid_entries else "ALERT"
        }


# Instancia global del Treasury Ledger
treasury_ledger = TreasuryLedger()
