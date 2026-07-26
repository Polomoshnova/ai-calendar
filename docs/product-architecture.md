# Product Architecture

Статус документа: product architecture baseline  
Дата: 2026-07-23

## 1. Product vision

AI Calendar превращает пользовательское намерение, сформулированное обычным
языком, в понятный, проверяемый и выполнимый план работы.

Продукт должен помогать пользователю пройти путь:

```text
намерение
  → структурированный черновик
  → подтверждённая задача
  → реалистичный календарный план
```

Основная ценность продукта — не просто найти свободное время, а предложить
логичное расписание, которое:

- учитывает приоритеты, сроки и рабочие часы;
- уважает существующую занятость;
- объясняет принятые решения;
- не скрывает задачи, которые невозможно запланировать;
- остаётся детерминированным и проверяемым после подтверждения входных данных;
- не создаёт задачи или календарные события без явного согласия пользователя.

AI используется только для интерпретации неструктурированного намерения и
подготовки предложения. Availability и scheduling остаются обычными
детерминированными компонентами, не зависящими от AI-провайдера.

## 2. Current architecture

Текущая реализация соответствует product foundation после Milestone 2C.

```text
Public FastAPI API                 Internal Scheduling Lab
        │                                   │
        ├──────────── application services ─┤
        │                                   │
        ├── Task CRUD                       ├── scenario loader
        ├── Preferences                     ├── review export
        └── Schedule Preview                └── validation UI
                         │
                         ▼
             Scheduling orchestration
             app/services/scheduling.py
                    │              │
                    ▼              ▼
              Availability     Scheduler
                    │              │
                    └──── pure core ┘
                         │
                 PostgreSQL / SQLAlchemy
```

### Реализовано сейчас

- FastAPI application и public API;
- PostgreSQL persistence;
- `User`, `Task`, `UserPreferences`;
- CRUD задач;
- загрузка effective user preferences;
- timezone-aware availability calculation;
- deterministic scheduler;
- stateless scheduling preview;
- structured reason codes, score components, warnings и unscheduled reasons;
- internal scheduling lab;
- product scenarios и human-review export;
- unit, scenario и integration tests.

### Не реализовано сейчас

- AI Intake;
- AI Gateway;
- `TaskDraft`;
- `ProposedStep`;
- persistent `TaskStep`;
- внешние calendar providers;
- чтение или запись событий календаря;
- authentication;
- background jobs;
- persisted schedule plans;
- автоматическое применение preview.

### Текущий основной поток

```text
Persistent Task + UserPreferences + temporary busy intervals
  → application orchestration
  → Availability
  → Scheduler
  → stateless Scheduling Preview
```

Scheduler и Availability являются чистыми компонентами. Они не зависят от
FastAPI, SQLAlchemy, ORM-моделей, AI SDK или calendar providers.

## 3. Target architecture

Целевая архитектура разделяет delivery, application, domain и infrastructure.

```text
┌──────────────────────────────── Delivery ────────────────────────────────┐
│ Web / Mobile UI · Public API · Internal tools                           │
└───────────────────────────────────┬──────────────────────────────────────┘
                                    │
┌────────────────────────────── Application ───────────────────────────────┐
│ AI Intake use case                                                      │
│ Task confirmation use case                                              │
│ Task planning orchestration                                             │
│ Scheduling preview use case                                             │
│ Ports: AI Gateway, Task Repository, Preferences Repository, Calendar    │
└───────────────┬───────────────────┬───────────────────────┬──────────────┘
                │                   │                       │
┌──────────── Domain ───────────┐   │   ┌──────── Pure planning core ─────┐
│ TaskDraft                    │   │   │ Availability                    │
│ ProposedStep                 │   │   │ Scheduler                       │
│ Task                         │   │   │ Scheduling policies/results     │
│ TaskStep                     │   │   └─────────────────────────────────┘
│ UserPreferences              │   │
└───────────────────────────────┘   │
                                    │
┌────────────────────────── Infrastructure adapters ───────────────────────┐
│ PostgreSQL / SQLAlchemy · AI provider · Calendar provider · telemetry    │
└──────────────────────────────────────────────────────────────────────────┘
```

