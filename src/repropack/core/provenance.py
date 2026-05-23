"""W3C PROV provenance graph using the prov library and NetworkX.

This module provides a complete provenance model for ReproPack experiments,
including agents (authors, system), activities (reproduction steps), and
entities (files, manifests, lockfiles, Dockerfiles, environments).
"""

from __future__ import annotations

import json
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
NS_PREFIX = NAMESPACE.prefix


class ProvenanceGraph:
    """W3C PROV provenance graph for a ReproPack experiment.

    The graph captures:
    - **Agents**: ReproPack CLI, experiment authors.
    - **Entities**: manifest, Dockerfile, lockfiles, project files, step inputs/outputs.
    - **Activities**: each reproduction step (automatic or manual).
    - **Relations**: ``used``, ``wasGeneratedBy``, ``wasAssociatedWith``,
      ``wasInformedBy`` (step dependencies).
    """

    def __init__(self) -> None:
        """Initialize an empty PROV document."""
        self.doc = ProvDocument()
        self.doc.add_namespace(NAMESPACE)
        self._nx_graph: nx.DiGraph | None = None

    def _qname(self, local: str) -> QualifiedName:
        """Generate a QualifiedName in the repropack namespace."""
        return QualifiedName(NAMESPACE, local)

    def add_agent(self, agent_id: str, name: str, **attrs: Any) -> ProvAgent:
        """Add an agent (person, software, organization).

        Args:
            agent_id: Local identifier (no namespace prefix needed).
            name: Human-readable name.
            **attrs: Additional attributes.

        Returns:
            The created ProvAgent.
        """
        attributes: dict[Any, Any] = {self._qname("name"): name}
        for k, v in attrs.items():
            attributes[self._qname(k)] = v
        return self.doc.agent(self._qname(agent_id), attributes)

    def add_entity(self, entity_id: str, name: str, **attrs: Any) -> ProvEntity:
        """Add an entity (file, dataset, model, manifest, etc.).

        Args:
            entity_id: Local identifier.
            name: Human-readable name.
            **attrs: Additional attributes.

        Returns:
            The created ProvEntity.
        """
        attributes: dict[Any, Any] = {self._qname("name"): name}
        for k, v in attrs.items():
            attributes[self._qname(k)] = v
        return self.doc.entity(self._qname(entity_id), attributes)

    def add_activity(
        self,
        activity_id: str,
        name: str,
        used: list[str] | None = None,
        generated: list[str] | None = None,
        was_associated_with: str | None = None,
        was_informed_by: list[str] | None = None,
        **attrs: Any,
    ) -> ProvActivity:
        """Add an activity (reproduction step).

        Args:
            activity_id: Local identifier.
            name: Human-readable name.
            used: IDs of entities consumed by this activity.
            generated: IDs of entities produced by this activity.
            was_associated_with: ID of the agent responsible.
            was_informed_by: IDs of preceding activities (step dependencies).
            **attrs: Additional attributes.

        Returns:
            The created ProvActivity.
        """
        attributes: dict[Any, Any] = {self._qname("name"): name}
        for k, v in attrs.items():
            attributes[self._qname(k)] = v
        activity = self.doc.activity(
            self._qname(activity_id), other_attributes=attributes
        )

        if used:
            for ent_id in used:
                self.doc.used(self._qname(activity_id), self._qname(ent_id))
        if generated:
            for ent_id in generated:
                self.doc.wasGeneratedBy(self._qname(ent_id), self._qname(activity_id))
        if was_associated_with:
            self.doc.wasAssociatedWith(
                self._qname(activity_id),
                self._qname(was_associated_with),
            )
        if was_informed_by:
            for prev_id in was_informed_by:
                self.doc.wasInformedBy(self._qname(activity_id), self._qname(prev_id))

        return activity

    def build_from_manifest(
        self,
        manifest: ReproPackManifest,
        file_hashes: dict[str, str] | None = None,
    ) -> None:
        """Build the complete provenance graph from a manifest.

        Args:
            manifest: The experiment manifest.
            file_hashes: Optional mapping of relative file paths to SHA256
                hashes (injected into entity attributes).
        """
        # --- Agents -----------------------------------------------------
        self.add_agent("repropack_system", "ReproPack CLI", version="0.1.0")

        if manifest.metadata.authors:
            for idx, author in enumerate(manifest.metadata.authors):
                self.add_agent(f"author_{idx}", author)

        # --- Core entities ---------------------------------------------
        self.add_entity(
            "manifest",
            "repropack.yml",
            version=manifest.repropack_version,
        )
        self.add_entity(
            "environment",
            "environment",
            base_image=manifest.environment.base_image,
        )
        self.add_entity(
            "dockerfile",
            "Dockerfile",
            role="container_definition",
        )

        if manifest.environment.python_requirements:
            self.add_entity(
                "python_requirements",
                manifest.environment.python_requirements,
                role="python_lockfile",
            )
        if manifest.environment.conda_environment:
            self.add_entity(
                "conda_environment",
                manifest.environment.conda_environment,
                role="conda_lockfile",
            )

        # --- Project file entities -------------------------------------
        if file_hashes:
            for rel_path, file_hash in file_hashes.items():
                safe = self._safe_id(f"file_{rel_path}")
                self.add_entity(
                    safe,
                    rel_path,
                    role="project_file",
                    sha256=file_hash,
                )

        # --- Step input/output entities --------------------------------
        for step in manifest.steps:
            for inp in step.inputs:
                safe_id = self._safe_id(f"input_{step.id}_{inp}")
                self.add_entity(safe_id, inp, role=f"input_{step.id}")
            for out in step.outputs:
                safe_id = self._safe_id(f"output_{step.id}_{out}")
                self.add_entity(safe_id, out, role=f"output_{step.id}")

        # --- Activities (steps) -----------------------------------------
        for step in manifest.steps:
            used = [self._safe_id(f"input_{step.id}_{inp}") for inp in step.inputs]
            generated = [
                self._safe_id(f"output_{step.id}_{out}") for out in step.outputs
            ]
            was_informed_by = [f"step_{dep}" for dep in step.depends_on]
            self.add_activity(
                f"step_{step.id}",
                step.id,
                used=used or None,
                generated=generated or None,
                was_associated_with="repropack_system",
                was_informed_by=was_informed_by or None,
                step_type=step.type.value,
                command=step.command,
                description=step.description,
            )

    def _safe_id(self, value: str) -> str:
        """Convert a path into a PROV-safe identifier."""
        return (
            value.replace("/", "_")
            .replace("\\", "_")
            .replace(".", "_")
            .replace("-", "_")
        )

    def to_json(self) -> str:
        """Serialize the PROV document to PROV-JSON.

        Returns:
            JSON string representation.
        """
        import io

        from prov.serializers.provjson import ProvJSONSerializer

        stream = io.StringIO()
        serializer = ProvJSONSerializer(self.doc)
        serializer.serialize(stream=stream, indent=2)
        return stream.getvalue()

    def to_dict(self) -> dict[str, Any]:
        """Return the PROV document as a Python dictionary.

        Returns:
            Dictionary representation of PROV-JSON.
        """
        return json.loads(self.to_json())  # type: ignore[no-any-return]

    def to_dot(self) -> str:
        """Generate a Graphviz DOT representation of the graph.

        Returns:
            DOT source string.
        """
        dot = prov_to_dot(self.doc)
        return str(dot.to_string())

    def to_mermaid(self) -> str:
        """Generate a Mermaid diagram of the graph.

        Returns:
            Mermaid syntax string.
        """
        lines: list[str] = ["graph TD"]
        nodes: set[str] = set()
        edges: set[str] = set()

        for record in self.doc.get_records():
            rec_id = record.identifier.localpart if record.identifier else "unknown"
            label = rec_id
            if hasattr(record, "attributes"):
                for attr, val in record.attributes:
                    if attr.localpart == "name":
                        label = str(val)
                        break

            node_line = ""
            if isinstance(record, ProvActivity):
                node_line = f'    {rec_id}["⚙️ {label}"]'
            elif isinstance(record, ProvEntity):
                node_line = f'    {rec_id}["📄 {label}"]'
            elif isinstance(record, ProvAgent):
                node_line = f'    {rec_id}["👤 {label}"]'

            if node_line and rec_id not in nodes:
                nodes.add(rec_id)
                lines.append(node_line)

        # Collect edges from PROV relation records
        for bundle in [self.doc] + list(self.doc.bundles):
            for record in bundle.get_records():
                edge: tuple[str, str] | None = None
                if hasattr(record, "attributes"):
                    attrs = {str(k): v for k, v in record.attributes}
                    if "prov:activity" in attrs and "prov:entity" in attrs:
                        act = str(attrs["prov:activity"]).replace(f"{NS_PREFIX}:", "")
                        ent = str(attrs["prov:entity"]).replace(f"{NS_PREFIX}:", "")
                        # Used: activity -> entity
                        if type(record).__name__ in (
                            "ProvUsage",
                            "ProvGeneration",
                        ):
                            edge = (act, ent)
                    elif "prov:activity" in attrs and "prov:agent" in attrs:
                        act = str(attrs["prov:activity"]).replace(f"{NS_PREFIX}:", "")
                        agt = str(attrs["prov:agent"]).replace(f"{NS_PREFIX}:", "")
                        # Association: activity -> agent
                        if type(record).__name__ == "ProvAssociation":
                            edge = (act, agt)
                    elif "prov:informed" in attrs and "prov:informant" in attrs:
                        inf = str(attrs["prov:informed"]).replace(f"{NS_PREFIX}:", "")
                        src = str(attrs["prov:informant"]).replace(f"{NS_PREFIX}:", "")
                        # Communication: informed -> informant
                        if type(record).__name__ == "ProvCommunication":
                            edge = (inf, src)

                if edge:
                    edge_line = f"    {edge[0]} --> {edge[1]}"
                    if edge_line not in edges:
                        edges.add(edge_line)
                        lines.append(edge_line)

        return "\n".join(lines)

    def to_html(self, title: str = "Provenance Graph") -> str:
        """Generate an HTML page with an embedded Mermaid diagram.

        Args:
            title: Page title.

        Returns:
            Self-contained HTML string.
        """
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

    def to_png(self, path: Path) -> None:
        """Render the graph to a PNG file using Graphviz.

        Args:
            path: Output file path (``.png`` suffix recommended).

        Raises:
            RuntimeError: If Graphviz is not installed.
        """
        try:
            import graphviz
        except ImportError as exc:
            raise RuntimeError(
                "graphviz Python package is required for PNG export"
            ) from exc

        dot = self.to_dot()
        src = graphviz.Source(dot)
        src.render(str(path.with_suffix("")), format="png", cleanup=True)

    def save(self, path: Path) -> None:
        """Save the graph in PROV-JSON format.

        Args:
            path: Output file path.
        """
        path.write_text(self.to_json(), encoding="utf-8")

    def build_nx_graph(self) -> nx.DiGraph:
        """Build a NetworkX DiGraph from the PROV document.

        Returns:
            Directed graph with nodes (agents, entities, activities) and
            edges representing PROV relations.
        """
        g = nx.DiGraph()

        # Add nodes
        for record in self.doc.get_records():
            rec_id = record.identifier.localpart if record.identifier else "unknown"
            label = rec_id
            if hasattr(record, "attributes"):
                for attr, val in record.attributes:
                    if attr.localpart == "name":
                        label = str(val)
                        break
            g.add_node(
                rec_id,
                type=type(record).__name__,
                label=label,
            )

        # Add edges from PROV relation records
        for bundle in [self.doc] + list(self.doc.bundles):
            for record in bundle.get_records():
                if hasattr(record, "attributes"):
                    attrs = {str(k): v for k, v in record.attributes}
                    if "prov:activity" in attrs and "prov:entity" in attrs:
                        act = str(attrs["prov:activity"]).replace(f"{NS_PREFIX}:", "")
                        ent = str(attrs["prov:entity"]).replace(f"{NS_PREFIX}:", "")
                        if type(record).__name__ == "ProvUsage":
                            g.add_edge(act, ent, relation="used")
                        elif type(record).__name__ == "ProvGeneration":
                            g.add_edge(act, ent, relation="wasGeneratedBy")
                    elif "prov:activity" in attrs and "prov:agent" in attrs:
                        act = str(attrs["prov:activity"]).replace(f"{NS_PREFIX}:", "")
                        agt = str(attrs["prov:agent"]).replace(f"{NS_PREFIX}:", "")
                        if type(record).__name__ == "ProvAssociation":
                            g.add_edge(act, agt, relation="wasAssociatedWith")
                    elif "prov:informed" in attrs and "prov:informant" in attrs:
                        inf = str(attrs["prov:informed"]).replace(f"{NS_PREFIX}:", "")
                        src = str(attrs["prov:informant"]).replace(f"{NS_PREFIX}:", "")
                        if type(record).__name__ == "ProvCommunication":
                            g.add_edge(inf, src, relation="wasInformedBy")

        self._nx_graph = g
        return g
