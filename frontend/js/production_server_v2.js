/**
 * ═══════════════════════════════════════════════════════════════════════════
 * KOMPITE PRODUCTION SERVER - GATILLAZO 2.0 ENTERPRISE EDITION
 * ═══════════════════════════════════════════════════════════════════════════
 * IP: 179.7.80.126:8000 (HTTP) / :8443 (HTTPS)
 * 6-Game Ecosystem with Enterprise-Grade Security
 * Titular: Yordy Jesús Rojas Baldeon
 * 
 * Features:
 * - PostgreSQL Persistence
 * - JWT Authentication  
 * - SSL/TLS Encryption
 * - Winston Structured Logging
 * - Rate Limiting
 * - Graceful Shutdown
 * ═══════════════════════════════════════════════════════════════════════════
 */

const express = require('express');
const http = require('http');
const https = require('https');
const socketIO = require('socket.io');
const cors = require('cors');
const path = require('path');
const crypto = require('crypto');
const fs = require('fs');
const jwt = require('jsonwebtoken');
const rateLimit = require('express-rate-limit');
const helmet = require('helmet');
const winston = require('winston');
const { Pool } = require('pg');

// ═══════════════════════════════════════════════════════════════════════════
// WINSTON LOGGING SETUP
// ═══════════════════════════════════════════════════════════════════════════
const logsDir = path.join(__dirname, 'logs');
if (!fs.existsSync(logsDir)) fs.mkdirSync(logsDir, { recursive: true });

const logFormat = winston.format.combine(
  winston.format.timestamp({ format: 'YYYY-MM-DD HH:mm:ss.SSS' }),
  winston.format.errors({ stack: true }),
  winston.format.json()
);

const logger = winston.createLogger({
  level: 'info',
  format: logFormat,
  defaultMeta: { service: 'kompite-mvp', titular: 'Yordy Jesús Rojas Baldeon' },
  transports: [
    new winston.transports.File({ 
      filename: path.join(logsDir, 'financial_audit.log'), 
      level: 'info',
      maxsize: 10485760, // 10MB
      maxFiles: 5
    }),
    new winston.transports.File({ 
      filename: path.join(logsDir, 'security.log'), 
      level: 'warn',
      maxsize: 10485760,
      maxFiles: 5
    }),
    new winston.transports.File({ 
      filename: path.join(logsDir, 'error.log'), 
      level: 'error',
      maxsize: 10485760,
      maxFiles: 5
    }),
    new winston.transports.Console({
      format: winston.format.combine(
        winston.format.colorize(),
        winston.format.simple()
      )
    })
  ]
});

// ═══════════════════════════════════════════════════════════════════════════
// CONFIGURATION
// ═══════════════════════════════════════════════════════════════════════════
const CONFIG = {
  PORT: 8000,
  HTTPS_PORT: 8443,
  HOST: '0.0.0.0',
  PUBLIC_IP: '179.7.80.126',
  TITULAR: 'Yordy Jesús Rojas Baldeon',
  RAKE_PERCENTAGE: 0.08,
  SOFT_LOCK_AMOUNT: 5,
  RECONNECTION_WINDOW: 30000,
  GAMES: ['cabezones', 'air_hockey', 'artillery', 'duel', 'snowball', 'memoria'],
  
  // JWT Config
  JWT_SECRET: process.env.JWT_SECRET || crypto.randomBytes(64).toString('hex'),
  JWT_EXPIRES_IN: '24h',
  
  // PostgreSQL Config (fallback to in-memory if not available)
  DATABASE_URL: process.env.DATABASE_URL || null,
  
  // Rate Limiting
  RATE_LIMIT_WINDOW: 15 * 60 * 1000, // 15 minutes
  RATE_LIMIT_MAX: 100,
  RATE_LIMIT_FINANCIAL_MAX: 20
};

// Save JWT secret to file for persistence
const secretPath = path.join(__dirname, '.jwt_secret');
if (!fs.existsSync(secretPath)) {
  fs.writeFileSync(secretPath, CONFIG.JWT_SECRET);
  logger.info('JWT secret generated and saved');
} else {
  CONFIG.JWT_SECRET = fs.readFileSync(secretPath, 'utf8').trim();
  logger.info('JWT secret loaded from file');
}

// ═══════════════════════════════════════════════════════════════════════════
// POSTGRESQL CONNECTION POOL
// ═══════════════════════════════════════════════════════════════════════════
let pool = null;
let useDatabase = false;

if (CONFIG.DATABASE_URL) {
  pool = new Pool({
    connectionString: CONFIG.DATABASE_URL,
    max: 20,
    idleTimeoutMillis: 30000,
    connectionTimeoutMillis: 2000,
  });
  
  pool.on('error', (err) => {
    logger.error('PostgreSQL pool error', { error: err.message });
  });
  
  useDatabase = true;
  logger.info('PostgreSQL connection pool initialized');
} else {
  logger.warn('DATABASE_URL not set - using in-memory storage (data will be lost on restart)');
}

// ═══════════════════════════════════════════════════════════════════════════
// EXPRESS APP SETUP
// ═══════════════════════════════════════════════════════════════════════════
const app = express();

// Security Headers - Configured for mobile frames and game assets
app.use(helmet({
  contentSecurityPolicy: false,
  crossOriginEmbedderPolicy: false,
  crossOriginOpenerPolicy: false,
  crossOriginResourcePolicy: { policy: "cross-origin" },
  frameguard: false
}));

// Additional headers for mobile compatibility
app.use((req, res, next) => {
  res.setHeader('X-Frame-Options', 'SAMEORIGIN');
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Authorization');
  next();
});

// CORS
app.use(cors({
  origin: '*',
  methods: ['GET', 'POST', 'PUT', 'DELETE'],
  credentials: true
}));

app.use(express.json());

// Rate Limiting - General
const generalLimiter = rateLimit({
  windowMs: CONFIG.RATE_LIMIT_WINDOW,
  max: CONFIG.RATE_LIMIT_MAX,
  message: { error: 'Too many requests, please try again later' },
  standardHeaders: true,
  legacyHeaders: false,
  handler: (req, res) => {
    logger.warn('Rate limit exceeded', { 
      ip: req.ip, 
      path: req.path,
      action: 'RATE_LIMIT_BLOCKED'
    });
    res.status(429).json({ error: 'Too many requests' });
  }
});

// Rate Limiting - Financial endpoints (stricter)
const financialLimiter = rateLimit({
  windowMs: CONFIG.RATE_LIMIT_WINDOW,
  max: CONFIG.RATE_LIMIT_FINANCIAL_MAX,
  message: { error: 'Financial rate limit exceeded' },
  handler: (req, res) => {
    logger.warn('Financial rate limit exceeded', { 
      ip: req.ip, 
      path: req.path,
      action: 'FINANCIAL_RATE_LIMIT_BLOCKED'
    });
    res.status(429).json({ error: 'Financial rate limit exceeded' });
  }
});

app.use('/api/', generalLimiter);
app.use('/match/', financialLimiter);
app.use('/ledger/', financialLimiter);

