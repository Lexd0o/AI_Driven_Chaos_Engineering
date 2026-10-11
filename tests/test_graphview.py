import json
import unittest
from pathlib import Path

from dashboard.graphview import normalise_edges


class GraphViewTest(unittest.TestCase):
    def test_from_is_the_caller_and_source_is_kept_as_provenance(self):
        rows = normalise_edges([{"from": "frontend", "to": "cart", "protocol": "http", "source": "jaeger", "calls": 98}])
        self.assertEqual((rows[0]["source"], rows[0]["target"], rows[0]["via"]), ("frontend", "cart", "jaeger"))
        self.assertEqual(rows[0]["calls"], 98)

    def test_jaeger_helper_rows_pass_through(self):
        rows = normalise_edges([{"source": "a", "target": "b", "calls": 3}])
        self.assertEqual((rows[0]["source"], rows[0]["target"]), ("a", "b"))

    def test_incomplete_rows_are_dropped(self):
        self.assertEqual(normalise_edges([{"from": "a"}, {"source": "x"}]), [])

    def test_the_real_graph_has_no_fake_jaeger_or_override_node(self):
        graph = json.loads((Path(__file__).resolve().parent.parent / "data" / "graph.json").read_text(encoding="utf-8"))
        rows = normalise_edges(graph["edges"])
        nodes = {r["source"] for r in rows} | {r["target"] for r in rows}
        real = {n["name"] for n in graph["nodes"]}
        self.assertEqual((nodes & {"jaeger", "override"}) - real, set())


if __name__ == "__main__":
    unittest.main()
