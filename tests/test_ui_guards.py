from __future__ import annotations

import ast
import unittest
from pathlib import Path


class UiGuardTests(unittest.TestCase):
    def test_all_plotly_charts_have_explicit_keys(self) -> None:
        ui_path = Path("D:/WS_AI_AGENT/personal-finance-tracking/finance_app/ui.py")
        tree = ast.parse(ui_path.read_text(encoding="utf-8"))
        missing_key_lines: list[int] = []

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute):
                continue
            if func.attr != "plotly_chart":
                continue
            has_key = any(keyword.arg == "key" for keyword in node.keywords if keyword.arg is not None)
            if not has_key:
                missing_key_lines.append(node.lineno)

        self.assertEqual(
            missing_key_lines,
            [],
            msg=f"Every st.plotly_chart call must set an explicit key. Missing at lines: {missing_key_lines}",
        )


if __name__ == "__main__":
    unittest.main()