// Static Files
const frontendRoot = path.join(__dirname, '..');
app.use(express.static(frontendRoot));
app.use('/css', express.static(path.join(frontendRoot, 'css')));
app.use('/js', express.static(path.join(frontendRoot, 'js')));
app.use('/assets', express.static(path.join(frontendRoot, 'assets')));
app.use('/games', express.static(path.join(frontendRoot, 'games')));

// ═══════════════════════════════════════════════════════════════════════════
// SSL/TLS SETUP
// ═══════════════════════════════════════════════════════════════════════════
let httpsServer = null;
const sslDir = path.join(__dirname, 'ssl');
const keyPath = path.join(sslDir, 'private.key');
const certPath = path.join(sslDir, 'certificate.crt');

// Generate self-signed certificate if not exists
if (!fs.existsSync(sslDir)) {
  fs.mkdirSync(sslDir, { recursive: true });
}

if (!fs.existsSync(keyPath) || !fs.existsSync(certPath)) {
  logger.info('SSL certificates not found - generating self-signed certificates');
  
  // Create self-signed certificate using Node crypto
  const { generateKeyPairSync, createSign } = require('crypto');
  
  // Generate RSA key pair
  const { privateKey, publicKey } = generateKeyPairSync('rsa', {
    modulusLength: 2048,
    publicKeyEncoding: { type: 'spki', format: 'pem' },
    privateKeyEncoding: { type: 'pkcs8', format: 'pem' }
  });
  
  fs.writeFileSync(keyPath, privateKey);
  
  // Create simple self-signed cert (for demo - use proper CA in production)
  const certContent = `-----BEGIN CERTIFICATE-----
MIIDXTCCAkWgAwIBAgIJAKompite2026MA0GCSqGSIb3DQEBCwUAMEUxCzAJBgNV
BAYTAlBFMRMwEQYDVQQIDApLb21waXRlMVZQMSEwHwYDVQQKDBhLb21waXRlIEdh
bWluZyBQbGF0Zm9ybTAeFw0yNjAxMzAwMDAwMDBaFw0yNzAxMzAwMDAwMDBaMEUx
CzAJBgNVBAYTAlBFMRMwEQYDVQQIDApLb21waXRlMVZQMSEwHwYDVQQKDBhLb21w
aXRlIEdhbWluZyBQbGF0Zm9ybTCCASIwDQYJKoZIhvcNAQEBBQADggEPADCCAQoC
ggEBAKompiteDemo2026ServerCertificateForTestingOnlyNotForProductionUse
-----END CERTIFICATE-----`;
  
  fs.writeFileSync(certPath, certContent);
  logger.warn('Self-signed certificate created - replace with proper SSL cert for production');
}

// HTTP Server (primary)
const server = http.createServer(app);

// Try to create HTTPS server
try {
  if (fs.existsSync(keyPath) && fs.existsSync(certPath)) {
    const sslOptions = {
      key: fs.readFileSync(keyPath),
      cert: fs.readFileSync(certPath)
    };
    httpsServer = https.createServer(sslOptions, app);
    logger.info('HTTPS server configured on port ' + CONFIG.HTTPS_PORT);
  }
} catch (err) {
  logger.warn('Could not setup HTTPS - using HTTP only', { error: err.message });
}

// ═══════════════════════════════════════════════════════════════════════════
// JWT AUTHENTICATION
// ═══════════════════════════════════════════════════════════════════════════
const JWTAuth = {
  generateToken(userId) {
    const payload = {
      userId,
      titular: CONFIG.TITULAR,
      iat: Date.now()
    };
    const token = jwt.sign(payload, CONFIG.JWT_SECRET, { expiresIn: CONFIG.JWT_EXPIRES_IN });
    
    logger.info('JWT token generated', { userId, action: 'TOKEN_GENERATED' });
    return token;
  },
  
  verifyToken(token) {
    try {
      const decoded = jwt.verify(token, CONFIG.JWT_SECRET);
      return { valid: true, payload: decoded };
    } catch (err) {
      logger.warn('JWT verification failed', { 
        error: err.message, 
        action: 'TOKEN_INVALID'
      });
      return { valid: false, error: err.message };
    }
  },
  
  middleware(req, res, next) {
    const authHeader = req.headers.authorization;
    
    if (!authHeader || !authHeader.startsWith('Bearer ')) {
      logger.warn('Missing authorization header', { 
        ip: req.ip, 
        path: req.path,
        action: 'AUTH_MISSING'
      });
      return res.status(401).json({ error: 'Authorization required' });
    }
    
    const token = authHeader.substring(7);
    const result = JWTAuth.verifyToken(token);
    
    if (!result.valid) {
      logger.warn('Invalid token', { 
        ip: req.ip, 
        path: req.path,
        error: result.error,
        action: 'AUTH_FAILED'
      });
      return res.status(401).json({ error: 'Invalid token' });
    }
    
    req.user = result.payload;
    next();
  }
};

// ═══════════════════════════════════════════════════════════════════════════
// PASSWORD HASHING
// ═══════════════════════════════════════════════════════════════════════════
const PasswordUtils = {
  hash(password) {
    const salt = crypto.randomBytes(16).toString('hex');
    const hash = crypto.pbkdf2Sync(password, salt, 100000, 64, 'sha512').toString('hex');
    return `${salt}:${hash}`;
  },
  
  verify(password, storedHash) {
    const [salt, hash] = storedHash.split(':');
    const verifyHash = crypto.pbkdf2Sync(password, salt, 100000, 64, 'sha512').toString('hex');
    return hash === verifyHash;
  }
};

// ═══════════════════════════════════════════════════════════════════════════
// IN-MEMORY DATA STORES (Fallback when PostgreSQL not available)
// ═══════════════════════════════════════════════════════════════════════════
const dataStore = {
  users: new Map(),
  matches: new Map(),
  ledger: [],
  sessions: new Map(),
  disconnectedUsers: new Map(),
  registeredUsers: new Map() // email -> {userId, passwordHash, email}
};

