"""Edge normalising for the Map tab."""
from __future__ import annotations


def normalise_edges(edges: list[dict]) -> list[dict]:
    """Rows with `source` (caller) and `target` (callee) for drawing.

    data/graph.json edges are {"from", "to", "source": "jaeger" | "override", ...} where `source` says WHERE the edge
    came from, not who the caller is, so when `from` is present it is the caller and the provenance is kept as `via`.
    Jaeger helper rows are already {"source", "target"}."""
    out = []
    for e in edges:
        if "from" in e or "to" in e:
            row = {"source": e.get("from"), "target": e.get("to"),
                   **{("via" if k == "source" else k): v for k, v in e.items() if k not in ("from", "to")}}
        else:
            row = dict(e)
        if row.get("source") and row.get("target"):
            out.append(row)
    return out
