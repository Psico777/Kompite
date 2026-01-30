-- ═══════════════════════════════════════════════════════════════════════════
-- KOMPITE MVP - SCHEMA DE BASE DE DATOS POSTGRESQL
-- Titular: Yordy Jesús Rojas Baldeon
-- ═══════════════════════════════════════════════════════════════════════════

-- Tabla de usuarios
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR(255) UNIQUE NOT NULL,
    balance DECIMAL(18, 8) DEFAULT 100.00000000,
    trust_score INTEGER DEFAULT 50 CHECK (trust_score >= -100 AND trust_score <= 100),
    balance_hash VARCHAR(64) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_active BOOLEAN DEFAULT TRUE
);

-- Tabla de partidas
CREATE TABLE IF NOT EXISTS matches (
    id SERIAL PRIMARY KEY,
    match_id VARCHAR(255) UNIQUE NOT NULL,
    game_type VARCHAR(50) NOT NULL CHECK (game_type IN ('cabezones', 'air_hockey', 'artillery', 'duel', 'snowball', 'memoria')),
    player1_id VARCHAR(255) NOT NULL,
    player2_id VARCHAR(255),
    pot DECIMAL(18, 8) DEFAULT 0,
    status VARCHAR(20) DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'ACTIVE', 'COMPLETED', 'CANCELLED', 'ABANDONED')),
    winner_id VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    ended_at TIMESTAMP,
    game_state JSONB
);

-- Ledger con Triple Entry (Débito, Crédito, Rake)
CREATE TABLE IF NOT EXISTS ledger (
    id SERIAL PRIMARY KEY,
    tx_id VARCHAR(255) UNIQUE NOT NULL,
    user_id VARCHAR(255) NOT NULL,
    entry_type VARCHAR(20) NOT NULL CHECK (entry_type IN ('DEBIT', 'CREDIT', 'RAKE', 'SOFT_LOCK', 'UNLOCK', 'BALANCE_UPDATE')),
    amount DECIMAL(18, 8) NOT NULL,
    match_id VARCHAR(255),
    reason VARCHAR(255),
    old_balance DECIMAL(18, 8),
    new_balance DECIMAL(18, 8),
    tx_hash VARCHAR(64) NOT NULL,
    tx_metadata JSONB,
    titular VARCHAR(255) DEFAULT 'Yordy Jesús Rojas Baldeon',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Sesiones activas
CREATE TABLE IF NOT EXISTS sessions (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR(255) NOT NULL,
    socket_id VARCHAR(255) NOT NULL,
    jwt_token_hash VARCHAR(64),
    ip_address VARCHAR(45),
    user_agent TEXT,
    connected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_ping TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_active BOOLEAN DEFAULT TRUE
);

-- Índices para performance
CREATE INDEX IF NOT EXISTS idx_users_user_id ON users(user_id);
CREATE INDEX IF NOT EXISTS idx_matches_status ON matches(status);
CREATE INDEX IF NOT EXISTS idx_matches_game_type ON matches(game_type);
CREATE INDEX IF NOT EXISTS idx_ledger_user_id ON ledger(user_id);
CREATE INDEX IF NOT EXISTS idx_ledger_match_id ON ledger(match_id);
CREATE INDEX IF NOT EXISTS idx_ledger_created_at ON ledger(created_at);
CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);

-- Vista para auditoría financiera
CREATE OR REPLACE VIEW v_financial_audit AS
SELECT 
    l.tx_id,
    l.user_id,
    l.entry_type,
    l.amount,
    l.match_id,
    l.old_balance,
    l.new_balance,
    l.tx_hash,
    l.created_at,
    u.balance as current_balance,
    u.trust_score
FROM ledger l
LEFT JOIN users u ON l.user_id = u.user_id
ORDER BY l.created_at DESC;

-- Función para settlement atómico
CREATE OR REPLACE FUNCTION atomic_settlement(
    p_match_id VARCHAR(255),
    p_winner_id VARCHAR(255),
    p_loser_id VARCHAR(255),
    p_bet_amount DECIMAL(18, 8),
    p_rake_percentage DECIMAL(5, 4)
) RETURNS JSONB AS $$
DECLARE
    v_rake DECIMAL(18, 8);
    v_winnings DECIMAL(18, 8);
    v_winner_old DECIMAL(18, 8);
    v_winner_new DECIMAL(18, 8);
    v_tx_id VARCHAR(255);
BEGIN
    v_rake := p_bet_amount * 2 * p_rake_percentage;
    v_winnings := (p_bet_amount * 2) - v_rake;
    v_tx_id := 'TX_' || EXTRACT(EPOCH FROM NOW())::BIGINT || '_' || substr(md5(random()::text), 1, 8);
    
    -- Obtener balance actual del ganador
    SELECT balance INTO v_winner_old FROM users WHERE user_id = p_winner_id FOR UPDATE;
    v_winner_new := v_winner_old + v_winnings;
    
    -- Actualizar balance del ganador
    UPDATE users SET 
        balance = v_winner_new,
        balance_hash = encode(sha256((p_winner_id || ':' || v_winner_new::text)::bytea), 'hex'),
        last_activity = NOW()
    WHERE user_id = p_winner_id;
    
    -- Registrar en ledger (Triple Entry)
    INSERT INTO ledger (tx_id, user_id, entry_type, amount, match_id, old_balance, new_balance, tx_hash, reason)
    VALUES 
        (v_tx_id || '_D', p_loser_id, 'DEBIT', p_bet_amount, p_match_id, NULL, NULL, 
         encode(sha256((v_tx_id || '_D')::bytea), 'hex'), 'MATCH_LOSS'),
        (v_tx_id || '_C', p_winner_id, 'CREDIT', v_winnings, p_match_id, v_winner_old, v_winner_new,
         encode(sha256((v_tx_id || '_C')::bytea), 'hex'), 'MATCH_WIN'),
        (v_tx_id || '_R', 'HOUSE', 'RAKE', v_rake, p_match_id, NULL, NULL,
         encode(sha256((v_tx_id || '_R')::bytea), 'hex'), 'RAKE_8%');
    
    -- Actualizar match
    UPDATE matches SET 
        status = 'COMPLETED',
        winner_id = p_winner_id,
        ended_at = NOW()
    WHERE match_id = p_match_id;
    
    RETURN jsonb_build_object(
        'success', true,
        'tx_id', v_tx_id,
        'winnings', v_winnings,
        'rake', v_rake,
        'winner_new_balance', v_winner_new
    );
    
EXCEPTION WHEN OTHERS THEN
    RAISE;
END;
$$ LANGUAGE plpgsql;