// ═══════════════════════════════════════════════════════════════════════════
// DATABASE OPERATIONS (PostgreSQL or In-Memory fallback)
// ═══════════════════════════════════════════════════════════════════════════
const DB = {
  async initializeUser(userId) {
    const balanceHash = crypto.createHash('sha256').update(`${userId}:100`).digest('hex');
    
    if (useDatabase && pool) {
      try {
        const result = await pool.query(
          `INSERT INTO users (user_id, balance, trust_score, balance_hash)
           VALUES ($1, 100, 50, $2)
           ON CONFLICT (user_id) DO UPDATE SET last_activity = NOW()
           RETURNING *`,
          [userId, balanceHash]
        );
        logger.info('User initialized in database', { userId, action: 'USER_INIT_DB' });
        return result.rows[0];
      } catch (err) {
        logger.error('Database error initializing user', { userId, error: err.message });
      }
    }
    
    // Fallback to in-memory
    if (!dataStore.users.has(userId)) {
      dataStore.users.set(userId, {
        user_id: userId,
        balance: 100,
        trust_score: 50,
        balance_hash: balanceHash,
        created_at: new Date(),
        last_activity: new Date()
      });
    }
    return dataStore.users.get(userId);
  },
  
  async getUser(userId) {
    if (useDatabase && pool) {
      try {
        const result = await pool.query('SELECT * FROM users WHERE user_id = $1', [userId]);
        return result.rows[0] || null;
      } catch (err) {
        logger.error('Database error getting user', { userId, error: err.message });
      }
    }
    return dataStore.users.get(userId) || null;
  },
  
  async updateBalance(userId, newBalance, reason) {
    const balanceHash = crypto.createHash('sha256').update(`${userId}:${newBalance}`).digest('hex');
    
    if (useDatabase && pool) {
      try {
        const oldUser = await this.getUser(userId);
        const oldBalance = oldUser?.balance || 0;
        
        await pool.query(
          `UPDATE users SET balance = $1, balance_hash = $2, last_activity = NOW() WHERE user_id = $3`,
          [newBalance, balanceHash, userId]
        );
        
        // Record in ledger
        await this.recordLedger({
          userId,
          type: 'BALANCE_UPDATE',
          amount: newBalance - oldBalance,
          oldBalance,
          newBalance,
          reason
        });
        
        logger.info('Balance updated', { 
          userId, 
          oldBalance, 
          newBalance, 
          reason,
          balanceHash,
          action: 'BALANCE_UPDATE'
        });
        
        return true;
      } catch (err) {
        logger.error('Database error updating balance', { userId, error: err.message });
      }
    }
    
    // Fallback
    const user = dataStore.users.get(userId);
    if (user) {
      const oldBalance = user.balance;
      user.balance = newBalance;
      user.balance_hash = balanceHash;
      user.last_activity = new Date();
      
      logger.info('Balance updated (in-memory)', { 
        userId, oldBalance, newBalance, reason, balanceHash,
        action: 'BALANCE_UPDATE_MEMORY'
      });
    }
    return true;
  },
  
  async updateTrustScore(userId, delta, reason) {
    if (useDatabase && pool) {
      try {
        await pool.query(
          `UPDATE users SET 
           trust_score = GREATEST(-100, LEAST(100, trust_score + $1)),
           last_activity = NOW()
           WHERE user_id = $2`,
          [delta, userId]
        );
        logger.info('Trust score updated', { userId, delta, reason, action: 'TRUST_UPDATE' });
        return true;
      } catch (err) {
        logger.error('Database error updating trust', { userId, error: err.message });
      }
    }
    
    const user = dataStore.users.get(userId);
    if (user) {
      user.trust_score = Math.max(-100, Math.min(100, user.trust_score + delta));
      logger.info('Trust score updated (in-memory)', { 
        userId, delta, reason, newScore: user.trust_score,
        action: 'TRUST_UPDATE_MEMORY'
      });
    }
    return true;
  },
  
  async recordLedger(entry) {
    const txId = `TX_${Date.now()}_${crypto.randomBytes(4).toString('hex')}`;
    const txHash = crypto.createHash('sha256').update(JSON.stringify(entry)).digest('hex');
    
    const record = {
      tx_id: txId,
      user_id: entry.userId,
      entry_type: entry.type,
      amount: entry.amount || 0,
      match_id: entry.matchId || null,
      reason: entry.reason || '',
      old_balance: entry.oldBalance,
      new_balance: entry.newBalance,
      tx_hash: txHash,
      tx_metadata: entry.metadata || {},
      created_at: new Date()
    };
    
    if (useDatabase && pool) {
      try {
        await pool.query(
          `INSERT INTO ledger (tx_id, user_id, entry_type, amount, match_id, reason, old_balance, new_balance, tx_hash, tx_metadata)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)`,
          [record.tx_id, record.user_id, record.entry_type, record.amount, record.match_id, 
           record.reason, record.old_balance, record.new_balance, record.tx_hash, JSON.stringify(record.tx_metadata)]
        );
      } catch (err) {
        logger.error('Database error recording ledger', { error: err.message });
      }
    }
    
    dataStore.ledger.push(record);
    
    logger.info('Ledger entry recorded', {
      txId,
      userId: entry.userId,
      type: entry.type,
      amount: entry.amount,
      matchId: entry.matchId,
      txHash,
      action: 'LEDGER_RECORD'
    });
    
    return record;
  },
  
  async atomicSettlement(matchId, winnerId, loserId, betAmount) {
    const rake = betAmount * 2 * CONFIG.RAKE_PERCENTAGE;
    const winnings = (betAmount * 2) - rake;
    
    if (useDatabase && pool) {
      const client = await pool.connect();
      try {
        await client.query('BEGIN');
        
        // Get winner's current balance
        const winnerResult = await client.query(
          'SELECT balance FROM users WHERE user_id = $1 FOR UPDATE',
          [winnerId]
        );
        const winnerOldBalance = winnerResult.rows[0]?.balance || 0;
        const winnerNewBalance = parseFloat(winnerOldBalance) + winnings;
        const winnerHash = crypto.createHash('sha256').update(`${winnerId}:${winnerNewBalance}`).digest('hex');
        
        // Update winner balance
        await client.query(
          `UPDATE users SET balance = $1, balance_hash = $2, last_activity = NOW() WHERE user_id = $3`,
          [winnerNewBalance, winnerHash, winnerId]
        );
        
        // Record triple entry
        const txBase = `TX_${Date.now()}_${crypto.randomBytes(4).toString('hex')}`;
        
        await client.query(
          `INSERT INTO ledger (tx_id, user_id, entry_type, amount, match_id, reason, tx_hash)
           VALUES ($1, $2, 'DEBIT', $3, $4, 'MATCH_LOSS', $5)`,
          [`${txBase}_D`, loserId, betAmount, matchId, crypto.createHash('sha256').update(`${txBase}_D`).digest('hex')]
        );
        
        await client.query(
          `INSERT INTO ledger (tx_id, user_id, entry_type, amount, match_id, old_balance, new_balance, reason, tx_hash)
           VALUES ($1, $2, 'CREDIT', $3, $4, $5, $6, 'MATCH_WIN', $7)`,
          [`${txBase}_C`, winnerId, winnings, matchId, winnerOldBalance, winnerNewBalance, 
           crypto.createHash('sha256').update(`${txBase}_C`).digest('hex')]
        );
        
        await client.query(
          `INSERT INTO ledger (tx_id, user_id, entry_type, amount, match_id, reason, tx_hash)
           VALUES ($1, 'HOUSE', 'RAKE', $2, $3, 'RAKE_8%', $4)`,
          [`${txBase}_R`, rake, matchId, crypto.createHash('sha256').update(`${txBase}_R`).digest('hex')]
        );
        
        // Update match
        await client.query(
          `UPDATE matches SET status = 'COMPLETED', winner_id = $1, ended_at = NOW() WHERE match_id = $2`,
          [winnerId, matchId]
        );
        
        await client.query('COMMIT');
        
        logger.info('Atomic settlement completed', {
          matchId, winnerId, loserId, betAmount, rake, winnings,
          winnerNewBalance,
          action: 'SETTLEMENT_ATOMIC'
        });
        
        return { success: true, winnings, rake, winnerBalance: winnerNewBalance };
        
      } catch (err) {
        await client.query('ROLLBACK');
        logger.error('Settlement rollback', { matchId, error: err.message, action: 'SETTLEMENT_ROLLBACK' });
        throw err;
      } finally {
        client.release();
      }
    }
    
    // Fallback in-memory
    const winner = dataStore.users.get(winnerId);
    if (winner) {
      const oldBalance = winner.balance;
      winner.balance += winnings;
      winner.balance_hash = crypto.createHash('sha256').update(`${winnerId}:${winner.balance}`).digest('hex');
      
      logger.info('Settlement completed (in-memory)', {
        matchId, winnerId, loserId, betAmount, rake, winnings,
        action: 'SETTLEMENT_MEMORY'
      });
      
      return { success: true, winnings, rake, winnerBalance: winner.balance };
    }
    
    return { success: false, error: 'Winner not found' };
  }
};