### Правила зависимостей

1. Domain не зависит от FastAPI, SQLAlchemy, AI SDK или calendar SDK.
2. Scheduler не вызывает AI и не читает базу данных.
3. Availability не обращается к calendar provider напрямую. Он получает уже
   нормализованные busy intervals.
4. AI Gateway возвращает структурированный draft, но не создаёт persistent
   tasks.
5. Только confirmation use case преобразует draft в persistent task.
6. Calendar adapter не принимает продуктовые решения. Он только читает или
   записывает события по команде application layer.
7. UI не содержит scheduling rules и не интерпретирует AI output как
   подтверждённую задачу.

### Предлагаемое размещение будущих компонентов

```text
app/
├── application/
│   ├── ai_intake.py
│   ├── task_confirmation.py
│   ├── planning.py
│   ├── scheduling_preview.py
│   └── ports/
│       ├── ai_gateway.py
│       ├── calendar_gateway.py
│       └── repositories.py
├── domain/
│   ├── task_drafts.py
│   ├── tasks.py
│   └── preferences.py
└── integrations/
    ├── ai/
    └── calendar/
```

Это целевое направление, а не требование немедленно перестроить текущий
репозиторий.

## 4. Domain objects

### 4.1 TaskDraft

`TaskDraft` — неперсистентный структурированный черновик, полученный из
пользовательского текста.

Пример содержимого:

- исходный текст пользователя;
- предложенный title;
- предполагаемая общая duration;
- priority;
- earliest start;
- deadline;
- preferred time of day;
- возможность splitting;
- список `ProposedStep`;
- assumptions и validation warnings;
- confidence/provenance для объяснения предложения.

Обязанности:

- представить интерпретацию намерения до подтверждения;
- сохранить неопределённости и assumptions;
- позволить пользователю исправить результат;
- служить входом confirmation use case.

`TaskDraft` не является `Task`, не должен автоматически сохраняться как задача и
не должен передаваться scheduler без подтверждения.

Текущий статус: отсутствует.

### 4.2 ProposedStep

`ProposedStep` — предложенный AI шаг внутри `TaskDraft`.

Пример содержимого:

- временный identifier;
- title;
- description;
- estimated duration;
- порядок или зависимости;
- признак обязательности;
- assumptions.

Обязанности:

- показать предлагаемую декомпозицию задачи;
- дать пользователю возможность удалить, изменить или объединить шаги;
- после подтверждения стать основой для `TaskStep`.

`ProposedStep` не является persistent entity и не участвует в расписании как
самостоятельная подтверждённая работа.

Текущий статус: отсутствует.

### 4.3 Task

`Task` — подтверждённая persistent задача пользователя.

Текущие поля:

- identifier и user identifier;
- title и optional description;
- duration;
- priority и status;
- earliest start и deadline;
- preferred time of day;
- splitting policy;
- minimum session;
- maximum sessions per day;
- timestamps.

Обязанности:

- быть authoritative workload после confirmation;
- хранить ограничения, необходимые scheduler;
- поддерживать lifecycle `pending`, `completed`, `cancelled`.

Текущий статус: реализован как SQLAlchemy model плюс Pydantic schemas и domain
validation. Отдельной transport-neutral domain entity пока нет.

### 4.4 TaskStep

`TaskStep` — подтверждённый persistent шаг задачи.

Предполагаемые поля:

- identifier;
- parent task identifier;
- title и optional description;
- duration;
- ordering/dependency information;
- status;
- scheduling policy или наследование ограничений parent task.

Обязанности:

- сохранить подтверждённую декомпозицию;
- позволить отдельно отслеживать выполнение;
- при выбранной продуктовой политике стать единицей планирования.

До реализации нужно принять отдельное продуктовое решение:

- scheduler планирует только целые `Task`;
- scheduler планирует `TaskStep`;
- scheduler поддерживает оба уровня, но предотвращает двойной учёт duration.

Текущий статус: отсутствует.

### 4.5 UserPreferences

`UserPreferences` — persistent настройки пользователя, влияющие на availability
и scheduling.

Текущие поля:

