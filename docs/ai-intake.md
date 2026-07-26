# AI Intake Engine

## Implemented internal intake contract (TaskDraft v2)

`POST /internal/api/task-drafts/analyze` is internal, stateless, and returns
`task-draft.schema.v2`. It does not persist drafts, create tasks, call application
services, or invoke the scheduler. `TaskDraft` is an application-layer AI
interpretation object, not a persisted domain entity.

The active prompt is configurable through `AI_INTAKE_PROMPT_VERSION`; its
recommended default is `ai-intake.task-draft.v2`. Relative dates are interpreted
using the current datetime and `AI_INTAKE_DEFAULT_TIMEZONE` (an IANA timezone,
`UTC` by default). Returned datetimes retain an explicit timezone offset.

Each task field is a typed DraftValue:

```json
{
  "value": 120,
  "source": "estimated",
  "confidence": 0.65,
  "explanation": "Оценка для подготовки короткой презентации.",
  "requires_confirmation": true
}
```

Sources have precise semantics:

- `user`: explicitly supplied by the user; confidence must be `1.0`;
- `inferred`: normalized language or arithmetic derived from explicit values;
- `estimated`: model judgment based on task type or complexity;
- `default`: a documented fallback policy, used sparingly.

Non-null inferred, estimated, and default values require an explanation. Null
values have null source and confidence. Estimated values normally require
confirmation. `clarification_questions` replaces v1 `assumptions` and
`uncertainties`; questions are emitted only when the answer materially affects
scheduling or interpretation.

`ProposedStepV2` uses DraftValue objects for title, description, and duration,
plus a unique sequential `order` starting at 1. Decomposition is omitted for
trivial single-action tasks. Recurrence is a known limitation: it is preserved
in description or clarification, but is not represented as a first-class field.

### Developer migration from v1

- Flat v1 values are replaced by typed DraftValue objects.
- Read `field.value` instead of the old flat field.
- `duration_minutes` becomes `duration`.
- `assumptions` and `uncertainties` become per-field explanations and
  `clarification_questions`.
- `schema_version` changes to `task-draft.schema.v2`.
- `TaskDraftV1` remains importable temporarily, but the endpoint returns only v2.
- No database migration is required.

Provider output is constrained by strict JSON Schema and then independently
validated by Pydantic/domain rules. Provider failures and structured/domain
validation failures return generic `502` responses while internal logs retain
the diagnostic category. Missing provider configuration returns `503`; disabled
internal tools return `404`.

Статус: architecture/design proposal  
Дата: 2026-07-23  
Scope: проектирование без реализации

## 1. Назначение

AI Intake Engine преобразует неструктурированный пользовательский текст в
валидируемый `TaskDraft`, который пользователь может уточнить, отредактировать и
явно подтвердить.

Полный lifecycle:

```text
User text
  ↓
AI
  ↓
TaskDraft
  ↓
Validation
  ↓
Confirmation
  ↓
Task
  ↓
Scheduler
```

Ключевая граница:

> AI предлагает структуру задачи, но не создаёт `Task`, не запускает scheduler и
> не пишет события в календарь.

Intake считается успешным только тогда, когда application layer получил
структурированный output, независимо провалидировал его и показал пользователю
до persistence.

## 2. Архитектура

```text
┌────────────────────────────── Delivery ──────────────────────────────┐
│ UI / API                                                            │
│ user text · clarification answers · edits · confirmation             │
└────────────────────────────────┬─────────────────────────────────────┘
                                 │
┌────────────────────────── AI Intake application ─────────────────────┐
│ Intake Orchestrator                                                 │
│ Prompt Registry                                                     │
│ Draft Normalizer                                                    │
│ Draft Validator                                                     │
│ Clarification Policy                                                │
│ Confirmation Use Case                                               │
└───────────────┬───────────────────┬─────────────────────┬────────────┘
                │                   │                     │
                │ AI Gateway port   │ Domain              │ Repositories
                ▼                   ▼                     ▼
┌────────────────────────┐  ┌──────────────────┐  ┌───────────────────┐
│ Provider Adapter       │  │ TaskDraft        │  │ Task persistence  │
│ OpenAI / other future  │  │ ProposedStep     │  │ User preferences  │
└────────────────────────┘  └────────┬─────────┘  └───────────────────┘
                                    │ confirmation
                                    ▼
                              Confirmed Task
                                    │
                                    ▼
                          Scheduling orchestration
                                    │
                                    ▼
                        Availability → Scheduler
```