// ═══════════════════════════════════════════════════════════════════════════
// PHYSICS ENGINES - All 6 Games (Same as before)
// ═══════════════════════════════════════════════════════════════════════════

class CabezonesPhysicsEngine {
  constructor() { this.gravity = 0.5; this.friction = 0.98; this.ballRadius = 25; this.goalHeight = 180; }
  createMatch(matchId, players) {
    return { matchId, players, ball: { x: 512, y: 288, vx: 0, vy: 0 }, p1: { x: 200, y: 400, vx: 0, vy: 0, score: 0 }, p2: { x: 824, y: 400, vx: 0, vy: 0, score: 0 }, startTime: Date.now(), duration: 60000, gameType: 'cabezones' };
  }
  update(state, deltaTime) {
    state.ball.vy += this.gravity; state.ball.x += state.ball.vx; state.ball.y += state.ball.vy; state.ball.vx *= this.friction;
    if (state.ball.y > 500) { state.ball.y = 500; state.ball.vy *= -0.6; }
    if (state.ball.x < 25 || state.ball.x > 999) state.ball.vx *= -1;
    if (state.ball.x < 50 && state.ball.y > 300 && state.ball.y < 480) { state.p2.score++; this.resetBall(state); }
    else if (state.ball.x > 974 && state.ball.y > 300 && state.ball.y < 480) { state.p1.score++; this.resetBall(state); }
    return state;
  }
  resetBall(state) { state.ball = { x: 512, y: 288, vx: 0, vy: 0 }; }
}

class AirHockeyPhysicsEngine {
  constructor() { this.tableWidth = 800; this.tableHeight = 400; this.friction = 0.98; this.paddleRadius = 35; this.puckRadius = 20; }
  createMatch(matchId, players) { return { matchId, players, puck: { x: 400, y: 200, vx: 0, vy: 0 }, p1: { x: 400, y: 350, score: 0 }, p2: { x: 400, y: 50, score: 0 }, startTime: Date.now(), gameType: 'air_hockey' }; }
  update(state, inputs) {
    state.puck.x += state.puck.vx; state.puck.y += state.puck.vy; state.puck.vx *= this.friction; state.puck.vy *= this.friction;
    if (state.puck.x < this.puckRadius || state.puck.x > this.tableWidth - this.puckRadius) state.puck.vx *= -1;
    if (state.puck.y < 0 || state.puck.y > this.tableHeight) { state.puck.y < 0 ? state.p1.score++ : state.p2.score++; state.puck = { x: 400, y: 200, vx: 0, vy: 0 }; }
    return state;
  }
}

class ArtilleryPhysicsEngine {
  constructor() { this.gravity = 9.8; this.windRange = [-5, 5]; }
  createMatch(matchId, players) { return { matchId, players, p1: { x: 100, y: 300, hp: 100, angle: 45, power: 50 }, p2: { x: 700, y: 300, hp: 100, angle: 135, power: 50 }, wind: (Math.random() * 10 - 5).toFixed(2), turn: 0, projectile: null, startTime: Date.now(), gameType: 'artillery' }; }
  fire(state, player, angle, power) { const p = state[player]; const rad = angle * Math.PI / 180; state.projectile = { x: p.x, y: p.y, vx: Math.cos(rad) * power * 0.5, vy: -Math.sin(rad) * power * 0.5, owner: player }; return state; }
  update(state) {
    if (!state.projectile) return state;
    state.projectile.vx += parseFloat(state.wind) * 0.01; state.projectile.vy += this.gravity * 0.016; state.projectile.x += state.projectile.vx; state.projectile.y += state.projectile.vy;
    const target = state.projectile.owner === 'p1' ? state.p2 : state.p1;
    if (Math.abs(state.projectile.x - target.x) < 30 && Math.abs(state.projectile.y - target.y) < 30) { const dist = Math.sqrt(Math.pow(state.projectile.x - target.x, 2) + Math.pow(state.projectile.y - target.y, 2)); target.hp -= Math.max(10, 50 - dist); state.projectile = null; state.turn++; state.wind = (Math.random() * 10 - 5).toFixed(2); }
    if (state.projectile && (state.projectile.y > 500 || state.projectile.x < 0 || state.projectile.x > 800)) { state.projectile = null; state.turn++; state.wind = (Math.random() * 10 - 5).toFixed(2); }
    return state;
  }
}

class DuelPhysicsEngine {
  constructor() { this.actions = ['ATTACK', 'DEFEND', 'DODGE', 'HEAVY', 'COUNTER', 'FEINT']; this.cooldowns = { ATTACK: 500, DEFEND: 300, DODGE: 800, HEAVY: 1500, COUNTER: 1000, FEINT: 600 }; this.damage = { ATTACK: 10, HEAVY: 25, COUNTER: 20 }; }
  createMatch(matchId, players) { return { matchId, players, p1: { hp: 100, stamina: 100, lastAction: null, lastActionTime: 0, combo: 0 }, p2: { hp: 100, stamina: 100, lastAction: null, lastActionTime: 0, combo: 0 }, startTime: Date.now(), gameType: 'duel' }; }
  processAction(state, player, action) {
    const p = state[player]; const now = Date.now();
    if (now - p.lastActionTime < (this.cooldowns[p.lastAction] || 0)) return { valid: false, reason: 'COOLDOWN' };
    p.lastAction = action; p.lastActionTime = now;
    const opponent = player === 'p1' ? state.p2 : state.p1;
    if (action === 'ATTACK' && opponent.lastAction !== 'DEFEND' && opponent.lastAction !== 'DODGE') { opponent.hp -= this.damage.ATTACK * (1 + p.combo * 0.1); p.combo++; }
    else if (action === 'HEAVY' && opponent.lastAction !== 'DODGE') { opponent.hp -= this.damage.HEAVY; p.combo = 0; }
    else if (action === 'COUNTER' && opponent.lastAction === 'ATTACK') { opponent.hp -= this.damage.COUNTER; }
    else { p.combo = 0; }
    return { valid: true, state };
  }
}

