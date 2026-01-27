"""
=============================================================================
KOMPITE - Filtro de Seguridad LK-SHIELD
=============================================================================
Sistema de verificación pre-match para detectar fraude, colusión y
comportamiento sospechoso antes de permitir el ingreso a partidas.

Implementa:
- Verificación de Trust Score
- Detección de colusión (mismo IP/dispositivo)
- Análisis de patrones de comportamiento
- Rate limiting para prevenir abuso
=============================================================================
"""

import hashlib
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple
from uuid import UUID
import asyncio
from collections import defaultdict


# =============================================================================
# CONFIGURACIÓN DE SEGURIDAD
# =============================================================================

class SecurityConfig:
    """Configuración de umbrales de seguridad."""
    
    # Trust Score
    MIN_TRUST_SCORE_FOR_MATCH = 30          # Mínimo para entrar a partidas
    MIN_TRUST_SCORE_FOR_HIGH_STAKES = 70    # Mínimo para apuestas altas
    TRUST_PENALTY_DISCONNECT = 5             # Penalización por desconexión sospechosa
    TRUST_PENALTY_FRAUD_ATTEMPT = 25         # Penalización por intento de fraude
    
    # Detección de colusión
    MAX_ACCOUNTS_PER_IP = 2                  # Máximo de cuentas por IP
    MAX_ACCOUNTS_PER_DEVICE = 2              # Máximo de cuentas por dispositivo
    COLLUSION_DETECTION_WINDOW = 3600        # Ventana de tiempo en segundos (1 hora)
    
    # Rate limiting
    MAX_MATCH_REQUESTS_PER_MINUTE = 10       # Solicitudes de partida por minuto
    MAX_FAILED_MATCHES_PER_HOUR = 5          # Partidas fallidas por hora
    
    # Apuestas
    HIGH_STAKE_THRESHOLD = Decimal("100.0")  # Umbral de apuesta alta (LKoins)
    SUSPICIOUS_WIN_RATE = 0.85               # Tasa de victoria sospechosa (85%)
    MIN_MATCHES_FOR_WIN_RATE = 20            # Mínimo de partidas para calcular tasa
    
    # Tiempo de cuarentena
    QUARANTINE_DURATION_HOURS = 2            # Horas en cuarentena por sospecha


class ShieldVerdict(Enum):
    """Veredicto del filtro LK-SHIELD."""
    APPROVED = "APPROVED"                    # Puede entrar a la partida
    DENIED_LOW_TRUST = "DENIED_LOW_TRUST"    # Trust score muy bajo
    DENIED_COLLUSION = "DENIED_COLLUSION"    # Posible colusión detectada
    DENIED_RATE_LIMIT = "DENIED_RATE_LIMIT"  # Demasiadas solicitudes
    DENIED_QUARANTINE = "DENIED_QUARANTINE"  # En cuarentena
    DENIED_FROZEN = "DENIED_FROZEN"          # Cuenta congelada
    DENIED_KYC = "DENIED_KYC"                # KYC no verificado para apuesta alta
    REVIEW_REQUIRED = "REVIEW_REQUIRED"      # Requiere revisión manual


@dataclass
class PlayerSecurityProfile:
    """Perfil de seguridad de un jugador."""
    user_id: UUID
    trust_score: int
    trust_level: str  # GREEN, YELLOW, RED
    kyc_status: str
    is_frozen: bool
    device_fingerprint: Optional[str]
    current_ip: str
    lkoins_balance: Decimal
    
    # Historial reciente
    recent_matches: int = 0
    recent_wins: int = 0
    recent_disconnects: int = 0
    last_match_timestamp: Optional[float] = None
    
    # Flags de seguridad
    in_quarantine: bool = False
    quarantine_until: Optional[datetime] = None


