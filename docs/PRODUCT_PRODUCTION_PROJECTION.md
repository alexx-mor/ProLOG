# Производственная проекция изделия

Статус: реализовано на этапе P5, ProLOG 0.5.12, core schema v4.

## Источник истины

Текущее состояние и timeline вычисляются из `ProductionEvent`. Отдельных
таблиц текущего состояния, timeline или аналитического кэша нет.
`Products.readiness_percent` является только rebuildable compatibility
snapshot, а `Products.product_status` на P5 не изменяется.

Effective event — событие со статусом `confirmed`. `superseded` сохраняется в
исторической timeline, но не участвует в текущем состоянии. `draft`, `ready` и
`rejected` доступны только в audit view.

## Хронология и поля

События сортируются стабильно:

1. `observed_at_utc`;
2. `recorded_at_utc`;
3. `id`.

Поэтому поздно зарегистрированное backdated-событие занимает фактическое место
в истории и не становится текущим только из-за времени ввода или подтверждения.

Проекция вычисляется отдельно по каждому полю:

- этап — последнее effective событие с non-NULL `stage_id`;
- готовность — последнее effective событие с non-NULL `readiness_percent`;
- последнее наблюдение — последнее effective событие независимо от полей.

NULL означает «значение не сообщалось» и не стирает ранее известное значение.
`rework` может уменьшать готовность. Superseded-события полностью исключаются
из расчета, а correction участвует в своей фактической `observed_at_utc`.

Отключенный ProductionStage остается видимым в истории и может быть текущим
этапом старого изделия. Активность влияет только на предложение этапа для новых
операций.

## Legacy fallback и reconciliation

Пока effective-событий с процентом нет, проекция возвращает значение Product с
`readiness_source=legacy_snapshot`. После первого такого события источником
становится только история с `readiness_source=production_event`.

После confirmation ProductionService пересчитывает проекцию и обновляет только
`Products.readiness_percent`. Сбой snapshot-sync не отменяет confirmed факт:
он записывается в журнал, а расхождение обнаруживает диагностика. Явный
`reconcile_product_snapshot` восстанавливает только compatibility snapshot и
никогда не изменяет ProductionEvent. Startup не выполняет массовую сверку и не
создает baseline.

`Products.product_status` не синхронизируется с этапом или процентом: для этого
нет утвержденного business mapping, включая правило «100% = Готово».

## Timeline

Обычная timeline включает effective confirmed и superseded history. Item
содержит ProductionEvent, текущую запись ProductionStage, снимки Actor,
reported Employee, упорядоченные Attachment metadata, explicit/manual WorkLog
relations, признак effective и ссылку на superseding correction. Физические
байты вложений не читаются и не проверяются обычным запросом.

## Трудозатраты между наблюдениями

Интервалы строятся между соседними effective observations. Источник —
`WorkLogEntry` с тем же `product_id`; результат в БД не записывается и не
создает `ProductionEventWorkLogs`.

У WorkLog есть только календарная дата. Для наблюдений в разные дни применяется
детерминированная граница `(previous_date, current_date]`: предыдущая дата не
входит, текущая входит. Если два события находятся в один день, часы не
распределяются искусственно: интервал помечается
`day_granularity_ambiguous=true`, а его агрегаты остаются нулевыми.

## Диагностика

Структурированная проверка обнаруживает отсутствующий Product/Stage,
несогласованную correction/superseded-цепочку и расхождение readiness snapshot.
Отключенный этап возвращается как информационное состояние, а не потеря истории.
Диагностика ничего не исправляет автоматически.

Новое ADR не создавалось: rebuildable snapshot и projection напрямую следуют
из event-centric решения ADR-001 и правил correction ADR-004.