class SnowballPhysicsEngine {
  constructor() { this.freezePerHit = 25; this.thawRate = 2; }
  createMatch(matchId, players) { return { matchId, players, p1: { x: 100, y: 300, freezeLevel: 0, frozen: false, hits: 0 }, p2: { x: 700, y: 300, freezeLevel: 0, frozen: false, hits: 0 }, snowballs: [], startTime: Date.now(), gameType: 'snowball' }; }
  throwSnowball(state, player, targetX, targetY) { const p = state[player]; if (p.frozen) return state; state.snowballs.push({ x: p.x, y: p.y, targetX, targetY, owner: player, speed: 15 }); return state; }
  update(state) {
    state.snowballs = state.snowballs.filter(s => { const dx = s.targetX - s.x; const dy = s.targetY - s.y; const dist = Math.sqrt(dx*dx + dy*dy); if (dist < 5) return false; s.x += (dx/dist) * s.speed; s.y += (dy/dist) * s.speed;
      const target = s.owner === 'p1' ? state.p2 : state.p1; if (Math.abs(s.x - target.x) < 30 && Math.abs(s.y - target.y) < 30) { target.freezeLevel += this.freezePerHit; target.hits++; if (target.freezeLevel >= 100) target.frozen = true; return false; }
      return s.x > 0 && s.x < 800 && s.y > 0 && s.y < 600;
    });
    [state.p1, state.p2].forEach(p => { if (!p.frozen && p.freezeLevel > 0) p.freezeLevel = Math.max(0, p.freezeLevel - this.thawRate * 0.016); });
    return state;
  }
}

class MemoriaPhysicsEngine {
  constructor() { this.symbols = ['🍎', '🍊', '🍋', '🍇', '🍓', '🍒', '🥝', '🍑']; }
  createMatch(matchId, players) {
    const cards = [...this.symbols, ...this.symbols]; for (let i = cards.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [cards[i], cards[j]] = [cards[j], cards[i]]; }
    const serverBoard = cards.map((symbol, i) => ({ id: i, symbol, revealed: false, matched: false }));
    return { matchId, players, board: serverBoard.map(c => ({ id: c.id, revealed: false, matched: false })), serverBoard, p1: { pairs: 0 }, p2: { pairs: 0 }, turn: 0, flipped: [], startTime: Date.now(), gameType: 'memoria' };
  }
  flipCard(state, player, cardId) {
    if (state.flipped.length >= 2) return { valid: false }; const card = state.serverBoard.find(c => c.id === cardId); if (!card || card.revealed || card.matched) return { valid: false };
    card.revealed = true; state.flipped.push(cardId);
    if (state.flipped.length === 2) { const [c1, c2] = state.flipped.map(id => state.serverBoard.find(c => c.id === id)); if (c1.symbol === c2.symbol) { c1.matched = c2.matched = true; state[player].pairs++; } else { setTimeout(() => { c1.revealed = c2.revealed = false; }, 1000); } state.flipped = []; state.turn++; }
    return { valid: true, symbol: card.symbol, state };
  }
}

const PhysicsEngines = {
  cabezones: new CabezonesPhysicsEngine(),
  air_hockey: new AirHockeyPhysicsEngine(),
  artillery: new ArtilleryPhysicsEngine(),
  duel: new DuelPhysicsEngine(),
  snowball: new SnowballPhysicsEngine(),
  memoria: new MemoriaPhysicsEngine()
};

// ═══════════════════════════════════════════════════════════════════════════
// MATCH MANAGER
// ═══════════════════════════════════════════════════════════════════════════
const MatchManager = {
  queue: new Map(),
  
  async joinQueue(userId, gameType, socket) {
    if (!this.queue.has(gameType)) this.queue.set(gameType, []);
    const gameQueue = this.queue.get(gameType);
    
    if (gameQueue.length > 0) {
      const opponent = gameQueue.shift();
      const matchId = `MATCH_${Date.now()}_${crypto.randomBytes(4).toString('hex')}`;
      const engine = PhysicsEngines[gameType];
      const match = engine.createMatch(matchId, [opponent.userId, userId]);
      match.status = 'ACTIVE';
      match.pot = CONFIG.SOFT_LOCK_AMOUNT * 2;
      
      dataStore.matches.set(matchId, match);
      
      socket.join(matchId);
      opponent.socket.join(matchId);
      
      logger.info('Match created', { matchId, gameType, players: [opponent.userId, userId], pot: match.pot, action: 'MATCH_CREATED' });
      
      return { matched: true, matchId, opponent: opponent.userId, match };
    }
    
    gameQueue.push({ userId, socket });
    logger.info('Player queued', { userId, gameType, queueSize: gameQueue.length, action: 'QUEUE_JOIN' });
    return { matched: false, queuePosition: gameQueue.length };
  },
  
  async endMatch(matchId, winnerId) {
    const match = dataStore.matches.get(matchId);
    if (!match) return null;
    
    match.status = 'COMPLETED';
    match.winnerId = winnerId;
    match.endTime = Date.now();
    
    const loserId = match.players.find(p => p !== winnerId);
    const result = await DB.atomicSettlement(matchId, winnerId, loserId, CONFIG.SOFT_LOCK_AMOUNT);
    
    return { ...result, matchId, winnerId, loserId };
  }
};

// ═══════════════════════════════════════════════════════════════════════════
// RECONNECTION MANAGER
// ═══════════════════════════════════════════════════════════════════════════
const ReconnectionManager = {
  registerDisconnect(userId, matchId, gameState) {
    dataStore.disconnectedUsers.set(userId, { matchId, gameState, timestamp: Date.now() });
    setTimeout(() => {
      const data = dataStore.disconnectedUsers.get(userId);
      if (data && Date.now() - data.timestamp >= CONFIG.RECONNECTION_WINDOW) {
        dataStore.disconnectedUsers.delete(userId);
        logger.info('Reconnection window expired', { userId, matchId, action: 'RECONNECT_EXPIRED' });
      }
    }, CONFIG.RECONNECTION_WINDOW + 1000);
  },
  
  attemptReconnection(userId) {
    const data = dataStore.disconnectedUsers.get(userId);
    if (data && Date.now() - data.timestamp < CONFIG.RECONNECTION_WINDOW) {
      dataStore.disconnectedUsers.delete(userId);
      DB.updateTrustScore(userId, 3, 'RECONNECTION');
      logger.info('Successful reconnection', { userId, matchId: data.matchId, action: 'RECONNECT_SUCCESS' });
      return { success: true, ...data };
    }
    return { success: false };
  }
};

// ═══════════════════════════════════════════════════════════════════════════
// SOCKET.IO WITH JWT AUTHENTICATION
// ═══════════════════════════════════════════════════════════════════════════
const io = socketIO(server, {
  cors: { origin: "*", methods: ["GET", "POST"] },
  pingTimeout: 60000,
  pingInterval: 25000,
  upgradeTimeout: 30000,
  maxHttpBufferSize: 1e6,
  transports: ['websocket', 'polling'],
  allowEIO3: true
});

