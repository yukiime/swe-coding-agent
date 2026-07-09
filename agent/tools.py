# 设计说明：四个工具是LLM影响世界的唯一途径，全部经 docker exec 在instance官方容器内
# 执行，宿主机零污染、跨instance零串扰。edit_file 采用"唯一精确匹配替换"：命中0处或
# 多处一律报错且报错文案直接告诉模型下一步怎么做——宁可多一轮对话，不替模型猜意图。
import shlex
import subprocess

import config

TOOLS = [
    {"type": "function", "function": {
        "name": "read_file",
        "description": "Read a file (with line numbers). Optionally a line range.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "File path, relative to /testbed or absolute"},
            "start_line": {"type": "integer", "description": "1-based, optional"},
            "end_line": {"type": "integer", "description": "inclusive, optional"}},
            "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "edit_file",
        "description": "Replace an exact string in a file. old_str must appear exactly once; "
                       "copy it character-for-character from read_file output (without line numbers).",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "old_str": {"type": "string"},
            "new_str": {"type": "string"}},
            "required": ["path", "old_str", "new_str"]}}},
    {"type": "function", "function": {
        "name": "run_tests",
        "description": "Run a shell command in the repo environment (e.g. pytest). "
                       "Returns exit code and output.",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string", "description": "e.g. python -m pytest tests/x.py -x -q"}},
            "required": ["command"]}}},
    {"type": "function", "function": {
        "name": "code_search",
        "description": "Search *.py files with grep -E (regex). Returns up to 50 matches as path:line:text.",
        "parameters": {"type": "object", "properties": {
            "pattern": {"type": "string"},
            "path": {"type": "string", "description": "Directory to search, default /testbed"}},
            "required": ["pattern"]}}},
]


class Container:
    def __init__(self, name: str):
        self.name = name

    def exec(self, cmd: str, timeout: int = config.TOOL_TIMEOUT_S,
             stdin: str | None = None) -> tuple[int, str, str]:
        """容器内 bash -c 执行，工作目录/testbed。返回 (exit_code, stdout, stderr)。"""
        proc = subprocess.run(
            ["docker", "exec", "-i", "-w", config.TESTBED, self.name, "bash", "-c", cmd],
            input=stdin, capture_output=True, text=True, errors="replace",
            timeout=timeout + 10)
        return proc.returncode, proc.stdout, proc.stderr


def image_of(instance_id: str) -> str:
    return config.IMAGE_PREFIX + instance_id.replace("__", "_1776_") + ":latest"


def start_container(instance_id: str) -> Container:
    name = f"swe-agent.{instance_id}"
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)  # 清理上次残留
    subprocess.run(["docker", "run", "-d", "--name", name, "--platform", "linux/amd64",
                    image_of(instance_id), "tail", "-f", "/dev/null"],
                   check=True, capture_output=True)
    return Container(name)


def remove_container(ctr: Container) -> None:
    subprocess.run(["docker", "rm", "-f", ctr.name], capture_output=True)


def _norm(path: str) -> str:
    return path if path.startswith("/") else f"{config.TESTBED}/{path}"


def read_file(ctr: Container, path: str, start_line: int | None = None,
              end_line: int | None = None) -> str:
    code, out, err = ctr.exec(f"cat -n {shlex.quote(_norm(path))}")
    if code != 0:
        return f"[error] {err.strip() or out.strip()}"
    lines = out.splitlines()
    if start_line or end_line:
        lines = lines[(start_line or 1) - 1:end_line or len(lines)]
    return "\n".join(lines) or "[empty file]"


def edit_file(ctr: Container, path: str, old_str: str, new_str: str) -> str:
    p = _norm(path)
    code, content, err = ctr.exec(f"cat {shlex.quote(p)}")
    if code != 0:
        return f"[error] cannot read {path}: {err.strip()}"
    n = content.count(old_str)
    if n == 0:
        return ("[error] old_str not found in file. Re-read the file and copy the exact "
                "text including whitespace/indentation (line-number prefixes excluded).")
    if n > 1:
        return f"[error] old_str matches {n} locations. Include more surrounding lines to make it unique."
    code, _, err = ctr.exec(f"cat > {shlex.quote(p)}", stdin=content.replace(old_str, new_str, 1))
    if code != 0:
        return f"[error] write failed: {err.strip()}"
    return f"[ok] edited {path}"


def run_tests(ctr: Container, command: str) -> str:
    code, out, err = ctr.exec(
        f"{config.CONDA_ACTIVATE} && timeout {config.RUN_TESTS_TIMEOUT_S} {command}",
        timeout=config.RUN_TESTS_TIMEOUT_S)
    note = " (124=timeout)" if code == 124 else ""
    return f"[exit={code}{note}]\n{out}{err}"


def code_search(ctr: Container, pattern: str, path: str = "") -> str:
    where = _norm(path) if path else config.TESTBED
    cmd = (f"grep -rn -E --include='*.py' {shlex.quote(pattern)} {shlex.quote(where)} "
           f"| head -50")
    code, out, err = ctr.exec(cmd)
    if code > 1:
        return f"[error] {err.strip()}"
    return out.replace(config.TESTBED + "/", "") if out.strip() else "[no matches]"


_IMPL = {"read_file": read_file, "edit_file": edit_file,
         "run_tests": run_tests, "code_search": code_search}


def run_tool(ctr: Container, name: str, args: dict) -> str:
    if name not in _IMPL:
        return f"[error] unknown tool: {name}"
    try:
        return _IMPL[name](ctr, **args)
    except TypeError as e:
        return f"[error] bad arguments for {name}: {e}"
    except subprocess.TimeoutExpired:
        return f"[error] {name} timed out"
