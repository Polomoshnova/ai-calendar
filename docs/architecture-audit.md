# Architecture Audit

Дата аудита: 2026-07-23  
Область: фактическая архитектура репозитория `ai-calendar` после Milestone 2C.

## 1. Резюме

Текущая реализация в целом соответствует слоистой архитектуре небольшого
FastAPI-приложения:

```text
HTTP / internal UI
        ↓
Pydantic schemas + application services
        ↓
domain values ── availability ── scheduler
        ↑
SQLAlchemy models / PostgreSQL adapters
```

Самая важная граница соблюдена:

- `app/scheduling` не импортирует FastAPI;
- `app/scheduling` не импортирует SQLAlchemy;
- `app/scheduling` не импортирует `app.models`;
- `app/availability` также не зависит от инфраструктуры;
- эта граница зафиксирована тестом
  `tests/unit/test_pure_module_boundaries.py`.

Основная scheduling orchestration реализована в
`app/services/scheduling.py`:

- `preview_schedule()` загружает пользователя, задачи и preferences из
  PostgreSQL;
- `generate_schedule_preview()` принимает нормализованные входы, вызывает
  availability и затем scheduler;
- public и internal preview используют эту общую orchestration.

Архитектура готова к дальнейшему развитию, но пока не является полностью
выраженной hexagonal/clean architecture. Application services напрямую знают
SQLAlchemy и ORM-модели, `app/services/tasks.py` принимает API-схемы, а
`app/internal/router.py` выполняет слишком много координационных обязанностей.
Для текущего объёма это приемлемо, но эти места станут основными точками роста.

`AI Gateway` и `TaskDraft` сейчас отсутствуют, что соответствует заявленному
scope README. Их не следует помещать в scheduler:

- `TaskDraft` должен быть доменным/application DTO до создания persistent
  `Task`;
- интерфейс `AI Gateway` должен быть application port;
- реализация конкретного AI-провайдера должна быть infrastructure adapter;
- orchestration преобразования текста в `TaskDraft` должна находиться в
  отдельном application service/use case.

## 2. Основание и целевая архитектура

В репозитории нет отдельной формальной спецификации target architecture.
Поэтому оценка опирается на:

1. README и заявленные ограничения milestone;
2. текущие package boundaries;
3. архитектурный тест чистоты scheduler/availability;
4. требование разместить будущие `AI Gateway` и `TaskDraft`;
5. обычное направление зависимостей для layered/hexagonal architecture.

Под **целевой архитектурой** далее понимается:

```text
Delivery
  FastAPI routes, internal web UI, CLI
        ↓
Application
  use cases, orchestration, ports
        ↓
Domain / pure scheduling core
  entities, value objects, policies, scheduler, availability
        ↑
Infrastructure adapters
  SQLAlchemy repositories, PostgreSQL, AI provider, calendar provider
```

Правило зависимостей: delivery и infrastructure могут зависеть от application
и domain contracts; domain и pure scheduling core не должны зависеть от
FastAPI, SQLAlchemy, БД, AI SDK или внешних провайдеров.

## 3. Структура директорий

| Директория | Фактическая роль | Оценка |
|---|---|---|
| `app/api` | Public HTTP delivery layer и FastAPI dependencies | Соответствует |
| `app/internal` | Internal delivery, internal schemas, scenario loading, export | Частично соответствует: router перегружен |
| `app/domain` | Enums, value objects и доменная валидация | Соответствует, но доменная модель пока тонкая |
| `app/availability` | Чистый расчёт рабочих и свободных интервалов | Соответствует |
| `app/scheduling` | Чистые scheduler types и deterministic algorithm | Соответствует |
| `app/services` | Application orchestration вместе с persistence operations | Частично соответствует |
| `app/models` | SQLAlchemy persistence models | Соответствует роли infrastructure |
| `app/schemas` | Public API DTO и domain mapping | Соответствует |
| `app/templates`, `app/static` | Internal server-rendered UI | Соответствует |
| `migrations` | Alembic/PostgreSQL schema evolution | Соответствует |
| `tests/unit` | Domain, availability и boundary tests | Соответствует |
| `tests/scenarios` | Автоматические scheduler regression scenarios | Соответствует |
| `tests/product` | Субъективная product validation | Соответствует |
| `tests/integration` | API, services и PostgreSQL integration | Соответствует |

