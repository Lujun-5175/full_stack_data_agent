from __future__ import annotations

import re
from typing import Any

from databao.agent.sql.query_frame import QueryFrame, SemanticFilter
from databao.agent.sql.semantic_schema import SemanticColumnSummary, SemanticSchemaInventory


def _clean_identifier(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]+", "_", str(value or "").strip()).strip("_").lower()


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys([value for value in values if value]))


def _column_matches_target(column: SemanticColumnSummary, target: str) -> float:
    normalized_target = _clean_identifier(target)
    normalized_column = _clean_identifier(column.column_name)
    score = 0.0
    if normalized_target == normalized_column:
        score += 3.0
    if normalized_target in normalized_column:
        score += 1.5
    if column.entity_like and normalized_target in {"customer", "order", "product", "contract", "region", "market", "segment"}:
        score += 1.2
    if column.groupable:
        score += 0.8
    if column.id_like:
        score += 0.3
    if "label_like" in column.role_tags:
        score += 0.8
    return score


def _best_group_column(target: str, inventory: SemanticSchemaInventory) -> SemanticColumnSummary | None:
    ranked = sorted(inventory.columns, key=lambda column: (_column_matches_target(column, target), column.column_name), reverse=True)
    return ranked[0] if ranked and _column_matches_target(ranked[0], target) > 0 else None


def _best_value_column(filter_spec: SemanticFilter, inventory: SemanticSchemaInventory) -> tuple[SemanticColumnSummary | None, str | None, float]:
    normalized_value = str(filter_spec.value or "").strip().lower()
    best: tuple[SemanticColumnSummary | None, str | None, float] = (None, None, 0.0)
    for column in inventory.columns:
        score = 0.0
        if filter_spec.entity and _clean_identifier(filter_spec.entity) == _clean_identifier(column.column_name):
            score += 2.0
        if filter_spec.semantic_role == "status" and "status_like" in column.role_tags:
            score += 2.0
        if filter_spec.semantic_role in {"category", "segment"} and "categorical_filter_candidate" in column.role_tags:
            score += 1.2
        for sample in column.sample_values:
            sample_normalized = sample.strip().lower()
            if sample_normalized == normalized_value:
                score += 3.5
                if score > best[2]:
                    best = (column, sample_normalized, score)
            elif sample_normalized.replace("_", " ") == normalized_value.replace("_", " "):
                score += 2.5
                if score > best[2]:
                    best = (column, sample_normalized, score)
        if normalized_value and normalized_value in _clean_identifier(column.column_name):
            score += 0.5
        if score > best[2]:
            best = (column, normalized_value, score)
    return best


def _metric_name(kind: str, source_column: str | None, fallback_target: str | None) -> str:
    if kind == "count":
        normalized_target = _clean_identifier(fallback_target or "")
        if normalized_target in {"order", "orders"}:
            return "count_orders"
        return f"count_{_clean_identifier(source_column or fallback_target or 'rows')}"
    if kind == "avg":
        return f"avg_{_clean_identifier(source_column or fallback_target or 'metric')}"
    if kind == "ratio":
        return _clean_identifier(source_column or fallback_target or "rate_metric")
    return _clean_identifier(source_column or fallback_target or "metric")


def _best_metric_source_column(metric_kind: str, metric_target: str | None, inventory: SemanticSchemaInventory) -> str | None:
    normalized_target = _clean_identifier(metric_target or "")
    if not normalized_target:
        return None
    if metric_kind == "count" and normalized_target in {"orders", "order"}:
        for column in inventory.columns:
            normalized_column = _clean_identifier(column.column_name)
            if "order" in normalized_column and (column.id_like or column.entity_like):
                return column.column_name
    source_match = _best_group_column(normalized_target, inventory)
    return source_match.column_name if source_match is not None else None


