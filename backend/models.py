"""
=============================================================================
KOMPITE - Modelos de Base de Datos (SQLAlchemy)
=============================================================================
Implementación del Sistema de Ledger de Triple Entrada para auditoría inmutable.
Incluye balance_hash SHA-256 para detección de manipulación y prevención de
Race Conditions mediante diseño de esquema.

Principios de Diseño:
- Neutralidad Operativa: La plataforma no participa en el juego
- Integridad Financiera: Transacciones atómicas con rollback inmediato
- Inmutabilidad: El saldo es un "estado derivado" del historial de transacciones
=============================================================================
"""

import hashlib
import secrets
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum as PyEnum
from typing import Optional, List
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.asyncio import AsyncAttrs, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


# =============================================================================
# ENUMERACIONES DEL SISTEMA
# =============================================================================

class KYCStatus(str, PyEnum):
    """Estado de verificación de identidad del usuario."""
    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"
    BANNED = "BANNED"


class TrustLevel(str, PyEnum):
    """Nivel de confianza basado en comportamiento histórico."""
    GREEN = "GREEN"      # Usuario confiable
    YELLOW = "YELLOW"    # Bajo vigilancia
    RED = "RED"          # Bandera roja - restricciones activas


class TransactionType(str, PyEnum):
    """
    Sistema de Triple Entrada para el Ledger.
    Cada transacción tiene tres componentes: DEBIT, CREDIT, y SYSTEM_FEE
    """
    DEPOSIT = "DEPOSIT"              # Entrada de fondos (compra de LKoins)
    WITHDRAWAL = "WITHDRAWAL"        # Salida de fondos (retiro)
    ESCROW_LOCK = "ESCROW_LOCK"      # Fondos bloqueados para partida
    ESCROW_RELEASE = "ESCROW_RELEASE"  # Fondos liberados de escrow
    PRIZE_CREDIT = "PRIZE_CREDIT"    # Crédito por victoria
    SYSTEM_FEE = "SYSTEM_FEE"        # Comisión de la plataforma (rake)
    ROLLBACK = "ROLLBACK"            # Reversión por fallo del sistema
    ADJUSTMENT = "ADJUSTMENT"        # Ajuste administrativo (requiere auditoría)


class DepositStatus(str, PyEnum):
    """Estado del depósito en el flujo de aprobación."""
    PENDING = "PENDING"      # Esperando revisión
    APPROVED = "APPROVED"    # Aprobado y acreditado
    REJECTED = "REJECTED"    # Rechazado (evidencia inválida)


class Currency(str, PyEnum):
    """Monedas soportadas para depósitos."""
    PEN = "PEN"    # Soles Peruanos (1 PEN = 1 LKoin)
    USD = "USD"    # Dólares Americanos
    USDT = "USDT"  # Tether (stablecoin)


class TransactionStatus(str, PyEnum):
    """Estado de la transacción en el Ledger."""
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    REVERSED = "REVERSED"


class MatchStatus(str, PyEnum):
    """
    Máquina de Estados Finita (FSM) para el ciclo de vida de partidas.
    Ref: Documentación sección 2.1
    """
    MATCHMAKING = "MATCHMAKING"  # Emparejamiento con Mutex
    LOCKED = "LOCKED"            # Fondos en Escrow, snapshot generado
    IN_PROGRESS = "IN_PROGRESS"  # Partida activa con Heartbeat
    VALIDATION = "VALIDATION"    # Auditoría forense y Shadow Simulation
    SETTLEMENT = "SETTLEMENT"    # Liquidación atómica en proceso
    COMPLETED = "COMPLETED"      # Partida finalizada exitosamente
    DISPUTED = "DISPUTED"        # Estado de resguardo - fondos congelados
    CANCELLED = "CANCELLED"      # Cancelada con rollback
    FROZEN_MATCH = "FROZEN_MATCH"  # Congelada para revisión manual


class EscrowStatus(str, PyEnum):
    """Estados del fideicomiso para fondos."""
    ACTIVE = "ACTIVE"            # Fondos bloqueados para partida activa
    RELEASED = "RELEASED"        # Fondos liberados al ganador
    REFUNDED = "REFUNDED"        # Fondos devueltos (rollback)
    FROZEN = "FROZEN"            # Congelado por disputa
    ESCROW_OUT = "ESCROW_OUT"    # En cola de retiro


