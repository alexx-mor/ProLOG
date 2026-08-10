# Сопоставление Production Inbox

Версия контракта: P10 / core schema 7 / ProLOG 0.6.6.

## 1. Граница ответственности

P10 читает current `ProductionInboxBundle` P9 и создает воспроизводимое
предложение интерпретации. Он не меняет immutable source P8, состав bundle P9,
справочники и состояние Product. Он не создает `ProductionEvent`, Production
Attachment, WorkLog и не использует AI, OCR или содержимое изображений.

Три линии истории независимы:

`source revision -> grouping bundle -> matching run`.

## 2. Canonical text и нормализация

Текст выбирается в порядке: closing text, captioned media, standalone text.
Если текстов несколько, они объединяются в deterministic bundle order внутри
этого приоритета. Старые revisions не читаются.

`source_text` сохраняется без изменения. `normalized_text` используется только
для matching: casefold, `ё/е`, варианты дефиса и `№`, повторные пробелы,
варианты пробелов в `ШУ 1`, `ШУ-1` и `70 %`.

## 3. Product и Object

Приоритет Product:

1. точный заводской номер, score 100;
2. точный код/шифр, score 90;
3. подтвержденный ProductAlias, score 80;
4. имя внутри явно определенного Object, score 70;
5. нормализованное имя без Object, score 60.

Границы токенов обязательны: `1234` не совпадает с `12345`. Равные верхние
кандидаты не выбираются автоматически. Короткие `ШУ1`, `ШУ 1`, `ШУ-1`
эквивалентны для поиска, но при повторе имени на разных объектах требуют review.

Object ищется по коду/номеру, имени и confirmed ObjectAlias. Надежно найденный
Product задает Object методом `derived_from_product`. Явный противоречащий
Object сохраняется как `object_conflict`, а не исправляется молча.

## 4. ProductionStage

ProductionStage не связан с WorkType. Приоритет: точный code, точное name,
подтвержденный `ProductionStageAlias`. Отключенное совпадение сохраняется как
candidate с предупреждением и не считается безопасно выбранным новым этапом.

Консервативный seed включает только очевидные формы для подготовки, слесарных
работ, установки оборудования, электромонтажа, маркировки, программирования,
проверки, ОТК и упаковки. `сборка` не означает автоматически ни один этап.
Слово `готово` также не устанавливает `COMPLETED`.

## 5. Readiness и segmentation

Readiness извлекается из `70%`, `70 %`, `готовность 70%` или `готовность 70`.
Допустимы 0..100. Голое число, включая заводской номер, не является readiness.
Несколько разных процентов и диапазон вроде `70-80%` возвращают ambiguity.

Текст делится только по строкам, `;` и нумерованным пунктам, когда каждый
сегмент содержит собственный однозначный Product. Поэтому `ШУ1 50%; ШУ2 70%`
может дать два proposals, а `ШУ1, ШУ2 70%` остается одним ambiguous proposal.

## 6. Persistence и объяснимость

`ProductionInboxMatchRuns` хранит bundle fingerprint, matcher rule,
directory-context fingerprint, source/normalized text, result fingerprint и
lineage. Только один run является current для bundle.

`ProductionInboxProposals` хранит typed выбранные поля. Отдельные candidate
таблицы сохраняют rank, deterministic score, метод и evidence. Evidence и issues
также типизированы; списки ID не хранятся JSON. Score 0..100 является весом
правила для стабильной сортировки, не вероятностью правильности.

Текущая версия правила: `production-matcher-v1`.

## 7. Повторный запуск и diagnostics

Одинаковые bundle fingerprint, directory context, input, rule и result не
создают новый MatchRun. Изменение справочника/alias создает новый current run,
а прежний остается audit history. Новый current bundle P9 получает собственную
линию интерпретации.

Диагностика выявляет bundle без run, stale fingerprints/context, отсутствующие
Product/Object/Stage, Product/Object conflict, invalid/ambiguous readiness,
повтор rank, inactive selection, неизвестную rule version и broken lineage.
Она ничего не исправляет автоматически.
