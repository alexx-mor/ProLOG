# Группировка Production Inbox

Версия контракта: P9 / core schema 6 / ProLOG 0.6.5.

## 1. Граница ответственности

P8 сохраняет одну immutable revision MAX как один `ProductionInboxMessage`.
P9 строит поверх текущего effective source view логические пакеты и отвечает
только на вопрос, какие последовательные source messages относятся к одному
сообщению автора.

P9 не определяет Product, Object, ProductionStage и readiness, не создает
ProductionEvent, Production Attachment или WorkLog. Эти решения остаются
следующим этапам review/matching.

## 2. Effective source view

Для каждого `source_id + source_message_id` выбирается revision с наибольшим
`source_revision_number`. Старые revisions остаются в P8 для аудита. Реальный
tombstone переносится отдельным immutable snapshot и исключает сообщение из
текущего source view, но не удаляет ни source revision, ни старый bundle.

Группировка использует source chronology:

1. `message_timestamp_utc`;
2. известный `source_sequence` перед отсутствующим;
3. `source_sequence`;
4. `source_message_id`;
5. `source_revision_number`;
6. `source_revision_id`.

SQLite ID, время синхронизации/скачивания, filename и SHA-256 media не задают
порядок сообщений.

## 3. Изоляция отправителей

Открытый контекст имеет ключ:

`source_id + chat_id + sender_max_user_id`.

Сообщение другого автора не закрывает, не перехватывает и не дополняет чужой
пакет. Поэтому мастер и заместитель могут писать в одной группе вперемешку.
Неизвестный sender не теряется; для безопасности такие сообщения изолируются
по `source_message_id` и не объединяются друг с другом автоматически.

Разные production sources и chat ID никогда не объединяются.

## 4. Детерминированный автомат

- Photo-only открывает пакет или добавляется к открытой последовательности
  своего sender.
- Text-only закрывает открытую фото-последовательность своего sender.
- Photo + caption закрывает текущую последовательность, включая собственные
  media этого сообщения; без открытого контекста это самостоятельный complete
  bundle.
- Text-only без фотографий сохраняется как `text_only` / `standalone_text`.
- Пустой source message сохраняется как `invalid`, а не исчезает.
- Несколько attachments одного message остаются одним source message; их
  `source_order` читается из P8.

Grouping statuses отделены от будущего business-review lifecycle:
`collecting`, `complete`, `needs_description`, `text_only`, `invalid`.

## 5. Окно и граница дня

Default window равен 15 минутам и задается настройкой
`production_grouping_window_minutes`. Значение сохраняется в секундах в каждом
bundle. Если пауза превышена, фото закрываются как `needs_description` с
`close_reason = timeout`; поздний текст становится самостоятельным пакетом.

UTC timestamps источника не меняются. Для календарной границы используется
настраиваемое смещение `production_grouping_utc_offset_minutes` (deployment
default: UTC+3). Переход локальной даты закрывает фотографии с
`close_reason = day_boundary`; перенос через полночь не выполняется.

## 6. Воспроизводимость и lineage

Текущая версия правила: `deterministic-v1`.

`source_fingerprint` является SHA-256 от source/chat/sender, версии правила,
окна, UTC offset и точных immutable revision identities/content hashes в
bundle order. Повторный запуск над тем же source view не создает дубликаты.

При edit или tombstone прежний bundle остается историческим (`is_current = 0`),
а новый current bundle с пересчитанным составом указывает на предшественника
через `supersedes_bundle_id`. Удаление closing text превращает новый effective
пакет фотографий в `needs_description`; удаление фото оставляет остальные
effective messages. Старые результаты не переписываются под новую историю.

`origin` уже различает `deterministic` и будущий `manual`, но P9 создает только
deterministic bundles. P11 сможет добавлять ручное объединение/разделение без
перезаписи source layer.

## 7. Persistence

`ProductionInboxBundles` хранит derived пакет, правило, окно, fingerprint,
текущий/исторический статус и lineage.

`ProductionInboxBundleMessages` хранит упорядоченную связь с immutable P8
message snapshot и роль: `photo_source`, `closing_text`, `captioned_media`,
`text_only` либо `source_only`.

Отдельной bundle-attachment таблицы нет. Media читается по цепочке:

`Bundle -> BundleMessages -> ProductionInboxAttachments`.

## 8. Диагностика

Структурированная диагностика обнаруживает:

- effective message без current bundle или сразу в нескольких;
- смешение source/chat/sender;
- нарушение bundle order или attachment source order;
- fingerprint mismatch и неизвестную rule version;
- current bundle, не соответствующий effective source view;
- просроченный `collecting`;
- broken supersedes lineage.

Диагностика ничего не удаляет и не исправляет автоматически.