@dataclass  
class ShieldCheckResult:
    """Resultado de la verificación LK-SHIELD."""
    verdict: ShieldVerdict
    player_id: UUID
    can_proceed: bool
    
    # Detalles
    reason: str = ""
    risk_score: int = 0  # 0-100, mayor = más riesgo
    flags: List[str] = field(default_factory=list)
    
    # Recomendaciones
    recommended_action: Optional[str] = None
    retry_after: Optional[int] = None  # Segundos hasta poder reintentar


@dataclass
class CollusionCheck:
    """Resultado de verificación de colusión entre dos jugadores."""
    player1_id: UUID
    player2_id: UUID
    is_suspicious: bool
    collusion_indicators: List[str] = field(default_factory=list)
    risk_level: str = "LOW"  # LOW, MEDIUM, HIGH, CRITICAL


# =============================================================================
# ALMACENAMIENTO EN MEMORIA (Redis en producción)
# =============================================================================

class SecurityMemoryStore:
    """
    Almacenamiento en memoria para datos de seguridad.
    En producción, esto debe ser reemplazado por Redis.
    """
    
    def __init__(self):
        # IP -> Set de user_ids que han usado esa IP
        self.ip_to_users: Dict[str, Set[UUID]] = defaultdict(set)
        
        # Device fingerprint -> Set de user_ids
        self.device_to_users: Dict[str, Set[UUID]] = defaultdict(set)
        
        # User ID -> timestamps de solicitudes de match
        self.match_requests: Dict[UUID, List[float]] = defaultdict(list)
        
        # User ID -> timestamps de partidas fallidas
        self.failed_matches: Dict[UUID, List[float]] = defaultdict(list)
        
        # User ID -> lista de IPs recientes
        self.user_ips: Dict[UUID, List[Tuple[str, float]]] = defaultdict(list)
        
        # Usuarios en cuarentena
        self.quarantine: Dict[UUID, datetime] = {}
        
        # Cache de historial de enfrentamientos (player1, player2) -> count
        self.encounter_history: Dict[Tuple[UUID, UUID], int] = defaultdict(int)
    
    def record_ip_usage(self, user_id: UUID, ip: str):
        """Registra uso de IP por usuario."""
        self.ip_to_users[ip].add(user_id)
        self.user_ips[user_id].append((ip, time.time()))
        # Limpiar registros antiguos (más de 24 horas)
        cutoff = time.time() - 86400
        self.user_ips[user_id] = [
            (ip, ts) for ip, ts in self.user_ips[user_id] if ts > cutoff
        ]
    
    def record_device_usage(self, user_id: UUID, device_fp: str):
        """Registra uso de dispositivo por usuario."""
        if device_fp:
            self.device_to_users[device_fp].add(user_id)
    
    def record_match_request(self, user_id: UUID):
        """Registra solicitud de partida."""
        now = time.time()
        self.match_requests[user_id].append(now)
        # Mantener solo último minuto
        cutoff = now - 60
        self.match_requests[user_id] = [
            ts for ts in self.match_requests[user_id] if ts > cutoff
        ]
    
    def record_failed_match(self, user_id: UUID):
        """Registra partida fallida."""
        now = time.time()
        self.failed_matches[user_id].append(now)
        # Mantener solo última hora
        cutoff = now - 3600
        self.failed_matches[user_id] = [
            ts for ts in self.failed_matches[user_id] if ts > cutoff
        ]
    
    def get_users_by_ip(self, ip: str) -> Set[UUID]:
        """Obtiene usuarios que han usado una IP."""
        return self.ip_to_users.get(ip, set())
    
    def get_users_by_device(self, device_fp: str) -> Set[UUID]:
        """Obtiene usuarios que han usado un dispositivo."""
        return self.device_to_users.get(device_fp, set())
    
    def get_match_request_count(self, user_id: UUID) -> int:
        """Cuenta solicitudes de partida en el último minuto."""
        now = time.time()
        cutoff = now - 60
        return len([ts for ts in self.match_requests.get(user_id, []) if ts > cutoff])
    
    def get_failed_match_count(self, user_id: UUID) -> int:
        """Cuenta partidas fallidas en la última hora."""
        now = time.time()
        cutoff = now - 3600
        return len([ts for ts in self.failed_matches.get(user_id, []) if ts > cutoff])
    
    def set_quarantine(self, user_id: UUID, hours: int = 2):
        """Pone a un usuario en cuarentena."""
        self.quarantine[user_id] = datetime.utcnow() + timedelta(hours=hours)
    
    def is_in_quarantine(self, user_id: UUID) -> Tuple[bool, Optional[datetime]]:
        """Verifica si un usuario está en cuarentena."""
        if user_id not in self.quarantine:
            return False, None
        
        until = self.quarantine[user_id]
        if datetime.utcnow() > until:
            del self.quarantine[user_id]
            return False, None
        
        return True, until
    
    def record_encounter(self, player1_id: UUID, player2_id: UUID):
        """Registra un enfrentamiento entre dos jugadores."""
        key = tuple(sorted([player1_id, player2_id]))
        self.encounter_history[key] += 1
    
    def get_encounter_count(self, player1_id: UUID, player2_id: UUID) -> int:
        """Obtiene número de enfrentamientos entre dos jugadores."""
        key = tuple(sorted([player1_id, player2_id]))
        return self.encounter_history.get(key, 0)


