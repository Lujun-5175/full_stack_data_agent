from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from full_stack_data_agent.app.result_artifacts import ResultArtifact


@dataclass
class ResultWorkspace:
    conversation_id: str | None = None
    turn_id: str | None = None
    artifacts_by_id: dict[str, ResultArtifact] = field(default_factory=dict)
    artifacts_by_type: dict[str, list[str]] = field(default_factory=dict)
    latest_by_type: dict[str, str] = field(default_factory=dict)
    root_artifact_id: str | None = None

    def register_artifact(self, artifact: ResultArtifact) -> ResultArtifact:
        parent = self.resolve_artifact(artifact.parent_artifact_id)
        artifact.ensure_preview()
        if artifact.is_chart_like() and parent is not None:
            artifact.metadata.setdefault("source_artifact_id", parent.artifact_id)
        if parent is not None:
            artifact.lineage = list(parent.lineage) + [artifact.artifact_id]
        elif artifact.lineage:
            artifact.lineage = list(artifact.lineage)
        else:
            artifact.lineage = [artifact.artifact_id]
        if not artifact.available_actions:
            artifact.available_actions = artifact.default_available_actions()
        if artifact.is_dataframe_like() and self.root_artifact_id is None:
            self.root_artifact_id = artifact.artifact_id
        self.artifacts_by_id[artifact.artifact_id] = artifact
        self.artifacts_by_type.setdefault(artifact.artifact_type, []).append(artifact.artifact_id)
        self.latest_by_type[artifact.artifact_type] = artifact.artifact_id
        return artifact

    def resolve_artifact(self, artifact_id: str | None) -> ResultArtifact | None:
        if not artifact_id:
            return None
        return self.artifacts_by_id.get(artifact_id)

    def latest(self, type_name: str) -> ResultArtifact | None:
        return self.resolve_artifact(self.latest_by_type.get(type_name))

    def source_artifact_for(self, artifact: ResultArtifact | None) -> ResultArtifact | None:
        if artifact is None:
            return None
        source_id = artifact.metadata.get("source_artifact_id") or artifact.parent_artifact_id
        source = self.resolve_artifact(str(source_id)) if source_id else None
        return source if source is not None else artifact

    def source_payload_for(self, artifact_id: str | None) -> dict[str, Any] | None:
        artifact = self.resolve_artifact(artifact_id)
        source = self.source_artifact_for(artifact)
        return source.summary_dict() if source is not None else None

    def preferred_analysis_target(self, artifact: ResultArtifact | None) -> ResultArtifact | None:
        if artifact is None:
            return None
        if artifact.is_chart_like():
            return self.source_artifact_for(artifact)
        return artifact

    def chart_object_for(self, artifact: ResultArtifact | None) -> ResultArtifact | None:
        if artifact is None:
            return None
        if artifact.is_chart_like():
            return artifact
        for child_id in reversed(self.artifacts_by_type.get("chart", [])):
            child = self.resolve_artifact(child_id)
            if child is not None and child.parent_artifact_id == artifact.artifact_id:
                return child
        return None

    def find_candidates_for_action(self, action: str) -> list[ResultArtifact]:
        candidates: list[ResultArtifact] = []
        for artifact_id in reversed(list(self.artifacts_by_id.keys())):
            artifact = self.artifacts_by_id[artifact_id]
            if action in artifact.available_actions:
                candidates.append(artifact)
        return candidates

    def artifact_summaries(self) -> list[dict[str, Any]]:
        return [artifact.summary_dict() for artifact in self.artifacts_by_id.values()]

    def artifact_snapshot_map(self) -> dict[str, dict[str, Any]]:
        return {artifact_id: artifact.summary_dict() for artifact_id, artifact in self.artifacts_by_id.items()}

    def to_snapshot(self) -> dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "turn_id": self.turn_id,
            "root_artifact_id": self.root_artifact_id,
            "latest_by_type": dict(self.latest_by_type),
            "artifacts": self.artifact_summaries(),
        }

    def to_dict(self) -> dict[str, Any]:
        return self.to_snapshot()