### 2.1 Delivery layer

Принимает:

- исходный пользовательский текст;
- locale и timezone context;
- ответы на clarification questions;
- ручные изменения draft;
- explicit confirm/reject action.

Delivery не строит prompt, не вызывает provider напрямую и не считает AI output
валидным только потому, что он соответствует JSON.

### 2.2 Intake Orchestrator

Application use case координирует lifecycle:

1. принимает intake command;
2. загружает допустимый пользовательский context;
3. выбирает prompt и schema versions;
4. вызывает AI Gateway;
5. проверяет provider envelope;
6. нормализует draft;
7. запускает validation;
8. выбирает `ready_for_confirmation` или `needs_clarification`;
9. возвращает presentation model в UI.

Orchestrator не содержит provider SDK calls и scheduling logic.

### 2.3 Draft Normalizer

Приводит structurally valid output к canonical form:

- trimming и Unicode normalization;
- enum normalization;
- перевод relative dates в абсолютные timezone-aware instants;
- применение безопасных defaults;
- stable identifiers для `ProposedStep`;
- canonical ordering steps;
- отделение отсутствующего значения от явно указанного пользователем;
- сохранение provenance: `user`, `ai_inferred`, `defaulted`,
  `user_corrected`.

Normalizer не исправляет неоднозначные данные молча. Неоднозначность становится
validation issue или clarification question.

### 2.4 Draft Validator

Проверяет структуру, доменные инварианты и готовность к confirmation. Только
application/domain validation является authoritative.

### 2.5 Confirmation Use Case

Получает конкретную версию validated draft и explicit user confirmation:

1. проверяет ownership, TTL и draft version;
2. повторно запускает domain validation;
3. отклоняет stale или изменённый draft;
4. создаёт `Task`;
5. при принятой политике создаёт `TaskStep` из подтверждённых `ProposedStep`;
6. возвращает identifier созданной задачи;
7. только после commit разрешает scheduling preview.

Confirmation не вызывает AI повторно и не изменяет draft скрытым образом.

## 3. TaskDraft lifecycle

Рекомендуемые состояния:

| State | Значение |
|---|---|
| `received` | Текст принят, AI ещё не вызван |
| `extracting` | Выполняется provider request |
| `needs_clarification` | Draft структурирован, но обязательные решения отсутствуют |
| `ready_for_confirmation` | Draft валиден и показан пользователю |
| `confirmed` | Пользователь подтвердил конкретную draft version |
| `rejected` | Пользователь отклонил draft |
| `expired` | Истёк TTL временного draft |
| `task_created` | Persistent `Task` создан |
| `failed` | Intake завершился необрабатываемой ошибкой |

Допустимые переходы:

```text
received
  → extracting
      → needs_clarification
          → extracting
          → ready_for_confirmation
      → ready_for_confirmation
          → confirmed
              → task_created
          → rejected
      → failed

needs_clarification / ready_for_confirmation
  → expired
```

`TaskDraft` может временно храниться для многошагового диалога, но это не делает
его confirmed `Task`. Для MVP достаточно session-scoped storage с TTL. Если
draft persistence появится в PostgreSQL, таблица должна быть отделена от
`tasks`, иметь expiration и не попадать в scheduler queries.

## 4. AI Gateway

AI Gateway — application port, скрывающий конкретного AI provider.

### 4.1 Responsibility

Gateway отвечает за:

- provider-neutral request/response contract;
- выбор provider adapter через dependency injection;
- timeout и cancellation;
- retry policy для retryable failures;
- передачу prompt/schema version metadata;
- structured-output request;
- нормализацию usage, latency и provider request identifier;
- redaction безопасной telemetry.

Gateway не отвечает за:

- domain validation;
- clarification policy;
- создание задач;
- persistence drafts/tasks;
- scheduling;
- UI messages.

### 4.2 Gateway request

Conceptual request содержит:

| Поле | Назначение |
|---|---|
| `request_id` | Correlation и idempotency |
| `user_text` | Исходный текст без prompt concatenation |
| `locale` | Язык интерпретации и ответов |
| `timezone` | IANA timezone пользователя |
| `current_time` | Явная опорная дата для relative expressions |
| `preferences_context` | Только разрешённые scheduling preferences |
| `clarification_context` | Предыдущий draft и ответы пользователя |
| `prompt_version` | Версия instruction template |
| `schema_version` | Версия structured output |

Не следует передавать:

- database credentials;
- environment variables;
- полный user profile без необходимости;
- calendar event descriptions, если для intake достаточно busy metadata;
- secrets других integrations.

