"""Application service that transports WorkBot source revisions one-to-one."""

from __future__ import annotations

from dataclasses import replace
from typing import Protocol, Sequence

from production.source_transport_models import (
    InboxSourceType,
    ProductionInboxSource,
    ProductionSourceDiagnosticIssue,
    ProductionSourceDiagnosticKind,
    ProductionSourceDiagnosticsReport,
    ProductionSourceSyncResult,
    SourceRevisionSnapshot,
    SourceRevisionFailure,
    SourceSyncCursor,
)
from production.source_transport_repository import (
    ProductionSourceTransportRepository,
    SourceRevisionConflictError,
)


class ProductionSourceGateway(Protocol):
    def fetch_new(
        self,
        chat_id: int,
        after_revision_id: int,
        *,
        limit: int,
    ) -> Sequence[SourceRevisionSnapshot | SourceRevisionFailure]: ...

    def fetch_revisions(
        self,
        chat_id: int,
        revision_ids: Sequence[int],
    ) -> Sequence[SourceRevisionSnapshot | SourceRevisionFailure]: ...

    def revision_identity(
        self,
        chat_id: int,
        revision_id: int,
    ) -> SourceSyncCursor | None: ...


class ProductionSourceTransportService:
    def __init__(self, repository: ProductionSourceTransportRepository) -> None:
        self.repository = repository

    def register_max_chat(
        self,
        display_name: str,
        chat_id: int,
        *,
        web_url: str = "",
        enabled: bool = True,
    ) -> ProductionInboxSource:
        return self.repository.save_source(
            ProductionInboxSource(
                InboxSourceType.MAX_CHAT,
                display_name,
                chat_id=chat_id,
                enabled=enabled,
                web_url=web_url,
            )
        )

    def sync_enabled_sources(
        self,
        gateway: ProductionSourceGateway,
        *,
        batch_size: int = 100,
    ) -> tuple[ProductionSourceSyncResult, ...]:
        return tuple(
            self.sync_source(source.id, gateway, batch_size=batch_size)
            for source in self.repository.list_sources(enabled_only=True)
            if source.id is not None
        )

    def sync_source(
        self,
        source_id: int,
        gateway: ProductionSourceGateway,
        *,
        batch_size: int = 100,
    ) -> ProductionSourceSyncResult:
        source = self.repository.get_source(source_id)
        if source is None:
            raise ValueError(f"Production source {source_id} не найден")
        if not source.enabled:
            return ProductionSourceSyncResult(source_id)
        if source.source_type is not InboxSourceType.MAX_CHAT:
            raise ValueError(f"Для source type {source.source_type} не настроен adapter")
        if source.chat_id is None:
            self.repository.record_issue(
                source_id, 0, "", 0, "source_without_chat_id",
                "Для MAX production-source не подтвержден chat_id",
            )
            return ProductionSourceSyncResult(source_id, error_count=1)

        cursor = self._validated_cursor(source, gateway)
        run_id = self.repository.begin_run(source_id, cursor.revision_id)
        result = ProductionSourceSyncResult(
            source_id,
            cursor_before=cursor.revision_id,
            cursor_after=cursor.revision_id,
        )
        errors: list[str] = []

        try:
            retry_ids = self.repository.unresolved_revision_ids(source_id)
            if retry_ids:
                result = self._process_revisions(
                    source,
                    gateway.fetch_revisions(source.chat_id, retry_ids),
                    run_id,
                    result,
                    errors,
                    advance_cursor=False,
                )

            while True:
                revisions = gateway.fetch_new(
                    source.chat_id,
                    result.cursor_after,
                    limit=max(1, batch_size),
                )
                if not revisions:
                    break
                result = self._process_revisions(
                    source,
                    revisions,
                    run_id,
                    result,
                    errors,
                    advance_cursor=True,
                )
                if len(revisions) < max(1, batch_size):
                    break
        except Exception as exc:
            errors.append(str(exc))
            result = replace(result, error_count=result.error_count + 1)
            self.repository.finish_run(
                run_id,
                result,
                error_summary="; ".join(errors[:10]),
                failed=True,
            )
            raise

        self.repository.finish_run(run_id, result, error_summary="; ".join(errors[:10]))
        return result

    def diagnostics(self) -> ProductionSourceDiagnosticsReport:
        rows = self.repository.diagnostics_rows()
        issues: list[ProductionSourceDiagnosticIssue] = []
        for source in rows["sources"]:
            if (
                bool(source["enabled"])
                and str(source["source_type"]) == InboxSourceType.MAX_CHAT.value
                and source["chat_id"] is None
            ):
                issues.append(
                    ProductionSourceDiagnosticIssue(
                        ProductionSourceDiagnosticKind.SOURCE_WITHOUT_CHAT_ID,
                        "У активного MAX production-source отсутствует chat_id",
                        int(source["id"]),
                    )
                )
        for row in rows["unresolved_issues"]:
            issues.append(
                ProductionSourceDiagnosticIssue(
                    ProductionSourceDiagnosticKind.SYNC_ISSUE,
                    f"{row['issue_code']}: {row['message']}",
                    int(row["source_id"]),
                    int(row["source_revision_id"]),
                )
            )
        for row in rows["messages_without_source"]:
            issues.append(
                ProductionSourceDiagnosticIssue(
                    ProductionSourceDiagnosticKind.MESSAGE_WITHOUT_SOURCE,
                    f"Inbox message {row[0]} не имеет source",
                )
            )
        for row in rows["attachments_without_message"]:
            issues.append(
                ProductionSourceDiagnosticIssue(
                    ProductionSourceDiagnosticKind.ATTACHMENT_WITHOUT_MESSAGE,
                    f"Inbox attachment {row[0]} не имеет message",
                )
            )
        counts = rows["counts"]
        return ProductionSourceDiagnosticsReport(
            tuple(issues), int(counts[0]), int(counts[1]), int(counts[2])
        )

    def _validated_cursor(
        self,
        source: ProductionInboxSource,
        gateway: ProductionSourceGateway,
    ) -> SourceSyncCursor:
        assert source.id is not None and source.chat_id is not None
        cursor = self.repository.cursor(source.id)
        if cursor.revision_id == 0:
            return cursor
        actual = gateway.revision_identity(source.chat_id, cursor.revision_id)
        if actual == cursor:
            return cursor
        self.repository.record_issue(
            source.id,
            cursor.revision_id,
            cursor.message_id,
            cursor.revision_number,
            "cursor_identity_mismatch",
            "WorkBot source cursor не совпадает с текущей БД; выполнен безопасный rescan",
        )
        self.repository.reset_cursor(source.id)
        return SourceSyncCursor()

    def _process_revisions(
        self,
        source: ProductionInboxSource,
        revisions: Sequence[SourceRevisionSnapshot | SourceRevisionFailure],
        run_id: int,
        result: ProductionSourceSyncResult,
        errors: list[str],
        *,
        advance_cursor: bool,
    ) -> ProductionSourceSyncResult:
        assert source.id is not None
        for revision in sorted(revisions, key=lambda item: item.revision_id):
            result = replace(result, read_count=result.read_count + 1)
            success = False
            if isinstance(revision, SourceRevisionFailure):
                errors.append(revision.error)
                result = replace(result, error_count=result.error_count + 1)
                self.repository.record_issue(
                    source.id,
                    revision.revision_id,
                    revision.source_message_id,
                    revision.revision_number,
                    "source_revision_read_error",
                    revision.error,
                )
            elif revision.sender_is_bot:
                result = replace(result, skipped_count=result.skipped_count + 1)
                success = True
            else:
                try:
                    self.repository.resolve_revision_issues(source.id, revision.revision_id)
                    snapshot, created = self.repository.import_revision(
                        source, revision, run_id
                    )
                    if not created:
                        result = replace(
                            result, unchanged_count=result.unchanged_count + 1
                        )
                    elif snapshot.change_kind.value == "changed":
                        result = replace(
                            result,
                            imported_count=result.imported_count + 1,
                            changed_count=result.changed_count + 1,
                        )
                    else:
                        result = replace(
                            result, imported_count=result.imported_count + 1
                        )
                    attachment_errors = 0
                    for attachment in revision.attachments:
                        if not attachment.issue_code:
                            continue
                        attachment_errors += 1
                        self.repository.record_issue(
                            source.id,
                            revision.revision_id,
                            revision.source_message_id,
                            revision.revision_number,
                            attachment.issue_code,
                            attachment.issue_message,
                            attachment_id=attachment.source_attachment_id,
                        )
                    if attachment_errors:
                        result = replace(
                            result, error_count=result.error_count + attachment_errors
                        )
                    success = True
                except SourceRevisionConflictError as exc:
                    errors.append(str(exc))
                    result = replace(result, error_count=result.error_count + 1)
                    self.repository.record_issue(
                        source.id, revision.revision_id,
                        revision.source_message_id, revision.revision_number,
                        "source_revision_conflict", str(exc),
                    )
                except Exception as exc:
                    errors.append(str(exc))
                    result = replace(result, error_count=result.error_count + 1)
                    self.repository.record_issue(
                        source.id, revision.revision_id,
                        revision.source_message_id, revision.revision_number,
                        "transport_error", str(exc),
                    )
            if advance_cursor and revision.revision_id > result.cursor_after:
                self.repository.advance_cursor(source.id, revision, success=success)
                result = replace(result, cursor_after=revision.revision_id)
        return result