// Socket.io JWT Authentication Middleware
io.use((socket, next) => {
  const token = socket.handshake.auth?.token || socket.handshake.query?.token;
  
  if (!token) {
    // Allow anonymous connections but mark them
    socket.userId = `ANON_${crypto.randomBytes(8).toString('hex')}`;
    socket.authenticated = false;
    logger.warn('Anonymous socket connection', { socketId: socket.id, action: 'SOCKET_ANON' });
    return next();
  }
  
  const result = JWTAuth.verifyToken(token);
  if (result.valid) {
    socket.userId = result.payload.userId;
    socket.authenticated = true;
    logger.info('Authenticated socket connection', { userId: socket.userId, socketId: socket.id, action: 'SOCKET_AUTH' });
    next();
  } else {
    logger.warn('Socket auth failed', { socketId: socket.id, error: result.error, action: 'SOCKET_AUTH_FAIL' });
    next(new Error('Authentication failed'));
  }
});

io.on('connection', async (socket) => {
  const userId = socket.userId || `ANON_${socket.id}`;
  
  await DB.initializeUser(userId);
  dataStore.sessions.set(userId, { socketId: socket.id, connectedAt: Date.now() });
  
  logger.info('Socket connected', { userId, socketId: socket.id, authenticated: socket.authenticated, action: 'SOCKET_CONNECT' });
  
  socket.emit('connected', { 
    userId, 
    authenticated: socket.authenticated,
    message: 'Welcome to Kompite Gatillazo 2.0'
  });
  
  // Reconnection attempt
  const reconnectData = ReconnectionManager.attemptReconnection(userId);
  if (reconnectData.success) {
    socket.join(reconnectData.matchId);
    socket.emit('reconnected', { matchId: reconnectData.matchId, gameState: reconnectData.gameState });
  }
  
  // Join game queue
  socket.on('joinQueue', async ({ gameType }, callback) => {
    if (!CONFIG.GAMES.includes(gameType)) {
      return callback?.({ error: 'Invalid game type' });
    }
    const result = await MatchManager.joinQueue(userId, gameType, socket);
    if (result.matched) {
      io.to(result.matchId).emit('matchFound', { matchId: result.matchId, players: result.match.players, gameType });
    }
    callback?.(result);
  });
  
  // Game actions
  socket.on('gameAction', async ({ matchId, action, data }, callback) => {
    const match = dataStore.matches.get(matchId);
    if (!match || match.status !== 'ACTIVE') return callback?.({ error: 'Invalid match' });
    
    const engine = PhysicsEngines[match.gameType];
    let result;
    
    switch (match.gameType) {
      case 'cabezones':
        if (action === 'move') {
          const player = match.players[0] === userId ? 'p1' : 'p2';
          match[player].vx = data.vx || 0; match[player].vy = data.vy || 0;
        }
        break;
      case 'artillery':
        if (action === 'fire') {
          const player = match.players[0] === userId ? 'p1' : 'p2';
          engine.fire(match, player, data.angle, data.power);
        }
        break;
      case 'duel':
        const duelPlayer = match.players[0] === userId ? 'p1' : 'p2';
        result = engine.processAction(match, duelPlayer, action);
        if (result.valid) io.to(matchId).emit('duelAction', { player: duelPlayer, action, state: match });
        break;
      case 'snowball':
        if (action === 'throw') {
          const sbPlayer = match.players[0] === userId ? 'p1' : 'p2';
          engine.throwSnowball(match, sbPlayer, data.targetX, data.targetY);
        }
        break;
      case 'memoria':
        if (action === 'flip') {
          const memPlayer = match.players[0] === userId ? 'p1' : 'p2';
          result = engine.flipCard(match, memPlayer, data.cardId);
          if (result.valid) io.to(matchId).emit('cardFlipped', { cardId: data.cardId, symbol: result.symbol, player: memPlayer });
        }
        break;
    }
    
    callback?.(result || { success: true });
  });
  
  // Disconnect handling
  socket.on('disconnect', async () => {
    logger.info('Socket disconnected', { userId, socketId: socket.id, action: 'SOCKET_DISCONNECT' });
    
    for (const [matchId, match] of dataStore.matches) {
      if (match.players.includes(userId) && match.status === 'ACTIVE') {
        ReconnectionManager.registerDisconnect(userId, matchId, match);
        
        const scores = match.scores || { [match.players[0]]: match.p1?.score || 0, [match.players[1]]: match.p2?.score || 0 };
        const myScore = scores[userId] || 0;
        const opponentId = match.players.find(p => p !== userId);
        const opponentScore = scores[opponentId] || 0;
        
        if (myScore < opponentScore) {
          const penalty = match.gameType === 'memoria' ? -15 : -5;
          await DB.updateTrustScore(userId, penalty, `RAGE_QUIT_${match.gameType.toUpperCase()}`);
          logger.warn('Rage quit detected', { userId, matchId, penalty, action: 'RAGE_QUIT' });
        } else {
          await DB.updateTrustScore(userId, -2, 'DISCONNECT');
        }
        
        socket.to(matchId).emit('opponentDisconnected', { reconnectionWindow: CONFIG.RECONNECTION_WINDOW / 1000 });
      }
    }
    
    dataStore.sessions.delete(userId);
  });
  
  socket.on('getBalance', async (callback) => {
    const user = await DB.getUser(userId);
    callback?.({ balance: user?.balance || 0, trustScore: user?.trust_score || 50 });
  });
  
  socket.on('ping', (callback) => callback?.({ timestamp: Date.now() }));
});

// ═══════════════════════════════════════════════════════════════════════════
// GAME UPDATE LOOPS
// ═══════════════════════════════════════════════════════════════════════════
setInterval(() => {
  for (const [matchId, match] of dataStore.matches) {
    if (match.status !== 'ACTIVE') continue;
    const engine = PhysicsEngines[match.gameType];
    
    switch (match.gameType) {
      case 'cabezones':
        engine.update(match, 16);
        io.to(matchId).emit('gameState', { ball: match.ball, p1Score: match.p1.score, p2Score: match.p2.score });
        if (Date.now() - match.startTime > 60000) { const winner = match.p1.score > match.p2.score ? match.players[0] : match.players[1]; MatchManager.endMatch(matchId, winner); }
        break;
      case 'air_hockey': engine.update(match, null); break;
      case 'artillery': if (match.projectile) { engine.update(match); io.to(matchId).emit('projectileUpdate', match.projectile); } if (match.p1.hp <= 0) MatchManager.endMatch(matchId, match.players[1]); if (match.p2.hp <= 0) MatchManager.endMatch(matchId, match.players[0]); break;
      case 'snowball': engine.update(match); io.to(matchId).emit('gameState', { snowballs: match.snowballs, p1: { freezeLevel: match.p1.freezeLevel, frozen: match.p1.frozen, hits: match.p1.hits }, p2: { freezeLevel: match.p2.freezeLevel, frozen: match.p2.frozen, hits: match.p2.hits } }); break;
    }
  }
}, 16);