class AuditStatus(str, PyEnum):
    """Estado de auditoría de integridad financiera."""
    SUCCESS = "SUCCESS"
    WARNING = "WARNING"
    CRITICAL_MISMATCH = "CRITICAL_MISMATCH"


class GameType(str, PyEnum):
    """Tipos de juegos disponibles en la plataforma."""
    ROCK_PAPER_SCISSORS = "ROCK_PAPER_SCISSORS"
    LUDO = "LUDO"
    MEMORY = "MEMORY"
    PENALTY_KICKS = "PENALTY_KICKS"
    BASKETBALL = "BASKETBALL"
    AIR_HOCKEY = "AIR_HOCKEY"


# =============================================================================
# BASE DECLARATIVA
# =============================================================================

class Base(AsyncAttrs, DeclarativeBase):
    """Clase base para todos los modelos con soporte async."""
    pass


# =============================================================================
# TABLA: USERS (Identidad y Patrimonio)
# =============================================================================

class User(Base):
    """
    Tabla de usuarios con balance_hash para detección de manipulación.
    
    SEGURIDAD: El balance_hash es un SHA-256 del saldo + user_id + salt.
    Si un atacante modifica el saldo directamente en la BD sin actualizar
    el hash, el sistema detecta la alteración y congela la cuenta.
    """
    __tablename__ = "users"
    
    # Identificación primaria
    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()")
    )
    
    # Datos de autenticación
    phone_number: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    email: Mapped[Optional[str]] = mapped_column(String(255), unique=True, nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    
    # Datos de identidad (KYC)
    full_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    dni_number: Mapped[Optional[str]] = mapped_column(String(20), unique=True, nullable=True)
    kyc_status: Mapped[KYCStatus] = mapped_column(
        Enum(KYCStatus),
        default=KYCStatus.PENDING,
        nullable=False
    )
    kyc_verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # ==========================================================================
    # SALDO Y BALANCE HASH (NO NEGOCIABLE)
    # Precisión: Numeric(12, 4) - máximo 99,999,999.9999 LKoins
    # Tasa de cambio: 1 PEN = 1 LKoin (LKC)
    # ==========================================================================
    lkoins_balance: Mapped[Decimal] = mapped_column(
        Numeric(12, 4),
        default=Decimal("0.0000"),
        nullable=False
    )
    
    # Hash SHA-256 para validación de integridad
    # Fórmula: SHA256(user_id + lkoins_balance + balance_salt)
    balance_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    balance_salt: Mapped[str] = mapped_column(String(32), nullable=False)
    
    # Versión del balance para control de concurrencia optimista
    # Previene Race Conditions al actualizar saldo
    balance_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    
    # ==========================================================================
    # CONFIANZA Y SEGURIDAD
    # ==========================================================================
    trust_level: Mapped[TrustLevel] = mapped_column(
        Enum(TrustLevel),
        default=TrustLevel.GREEN,
        nullable=False
    )
    trust_score: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    
    # Device Fingerprinting
    device_fingerprint: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    last_known_ip: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    
    # Banderas de seguridad
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_frozen: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    frozen_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False
    )
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Relaciones
    transactions: Mapped[List["Transaction"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan"
    )
    match_participations: Mapped[List["MatchParticipant"]] = relationship(
        back_populates="user"
    )
    
    # Índices para optimización
    __table_args__ = (
        Index("idx_users_phone", "phone_number"),
        Index("idx_users_dni", "dni_number"),
        Index("idx_users_kyc_status", "kyc_status"),
        Index("idx_users_trust_level", "trust_level"),
        Index("idx_users_is_active", "is_active"),
        CheckConstraint("lkoins_balance >= 0", name="check_positive_balance"),
        CheckConstraint("trust_score >= 0 AND trust_score <= 100", name="check_trust_score_range"),
    )
    
    def compute_balance_hash(self) -> str:
        """
        Calcula el hash SHA-256 del balance para validación de integridad.
        CRÍTICO: Este método debe ser llamado SIEMPRE que se modifique el saldo.
        """
        hash_input = f"{self.id}:{self.lkoins_balance}:{self.balance_salt}"
        return hashlib.sha256(hash_input.encode()).hexdigest()
    
    def verify_balance_integrity(self) -> bool:
        """
        Verifica que el balance no haya sido manipulado directamente en la BD.
        Retorna False si se detecta alteración.
        """
        expected_hash = self.compute_balance_hash()
        return secrets.compare_digest(self.balance_hash, expected_hash)
    
    @staticmethod
    def generate_balance_salt() -> str:
        """Genera un salt aleatorio para el hash del balance."""
        return secrets.token_hex(16)


# =============================================================================
# TABLA: TRANSACTIONS (Sistema de Ledger de Triple Entrada)
# =============================================================================

class Transaction(Base):
    """
    Libro Mayor (Ledger) con Triple Entrada para auditoría inmutable.
    
    PRINCIPIO: El saldo del usuario es un "estado derivado" de la suma de
    todas sus transacciones. Esto permite reconstruir el historial completo
    y detectar cualquier inconsistencia.
    
    TRIPLE ENTRADA:
    1. DEBIT: Lo que sale de una cuenta
    2. CREDIT: Lo que entra a una cuenta
    3. SYSTEM: Registro del sistema (fees, metadata)
    """
    __tablename__ = "transactions"
    
    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()")
    )
    
    # Referencia al usuario
    user_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False
    )
    
    # Referencia opcional a la partida (para escrow/premios)
    match_id: Mapped[Optional[UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("matches.id", ondelete="SET NULL"),
        nullable=True
    )
    
    # ==========================================================================
    # SISTEMA DE TRIPLE ENTRADA
    # ==========================================================================
    transaction_type: Mapped[TransactionType] = mapped_column(
        Enum(TransactionType),
        nullable=False
    )
    
    # Montos (siempre positivos, el tipo determina la dirección)
    amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 8),
        nullable=False
    )
    
    # Balance antes y después de la transacción (para auditoría)
    balance_before: Mapped[Decimal] = mapped_column(
        Numeric(18, 8),
        nullable=False
    )
    balance_after: Mapped[Decimal] = mapped_column(
        Numeric(18, 8),
        nullable=False
    )
    
    # Comisión del sistema (si aplica)
    system_fee: Mapped[Decimal] = mapped_column(
        Numeric(18, 8),
        default=Decimal("0.00000000"),
        nullable=False
    )
    
    # ==========================================================================
    # INMUTABILIDAD Y AUDITORÍA
    # ==========================================================================
    status: Mapped[TransactionStatus] = mapped_column(
        Enum(TransactionStatus),
        default=TransactionStatus.PENDING,
        nullable=False
    )
    
    # Hash de la transacción para cadena de integridad
    # Fórmula: SHA256(prev_tx_hash + amount + timestamp + user_id)
    transaction_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    previous_tx_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    
    # Referencia externa (para vincular con Yape/Plin/etc)
    external_reference: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    
    # Metadata adicional (IP, dispositivo, etc.)
    metadata: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    
    # Timestamps inmutables
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )
    
    # Solo para transacciones PENDING que se completan
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True
    )
    
    # Relaciones
    user: Mapped["User"] = relationship(back_populates="transactions")
    match: Mapped[Optional["Match"]] = relationship(back_populates="transactions")
    
    # Índices para optimización de consultas de auditoría
    __table_args__ = (
        Index("idx_tx_user_id", "user_id"),
        Index("idx_tx_match_id", "match_id"),
        Index("idx_tx_type", "transaction_type"),
        Index("idx_tx_status", "status"),
        Index("idx_tx_created_at", "created_at"),
        Index("idx_tx_hash", "transaction_hash"),
        CheckConstraint("amount >= 0", name="check_positive_amount"),
        CheckConstraint("system_fee >= 0", name="check_positive_fee"),
    )
    
    def compute_transaction_hash(self, previous_hash: Optional[str] = None) -> str:
        """
        Calcula el hash de la transacción para la cadena de integridad.
        Vincula cada transacción con la anterior para detectar manipulación.
        """
        prev = previous_hash or "GENESIS"
        hash_input = f"{prev}:{self.amount}:{self.created_at.isoformat()}:{self.user_id}"
        return hashlib.sha256(hash_input.encode()).hexdigest()


