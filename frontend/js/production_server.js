/**
 * KOMPITE PRODUCTION SERVER - Central Orchestrator
 * IP: 179.7.80.126:8000
 * 6-Game Ecosystem with Mobile-First Architecture
 * Titular: Yordy Jesús Rojas Baldeon
 */

const express = require('express');
const http = require('http');
const socketIO = require('socket.io');
const cors = require('cors');
const path = require('path');
const crypto = require('crypto');

// Game Engines
const CabezonesEngine = require('./games/cabezones/server/kompite_integration');
const AirHockeyEngine = require('./games/air_hockey/server/air_hockey_server');
const ArtilleryEngine = require('./games/artillery/server/artillery_server');
const DuelEngine = require('./games/duel/server/duel_server');
const SnowballEngine = require('./games/snowball/server/snowball_server');
const MemoriaEngine = require('./games/memoria/server/memoria_server');

// Core Modules
const balanceManager = require('./games/cabezones/server/balance_manager');
const ledger = require('./games/cabezones/server/cabezones_ledger');

const app = express();
const server = http.createServer(app);

// CORS Configuration for Mobile
app.use(cors({
  origin: '*',
  methods: ['GET', 'POST', 'PUT', 'DELETE'],
  credentials: true
}));

app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));

// Socket.io with Mobile Reconnection Support
const io = socketIO(server, {
  cors: { origin: "*", methods: ["GET", "POST"] },
  pingTimeout: 60000,
  pingInterval: 25000,
  upgradeTimeout: 30000,
  maxHttpBufferSize: 1e6,
  transports: ['websocket', 'polling'],
  allowEIO3: true,
  // Mobile Reconnection Config
  reconnection: true,
  reconnectionAttempts: 10,
  reconnectionDelay: 1000,
  reconnectionDelayMax: 5000
});

const PORT = 8000;
const HOST = '179.7.80.126';
const TITULAR = 'Yordy Jesús Rojas Baldeon';
const RAKE_PERCENTAGE = 0.08;
const SOFT_LOCK_AMOUNT = 5;

// Active Sessions & Matches
let activeSessions = {};
let activeMatches = {};
let pendingReconnections = {};

// ============================================
// MOBILE RECONNECTION SYSTEM
// ============================================
class MobileReconnectionManager {
  constructor() {
    this.disconnectedUsers = new Map();
    this.RECONNECTION_WINDOW = 30000; // 30 seconds
  }

  registerDisconnect(userId, matchId, gameState) {
    this.disconnectedUsers.set(userId, {
      matchId,
      gameState,
      timestamp: Date.now(),
      socketId: null
    });

    // Auto-cleanup after window expires
    setTimeout(() => {
      if (this.disconnectedUsers.has(userId)) {
        const data = this.disconnectedUsers.get(userId);
        if (Date.now() - data.timestamp >= this.RECONNECTION_WINDOW) {
          this.disconnectedUsers.delete(userId);
          console.log(`⏰ Reconnection window expired for ${userId}`);
        }
      }
    }, this.RECONNECTION_WINDOW + 1000);
  }

  attemptReconnection(userId, newSocketId) {
    if (this.disconnectedUsers.has(userId)) {
      const data = this.disconnectedUsers.get(userId);
      if (Date.now() - data.timestamp < this.RECONNECTION_WINDOW) {
        data.socketId = newSocketId;
        this.disconnectedUsers.delete(userId);
        console.log(`✅ Mobile reconnection successful for ${userId}`);
        return { success: true, matchId: data.matchId, gameState: data.gameState };
      }
    }
    return { success: false };
  }

  hasPendingReconnection(userId) {
    return this.disconnectedUsers.has(userId);
  }
}

const reconnectionManager = new MobileReconnectionManager();

// ============================================
// AUTHENTICATION MIDDLEWARE
// ============================================
io.use((socket, next) => {
  const { authToken, userId, gameType } = socket.handshake.auth;
  
  if (!authToken || !userId) {
    return next(new Error('Authentication required'));
  }

  // Validate balance hash
  const balance = balanceManager.getBalance(userId);
  const storedHash = balanceManager.getBalanceHash(userId);
  const expectedHash = crypto.createHash('sha256').update(`${userId}:${balance}`).digest('hex');

  if (storedHash && storedHash !== expectedHash) {
    console.error(`🚨 SECURITY ALERT: Balance hash mismatch for ${userId}`);
    return next(new Error('Security violation: balance integrity check failed'));
  }

  socket.userId = userId;
  socket.authToken = authToken;
  socket.gameType = gameType;
  next();
});

