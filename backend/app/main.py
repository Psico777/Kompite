"""
=============================================================================
KOMPITE - Punto de Entrada Principal (FastAPI + Socket.IO)
=============================================================================
Servidor de la Infraestructura de Arbitraje para Economía de Habilidad.
Implementa Neutralidad Operativa y Transparencia Algorítmica.

Integra:
- FastAPI para REST API
- Socket.IO para comunicación en tiempo real (WebSockets)
- Middleware de seguridad y CORS
=============================================================================
"""

import time
from contextlib import asynccontextmanager
from typing import Any, Dict

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .websocket_handler import sio, create_socket_app, match_manager
from .security import lk_shield, PlayerSecurityProfile
from .jitter_detector import jitter_detector
from .game_engine import GameEngineFactory, ShadowSimulationValidator
from .admin import router as admin_router, webhook_router, wallet_router


# =============================================================================
# LIFECYCLE
# =============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Gestiona el ciclo de vida de la aplicación."""
    # Startup
    print("[KOMPITE] Iniciando servidor...")
    print("[KOMPITE] Motor de física cargado")
    print("[KOMPITE] LK-SHIELD activo")
    print("[KOMPITE] Detector de Jitter iniciado")
    yield
    # Shutdown
    print("[KOMPITE] Cerrando servidor...")


# =============================================================================
# APLICACIÓN FASTAPI
# =============================================================================

app = FastAPI(
    title="Kompite API",
    description="""
    ## Infraestructura de Arbitraje para Economía de Habilidad con LKoins
    
    ### Características:
    - **Neutralidad Operativa**: La plataforma no participa en el juego
    - **Shadow Simulation**: Validación física independiente
    - **LK-SHIELD**: Sistema de seguridad anti-fraude
    - **WebSockets**: Comunicación bidireccional en tiempo real
    
    ### Estados de Partida (FSM):
    1. MATCHMAKING → LOCKED → IN_PROGRESS → VALIDATION → SETTLEMENT → COMPLETED
    """,
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan
)


# =============================================================================
# MIDDLEWARE
# =============================================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configurar dominios específicos en producción
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """Agrega headers de seguridad a las respuestas."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


# =============================================================================
# ENDPOINTS - HEALTH & STATUS
# =============================================================================

@app.get("/health")
async def health_check():
    """Endpoint de health check para Docker y load balancers."""
    return {
        "status": "healthy",
        "service": "kompite-backend",
        "version": "0.1.0",
        "timestamp": time.time()
    }


@app.get("/")
async def root():
    """Endpoint raíz con información básica del servicio."""
    return {
        "message": "Bienvenido a Kompite API",
        "docs": "/docs",
        "health": "/health",
        "websocket": "/socket.io",
        "version": "0.1.0"
    }


@app.get("/api/v1/status")
async def server_status():
    """Estado detallado del servidor."""
    return {
        "server": "online",
        "active_matches": len(match_manager.active_matches),
        "players_in_matchmaking": sum(
            len(q) for q in match_manager.matchmaking_queue.values()
        ),
        "jitter_profiles_active": len(jitter_detector.profiles),
        "timestamp": time.time()
    }


# =============================================================================
# ENDPOINTS - GAME ENGINE
# =============================================================================

@app.get("/api/v1/games")
async def list_games():
    """Lista los juegos disponibles."""
    return {
        "games": [
            {
                "type": "PENALTY_KICKS",
                "name": "Penales",
                "description": "Tiro de penales 1v1",
                "physics_enabled": True,
                "min_bet": 1.0,
                "max_bet": 1000.0
            },
            {
                "type": "BASKETBALL",
                "name": "Tiro Libre",
                "description": "Competencia de tiros libres",
                "physics_enabled": True,
                "min_bet": 1.0,
                "max_bet": 1000.0
            },
            {
                "type": "ROCK_PAPER_SCISSORS",
                "name": "Piedra, Papel, Tijera",
                "description": "Clásico juego de destreza mental",
                "physics_enabled": False,
                "min_bet": 0.5,
                "max_bet": 100.0
            }
        ]
    }


@app.get("/api/v1/games/{game_type}/physics-config")
async def get_physics_config(game_type: str):
    """Obtiene la configuración de física para un juego."""
    try:
        simulator = GameEngineFactory.get_simulator(game_type)
        
        if game_type == "PENALTY_KICKS":
            return {
                "game_type": game_type,
                "goal_width": simulator.goal_width,
                "goal_height": simulator.goal_height,
                "penalty_distance": simulator.penalty_distance,
                "max_power": simulator.max_power,
                "dt": simulator.dt
            }
        elif game_type == "BASKETBALL":
            return {
                "game_type": game_type,
                "hoop_height": simulator.hoop_height,
                "hoop_radius": simulator.hoop_radius,
                "throw_distance": simulator.throw_distance,
                "max_power": simulator.max_power,
                "dt": simulator.dt
            }
        
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# =============================================================================
# ENDPOINTS - SECURITY (LK-SHIELD)
# =============================================================================

@app.post("/api/v1/security/verify-player")
async def verify_player_endpoint(request: Request):
    """
    Verifica si un jugador puede entrar a una partida.
    Usado internamente antes del matchmaking.
    """
    data = await request.json()
    
    from decimal import Decimal
    from uuid import UUID
    
    profile = PlayerSecurityProfile(
        user_id=UUID(data['user_id']),
        trust_score=data.get('trust_score', 100),
        trust_level=data.get('trust_level', 'GREEN'),
        kyc_status=data.get('kyc_status', 'PENDING'),
        is_frozen=data.get('is_frozen', False),
        device_fingerprint=data.get('device_fingerprint'),
        current_ip=data.get('current_ip', ''),
        lkoins_balance=Decimal(str(data.get('lkoins_balance', 0)))
    )
    
    bet_amount = Decimal(str(data.get('bet_amount', 0)))
    
    result = await lk_shield.verify_player(profile, bet_amount)
    
    return {
        "verdict": result.verdict.value,
        "can_proceed": result.can_proceed,
        "reason": result.reason,
        "risk_score": result.risk_score,
        "flags": result.flags,
        "retry_after": result.retry_after
    }


# =============================================================================
# MONTAR SOCKET.IO
# =============================================================================

# En lugar de montar Socket.IO como subaplicación,
# creamos la aplicación combinada que Socket.IO envuelve a FastAPI
# Esto permite que WebSocket upgrades funcionen correctamente
combined_app = create_socket_app(app)


# =============================================================================
# INCLUIR ROUTERS DE ADMINISTRACIÓN
# =============================================================================

# Admin API (requiere autenticación de admin)
app.include_router(admin_router, prefix="/api/v1")

# Webhooks (protegido por API Key)
app.include_router(webhook_router, prefix="/api/v1")

# Wallet API (para usuarios - incluye escáner OCR)
app.include_router(wallet_router, prefix="/api/v1")
