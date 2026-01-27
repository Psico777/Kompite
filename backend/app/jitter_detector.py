"""
=============================================================================
KOMPITE - Detector de Jitter y Lag Switching
=============================================================================
Análisis de latencia para detectar "Lag Switching" - cuando un jugador
manipula su conexión para obtener ventaja en momentos críticos.

Ref: Documentación sección 2.1 - HEARTBEAT & SYNC
=============================================================================
"""

import time
import statistics
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Tuple
from uuid import UUID
from collections import deque


# =============================================================================
# CONFIGURACIÓN DE DETECCIÓN
# =============================================================================

class JitterConfig:
    """Configuración para detección de lag switching."""
    
    # Intervalo de heartbeat esperado (segundos)
    HEARTBEAT_INTERVAL = 3.0
    
    # Tolerancia para heartbeat (segundos)
    HEARTBEAT_TOLERANCE = 1.0
    
    # Número de muestras para calcular baseline
    BASELINE_SAMPLES = 10
    
    # Umbral de jitter sospechoso (desviación estándar)
    JITTER_THRESHOLD_STD = 2.5
    
    # Umbral de latencia absoluta (ms)
    LATENCY_SPIKE_THRESHOLD = 500
    
    # Número de spikes permitidos antes de marcar
    MAX_SPIKES_BEFORE_FLAG = 3
    
    # Ventana de tiempo para contar spikes (segundos)
    SPIKE_WINDOW = 60
    
    # Tiempo de gracia después de desconexión genuina (segundos)
    GRACE_PERIOD = 45
    
    # Umbral para detectar desconexión masiva (porcentaje de jugadores)
    MASS_DISCONNECT_THRESHOLD = 0.20


class ConnectionQuality(Enum):
    """Calidad de conexión del jugador."""
    EXCELLENT = "EXCELLENT"    # Latencia < 50ms, jitter bajo
    GOOD = "GOOD"              # Latencia 50-100ms
    FAIR = "FAIR"              # Latencia 100-200ms
    POOR = "POOR"              # Latencia 200-500ms
    CRITICAL = "CRITICAL"      # Latencia > 500ms o pérdida de paquetes


class DisconnectType(Enum):
    """Tipo de desconexión detectada."""
    GENUINE = "GENUINE"        # Parece conexión legítima perdida
    SUSPICIOUS = "SUSPICIOUS"  # Timing sospechoso
    LAG_SWITCH = "LAG_SWITCH"  # Patrón de lag switching detectado
    MASS_OUTAGE = "MASS_OUTAGE"  # Caída masiva de red


@dataclass
class HeartbeatSample:
    """Muestra de heartbeat individual."""
    timestamp: float           # Timestamp del servidor
    client_timestamp: float    # Timestamp del cliente
    round_trip_time: float     # RTT en ms
    sequence_number: int       # Número de secuencia
    game_state: str = ""       # Estado del juego al momento del heartbeat


@dataclass
class LatencyProfile:
    """Perfil de latencia de un jugador."""
    user_id: UUID
    
    # Estadísticas base
    baseline_rtt: float = 0.0        # RTT promedio baseline
    baseline_std: float = 0.0        # Desviación estándar baseline
    current_rtt: float = 0.0         # RTT actual
    
    # Historial
    samples: deque = field(default_factory=lambda: deque(maxlen=100))
    spike_timestamps: List[float] = field(default_factory=list)
    
    # Estado
    connection_quality: ConnectionQuality = ConnectionQuality.GOOD
    is_flagged: bool = False
    flag_reason: str = ""
    
    # Contador de heartbeats perdidos
    missed_heartbeats: int = 0
    last_heartbeat: float = 0.0
    
    # Para detección de patrones
    spikes_during_critical: int = 0
    total_critical_moments: int = 0


@dataclass
class JitterAnalysis:
    """Resultado del análisis de jitter."""
    user_id: UUID
    timestamp: float
    
    # Métricas
    current_jitter: float      # Variación actual vs baseline
    jitter_score: float        # 0-100, mayor = más sospechoso
    latency_trend: str         # STABLE, INCREASING, DECREASING, ERRATIC
    
    # Detección
    is_suspicious: bool = False
    disconnect_type: DisconnectType = DisconnectType.GENUINE
    
    # Detalles
    details: Dict = field(default_factory=dict)
    recommended_action: str = ""


# =============================================================================
# DETECTOR DE JITTER
# =============================================================================