def bind_query_frame_to_obligations(
    frame: QueryFrame,
    inventory: SemanticSchemaInventory,
    *,
    obligations_factory: Any,
    filter_factory: Any,
    metric_factory: Any,
    derived_metric_factory: Any,
    sort_factory: Any,
) -> Any:
    filters = []
    group_by: list[str] = []
    metrics = []
    derived_metrics = []
    sort = []
    required_output_columns: list[str] = []
    binding_evidence: list[dict[str, Any]] = []
    obligation_tiers: dict[str, str] = {}
    provenance = {"semantic_frame": frame.to_dict(), "binding_strategy": "semantic_binder"}

    for group in frame.semantic_groups:
        column = _best_group_column(group.target, inventory)
        bound_group = column.column_name if column is not None else _clean_identifier(group.target)
        group_by.append(bound_group)
        obligation_tiers[f"group_by:{bound_group.lower()}"] = "critical"
        binding_evidence.append({"kind": "group_by", "semantic_target": group.target, "bound_column": bound_group, "confidence": group.confidence})

    for semantic_filter in frame.semantic_filters:
        column, grounded_value, confidence = _best_value_column(semantic_filter, inventory)
        if column is None or grounded_value is None:
            continue
        filters.append(filter_factory(column=column.column_name, op=semantic_filter.operator, value=grounded_value, stage="where"))
        obligation_tiers[f"filter:{column.column_name.lower()}"] = "critical"
        binding_evidence.append(
            {
                "kind": "filter",
                "semantic_role": semantic_filter.semantic_role,
                "semantic_value": semantic_filter.value,
                "bound_column": column.column_name,
                "bound_value": grounded_value,
                "confidence": max(semantic_filter.confidence, confidence / 5.0),
            }
        )

    metric_name_map: dict[str, str] = {}
    for metric in frame.semantic_metrics:
        if metric.kind == "ratio" or (metric.name and "rate" in metric.name):
            derived_name = _clean_identifier(metric.name or metric.target or "rate_metric")
            derived_metrics.append(derived_metric_factory(name=derived_name, numerator="numerator_metric", denominator="denominator_metric", expression_kind="ratio"))
            obligation_tiers[f"derived_metric:{derived_name.lower()}"] = "critical"
            required_output_columns.append(derived_name)
            metric_name_map[metric.name or metric.target or derived_name] = derived_name
            continue

        source_column = _best_metric_source_column(metric.kind, metric.target, inventory)
        name = _clean_identifier(metric.name) if metric.name else _metric_name(metric.kind, source_column, metric.target)
        metrics.append(metric_factory(name=name, kind=metric.kind, source_column=source_column))
        obligation_tiers[f"metric:{name.lower()}"] = "critical"
        required_output_columns.append(name)
        metric_name_map[metric.name or metric.target or name] = name
        binding_evidence.append({"kind": "metric", "semantic_kind": metric.kind, "semantic_target": metric.target, "bound_metric": name, "source_column": source_column, "confidence": metric.confidence})

    if frame.ranking_intent is not None:
        ranking_target = metric_name_map.get(frame.ranking_intent.target) or _clean_identifier(frame.ranking_intent.target)
        if ranking_target:
            sort.append(sort_factory(column=ranking_target, direction=frame.ranking_intent.direction))
            obligation_tiers["sort"] = "critical"
            binding_evidence.append({"kind": "sort", "semantic_target": frame.ranking_intent.target, "bound_column": ranking_target, "direction": frame.ranking_intent.direction, "confidence": frame.ranking_intent.confidence})

    if frame.limit is not None:
        obligation_tiers["limit"] = "critical"

    for column in group_by:
        obligation_tiers.setdefault(f"output_column:{column.lower()}", "important")
        required_output_columns.append(column)
    for deliverable in frame.deliverables:
        obligation_tiers[f"deliverable:{deliverable}"] = "optional" if deliverable in {"chart", "explain"} else "important"

    required_literals = frame.time_scope.literals if frame.time_scope is not None else []
    for literal in required_literals:
        obligation_tiers[f"literal:{literal.lower()}"] = "critical"

    preferred_tables = list(inventory.preferred_tables)
    candidate_tables = list(inventory.candidate_tables)
    base_tables = list(preferred_tables)
    if not base_tables and frame.entities:
        for table in inventory.tables:
            if any(entity in table.table_name for entity in frame.entities) and "fact_like" in table.role_tags:
                base_tables.append(table.table_name)
    if frame.time_scope is not None and not base_tables:
        base_tables = list(preferred_tables[:1])

    return obligations_factory(
        base_tables=base_tables or None,
        preferred_tables=preferred_tables,
        candidate_tables=candidate_tables,
        required_literals=required_literals,
        filters=filters,
        group_by=_dedupe(group_by),
        metrics=metrics,
        derived_metrics=derived_metrics,
        post_filters=[],
        sort=sort,
        limit=frame.limit,
        required_output_columns=_dedupe([_clean_identifier(item) for item in required_output_columns]),
        deliverables=list(frame.deliverables),
        chart_requirements=None,
        notes=[note.message for note in frame.notes] + list(frame.ambiguity),
        semantic_frame=frame.to_dict(),
        binding_evidence=binding_evidence,
        obligation_tiers=obligation_tiers,
        provenance=provenance,
        confidence=frame.confidence,
    )
