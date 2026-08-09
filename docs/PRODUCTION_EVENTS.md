# ProductionEvent persistence и lifecycle

Статус: реализовано на этапе P4, ProLOG 0.5.11, core schema v4.

## Границы

`ProductionEvent` — типизированный факт наблюдения или изменения состояния
изделия. Он не заменяет `WorkLogEntry`, не обновляет автоматически legacy-поля
`Products.product_status`/`Products.readiness_percent` и не зависит от UI,
WorkBot, MAX, парсера или файловой системы.

Цепочка P4:

```text
ProductionEvent -> ProductionService -> ProductionEventRepository -> SQLite
```

## Lifecycle

Разрешены только переходы:

```text
draft -> ready -> confirmed
draft -> rejected
ready -> rejected
confirmed -> superseded  (только при подтверждении correction)
```

`confirmed` и `superseded` нельзя вернуть в черновик, а `rejected` нельзя
активировать или подтвердить. Подтвержденные бизнес-поля защищены SQLite-
триггером от inplace-update. Физическое удаление confirmed/superseded запрещено.

Вложения и явные WorkLog-связи можно менять только у `draft`/`ready` через
`ProductionService`. Удаление legacy `WorkLogEntry` удаляет только строку связи
`ProductionEventWorkLogs`, сохраняя ProductionEvent и Attachment.

## Actor persistence

Actor и Employee остаются разными идентичностями. Для создания, подтверждения,
отклонения и создания WorkLog-связи сохраняется lossless snapshot полей
`ActorRef`:

```text
*_actor_type
*_actor_uid
*_actor_local_user_id
*_actor_display_name_snapshot
```

Префиксы события: `created_`, `confirmed_`, `rejected_`. Связь WorkLog хранит
`created_`. Actor UID не ссылается на Employee и не зависит от `auth.json`.

## Confirmation и object snapshot

Перед подтверждением сервис проверяет событие, Product, ProductionStage,
Attachment metadata и reported Employee. Затем текущий `Product.object_id`
записывается в `object_id_snapshot`. Последующее перемещение изделия не меняет
исторический снимок.

Confirmation записывает UTC-время и отдельного Actor. Операция correction
дополнительно меняет исходный факт на `superseded` в той же SQLite-транзакции.

## Correction

Исправление не обновляет подтвержденный факт:

1. создается новый draft с `event_type=correction` и `supersedes_event_id`;
2. исходный факт остается `confirmed`;
3. correction переводится в `ready`;
4. при confirmation новая запись становится `confirmed`, исходная —
   `superseded` одной транзакцией.

Можно исправлять последнюю confirmed correction, формируя воспроизводимую
цепочку. Нельзя исправлять rejected/superseded, ссылаться на себя, создавать
цикл или подтверждать вторую correction одного уже замененного события.

## Readiness и rework

Readiness допускает `NULL` и целые 0..100. Процент не является монотонным
счетчиком:

- исправление ошибочной оценки выполняется `correction`;
- реальный возврат назад фиксируется `rework`;
- меньшее значение нового `observation` требует непустой сохраняемой
  `change_reason`;
- равное или большее значение допускается без причины.

Ни одно правило P4 не изменяет текущий процент в таблице `Products`.

## Идемпотентность

Непустой `idempotency_key` уникален частичным индексом. Повтор с тем же
каноническим payload возвращает существующий event, даже если он уже
подтвержден. Тот же ключ с другим payload вызывает явный conflict.

`source_ref` намеренно не уникален: один источник в будущей интеграции может
иметь несколько редакций или типизированных фактов.

## FK и cascade policy

- `stage_id -> ProductionStages`: `RESTRICT`;
- `supersedes_event_id -> ProductionEvents`: `RESTRICT`;
- event-attachment: event `CASCADE`, Attachment `RESTRICT`;
- event-worklog: event `CASCADE`, WorkLog `CASCADE`.

Cascade относится только к строкам связи. Он никогда не удаляет Attachment,
физический файл, WorkLog или ProductionEvent как следствие удаления другой
первичной сущности. Публичного delete confirmed event нет.

Product, Object snapshot и Employee находятся в component DB, поэтому их
ссылки валидирует ProductionService и общая cross-database diagnostics.

## Диагностика

Штатная проверка дополнительно обнаруживает отсутствующие:

- Product производственного события;
- Object исторического snapshot;
- reported Employee;
- ProductionStage;
- исходный superseded event;
- Attachment или ProductionEvent в таблице связей;
- WorkLogEntry или ProductionEvent в таблице связей.

Диагностика ничего не исправляет автоматически.