class JitterDetector:
    """
    Sistema de detección de Lag Switching basado en análisis de jitter.
    
    Funcionalidad:
    1. Establecer baseline de latencia por jugador
    2. Detectar spikes de latencia en momentos críticos
    3. Identificar patrones de lag switching
    4. Diferenciar entre problemas genuinos y manipulación
    """
    
    def __init__(self):
        self.config = JitterConfig()
        self.profiles: Dict[UUID, LatencyProfile] = {}
        
        # Para detección de caídas masivas
        self.active_players: Dict[UUID, float] = {}  # user_id -> last_seen
        self.recent_disconnects: List[Tuple[UUID, float]] = []
    
    def register_player(self, user_id: UUID) -> LatencyProfile:
        """Registra un nuevo jugador para monitoreo."""
        if user_id not in self.profiles:
            self.profiles[user_id] = LatencyProfile(user_id=user_id)
        self.active_players[user_id] = time.time()
        return self.profiles[user_id]
    
    def unregister_player(self, user_id: UUID):
        """Elimina un jugador del monitoreo."""
        if user_id in self.profiles:
            del self.profiles[user_id]
        if user_id in self.active_players:
            del self.active_players[user_id]
    
    def process_heartbeat(
        self,
        user_id: UUID,
        client_timestamp: float,
        sequence_number: int,
        game_state: str = ""
    ) -> JitterAnalysis:
        """
        Procesa un heartbeat y retorna análisis de jitter.
        
        Args:
            user_id: ID del jugador
            client_timestamp: Timestamp enviado por el cliente
            sequence_number: Número de secuencia del heartbeat
            game_state: Estado actual del juego (para detectar timing)
            
        Returns:
            JitterAnalysis con resultado del análisis
        """
        server_time = time.time()
        profile = self.profiles.get(user_id)
        
        if not profile:
            profile = self.register_player(user_id)
        
        # Calcular RTT (simplificado - en producción usar NTP sync)
        rtt = (server_time - client_timestamp) * 1000  # Convertir a ms
        
        # Crear muestra
        sample = HeartbeatSample(
            timestamp=server_time,
            client_timestamp=client_timestamp,
            round_trip_time=rtt,
            sequence_number=sequence_number,
            game_state=game_state
        )
        
        # Agregar al historial
        profile.samples.append(sample)
        profile.current_rtt = rtt
        profile.last_heartbeat = server_time
        profile.missed_heartbeats = 0
        self.active_players[user_id] = server_time
        
        # Actualizar baseline si tenemos suficientes muestras
        if len(profile.samples) >= self.config.BASELINE_SAMPLES:
            self._update_baseline(profile)
        
        # Analizar jitter
        return self._analyze_jitter(profile, sample)
    
    def check_missed_heartbeat(self, user_id: UUID) -> Optional[JitterAnalysis]:
        """
        Verifica si un jugador ha perdido heartbeats.
        Debe llamarse periódicamente (cada HEARTBEAT_INTERVAL).
        """
        profile = self.profiles.get(user_id)
        if not profile:
            return None
        
        current_time = time.time()
        time_since_last = current_time - profile.last_heartbeat
        
        expected_interval = self.config.HEARTBEAT_INTERVAL + self.config.HEARTBEAT_TOLERANCE
        
        if time_since_last > expected_interval:
            profile.missed_heartbeats += 1
            
            # Verificar si es desconexión
            if profile.missed_heartbeats >= 3:
                disconnect_type = self._classify_disconnect(user_id)
                
                return JitterAnalysis(
                    user_id=user_id,
                    timestamp=current_time,
                    current_jitter=float('inf'),
                    jitter_score=100,
                    latency_trend="DISCONNECTED",
                    is_suspicious=(disconnect_type != DisconnectType.GENUINE and 
                                   disconnect_type != DisconnectType.MASS_OUTAGE),
                    disconnect_type=disconnect_type,
                    details={
                        "missed_heartbeats": profile.missed_heartbeats,
                        "last_seen": profile.last_heartbeat,
                        "time_since_last": time_since_last
                    },
                    recommended_action=self._get_disconnect_action(disconnect_type)
                )
        
        return None
    
    def _update_baseline(self, profile: LatencyProfile):
        """Actualiza el baseline de latencia del jugador."""
        recent_rtts = [s.round_trip_time for s in list(profile.samples)[-20:]]
        
        if len(recent_rtts) >= self.config.BASELINE_SAMPLES:
            # Filtrar outliers para el baseline
            sorted_rtts = sorted(recent_rtts)
            trimmed = sorted_rtts[2:-2] if len(sorted_rtts) > 4 else sorted_rtts
            
            profile.baseline_rtt = statistics.mean(trimmed)
            profile.baseline_std = statistics.stdev(trimmed) if len(trimmed) > 1 else 0
    
    def _analyze_jitter(
        self,
        profile: LatencyProfile,
        sample: HeartbeatSample
    ) -> JitterAnalysis:
        """Analiza el jitter de una muestra."""
        current_time = sample.timestamp
        
        # Si no hay baseline todavía, retornar análisis básico
        if profile.baseline_rtt == 0:
            return JitterAnalysis(
                user_id=profile.user_id,
                timestamp=current_time,
                current_jitter=0,
                jitter_score=0,
                latency_trend="ESTABLISHING_BASELINE",
                details={"samples_collected": len(profile.samples)}
            )
        
        # Calcular desviación del baseline
        deviation = sample.round_trip_time - profile.baseline_rtt
        normalized_deviation = deviation / (profile.baseline_std + 1)  # +1 para evitar div/0
        
        # Detectar spike
        is_spike = (
            sample.round_trip_time > self.config.LATENCY_SPIKE_THRESHOLD or
            normalized_deviation > self.config.JITTER_THRESHOLD_STD
        )
        
        if is_spike:
            profile.spike_timestamps.append(current_time)
            # Limpiar spikes antiguos
            cutoff = current_time - self.config.SPIKE_WINDOW
            profile.spike_timestamps = [
                ts for ts in profile.spike_timestamps if ts > cutoff
            ]
            
            # Verificar si el spike ocurre en momento crítico
            if self._is_critical_moment(sample.game_state):
                profile.spikes_during_critical += 1
                profile.total_critical_moments += 1
        elif self._is_critical_moment(sample.game_state):
            profile.total_critical_moments += 1
        
        # Calcular score de sospecha (0-100)
        jitter_score = self._calculate_jitter_score(profile, normalized_deviation)
        
        # Determinar calidad de conexión
        profile.connection_quality = self._classify_connection(sample.round_trip_time)
        
        # Determinar tendencia
        latency_trend = self._calculate_trend(profile)
        
        # Detectar lag switching
        is_suspicious = False
        flag_reason = ""
        
        if len(profile.spike_timestamps) >= self.config.MAX_SPIKES_BEFORE_FLAG:
            is_suspicious = True
            flag_reason = "Multiple latency spikes detected"
        
        # Patrón de lag switching: spikes solo en momentos críticos
        if profile.total_critical_moments >= 5:
            critical_spike_ratio = profile.spikes_during_critical / profile.total_critical_moments
            if critical_spike_ratio > 0.6:  # 60% de spikes en momentos críticos
                is_suspicious = True
                flag_reason = "Suspicious spike timing pattern"
                jitter_score = min(100, jitter_score + 30)
        
        if is_suspicious and not profile.is_flagged:
            profile.is_flagged = True
            profile.flag_reason = flag_reason
        
        return JitterAnalysis(
            user_id=profile.user_id,
            timestamp=current_time,
            current_jitter=normalized_deviation,
            jitter_score=jitter_score,
            latency_trend=latency_trend,
            is_suspicious=is_suspicious,
            disconnect_type=DisconnectType.GENUINE,
            details={
                "rtt": sample.round_trip_time,
                "baseline_rtt": profile.baseline_rtt,
                "deviation": deviation,
                "spike_count": len(profile.spike_timestamps),
                "connection_quality": profile.connection_quality.value,
                "critical_spike_ratio": (
                    profile.spikes_during_critical / max(1, profile.total_critical_moments)
                )
            },
            recommended_action="MONITOR" if is_suspicious else ""
        )
    
    def _is_critical_moment(self, game_state: str) -> bool:
        """Determina si el estado del juego es un momento crítico."""
        critical_states = [
            "SHOOTING", "DEFENDING", "PENALTY", "MATCH_POINT",
            "FINAL_MOVE", "WINNING_POSITION", "LOSING_POSITION"
        ]
        return game_state.upper() in critical_states
    
    def _calculate_jitter_score(
        self,
        profile: LatencyProfile,
        normalized_deviation: float
    ) -> float:
        """Calcula el score de sospecha basado en jitter."""
        score = 0.0
        
        # Factor 1: Desviación actual
        score += min(30, abs(normalized_deviation) * 10)
        
        # Factor 2: Número de spikes recientes
        spike_factor = len(profile.spike_timestamps) * 5
        score += min(30, spike_factor)
        
        # Factor 3: Ratio de spikes en momentos críticos
        if profile.total_critical_moments > 0:
            critical_ratio = profile.spikes_during_critical / profile.total_critical_moments
            score += critical_ratio * 40
        
        return min(100, score)
    
    def _classify_connection(self, rtt: float) -> ConnectionQuality:
        """Clasifica la calidad de conexión basado en RTT."""
        if rtt < 50:
            return ConnectionQuality.EXCELLENT
        elif rtt < 100:
            return ConnectionQuality.GOOD
        elif rtt < 200:
            return ConnectionQuality.FAIR
        elif rtt < 500:
            return ConnectionQuality.POOR
        else:
            return ConnectionQuality.CRITICAL
    
    def _calculate_trend(self, profile: LatencyProfile) -> str:
        """Calcula la tendencia de latencia."""
        if len(profile.samples) < 5:
            return "INSUFFICIENT_DATA"
        
        recent = [s.round_trip_time for s in list(profile.samples)[-10:]]
        older = [s.round_trip_time for s in list(profile.samples)[-20:-10]]
        
        if not older:
            return "ESTABLISHING"
        
        recent_avg = statistics.mean(recent)
        older_avg = statistics.mean(older)
        
        diff = recent_avg - older_avg
        threshold = profile.baseline_std * 0.5
        
        if abs(diff) < threshold:
            return "STABLE"
        elif diff > 0:
            return "INCREASING"
        else:
            return "DECREASING"
    
    def _classify_disconnect(self, user_id: UUID) -> DisconnectType:
        """Clasifica el tipo de desconexión."""
        current_time = time.time()
        
        # Registrar desconexión
        self.recent_disconnects.append((user_id, current_time))
        
        # Limpiar desconexiones antiguas (últimos 30 segundos)
        cutoff = current_time - 30
        self.recent_disconnects = [
            (uid, ts) for uid, ts in self.recent_disconnects if ts > cutoff
        ]
        
        # Verificar caída masiva
        total_active = len(self.active_players)
        recent_disconnect_count = len(self.recent_disconnects)
        
        if total_active > 0:
            disconnect_ratio = recent_disconnect_count / total_active
            if disconnect_ratio >= self.config.MASS_DISCONNECT_THRESHOLD:
                return DisconnectType.MASS_OUTAGE
        
        # Verificar perfil del usuario
        profile = self.profiles.get(user_id)
        if profile:
            # Si ya estaba flaggeado, más sospechoso
            if profile.is_flagged:
                return DisconnectType.LAG_SWITCH
            
            # Si tenía muchos spikes, sospechoso
            if len(profile.spike_timestamps) >= 2:
                return DisconnectType.SUSPICIOUS
        
        return DisconnectType.GENUINE
    
    def _get_disconnect_action(self, disconnect_type: DisconnectType) -> str:
        """Retorna la acción recomendada para un tipo de desconexión."""
        actions = {
            DisconnectType.GENUINE: "APPLY_GRACE_PERIOD",
            DisconnectType.SUSPICIOUS: "MONITOR_ON_RECONNECT",
            DisconnectType.LAG_SWITCH: "FLAG_FOR_REVIEW",
            DisconnectType.MASS_OUTAGE: "PAUSE_MATCH_OR_ROLLBACK"
        }
        return actions.get(disconnect_type, "MONITOR")
    
    def get_player_summary(self, user_id: UUID) -> Optional[Dict]:
        """Retorna resumen del perfil de latencia de un jugador."""
        profile = self.profiles.get(user_id)
        if not profile:
            return None
        
        return {
            "user_id": str(user_id),
            "baseline_rtt": profile.baseline_rtt,
            "current_rtt": profile.current_rtt,
            "connection_quality": profile.connection_quality.value,
            "is_flagged": profile.is_flagged,
            "flag_reason": profile.flag_reason,
            "spike_count": len(profile.spike_timestamps),
            "missed_heartbeats": profile.missed_heartbeats,
            "samples_collected": len(profile.samples)
        }


# =============================================================================
# INSTANCIA GLOBAL
# =============================================================================

jitter_detector = JitterDetector()