Имена директорий понятны и не смешивают production frontend с backend.
Отдельного frontend-репозитория и build pipeline нет.

## 4. Компоненты

### 4.1 Domain

#### Что реализовано

- `app/domain/tasks.py`:
  - `TaskPriority`;
  - `TaskStatus`;
  - `PreferredTimeOfDay`;
  - `validate_task()`.
- `app/domain/preferences.py`:
  - `Weekday`;
  - `WallClockWindow`;
  - `WorkingHours`;
  - `SchedulingPreferences`;
  - defaults, parsing и serialization рабочих часов.
- `app/domain/timezones.py`:
  - валидация IANA timezone.

#### Обязанности

Domain содержит общие понятия и инварианты, используемые API schemas,
ORM-моделями, availability и scheduler.

#### Зависимости

- только Python standard library;
- `preferences.py` зависит от `domain.tasks.PreferredTimeOfDay`;
- нет FastAPI, Pydantic, SQLAlchemy или ORM.

#### Single Responsibility

В основном соблюдён. `preferences.py` совмещает:

- domain value objects;
- defaults;
- parsing/serialization JSON-представления.

Для текущего масштаба это допустимо. При появлении нескольких storage/transport
formats сериализацию лучше вынести в mapper/adapter.

#### Соответствие target

Хорошее. Однако это пока не полноценная rich domain model: persistent `Task` и
`User` представлены ORM-классами, а domain содержит enums и validators, но не
отдельные domain entities.

### 4.2 SQLAlchemy models

#### Что реализовано

- `User`;
- `Task`;
- `UserPreferences`;
- relationships, PostgreSQL enums/JSONB, check constraints и timestamps.

#### Обязанности

Хранение состояния и защита основных инвариантов на уровне БД.

#### Зависимости

- SQLAlchemy;
- `app.core.database.Base`;
- общие domain enums и validators.

#### Single Responsibility

В основном соблюдён. ORM-модели выполняют persistence mapping и минимальную
валидацию. Важные правила частично продублированы между:

- domain validation;
- Pydantic schemas;
- SQL constraints.

Это полезная defence in depth, пока правила остаются синхронизированными.

#### Соответствие target

Соответствует infrastructure/persistence layer. Недостающий архитектурный
элемент для более строгой target architecture — repository ports: application
services сейчас работают с ORM и `Session` напрямую.

### 4.3 Services

#### `app/services/tasks.py`

Реализует CRUD задач, проверку существования пользователя, повторную доменную
валидацию update и commits.

Зависимости:

- SQLAlchemy `Session`;
- ORM `Task` и `User`;
- `app.schemas.task.TaskCreate/TaskUpdate`;
- domain validation.

SRP соблюдён частично: модуль сфокусирован на task persistence use cases, но
application layer зависит от delivery DTO. Целевое направление лучше выразить
через application commands/domain inputs или repository port.

#### `app/services/preferences.py`

Загружает effective preferences, применяет defaults без persistence и явно
сохраняет preferences.

Зависимости:

- SQLAlchemy `Session`;
- ORM `User`, `UserPreferences`;
- domain preferences.

SRP в целом соблюдён. Inline-import `serialize_working_hours` не меняет
архитектуру, но показывает, что mapping persistence/domain находится внутри
сервиса.

#### `app/services/scheduling.py`

Содержит два уровня:

1. `preview_schedule()` — database-backed application use case;
2. `generate_schedule_preview()` — общая stateless orchestration нормализованных
   входов.

Также здесь находятся:

- SQL query для отбора pending задач в planning window;
- mapping ORM `Task` → `SchedulingTask`;
- сборка `SchedulerPreferences`;
- последовательность availability → scheduler.

Зависимости:

- SQLAlchemy и ORM для database-backed entry point;
- domain preferences/status;
- availability;
- scheduling core;
- preferences service.

SRP соблюдён частично. Модуль логически сфокусирован на schedule preview, но
объединяет persistence loading, mapping и pure orchestration. Это не приводит к
утечке инфраструктуры в scheduler, однако при добавлении других источников задач
стоит разделить:

- use case/orchestrator;
- repository/query adapter;
- mappers.

#### Соответствие target

Функционально соответствует application layer. Структурно это pragmatic service
layer, а не полностью инвертированная application layer.

### 4.4 Orchestration

#### Где происходит

Главная production orchestration:

```text
app/services/scheduling.py

preview_schedule()
  → load DB tasks
  → load effective preferences
  → generate_schedule_preview()
      → calculate_free_intervals()
      → build SchedulerPreferences
      → schedule_tasks()
```

Public route и internal route не реализуют scheduling rules:

- public route делегирует `preview_schedule()`;
- internal existing-user mode делегирует `preview_schedule()`;
- internal scenario mode делегирует `generate_schedule_preview()`.

#### SRP и target

Общая цепочка не дублируется между public и internal preview — это соответствует
target. Product runner всё ещё самостоятельно вызывает:

```text
calculate_free_intervals() → schedule_tasks()
```

Это дублирование orchestration на уровне product tooling. Алгоритм не
дублируется, но есть риск, что future preprocessing в application orchestrator
не попадёт в product runner.

### 4.5 Availability

#### Что реализовано

- immutable `TimeInterval`;
- timezone-aware working intervals;
- DST gap/fold resolution;
- clipping planning window;
- normalization/merge busy intervals;
- subtraction busy from working time.

#### Зависимости

- standard library и `zoneinfo`;
- domain working-hours/timezone contracts;
- никаких framework или persistence dependencies.

#### Single Responsibility

Соблюдён. Модуль отвечает только за преобразование planning window, рабочих часов
и busy intervals в free intervals.

#### Соответствие target

Полное. Это чистый deterministic domain service.

### 4.6 Scheduler

#### Что реализовано

- deterministic ordering задач;
- priority/deadline/time-of-day scoring;
- splitting и minimum session;
- maximum sessions/day;
- breaks и cutoff;
- accepted blocks;
- structured reason codes, score components, warnings и unscheduled reasons;
- versioned result (`2a.1`).

#### Зависимости

Фактические imports:

- `app.availability.TimeInterval`;
- domain enums и timezone validation;
- собственные `app.scheduling.types`;
- Python standard library.

Прямой ответ на ключевые вопросы:

| Проверка | Результат |
|---|---|
| Зависит ли scheduler от FastAPI? | Нет |
| Зависит ли scheduler от SQLAlchemy? | Нет |
| Зависит ли scheduler от ORM/моделей БД? | Нет |
| Зависит ли scheduler от AI/OpenAI SDK? | Нет |
| Зафиксировано ли это тестом? | Да, `test_pure_module_boundaries.py` |

#### Single Responsibility

На уровне package соблюдён: scheduler принимает нормализованные inputs и free
intervals, возвращает результат. `scheduler.py` содержит много private functions,
но все относятся к одной причине изменения — scheduling policy/algorithm.

#### Соответствие target

Полное. Scheduler правильно остаётся pure core и не должен в будущем получать
доступ к БД, HTTP, AI Gateway или calendar providers.

### 4.7 Public API

#### Что реализовано

- Task CRUD;
- stateless schedule preview;
- Pydantic request/response schemas;
- FastAPI dependency для DB session;
- HTTP mapping ошибок.

#### Зависимости

- FastAPI;
- API schemas;
- application services;
- `app/api/v1/tasks.py` дополнительно импортирует ORM `Task` и SQLAlchemy
  `Session` для lookup/type annotations.

#### Single Responsibility

Scheduling route тонкий и соответствует delivery responsibility. Tasks route
частично знает persistence model через `require_task()`. Поведение корректно, но
граница delivery/application не полностью строгая.

#### Соответствие target

Хорошее для текущего этапа. В строгой target architecture routes должны зависеть
от use case contracts и response DTO, а не от ORM entities.

### 4.8 Internal tools

#### Что реализовано

- development-only Jinja2 page;
- gated internal assets/API;
- existing-user и stateless product-scenario modes;
- user/preferences endpoints;
- scenario discovery/loading;
- internal preview;
- review export;
- local timezone presentation;
- vanilla JS/CSS UI.

#### Зависимости

`app/internal/router.py` зависит от:

- FastAPI/Jinja2;
- DB dependency и ORM `User`;
- public scheduling response schema;
- preferences/tasks/scheduling services;
- internal schemas, export и scenario loader;
- configuration.

`internal.schemas` зависит от domain, scheduling types и public scheduling
schemas. `scenario_loader`, `export` и `presentation` более узкие и изолированные.

#### Single Responsibility