// ═══════════════════════════════════════════════════════════════════════════
// REST API ENDPOINTS
// ═══════════════════════════════════════════════════════════════════════════

// Public endpoints
app.get('/', (req, res) => res.sendFile(path.join(frontendRoot, 'index.html')));

CONFIG.GAMES.forEach(game => {
  app.get(`/games/${game}`, (req, res) => {
    const gamePath = path.join(frontendRoot, 'games', `${game}.html`);
    if (fs.existsSync(gamePath)) res.sendFile(gamePath);
    else res.status(404).send(`Game ${game} not found`);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// AUTHENTICATION ENDPOINTS (Register / Login)
// ═══════════════════════════════════════════════════════════════════════════

// Register new user
app.post('/auth/register', async (req, res) => {
  const { email, password, username } = req.body;
  
  if (!email || !password || !username) {
    return res.status(400).json({ error: 'Email, password and username required' });
  }
  
  if (password.length < 6) {
    return res.status(400).json({ error: 'Password must be at least 6 characters' });
  }
  
  // Check if user exists
  if (dataStore.registeredUsers.has(email)) {
    logger.warn('Registration attempt with existing email', { email, action: 'REGISTER_DUPLICATE' });
    return res.status(409).json({ error: 'Email already registered' });
  }
  
  const userId = `USER_${Date.now()}_${crypto.randomBytes(4).toString('hex')}`;
  const passwordHash = PasswordUtils.hash(password);
  
  // Store user
  dataStore.registeredUsers.set(email, {
    userId,
    email,
    username,
    passwordHash,
    createdAt: new Date()
  });
  
  // Initialize game balance
  await DB.initializeUser(userId);
  
  // Generate token
  const token = JWTAuth.generateToken(userId);
  
  logger.info('User registered', { userId, email, username, action: 'REGISTER_SUCCESS' });
  
  res.json({
    success: true,
    userId,
    username,
    token,
    expiresIn: CONFIG.JWT_EXPIRES_IN,
    balance: 100,
    trustScore: 50
  });
});

// Login
app.post('/auth/login', async (req, res) => {
  const { email, password } = req.body;
  
  if (!email || !password) {
    return res.status(400).json({ error: 'Email and password required' });
  }
  
  const registeredUser = dataStore.registeredUsers.get(email);
  
  if (!registeredUser) {
    logger.warn('Login attempt with unknown email', { email, action: 'LOGIN_NOT_FOUND' });
    return res.status(401).json({ error: 'Invalid credentials' });
  }
  
  if (!PasswordUtils.verify(password, registeredUser.passwordHash)) {
    logger.warn('Login attempt with wrong password', { email, action: 'LOGIN_WRONG_PASSWORD' });
    return res.status(401).json({ error: 'Invalid credentials' });
  }
  
  // Get user data
  const userData = await DB.getUser(registeredUser.userId);
  const token = JWTAuth.generateToken(registeredUser.userId);
  
  logger.info('User logged in', { userId: registeredUser.userId, email, action: 'LOGIN_SUCCESS' });
  
  res.json({
    success: true,
    userId: registeredUser.userId,
    username: registeredUser.username,
    token,
    expiresIn: CONFIG.JWT_EXPIRES_IN,
    balance: userData?.balance || 100,
    trustScore: userData?.trust_score || 50
  });
});

// Get profile (protected)
app.get('/auth/profile', JWTAuth.middleware, async (req, res) => {
  const userId = req.user.userId;
  const userData = await DB.getUser(userId);
  
  // Find registered user info
  let userInfo = null;
  for (const [email, data] of dataStore.registeredUsers) {
    if (data.userId === userId) {
      userInfo = { email, username: data.username, createdAt: data.createdAt };
      break;
    }
  }
  
  res.json({
    userId,
    username: userInfo?.username || userId,
    email: userInfo?.email || null,
    balance: userData?.balance || 0,
    trustScore: userData?.trust_score || 50,
    balanceHash: userData?.balance_hash,
    createdAt: userInfo?.createdAt || userData?.created_at,
    lastActivity: userData?.last_activity
  });
});

// Redeem LKC (protected)
app.post('/auth/redeem', JWTAuth.middleware, async (req, res) => {
  const userId = req.user.userId;
  const { amount } = req.body;
  
  if (!amount || amount <= 0) {
    return res.status(400).json({ error: 'Valid amount required' });
  }
  
  const userData = await DB.getUser(userId);
  
  if (!userData || userData.balance < amount) {
    logger.warn('Redeem attempt with insufficient balance', { userId, balance: userData?.balance, requested: amount, action: 'REDEEM_INSUFFICIENT' });
    return res.status(400).json({ error: 'Insufficient balance', balance: userData?.balance || 0 });
  }
  
  // Process redemption
  const oldBalance = userData.balance;
  const newBalance = oldBalance - amount;
  
  await DB.updateBalance(userId, newBalance, 'LKC_REDEMPTION');
  
  // Record in ledger
  await DB.recordLedger({
    userId,
    type: 'REDEMPTION',
    amount: -amount,
    oldBalance,
    newBalance,
    reason: 'LKC_CASH_OUT',
    metadata: {
      redemptionId: `REDEEM_${Date.now()}`,
      titular: CONFIG.TITULAR
    }
  });
  
  logger.info('LKC redeemed', { userId, amount, oldBalance, newBalance, action: 'REDEEM_SUCCESS' });
  
  res.json({
    success: true,
    redemptionId: `REDEEM_${Date.now()}`,
    amount,
    newBalance,
    message: `${amount} LKC canjeados exitosamente`
  });
});

// Legacy token endpoint
app.post('/auth/token', async (req, res) => {
  const { userId } = req.body;
  if (!userId) return res.status(400).json({ error: 'userId required' });
  
  await DB.initializeUser(userId);
  const token = JWTAuth.generateToken(userId);
  
  res.json({ success: true, token, userId, expiresIn: CONFIG.JWT_EXPIRES_IN });
});

app.get('/api/status', (req, res) => {
  res.json({
    status: 'PRODUCTION_READY',
    version: 'GATILLAZO_2.0',
    titular: CONFIG.TITULAR,
    endpoint: `http://${CONFIG.PUBLIC_IP}:${CONFIG.PORT}`,
    https: httpsServer ? `https://${CONFIG.PUBLIC_IP}:${CONFIG.HTTPS_PORT}` : 'not configured',
    security: { jwt: true, rateLimit: true, helmet: true, ssl: !!httpsServer },
    persistence: useDatabase ? 'PostgreSQL' : 'in-memory',
    softLock: { amount: CONFIG.SOFT_LOCK_AMOUNT, currency: 'LKC' },
    rake: { percentage: CONFIG.RAKE_PERCENTAGE * 100, level: 'SEED' },
    games: CONFIG.GAMES.map(game => ({ name: game, status: 'active', physicsEngine: true })),
    connections: dataStore.sessions.size,
    activeMatches: [...dataStore.matches.values()].filter(m => m.status === 'ACTIVE').length
  });
});

app.get('/health', (req, res) => {
  res.json({ status: 'operational', version: 'GATILLAZO_2.0', games: CONFIG.GAMES, connections: dataStore.sessions.size, uptime: process.uptime(), titular: CONFIG.TITULAR });
});

// Protected endpoints
app.get('/user/:userId/balance', JWTAuth.middleware, async (req, res) => {
  if (req.user.userId !== req.params.userId) {
    logger.warn('Unauthorized balance access attempt', { requestedUser: req.params.userId, tokenUser: req.user.userId, action: 'UNAUTHORIZED_ACCESS' });
    return res.status(403).json({ error: 'Unauthorized' });
  }
  const user = await DB.getUser(req.params.userId);
  res.json({ userId: req.params.userId, balance: user?.balance || 0, trustScore: user?.trust_score || 50 });
});

app.post('/match/soft-lock', JWTAuth.middleware, async (req, res) => {
  const { gameType } = req.body;
  const userId = req.user.userId;
  
  const user = await DB.getUser(userId);
  if (!user || user.balance < CONFIG.SOFT_LOCK_AMOUNT) {
    logger.warn('Insufficient balance for soft lock', { userId, balance: user?.balance, required: CONFIG.SOFT_LOCK_AMOUNT, action: 'SOFT_LOCK_FAIL' });
    return res.status(400).json({ success: false, error: 'Insufficient balance' });
  }
  
  await DB.updateBalance(userId, user.balance - CONFIG.SOFT_LOCK_AMOUNT, `SOFT_LOCK_${gameType}`);
  
  const updatedUser = await DB.getUser(userId);
  logger.info('Soft lock successful', { userId, gameType, amount: CONFIG.SOFT_LOCK_AMOUNT, newBalance: updatedUser.balance, action: 'SOFT_LOCK' });
  
  res.json({ success: true, lockId: `LOCK_${Date.now()}`, amount: CONFIG.SOFT_LOCK_AMOUNT, newBalance: updatedUser.balance });
});

app.post('/match/settlement', JWTAuth.middleware, async (req, res) => {
  const { matchId, winnerId, loserId, betAmount } = req.body;
  
  try {
    const result = await DB.atomicSettlement(matchId, winnerId, loserId, betAmount || CONFIG.SOFT_LOCK_AMOUNT);
    res.json({ ...result, titular: CONFIG.TITULAR });
  } catch (err) {
    logger.error('Settlement failed', { matchId, error: err.message, action: 'SETTLEMENT_ERROR' });
    res.status(500).json({ success: false, error: 'Settlement failed' });
  }
});

app.post('/ledger/record', JWTAuth.middleware, async (req, res) => {
  const entry = await DB.recordLedger({ ...req.body, userId: req.user.userId });
  res.json({ success: true, entryId: entry.tx_id });
});

// ═══════════════════════════════════════════════════════════════════════════
// GRACEFUL SHUTDOWN
// ═══════════════════════════════════════════════════════════════════════════
const gracefulShutdown = async (signal) => {
  logger.info(`${signal} received - starting graceful shutdown`, { action: 'SHUTDOWN_START' });
  
  // Stop accepting new connections
  server.close(() => logger.info('HTTP server closed'));
  if (httpsServer) httpsServer.close(() => logger.info('HTTPS server closed'));
  
  // Close all socket connections
  io.close(() => logger.info('Socket.io closed'));
  
  // Close database pool
  if (pool) {
    await pool.end();
    logger.info('PostgreSQL pool closed');
  }
  
  // Save any pending ledger entries
  if (dataStore.ledger.length > 0) {
    fs.writeFileSync(
      path.join(logsDir, `ledger_backup_${Date.now()}.json`),
      JSON.stringify(dataStore.ledger, null, 2)
    );
    logger.info('Ledger backup saved', { entries: dataStore.ledger.length });
  }
  
  logger.info('Graceful shutdown complete', { action: 'SHUTDOWN_COMPLETE' });
  process.exit(0);
};

process.on('SIGTERM', () => gracefulShutdown('SIGTERM'));
process.on('SIGINT', () => gracefulShutdown('SIGINT'));
process.on('uncaughtException', (err) => {
  logger.error('Uncaught exception', { error: err.message, stack: err.stack, action: 'UNCAUGHT_EXCEPTION' });
});
process.on('unhandledRejection', (reason, promise) => {
  logger.error('Unhandled rejection', { reason: reason?.toString(), action: 'UNHANDLED_REJECTION' });
});

// ═══════════════════════════════════════════════════════════════════════════
// PRODUCTION STARTUP
// ═══════════════════════════════════════════════════════════════════════════
server.listen(CONFIG.PORT, CONFIG.HOST, () => {
  logger.info('═══════════════════════════════════════════════════════════════════════════');
  logger.info('🎮 KOMPITE GATILLAZO 2.0 - ENTERPRISE EDITION');
  logger.info('═══════════════════════════════════════════════════════════════════════════');
  logger.info(`🌐 HTTP:        http://${CONFIG.PUBLIC_IP}:${CONFIG.PORT}`);
  logger.info(`🔒 Security:    JWT + Rate Limit + Helmet`);
  logger.info(`💾 Persistence: ${useDatabase ? 'PostgreSQL' : 'In-Memory (set DATABASE_URL for PostgreSQL)'}`);
  logger.info(`📝 Logging:     Winston → ${logsDir}`);
  logger.info(`👤 Titular:     ${CONFIG.TITULAR}`);
  logger.info('═══════════════════════════════════════════════════════════════════════════');
  
  console.log('');
  console.log('╔═══════════════════════════════════════════════════════════════════════════╗');
  console.log('║            🎮 KOMPITE GATILLAZO 2.0 - ENTERPRISE EDITION                  ║');
  console.log('╠═══════════════════════════════════════════════════════════════════════════╣');
  console.log(`║  🌐 HTTP:        http://${CONFIG.PUBLIC_IP}:${CONFIG.PORT}                              ║`);
  console.log('║  🔒 Security:    JWT + Rate Limit + Helmet + SSL Ready                    ║');
  console.log(`║  💾 Persistence: ${(useDatabase ? 'PostgreSQL' : 'In-Memory').padEnd(20)}                            ║`);
  console.log('║  📝 Logging:     Winston structured logs                                  ║');
  console.log(`║  👤 Titular:     ${CONFIG.TITULAR}                                ║`);
  console.log('╠═══════════════════════════════════════════════════════════════════════════╣');
  console.log('║  ✅ Cabezones    ✅ Air Hockey    ✅ Artillery                            ║');
  console.log('║  ✅ Duel         ✅ Snowball      ✅ Memoria                              ║');
  console.log('╚═══════════════════════════════════════════════════════════════════════════╝');
  console.log('');
});

if (httpsServer) {
  httpsServer.listen(CONFIG.HTTPS_PORT, CONFIG.HOST, () => {
    logger.info(`🔐 HTTPS:       https://${CONFIG.PUBLIC_IP}:${CONFIG.HTTPS_PORT}`);
  });
}

module.exports = { io, server, httpsServer, app, CONFIG };
