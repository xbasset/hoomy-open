import contextlib
import io
import unittest
from unittest.mock import patch

import run


class RunTests(unittest.TestCase):
    def test_inference_check_requires_exact_confirmation(self):
        with patch("builtins.input", return_value="yes"), \
             patch.object(run, "live_inference_check") as check, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            run.check_inference()
        check.assert_not_called()
        self.assertIn("Cancelled", output.getvalue())

    def test_inference_check_prints_only_safe_result(self):
        safe = {"authentication": "chatgpt", "codex_version": "test",
                "model": "default", "restricted_boundary": "passed",
                "structured_turn": "passed"}
        with patch("builtins.input", return_value="CHECK CODEX INFERENCE"), \
             patch.object(run, "live_inference_check", return_value=safe) as check, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            run.check_inference()
        check.assert_called_once_with()
        self.assertIn('"structured_turn": "passed"', output.getvalue())


if __name__ == "__main__":
    unittest.main()