# =============================================================================
# TABLA: DEPOSITS (Sistema de Depósitos con Aprobación Manual/Automática)
# =============================================================================

class Deposit(Base):
    """
    Registro de depósitos de fondos en la plataforma.
    
    FLUJO:
    1. Usuario envía dinero via Yape/Plin/Transferencia
    2. Sube evidencia (captura de pantalla + referencia)
    3. Admin o Webhook valida y aprueba
    4. Sistema acredita LKoins atómicamente (1 PEN = 1 LKoin)
    
    SEGURIDAD:
    - Validación de balance_hash antes de acreditar
    - Registro inmutable de aprobación (quién y cuándo)
    - Webhook con API Key para automatización vía Termux
    """
    __tablename__ = "deposits"
    
    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()")
    )
    
    # Usuario que realiza el depósito
    user_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False
    )
    
    # Monto en moneda original (PEN por defecto)
    amount_pen: Mapped[Decimal] = mapped_column(
        Numeric(12, 4),
        nullable=False
    )
    
    # Moneda utilizada (para futuras conversiones)
    currency: Mapped[Currency] = mapped_column(
        Enum(Currency),
        default=Currency.PEN,
        nullable=False
    )
    
    # Evidencia del depósito
    evidence_url: Mapped[str] = mapped_column(String(500), nullable=False)
    
    # Referencia de la transacción (código Yape, número de operación, etc.)
    transaction_reference: Mapped[str] = mapped_column(String(100), nullable=False)
    
    # Estado del depósito
    status: Mapped[DepositStatus] = mapped_column(
        Enum(DepositStatus),
        default=DepositStatus.PENDING,
        nullable=False
    )
    
    # Metadata para auditoría de aprobación
    approved_by: Mapped[Optional[UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True
    )
    approved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True
    )
    rejection_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Referencia a la transacción generada al aprobar
    transaction_id: Mapped[Optional[UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("transactions.id", ondelete="SET NULL"),
        nullable=True
    )
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False
    )
    
    # Metadata adicional (IP, dispositivo, etc.)
    metadata: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    
    # Relaciones
    user: Mapped["User"] = relationship(foreign_keys=[user_id])
    approver: Mapped[Optional["User"]] = relationship(foreign_keys=[approved_by])
    transaction: Mapped[Optional["Transaction"]] = relationship()
    
    # Índices para optimización
    __table_args__ = (
        Index("idx_deposits_user_id", "user_id"),
        Index("idx_deposits_status", "status"),
        Index("idx_deposits_created_at", "created_at"),
        Index("idx_deposits_tx_reference", "transaction_reference"),
        CheckConstraint("amount_pen > 0", name="check_positive_deposit_amount"),
    )


