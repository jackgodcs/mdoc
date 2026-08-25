from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

from .errors import MdocError
from .io import canonical_digest, file_digest, inside, relative_path, write_json_atomic
from .models import thaw


def _registered_runtime(workspace, token: str) -> Path:
    if not token.startswith("runtime:") or token.count(":") != 1:
        raise MdocError("MDOC-ADAPTER-RUNTIME-INVALID", "适配器命令必须以已注册的 runtime:<id> 开头。")
    runtime_id = token.split(":", 1)[1]
    runtime = workspace.local.get("runtimes", {}).get(runtime_id)
    executable = runtime.get("executable") if isinstance(runtime, Mapping) else None
    if not isinstance(executable, str) or not executable:
        raise MdocError("MDOC-ADAPTER-RUNTIME-MISSING", "本机配置未注册适配器所需运行时。", {"runtime": runtime_id})
    path = Path(executable).expanduser().resolve()
    if not path.is_file():
        raise MdocError("MDOC-ADAPTER-RUNTIME-MISSING", "已注册运行时不存在。", {"runtime": runtime_id, "executable": str(path)})
    return path


def _adapter_command(workspace, adapter: Mapping, sandbox: Path) -> tuple[list[str], Path]:
    command = adapter.get("command")
    if not isinstance(command, Sequence) or isinstance(command, (str, bytes)) or len(command) < 2 or not all(isinstance(item, str) and item for item in command):
        raise MdocError("MDOC-ADAPTER-COMMAND-INVALID", "适配器 command 必须是非空参数数组，并包含仓库内脚本。")
    executable = _registered_runtime(workspace, command[0])
    script_relative = relative_path(command[1], "adapter.command[1]")
    source_script = inside(workspace.repository, script_relative)
    if not source_script.is_file():
        raise MdocError("MDOC-ADAPTER-SCRIPT-MISSING", "适配器脚本不存在。", {"script": script_relative.as_posix()})
    sandbox_script = inside(sandbox, script_relative)
    sandbox_script.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source_script, sandbox_script)
    return [str(executable), str(sandbox_script), *command[2:]], source_script


def _copy_declared_inputs(workspace, adapter: Mapping, sandbox: Path) -> dict[str, str]:
    digests: dict[str, str] = {}
    for raw in adapter.get("inputs", ()):
        relative = relative_path(raw, "adapter.inputs")
        source = inside(workspace.repository, relative)
        if not source.is_file():
            raise MdocError("MDOC-ADAPTER-INPUT-MISSING", "适配器声明的输入文件不存在。", {"input": relative.as_posix()})
        destination = inside(sandbox, relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        digests[relative.as_posix()] = file_digest(source)
    return digests


def _generator_manifest(adapter: Mapping, output_root: Path) -> tuple[list[dict], dict[str, str]]:
    outputs = adapter.get("outputs")
    if not isinstance(outputs, Mapping):
        raise MdocError("MDOC-GENERATOR-OUTPUT-INVALID", "generator 必须声明 outputs。")
    try:
        pattern = re.compile(str(outputs["pattern"]))
        destination_root = relative_path(str(outputs["root"]), "generator.outputs.root")
        locale = str(outputs["locale"])
        kind = str(outputs["kind"])
    except (KeyError, re.error) as exc:
        raise MdocError("MDOC-GENERATOR-OUTPUT-INVALID", "generator 输出声明无效。", {"cause": str(exc)}) from exc
    if kind not in {"page", "asset"} or not locale:
        raise MdocError("MDOC-GENERATOR-OUTPUT-INVALID", "generator 输出 kind 或 locale 无效。")
    files = sorted(path for path in output_root.rglob("*") if path.is_file())
    if not files:
        raise MdocError("MDOC-GENERATOR-OUTPUT-INVALID", "generator 没有产生任何输出。")
    manifest: list[dict] = []
    digests: dict[str, str] = {}
    for path in files:
        relative = path.relative_to(output_root).as_posix()
        if not pattern.fullmatch(relative):
            raise MdocError("MDOC-GENERATOR-OUTPUT-INVALID", "generator 产生了未声明的输出。", {"output": relative})
        manifest.append({"action": "create", "locale": locale, "path": (destination_root / Path(relative)).as_posix(), "kind": kind, "evidence": []})
        digests[relative] = file_digest(path)
    minimum = outputs.get("min_count")
    maximum = outputs.get("max_count")
    if (minimum is not None and len(files) < minimum) or (maximum is not None and len(files) > maximum):
        raise MdocError("MDOC-GENERATOR-OUTPUT-INVALID", "generator 输出数量超出声明范围。", {"count": len(files)})
    return manifest, digests


def define_generator(workspace, task_directory: Path, request: Mapping, evidence_ids: list[str]) -> tuple[list[dict], dict]:
    generator_id = request.get("id")
    adapter = workspace.config.get("generators", {}).get(generator_id)
    if not isinstance(adapter, Mapping):
        raise MdocError("MDOC-GENERATOR-MISSING", "任务引用了未注册的 generator。", {"generator": generator_id})
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="generator-run-", dir=task_directory) as temporary:
        sandbox = Path(temporary)
        output_root = sandbox / "output"
        output_root.mkdir()
        command, source_script = _adapter_command(workspace, adapter, sandbox)
        input_digests = _copy_declared_inputs(workspace, adapter, sandbox)
        try:
            result = subprocess.run(
                command, cwd=output_root, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=int(adapter.get("timeout_seconds", 120)),
                env={"PATH": os.environ.get("PATH", ""), "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""), "TEMP": str(sandbox), "TMP": str(sandbox)},
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise MdocError("MDOC-GENERATOR-TIMEOUT", "generator 执行超时。", {"generator": generator_id}) from exc
        if result.returncode != 0:
            raise MdocError("MDOC-GENERATOR-FAILED", "generator 执行失败。", {"generator": generator_id, "exit_code": result.returncode, "stderr": result.stderr[-4000:]})
        manifest, output_digests = _generator_manifest(adapter, output_root)
        for item in manifest:
            item["evidence"] = list(evidence_ids)
        cache = task_directory / "generator-output"
        if cache.exists():
            shutil.rmtree(cache)
        shutil.copytree(output_root, cache)
    record = {
        "id": generator_id, "inputs": thaw(request.get("inputs", {})),
        "adapter_digest": canonical_digest(adapter), "implementation_digest": file_digest(source_script),
        "input_digests": input_digests, "output_digests": output_digests,
        "duration_ms": int((time.monotonic() - started) * 1000), "exit_code": 0,
    }
    write_json_atomic(task_directory / "generator-record.json", record)
    return manifest, record


def import_generator_outputs(task) -> None:
    generator = task.definition.get("generator")
    if not isinstance(generator, Mapping):
        return
    adapter = task.workspace.config["generators"].get(generator["id"])
    if not isinstance(adapter, Mapping) or canonical_digest(adapter) != generator.get("adapter_digest"):
        raise MdocError("MDOC-GENERATOR-DEFINITION-STALE", "generator 配置在任务定义后发生变化。")
    cache = task.directory / "generator-output"
    destination_root = relative_path(adapter["outputs"]["root"], "generator.outputs.root")
    locale = adapter["outputs"]["locale"]
    for relative, expected in generator.get("output_digests", {}).items():
        source = inside(cache, relative_path(relative, "generator.output"))
        if not source.is_file() or file_digest(source) != expected:
            raise MdocError("MDOC-GENERATOR-DEFINITION-STALE", "冻结的 generator 输出已丢失或变化。", {"output": relative})
        target = inside(task.directory / "staging" / locale, destination_root / Path(relative))
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
