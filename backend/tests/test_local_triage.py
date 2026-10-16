import unittest
from hoomy.local_triage import excerpts, rank, signals


class LocalTriageTests(unittest.TestCase):
    def context(self):
        return [{"name": "Orchid", "examples": [{"title": "Orchid deployment", "belongs": True}]}]

    def test_budget_provenance_and_tools_are_excluded(self):
        messages = [{"id": str(i), "role": "user", "text": "Orchid " + "x" * 2000} for i in range(9)]
        messages += [{"id": "tool", "role": "tool", "text": "Orchid credentials"},
                     {"id": "prompt", "role": "user", "text": "# AGENTS Orchid private instructions"}]
        selected = excerpts(messages, self.context())
        self.assertLessEqual(sum(len(row["text"]) for row in selected), 3000)
        originals = {row["id"]: row["text"] for row in messages}
        for row in selected:
            self.assertEqual(row["text"], originals[row["message_id"]][row["start"]:row["start"] + len(row["text"])])
            self.assertNotIn(row["message_id"], ("tool", "prompt"))
        self.assertEqual(len({row["text"] for row in selected}), len(selected))

    def test_ranking_keeps_exploration_and_does_not_make_assignments(self):
        rows = [{"session_id": str(i), "title": "Orchid" if i < 20 else "Unrelated", "cwd": "/shape", "modified_at": str(i)} for i in range(24)]
        chosen = rank(rows, self.context())
        self.assertEqual(len(chosen), 12)
        self.assertEqual(sum(row["title"] == "Unrelated" for row in chosen), 3)
        self.assertTrue(all("project_id" not in row for row in rows))
        self.assertNotIn("secret", signals(self.context() + [{"name": "secret", "provisional": True}]))

    def test_no_keyword_hit_still_provides_limited_context(self):
        selected = excerpts([{"id": "s", "role": "user", "text": "A new purpose outside existing projects."}], self.context())
        self.assertEqual(len(selected), 1)