### 4.3 Gateway result

Gateway возвращает envelope:

| Поле | Назначение |
|---|---|
| `provider` | Логическое имя adapter |
| `model` | Фактически использованная модель |
| `provider_request_id` | Диагностика у provider |
| `prompt_version` | Использованная версия prompt |
| `schema_version` | Использованная версия schema |
| `raw_structured_output` | Provider-neutral JSON object |
| `usage` | Tokens/cost metadata без product decisions |
| `latency_ms` | Наблюдаемость |
| `finish_reason` | Нормализованная причина завершения |

Raw free-form provider text не должен становиться `TaskDraft` без schema parse.

## 5. Provider interface

Provider interface является infrastructure boundary под AI Gateway.

### 5.1 Минимальные операции

| Операция | Responsibility |
|---|---|
| `generate_structured` | Получить structured output по prompt и JSON Schema |
| `capabilities` | Сообщить поддержку structured output и limits |
| `classify_error` | Нормализовать provider-specific exception |

Health checks и model discovery могут быть добавлены позже, но не должны влиять
на domain contracts.

### 5.2 Provider adapter

Конкретный adapter:

- преобразует gateway request в provider SDK request;
- передаёт system/developer instructions отдельно от user content;
- включает strict structured output, если provider это поддерживает;
- ограничивает output tokens;
- преобразует SDK response в gateway envelope;
- не импортируется из domain, scheduler или availability.

### 5.3 Provider selection

MVP использует один configured provider/model. Gateway должен сохранять
provider-neutral contract, но не требует premature multi-provider routing.

Future selection может учитывать:

- capability structured output;
- locale;
- latency;
- cost;
- availability;
- data residency.

Fallback между providers допустим только если:

- ошибка retryable;
- один logical request сохраняет correlation;
- результат проходит ту же schema/domain validation;
- telemetry показывает, какой provider реально использован.

## 6. Prompt versioning

Prompt — versioned product artifact, а не строка внутри route.

### 6.1 Version tuple

Каждый intake result связывается с:

```text
prompt_version
schema_version
normalizer_version
validator_version
provider
model
```

Минимальная версия prompt:

```text
ai-intake.task-draft.v1
```

Schema имеет независимую версию:

```text
task-draft.schema.v1
```

Prompt update не требует schema update, если contract не изменился. Schema
update не должна маскироваться изменением prompt version.

### 6.2 Prompt registry

Prompt Registry хранит:

- immutable version identifier;
- system instructions;
- schema reference;
- supported locales;
- examples/evaluation references;
- activation status;
- release notes.

После выпуска version content не изменяется. Исправление создаёт новую version.

### 6.3 Prompt composition

Prompt логически разделён:

1. постоянные system instructions;
2. schema и field semantics;
3. trusted application context;
4. untrusted user text;
5. clarification history.

User text всегда рассматривается как данные. Инструкции внутри текста не могут
отключать JSON Schema, менять system policy или запрашивать secrets.

### 6.4 Rollout

Новая prompt version проходит:

- offline examples;
- adversarial/prompt-injection tests;
- schema-validity rate;
- domain-validity rate;
- clarification rate;
- user correction rate;
- confirmation rate;
- latency/cost comparison.

Production rollout должен поддерживать быстрый rollback на предыдущую immutable
version.

## 7. JSON Schema

JSON Schema определяет синтаксический provider contract. Она не заменяет domain
validation.

Иллюстративная schema v1:

```json
{
  "$id": "task-draft.schema.v1",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "title",
    "duration_minutes",
    "priority",
    "preferred_time_of_day",
    "is_splittable",
    "proposed_steps",
    "assumptions",
    "uncertainties"
  ],
  "properties": {
    "title": {
      "type": "string",
      "minLength": 1,
      "maxLength": 255
    },
    "description": {
      "type": ["string", "null"],
      "maxLength": 4000
    },
    "duration_minutes": {
      "type": ["integer", "null"],
      "minimum": 1
    },
    "priority": {
      "enum": ["low", "medium", "high", "urgent", null]
    },
    "earliest_start": {
      "type": ["string", "null"],
      "format": "date-time"
    },
    "deadline": {
      "type": ["string", "null"],
      "format": "date-time"
    },
    "preferred_time_of_day": {
      "enum": ["any", "morning", "afternoon", "evening", null]
    },
    "is_splittable": {
      "type": ["boolean", "null"]
    },
    "minimum_session_minutes": {
      "type": ["integer", "null"],
      "minimum": 1
    },
    "maximum_sessions_per_day": {
      "type": ["integer", "null"],
      "minimum": 1
    },
    "proposed_steps": {
      "type": "array",
      "maxItems": 20,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["title", "duration_minutes"],
        "properties": {
          "title": {
            "type": "string",
            "minLength": 1,
            "maxLength": 255
          },
          "description": {
            "type": ["string", "null"],
            "maxLength": 2000
          },
          "duration_minutes": {
            "type": ["integer", "null"],
            "minimum": 1
          }
        }
      }
    },
    "assumptions": {
      "type": "array",
      "items": {"type": "string"},
      "maxItems": 20
    },
    "uncertainties": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["field", "reason"],
        "properties": {
          "field": {"type": "string"},
          "reason": {"type": "string"}
        }
      },
      "maxItems": 20
    }
  }
}
```

