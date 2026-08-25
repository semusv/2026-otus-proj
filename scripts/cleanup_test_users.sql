-- Чистка автоматических тестовых пользователей из dev-БД (postman/curl прогоны).
-- Паттерны покрывают только machine-generated имена; ручные аккаунты не затрагиваются.
-- audit_log НЕ удаляется (требование аудита) — отвязываем user_id, событие остаётся.

\echo '=== Будут удалены: ==='
SELECT username, created_at FROM users
WHERE username LIKE 'qa\_view\_%'
   OR username LIKE 'qa\_analyst\_%'
   OR username LIKE 'qa\_self\_%'
   OR username LIKE 'qa\_weak\_%'
   OR username LIKE 'hacker\_%'
   OR username IN ('live_self1', 'live_analyst1')
ORDER BY created_at;

BEGIN;

CREATE TEMP TABLE _targets ON COMMIT DROP AS
SELECT id FROM users
WHERE username LIKE 'qa\_view\_%'
   OR username LIKE 'qa\_analyst\_%'
   OR username LIKE 'qa\_self\_%'
   OR username LIKE 'qa\_weak\_%'
   OR username LIKE 'hacker\_%'
   OR username IN ('live_self1', 'live_analyst1');

DELETE FROM chat_messages
WHERE chat_session_id IN (SELECT id FROM chat_sessions WHERE user_id IN (SELECT id FROM _targets));
DELETE FROM chat_sessions WHERE user_id IN (SELECT id FROM _targets);
DELETE FROM sessions WHERE user_id IN (SELECT id FROM _targets);
UPDATE audit_log SET user_id = NULL WHERE user_id IN (SELECT id FROM _targets);
DELETE FROM users WHERE id IN (SELECT id FROM _targets);

COMMIT;

\echo '=== Остались: ==='
SELECT username, role_id, created_at FROM users ORDER BY created_at;
