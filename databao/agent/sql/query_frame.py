from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from databao.agent.sql.semantic_schema import SemanticSchemaInventory


def _clean_identifier(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]+", "_", str(value or "").strip()).strip("_").lower()


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys([value for value in values if value]))


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    lowered = str(text or "").lower()
    return any(marker in lowered for marker in markers)


@dataclass(frozen=True)
class SemanticFilter:
    semantic_role: str
    value: str
    entity: str | None = None
    operator: str = "="
    confidence: float = 0.0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SemanticMetric:
    kind: str
    target: str | None = None
    name: str | None = None
    confidence: float = 0.0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SemanticGroup:
    target: str
    confidence: float = 0.0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RankingIntent:
    direction: str
    target: str
    confidence: float = 0.0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TimeScopeIntent:
    literals: list[str] = field(default_factory=list)
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FrameNote:
    kind: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class QueryFrame:
    intent: str
    entities: list[str] = field(default_factory=list)
    semantic_filters: list[SemanticFilter] = field(default_factory=list)
    semantic_groups: list[SemanticGroup] = field(default_factory=list)
    semantic_metrics: list[SemanticMetric] = field(default_factory=list)
    ranking_intent: RankingIntent | None = None
    limit: int | None = None
    time_scope: TimeScopeIntent | None = None
    deliverables: list[str] = field(default_factory=list)
    ambiguity: list[str] = field(default_factory=list)
    confidence: float = 0.0
    notes: list[FrameNote] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "entities": list(self.entities),
            "semantic_filters": [item.to_dict() for item in self.semantic_filters],
            "semantic_groups": [item.to_dict() for item in self.semantic_groups],
            "semantic_metrics": [item.to_dict() for item in self.semantic_metrics],
            "ranking_intent": self.ranking_intent.to_dict() if self.ranking_intent is not None else None,
            "limit": self.limit,
            "time_scope": self.time_scope.to_dict() if self.time_scope is not None else None,
            "deliverables": list(self.deliverables),
            "ambiguity": list(self.ambiguity),
            "confidence": self.confidence,
            "notes": [item.to_dict() for item in self.notes],
        }


def _detect_entities(query: str, inventory: SemanticSchemaInventory) -> list[str]:
    lowered = query.lower()
    entities: list[str] = []
    for token in ("customer", "customers", "order", "orders", "product", "products", "region", "market", "segment", "contract", "payment", "payments", "revenue", "sales"):
        if token in lowered:
            entities.append(token[:-1] if token.endswith("s") else token)
    for table in inventory.candidate_tables:
        if table in lowered:
            entities.append(table)
    return _dedupe(entities)


def _detect_groups(query: str, inventory: SemanticSchemaInventory) -> list[SemanticGroup]:
    text = query.lower()
    groups: list[SemanticGroup] = []
    for raw, normalized in {
        "customers": "customer",
        "customer": "customer",
        "orders": "order",
        "regions": "region",
        "region": "region",
        "market": "market",
        "segment": "segment",
        "contract": "contract",
    }.items():
        if re.search(rf"\b{re.escape(raw)}\b", text) and re.search(r"\b(top|bottom|highest|lowest|most|least|per|by|for each)\b", text):
            groups.append(SemanticGroup(target=normalized, confidence=0.8, notes=["derived from ranking/group phrasing"]))
    for match in re.finditer(r"(?:group(?:ed)? by|per|by|for each|按|按照|根据)\s+([a-zA-Z_][\w]*)", query, flags=re.IGNORECASE):
        groups.append(SemanticGroup(target=_clean_identifier(match.group(1)), confidence=0.95, notes=["explicit grouping marker"]))
    if not groups:
        for column in inventory.columns:
            if column.groupable and column.entity_like and re.search(rf"\b{re.escape(_clean_identifier(column.column_name))}\b", text):
                groups.append(SemanticGroup(target=_clean_identifier(column.column_name), confidence=0.65))
    return list({group.target: group for group in groups}.values())


def _detect_filters(query: str, inventory: SemanticSchemaInventory) -> list[SemanticFilter]:
    lowered = query.lower()
    filters: list[SemanticFilter] = []
    for value in ("active", "churned", "premium", "delivered", "cancelled", "completed"):
        if re.search(rf"\b{re.escape(value)}\b", lowered):
            filters.append(SemanticFilter(semantic_role="status", value=value, confidence=0.8, notes=["common semantic value detected"]))
    for column in inventory.columns:
        for sample in column.sample_values:
            normalized_sample = sample.strip().lower()
            if normalized_sample and re.search(rf"\b{re.escape(normalized_sample)}\b", lowered):
                role = "status" if "status_like" in column.role_tags else "category"
                filters.append(
                    SemanticFilter(
                        semantic_role=role,
                        entity=_clean_identifier(column.column_name),
                        value=normalized_sample,
                        confidence=0.9,
                        notes=["matched schema sample value"],
                    )
                )
    for match in re.finditer(r"([A-Za-z_][\w]*)\s*(=|>=|<=|>|<)\s*['\"]?([A-Za-z0-9_\-\. ]+)['\"]?", query, flags=re.IGNORECASE):
        filters.append(
            SemanticFilter(
                semantic_role=_clean_identifier(match.group(1)),
                entity=_clean_identifier(match.group(1)),
                operator=match.group(2),
                value=match.group(3).strip().lower(),
                confidence=0.95,
                notes=["explicit predicate"],
            )
        )
    keyed: dict[tuple[str, str, str], SemanticFilter] = {}
    for item in filters:
        keyed[(item.semantic_role, item.entity or "", item.value)] = item
    return list(keyed.values())


