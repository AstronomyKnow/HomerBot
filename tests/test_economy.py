import ast
from pathlib import Path


def test_steal_command_is_registered_with_hybrid_decorator():
    source = Path(__file__).resolve().parents[1] / "cogs" / "economy.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))

    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Economy":
            for item in node.body:
                if isinstance(item, ast.AsyncFunctionDef) and item.name == "steal_prefix":
                    decorator_names = []
                    for dec in item.decorator_list:
                        if isinstance(dec, ast.Call):
                            decorator_names.append(getattr(dec.func, "attr", None))
                        else:
                            decorator_names.append(getattr(dec, "attr", None))
                    assert "hybrid_command" in decorator_names
                    break
            else:
                raise AssertionError("steal_prefix not found")
            break
    else:
        raise AssertionError("Economy class not found")