# Instancia global (en producción usar Redis)
security_store = SecurityMemoryStore()


# =============================================================================
# FILTRO LK-SHIELD PRINCIPAL
# =============================================================================

class LKShield:
    """
    Sistema de filtrado de seguridad LK-SHIELD.
    
    Verifica múltiples capas de seguridad antes de permitir
    que un jugador entre a una partida:
    
    1. Trust Score mínimo
    2. Estado de cuenta (no congelada, KYC válido)
    3. Rate limiting
    4. Detección de colusión
    5. Patrones de comportamiento sospechoso
    """
    
    def __init__(self, store: SecurityMemoryStore = None):
        self.store = store or security_store
        self.config = SecurityConfig()
    
    async def verify_player(
        self,
        profile: PlayerSecurityProfile,
        bet_amount: Decimal
    ) -> ShieldCheckResult:
        """
        Verifica si un jugador puede entrar a una partida.
        
        Args:
            profile: Perfil de seguridad del jugador
            bet_amount: Monto de la apuesta
            
        Returns:
            ShieldCheckResult con el veredicto
        """
        flags = []
        risk_score = 0
        
        # 1. Verificar si la cuenta está congelada
        if profile.is_frozen:
            return ShieldCheckResult(
                verdict=ShieldVerdict.DENIED_FROZEN,
                player_id=profile.user_id,
                can_proceed=False,
                reason="Cuenta congelada por seguridad",
                risk_score=100,
                flags=["ACCOUNT_FROZEN"]
            )
        
        # 2. Verificar cuarentena
        in_quarantine, until = self.store.is_in_quarantine(profile.user_id)
        if in_quarantine:
            retry_after = int((until - datetime.utcnow()).total_seconds())
            return ShieldCheckResult(
                verdict=ShieldVerdict.DENIED_QUARANTINE,
                player_id=profile.user_id,
                can_proceed=False,
                reason="Cuenta en cuarentena temporal",
                retry_after=retry_after,
                flags=["IN_QUARANTINE"]
            )
        
        # 3. Verificar Trust Score
        if profile.trust_score < self.config.MIN_TRUST_SCORE_FOR_MATCH:
            risk_score += 40
            flags.append("LOW_TRUST_SCORE")
            return ShieldCheckResult(
                verdict=ShieldVerdict.DENIED_LOW_TRUST,
                player_id=profile.user_id,
                can_proceed=False,
                reason=f"Trust Score muy bajo: {profile.trust_score}",
                risk_score=risk_score,
                flags=flags,
                recommended_action="Mejorar comportamiento para recuperar confianza"
            )
        
        # 4. Verificar Trust Level
        if profile.trust_level == "RED":
            risk_score += 30
            flags.append("RED_FLAG_ACCOUNT")
        elif profile.trust_level == "YELLOW":
            risk_score += 15
            flags.append("YELLOW_FLAG_ACCOUNT")
        
        # 5. Verificar KYC para apuestas altas
        if bet_amount >= self.config.HIGH_STAKE_THRESHOLD:
            if profile.kyc_status != "VERIFIED":
                return ShieldCheckResult(
                    verdict=ShieldVerdict.DENIED_KYC,
                    player_id=profile.user_id,
                    can_proceed=False,
                    reason="KYC requerido para apuestas altas",
                    risk_score=50,
                    flags=["HIGH_STAKE_NO_KYC"],
                    recommended_action="Completar verificación de identidad"
                )
            
            # Trust Score más alto para apuestas altas
            if profile.trust_score < self.config.MIN_TRUST_SCORE_FOR_HIGH_STAKES:
                risk_score += 20
                flags.append("LOW_TRUST_HIGH_STAKE")
        
        # 6. Rate limiting
        self.store.record_match_request(profile.user_id)
        request_count = self.store.get_match_request_count(profile.user_id)
        
        if request_count > self.config.MAX_MATCH_REQUESTS_PER_MINUTE:
            return ShieldCheckResult(
                verdict=ShieldVerdict.DENIED_RATE_LIMIT,
                player_id=profile.user_id,
                can_proceed=False,
                reason="Demasiadas solicitudes de partida",
                retry_after=60,
                flags=["RATE_LIMITED"]
            )
        
        # 7. Verificar partidas fallidas
        failed_count = self.store.get_failed_match_count(profile.user_id)
        if failed_count >= self.config.MAX_FAILED_MATCHES_PER_HOUR:
            risk_score += 25
            flags.append("MANY_FAILED_MATCHES")
        
        # 8. Registrar IP y dispositivo
        self.store.record_ip_usage(profile.user_id, profile.current_ip)
        if profile.device_fingerprint:
            self.store.record_device_usage(profile.user_id, profile.device_fingerprint)
        
        # 9. Verificar tasa de victoria sospechosa
        if profile.recent_matches >= self.config.MIN_MATCHES_FOR_WIN_RATE:
            win_rate = profile.recent_wins / profile.recent_matches
            if win_rate >= self.config.SUSPICIOUS_WIN_RATE:
                risk_score += 20
                flags.append("SUSPICIOUS_WIN_RATE")
        
        # 10. Verificar desconexiones recientes
        if profile.recent_disconnects >= 3:
            risk_score += 15
            flags.append("FREQUENT_DISCONNECTS")
        
        # Determinar veredicto final
        if risk_score >= 70:
            return ShieldCheckResult(
                verdict=ShieldVerdict.REVIEW_REQUIRED,
                player_id=profile.user_id,
                can_proceed=False,
                reason="Perfil requiere revisión manual",
                risk_score=risk_score,
                flags=flags,
                recommended_action="Esperar aprobación de administrador"
            )
        
        return ShieldCheckResult(
            verdict=ShieldVerdict.APPROVED,
            player_id=profile.user_id,
            can_proceed=True,
            reason="Verificación exitosa",
            risk_score=risk_score,
            flags=flags
        )
    
    async def check_collusion(
        self,
        player1: PlayerSecurityProfile,
        player2: PlayerSecurityProfile
    ) -> CollusionCheck:
        """
        Verifica posible colusión entre dos jugadores.
        
        Detecta:
        - Mismo IP
        - Mismo dispositivo
        - Historial de transferencias
        - Encuentros frecuentes con resultados predecibles
        """
        indicators = []
        risk_level = "LOW"
        
        # 1. Verificar IP compartida
        if player1.current_ip == player2.current_ip:
            indicators.append("SAME_IP")
            risk_level = "MEDIUM"
        
        # 2. Verificar dispositivo compartido
        if (player1.device_fingerprint and player2.device_fingerprint and
            player1.device_fingerprint == player2.device_fingerprint):
            indicators.append("SAME_DEVICE")
            risk_level = "HIGH"
        
        # 3. Verificar historial de IPs
        users_from_ip1 = self.store.get_users_by_ip(player1.current_ip)
        users_from_ip2 = self.store.get_users_by_ip(player2.current_ip)
        
        if player2.user_id in users_from_ip1 or player1.user_id in users_from_ip2:
            indicators.append("IP_HISTORY_OVERLAP")
            if risk_level != "HIGH":
                risk_level = "MEDIUM"
        
        # 4. Verificar historial de dispositivos
        if player1.device_fingerprint:
            users_from_device1 = self.store.get_users_by_device(player1.device_fingerprint)
            if player2.user_id in users_from_device1:
                indicators.append("DEVICE_HISTORY_OVERLAP")
                risk_level = "HIGH"
        
        # 5. Verificar encuentros previos frecuentes
        encounter_count = self.store.get_encounter_count(
            player1.user_id, player2.user_id
        )
        if encounter_count > 10:
            indicators.append("FREQUENT_ENCOUNTERS")
            if risk_level == "LOW":
                risk_level = "MEDIUM"
        
        is_suspicious = len(indicators) >= 2 or risk_level in ["HIGH", "CRITICAL"]
        
        if is_suspicious and risk_level == "HIGH" and len(indicators) >= 3:
            risk_level = "CRITICAL"
        
        return CollusionCheck(
            player1_id=player1.user_id,
            player2_id=player2.user_id,
            is_suspicious=is_suspicious,
            collusion_indicators=indicators,
            risk_level=risk_level
        )
    
    async def verify_match_entry(
        self,
        players: List[PlayerSecurityProfile],
        bet_amount: Decimal
    ) -> Tuple[bool, List[ShieldCheckResult], Optional[CollusionCheck]]:
        """
        Verifica entrada a partida para todos los jugadores.
        
        Returns:
            Tuple de (puede_proceder, resultados_individuales, check_colusion)
        """
        # Verificar cada jugador individualmente
        results = []
        for player in players:
            result = await self.verify_player(player, bet_amount)
            results.append(result)
        
        # Si algún jugador no puede proceder, denegar
        if not all(r.can_proceed for r in results):
            return False, results, None
        
        # Verificar colusión entre jugadores (si son 2)
        collusion_check = None
        if len(players) == 2:
            collusion_check = await self.check_collusion(players[0], players[1])
            
            if collusion_check.is_suspicious:
                # Modificar resultado para indicar posible colusión
                for result in results:
                    result.flags.append(f"COLLUSION_RISK_{collusion_check.risk_level}")
                    result.risk_score += 30 if collusion_check.risk_level == "HIGH" else 15
                
                if collusion_check.risk_level in ["HIGH", "CRITICAL"]:
                    return False, results, collusion_check
        
        # Registrar el encuentro
        if len(players) == 2:
            self.store.record_encounter(players[0].user_id, players[1].user_id)
        
        return True, results, collusion_check
    
    def apply_trust_penalty(self, user_id: UUID, reason: str, amount: int):
        """
        Aplica penalización al Trust Score.
        Este método debe integrarse con la base de datos.
        """
        # TODO: Integrar con SQLAlchemy para actualizar en BD
        pass
    
    def trigger_quarantine(self, user_id: UUID, reason: str):
        """Pone a un usuario en cuarentena."""
        self.store.set_quarantine(
            user_id, 
            self.config.QUARANTINE_DURATION_HOURS
        )


# =============================================================================
# INSTANCIA GLOBAL DEL SHIELD
# =============================================================================

lk_shield = LKShield()