def _detect_metrics(query: str) -> tuple[list[SemanticMetric], RankingIntent | None]:
    lowered = query.lower()
    metrics: list[SemanticMetric] = []
    if re.search(r"\b(number of|count|how many|most orders)\b", lowered):
        metrics.append(SemanticMetric(kind="count", target="orders", name="count_orders", confidence=0.9))
    if re.search(r"\baverage revenue\b", lowered):
        metrics.append(SemanticMetric(kind="avg", target="revenue", name="avg_revenue", confidence=0.95))
    elif re.search(r"\baverage\b", lowered):
        match = re.search(r"\baverage\s+([a-zA-Z_][\w]*)", query, flags=re.IGNORECASE)
        metrics.append(SemanticMetric(kind="avg", target=_clean_identifier(match.group(1)) if match else None, confidence=0.8))
    if re.search(r"\bchurn rate\b", lowered):
        metrics.append(SemanticMetric(kind="ratio", target="churn", name="churn_rate", confidence=0.95))
    if re.search(r"\brevenue\b", lowered) and not any(metric.target == "revenue" for metric in metrics):
        metrics.append(SemanticMetric(kind="value", target="revenue", name="revenue", confidence=0.5))
    if re.search(r"\borders\b", lowered) and not any(metric.target == "orders" for metric in metrics):
        metrics.append(SemanticMetric(kind="count", target="orders", name="count_orders", confidence=0.65))
    for block in re.findall(r"(?:compute|calculate|show|provide|include|return|统计|计算|展示|给出)\s+([A-Za-z_][\w]*(?:\s*(?:,|and|、|和|及|与)\s*[A-Za-z_][\w]*)*)", query, flags=re.IGNORECASE):
        for raw_name in re.split(r"\s*(?:,|and|、|和|及|与)\s*", block):
            normalized = _clean_identifier(raw_name)
            if not normalized:
                continue
            if "rate" in normalized or "ratio" in normalized:
                metrics.append(SemanticMetric(kind="ratio", target=normalized, name=normalized, confidence=0.85))
            elif not any(metric.name == normalized for metric in metrics):
                metrics.append(SemanticMetric(kind="value", target=normalized, name=normalized, confidence=0.7))

    ranking: RankingIntent | None = None
    if re.search(r"\b(highest|top|most)\b", lowered):
        target = metrics[-1].name or metrics[-1].target or "metric" if metrics else "metric"
        ranking = RankingIntent(direction="desc", target=target, confidence=0.85)
    elif re.search(r"\b(lowest|least|bottom)\b", lowered):
        target = metrics[-1].name or metrics[-1].target or "metric" if metrics else "metric"
        ranking = RankingIntent(direction="asc", target=target, confidence=0.85)
    return metrics, ranking


def _detect_limit(query: str) -> int | None:
    lowered = query.lower()
    for pattern in (r"\btop\s+(\d+)\b", r"\blimit\s+(\d+)\b", r"前\s*(\d+)"):
        match = re.search(pattern, lowered)
        if match:
            return int(match.group(1))
    return None


def _detect_deliverables(query: str) -> list[str]:
    lowered = query.lower()
    deliverables: list[str] = []
    if _contains_any(lowered, ("chart", "plot", "graph", "图", "画图", "箱线图", "bar chart")):
        deliverables.append("chart")
    if _contains_any(lowered, ("explain", "interpret", "explanation", "解释", "说明")):
        deliverables.append("explain")
    if _contains_any(lowered, ("table", "tabular", "表格")):
        deliverables.append("grouped_table")
    if not deliverables:
        deliverables.append("grouped_table")
    return _dedupe(deliverables)


