# GraphRAG Frontend (этап 8)

Минимальный SPA: **Vite + React 19 + TypeScript** без Redux/UI-китов и роутера.
Тёмно-синяя тема на чистом CSS (переменные в `src/styles/theme.css`), без внешних CDN (закрытый контур).

## Экраны

| Экран | Доступ | Что делает |
|---|---|---|
| **Login** | все | `POST /auth/login` → JWT в `sessionStorage`, профиль `/auth/me` (роль + метки доступа) |
| **Chat** | все | SSE-стриминг `POST /api/chat`: токены, чипы стадий конвейера, интерактивные цитаты `[S#]` ↔ карточки источников (клик/ховер — подсветка и подскролл), путь по графу, trace_id/replans/notes |
| **Admin** | только `admin` | Кнопка запуска ingestion (с confirm), поллинг статуса с таймером «идёт NN мин», **«Состояние хранилищ»** (`GET /admin/stats`: Qdrant points, Neo4j Act/Authority/Topic/Concept+рёбра, PG users/sessions/messages) |

## Запуск

### Продакшн-путь (docker-compose)

```bash
cd infra && docker compose up -d --build frontend
```

SPA доступен через Traefik: **http://localhost/** (nginx same-origin проксирует
`/api`, `/auth`, `/admin`, `/health` на `backend:8000`; для SSE отключена буферизация).
CORS в бэкенде не используется — браузер ходит только на `localhost`.

### Дев-режим (vite dev server)

```bash
cd frontend && npm ci && npm run dev
```

Прокси dev-сервера идёт на `http://127.0.0.1:8000` (переопределяется `VITE_DEV_PROXY_TARGET`
в `frontend/.env.local`). Бэкенд при этом доступен напрямую, минуя Traefik.

### Тесты и качество

```bash
make frontend-lint     # eslint + tsc --noEmit (gate)
make frontend-test     # vitest smoke: парсер SSE-фреймов, маппинг ошибок API (testTimeout 5s)
make frontend-build    # production build
make openapi-types     # регенерация src/lib/api-types.ts из docs/api/openapi.yaml
```

Типы API генерируются из коммиченного контракта `docs/api/openapi.yaml` (`openapi-typescript`)
и коммитятся. Контракт изменился → `make openapi-types` → коммит. SSE-payload'ы
(`status/token/done/error`) типизированы вручную в `src/lib/sse.ts` по описанию контракта
(в yaml эндпоинт объявлен как object); структура `done` == ChatResponse бэкенда.

## Пользователи и регистрация

Стартовые учётки создаёт сид (`make seed-users` или автоматически при старте backend):

| Логин | Пароль | Роль | Метки доступа (clearance) |
|---|---|---|---|
| viewer | viewer123 | viewer | PUBLIC |
| analyst | analyst123 | analyst | PUBLIC, INTERNAL |
| admin | admin123 | admin | PUBLIC, INTERNAL, SECRET |

Способы создать нового пользователя:
- **Саморегистрация** — на экране входа ссылка «Зарегистрироваться» (или `POST /auth/register`).
  Умышленно минимальная роль **viewer** (только PUBLIC); расширение доступа — только через админа.
- **Админ** — вкладка Админ → карточка «Пользователи»: список учёток + форма создания с выбором
  роли (`POST /admin/users`, только роль admin; дубликат имени → 409, факт создания пишется в аудит).

Проверка разграничения доступа на двух пользователях:
1. Админ создаёт `qa_view` (viewer) и `qa_analyst` (analyst).
2. Оба входят в SPA: в шапке у них разные метки доступа.
3. Один и тот же вопрос: у qa_view все цитаты `[S#]` помечены PUBLIC, INTERNAL-источники
   отфильтрованы ещё на этапе выборки из Qdrant/Neo4j; qa_analyst видит и INTERNAL.
4. `qa_view` получает 403 на любой `/admin/*`; qa_analyst тоже. Admin — 200.

Тот же сценарий автоматизирован Postman-коллекцией `postman/graphrag.postman_collection.json`
(18 запросов с ассертами, включая разбор SSE-чата) + окружение `postman/local.postman_environment.json`.
Запуск из Postman Runner по порядку либо:

```bash
make postman-run   # npx newman run ... -e postman/local.postman_environment.json
```

## Как работают метки секретности

Механизм двухсторонний: метка на документе + метки у пользователя, пересечение на каждом чтении.

**На документах (ingestion):** метка акта вычисляется детерминированно из его id
(`backend/app/ingestion/clearance.py`): md5(act_id) % 100 раскладывает акты по корзинам
SECRET / INTERNAL / PUBLIC с настраиваемыми процентами (`APP_INGEST_SECRET_PERCENT`,
`APP_INGEST_INTERNAL_PERCENT`; остаток — PUBLIC). Это демо-разметка вместо реальной гриф-полики:
один и тот же акт всегда получает одну и ту же метку между прогонами. Метка пишется в payload
каждого чанка Qdrant (с payload-индексом `clearance`) и в свойство узла `(:Act {clearance})` Neo4j.

**У пользователей:** меток per-user нет — их даёт **роль** (`ROLE_CLEARANCES` в
`app/core/security.py`): viewer → PUBLIC; analyst → +INTERNAL; admin → +SECRET. Профиль можно
увидеть в `GET /auth/me` (и он отображается в SPA после входа).

