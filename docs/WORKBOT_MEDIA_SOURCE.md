# WorkBot MAX source archive
Версия контракта: P7 / WorkBot schema 2 / ProLOG 0.6.2.

## 1. Назначение и граница

WorkBot сохраняет воспроизводимый исходный поток MAX до запуска parser:

```text
MAX update
  -> source message
  -> immutable revision
  -> ordered source attachment metadata
  -> WorkBot media CAS
  -> legacy text parser (если применимо)
```

Source archive ничего не решает о производстве. Он не импортирует production,
не создает Product, ProductionStage, Production Attachment, ProductionEvent и
не использует WorkBotImportRows как очередь media.

`body.text` MAX является единым source text. Для сообщения с media это же поле
играет роль подписи; отдельное выдуманное поле caption не создается.

## 2. SQLite schema v2

WorkBot schema 2 добавляет четыре таблицы, не изменяя legacy-таблицы.

### source_messages

- `source_message_id TEXT PRIMARY KEY` — реальный `body.mid`, fallback только
  для транспорта без ID;
- `chat_id INTEGER NULL`;
- `sender_max_user_id INTEGER NOT NULL`;
- `sender_display_snapshot TEXT NOT NULL`;
- `message_timestamp_utc TEXT NOT NULL`;
- `source_sequence INTEGER NULL` — `body.seq` MAX;
- `first_received_at_utc`, `last_received_at_utc`;
- `is_deleted`, `deleted_at_utc` — только по реальному `message_removed`.

### source_message_revisions

- `id INTEGER PRIMARY KEY`;
- `source_message_id` с FK `ON DELETE RESTRICT`;
- `revision_number INTEGER NOT NULL`;
- `source_sequence INTEGER NULL`;
- `source_text TEXT NULL`;
- `content_hash TEXT NOT NULL`;
- `content_json TEXT NOT NULL`;
- `raw_envelope_json TEXT NOT NULL`;
- `message_timestamp_utc`, `edited_at_utc`, `received_at_utc`;
- уникальность `(source_message_id, revision_number)`;
- уникальность `(source_message_id, content_hash)`.

### source_message_attachments

- `id INTEGER PRIMARY KEY`;
- `revision_id` с FK `ON DELETE RESTRICT`;
- `source_attachment_id`, `identity_kind`, `source_order`;
- `attachment_type`, `mime_type`, `original_name`, `source_size`;
- `source_url`, `source_token`, `source_payload_json` для retry;
- `download_status`, `download_attempts`, `last_error`;
- `sha256`, относительный `storage_key`;
- `received_at_utc`, `last_attempt_at_utc`, `next_retry_at_utc`,
  `downloaded_at_utc`;
- уникальность `(revision_id, source_attachment_id)`;
- уникальность `(revision_id, source_order)`.

Допустимые статусы: `pending`, `downloading`, `downloaded`, `failed`,
`unavailable`.

### source_message_tombstones

Хранит реальные события удаления, включая случай, когда локального source
message еще нет: `source_message_id`, `chat_id`, `deleted_at_utc` и исходный
update JSON. Локальные ревизии и файлы не удаляются.

Точный исполняемый SQL находится в `workbot/migrations.py`.

## 3. Source identity и ревизии

Основная identity сообщения — `body.mid`. Identity вложения выбирается только
из реально полученных полей: attachment/file ID, затем `payload.token`, затем
`payload.url`. Если transport не дал ни одного идентификатора, fallback явно
маркируется `derived_metadata_hash` и строится из канонических metadata и
source order.

Content hash считается по каноническим `text + attachments + link`. Повторная
доставка того же содержимого не создает ревизию. Изменение текста, подписи или
состава вложений создает следующую ревизию; предыдущая остается неизменной.

Порядок media хранится только в `source_order` и не зависит от имени, хэша,
файловой системы или порядка завершения download.

## 4. Физическое хранилище

Корень задается `WORKBOT_MEDIA_ROOT`, portable default:

```text
data/workbot_media/
  ab/
    cd/
      abcdef...64-hex-sha256
```

В SQLite хранится только относительный `storage_key`. Корень можно перенести
без изменения metadata. WorkBot и production используют общую безопасную CAS
инфраструктуру, но разные roots и разные бизнес-сущности.

Сохранение: temp в целевом каталоге, write, flush, fsync, проверка SHA-256,
атомарная публикация без молчаливого перезаписывания, повторная проверка.
Path traversal, абсолютные/UNC/drive paths и выход через symlink отклоняются.

## 5. Download, retry и recovery

Envelope и metadata фиксируются до сети. Затем attachment атомарно переводится
в `downloading`. Сетевой сбой дает `failed` и ограниченный exponential backoff;
отсутствие доступного URL дает `unavailable`. После restart выбираются
`pending`, готовые к retry `failed` и stale `downloading`.

- crash после envelope: source остается диагностируемым;
- crash после metadata: download продолжится после restart;
- temp до publish: diagnostics сообщает `temp_file`;
- файл готов, DB update не выполнен: diagnostics сообщает `orphan_file`;
- повтор update/retry не создает duplicate revision, metadata или bytes.

## 6. Diagnostics

Структурированный отчет обнаруживает отсутствующих родителей, content hash
inconsistency, duplicate identity, stale pending, failed/unavailable download,
небезопасный key, отсутствующий/поврежденный файл, orphan/temp и недоступный
root. Diagnostics ничего не исправляет и не удаляет автоматически.

## 7. Backup и восстановление

Полная единица WorkBot backup после P7:

```text
data/workbot.sqlite3 + WORKBOT_MEDIA_ROOT
```

Копия только SQLite больше не является полной копией source archive. Для
согласованного backup нужно остановить polling либо применить SQLite online
backup, затем скопировать media root. Production attachment root остается
отдельной областью, хотя общий backup установки должен включать обе.

## 8. Ограничения P7

Long Polling сообщает `message_removed`, поэтому tombstone поддерживается.
Автоматический historical media backfill не запускается. Существующая явная
команда backfill продолжает обслуживать только прежний текстовый сценарий.
Группировка, Product matching, этап, readiness и production inbox относятся к
P8-P11.