def build_query_frame_baseline(query: str, inventory: SemanticSchemaInventory) -> QueryFrame:
    metrics, ranking = _detect_metrics(query)
    year_literals = re.findall(r"\b(?:19|20)\d{2}\b", query)
    notes: list[FrameNote] = []
    if re.search(r"\b(top|highest|lowest|most|least)\b", query.lower()) and ranking is None:
        notes.append(FrameNote(kind="ranking", message="ranking language detected without a confident target"))
    return QueryFrame(
        intent="aggregate" if metrics or _detect_groups(query, inventory) else "select",
        entities=_detect_entities(query, inventory),
        semantic_filters=_detect_filters(query, inventory),
        semantic_groups=_detect_groups(query, inventory),
        semantic_metrics=metrics,
        ranking_intent=ranking,
        limit=_detect_limit(query),
        time_scope=TimeScopeIntent(literals=year_literals, confidence=0.95) if year_literals else None,
        deliverables=_detect_deliverables(query),
        ambiguity=[],
        confidence=0.7,
        notes=notes,
    )


def _llm_prompt(query: str, inventory: SemanticSchemaInventory) -> str:
    return (
        "Build a constrained semantic query frame for a SQL assistant.\n"
        "Use only semantic targets, not final schema bindings.\n"
        "Return JSON only with keys: intent, entities, semantic_filters, semantic_groups, semantic_metrics, ranking_intent, limit, time_scope, deliverables, ambiguity, confidence.\n"
        f"Query: {query}\n"
        f"Semantic schema inventory: {json.dumps(inventory.compact_summary(), ensure_ascii=False)}"
    )


def _parse_llm_frame(payload: Any, fallback: QueryFrame) -> QueryFrame:
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        raise ValueError("LLM frame payload must be a JSON object")
    filters = [
        SemanticFilter(
            semantic_role=str(item.get("semantic_role") or item.get("role") or ""),
            value=str(item.get("value") or ""),
            entity=str(item.get("entity") or "") or None,
            operator=str(item.get("operator") or "="),
            confidence=float(item.get("confidence", 0.7)),
            notes=[str(note) for note in (item.get("notes") or [])],
        )
        for item in (payload.get("semantic_filters") or [])
        if isinstance(item, dict) and str(item.get("semantic_role") or item.get("role") or "").strip() and str(item.get("value") or "").strip()
    ]
    groups = [
        SemanticGroup(
            target=str(item.get("target") or item.get("name") or ""),
            confidence=float(item.get("confidence", 0.7)),
            notes=[str(note) for note in (item.get("notes") or [])],
        )
        for item in (payload.get("semantic_groups") or [])
        if isinstance(item, dict) and str(item.get("target") or item.get("name") or "").strip()
    ]
    metrics = [
        SemanticMetric(
            kind=str(item.get("kind") or ""),
            target=str(item.get("target") or "") or None,
            name=str(item.get("name") or "") or None,
            confidence=float(item.get("confidence", 0.7)),
            notes=[str(note) for note in (item.get("notes") or [])],
        )
        for item in (payload.get("semantic_metrics") or [])
        if isinstance(item, dict) and str(item.get("kind") or "").strip()
    ]
    ranking_payload = payload.get("ranking_intent")
    ranking = None
    if isinstance(ranking_payload, dict) and str(ranking_payload.get("target") or "").strip():
        ranking = RankingIntent(
            direction=str(ranking_payload.get("direction") or "desc"),
            target=str(ranking_payload.get("target")),
            confidence=float(ranking_payload.get("confidence", 0.7)),
            notes=[str(note) for note in (ranking_payload.get("notes") or [])],
        )
    time_payload = payload.get("time_scope")
    time_scope = None
    if isinstance(time_payload, dict):
        literals = [str(item) for item in (time_payload.get("literals") or []) if str(item).strip()]
        if literals:
            time_scope = TimeScopeIntent(literals=literals, confidence=float(time_payload.get("confidence", 0.7)))
    return QueryFrame(
        intent=str(payload.get("intent") or fallback.intent),
        entities=_dedupe([str(item) for item in (payload.get("entities") or fallback.entities) if str(item).strip()]),
        semantic_filters=filters or fallback.semantic_filters,
        semantic_groups=groups or fallback.semantic_groups,
        semantic_metrics=metrics or fallback.semantic_metrics,
        ranking_intent=ranking or fallback.ranking_intent,
        limit=int(payload["limit"]) if payload.get("limit") is not None else fallback.limit,
        time_scope=time_scope or fallback.time_scope,
        deliverables=_dedupe([str(item) for item in (payload.get("deliverables") or fallback.deliverables) if str(item).strip()]) or fallback.deliverables,
        ambiguity=[str(item) for item in (payload.get("ambiguity") or fallback.ambiguity)],
        confidence=float(payload.get("confidence", fallback.confidence)),
        notes=fallback.notes,
    )


def build_query_frame(
    query: str,
    inventory: SemanticSchemaInventory,
    *,
    llm_call: Callable[[str], str] | None = None,
) -> QueryFrame:
    baseline = build_query_frame_baseline(query, inventory)
    if llm_call is None:
        return baseline
    try:
        return _parse_llm_frame(llm_call(_llm_prompt(query, inventory)), baseline)
    except Exception:
        return baseline
