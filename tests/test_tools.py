from pathlib import Path

import pytest

from pico.tool_context import ToolContext
from pico.tools import build_tool_registry, tool_delegate, tool_find_files, tool_read_file, validate_tool


def _tool_context(tmp_path):
    return ToolContext(
        root=tmp_path,
        path_resolver=lambda raw_path: (tmp_path / raw_path).resolve(),
        shell_env_provider=lambda: {"PWD": str(tmp_path)},
        depth=0,
        max_depth=1,
        spawn_delegate=lambda args: "unused",
    )


def _result_paths(result):
    return [line.split(" ", 1)[1].replace("\\", "/") for line in result.splitlines() if line.startswith(("[F] ", "[D] "))]


def test_tool_context_supports_file_tools_without_full_pico(tmp_path):
    (tmp_path / "sample.txt").write_text("alpha\n", encoding="utf-8")
    context = ToolContext(
        root=tmp_path,
        path_resolver=lambda raw_path: (tmp_path / raw_path).resolve(),
        shell_env_provider=lambda: {"PWD": str(tmp_path)},
        depth=0,
        max_depth=1,
        spawn_delegate=lambda args: "unused",
    )

    result = tool_read_file(context, {"path": "sample.txt", "start": 1, "end": 1})

    assert "# sample.txt" in result
    assert "alpha" in result


def test_delegate_uses_context_spawn_without_runtime_import(tmp_path):
    calls = []
    context = ToolContext(
        root=tmp_path,
        path_resolver=lambda raw_path: Path(tmp_path / raw_path),
        shell_env_provider=lambda: {"PWD": str(tmp_path)},
        depth=0,
        max_depth=1,
        spawn_delegate=lambda args: calls.append(args) or "delegate_result:\nDone",
    )

    result = tool_delegate(context, {"task": "inspect README.md", "max_steps": 2})

    assert result == "delegate_result:\nDone"
    assert calls == [{"task": "inspect README.md", "max_steps": 2}]


def test_build_tool_registry_binds_runners_to_tool_context(tmp_path):
    context = ToolContext(
        root=tmp_path,
        path_resolver=lambda raw_path: Path(tmp_path / raw_path),
        shell_env_provider=lambda: {"PWD": str(tmp_path)},
        depth=1,
        max_depth=1,
        spawn_delegate=lambda args: "unused",
    )

    tools = build_tool_registry(context)

    assert "read_file" in tools
    assert "find_files" in tools
    assert "delegate" not in tools


def _find_files_fixture(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("app\n", encoding="utf-8")
    (tmp_path / "utils").mkdir()
    (tmp_path / "utils" / "foo.py").write_text("foo\n", encoding="utf-8")
    (tmp_path / "utils" / "nested").mkdir()
    (tmp_path / "utils" / "nested" / "bar.py").write_text("bar\n", encoding="utf-8")
    (tmp_path / "subdir").mkdir()
    (tmp_path / "subdir" / "calc.py").write_text("calc\n", encoding="utf-8")
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "test_x.py").write_text("test\n", encoding="utf-8")
    (tmp_path / "other.txt").write_text("txt\n", encoding="utf-8")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text("mod\n", encoding="utf-8")
    return _tool_context(tmp_path)


def test_find_files_returns_nested_matches(tmp_path):
    context = _find_files_fixture(tmp_path)

    result = tool_find_files(context, {"pattern": "*.py"})

    assert "[F]" in result
    assert "app.py" in result
    assert "src/app.py" in _result_paths(result) or "src\\app.py" in result


def test_find_files_pattern_semantics(tmp_path):
    context = _find_files_fixture(tmp_path)

    py_paths = _result_paths(tool_find_files(context, {"pattern": "*.py"}))
    assert "src/app.py" in py_paths
    assert "utils/foo.py" in py_paths
    assert "utils/nested/bar.py" in py_paths
    assert "other.txt" not in py_paths

    utils_one_level = _result_paths(tool_find_files(context, {"pattern": "utils/*.py"}))
    assert utils_one_level == ["utils/foo.py"]

    calc_paths = _result_paths(tool_find_files(context, {"pattern": "calc.py"}))
    assert calc_paths == ["subdir/calc.py"]

    test_paths = _result_paths(tool_find_files(context, {"pattern": "**/test_*.py"}))
    assert test_paths == ["a/test_x.py"]


def test_find_files_no_match_returns_sentinel(tmp_path):
    context = _find_files_fixture(tmp_path)

    result = tool_find_files(context, {"pattern": "missing_xyz.py"})

    assert result == "(no matches)"


def test_find_files_missing_directory_is_invalid(tmp_path):
    context = _find_files_fixture(tmp_path)

    with pytest.raises(ValueError, match="path is not a directory"):
        validate_tool(context, "find_files", {"pattern": "*.py", "path": "no_such_dir"})
    with pytest.raises(ValueError, match="path is not a directory"):
        tool_find_files(context, {"pattern": "*.py", "path": "no_such_dir"})


def test_find_files_rejects_empty_pattern(tmp_path):
    context = _find_files_fixture(tmp_path)

    for pattern in ("", "   ", "/"):
        with pytest.raises(ValueError, match="pattern must not be empty"):
            validate_tool(context, "find_files", {"pattern": pattern})
        with pytest.raises(ValueError, match="pattern must not be empty"):
            tool_find_files(context, {"pattern": pattern})


def test_find_files_scoped_to_subdirectory(tmp_path):
    context = _find_files_fixture(tmp_path)

    paths = _result_paths(tool_find_files(context, {"pattern": "*.py", "path": "src"}))

    assert paths == ["src/app.py"]
    assert "utils/foo.py" not in paths


def test_find_files_output_format(tmp_path):
    context = _find_files_fixture(tmp_path)

    result = tool_find_files(context, {"pattern": "pkg"})
    lines = result.splitlines()

    assert lines[0].startswith("[D] ")
    assert "pkg" in lines[0]
    assert not any(line.startswith("[F] ") for line in lines)
    assert "truncated" not in result.lower()

    mixed = tool_find_files(context, {"pattern": "*"})
    mixed_lines = mixed.splitlines()
    dir_indexes = [i for i, line in enumerate(mixed_lines) if line.startswith("[D] ")]
    file_indexes = [i for i, line in enumerate(mixed_lines) if line.startswith("[F] ")]
    assert dir_indexes
    assert file_indexes
    assert max(dir_indexes) < min(file_indexes)