# =============================================================================
# TABLA: GAMES (Configuración de Mesa)
# =============================================================================

class Game(Base):
    """
    Configuración de los juegos disponibles en la plataforma.
    Incluye seed_server para generación justa de números aleatorios (Provably Fair).
    """
    __tablename__ = "games"
    
    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()")
    )
    
    game_type: Mapped[GameType] = mapped_column(
        Enum(GameType),
        unique=True,
        nullable=False
    )
    
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Configuración de apuestas
    min_bet: Mapped[Decimal] = mapped_column(
        Numeric(18, 8),
        nullable=False
    )
    max_bet: Mapped[Decimal] = mapped_column(
        Numeric(18, 8),
        nullable=False
    )
    
    # Comisión de la plataforma (porcentaje)
    rake_percentage: Mapped[Decimal] = mapped_column(
        Numeric(5, 2),
        default=Decimal("5.00"),  # 5% por defecto
        nullable=False
    )
    
    # Seed del servidor para Provably Fair
    seed_server: Mapped[str] = mapped_column(String(64), nullable=False)
    seed_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    
    # Configuración de juego
    max_players: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    
    # Estado
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )
    
    # Relaciones
    matches: Mapped[List["Match"]] = relationship(back_populates="game")
    
    __table_args__ = (
        CheckConstraint("min_bet > 0", name="check_min_bet_positive"),
        CheckConstraint("max_bet >= min_bet", name="check_max_bet_gte_min"),
        CheckConstraint("rake_percentage >= 0 AND rake_percentage <= 100", name="check_rake_range"),
    )


# =============================================================================
# TABLA: MATCHES (El Libro de Actas)
# =============================================================================