- timezone хранится на `User` и является единственным timezone authority;
- working hours для семи дней;
- preferred task time;
- minimum break;
- no deep work after;
- default minimum session.

Обязанности:

- определить личные scheduling constraints и defaults;
- преобразовать local wall-clock preferences в timezone-aware planning inputs;
- не содержать provider-specific calendar configuration.

Текущий статус: реализован. При отсутствии persistent preferences application
использует defaults без автоматического создания записи.

### Связи объектов

```text
Natural-language request
        │
        ▼
TaskDraft 1 ─────── * ProposedStep
        │ confirmation
        ▼
Task 1 ──────────── * TaskStep
        │
        ├── UserPreferences
        ├── normalized busy intervals
        ▼
Scheduling Preview
```

Draft objects и confirmed objects намеренно разделены. Confirmation является
явной архитектурной и продуктовой границей.

## 5. Основной пользовательский сценарий

```text
Natural language
  → TaskDraft
  → Confirmation
  → Task
  → Scheduling Preview
```

### Шаг 1. Natural language

Пользователь описывает цель:

> Подготовь презентацию к пятнице, примерно на три часа, лучше утром.

UI отправляет исходный текст в AI Intake use case. Текст ещё не является задачей.

### Шаг 2. TaskDraft

AI Intake вызывает AI Gateway и получает структурированное предложение:

- title;
- deadline;
- estimated duration;
- priority и time preference;
- optional proposed steps;
- assumptions и неуверенные поля.

Application layer валидирует структуру и возвращает `TaskDraft` в UI.

### Шаг 3. Confirmation

Пользователь:

- проверяет title, duration и deadline;
- исправляет неверные assumptions;
- подтверждает или отклоняет proposed steps;
- явно нажимает создание задачи.

Без confirmation persistent `Task` не создаётся.

### Шаг 4. Task

Confirmation use case:

- повторно валидирует данные;
- создаёт `Task`;
- при необходимости создаёт подтверждённые `TaskStep`;
- не создаёт календарные события.

### Шаг 5. Scheduling Preview

Planning orchestration:

1. загружает pending tasks;
2. загружает effective preferences;
3. получает busy intervals;
4. Availability вычисляет free intervals;
5. Scheduler создаёт deterministic preview;
6. UI показывает schedule, explanations, warnings и unscheduled tasks.

Preview остаётся предложением. Его применение к внешнему календарю требует
отдельного explicit user action и не входит в текущую реализацию.

## 6. Responsibility компонентов

### 6.1 AI Intake

Ответственность:

- принять natural-language input;
- вызвать AI Gateway через application port;
- нормализовать provider response;
- сформировать `TaskDraft` и `ProposedStep`;
- вернуть assumptions и validation problems;
- никогда не создавать `Task` без confirmation.

Не отвечает за:

- поиск свободного времени;
- scheduling;
- работу с календарём;
- persistence confirmed tasks;
- выбор конкретного времени выполнения.

Текущий статус: не реализован.

### 6.2 Planning

Planning — application orchestration между confirmed workload, availability и
scheduler.

Ответственность:

- загрузить задачи и effective preferences;
- определить planning window;
- получить нормализованную занятость;
- подготовить scheduler inputs;
- вызвать Availability;
- вызвать Scheduler;
- вернуть preview и explanations.

Не отвечает за:

- parsing natural language;
- provider-specific API calls;
- UI rendering;
- скрытую запись preview в календарь.

Текущий статус: частично реализован в `app/services/scheduling.py`.

### 6.3 Availability

Ответственность:

- преобразовать working hours в абсолютные интервалы;
- корректно учитывать IANA timezone и DST;
- нормализовать и объединять busy intervals;
- вычесть занятость;
- вернуть free intervals.

Не отвечает за:

- приоритеты задач;
- AI interpretation;
- persistence;
- календарные API.

Текущий статус: реализован как pure component в `app/availability`.

### 6.4 Calendar

Ответственность target-компонента:

- через application port получать занятые интервалы;
- нормализовать provider events в общий формат;
- при явном подтверждении создавать, обновлять или удалять calendar events;
- обеспечивать idempotency и correlation с внутренними сущностями.

