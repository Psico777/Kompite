Roadmap de Desarrollo Kompite (MVP - 60 Días)

---

## Historial de Implementación

### v1.0.0 - Despliegue de Producción ✅

**Objetivo:** Implementar la lógica de Rake profesional y habilitar cuentas de recaudación con titularidad verificada.

#### Componentes Implementados:

**1. Algoritmo de Rake Profesional (`backend/app/game_engine.py`)**
- Clase `RakeCalculator` con comisiones por nivel:
  - **Nivel Semilla (1-10 LKC):** 8% → Mesa 5: -0.40 cada uno, Premio: 9.20
  - **Nivel Competidor (11-50 LKC):** 6% → Mesa 25: -1.50 cada uno, Premio: 47.00
  - **Nivel Pro (51+ LKC):** 5% → Mesa 100: -5.00 cada uno, Premio: 190.00
- Método `calculate_fee()` retorna breakdown completo
- Método `calculate_fee_breakdown()` para logs legibles

**2. LK_Bot - Oponente Universal (`backend/app/game_engine.py`)**
- Clase `LKBot` con saldo infinito para pruebas
- Acepta automáticamente cualquier match
- Integrado en `MatchManager` para testeo rápido LOCKED → SETTLEMENT
- Flag `ENABLE_BOT_MATCHMAKING` en configuración

**3. Auditoría Ledger Triple Entrada (`backend/app/game_engine.py`)**
- Clase `TreasuryLedger` con libro mayor inmutable
- Cada partida genera registro triple:
  - **Débito:** Apuesta del perdedor
  - **Crédito:** Premio al ganador (pot - rake)
  - **Rake:** Comisión al `LK_TREASURY`
- Verificación de `balance_hash` antes y después
- Método `verify_all_entries()` para auditoría de integridad

**4. Módulo de Retiros v1.0.0 (`backend/app/admin.py`)**
- Mínimo 5 LKC para solicitar retiro
- Estado ESCROW_OUT durante procesamiento
- Dashboard admin muestra datos KYC y bancarios
- `payment_instructions` con datos para pago manual
- Alertas de seguridad (retiros frecuentes, montos altos)

**5. Cuentas de Recaudación Final**
- **Titular Verificado:** Yordy Jesús Rojas Baldeon
- **Yape:** 995 665 397
- **Plin:** 960 912 996
- **CCI Caja Arequipa:** 80312700531552100105
- Mostrado en vista de depósito con estilos destacados

#### Archivos Modificados:
```
backend/app/game_engine.py    → +400 líneas (Rake, LKBot, Ledger)
backend/app/websocket_handler.py → Integración Rake + Bot + Ledger
backend/app/admin.py          → Cola de retiros mejorada
frontend/index.html           → Titular verificado en depósitos
frontend/styles/main.css      → Estilos para account-holder
```

---

### Fase 2.1: Integración de Ludo & Liquidez ✅

**Objetivo:** Conectar el primer juego funcional (Ludo) y habilitar el ciclo completo de economía real: Recarga → Juego → Ganancia → Retiro.

#### Componentes Implementados:

**1. Motor de Ludo Backend (`backend/app/ludo_engine.py`)**
- Clase `LudoEngine` con lógica completa del juego de mesa
- Sistema `ProvablyFairDice` usando SHA256(server_seed:client_seed:nonce)
- Estados de pieza: HOME, ACTIVE, SAFE_ZONE, FINISHED
- Captura automática de piezas enemigas
- Regla de turnos extra con 6

**2. Datos de Pago Reales (`frontend/index.html`)**
- Yape: 995 665 397
- Plin: 960 912 996
- Caja Arequipa CCI: 80312700531552100105
- Botones de copiar al portapapeles

**3. Módulo de Retiros ESCROW_OUT (`backend/app/admin.py`)**
- Endpoint `POST /wallet/withdraw` para solicitar retiros
- Endpoint `GET /wallet/balance` para consultar balance disponible/escrow
- Endpoint `GET /admin/cajero` para cola de retiros pendientes
- Endpoint `POST /admin/cajero/{ticket}/process` para aprobar/rechazar
- Estado ESCROW_OUT: fondos bloqueados hasta confirmación del cajero

**4. Frontend Ludo Canvas (`frontend/js/games/ludo.js`)**
- Renderizado con Canvas HTML5
- Paleta Cyber-Luxury: #121212, #D4AF37, #00F3FF, #FF3366, #00FF88
- Animación de dado y movimiento de piezas
- Indicador de turno y piezas seleccionables
- Integración WebSocket con el servidor

**5. Matchmaking Soft Lock (`backend/app/websocket_handler.py`)**
- Funciones `_execute_soft_lock()`, `_lock_user_balance()`, `_unlock_user_balance()`
- Bloqueo atómico: mueve bet_amount de 'available' a 'escrow_match'
- Rollback automático si algún jugador falla el lock
- Función `_settle_escrow()` para liquidación al finalizar partida

**6. Handlers WebSocket para Ludo**
- `ludo_start_game`: Crea instancia de LudoEngine
- `ludo_roll_dice`: Genera dado Provably Fair
- `ludo_move_piece`: Valida y ejecuta movimiento
- `_handle_ludo_game_over`: Liquida escrow y distribuye premio

**7. Estilos Cyber-Luxury (`frontend/styles/main.css`)**
- Contenedor de tablero con glow dorado
- Controles del dado con animación
- Indicador de turno por color de jugador
- Badge de Provably Fair

#### Flujo Completo:
```
1. Usuario selecciona Ludo en lobby
2. Elige monto de apuesta → entra a matchmaking
3. Sistema encuentra oponente (o LK_Bot)
4. Soft Lock: ambos balances bloqueados en escrow_match
5. Partida inicia con dados Provably Fair
6. Ganador recibe pot - rake (según nivel)
7. Rake va automáticamente al LK_Treasury
8. Usuario puede retirar a Yape/Plin/Banco
9. Admin procesa retiro → fondos liberados
```

---

​Fase 1: El Corazón (Backend & DB) - Días 1-15
​[x] Configuración de entorno Docker (PostgreSQL + Redis + FastAPI).
​[x] Modelado de Base de Datos (Tablas Users, Transactions, Matches con balance_hash).
​[x] Implementación del Sistema de Ledger (Libro Mayor) para transacciones atómicas.
​[x] API de Autenticación y Carga de DNI (OCR Mockup).
​[x] Fase 1.7: OCR & Análisis Forense (Documentación Verificada)

Fase 2: El Motor de Juego (WebSockets) - Días 16-35
​[x] Desarrollo del Servidor de WebSockets (Socket.io/FastAPI).
​[x] Lógica de Matchmaking y Estado LOCKED (Escrow).
​[x] Implementación del "Heartbeat" y reglas de desconexión.
​[x] Fase 2.1: Integración de Ludo con Provably Fair Dice
​[ ] Integración del primer juego: Piedra, Papel o Tijera (Para testear el flujo de dinero).

​Fase 3: La Parrilla de Juegos - Días 36-50
​[ ] Adaptación de repositorios: Ludo 1vs1 y Memory.
​[ ] Implementación de física en servidor para Penales y Basketball.
​[ ] Desarrollo del motor de Air Hockey.

​Fase 4: La Ventana de Salida & Launch - Días 51-60
​[x] Sistema de retiros con ESCROW_OUT
​[ ] Sistema de generación de QR para retiros.
​[x] Panel de Administración (LK-Shield) para monitoreo de fraudes.
​[ ] Pruebas de estrés (Stress Testing) de 500 usuarios simultáneos.
