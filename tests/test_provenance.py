"""Tests for the W3C PROV provenance graph module."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from repropack.core.manifest import (
    EnvironmentSpec,
    Metadata,
    ReproPackManifest,
    Step,
    StepType,
)
from repropack.core.provenance import ProvenanceGraph

# =====================================================================
# Helpers
# =====================================================================


def _make_manifest(
    with_steps: bool = True,
    with_authors: bool = True,
    with_hashes: bool = True,
) -> ReproPackManifest:
    """Create a sample manifest for provenance testing."""
    metadata = Metadata(
        name="test_experiment",
        authors=["Alice <alice@example.com>"] if with_authors else [],
        description="A test experiment",
    )
    environment = EnvironmentSpec(
        base_image="python:3.11-slim@sha256:abc123",
        python_requirements="requirements.lock",
        system_packages=["build-essential"],
    )
    steps = []
    if with_steps:
        steps = [
            Step(
                id="prepare",
                type=StepType.AUTOMATIC,
                command="python prepare.py",
                inputs=["data/raw/"],
                outputs=["data/processed/"],
            ),
            Step(
                id="train",
                type=StepType.AUTOMATIC,
                command="python train.py",
                inputs=["data/processed/", "configs/model.yml"],
                outputs=["results/model.pkl", "results/metrics.json"],
                depends_on=["prepare"],
            ),
            Step(
                id="review",
                type=StepType.MANUAL,
                description="Review metrics",
                instructions="Verify AUC > 0.85",
            ),
        ]
    return ReproPackManifest(
        metadata=metadata,
        environment=environment,
        steps=steps,
    )


def _make_file_hashes() -> dict[str, str]:
    return {
        "main.py": "a" * 64,
        "train.py": "b" * 64,
        "requirements.lock": "c" * 64,
    }


# =====================================================================
# Graph creation
# =====================================================================


class TestProvenanceGraphCreation:
    """Tests for building the PROV graph."""

    def test_empty_graph(self) -> None:
        """A fresh graph must contain an empty ProvDocument."""
        prov = ProvenanceGraph()
        data = prov.to_dict()
        assert "agent" not in data or data["agent"] == {}

    def test_add_agent(self) -> None:
        """add_agent must create a ProvAgent in the document."""
        prov = ProvenanceGraph()
        agent = prov.add_agent("system", "ReproPack", version="0.1.1")
        assert isinstance(agent, type(agent))  # ProvAgent
        data = prov.to_dict()
        assert "repropack:system" in str(data)

    def test_add_entity(self) -> None:
        """add_entity must create a ProvEntity."""
        prov = ProvenanceGraph()
        prov.add_entity("manifest", "repropack.yml", version="0.1.1")
        data = prov.to_dict()
        assert "repropack:manifest" in str(data)

    def test_add_activity(self) -> None:
        """add_activity must create a ProvActivity with relations."""
        prov = ProvenanceGraph()
        prov.add_agent("system", "System")
        prov.add_entity("input", "data.csv")
        prov.add_entity("output", "model.pkl")
        prov.add_activity(
            "train",
            "train model",
            used=["input"],
            generated=["output"],
            was_associated_with="system",
        )
        data = prov.to_dict()
        assert "repropack:train" in str(data)

    def test_build_from_manifest_basic(self) -> None:
        """build_from_manifest must populate agents, entities, activities."""
        manifest = _make_manifest()
        prov = ProvenanceGraph()
        prov.build_from_manifest(manifest)
        data = prov.to_dict()

        # Agents
        assert "repropack_system" in str(data)
        assert "author_0" in str(data)

        # Core entities
        assert "manifest" in str(data)
        assert "environment" in str(data)
        assert "dockerfile" in str(data)
        assert "python_requirements" in str(data)

        # Activities
        assert "step_prepare" in str(data)
        assert "step_train" in str(data)
        assert "step_review" in str(data)

    def test_build_from_manifest_with_hashes(self) -> None:
        """File hashes must appear as entity attributes."""
        manifest = _make_manifest()
        hashes = _make_file_hashes()
        prov = ProvenanceGraph()
        prov.build_from_manifest(manifest, file_hashes=hashes)
        data = prov.to_dict()
        assert "main.py" in str(data)
        assert "a" * 64 in str(data)

    def test_step_dependencies(self) -> None:
        """Step dependencies must create wasInformedBy relations."""
        manifest = _make_manifest()
        prov = ProvenanceGraph()
        prov.build_from_manifest(manifest)
        data = prov.to_dict()
        # train depends_on prepare
        assert "wasinformedby" in str(data).lower()


# =====================================================================
# Export formats
# =====================================================================


class TestGraphExportFormats:
    """Snapshot-like tests for serialisation outputs."""

    def test_to_json_contains_records(self) -> None:
        """PROV-JSON must contain agent, entity, activity keys."""
        manifest = _make_manifest()
        prov = ProvenanceGraph()
        prov.build_from_manifest(manifest)
        json_str = prov.to_json()
        data = json.loads(json_str)
        assert "agent" in data
        assert "entity" in data
        assert "activity" in data

    def test_to_dict(self) -> None:
        """to_dict must return a Python dict."""
        manifest = _make_manifest()
        prov = ProvenanceGraph()
        prov.build_from_manifest(manifest)
        d = prov.to_dict()
        assert isinstance(d, dict)
        assert "agent" in d

    def test_to_dot_contains_nodes(self) -> None:
        """DOT output must include node declarations."""
        manifest = _make_manifest()
        prov = ProvenanceGraph()
        prov.build_from_manifest(manifest)
        dot = prov.to_dot()
        assert "digraph" in dot.lower()
        assert "repropack_system" in dot

    def test_to_mermaid_contains_graph(self) -> None:
        """Mermaid output must start with 'graph TD'."""
        manifest = _make_manifest()
        prov = ProvenanceGraph()
        prov.build_from_manifest(manifest)
        mmd = prov.to_mermaid()
        assert "graph TD" in mmd
        assert "step_prepare" in mmd
        assert "step_train" in mmd

    def test_to_html_contains_mermaid(self) -> None:
        """HTML must contain the Mermaid script and diagram."""
        manifest = _make_manifest()
        prov = ProvenanceGraph()
        prov.build_from_manifest(manifest)
        html = prov.to_html(title="Test Provenance")
        assert "mermaid" in html
        assert "Test Provenance" in html
        assert "graph TD" in html

    def test_to_png_raises_without_graphviz(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """to_png must raise RuntimeError when graphviz is missing."""
        import builtins

        real_import = builtins.__import__

        def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "graphviz":
                raise ImportError("No module named 'graphviz'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)

        prov = ProvenanceGraph()
        with pytest.raises(RuntimeError, match="graphviz Python package"):
            prov.to_png(Path("/tmp/out.png"))

    def test_save(self, tmp_path: Path) -> None:
        """Save must write valid PROV-JSON to disk."""
        manifest = _make_manifest()
        prov = ProvenanceGraph()
        prov.build_from_manifest(manifest)
        path = tmp_path / "provenance.json"
        prov.save(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert "agent" in data


# =====================================================================
# Integrity
# =====================================================================


class TestProvenanceIntegrity:
    """Verify that every manifest artifact is represented in the graph."""

    def test_all_steps_present(self) -> None:
        """Every step in the manifest must have a corresponding activity."""
        manifest = _make_manifest()
        prov = ProvenanceGraph()
        prov.build_from_manifest(manifest)
        data = prov.to_dict()
        for step in manifest.steps:
            assert f"step_{step.id}" in str(data)

    def test_all_inputs_and_outputs_present(self) -> None:
        """Every input/output of every step must have an entity."""
        manifest = _make_manifest()
        prov = ProvenanceGraph()
        prov.build_from_manifest(manifest)
        data = prov.to_dict()
        for step in manifest.steps:
            for inp in step.inputs:
                safe = prov._safe_id(f"input_{step.id}_{inp}")
                assert safe in str(data)
            for out in step.outputs:
                safe = prov._safe_id(f"output_{step.id}_{out}")
                assert safe in str(data)

    def test_all_file_hashes_present(self) -> None:
        """Every file hash must produce an entity in the graph."""
        manifest = _make_manifest()
        hashes = _make_file_hashes()
        prov = ProvenanceGraph()
        prov.build_from_manifest(manifest, file_hashes=hashes)
        data = prov.to_dict()
        for rel_path in hashes:
            safe = prov._safe_id(f"file_{rel_path}")
            assert safe in str(data)

    def test_authors_as_agents(self) -> None:
        """Each author must become an agent."""
        manifest = _make_manifest(with_authors=True)
        prov = ProvenanceGraph()
        prov.build_from_manifest(manifest)
        data = prov.to_dict()
        assert "author_0" in str(data)

    def test_no_authors_when_empty(self) -> None:
        """If authors list is empty, no author agents should be added."""
        manifest = _make_manifest(with_authors=False)
        prov = ProvenanceGraph()
        prov.build_from_manifest(manifest)
        data = prov.to_dict()
        assert "author_0" not in str(data)


# =====================================================================
# NetworkX
# =====================================================================


class TestNetworkXGraph:
    """Tests for the NetworkX DiGraph export."""

    def test_build_nx_graph_returns_digraph(self) -> None:
        """build_nx_graph must return a nx.DiGraph."""
        manifest = _make_manifest()
        prov = ProvenanceGraph()
        prov.build_from_manifest(manifest)
        g = prov.build_nx_graph()
        assert isinstance(g, type(g))  # nx.DiGraph
        assert len(g.nodes) > 0

    def test_nx_graph_contains_nodes_and_edges(self) -> None:
        """The NetworkX graph must contain nodes and edges."""
        manifest = _make_manifest()
        prov = ProvenanceGraph()
        prov.build_from_manifest(manifest)
        g = prov.build_nx_graph()
        assert "repropack_system" in g.nodes
        assert "step_prepare" in g.nodes
        assert len(g.edges) > 0

    def test_nx_graph_node_labels(self) -> None:
        """Nodes must carry 'label' and 'type' attributes."""
        manifest = _make_manifest()
        prov = ProvenanceGraph()
        prov.build_from_manifest(manifest)
        g = prov.build_nx_graph()
        for _, attrs in g.nodes(data=True):
            assert "type" in attrs
            assert "label" in attrs


# =====================================================================
# Edge cases
# =====================================================================


class TestEdgeCases:
    """Edge cases for provenance handling."""

    def test_empty_manifest(self) -> None:
        """An empty manifest must still produce core entities."""
        manifest = _make_manifest(with_steps=False)
        prov = ProvenanceGraph()
        prov.build_from_manifest(manifest)
        data = prov.to_dict()
        assert "manifest" in str(data)
        assert "environment" in str(data)
        assert "dockerfile" in str(data)

    def test_no_python_requirements(self) -> None:
        """If no python_requirements, entity should not be created."""
        manifest = _make_manifest()
        manifest.environment.python_requirements = None
        prov = ProvenanceGraph()
        prov.build_from_manifest(manifest)
        data = prov.to_dict()
        assert "python_requirements" not in str(data)

    def test_safe_id_sanitization(self) -> None:
        """_safe_id must replace special characters."""
        prov = ProvenanceGraph()
        assert prov._safe_id("a/b.c-d") == "a_b_c_d"