Не отвечает за:

- scheduling decisions;
- task decomposition;
- AI prompts;
- автоматическое принятие preview.

В MVP до внешней интеграции роль Calendar выполняет ручной ввод temporary busy
intervals. Текущий статус внешнего Calendar component: не реализован.

### 6.5 UI

Ответственность:

- принять natural-language input;
- показать и дать отредактировать draft;
- получить explicit confirmation;
- управлять подтверждёнными tasks/preferences;
- показать planning window, busy intervals и schedule preview;
- визуализировать explanations, warnings и unscheduled tasks;
- отделять preview от applied schedule.

Не отвечает за:

- domain validation как единственный источник истины;
- AI provider integration;
- availability calculation;
- scheduling heuristics.

Текущий статус: production UI отсутствует; реализован internal scheduling lab
для product validation.

## 7. Что входит в MVP

Здесь **MVP** означает первый пользовательский продукт, а не только текущий
технический milestone.

### Intake и confirmation

- ввод одного natural-language запроса;
- создание одного `TaskDraft`;
- optional `ProposedStep`;
- отображение assumptions и validation errors;
- редактирование draft;
- explicit confirmation;
- создание persistent `Task`;
- отсутствие автоматического persistence до confirmation.

### Task management

- создание, редактирование, удаление;
- cancel и complete;
- priority, duration, earliest start, deadline;
- splitting и session constraints;
- базовая поддержка confirmed `TaskStep`, только если до MVP определена единица
  планирования.

### Planning

- user timezone и working hours;
- minimum breaks и time-of-day preferences;
- editable planning window;
- temporary busy intervals;
- deterministic scheduling preview;
- day-by-day local-time visualization;
- reason codes, score components, warnings и unscheduled tasks;
- отсутствие silent task dropping.

### Safety и trust

- пользователь видит draft до создания task;
- пользователь видит preview до calendar changes;
- AI output валидируется;
- scheduler не зависит от AI;
- preview не создаёт records;
- объяснения доступны для review.

### MVP Calendar boundary

Минимально достаточный вариант:

- ручные busy intervals;
- без внешней calendar integration;
- без calendar writes.

Если чтение календаря будет включено до выпуска MVP, оно должно быть
read-only и реализовано через Calendar port. Calendar writes остаются отдельным
этапом после подтверждения product safety.

## 8. Future scope

### AI

- несколько AI providers;
- provider fallback;
- prompt/model versioning;
- evaluation datasets;
- multilingual intake;
- уточняющий диалог;
- confidence calibration;
- более сложная task decomposition;
- извлечение зависимостей между задачами.

### Planning и scheduler

- planning нескольких недель;
- dependencies между `TaskStep`;
- recurring tasks;
- energy/focus profiles;
- location/context constraints;
- travel time;
- автоматическое перепланирование;
- partially accepted plans;
- persisted plan versions;
- comparison нескольких scheduling strategies;
- user feedback learning.

### Calendar

- Google Calendar и другие providers;
- OAuth и безопасное хранение credentials;
- read-only busy sync;
- explicit calendar writes;
- update/delete synchronization;
- conflict detection;
- idempotency;
- reconciliation после внешних изменений;
- multiple calendars.

### Platform

- authentication и authorization;
- multi-user isolation;
- production UI;
- background workers;
- retries и rate limiting;
- audit log;
- observability;
- provider cost controls;
- privacy retention policies;
- export/delete user data;
- notifications.

### Collaboration

- shared tasks;
- team availability;
- delegated tasks;
- approval workflows;
- role-based access.

## 9. Scope boundaries

Следующие решения должны сохраняться независимо от milestone:

- AI предлагает, пользователь подтверждает;
- `TaskDraft` не равен `Task`;
- `ProposedStep` не равен `TaskStep`;
- scheduler работает только с нормализованными inputs;
- Availability получает busy intervals, а не calendar client;
- preview не равен calendar write;
- provider adapters не содержат продуктовые scheduling rules;
- UI не дублирует domain или scheduling logic.

Эти границы позволяют развивать AI, Calendar и UI независимо, не нарушая уже
реализованный deterministic planning core.
