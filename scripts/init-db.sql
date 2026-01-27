-- =============================================================================
-- KOMPITE - Script de Inicialización de Base de Datos
-- Configuraciones de seguridad y extensiones necesarias
-- =============================================================================

-- Extensiones requeridas
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Configuración de timezone
SET timezone = 'America/Lima';

-- =============================================================================
-- FUNCIONES DE UTILIDAD PARA INTEGRIDAD
-- =============================================================================

-- Función para verificar integridad del balance de un usuario
CREATE OR REPLACE FUNCTION verify_user_balance_integrity(p_user_id UUID)
RETURNS BOOLEAN AS $$
DECLARE
    v_balance NUMERIC(18, 8);
    v_salt TEXT;
    v_stored_hash TEXT;
    v_computed_hash TEXT;
BEGIN
    SELECT lkoins_balance, balance_salt, balance_hash
    INTO v_balance, v_salt, v_stored_hash
    FROM users
    WHERE id = p_user_id;
    
    IF NOT FOUND THEN
        RETURN FALSE;
    END IF;
    
    -- Calcular hash esperado
    v_computed_hash := encode(
        sha256(
            (p_user_id::TEXT || ':' || v_balance::TEXT || ':' || v_salt)::BYTEA
        ),
        'hex'
    );
    
    RETURN v_stored_hash = v_computed_hash;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Función para actualizar balance con verificación atómica
CREATE OR REPLACE FUNCTION atomic_balance_update(
    p_user_id UUID,
    p_amount NUMERIC(18, 8),
    p_expected_version INTEGER
)
RETURNS TABLE (
    success BOOLEAN,
    new_balance NUMERIC(18, 8),
    new_version INTEGER,
    error_message TEXT
) AS $$
DECLARE
    v_current_balance NUMERIC(18, 8);
    v_current_version INTEGER;
    v_new_balance NUMERIC(18, 8);
    v_salt TEXT;
    v_new_hash TEXT;
BEGIN
    -- Bloquear la fila para prevenir race conditions
    SELECT lkoins_balance, balance_version, balance_salt
    INTO v_current_balance, v_current_version, v_salt
    FROM users
    WHERE id = p_user_id
    FOR UPDATE;
    
    IF NOT FOUND THEN
        RETURN QUERY SELECT FALSE, NULL::NUMERIC(18,8), NULL::INTEGER, 'Usuario no encontrado'::TEXT;
        RETURN;
    END IF;
    
    -- Verificar versión para concurrencia optimista
    IF v_current_version != p_expected_version THEN
        RETURN QUERY SELECT FALSE, v_current_balance, v_current_version, 
            'Conflicto de versión - transacción concurrente detectada'::TEXT;
        RETURN;
    END IF;
    
    -- Calcular nuevo balance
    v_new_balance := v_current_balance + p_amount;
    
    -- Verificar que el balance no sea negativo
    IF v_new_balance < 0 THEN
        RETURN QUERY SELECT FALSE, v_current_balance, v_current_version, 
            'Balance insuficiente'::TEXT;
        RETURN;
    END IF;
    
    -- Calcular nuevo hash
    v_new_hash := encode(
        sha256(
            (p_user_id::TEXT || ':' || v_new_balance::TEXT || ':' || v_salt)::BYTEA
        ),
        'hex'
    );
    
    -- Actualizar balance, hash y versión
    UPDATE users
    SET lkoins_balance = v_new_balance,
        balance_hash = v_new_hash,
        balance_version = v_current_version + 1,
        updated_at = NOW()
    WHERE id = p_user_id;
    
    RETURN QUERY SELECT TRUE, v_new_balance, v_current_version + 1, NULL::TEXT;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- =============================================================================
-- TRIGGER PARA AUDITORÍA DE TRANSACCIONES
-- =============================================================================

CREATE OR REPLACE FUNCTION audit_transaction_insert()
RETURNS TRIGGER AS $$
BEGIN
    -- Log de auditoría para cada transacción
    IF NEW.transaction_type IN ('WITHDRAWAL', 'DEPOSIT') AND NEW.amount > 1000 THEN
        -- Marcar transacciones grandes para revisión
        INSERT INTO system_audits (
            checkpoint_timestamp,
            expected_vault,
            actual_user_sum,
            drift_detected,
            total_in_escrow,
            total_fees_collected,
            users_verified,
            status,
            details
        ) VALUES (
            NOW(),
            0, 0, 0, 0, 0, 0,
            'WARNING',
            jsonb_build_object(
                'trigger', 'large_transaction',
                'transaction_id', NEW.id,
                'amount', NEW.amount,
                'type', NEW.transaction_type
            )
        );
    END IF;
    
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- =============================================================================
-- ÍNDICES ADICIONALES PARA OPTIMIZACIÓN
-- =============================================================================

-- Índice parcial para usuarios activos con balance positivo
CREATE INDEX IF NOT EXISTS idx_users_active_with_balance 
ON users (lkoins_balance) 
WHERE is_active = TRUE AND lkoins_balance > 0;

-- Índice para búsqueda rápida de transacciones pendientes
CREATE INDEX IF NOT EXISTS idx_transactions_pending
ON transactions (user_id, created_at)
WHERE status = 'PENDING';

-- Índice para partidas en progreso
CREATE INDEX IF NOT EXISTS idx_matches_in_progress
ON matches (game_id, created_at)
WHERE status IN ('MATCHMAKING', 'LOCKED', 'IN_PROGRESS');

-- =============================================================================
-- GRANTS Y PERMISOS (Principio de mínimo privilegio)
-- =============================================================================

-- El rol de la aplicación solo puede ejecutar las funciones definidas
-- GRANT EXECUTE ON FUNCTION verify_user_balance_integrity TO kompite_app;
-- GRANT EXECUTE ON FUNCTION atomic_balance_update TO kompite_app;

COMMENT ON FUNCTION verify_user_balance_integrity IS 
'Verifica que el hash SHA-256 del balance coincida con el almacenado. 
Retorna FALSE si se detecta manipulación directa en la base de datos.';

COMMENT ON FUNCTION atomic_balance_update IS 
'Actualiza el balance de un usuario de forma atómica con control de concurrencia optimista.
Previene race conditions mediante bloqueo de fila y verificación de versión.';