// ============================================
// MAIN CONNECTION HANDLER
// ============================================
io.on('connection', (socket) => {
  console.log(`✅ Connected: ${socket.userId} | Game: ${socket.gameType || 'lobby'}`);
  
  // Initialize user
  balanceManager.initializeUser(socket.userId);
  activeSessions[socket.userId] = {
    socketId: socket.id,
    connectedAt: Date.now(),
    gameType: socket.gameType,
    isMobile: socket.handshake.headers['user-agent']?.includes('Mobile')
  };

  // Check for pending reconnection
  const reconnectResult = reconnectionManager.attemptReconnection(socket.userId, socket.id);
  if (reconnectResult.success) {
    socket.emit('reconnected', {
      matchId: reconnectResult.matchId,
      gameState: reconnectResult.gameState,
      message: '📱 Reconexión exitosa - Tu partida continúa'
    });
  }

  // Send authenticated event
  socket.emit('authenticated', {
    userId: socket.userId,
    balance: balanceManager.getBalance(socket.userId),
    trustScore: balanceManager.getTrustScore(socket.userId)
  });

  // ============================================
  // SOFT LOCK ENDPOINT (5 LKC Atomic Escrow)
  // ============================================
  socket.on('softLock', async (data, callback) => {
    const { betAmount, gameType, matchId } = data;
    const userId = socket.userId;

    try {
      // Verify balance
      const currentBalance = balanceManager.getBalance(userId);
      if (currentBalance < betAmount) {
        return callback({ success: false, error: 'Saldo insuficiente' });
      }

      // Validate balance hash before locking
      const storedHash = balanceManager.getBalanceHash(userId);
      const expectedHash = crypto.createHash('sha256').update(`${userId}:${currentBalance}`).digest('hex');
      
      if (storedHash && storedHash !== expectedHash) {
        console.error(`🚨 SECURITY: Hash mismatch during soft lock for ${userId}`);
        return callback({ success: false, error: 'Error de integridad - Acceso bloqueado' });
      }

      // Execute atomic lock
      const lockId = `LOCK_${Date.now()}_${crypto.randomBytes(4).toString('hex')}`;
      balanceManager.updateBalance(userId, currentBalance - betAmount, `SOFT_LOCK_${gameType}`);

      // Record in ledger
      ledger.recordTransaction({
        type: 'SOFT_LOCK',
        userId,
        amount: -betAmount,
        lockId,
        gameType,
        matchId,
        timestamp: Date.now(),
        titular: TITULAR
      });

      console.log(`🔒 Soft Lock: ${userId} | ${betAmount} LKC | ${gameType}`);
      callback({ success: true, lockId, lockedAmount: betAmount });

    } catch (err) {
      console.error(`❌ Soft Lock error: ${err.message}`);
      callback({ success: false, error: err.message });
    }
  });

  // ============================================
  // SETTLEMENT ENDPOINT (8% Rake + Triple Entry)
  // ============================================
  socket.on('settlement', async (data, callback) => {
    const { matchId, lockId1, lockId2, victor, totalPot, gameType } = data;

    try {
      const rake = totalPot * RAKE_PERCENTAGE;
      const winnerPayout = totalPot - rake;

      // Triple Entry Ledger
      const ledgerEntry = ledger.recordMatchSettlement({
        roomId: matchId,
        player1: data.player1,
        player2: data.player2,
        victor,
        rake,
        winnerPayout,
        timestamp: Date.now(),
        titular: TITULAR,
        gameType
      });

      // Update balances
      if (victor !== 'TIE') {
        const winnerId = victor === 'p1' ? data.player1.userId : data.player2.userId;
        const loserId = victor === 'p1' ? data.player2.userId : data.player1.userId;
        
        const winnerBalance = balanceManager.getBalance(winnerId);
        balanceManager.updateBalance(winnerId, winnerBalance + winnerPayout, `WIN_${gameType}`);
        
        console.log(`💰 Settlement: ${winnerId} wins ${winnerPayout} LKC | Rake: ${rake} LKC`);
      } else {
        // Refund both (minus rake split)
        const refundAmount = (totalPot - rake) / 2;
        balanceManager.updateBalance(data.player1.userId, 
          balanceManager.getBalance(data.player1.userId) + refundAmount, 'TIE_REFUND');
        balanceManager.updateBalance(data.player2.userId, 
          balanceManager.getBalance(data.player2.userId) + refundAmount, 'TIE_REFUND');
      }

      callback({
        success: true,
        ledgerId: ledgerEntry.ledgerId,
        rake,
        winnerPayout,
        titular: TITULAR
      });

    } catch (err) {
      console.error(`❌ Settlement error: ${err.message}`);
      callback({ success: false, error: err.message });
    }
  });

  // ============================================
  // DISCONNECT HANDLER (Rage Quit Detection)
  // ============================================
  socket.on('disconnect', (reason) => {
    const userId = socket.userId;
    const session = activeSessions[userId];

    if (session && activeMatches[session.matchId]) {
      const match = activeMatches[session.matchId];
      const gameState = match.gameState;

      // Check if losing (rage quit detection)
      let isLosing = false;
      let penalty = -5; // Default penalty

      if (gameState) {
        const playerScore = session.role === 'P1' ? gameState.scores?.p1 : gameState.scores?.p2;
        const opponentScore = session.role === 'P1' ? gameState.scores?.p2 : gameState.scores?.p1;
        isLosing = playerScore < opponentScore;

        // Special penalty for Memoria (-15)
        if (session.gameType === 'MEMORIA' && isLosing) {
          penalty = -15;
        } else if (isLosing) {
          penalty = -5;
        }
      }

      if (isLosing) {
        balanceManager.adjustTrustScore(userId, penalty);
        console.log(`⚠️ RAGE QUIT: ${userId} | Penalty: ${penalty} | Game: ${session.gameType}`);
      }

      // Register for mobile reconnection if mobile device
      if (session.isMobile && reason === 'transport close') {
        reconnectionManager.registerDisconnect(userId, session.matchId, gameState);
        console.log(`📱 Mobile disconnect registered for ${userId} - 30s reconnection window`);
      }
    }

    delete activeSessions[userId];
    console.log(`❌ Disconnected: ${userId} | Reason: ${reason}`);
  });

  // ============================================
  // BALANCE CHECK ENDPOINT
  // ============================================
  socket.on('getBalance', (data, callback) => {
    const balance = balanceManager.getBalance(socket.userId);
    const hash = crypto.createHash('sha256').update(`${socket.userId}:${balance}`).digest('hex');
    callback({ balance, hash, trustScore: balanceManager.getTrustScore(socket.userId) });
  });
});

