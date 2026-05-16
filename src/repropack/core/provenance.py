"""W3C PROV provenance graph using the prov library and NetworkX."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import networkx as nx
from prov.dot import prov_to_dot
from prov.model import (
    Namespace,
    ProvActivity,
    ProvAgent,
    ProvDocument,
    ProvEntity,
    QualifiedName,
)

from repropack.core.manifest import ReproPackManifest

NAMESPACE = Namespace("repropack", "http://repropack.org/ns/prov#")
NS_URI = NAMESPACE.uri


class ProvenanceGraph:
    """W3C PROV provenance graph for a ReproPack experiment."""

    def __init__(self) -> None:
        """Initialize an empty PROV document."""
        self.doc = ProvDocument()
        self.doc.add_namespace(NAMESPACE)
        self._nx_graph: nx.DiGraph | None = None

    def _qname(self, local: str) -> QualifiedName:
        """Generate a QualifiedName in the repropack namespace."""
        return QualifiedName(NAMESPACE, local)

    def add_agent(self, agent_id: str, name: str, **attrs: Any) -> ProvAgent:
        """Add an agent (person, software, organization)."""
        attributes: dict[Any, Any] = {self._qname("name"): name}
        for k, v in attrs.items():
            attributes[self._qname(k)] = v
        agent = self.doc.agent(self._qname(agent_id), attributes)
        return agent

    def add_entity(self, entity_id: str, name: str, **attrs: Any) -> ProvEntity:
        """Add an entity (file, dataset, model)."""
        attributes: dict[Any, Any] = {self._qname("name"): name}
        for k, v in attrs.items():
            attributes[self._qname(k)] = v
        entity = self.doc.entity(self._qname(entity_id), attributes)
        return entity

    def add_activity(
        self,
        activity_id: str,
        name: str,
        used: list[str] | None = None,
        generated: list[str] | None = None,
        was_associated_with: str | None = None,
        **attrs: Any,
    ) -> ProvActivity:
        """Add an activity (reproduction step)."""
        attributes: dict[Any, Any] = {self._qname("name"): name}
        for k, v in attrs.items():
            attributes[self._qname(k)] = v
        activity = self.doc.activity(
            self._qname(activity_id),
            other_attributes=attributes,
        )
        if used:
            for ent_id in used:
                self.doc.used(self._qname(activity_id), self._qname(ent_id))
        if generated:
            for ent_id in generated:
                self.doc.wasGeneratedBy(self._qname(ent_id), self._qname(activity_id))
        if was_associated_with:
            self.doc.wasAssociatedWith(
                self._qname(activity_id), self._qname(was_associated_with)
            )
        return activity

    def build_from_manifest(self, manifest: ReproPackManifest) -> None:
        """Build the complete graph from a manifest."""
        # Main agent: ReproPack system
        self.add_agent("repropack_system", "ReproPack CLI", version="0.1.0")

        # Agent: experiment author
        if manifest.metadata.authors:
            for idx, author in enumerate(manifest.metadata.authors):
                self.add_agent(f"author_{idx}", author)

        # Entity: manifest
        self.add_entity(
            "manifest",
            "repropack.yml",
            version=manifest.repropack_version,
        )

        # Entity: environment
        self.add_entity(
            "environment",
            "environment",
            base_image=manifest.environment.base_image,
        )

        # Input and output entities for each step
        for step in manifest.steps:
            for inp in step.inputs:
                safe_id = self._safe_id(f"input_{step.id}_{inp}")
                self.add_entity(safe_id, inp, role=f"input_{step.id}")
            for out in step.outputs:
                safe_id = self._safe_id(f"output_{step.id}_{out}")
                self.add_entity(safe_id, out, role=f"output_{step.id}")

        # Activities (steps)
        for step in manifest.steps:
            used = [self._safe_id(f"input_{step.id}_{inp}") for inp in step.inputs]
            generated = [
                self._safe_id(f"output_{step.id}_{out}") for out in step.outputs
            ]
            self.add_activity(
                f"step_{step.id}",
                step.id,
                used=used or None,
                generated=generated or None,
                was_associated_with="repropack_system",
                step_type=step.type.value,
                command=step.command,
            )

    def _safe_id(self, value: str) -> str:
        """Convert a path into a PROV-safe identifier."""
        return value.replace("/", "_").replace("\\", "_").replace(".", "_")

    def to_json(self) -> str:
        """Serialize the PROV document to JSON."""
        import io

        from prov.serializers.provjson import ProvJSONSerializer

        stream = io.StringIO()
        serializer = ProvJSONSerializer(self.doc)
        serializer.serialize(stream=stream, indent=2)
        return stream.getvalue()

    def to_dot(self) -> str:
        """Generate Graphviz DOT representation of the graph."""
        dot = prov_to_dot(self.doc)
        return str(dot.to_string())

    def to_mermaid(self) -> str:
        """Generate a Mermaid diagram of the graph."""
        lines: list[str] = ["graph TD"]
        for record in self.doc.get_records():
            rec_id = record.identifier.localpart if record.identifier else "unknown"
            label = rec_id
            if hasattr(record, "attributes"):
                for attr, val in record.attributes:
                    if attr.localpart == "name":
                        label = str(val)
                        break
            if isinstance(record, ProvActivity):
                lines.append(f'    {rec_id}["⚙️ {label}"]')
            elif isinstance(record, ProvEntity):
                lines.append(f'    {rec_id}["📄 {label}"]')
            elif isinstance(record, ProvAgent):
                lines.append(f'    {rec_id}["👤 {label}"]')
        for record in self.doc.get_records():
            rec_id = record.identifier.localpart if record.identifier else "unknown"
            for attr, val in record.attributes:
                if attr.localpart in ("used", "wasGeneratedBy", "wasAssociatedWith"):
                    target = str(val).replace("repropack:", "")
                    lines.append(f"    {rec_id} --> {target}")
        return "\n".join(lines)

    def to_html(self, title: str = "Provenance Graph") -> str:
        """Generate an HTML page with an embedded Mermaid diagram."""
        mermaid_code = self.to_mermaid()
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>{title}</title>
    <script type="module">
        import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs';
        mermaid.initialize({{ startOnLoad: true }});
    </script>
    <style>
        body {{ font-family: sans-serif; margin: 2rem; }}
        pre {{
            background: #f5f5f5;
            padding: 1rem;
            border-radius: 8px;
            overflow: auto;
        }}
    </style>
</head>
<body>
    <h1>{title}</h1>
    <pre class="mermaid">
{mermaid_code}
    </pre>
</body>
</html>
"""

    def save(self, path: Path) -> None:
        """Save the graph in JSON format."""
        path.write_text(self.to_json(), encoding="utf-8")

    def build_nx_graph(self) -> nx.DiGraph:
        """Build a NetworkX DiGraph from the PROV document."""
        g = nx.DiGraph()
        for record in self.doc.get_records():
            rec_id = record.identifier.localpart if record.identifier else "unknown"
            g.add_node(rec_id, type=type(record).__name__)
        # Use PROV relations for edges
        for bundle in [self.doc] + list(self.doc.bundles.values()):
            for record in bundle.get_records():
                rec_id = record.identifier.localpart if record.identifier else "unknown"
                for attr, val in record.attributes:
                    if attr.localpart in ("used", "wasGeneratedBy"):
                        target = str(val).replace(f"{NAMESPACE.prefix}:", "")
                        g.add_edge(rec_id, target)
        self._nx_graph = g
        return g
