# Production Inbox Review

Версия контракта: P11 / core schema 8 / ProLOG 0.7.0.

## Граница модуля

P11 является human-in-the-loop слоем между предложением P10 и подтвержденным
ProductionEvent. Ни точное совпадение, ни наличие фотографий не создают событие
автоматически. MAX sender сохраняется как источник и возможный reporter;
Actor решения всегда является текущей учетной записью ProLOG.

Полная цепочка аудита:

`ProductionEvent -> ProductionInboxReview -> Proposal -> MatchRun -> Bundle ->
ProductionInboxMessage/Attachment -> WorkBot revision/media -> MAX`.

Source P8, deterministic bundle P9 и MatchRun/Proposal P10 не редактируются.

## Очередь

Вкладка `Фотоотчёты` показывает review-effective bundles. По умолчанию видны
только записи, требующие проверки. Отдельные фильтры показывают подтвержденные,
отклоненные, измененные после решения, photo-only, text-only и все записи.

При открытии вкладки и по кнопке `Обновить` в рабочем потоке запускается
incremental pipeline P8 -> P9 -> P10. Qt UI не читает SQLite и WorkBot media
напрямую. Постоянного polling нет.

## Решение оператора

Product обязателен. ProductionStage, readiness и reporter необязательны. Object
в интерфейсе следует выбранному Product, а исторический snapshot фиксирует
ProductionService. `observed_at_utc` по умолчанию берется из последнего source
message bundle и может быть исправлен в локальном времени перед подтверждением.

Снижение readiness требует одного из существующих аудируемых вариантов:

* `rework`;
* correction подтвержденного события;
* observation с обязательной причиной.

Stage не выводится из WorkType. Слово `сборка` не создает stage и не сохраняется
как alias автоматически.

## Подтверждение и media

`ProductionInboxReviewService` выполняет последовательность:

1. повторно проверяет current bundle, fingerprint, current MatchRun и directory
   context fingerprint;
2. создает immutable snapshot решения в статусе `confirming`;
3. читает каждое source media через WorkBot gateway и проверяет SHA-256;
4. передает исходные bytes в AttachmentService с устойчивой source identity;
5. создает draft через ProductionService, связывает Attachment в source order;
6. переводит событие в ready и confirmed;
7. сохраняет event link и append-only audit action.

При недоступной или поврежденной intended фотографии событие не подтверждается.
Text-only bundle может быть подтвержден без Attachment. WorkBot source media не
удаляется; Production Attachment получает собственную physical copy/CAS и
доступен через обычную карточку и экспорт P6.1.

Review UID образует idempotency key `p11-review:<uid>`. Повтор использует уже
созданные Attachment и Event. Если Event подтвердился, но запись review-link
завершилась сбоем, retry находит Event по ключу и завершает linkage.

## Edit, stale и correction

Изменение source до решения делает открытый экран stale и блокирует
подтверждение. Изменение после подтверждения не меняет Event: новая lineage
показывается как `Источник изменен` с исходным и новым текстом. Оператор может
создать correction, оставить прежний Event или открыть карточку изделия.

Correction создает новый ProductionEvent. Исходный факт становится superseded
только при успешном подтверждении correction.

## Reject и ручная группировка

Reject сохраняет Actor, typed reason и комментарий, но не создает Event и не
удаляет источник. Manual split/merge создает bundles с `origin=manual` и связи
`ProductionInboxManualBundleSources`. Исходные deterministic bundles остаются
current для source-аудита, но исключаются из review-effective очереди. Merge
между chat/source запрещен; смешение sender требует явного подтверждения.

## Alias learning

Checkbox обучения всегда выключен по умолчанию. Пользователь вводит конкретное
короткое выражение. Product использует существующий ProductAlias, Stage -
ProductionStageAlias. Полные descriptions, проценты, выражения длиннее пяти слов
и слово `сборка` отклоняются. Подтвержденный Event не отменяется при ошибке
необязательного сохранения alias.

## Диагностика

Structured diagnostics обнаруживает отсутствующий Event у confirmed review,
broken event/source_ref, незавершенную promotion, потерю provenance и broken
manual lineage. Диагностика ничего не исправляет и не удаляет автоматически.