На уровне package обязанности связаны общей целью product-validation tool.
На уровне `router.py` SRP соблюдён частично: один модуль содержит page delivery,
static file delivery, users/preferences, scenarios, preview и export.

Internal route не содержит scheduling algorithm, но знает слишком много деталей
нескольких подсистем. При росте internal tools разумно разделить routers по
capability, не изменяя core.

#### Соответствие target

Функционально хорошее:

- boundary отделён от public API;
- disabled state возвращает 404;
- scenario preview и review stateless;
- scheduler вызывается через shared orchestration.

Структурно internal schemas переиспользуют public schema
`DateTimeInterval/SchedulePreviewResponse`. Это уменьшает duplication, но связывает
два delivery interface. Более строгая target architecture использовала бы общий
application DTO, от которого оба transport слоя строят свои representations.

### 4.9 Product validation

#### Что реализовано

- 10 JSON scenarios;
- expected observations для human review;
- standalone runner без БД;
- internal lab загружает те же scenario files;
- unit/integration tests для loader, preview и export.

#### Зависимости

Runner зависит напрямую от domain, availability и scheduler. Он не зависит от
FastAPI, SQLAlchemy или БД.

#### Single Responsibility

Соблюдён: runner предназначен для человеко-ориентированной оценки, а не для
автоматического quality verdict.

#### Соответствие target

Хорошее, с одним gap: runner повторяет application orchestration availability →
scheduler вместо использования общего normalized-input orchestrator. Сейчас
результат эквивалентен, но future preprocessing может разойтись.

### 4.10 Tests

Тестовая структура поддерживает архитектуру:

- unit tests проверяют domain/availability;
- scenario tests проверяют scheduler behavior;
- integration tests проверяют PostgreSQL/API;
- product scenarios оставляют субъективный verdict человеку;
- boundary test запрещает infrastructure imports в availability/scheduling.

Gap: нет аналогичных dependency tests для направления:

- domain → infrastructure;
- API → ORM;
- services → API schemas;
- internal → public schemas.

## 5. Фактическое направление зависимостей

```text
app.main
├── app.api
│   ├── app.schemas
│   ├── app.services
│   ├── app.models          ← небольшая утечка persistence в tasks route
│   └── app.core.database
└── app.internal
    ├── app.services
    ├── app.models
    ├── app.schemas         ← coupling с public delivery DTO
    └── app.core.config/database

app.services
├── app.models / SQLAlchemy
├── app.schemas             ← только task service
├── app.domain
├── app.availability
└── app.scheduling

app.models
├── app.core.database
└── app.domain

app.scheduling
├── app.availability
└── app.domain

app.availability
└── app.domain

app.domain
└── standard library
```

Циклических runtime imports между слоями не обнаружено. `TYPE_CHECKING` imports
между ORM-моделями нужны только для typing relationships.

## 6. Где должны появиться AI Gateway и TaskDraft

### TaskDraft

`TaskDraft` не должен быть SQLAlchemy-моделью и не должен входить в scheduler.
Это неперсистентный результат интерпретации пользовательского намерения до
explicit confirmation.

Рекомендуемое место:

```text
app/domain/task_drafts.py
```

или, если draft содержит только use-case-specific данные без доменного поведения:

```text
app/application/dto/task_draft.py
```

Он должен содержать поля, необходимые для будущего `TaskCreate`, provenance и
validation issues, но не DB session, HTTP request или provider response objects.

Поток:

```text
natural-language input
  → parse-task use case
  → AI Gateway port
  → validated TaskDraft
  → user confirmation/edit
  → existing task creation use case
  → persistent Task
```

### AI Gateway

Контракт/port:

```text
app/application/ports/ai_gateway.py
```

или при сохранении текущей структуры:

```text
app/services/ports/ai_gateway.py
```

Provider-specific adapter:

```text
app/integrations/ai/openai_gateway.py
```

Composition/configuration:

```text
app/api/dependencies.py
app/core/config.py
```

Orchestration:

```text
app/application/task_drafting.py
```

AI Gateway не должен импортироваться из:

- `app/domain`;
- `app/availability`;
- `app/scheduling`;
- ORM-моделей.

Scheduler должен получать только уже подтверждённые и нормализованные
`SchedulingTask`.

## 7. Оценка Single Responsibility

