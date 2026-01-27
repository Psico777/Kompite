"""
=============================================================================
KOMPITE - Endpoints de Administración
=============================================================================
API REST para el panel de administración.
Incluye:
- Gestión de depósitos con aprobación atómica
- Consultas de usuarios, partidas y transacciones
- Webhook para automatización vía Termux Bridge
- Análisis OCR forense de comprobantes
=============================================================================
"""

import hashlib
import secrets
import base64
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Optional, List
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Header, Query, status, UploadFile, File, Form
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from sqlalchemy import select, func, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

# Importaciones internas
from .ocr_engine import ocr_engine, analyze_deposit_evidence, format_terminal_output, AnalysisVerdict

# Importaciones internas (ajustar según estructura del proyecto)
# from ..database import get_db_session
# from ..models import User, Deposit, Transaction, Match, DepositStatus, TransactionType, TransactionStatus
# from ..auth import verify_admin_token

router = APIRouter(prefix="/admin", tags=["Admin"])

# =============================================================================
# SECURITY
# =============================================================================
security = HTTPBearer()

# API Key para webhooks (en producción: almacenar en Redis/DB con hash)
WEBHOOK_API_KEYS = {}  # {key_hash: {user_id, created_at, name}}


async def get_current_admin(
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """
    Verifica que el token pertenece a un administrador.
    """
    token = credentials.credentials
    # TODO: Implementar verificación real con JWT
    # admin = await verify_admin_token(token)
    # if not admin:
    #     raise HTTPException(status_code=401, detail="Invalid admin token")
    # return admin
    return {"id": uuid4(), "name": "Admin", "role": "admin"}


# =============================================================================
# SCHEMAS
# =============================================================================

class DepositApproveRequest(BaseModel):
    """Request para aprobar un depósito."""
    pass  # Sin campos adicionales, el ID viene en la URL


class DepositRejectRequest(BaseModel):
    """Request para rechazar un depósito."""
    reason: str = Field(..., min_length=5, max_length=500)


class DepositResponse(BaseModel):
    """Respuesta con datos de depósito."""
    id: str
    user_id: str
    user_name: Optional[str]
    user_phone: str
    user_balance: str
    user_balance_hash: str
    amount_pen: str
    currency: str
    evidence_url: str
    transaction_reference: str
    status: str
    approved_by: Optional[str]
    approved_at: Optional[datetime]
    rejection_reason: Optional[str]
    created_at: datetime
    
    class Config:
        from_attributes = True


class WebhookDepositRequest(BaseModel):
    """Request del webhook de Termux Bridge."""
    transaction_reference: str = Field(..., min_length=3, max_length=100)
    amount_pen: Decimal = Field(..., gt=0)
    sender_phone: Optional[str] = None


class StatsResponse(BaseModel):
    """Estadísticas del dashboard."""
    total_lkoins_circulation: str
    pending_deposits: int
    today_approved: int
    flagged_users: int
    recent_deposits: List[dict]
    system_health: dict


# =============================================================================
# ENDPOINT: ESTADÍSTICAS DEL DASHBOARD
# =============================================================================

@router.get("/stats", response_model=StatsResponse)
async def get_admin_stats(
    admin = Depends(get_current_admin),
    # db: AsyncSession = Depends(get_db_session)
):
    """
    Obtiene estadísticas generales para el dashboard de administración.
    """
    # TODO: Implementar queries reales cuando DB esté disponible
    
    # Simulación de datos para desarrollo
    return StatsResponse(
        total_lkoins_circulation="125,430.5000",
        pending_deposits=5,
        today_approved=12,
        flagged_users=2,
        recent_deposits=[
            {
                "id": str(uuid4()),
                "user_name": "Juan Pérez",
                "user_phone": "+51999888777",
                "amount_pen": "50.00",
                "status": "PENDING",
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
            {
                "id": str(uuid4()),
                "user_name": "María García",
                "user_phone": "+51998877666",
                "amount_pen": "100.00",
                "status": "APPROVED",
                "created_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
            },
        ],
        system_health={
            "postgres": "healthy",
            "redis": "healthy",
            "websocket": "healthy",
            "celery": "healthy",
        }
    )


# =============================================================================
# ENDPOINTS: GESTIÓN DE DEPÓSITOS
# =============================================================================

@router.get("/deposits")
async def list_deposits(
    status: Optional[str] = Query(None, regex="^(PENDING|APPROVED|REJECTED)$"),
    date: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    admin = Depends(get_current_admin),
    # db: AsyncSession = Depends(get_db_session)
):
    """
    Lista depósitos con filtros opcionales.
    """
    # TODO: Implementar query real
    # query = select(Deposit).options(selectinload(Deposit.user))
    # if status:
    #     query = query.where(Deposit.status == DepositStatus(status))
    # if date:
    #     target_date = datetime.strptime(date, "%Y-%m-%d").date()
    #     query = query.where(func.date(Deposit.created_at) == target_date)
    # query = query.order_by(Deposit.created_at.desc())
    # query = query.offset((page - 1) * limit).limit(limit)
    # result = await db.execute(query)
    # deposits = result.scalars().all()
    
    # Simulación
    return {
        "deposits": [
            {
                "id": str(uuid4()),
                "user_id": str(uuid4()),
                "user_name": "Juan Pérez",
                "user_phone": "+51999888777",
                "user_balance": "150.0000",
                "user_balance_hash": hashlib.sha256(b"test").hexdigest(),
                "amount_pen": "50.00",
                "currency": "PEN",
                "evidence_url": "https://example.com/evidence.jpg",
                "transaction_reference": "YAPE-ABC123",
                "status": status or "PENDING",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        ],
        "total": 1,
        "page": page,
        "limit": limit,
    }


@router.post("/deposits/{deposit_id}/approve")
async def approve_deposit(
    deposit_id: UUID,
    admin = Depends(get_current_admin),
    # db: AsyncSession = Depends(get_db_session)
):
    """
    Aprueba un depósito y acredita LKoins al usuario.
    
    PROCESO ATÓMICO:
    1. Verifica que el depósito esté PENDING
    2. Valida balance_hash del usuario (integridad)
    3. Crea transacción DEPOSIT en el ledger
    4. Actualiza balance del usuario
    5. Recalcula balance_hash
    6. Actualiza estado del depósito a APPROVED
    
    Todo en una sola transacción de base de datos.
    """
    # async with db.begin():
    #     # 1. Obtener depósito con lock
    #     deposit = await db.get(Deposit, deposit_id, with_for_update=True)
    #     if not deposit:
    #         raise HTTPException(status_code=404, detail="Depósito no encontrado")
    #     
    #     if deposit.status != DepositStatus.PENDING:
    #         raise HTTPException(
    #             status_code=400, 
    #             detail=f"Depósito ya procesado: {deposit.status}"
    #         )
    #     
    #     # 2. Obtener usuario con lock
    #     user = await db.get(User, deposit.user_id, with_for_update=True)
    #     if not user:
    #         raise HTTPException(status_code=404, detail="Usuario no encontrado")
    #     
    #     # 3. Validar integridad del balance
    #     if not user.verify_balance_integrity():
    #         raise HTTPException(
    #             status_code=409,
    #             detail="ALERTA: Integridad de balance comprometida. Cuenta congelada."
    #         )
    #         # TODO: Congelar cuenta y notificar
    #     
    #     # 4. Obtener último hash de transacción para la cadena
    #     last_tx = await db.execute(
    #         select(Transaction)
    #         .where(Transaction.user_id == user.id)
    #         .order_by(Transaction.created_at.desc())
    #         .limit(1)
    #     )
    #     last_tx = last_tx.scalar_one_or_none()
    #     previous_hash = last_tx.transaction_hash if last_tx else None
    #     
    #     # 5. Crear transacción de depósito
    #     balance_before = user.lkoins_balance
    #     balance_after = balance_before + deposit.amount_pen  # 1 PEN = 1 LKoin
    #     
    #     transaction = Transaction(
    #         user_id=user.id,
    #         transaction_type=TransactionType.DEPOSIT,
    #         amount=deposit.amount_pen,
    #         balance_before=balance_before,
    #         balance_after=balance_after,
    #         status=TransactionStatus.COMPLETED,
    #         external_reference=deposit.transaction_reference,
    #         previous_tx_hash=previous_hash,
    #         metadata={
    #             "deposit_id": str(deposit.id),
    #             "evidence_url": deposit.evidence_url,
    #             "approved_by": str(admin["id"]),
    #         }
    #     )
    #     transaction.transaction_hash = transaction.compute_transaction_hash(previous_hash)
    #     transaction.completed_at = datetime.now(timezone.utc)
    #     db.add(transaction)
    #     
    #     # 6. Actualizar balance del usuario
    #     user.lkoins_balance = balance_after
    #     user.balance_version += 1
    #     user.balance_hash = user.compute_balance_hash()
    #     
    #     # 7. Actualizar depósito
    #     deposit.status = DepositStatus.APPROVED
    #     deposit.approved_by = admin["id"]
    #     deposit.approved_at = datetime.now(timezone.utc)
    #     deposit.transaction_id = transaction.id
    #     
    #     await db.commit()
    
    return {
        "success": True,
        "message": "Depósito aprobado y LKoins acreditados",
        "deposit_id": str(deposit_id),
        # "transaction_id": str(transaction.id),
        # "new_balance": str(balance_after),
    }


@router.post("/deposits/{deposit_id}/reject")
async def reject_deposit(
    deposit_id: UUID,
    request: DepositRejectRequest,
    admin = Depends(get_current_admin),
    # db: AsyncSession = Depends(get_db_session)
):
    """
    Rechaza un depósito pendiente.
    """
    # deposit = await db.get(Deposit, deposit_id)
    # if not deposit:
    #     raise HTTPException(status_code=404, detail="Depósito no encontrado")
    # 
    # if deposit.status != DepositStatus.PENDING:
    #     raise HTTPException(
    #         status_code=400, 
    #         detail=f"Depósito ya procesado: {deposit.status}"
    #     )
    # 
    # deposit.status = DepositStatus.REJECTED
    # deposit.rejection_reason = request.reason
    # deposit.approved_by = admin["id"]  # quien rechazó
    # deposit.approved_at = datetime.now(timezone.utc)
    # 
    # await db.commit()
    # 
    # # TODO: Notificar al usuario via push notification
    
    return {
        "success": True,
        "message": "Depósito rechazado",
        "deposit_id": str(deposit_id),
        "reason": request.reason,
    }


# =============================================================================
# ENDPOINTS: USUARIOS
# =============================================================================

@router.get("/users")
async def list_users(
    search: Optional[str] = Query(None),
    trust_level: Optional[str] = Query(None, regex="^(GREEN|YELLOW|RED)$"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    admin = Depends(get_current_admin),
    # db: AsyncSession = Depends(get_db_session)
):
    """
    Lista usuarios con filtros opcionales.
    """
    # TODO: Implementar query real
    return {
        "users": [
            {
                "id": str(uuid4()),
                "phone_number": "+51999888777",
                "full_name": "Juan Pérez",
                "lkoins_balance": "150.0000",
                "trust_level": trust_level or "GREEN",
                "kyc_status": "VERIFIED",
            }
        ],
        "total": 1,
        "page": page,
        "limit": limit,
    }


# =============================================================================
# ENDPOINTS: PARTIDAS
# =============================================================================

@router.get("/matches")
async def list_matches(
    status: Optional[str] = Query(None),
    game_type: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    admin = Depends(get_current_admin),
    # db: AsyncSession = Depends(get_db_session)
):
    """
    Lista partidas con filtros opcionales.
    """
    return {
        "matches": [
            {
                "id": str(uuid4()),
                "game_type": game_type or "PENALES",
                "players": [
                    {"id": str(uuid4()), "name": "Jugador 1"},
                    {"id": str(uuid4()), "name": "Jugador 2"},
                ],
                "bet_amount": "10.0000",
                "status": status or "IN_PROGRESS",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        ],
        "total": 1,
        "page": page,
        "limit": limit,
    }


# =============================================================================
# ENDPOINTS: TRANSACCIONES (LEDGER)
# =============================================================================

@router.get("/transactions")
async def list_transactions(
    type: Optional[str] = Query(None),
    user_id: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    admin = Depends(get_current_admin),
    # db: AsyncSession = Depends(get_db_session)
):
    """
    Lista transacciones del ledger con filtros opcionales.
    """
    return {
        "transactions": [
            {
                "id": str(uuid4()),
                "transaction_hash": hashlib.sha256(b"tx1").hexdigest(),
                "user_id": user_id or str(uuid4()),
                "user_name": "Juan Pérez",
                "transaction_type": type or "DEPOSIT",
                "amount": "50.0000",
                "balance_before": "100.0000",
                "balance_after": "150.0000",
                "status": "COMPLETED",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        ],
        "total": 1,
        "page": page,
        "limit": limit,
    }


# =============================================================================
# ENDPOINTS: WEBHOOKS
# =============================================================================

@router.get("/webhook/config")
async def get_webhook_config(
    admin = Depends(get_current_admin),
    # db: AsyncSession = Depends(get_db_session)
):
    """
    Obtiene la configuración del webhook para Termux Bridge.
    """
    # En producción: obtener de Redis/DB
    return {
        "api_key": "kp_live_" + secrets.token_hex(16),
        "recent_logs": [
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "success": True,
                "message": "Depósito YAPE-123 auto-aprobado",
            },
            {
                "timestamp": (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat(),
                "success": False,
                "message": "Referencia duplicada: YAPE-456",
            },
        ],
    }


@router.post("/webhook/regenerate-key")
async def regenerate_webhook_key(
    admin = Depends(get_current_admin),
    # db: AsyncSession = Depends(get_db_session)
):
    """
    Regenera la API Key del webhook.
    """
    new_key = "kp_live_" + secrets.token_hex(16)
    # TODO: Guardar hash en Redis/DB, invalidar key anterior
    
    return {
        "api_key": new_key,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


# =============================================================================
# WEBHOOK: VERIFICACIÓN AUTOMÁTICA DE DEPÓSITOS (Termux Bridge)
# =============================================================================

webhook_router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


async def verify_webhook_api_key(
    x_api_key: str = Header(..., alias="X-API-Key")
):
    """
    Verifica la API Key del webhook.
    """
    # En producción: verificar contra hash almacenado en Redis/DB
    if not x_api_key.startswith("kp_live_"):
        raise HTTPException(
            status_code=401,
            detail="Invalid API Key"
        )
    # TODO: Verificar hash de la key
    return True


@webhook_router.post("/deposit-verify")
async def webhook_deposit_verify(
    request: WebhookDepositRequest,
    _: bool = Depends(verify_webhook_api_key),
    # db: AsyncSession = Depends(get_db_session)
):
    """
    Webhook para verificación automática de depósitos desde Termux Bridge.
    
    FLUJO:
    1. Recibe notificación de transacción bancaria
    2. Busca depósito PENDING con la misma referencia
    3. Valida que el monto coincida
    4. Auto-aprueba el depósito
    
    SEGURIDAD:
    - Protegido por API Key
    - Rate limiting (implementar con Redis)
    - Logging de todas las operaciones
    """
    # async with db.begin():
    #     # 1. Buscar depósito pendiente con esta referencia
    #     result = await db.execute(
    #         select(Deposit)
    #         .where(
    #             and_(
    #                 Deposit.transaction_reference == request.transaction_reference,
    #                 Deposit.status == DepositStatus.PENDING
    #             )
    #         )
    #         .with_for_update()
    #     )
    #     deposit = result.scalar_one_or_none()
    #     
    #     if not deposit:
    #         # Puede ser referencia no registrada o ya procesada
    #         return {
    #             "success": False,
    #             "message": "No se encontró depósito pendiente con esta referencia",
    #             "reference": request.transaction_reference,
    #         }
    #     
    #     # 2. Validar monto
    #     if deposit.amount_pen != request.amount_pen:
    #         return {
    #             "success": False,
    #             "message": f"Monto no coincide. Esperado: {deposit.amount_pen}, Recibido: {request.amount_pen}",
    #             "reference": request.transaction_reference,
    #         }
    #     
    #     # 3. Auto-aprobar usando la misma lógica que approve_deposit
    #     user = await db.get(User, deposit.user_id, with_for_update=True)
    #     
    #     if not user.verify_balance_integrity():
    #         return {
    #             "success": False,
    #             "message": "Error de integridad de balance. Requiere revisión manual.",
    #             "reference": request.transaction_reference,
    #         }
    #     
    #     # ... (misma lógica que approve_deposit)
    #     
    #     await db.commit()
    
    return {
        "success": True,
        "message": "Depósito auto-aprobado por webhook",
        "reference": request.transaction_reference,
        "amount": str(request.amount_pen),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# =============================================================================
# ENDPOINT: ANÁLISIS OCR DE COMPROBANTES
# =============================================================================

@router.post("/deposits/analyze")
async def analyze_deposit_image(
    file: UploadFile = File(...),
    user_id: Optional[str] = Form(None),
    admin = Depends(get_current_admin),
):
    """
    Analiza una imagen de comprobante de pago usando el motor OCR forense.
    
    Retorna:
    - Datos extraídos (monto, referencia, fecha, proveedor)
    - Análisis forense (edición detectada, resolución, flags)
    - Veredicto y recomendaciones
    - Output formateado para terminal (admin audit)
    """
    # Validar tipo de archivo
    allowed_types = ["image/png", "image/jpeg", "image/jpg", "image/webp"]
    if file.content_type not in allowed_types:
        raise HTTPException(
            status_code=400,
            detail=f"Tipo de archivo no soportado: {file.content_type}. Use PNG, JPEG o WebP."
        )
    
    # Leer contenido
    image_data = await file.read()
    
    # Validar tamaño (máximo 10MB)
    if len(image_data) > 10 * 1024 * 1024:
        raise HTTPException(
            status_code=400,
            detail="Archivo demasiado grande. Máximo 10MB."
        )
    
    # Analizar imagen
    uid = UUID(user_id) if user_id else None
    result = await ocr_engine.analyze_image(
        image_data=image_data,
        filename=file.filename,
        user_id=uid,
        check_duplicates=True
    )
    
    # Generar output de terminal para auditoría
    terminal_output = format_terminal_output(result)
    
    return {
        **result.to_dict(),
        "terminal_output": terminal_output,
    }


# =============================================================================
# ENDPOINTS: RETIROS (ESCROW_OUT)
# =============================================================================

class WithdrawalRequest(BaseModel):
    """Request para solicitud de retiro."""
    amount: Decimal = Field(..., ge=5, description="Monto mínimo 5 LKoins")
    method: str = Field(..., regex="^(YAPE|PLIN|BANK_TRANSFER)$")
    destination: str = Field(..., min_length=9, max_length=20)


class WithdrawalResponse(BaseModel):
    """Respuesta de solicitud de retiro."""
    success: bool
    ticket_id: str
    amount: str
    method: str
    destination_masked: str
    status: str
    escrow_balance: str
    available_balance: str
    message: str


@router.get("/cajero")
async def get_withdrawal_queue(
    status: Optional[str] = Query("PENDING", regex="^(PENDING|PROCESSING|COMPLETED|REJECTED)$"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    admin = Depends(get_current_admin),
):
    """
    Lista retiros pendientes para el cajero (admin).
    
    Incluye:
    - Datos del usuario (nombre, teléfono, DNI)
    - Monto solicitado
    - Método de retiro (YAPE/PLIN/BANK)
    - Cuenta destino para pago manual
    - Trust level para priorización
    """
    # TODO: Query real a la base de datos
    return {
        "withdrawals": [
            {
                "ticket_id": f"WD-{secrets.token_hex(4).upper()}",
                "user_id": str(uuid4()),
                # Datos KYC para verificación
                "user_name": "Juan Carlos Pérez López",
                "user_dni": "********76",  # Últimos 2 dígitos
                "user_phone": "+51999888777",
                "user_kyc_status": "VERIFIED",
                # Solicitud
                "amount": "50.0000",
                "amount_pen": "50.00",  # En soles para pago
                "method": "YAPE",
                "destination": "999888777",
                "destination_display": "Yape: 999 888 777",
                # Datos bancarios completos para pago
                "payment_instructions": {
                    "method": "YAPE",
                    "number": "999888777",
                    "holder_name": "JUAN CARLOS PEREZ LOPEZ",
                    "verified": True
                },
                # Estado y auditoría
                "status": status,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "trust_level": "GREEN",
                "withdrawal_count_30d": 2,  # Retiros en últimos 30 días
                "total_withdrawn_30d": "120.00",
                # Alertas
                "alerts": [],
            }
        ],
        "total": 1,
        "total_amount_pending": "50.0000",
        "page": page,
        "limit": limit,
        # Recaudación info para el admin
        "treasury_info": {
            "target_account_holder": "Yordy Jesús Rojas Baldeon",
            "yape": "995665397",
            "plin": "960912996",
            "cci_caja_arequipa": "80312700531552100105"
        }
    }


@router.post("/cajero/{ticket_id}/process")
async def process_withdrawal(
    ticket_id: str,
    action: str = Query(..., regex="^(complete|reject)$"),
    rejection_reason: Optional[str] = Query(None),
    admin = Depends(get_current_admin),
):
    """
    Procesa un retiro: completa o rechaza.
    
    FLUJO COMPLETE:
    1. Marca retiro como COMPLETED
    2. Libera saldo de ESCROW_OUT
    3. Descuenta del balance real
    4. Registra en ledger
    
    FLUJO REJECT:
    1. Marca retiro como REJECTED
    2. Devuelve saldo de ESCROW_OUT a balance disponible
    3. Notifica al usuario
    """
    if action == "complete":
        return {
            "success": True,
            "ticket_id": ticket_id,
            "status": "COMPLETED",
            "processed_by": str(admin["id"]),
            "processed_at": datetime.now(timezone.utc).isoformat(),
            "message": "Retiro procesado exitosamente. El usuario ha sido notificado.",
        }
    else:
        if not rejection_reason:
            raise HTTPException(status_code=400, detail="Se requiere motivo de rechazo")
        return {
            "success": True,
            "ticket_id": ticket_id,
            "status": "REJECTED",
            "rejection_reason": rejection_reason,
            "processed_by": str(admin["id"]),
            "processed_at": datetime.now(timezone.utc).isoformat(),
            "message": "Retiro rechazado. El saldo ha sido devuelto al usuario.",
        }


# Endpoint público para solicitar retiro (wallet del usuario)
@wallet_router.post("/withdraw", response_model=WithdrawalResponse)
async def request_withdrawal(
    request: WithdrawalRequest,
    # user = Depends(get_current_user)
):
    """
    Solicita un retiro de LKoins.
    
    FLUJO:
    1. Valida que el usuario tenga balance suficiente
    2. Valida monto mínimo (5 LK)
    3. Mueve el monto a ESCROW_OUT (bloquea saldo)
    4. Crea ticket de retiro para el cajero
    5. Notifica al admin
    
    REGLAS DE NEGOCIO:
    - Mínimo de retiro: 5 LKoins
    - El saldo queda bloqueado hasta procesamiento
    - Un usuario solo puede tener 1 retiro pendiente
    """
    # Mock user data
    user_balance = Decimal("150.0000")
    user_escrow = Decimal("0.0000")
    
    if request.amount > user_balance:
        raise HTTPException(
            status_code=400,
            detail=f"Balance insuficiente. Disponible: {user_balance} LK"
        )
    
    if request.amount < 5:
        raise HTTPException(
            status_code=400,
            detail="El monto mínimo de retiro es 5 LKoins"
        )
    
    # Generar ticket
    ticket_id = f"WD-{secrets.token_hex(4).upper()}"
    
    # Mask destination
    dest = request.destination
    if len(dest) >= 9:
        masked = "***" + dest[-6:]
    else:
        masked = "***" + dest[-3:]
    
    # En producción:
    # 1. Iniciar transacción DB
    # 2. Restar de balance, agregar a escrow_out
    # 3. Crear registro de withdrawal
    # 4. Emitir notificación via WebSocket al admin
    # 5. Commit
    
    new_escrow = user_escrow + request.amount
    new_balance = user_balance - request.amount
    
    return WithdrawalResponse(
        success=True,
        ticket_id=ticket_id,
        amount=str(request.amount),
        method=request.method,
        destination_masked=masked,
        status="PENDING",
        escrow_balance=str(new_escrow),
        available_balance=str(new_balance),
        message=f"Tu retiro de {request.amount} LK está en proceso. Ticket: {ticket_id}"
    )


@wallet_router.get("/balance")
async def get_wallet_balance(
    # user = Depends(get_current_user)
):
    """
    Obtiene el balance del wallet del usuario.
    Incluye balance disponible, en escrow (partidas), y en escrow_out (retiros).
    """
    # Mock data
    return {
        "available": "150.0000",
        "escrow_match": "0.0000",      # Bloqueado en partidas activas
        "escrow_out": "0.0000",        # Bloqueado en retiros pendientes
        "total": "150.0000",
        "pending_withdrawal": None,     # Si hay un retiro pendiente, su info
        "fiat_equivalent": "150.00",   # En soles
    }


@wallet_router.post("/scan-receipt")
async def scan_receipt_for_deposit(
    file: UploadFile = File(...),
    # user = Depends(get_current_user)  # Auth de usuario normal
):
    """
    Escanea un comprobante de pago para pre-llenar el formulario de depósito.
    Usado desde el frontend del wallet del usuario.
    
    Retorna datos extraídos para pre-llenado si el análisis es positivo,
    o advertencias si requiere atención.
    """
    # Validar tipo de archivo
    allowed_types = ["image/png", "image/jpeg", "image/jpg", "image/webp"]
    if file.content_type not in allowed_types:
        raise HTTPException(
            status_code=400,
            detail=f"Tipo de archivo no soportado. Use PNG, JPEG o WebP."
        )
    
    image_data = await file.read()
    
    if len(image_data) > 10 * 1024 * 1024:
        raise HTTPException(
            status_code=400,
            detail="Archivo demasiado grande. Máximo 10MB."
        )
    
    # user_id = user.id  # Obtener del usuario autenticado
    user_id = None  # Mock
    
    result = await ocr_engine.analyze_image(
        image_data=image_data,
        filename=file.filename,
        user_id=user_id,
        check_duplicates=True
    )
    
    # Respuesta simplificada para el wallet
    response = {
        "success": result.success,
        "can_prefill": result.verdict == AnalysisVerdict.APPROVED,
        "needs_manual_input": result.verdict == AnalysisVerdict.NEEDS_REVIEW,
        "is_rejected": result.verdict in [AnalysisVerdict.REJECTED, AnalysisVerdict.SUSPICIOUS],
        "confidence": round(result.confidence, 2),
    }
    
    # Si es válido, incluir datos para pre-llenado
    if result.success and result.verdict in [AnalysisVerdict.APPROVED, AnalysisVerdict.NEEDS_REVIEW]:
        ed = result.extracted_data
        response["prefill_data"] = {
            "amount": str(ed.amount) if ed.amount else None,
            "transaction_reference": ed.transaction_reference,
            "provider": ed.provider.value,
            "provider_display": {
                "YAPE": "Yape",
                "PLIN": "Plin",
                "YAPE_TO_PLIN": "Yape → Plin",
                "PLIN_TO_YAPE": "Plin → Yape",
                "BCP": "BCP",
                "BBVA": "BBVA",
            }.get(ed.provider.value, ed.provider.value),
        }
    
    # Warnings para el usuario
    response["warnings"] = result.warnings
    
    # Mensaje principal
    if result.verdict == AnalysisVerdict.APPROVED:
        response["message"] = "✓ Comprobante detectado correctamente"
        response["message_type"] = "success"
    elif result.verdict == AnalysisVerdict.NEEDS_REVIEW:
        response["message"] = "⚠ Captura detectada. Por favor, asegúrese de que el Nro. de Operación sea legible para evitar demoras."
        response["message_type"] = "warning"
    elif result.verdict == AnalysisVerdict.SUSPICIOUS:
        response["message"] = "🚨 Se detectaron inconsistencias en la imagen. Suba una captura original sin editar."
        response["message_type"] = "error"
    else:
        response["message"] = result.errors[0] if result.errors else "No se pudo procesar el comprobante."
        response["message_type"] = "error"
    
    return response


# =============================================================================
# INCLUIR ROUTERS EN LA APP PRINCIPAL
# =============================================================================
# En main.py:
# from .admin import router as admin_router, webhook_router
# app.include_router(admin_router, prefix="/api/v1")
# app.include_router(webhook_router, prefix="/api/v1")
