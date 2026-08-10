# Production source transport

Версия контракта: P8 / core schema 5 / ProLOG 0.6.3.

## 1. Назначение и граница

P8 переносит подтвержденные конфигурацией исходные ревизии из WorkBot source
archive в независимый immutable snapshot ProLOG:

```text
WorkBot source revision
  -> enabled ProductionInboxSource
  -> ProductionInboxMessage
  -> ProductionInboxAttachment provenance
```

Одна source revision всегда создает не более одной inbox message. P8 не
объединяет последовательные фотографии и текст. `ProductionInboxBundle`
остается контрактом будущего логического пакета P9 и на P8 не хранится.

P8 не определяет Product, Object, ProductionStage и readiness, не создает
ProductionEvent, Production Attachment и не использует WorkBotImportRows.

## 2. Настроенный MAX source

Для текущего deployment зарегистрирован источник:

- type: `max_chat`;
- display name: `Фотоотчеты Электроцех`;
- web URL: `https://web.max.ru/-77703766302910`;
- фактический MAX API chat ID: `-77703766302910`.

Совпадение с числом web-ссылки не предполагалось. Chat ID подтвержден:

1. raw P7 envelope содержит `recipient.chat_id = -77703766302910`;
2. read-only `GET /chats/-77703766302910` вернул title
   `Фотоотчеты Электроцех`, type `chat`, status `active`.

Значение хранится в `ProductionInboxSources`, а не в Python parser. Новая
группа добавляется новой конфигурационной записью без изменения алгоритма.

## 3. Core schema 5

`ProductionInboxSources` хранит UUID, source type/ref, display name, nullable
chat ID, enabled, web URL и UTC created/updated timestamps.

`ProductionInboxMessages` хранит immutable snapshot: WorkBot message и
revision identity, chat/sender snapshots, source timestamps и sequence,
text/caption, content hash/JSON, raw envelope, change kind и ссылку на
предыдущую inbox revision. Уникальность защищает source revision ID и
message/revision number внутри source.

`ProductionInboxAttachments` хранит только metadata/provenance: WorkBot row и
attachment identity, source order, MIME/name/size, download status, SHA-256,
относительный WorkBot storage key, downloaded timestamp и media state.
Абсолютный путь и байты в core SQLite не сохраняются.

`ProductionInboxSyncState` содержит revision-aware cursor:

```text
revision_id + message_id + revision_number + content_hash
```

`ProductionInboxSyncRuns` хранит счетчики и результат transport batch.
`ProductionInboxSyncIssues` хранит повторяемые диагностируемые ошибки одной
ревизии или attachment без отката независимых сообщений.

## 4. Incremental sync и revisions

WorkBot `source_message_revisions.id` является возрастающим watermark. Позднее
редактирование старого message получает новый revision row ID и поэтому не
теряется. Простые MAX message IDs как cursor не используются.

Cursor identity сверяется с текущей WorkBot DB. Если база заменена, откатилась
или ID указывает на другое содержимое, cursor сбрасывается и выполняется
безопасный rescan. Уникальные ограничения inbox превращают его в идемпотентную
операцию.

Одинаковая повторная синхронизация ничего не создает. Новая revision создает
новый immutable snapshot с `change_kind = changed` и ссылкой на предыдущий;
старая revision не изменяется.

## 5. Filtering и sender

Transport читает только enabled `max_chat` sources. Обычные группы ежедневных
отчетов не попадают в production inbox. Sender MAX ID и display snapshot
сохраняются независимо от Employee binding. Сообщения самого бота пропускаются
только transport-слоем.

## 6. Media provenance и перенос root

WorkBot media остается в отдельном CAS root. Путь root задается через
`workbot_media_root` в конфигурации ProLOG; default - каталог `workbot_media`
рядом с выбранной WorkBot DB. Inbox хранит только относительный storage key,
поэтому перенос root не требует изменения SQLite snapshots.

P8 проверяет доступность и SHA-256, но не копирует байты в production
attachment root. Missing/corrupt/pending media фиксируется как provenance и
issue, не блокируя следующие messages. Materialization Production Attachment
относится к последующему подтверждаемому workflow.

## 7. Диагностика и recovery

Структурированная диагностика показывает source без chat ID, unresolved sync
issues, поврежденные внутренние связи и счетчики snapshot. Некорректная source
revision превращается в failure-элемент batch: cursor продвигается, issue
остается для retry, следующие revisions продолжают импортироваться.

## 8. Граница P8/P9

Последовательность `photo1`, `photo2`, `photo3`, `text` после P8 остается
четырьмя inbox messages. Только P9 сможет детерминированно объединить их в
logical bundle по chat, sender, времени и правилам завершения пакета.