### 7.1 Schema principles

- `additionalProperties: false`;
- bounded string lengths и array sizes;
- explicit nullable fields;
- enums вместо свободных строк;
- timezone-aware ISO 8601 datetimes;
- schema version вне user-controlled data;
- никакого provider-specific metadata внутри domain payload.

### 7.2 Missing versus inferred

Если AI не может надёжно определить значение, поле должно быть `null`, а причина
должна попасть в `uncertainties`. AI не должен придумывать deadline, duration или
priority без явной маркировки assumption.

## 8. Validation

Validation выполняется слоями.

### 8.1 Transport/provider validation

Проверяет:

- response получен полностью;
- finish reason допустим;
- structured output является JSON object;
- размер output в пределах limit;
- schema/prompt versions совпадают с request;
- provider envelope не повреждён.

### 8.2 JSON Schema validation

Проверяет:

- required fields;
- types;
- enums;
- formats;
- length/range limits;
- отсутствие неизвестных полей.

Schema failure означает invalid provider output, а не ошибку пользователя.

### 8.3 Normalization validation

Проверяет:

- корректность locale-sensitive dates;
- преобразование relative time относительно explicit `current_time`;
- IANA timezone;
- canonical UTC instants;
- enum normalization;
- stable step ordering.

### 8.4 Domain validation

Переиспользует authoritative task invariants:

- duration положительна;
- datetimes timezone-aware;
- `earliest_start < deadline`;
- minimum session положительна;
- maximum sessions/day положительно;
- minimum session не превышает splittable task duration.

Дополнительно для draft:

- title содержателен;
- сумма step durations согласуется с task duration или отмечена как assumption;
- deadline не находится в явно невозможном прошлом;
- нет дублирующихся proposed steps;
- обязательные confirmation fields определены.

### 8.5 Context/policy validation

Проверяет:

- принадлежность user context;
- planning horizon;
- разрешённые locale/timezone;
- максимальную task duration;
- limits clarification loop;
- отсутствие secrets или environment data;
- prompt-injection indicators для telemetry, не для слепого доверия.

### 8.6 Validation result

Validator возвращает не boolean, а structured result:

- normalized draft;
- blocking errors;
- clarification issues;
- non-blocking warnings;
- assumptions;
- provenance по полям;
- readiness:
  - `needs_clarification`;
  - `ready_for_confirmation`;
  - `rejected`.

## 9. Error handling

### 9.1 Error taxonomy

| Категория | Пример | Retry | Пользовательский результат |
|---|---|---:|---|
| `invalid_user_input` | Пустой или слишком длинный текст | Нет | Попросить исправить input |
| `provider_timeout` | Timeout | Да, bounded | Сообщить о временной проблеме |
| `provider_rate_limited` | 429 | Да, с backoff | Предложить повторить позже |
| `provider_unavailable` | 5xx/network | Да, bounded/fallback | Временная ошибка |
| `provider_auth/config` | Неверный API key/model | Нет | Internal error, alert |
| `invalid_provider_output` | JSON/schema failure | Один repair/retry | Не показывать raw output как draft |
| `domain_validation_failed` | Невозможные constraints | Не автоматически | Clarification или user edit |
| `clarification_limit` | Слишком много циклов | Нет | Ручное заполнение |
| `stale_draft` | Confirm старой версии | Нет | Перезагрузить актуальный draft |
| `task_persistence_conflict` | Duplicate/idempotency conflict | Controlled | Показать существующий result |

### 9.2 Retry policy

- только retryable technical failures;
- ограниченное число попыток;
- exponential backoff и jitter;
- общий deadline на intake request;
- cancellation при уходе клиента, если provider позволяет;
- один logical `request_id` на все attempts;
- повторный вызов не создаёт `Task`.