| Компонент | SRP | Комментарий |
|---|---|---|
| `domain/tasks.py` | Да | Enums и task invariants |
| `domain/preferences.py` | Частично | Domain values плюс JSON parsing/serialization |
| `domain/timezones.py` | Да | Только IANA validation |
| `availability` | Да | Только free-time calculation |
| `scheduling/types.py` | Да | Scheduler contracts/results |
| `scheduling/scheduler.py` | Да | Одна cohesive scheduling policy |
| `services/tasks.py` | Частично | Use cases связаны с API DTO и ORM |
| `services/preferences.py` | В основном да | Effective preferences и explicit persistence |
| `services/scheduling.py` | Частично | DB loading, mapping и orchestration |
| public scheduling API | Да | Тонкий transport adapter |
| public tasks API | Частично | Route содержит ORM-aware lookup |
| `internal/router.py` | Частично | Много internal capabilities в одном модуле |
| `internal/scenario_loader.py` | Да | Safe scenario discovery/loading |
| `internal/export.py` | Да | Export payload и secret guard |
| product runner | Да | Human-oriented scenario execution |

## 8. Итоговая таблица

| Current | Target | Gap | Recommendation |
|---|---|---|---|
| Pure deterministic scheduler | Pure domain/core scheduler | Существенного gap нет | Сохранить запрет FastAPI/SQLAlchemy/AI imports |
| Pure availability engine | Pure domain service | Существенного gap нет | Сохранить текущую boundary и DST tests |
| Domain enums, value objects, validators | Явная domain model | Нет domain entities для Task/User; serialization находится рядом с domain values | Вводить entities/mappers только когда появятся реальные use cases |
| ORM models используют domain enums | Infrastructure adapters вокруг domain | Application services возвращают/принимают ORM entities | При росте добавить repository ports и application DTO |
| Task service принимает Pydantic schemas | Application use case принимает transport-neutral command | Delivery DTO течёт в service layer | Будущее изменение: `CreateTaskCommand`/`UpdateTaskCommand` |
| Scheduling service содержит DB query, mapping и orchestration | Use case + ports + adapters | Responsibilities объединены | При появлении новых источников отделить loader/repository и mapper |
| Shared `generate_schedule_preview()` для public/internal | Единственная orchestration path | Product runner вызывает core напрямую | Перевести runner на общий normalized-input orchestrator при первом изменении preprocessing |
| Public scheduling route тонкий | Delivery adapter | Gap нет | Сохранить |
| Tasks route импортирует ORM `Task` | Delivery зависит только от use case contracts | Небольшая persistence coupling | Перенести require/get semantics в application service при росте API |
| Internal tools отделены и gated | Отдельный internal delivery adapter | `internal/router.py` перегружен; internal schemas зависят от public schemas | Разделить routers/общие application DTO при расширении tools |
| Product scenarios без БД | Независимая product validation | Возможен drift orchestration | Добавить parity test или использовать shared orchestrator |
| Boundary test для scheduling/availability | Полный набор dependency rules | Другие слои не защищены | Добавить import-boundary tests для domain/application/delivery |
| AI отсутствует | AI через application port и infrastructure adapter | `AI Gateway` ещё не реализован | Добавить port + provider adapter только в соответствующем milestone |
| TaskDraft отсутствует | Неперсистентный domain/application DTO | Draft lifecycle ещё не реализован | Добавить `TaskDraft` до persistence и требовать confirmation |
| Direct SQLAlchemy sessions в services | Repository/unit-of-work ports | Infrastructure привязана к application services | Пока приемлемо; инвертировать при втором storage/use-case channel |

## 9. Заключение

Архитектура соответствует текущему milestone лучше всего в самой критичной
области: scheduling core и availability действительно чистые, deterministic и
infrastructure-independent. Orchestration размещена в application service, а
public/internal delivery layers переиспользуют её.

Основные отклонения не являются ошибками поведения и не требуют немедленного
рефакторинга:

1. services напрямую используют SQLAlchemy/ORM;
2. task service зависит от Pydantic API schemas;
3. internal router агрегирует слишком много обязанностей;
4. product runner повторяет верхнеуровневую orchestration;
5. target architecture не зафиксирована отдельным ADR/document.

Текущую реализацию можно считать архитектурно здоровой для Milestone 2C.
Следующий архитектурный риск возникнет не в scheduler, а при добавлении AI:
`AI Gateway` и `TaskDraft` нужно провести через application ports/use cases, не
нарушая уже существующую чистую границу scheduling core.