**При поиске (defense in depth):** clearances берутся из токена → передаются в каждый инструмент
поиска → фильтр `clearance IN [...]` применяется **внутри** Qdrant (Filter), Cypher-запросов Neo4j
(в т.ч. на каждом Act вдоль пути по графу) и контрольным пост-фильтром в output-guardrails.
Соседство по графу права не расширяет.

## Демо-сценарий (acceptance этапа 8)

Стек поднят: `infra/.env` заполнен, сид-пользователи созданы (`make seed-users`),
корpus про-ingested. Открыть **http://localhost/**

1. **Логин analyst** (`analyst` / `analyst123`) → хедер показывает роль и метки `[PUBLIC, INTERNAL]`.
2. **Вопрос**: «Какие требования к раскрытию информации эмитентами?»
   - под сообщением загораются чипы стадий: Guardrails → Planner (tools) → Retrieval → Fusion+Rerank → Generate → Evaluate → Citations check;
   - при re-plan на Planner появляется бейдж `re-plan ×N`;
   - текст стримится с мигающим курсором;
   - после финала: карточки источников `[S#] Название · чанк N` с бейджами clearance,
     цепочка «Путь по графе» (чипы актов со стрелками), бейдж статуса ok/degraded/empty, `trace_id`.
3. **Продолжение диалога** — второй уточняющий вопрос: агент помнит сессию (`session_id` переиспользуется,
   в тулбаре виден префикс id). Кнопка **«+ Новый чат»** начинает новую сессию (чистит ленту и `session_id`,
   прерывает активный стриминг).
4. **RBAC**: повторить шаги 1–2 под **viewer** (`viewer` / `viewer123`) и задать вопрос про
   INTERNAL/SECRET акт (например «Расскажи содержание акта 102010099 Гражданский кодекс РСФСР»):
   в цитатах и пути графа отсутствуют непубличные источники, статус `degraded`/`empty`,
   секретного текста нет нигде. Вкладка **Админ** у viewer/analyst не отображается, прямой
   вызов `/admin/*` возвращает 403.
5. **Admin** (`admin` / `admin123`) → вкладка Админ → кнопка «Запустить ingestion» (с подтверждением) →
   статус `running` с таймером прошедшего времени (поллинг каждые 2 c) → `done` со статистикой.
   Полный прогон corpus_test (~100 XML / ~2300 чанков) на CPU занимает ~10–30 минут — это не
   зависание: прогресс виден в `docker logs graphrag-backend`, счётчики «Состояния хранилищ»
   растут по мере записи. Статус прогона хранится в памяти backend: перезапуск контейнера
   сбрасывает его в `idle`. Повторный клик во время прогона → «Прогон уже выполняется» (409).
   Панель поясняет источник корпуса: backend читает
   `APP_INGEST_CORPUS_DIR` (compose монтирует туда `corpus_test/` репозитория как `/data/corpus`),
   прогон идемпотентен — акты перезаписываются без дублей; блок «Как это работает» раскрывает шаги
   конвейера.
6. **Статистика БД**: карточка «Состояние хранилищ» показывает текущее наполнение
   (Qdrant `chunks.points`, Neo4j acts/authorities/topics/concepts/relationships,
   PG users/chat_sessions/chat_messages); кнопка «Обновить статистику», автообновление во время прогона.
7. **Интерактивные источники**: маркеры `[S#]` в тексте ответа кликабельны — подсвечивают
   соответствующую карточку источника (и наоборот: клик по карточке подсвечивает маркер в тексте).
8. **Регистрация**: выйти → «Зарегистрироваться» → новый аккаунт (viewer) → тот же вопрос даёт
   только PUBLIC-цитаты; админ может поднять роль через Админ → Пользователи.

## Структура

```
src/
├── main.tsx              # entrypoint, стили темы
├── App.tsx               # auth-состояние, экраны, хедер с ролью/clearances/logout
├── pages/
│   ├── LoginPage.tsx     # форма входа → JWT → /auth/me
│   ├── ChatPage.tsx      # SSE-стриминг, сообщения, предложения вопросов
│   └── AdminPage.tsx     # ingestion + поллинг статуса
├── components/
│   ├── PipelineChips.tsx # чипы стадий конвейера (re-plan/tools)
│   └── Badges.tsx        # clearance/статус бейджи
├── lib/
│   ├── api-types.ts      # СГЕНЕРИРОВАНО из docs/api/openapi.yaml — не править руками
│   ├── api.ts            # fetch-обёртка: Bearer, X-Trace-Id, ошибки по схемам контракта
│   ├── sse.ts            # парсер SSE-фреймов + типы событий чата
│   └── auth.ts           # JWT в sessionStorage (+expires_in), профиль
└── styles/               # theme.css (переменные) + app/components.css
```

Безопасность: JWT живёт до закрытия вкладки (`sessionStorage`), автоматически очищается
при logout и на любой ответ 401; `X-Trace-Id` (32-hex) генерируется на каждый запрос —
сквозная трассировка фронт→Traefik→backend→Jaeger/Langfuse (этап 7).