class Match(Base):
    """
    Registro de partidas con firma de integridad.
    
    SEGURIDAD: El integrity_signature sella el historial de jugadas.
    Si alguien intenta editar una jugada pasada, la firma se rompe.
    """
    __tablename__ = "matches"
    
    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()")
    )
    
    # Referencia al juego
    game_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("games.id", ondelete="RESTRICT"),
        nullable=False
    )
    
    # ==========================================================================
    # ESTADO DE LA PARTIDA (FSM)
    # ==========================================================================
    status: Mapped[MatchStatus] = mapped_column(
        Enum(MatchStatus),
        default=MatchStatus.MATCHMAKING,
        nullable=False
    )
    
    # Monto apostado por jugador
    bet_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 8),
        nullable=False
    )
    
    # Pool total en escrow
    escrow_total: Mapped[Decimal] = mapped_column(
        Numeric(18, 8),
        default=Decimal("0.00000000"),
        nullable=False
    )
    
    # Comisión cobrada
    rake_collected: Mapped[Decimal] = mapped_column(
        Numeric(18, 8),
        default=Decimal("0.00000000"),
        nullable=False
    )
    
    # ==========================================================================
    # INTEGRIDAD Y AUDITORÍA
    # ==========================================================================
    # ID de sesión único inmutable
    session_id: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        nullable=False,
        default=lambda: secrets.token_hex(32)
    )
    
    # Hash de estado inicial (snapshot de fideicomiso)
    initial_state_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    
    # Firma digital del JSONB de jugadas
    integrity_signature: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    
    # Historial de jugadas (inmutable después de VALIDATION)
    moves_log: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    
    # Seeds para Provably Fair
    client_seed: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    server_seed: Mapped[str] = mapped_column(String(64), nullable=False)
    server_seed_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    
    # ==========================================================================
    # RESULTADOS
    # ==========================================================================
    winner_id: Mapped[Optional[UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True
    )
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Metadata de la partida
    metadata: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    
    # Relaciones
    game: Mapped["Game"] = relationship(back_populates="matches")
    participants: Mapped[List["MatchParticipant"]] = relationship(
        back_populates="match",
        cascade="all, delete-orphan"
    )
    transactions: Mapped[List["Transaction"]] = relationship(back_populates="match")
    escrow_entries: Mapped[List["Escrow"]] = relationship(back_populates="match")
    
    __table_args__ = (
        Index("idx_match_status", "status"),
        Index("idx_match_game", "game_id"),
        Index("idx_match_session", "session_id"),
        Index("idx_match_created", "created_at"),
        CheckConstraint("bet_amount > 0", name="check_bet_positive"),
        CheckConstraint("escrow_total >= 0", name="check_escrow_positive"),
    )
    
    def generate_initial_state_hash(self, participants_data: dict) -> str:
        """
        Genera el hash del estado inicial (snapshot de fideicomiso).
        Incluye saldos, IDs de dispositivo y direcciones IP.
        """
        hash_input = f"{self.id}:{self.session_id}:{participants_data}"
        return hashlib.sha256(hash_input.encode()).hexdigest()
    
    def sign_moves_log(self, secret_key: str) -> str:
        """
        Firma el historial de jugadas para garantizar inmutabilidad.
        Usa HMAC-SHA256 con una clave secreta del servidor.
        """
        import hmac
        import json
        moves_json = json.dumps(self.moves_log, sort_keys=True)
        return hmac.new(
            secret_key.encode(),
            moves_json.encode(),
            hashlib.sha256
        ).hexdigest()


# =============================================================================
# TABLA: MATCH_PARTICIPANTS (Jugadores por Partida)
# =============================================================================

class MatchParticipant(Base):
    """Relación entre usuarios y partidas con estado individual."""
    __tablename__ = "match_participants"
    
    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()")
    )
    
    match_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("matches.id", ondelete="CASCADE"),
        nullable=False
    )
    
    user_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False
    )
    
    # Estado del jugador en la partida
    is_ready: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_connected: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    
    # Snapshot al momento del LOCK
    balance_at_lock: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 8), nullable=True)
    device_id_at_lock: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    ip_at_lock: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    
    # Resultado individual
    is_winner: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    prize_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 8),
        default=Decimal("0.00000000"),
        nullable=False
    )
    
    # Timestamps de heartbeat
    last_heartbeat: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Relaciones
    match: Mapped["Match"] = relationship(back_populates="participants")
    user: Mapped["User"] = relationship(back_populates="match_participations")
    
    __table_args__ = (
        UniqueConstraint("match_id", "user_id", name="unique_match_participant"),
        Index("idx_participant_match", "match_id"),
        Index("idx_participant_user", "user_id"),
    )


