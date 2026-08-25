# Postman / Newman (этапы 3–9)

Коллекция ведётся в `tests/postman/collection.json`, окружение — `env.local.json`.
Источник правды по контракту — `docs/api/openapi.yaml` (экспорт из FastAPI:
`make openapi-export`); при изменении эндпоинтов коллекция дополняется вручную
по контракту.

## Запуск через Newman

```powershell
npm install -g newman          # один раз
newman run tests/postman/collection.json -e tests/postman/env.local.json
```

Против Traefik: заменить `baseUrl` на `http://api.localhost`
(переменная `baseUrlTraefik` в окружении).

## Сценарии этапа 3

1. Health (+ проверка эха X-Trace-Id)
2. Login admin → токен сохраняется в переменную коллекции
3. Login с неверным паролем → 401 invalid_credentials
4. GET /auth/me по токену → role + clearances
5. GET /auth/me без токена → 401 invalid_token

На этапах 5–9 добавляются чат/RBAC/метрики; полный прогон — gate этапа 9.