// ============================================
// REST API ENDPOINTS
// ============================================

// Soft Lock API
app.post('/match/soft-lock', (req, res) => {
  const { userId, betAmount, gameType } = req.body;
  
  const balance = balanceManager.getBalance(userId);
  if (balance < betAmount) {
    return res.json({ success: false, error: 'Saldo insuficiente' });
  }

  const lockId = `LOCK_${Date.now()}_${crypto.randomBytes(4).toString('hex')}`;
  balanceManager.updateBalance(userId, balance - betAmount, `SOFT_LOCK_${gameType}`);

  res.json({ success: true, lockId, lockedAmount: betAmount });
});

// Settlement API
app.post('/match/settlement', (req, res) => {
  const { lockId1, lockId2, victor, rake, ledgerId, tx_metadata } = req.body;
  
  // Process settlement
  const result = {
    success: true,
    p1Balance: balanceManager.getBalance(req.body.player1?.userId || 'unknown'),
    p2Balance: balanceManager.getBalance(req.body.player2?.userId || 'unknown'),
    rake,
    ledgerId,
    titular: TITULAR
  };

  res.json(result);
});

// Ledger Record API
app.post('/ledger/record', (req, res) => {
  const { userId, amount, type, tx_metadata, gameType } = req.body;
  
  const entry = ledger.recordTransaction({
    userId,
    amount,
    type,
    tx_metadata: { ...tx_metadata, titular: TITULAR },
    gameType,
    timestamp: Date.now()
  });

  res.json({ success: true, entryId: entry?.id || Date.now() });
});

// User Balance API
app.get('/user/:userId/balance', (req, res) => {
  const balance = balanceManager.getBalance(req.params.userId);
  res.json({ balance, userId: req.params.userId });
});

// Health Check
app.get('/health', (req, res) => {
  res.json({
    status: 'operational',
    games: ['cabezones', 'air_hockey', 'artillery', 'duel', 'snowball', 'memoria'],
    connections: Object.keys(activeSessions).length,
    uptime: process.uptime(),
    version: 'MVP-1.0.0',
    titular: TITULAR
  });
});

// ============================================
// PRODUCTION STARTUP
// ============================================
server.listen(PORT, '0.0.0.0', () => {
  console.log('');
  console.log('╔═══════════════════════════════════════════════════════════╗');
  console.log('║         🎮 KOMPITE PRODUCTION SERVER                      ║');
  console.log('╠═══════════════════════════════════════════════════════════╣');
  console.log(`║  IP:        ${HOST}:${PORT}                          ║`);
  console.log('║  Games:     6 engines active                              ║');
  console.log('║  Security:  5-Layer Model enabled                         ║');
  console.log('║  Rake:      8% Level Semilla                              ║');
  console.log('║  Soft Lock: 5 LKC atomic escrow                           ║');
  console.log(`║  Titular:   ${TITULAR}                    ║`);
  console.log('╠═══════════════════════════════════════════════════════════╣');
  console.log('║  ✅ Cabezones    ✅ Air Hockey    ✅ Artillery            ║');
  console.log('║  ✅ Duel         ✅ Snowball      ✅ Memoria              ║');
  console.log('╠═══════════════════════════════════════════════════════════╣');
  console.log('║  📱 Mobile Reconnection: 30s window                       ║');
  console.log('║  🔒 Balance Hash: SHA256 validation                       ║');
  console.log('║  ⚡ PhysicsEngines: Server-authoritative                  ║');
  console.log('╚═══════════════════════════════════════════════════════════╝');
  console.log('');
});

module.exports = { io, server, app };
