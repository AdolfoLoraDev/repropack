# Plugins & Exporters

ReproPack exposes a small plugin API so third parties can add **exporters** —
callables that turn a `.rpk` into another artifact (a citation file, an
alternative provenance serialisation, a Nextflow/Galaxy descriptor, ...).

## Built-in exporters

| Name | Output |
|------|--------|
| `citation` | `CITATION.cff` (Citation File Format 1.2.0) |
| `provxml` | W3C PROV-XML of the provenance graph |
| `mermaid` | Mermaid diagram of the provenance graph |

```bash
repropack export exp.rpk                       # list available exporters
repropack export exp.rpk -e provxml -o prov.xml
```

## Writing an exporter

An exporter is any callable `(rpk_path: Path, output: Path) -> Path`.

### In-process registration

```python
from pathlib import Path
from repropack.core.plugins import register_exporter

@register_exporter("my-format")
def export_my_format(rpk_path: Path, output: Path) -> Path:
    output.write_text("...", encoding="utf-8")
    return output
```

### Distributing as a package (entry points)

Expose your exporter under the `repropack.exporters` entry-point group so it is
discovered automatically once installed:

```toml
# pyproject.toml of your plugin package
[project.entry-points."repropack.exporters"]
my-format = "my_plugin.exporters:export_my_format"
```

After `pip install my-plugin`, `repropack export exp.rpk -e my-format -o out`
just works.