### 9.3 Invalid structured output

Допустим максимум один controlled repair/retry с теми же schema semantics.
Application не должна:

- парсить prose регулярными выражениями как fallback;
- принимать частично валидный JSON молча;
- заменять критические поля случайными defaults;
- создавать task из raw provider text.

После неудачи пользователь получает безопасное сообщение и возможность ручного
ввода.

### 9.4 Observability

Логируются:

- correlation/request identifier;
- versions;
- provider/model;
- latency и usage;
- error category;
- schema/domain validity;
- clarification count;
- confirmation/rejection result.

Не логируются без отдельной privacy policy:

- полный user text;
- полный draft;
- secrets;
- calendar descriptions;
- provider credentials.

## 10. Clarification flow

Clarification запускается, когда draft structurally valid, но недостаточно
определён для безопасной confirmation.

### 10.1 Когда требуется clarification

Примеры:

- неизвестна duration;
- deadline неоднозначен;
- relative date нельзя однозначно разрешить;
- splittable policy противоречит minimum session;
- proposed step durations не согласованы;
- user request содержит взаимоисключающие требования;
- значение критического поля только выдумано AI и требует подтверждения.

### 10.2 Формирование вопроса

Clarification Policy получает structured validation issues и выбирает:

- минимальное число вопросов;
- только blocking issues;
- один вопрос на одно решение или компактную связанную группу;
- варианты ответа, когда они действительно взаимоисключающие;
- понятный язык без provider terminology.

Предпочтительно строить вопросы application templates из issue codes. AI может
помогать с естественной формулировкой, но не должен менять semantics вопроса.

### 10.3 Merge ответа

Ответ пользователя:

1. сохраняется как authoritative user provenance;
2. объединяется с предыдущей draft version;
3. при необходимости отправляется через AI Gateway для повторной extraction;
4. проходит полный normalization и validation заново;
5. создаёт новую immutable draft version.

AI не может перезаписать уже подтверждённое пользователем поле без явного
conflict issue.

### 10.4 Ограничения

- ограниченное число clarification rounds;
- TTL draft session;
- возможность перейти к ручному редактированию;
- возможность отменить intake;
- отсутствие task persistence;
- отсутствие scheduling до confirmation.

## 11. Confirmation flow

Confirmation — обязательная граница между AI suggestion и persistent domain.

### 11.1 UI перед confirmation

Пользователь видит:

- title и description;
- duration;
- priority;
- earliest start и deadline в своей timezone;
- preferred time;
- splitting/session constraints;
- proposed steps;
- assumptions;
- warnings и исправленные поля;
- marker каждого inferred/defaulted значения.

### 11.2 Confirm command

Команда подтверждения содержит:

- draft identifier;
- exact draft version или content hash;
- final user-edited values;
- idempotency key;
- user identifier.

Она не содержит provider credentials или raw prompt.

### 11.3 Server-side confirmation

Application:

1. проверяет ownership и draft state;
2. убеждается, что version не устарела;
3. повторно валидирует final values;
4. создаёт `Task`;
5. преобразует accepted `ProposedStep` в `TaskStep`, если эта capability
   реализована;
6. отмечает draft confirmed;
7. возвращает created task;
8. предлагает отдельный scheduling preview.

При повторе с тем же idempotency key возвращается тот же task result.

### 11.4 Reject/edit

- Reject переводит draft в `rejected` и ничего не создаёт.
- Edit создаёт новую draft version и требует нового confirmation.
- Expired draft нельзя подтвердить без восстановления/повторной validation.

## 12. Scheduling handoff

После confirmation scheduler получает только confirmed persistent workload:

```text
Task / TaskStep
UserPreferences
Planning window
Busy intervals
  ↓
Planning orchestration
  ↓
Availability
  ↓
Scheduler
  ↓
Scheduling Preview
```

AI Intake не передаёт prompt, confidence или provider output в scheduler.
Scheduler использует только нормализованные scheduling fields.

Scheduling preview:

- не создаёт calendar events;
- не изменяет task;
- остаётся deterministic для одинаковых inputs;
- возвращает explanations, warnings и unscheduled tasks.

