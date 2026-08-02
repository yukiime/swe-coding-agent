import subprocess

import config
from agent import tools
from tests.conftest import FakeContainer


# --- 路径与命名 ---

def test_norm_prefixes_relative_paths():
    assert tools._norm("a/b.py") == "/testbed/a/b.py"
    assert tools._norm("/etc/hosts") == "/etc/hosts"


def test_image_and_container_naming():
    assert tools.image_of("django__django-11066") == \
        "swebench/sweb.eval.x86_64.django_1776_django-11066:latest"
    assert tools.container_name("django__django-11066") == "swe-agent.django__django-11066"


# --- read_file ---

def test_read_file_slices_line_range():
    ctr = FakeContainer([(0, "     1\tone\n     2\ttwo\n     3\tthree\n", "")])
    assert tools.read_file(ctr, "a.py", start_line=2, end_line=3) == "     2\ttwo\n     3\tthree"


def test_read_file_reports_error_without_raising():
    ctr = FakeContainer([(1, "", "cat: no such file")])
    assert tools.read_file(ctr, "nope.py").startswith("[error]")


# --- edit_file 决策树 ---

def test_edit_noop_is_rejected_before_touching_the_container():
    """被 tool_choice 强制编辑却没有修复假设的模型会提交 no-op，返回 [ok] 等于撒谎。"""
    ctr = FakeContainer()
    assert tools.edit_file(ctr, "a.py", "same", "same").startswith("[error]")
    assert ctr.calls == []


def test_edit_missing_file_with_old_str_tells_model_how_to_create():
    ctr = FakeContainer([(1, "", "No such file")])
    out = tools.edit_file(ctr, "new.py", "x", "y")
    assert out.startswith("[error]")
    assert 'old_str=""' in out


def test_edit_creates_new_file_when_old_str_empty():
    ctr = FakeContainer([(1, "", "No such file"), (0, "", "")])
    assert tools.edit_file(ctr, "pkg/new.py", "", "BODY") == "[ok] created pkg/new.py"
    write_cmd, stdin = ctr.calls[1]
    assert "mkdir -p" in write_cmd and "/testbed/pkg" in write_cmd
    assert stdin == "BODY"


def test_edit_refuses_empty_old_str_on_existing_file():
    ctr = FakeContainer([(0, "existing content", "")])
    assert "already exists" in tools.edit_file(ctr, "a.py", "", "BODY")


def test_edit_rejects_zero_matches_without_writing():
    ctr = FakeContainer([(0, "alpha beta", "")])
    out = tools.edit_file(ctr, "a.py", "gamma", "delta")
    assert out.startswith("[error]") and "not found" in out
    assert len(ctr.calls) == 1          # 只 cat 过，没写


def test_edit_rejects_ambiguous_match_and_reports_the_count():
    ctr = FakeContainer([(0, "x = 1\nx = 1\n", "")])
    assert "matches 2 locations" in tools.edit_file(ctr, "a.py", "x = 1", "x = 2")
    assert len(ctr.calls) == 1


def test_edit_writes_back_content_with_exactly_one_replacement():
    ctr = FakeContainer([(0, "a\nTARGET\nb\n", ""), (0, "", "")])
    assert tools.edit_file(ctr, "a.py", "TARGET", "FIXED") == "[ok] edited a.py"
    assert ctr.calls[1][1] == "a\nFIXED\nb\n"


def test_edit_surfaces_write_failure():
    ctr = FakeContainer([(0, "TARGET", ""), (1, "", "disk full")])
    assert tools.edit_file(ctr, "a.py", "TARGET", "FIXED").startswith("[error] write failed")


# --- code_search ---

def test_code_search_command_keeps_pipefail_and_avoids_head():
    """pipefail 缺失会把非法正则吞成 [no matches]；用 head 截断会因 SIGPIPE 返回 141
    把正常结果误报成错误。两者都让模型以为仓库里没这个符号（commit bd7b02a）。"""
    ctr = FakeContainer([(0, "hit\n", "")])
    tools.code_search(ctr, "def foo")
    cmd = ctr.calls[0][0]
    assert "set -o pipefail" in cmd
    assert "sed -n '1,50p'" in cmd
    assert "head" not in cmd


def test_code_search_exit_1_means_no_matches_not_error():
    assert tools.code_search(FakeContainer([(1, "", "")]), "nothing") == "[no matches]"


def test_code_search_exit_2_is_an_error():
    ctr = FakeContainer([(2, "", "grep: invalid regex")])
    assert tools.code_search(ctr, "[bad").startswith("[error]")


def test_code_search_strips_testbed_prefix():
    ctr = FakeContainer([(0, "/testbed/pkg/a.py:3:hit\n", "")])
    assert tools.code_search(ctr, "hit") == "pkg/a.py:3:hit\n"


# --- run_tests ---

def test_run_tests_wraps_command_in_bash_c_under_conda():
    """timeout(1) 只能 exec 外部程序，包不住 `cd x && ...`；基线 50 例中 25 例踩到 exit=127。"""
    ctr = FakeContainer([(0, "2 passed", "")])
    tools.run_tests(ctr, "cd /testbed && pytest -q")
    cmd = ctr.calls[0][0]
    assert config.CONDA_ACTIVATE in cmd
    assert f"timeout {config.RUN_TESTS_TIMEOUT_S} bash -c" in cmd


def test_run_tests_labels_timeout_exit_code():
    assert "(124=timeout)" in tools.run_tests(FakeContainer([(124, "", "")]), "sleep 999")


# --- run_tool 兜底 ---

def test_run_tool_unknown_name():
    assert tools.run_tool(FakeContainer(), "delete_repo", {}).startswith("[error] unknown tool")


def test_run_tool_bad_arguments_returns_error_string():
    out = tools.run_tool(FakeContainer(), "read_file", {"wrong_kwarg": 1})
    assert out.startswith("[error] bad arguments for read_file")


def test_run_tool_timeout_returns_error_string():
    class Timeouting(FakeContainer):
        def exec(self, cmd, timeout=None, stdin=None):
            raise subprocess.TimeoutExpired(cmd, timeout or 1)

    assert tools.run_tool(Timeouting(), "run_tests", {"command": "sleep 999"}) == \
        "[error] run_tests timed out"
