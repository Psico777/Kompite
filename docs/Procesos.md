Roadmap de Desarrollo Kompite (MVP - 60 Días)
​Fase 1: El Corazón (Backend & DB) - Días 1-15
​[ ] Configuración de entorno Docker (PostgreSQL + Redis + FastAPI).
​[ ] Modelado de Base de Datos (Tablas Users, Transactions, Matches con balance_hash).
​[ ] Implementación del Sistema de Ledger (Libro Mayor) para transacciones atómicas.
​[ ] API de Autenticación y Carga de DNI (OCR Mockup).
​Fase 2: El Motor de Juego (WebSockets) - Días 16-35
​[ ] Desarrollo del Servidor de WebSockets (Socket.io/FastAPI).
​[ ] Lógica de Matchmaking y Estado LOCKED (Escrow).
​[ ] Implementación del "Heartbeat" y reglas de desconexión.
​[ ] Integración del primer juego: Piedra, Papel o Tijera (Para testear el flujo de dinero).
​Fase 3: La Parrilla de Juegos - Días 36-50
​[ ] Adaptación de repositorios: Ludo 1vs1 y Memory.
​[ ] Implementación de física en servidor para Penales y Basketball.
​[ ] Desarrollo del motor de Air Hockey.
​Fase 4: La Ventana de Salida & Launch - Días 51-60
​[ ] Sistema de generación de QR para retiros.
​[ ] Panel de Administración (LK-Shield) para monitoreo de fraudes.
​[ ] Pruebas de estrés (Stress Testing) de 500 usuarios simultáneos.