# =============================================================================
# TABLA: ESCROW (Fideicomiso de Fondos)
# =============================================================================

class Escrow(Base):
    """
    Registro de fondos en fideicomiso para partidas.
    Garantiza que los fondos estén bloqueados durante toda la partida.
    """
    __tablename__ = "escrow"
    
    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()")
    )
    
    match_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("matches.id", ondelete="RESTRICT"),
        nullable=False
    )
    
    user_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False
    )
    
    amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 8),
        nullable=False
    )
    
    status: Mapped[EscrowStatus] = mapped_column(
        Enum(EscrowStatus),
        default=EscrowStatus.ACTIVE,
        nullable=False
    )
    
    # Timestamps
    locked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )
    released_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Relaciones
    match: Mapped["Match"] = relationship(back_populates="escrow_entries")
    
    __table_args__ = (
        Index("idx_escrow_match", "match_id"),
        Index("idx_escrow_user", "user_id"),
        Index("idx_escrow_status", "status"),
        CheckConstraint("amount > 0", name="check_escrow_amount_positive"),
    )


# =============================================================================
# TABLA: SYSTEM_AUDIT (Auditoría de Integridad Financiera)
# =============================================================================

class SystemAudit(Base):
    """
    Auditoría periódica de integridad financiera.
    Implementa el sistema de Checkpoints para detección de "drift".
    
    Ref: Documentación sección 3.2 - Optimización de Alto Rendimiento
    """
    __tablename__ = "system_audits"
    
    audit_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True
    )
    
    # Momento del checkpoint
    checkpoint_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )
    
    # ==========================================================================
    # VALIDACIÓN DE INTEGRIDAD
    # ==========================================================================
    # Suma calculada: (Saldo inicial + Entradas – Salidas)
    expected_vault: Mapped[Decimal] = mapped_column(
        Numeric(18, 8),
        nullable=False
    )
    
    # Suma real de la columna lkoins_balance de todos los usuarios
    actual_user_sum: Mapped[Decimal] = mapped_column(
        Numeric(18, 8),
        nullable=False
    )
    
    # Diferencia detectada (DEBE ser 0)
    drift_detected: Mapped[Decimal] = mapped_column(
        Numeric(18, 8),
        nullable=False
    )
    
    # Fondos actualmente en escrow
    total_in_escrow: Mapped[Decimal] = mapped_column(
        Numeric(18, 8),
        nullable=False
    )
    
    # Ganancias acumuladas de LK
    total_fees_collected: Mapped[Decimal] = mapped_column(
        Numeric(18, 8),
        nullable=False
    )
    
    # Número de usuarios verificados
    users_verified: Mapped[int] = mapped_column(Integer, nullable=False)
    
    # Número de usuarios con hash de balance inválido
    hash_mismatches_found: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    
    # Estado del checkpoint
    status: Mapped[AuditStatus] = mapped_column(
        Enum(AuditStatus),
        nullable=False
    )
    
    # Detalles adicionales
    details: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    
    __table_args__ = (
        Index("idx_audit_timestamp", "checkpoint_timestamp"),
        Index("idx_audit_status", "status"),
    )


# =============================================================================
# TABLA: DISTRIBUTED_LOCKS (Bloqueos Distribuidos - Backup en PostgreSQL)
# =============================================================================

class DistributedLock(Base):
    """
    Tabla de respaldo para bloqueos distribuidos.
    Complementa a Redis para garantizar consistencia en caso de falla.
    """
    __tablename__ = "distributed_locks"
    
    lock_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(255), nullable=False)
    acquired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    
    __table_args__ = (
        Index("idx_lock_expires", "expires_at"),
    )


# =============================================================================
# EVENT LISTENERS PARA INTEGRIDAD AUTOMÁTICA
# =============================================================================

@event.listens_for(User, "before_insert")
def user_before_insert(mapper, connection, target: User):
    """Genera el salt y hash inicial del balance antes de insertar."""
    if not target.balance_salt:
        target.balance_salt = User.generate_balance_salt()
    target.balance_hash = target.compute_balance_hash()


@event.listens_for(User, "before_update")
def user_before_update(mapper, connection, target: User):
    """Actualiza el hash del balance y la versión al modificar."""
    # Incrementar versión para control de concurrencia optimista
    target.balance_version += 1
    target.balance_hash = target.compute_balance_hash()
