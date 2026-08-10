"""Deterministic, explainable proposal generation for current P9 bundles."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, replace

from matching_text import (
    find_identifier,
    find_identifier_span,
    identifier_tokens,
    normalize_match_text,
)
from production.matching_models import (
    MATCHER_RULE_VERSION,
    BundleMatchingInput,
    MatchAnalysis,
    MatchCandidate,
    MatchingContext,
    MatchingDiagnosticIssue,
    MatchingDiagnosticKind,
    MatchingDiagnosticsReport,
    MatchingResult,
    MatchQuality,
    MatchRunStatus,
    ProposalDraft,
    ProposalEvidence,
    ProposalIssue,
)
from production.matching_repository import ProductionInboxMatchingRepository


_SEGMENT_RE = re.compile(r"[^;\n]+")
_NUMBERED_PREFIX_RE = re.compile(r"^\s*\d+[.)]\s*")
_RANGE_RE = re.compile(r"(?<!\d)(\d{1,3})\s*-\s*(\d{1,3})\s*%")
_PERCENT_RE = re.compile(r"(?<!\d)(\d{1,3})%")
_READINESS_WORD_RE = re.compile(r"\bготовност(?:ь|и)\s*:?\s*(\d{1,3})(?!\s*%)")


class ProductionInboxMatchingService:
    def __init__(self, repository: ProductionInboxMatchingRepository) -> None:
        self.repository = repository

    def match_bundle(self, bundle_id: int) -> MatchingResult:
        bundle = self.repository.load_bundle_input(bundle_id)
        if bundle is None:
            raise ValueError("Production Inbox bundle не найден")
        context = self.repository.load_context()
        return self.repository.save_analysis(self.analyze(bundle, context))

    def match_all_current(self) -> tuple[MatchingResult, ...]:
        self.repository.deactivate_runs_for_noncurrent_bundles()
        context = self.repository.load_context()
        return tuple(
            self.repository.save_analysis(
                self.analyze(self.repository.load_bundle_input(bundle_id), context)
            )
            for bundle_id in self.repository.current_bundle_ids()
        )

    def analyze(
        self,
        bundle: BundleMatchingInput | None,
        context: MatchingContext,
    ) -> MatchAnalysis:
        if bundle is None:
            raise ValueError("Production Inbox bundle не найден")
        normalized = normalize_match_text(bundle.source_text)
        context_hash = directory_context_fingerprint(context)
        input_hash = _sha(bundle.source_text)
        if not normalized:
            proposals = (_missing_description_proposal(bundle),)
            status = MatchRunStatus.NO_TEXT
        else:
            segments = _deterministic_segments(bundle.source_text, context)
            proposals = tuple(
                _match_segment(
                    text,
                    start,
                    end,
                    order,
                    context,
                    segmented=len(segments) > 1,
                )
                for order, (text, start, end) in enumerate(segments)
            )
            status = (
                MatchRunStatus.NEEDS_REVIEW
                if any(item.requires_review for item in proposals)
                else MatchRunStatus.MATCHED
            )
        result_hash = _proposal_fingerprint(proposals)
        return MatchAnalysis(
            bundle, normalized, context_hash, input_hash, result_hash, status, proposals
        )

    def diagnostics(self) -> MatchingDiagnosticsReport:
        context = self.repository.load_context()
        context_hash = directory_context_fingerprint(context)
        objects = {item.id: item for item in context.objects}
        products = {item.id: item for item in context.products}
        stages = {item.id: item for item in context.stages}
        raw = self.repository.diagnostics_rows()
        issues: list[MatchingDiagnosticIssue] = []
        runs_by_bundle = {int(row["bundle_id"]): row for row in raw["current_runs"]}
        for bundle in raw["current_bundles"]:
            bundle_id = int(bundle["id"])
            if bundle_id not in runs_by_bundle:
                issues.append(MatchingDiagnosticIssue(
                    MatchingDiagnosticKind.CURRENT_BUNDLE_WITHOUT_RUN,
                    "Current bundle не имеет current match run", bundle_id=bundle_id,
                ))
        for run in raw["current_runs"]:
            run_id = int(run["id"])
            bundle_id = int(run["bundle_id"])
            if str(run["bundle_fingerprint"]) != str(run["actual_bundle_fingerprint"]):
                issues.append(MatchingDiagnosticIssue(
                    MatchingDiagnosticKind.BUNDLE_FINGERPRINT_MISMATCH,
                    "MatchRun относится к другой версии bundle fingerprint",
                    bundle_id, run_id,
                ))
            if str(run["directory_context_fingerprint"]) != context_hash:
                issues.append(MatchingDiagnosticIssue(
                    MatchingDiagnosticKind.CONTEXT_FINGERPRINT_MISMATCH,
                    "Справочники изменились после выполнения matcher",
                    bundle_id, run_id,
                ))
            if str(run["matcher_rule_version"]) != MATCHER_RULE_VERSION:
                issues.append(MatchingDiagnosticIssue(
                    MatchingDiagnosticKind.UNKNOWN_RULE_VERSION,
                    "Неизвестная версия production matcher", bundle_id, run_id,
                ))
        for row in raw["proposals"]:
            proposal_id = int(row["id"])
            product_id = row["product_id"]
            object_id = row["object_id"]
            stage_id = row["stage_id"]
            if product_id is not None and int(product_id) not in products:
                issues.append(_diag(MatchingDiagnosticKind.PRODUCT_MISSING, row))
            if object_id is not None and int(object_id) not in objects:
                issues.append(_diag(MatchingDiagnosticKind.OBJECT_MISSING, row))
            if stage_id is not None and int(stage_id) not in stages:
                issues.append(_diag(MatchingDiagnosticKind.STAGE_MISSING, row))
            if product_id is not None and object_id is not None:
                product = products.get(int(product_id))
                if product is not None and product.object_id != int(object_id):
                    issues.append(_diag(
                        MatchingDiagnosticKind.PRODUCT_OBJECT_CONFLICT, row
                    ))
            readiness = row["readiness_percent"]
            if readiness is not None and not 0 <= int(readiness) <= 100:
                issues.append(_diag(MatchingDiagnosticKind.INVALID_READINESS, row))
            if str(row["issue_code"] or "") == "readiness_ambiguous":
                issues.append(_diag(MatchingDiagnosticKind.READINESS_AMBIGUOUS, row))
            selected = (
                products.get(int(product_id)) if product_id is not None else None,
                objects.get(int(object_id)) if object_id is not None else None,
                stages.get(int(stage_id)) if stage_id is not None else None,
            )
            if any(item is not None and not item.is_active for item in selected):
                issues.append(_diag(MatchingDiagnosticKind.INACTIVE_SELECTED, row))
        seen_ranks: set[tuple[str, int, int]] = set()
        for row in raw["candidate_ranks"]:
            key = (str(row["kind"]), int(row["proposal_id"]), int(row["rank"]))
            if key in seen_ranks:
                issues.append(MatchingDiagnosticIssue(
                    MatchingDiagnosticKind.DUPLICATE_CANDIDATE_RANK,
                    "Повторяется deterministic candidate rank",
                    proposal_id=int(row["proposal_id"]),
                ))
            seen_ranks.add(key)
        for row in raw["lineage"]:
            if row["parent_id"] is None or int(row["id"]) == int(row["parent_id"]):
                issues.append(MatchingDiagnosticIssue(
                    MatchingDiagnosticKind.BROKEN_LINEAGE,
                    "Нарушена lineage MatchRun", match_run_id=int(row["id"]),
                ))
        return MatchingDiagnosticsReport(
            tuple(issues), len(raw["current_bundles"]),
            len(raw["current_runs"]), len(raw["proposals"]),
        )


def directory_context_fingerprint(context: MatchingContext) -> str:
    payload = {
        "objects": [asdict(item) for item in context.objects],
        "products": [asdict(item) for item in context.products],
        "stages": [asdict(item) for item in context.stages],
        "object_aliases": [asdict(item) for item in context.object_aliases],
        "product_aliases": [asdict(item) for item in context.product_aliases],
        "stage_aliases": [asdict(item) for item in context.stage_aliases],
        "rule": MATCHER_RULE_VERSION,
    }
    return _sha(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _deterministic_segments(
    source_text: str,
    context: MatchingContext,
) -> tuple[tuple[str, int, int], ...]:
    raw: list[tuple[str, int, int]] = []
    for match in _SEGMENT_RE.finditer(source_text):
        text = match.group(0)
        leading = len(text) - len(text.lstrip())
        stripped = text.strip()
        numbered = _NUMBERED_PREFIX_RE.match(stripped)
        prefix = numbered.end() if numbered else 0
        segment = stripped[prefix:].strip()
        if not segment:
            continue
        start = match.start() + leading + prefix
        raw.append((segment, start, start + len(segment)))
    if len(raw) < 2:
        return ((source_text, 0, len(source_text)),)
    product_ids: list[int] = []
    for segment, _start, _end in raw:
        object_candidates = _object_candidates(segment, context)
        object_id = _unique_top_id(object_candidates)
        candidates = _product_candidates(segment, context, object_id)
        product_id = _unique_top_id(candidates)
        if product_id is None:
            return ((source_text, 0, len(source_text)),)
        product_ids.append(product_id)
    if len(set(product_ids)) != len(product_ids):
        return ((source_text, 0, len(source_text)),)
    return tuple(raw)


def _match_segment(text, start, end, order, context, *, segmented) -> ProposalDraft:
    normalized = normalize_match_text(text)
    object_candidates = _object_candidates(text, context)
    explicit_object_id = _unique_top_id(object_candidates)
    product_candidates = _product_candidates(text, context, explicit_object_id)
    product_id = _unique_top_id(product_candidates)
    product = next((item for item in context.products if item.id == product_id), None)
    issues: list[ProposalIssue] = []
    evidence: list[ProposalEvidence] = []

    if len(product_candidates) > 1 and product_id is None:
        exact_identifier_conflict = product_candidates[0].method in {
            "exact_serial", "exact_code"
        }
        code = (
            "exact_identifier_ambiguous"
            if exact_identifier_conflict
            else "split_ambiguous" if not segmented else "product_ambiguous"
        )
        issues.append(ProposalIssue(
            code,
            "Найдено несколько равнозначных изделий; требуется выбор оператора",
            ", ".join(item.matched_text for item in product_candidates),
        ))
    elif product is None:
        issues.append(ProposalIssue("product_unresolved", "Изделие не определено"))
    elif not product.is_active:
        issues.append(ProposalIssue(
            "inactive_product_candidate", "Найденное изделие отключено",
            product.name,
        ))

    object_id = explicit_object_id
    object_method = _selected_method(object_candidates, explicit_object_id)
    if product is not None:
        if explicit_object_id is not None and explicit_object_id != product.object_id:
            issues.append(ProposalIssue(
                "object_conflict",
                "Явно указанный объект противоречит объекту изделия",
                f"text object={explicit_object_id}; product object={product.object_id}",
            ))
        object_id = product.object_id
        object_method = "derived_from_product"
        evidence.append(ProposalEvidence(
            "object", "derived_from_product", str(product.object_id),
            f"Изделие {product.name} относится к объекту ID {product.object_id}",
        ))
    elif len(object_candidates) > 1 and explicit_object_id is None:
        issues.append(ProposalIssue(
            "object_ambiguous", "Найдено несколько равнозначных объектов",
            ", ".join(item.matched_text for item in object_candidates),
        ))

    if product_id is not None:
        selected = next(item for item in product_candidates if item.target_id == product_id)
        evidence.append(ProposalEvidence(
            "product", selected.method, selected.matched_text, selected.evidence,
        ))
    elif product_candidates:
        evidence.extend(
            ProposalEvidence("product", item.method, item.matched_text, item.evidence)
            for item in product_candidates
        )

    stage_candidates = _stage_candidates(text, context)
    stage_id = _unique_top_id(stage_candidates)
    stage_method = _selected_method(stage_candidates, stage_id)
    selected_stage = next((item for item in stage_candidates if item.target_id == stage_id), None)
    if selected_stage is not None and not selected_stage.is_active:
        issues.append(ProposalIssue(
            "inactive_stage_candidate", "Найденный производственный этап отключен",
            selected_stage.matched_text,
        ))
        stage_id = None
        stage_method = None
    elif len(stage_candidates) > 1 and stage_id is None:
        issues.append(ProposalIssue(
            "stage_ambiguous", "Найдено несколько равнозначных этапов",
            ", ".join(item.matched_text for item in stage_candidates),
        ))
    elif stage_id is None:
        issues.append(ProposalIssue("stage_unresolved", "Производственный этап не определен"))
    if selected_stage is not None:
        evidence.append(ProposalEvidence(
            "stage", selected_stage.method, selected_stage.matched_text,
            selected_stage.evidence,
        ))

    readiness, readiness_method, readiness_evidence, readiness_issue = _readiness(text)
    if readiness_evidence:
        evidence.append(ProposalEvidence(
            "readiness", readiness_method or "ambiguous", readiness_evidence,
            "Готовность извлечена только из явного процента или слова «готовность»",
        ))
    if readiness_issue is not None:
        issues.append(readiness_issue)
    product_method = _selected_method(product_candidates, product_id)
    critical_ambiguity = any(
        item.code in {
            "split_ambiguous", "product_ambiguous", "exact_identifier_ambiguous",
            "object_ambiguous",
            "object_conflict", "stage_ambiguous", "readiness_ambiguous",
            "invalid_readiness",
        }
        for item in issues
    )
    any_match = any(value is not None for value in (object_id, product_id, stage_id, readiness))
    if critical_ambiguity:
        quality = MatchQuality.AMBIGUOUS
    elif not any_match:
        quality = MatchQuality.NONE
    elif product_method in {"exact_serial", "exact_code"} and not issues:
        quality = MatchQuality.EXACT
    else:
        quality = MatchQuality.STRONG
    if segmented:
        evidence.insert(0, ProposalEvidence(
            "segmentation", "explicit_segment", text,
            "Сегмент отделен строкой/точкой с запятой и содержит самостоятельное изделие",
        ))
    return ProposalDraft(
        order=order,
        source_segment_text=text,
        normalized_segment_text=normalized,
        source_segment_start=start,
        source_segment_end=end,
        object_id=object_id,
        object_match_method=object_method,
        product_id=product_id,
        product_match_method=product_method,
        stage_id=stage_id,
        stage_match_method=stage_method,
        readiness_percent=readiness,
        readiness_match_method=readiness_method,
        description_text=text,
        normalized_description=normalized,
        match_quality=quality,
        requires_review=bool(issues),
        product_candidates=product_candidates,
        object_candidates=object_candidates,
        stage_candidates=stage_candidates,
        evidence=tuple(evidence),
        issues=tuple(issues),
    )


def _object_candidates(text: str, context: MatchingContext) -> tuple[MatchCandidate, ...]:
    best: dict[int, MatchCandidate] = {}
    objects = {item.id: item for item in context.objects}
    for item in context.objects:
        for value, score, method in (
            (item.project_number, 100, "exact_object_code"),
            (item.contract_number, 100, "exact_object_code"),
            (item.name, 90, "exact_object_name"),
        ):
            matched = find_identifier(text, value) if value.strip() else ""
            if matched:
                _keep(best, MatchCandidate(
                    item.id, 0, score, method, matched,
                    f"Текст содержит идентификатор объекта «{value}»",
                    item.is_active,
                ))
    for alias in context.object_aliases:
        matched = find_identifier(text, alias.alias_text)
        target = objects.get(alias.target_id)
        if matched and target is not None:
            _keep(best, MatchCandidate(
                target.id, 0, 80, "confirmed_object_alias", matched,
                f"Подтвержденный алиас объекта «{alias.alias_text}»",
                target.is_active,
            ))
    return _rank(best.values())


def _product_candidates(
    text: str,
    context: MatchingContext,
    object_id: int | None,
) -> tuple[MatchCandidate, ...]:
    best: dict[int, MatchCandidate] = {}
    products = {item.id: item for item in context.products}
    for item in context.products:
        for value, score, method in (
            (item.serial_number, 100, "exact_serial"),
            (item.code, 90, "exact_code"),
        ):
            matched = find_identifier(text, value) if value.strip() else ""
            if matched:
                _keep(best, MatchCandidate(
                    item.id, 0, score, method, matched,
                    f"Точное совпадение {'заводского номера' if method == 'exact_serial' else 'шифра'} «{value}»",
                    item.is_active, item.object_id,
                ))
    for alias in context.product_aliases:
        target = products.get(alias.target_id)
        matched = find_identifier(text, alias.alias_text)
        if matched and target is not None:
            _keep(best, MatchCandidate(
                target.id, 0, 80, "confirmed_alias", matched,
                f"Подтвержденный алиас изделия «{alias.alias_text}»",
                target.is_active, target.object_id,
            ))
    name_matches = []
    for item in context.products:
        span = find_identifier_span(text, item.name)
        if span is None:
            continue
        name_matches.append((item, span, identifier_tokens(item.name)))
    for item, span, tokens in name_matches:
        if any(
            len(tokens) < len(other_tokens)
            and other_span[0] <= span[0]
            and span[1] <= other_span[1]
            for _other, other_span, other_tokens in name_matches
        ):
            continue
        matched = normalize_match_text(text)[span[0]:span[1]]
        scoped = object_id is not None and item.object_id == object_id
        score = 70 if scoped else 60
        method = "object_scoped_name" if scoped else "unique_name"
        _keep(best, MatchCandidate(
            item.id, 0, score, method, matched,
            (
                f"Нормализованное имя «{item.name}» найдено внутри объекта {object_id}"
                if scoped else f"Нормализованное имя изделия «{item.name}»"
            ),
            item.is_active, item.object_id,
        ))
    return _rank(best.values())


def _stage_candidates(text: str, context: MatchingContext) -> tuple[MatchCandidate, ...]:
    best: dict[int, MatchCandidate] = {}
    stages = {item.id: item for item in context.stages}
    for item in context.stages:
        code_match = find_identifier(text, item.code)
        if code_match:
            _keep(best, MatchCandidate(
                item.id, 0, 100, "exact_stage_code", code_match,
                f"Точный машинный код этапа {item.code}", item.is_active,
            ))
        if item.code == "COMPLETED" and normalize_match_text(item.name) == "готово":
            continue
        name_match = find_identifier(text, item.name)
        if name_match:
            _keep(best, MatchCandidate(
                item.id, 0, 90, "exact_stage_name", name_match,
                f"Точное название этапа «{item.name}»", item.is_active,
            ))
    for alias in context.stage_aliases:
        target = stages.get(alias.target_id)
        matched = find_identifier(text, alias.alias_text)
        if matched and target is not None:
            _keep(best, MatchCandidate(
                target.id, 0, 80, "confirmed_stage_alias", matched,
                f"Подтвержденный алиас этапа «{alias.alias_text}»",
                target.is_active and alias.is_active,
            ))
    return _rank(best.values())


def _readiness(text: str):
    normalized = normalize_match_text(text)
    ranges = list(_RANGE_RE.finditer(normalized))
    if ranges:
        evidence = ", ".join(item.group(0) for item in ranges)
        return None, None, evidence, ProposalIssue(
            "readiness_ambiguous", "Диапазон готовности требует решения оператора",
            evidence,
        )
    matches = [(int(item.group(1)), item.group(0), "explicit_percent") for item in _PERCENT_RE.finditer(normalized)]
    matches.extend(
        (int(item.group(1)), item.group(0), "readiness_keyword")
        for item in _READINESS_WORD_RE.finditer(normalized)
    )
    if not matches:
        return None, None, "", None
    evidence = ", ".join(item[1] for item in matches)
    if any(not 0 <= item[0] <= 100 for item in matches):
        return None, None, evidence, ProposalIssue(
            "invalid_readiness", "Готовность должна находиться в диапазоне 0..100",
            evidence,
        )
    values = {item[0] for item in matches}
    if len(values) != 1:
        return None, None, evidence, ProposalIssue(
            "readiness_ambiguous", "Найдено несколько различных значений готовности",
            evidence,
        )
    methods = {item[2] for item in matches}
    method = "explicit_percent" if "explicit_percent" in methods else "readiness_keyword"
    return values.pop(), method, evidence, None


def _missing_description_proposal(bundle: BundleMatchingInput) -> ProposalDraft:
    issue = ProposalIssue(
        "missing_description", "В bundle отсутствует текстовое описание"
    )
    return ProposalDraft(
        0, "", "", None, None, None, None, None, None, None, None,
        None, None, "", "", MatchQuality.NONE, True, issues=(issue,),
    )


def _rank(candidates) -> tuple[MatchCandidate, ...]:
    ordered = sorted(candidates, key=lambda item: (-item.score, item.target_id))
    return tuple(replace(item, rank=index) for index, item in enumerate(ordered, 1))


def _keep(target: dict[int, MatchCandidate], candidate: MatchCandidate) -> None:
    current = target.get(candidate.target_id)
    if current is None or candidate.score > current.score:
        target[candidate.target_id] = candidate


def _unique_top_id(candidates: tuple[MatchCandidate, ...]) -> int | None:
    if not candidates:
        return None
    if len(candidates) > 1 and candidates[0].score == candidates[1].score:
        return None
    return candidates[0].target_id


def _selected_method(candidates, target_id):
    item = next((candidate for candidate in candidates if candidate.target_id == target_id), None)
    return item.method if item else None


def _proposal_fingerprint(proposals: tuple[ProposalDraft, ...]) -> str:
    return _sha(json.dumps(
        [asdict(item) for item in proposals],
        ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"),
    ))


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _diag(kind, row):
    return MatchingDiagnosticIssue(
        kind, kind.value, match_run_id=int(row["match_run_id"]),
        proposal_id=int(row["id"]),
    )