## 13. Sequence diagram

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant UI
    participant Intake as AI Intake Orchestrator
    participant Registry as Prompt Registry
    participant Gateway as AI Gateway
    participant Provider as AI Provider
    participant Validator as Draft Validator
    participant Confirm as Confirmation Use Case
    participant Tasks as Task Repository
    participant Planning as Planning Orchestrator
    participant Availability
    participant Scheduler

    User->>UI: Natural-language task request
    UI->>Intake: Start intake(text, locale, timezone)
    Intake->>Registry: Resolve prompt + schema versions
    Registry-->>Intake: Immutable prompt configuration
    Intake->>Gateway: Generate structured draft
    Gateway->>Provider: Structured-output request
    Provider-->>Gateway: Provider response
    Gateway-->>Intake: Normalized provider envelope
    Intake->>Validator: Normalize and validate TaskDraft
    Validator-->>Intake: Draft + issues + readiness

    alt Clarification required
        Intake-->>UI: TaskDraft + clarification questions
        UI-->>User: Show draft and questions
        User->>UI: Clarification answers
        UI->>Intake: Continue intake(draft version, answers)
        Intake->>Gateway: Regenerate/complete structured draft
        Gateway->>Provider: Structured-output request
        Provider-->>Gateway: Provider response
        Gateway-->>Intake: Normalized provider envelope
        Intake->>Validator: Revalidate new draft version
        Validator-->>Intake: Ready or remaining issues
    end

    Intake-->>UI: Validated TaskDraft
    UI-->>User: Show fields, assumptions and proposed steps

    alt User edits
        User->>UI: Edit draft
        UI->>Intake: Validate edited draft
        Intake->>Validator: Validate user-authored values
        Validator-->>Intake: New validated draft version
        Intake-->>UI: Updated TaskDraft
    end

    alt User confirms
        User->>UI: Explicit confirmation
        UI->>Confirm: Confirm(draft id, version, idempotency key)
        Confirm->>Validator: Final authoritative validation
        Validator-->>Confirm: Valid
        Confirm->>Tasks: Create Task and accepted TaskSteps
        Tasks-->>Confirm: Persistent Task
        Confirm-->>UI: Task created

        UI->>Planning: Request scheduling preview
        Planning->>Tasks: Load confirmed pending workload
        Tasks-->>Planning: Tasks + preferences
        Planning->>Availability: Compute free intervals
        Availability-->>Planning: Free intervals
        Planning->>Scheduler: Schedule normalized tasks
        Scheduler-->>Planning: Deterministic result
        Planning-->>UI: Scheduling Preview
        UI-->>User: Show schedule and explanations
    else User rejects
        User->>UI: Reject draft
        UI->>Intake: Reject(draft id, version)
        Intake-->>UI: Rejected; no Task created
    end
```

## 14. Security and privacy boundaries

- User text является untrusted input.
- Prompt injection не может изменить system policy или schema.
- Provider получает минимально необходимый context.
- Secrets и environment configuration никогда не включаются в prompt.
- Confirmation выполняется server-side.
- Ownership проверяется на каждом draft action.
- Draft имеет TTL и version.
- Provider output не исполняется как код.
- HTML/Markdown из AI output экранируется UI.
- Raw prompts/responses имеют отдельную retention policy.
- Scheduler, database credentials и calendar writes недоступны AI provider.

## 15. MVP boundaries

Для первого AI Intake milestone достаточно:

- один provider adapter;
- один configured model;
- versioned prompt registry;
- strict JSON Schema v1;
- `TaskDraft` и optional `ProposedStep`;
- normalization и domain validation;
- один или несколько bounded clarification rounds;
- ручное редактирование;
- explicit confirmation;
- idempotent Task creation;
- запуск существующего stateless scheduling preview после confirmation;
- telemetry без raw sensitive content.

Не входят в первый AI Intake milestone:

- multi-provider routing;
- autonomous task creation;
- calendar writes;
- background agents;
- automatic rescheduling;
- long-term conversation memory;
- model fine-tuning;
- learning from user behavior;
- automatic acceptance of proposed steps.

## 16. Architectural invariants

1. `TaskDraft` не равен `Task`.
2. `ProposedStep` не равен `TaskStep`.
3. AI output никогда не является authoritative без validation.
4. Validation не делегируется AI provider.
5. Confirmation всегда explicit и version-specific.
6. Retry AI request не создаёт persistent records.
7. Scheduler не зависит от AI Gateway.
8. Availability не зависит от AI или calendar SDK.
9. AI Gateway не содержит domain decisions.
10. Provider adapter не импортируется из domain/scheduling packages.
11. Scheduling preview следует только после создания confirmed `Task`.
12. Calendar write, если появится, требует отдельного explicit action.
