from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ruamel.yaml import YAML


PROTECTED = re.compile(r"\x60[^\x60]*\x60|https?://\S+|\b\S+@\S+\b|\$\{[^}]+\}|\{\{[^}]+\}\}|%[A-Z0-9_]+%|--[a-z0-9-]+|!?\[[^]]*]\([^)]+\)|<[^>]+>", re.IGNORECASE)


def providers_path(workspace: Path) -> Path:
    return workspace / ".mdoc" / "config" / "translation-providers.json"


def _revision(value: dict) -> str:
    return __import__("hashlib").sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def load_providers(workspace: Path, public: bool = True) -> dict:
    path = providers_path(workspace)
    try: value = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"schema_version": 1, "providers": []}
    except json.JSONDecodeError as exc: raise ValueError("翻译服务配置不是有效 JSON。") from exc
    revision = _revision(value)
    if public: value = {**value, "providers": [{**{key: item for key, item in provider.items() if key != "api_key"}, "key_configured": bool(provider.get("api_key"))} for provider in value.get("providers", [])]}
    return {**value, "revision": revision}


def save_providers(workspace: Path, value: dict, revision: str | None = None) -> dict:
    current = load_providers(workspace, False)
    if revision is not None and revision != current["revision"]: raise ValueError("翻译服务配置已被其他页面修改，请重新加载。")
    providers = value.get("providers")
    if not isinstance(providers, list): raise ValueError("providers 必须是数组。")
    ids = set(); normalized = []
    for item in providers:
        identifier = str(item.get("id", "")).strip(); base = str(item.get("base_url", "")).rstrip("/"); api_type = item.get("api_type")
        if not identifier or identifier in ids: raise ValueError("翻译服务 ID 不能为空且必须唯一。")
        if api_type not in {"openai-completions", "openai-chat-completions"}: raise ValueError("不支持的翻译接口类型。")
        parsed = urlsplit(base)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password: raise ValueError("Base URL 无效。")
        models = item.get("models", []); model_ids = [model.get("id") for model in models]
        if not models or item.get("default_model") not in model_ids: raise ValueError("必须配置模型并选择默认模型。")
        old = next((provider for provider in current.get("providers", []) if provider.get("id") == identifier), {})
        normalized.append({**item, "id": identifier, "base_url": base, "api_key": item.get("api_key", old.get("api_key", ""))}); ids.add(identifier)
    result = {"schema_version": 1, "providers": normalized}; path = providers_path(workspace); path.parent.mkdir(parents=True, exist_ok=True); temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"); os.replace(temporary, path)
    return {"saved": True, "revision": _revision(result)}


def request(provider: dict, prompt: str, timeout: int = 120) -> str:
    endpoint = provider["base_url"] + ("/completions" if provider["api_type"] == "openai-completions" else "/chat/completions")
    maximum = next((model.get("max_output_tokens", 4096) for model in provider["models"] if model["id"] == provider["default_model"]), 4096)
    body = {"model": provider["default_model"], "max_tokens": maximum, "temperature": .1, "stream": False}
    if provider["api_type"] == "openai-completions": body["prompt"] = prompt
    else: body["messages"] = [{"role": "user", "content": prompt}]
    http = Request(endpoint, data=json.dumps(body).encode(), headers={"Authorization": "Bearer " + provider.get("api_key", ""), "Content-Type": "application/json"}, method="POST")
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise ValueError("翻译服务返回重定向，已拒绝继续发送密钥。")
    try:
        with build_opener(NoRedirect()).open(http, timeout=timeout) as response: value = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read(4096).decode("utf-8", errors="replace").strip(); key = provider.get("api_key", "")
        if key: detail = detail.replace(key, "***")
        raise ValueError(f"翻译服务返回 HTTP {exc.code}" + (f"：{detail}" if detail else "。")) from exc
    except URLError as exc: raise ValueError("无法连接翻译服务：" + str(exc.reason)) from exc
    except json.JSONDecodeError as exc: raise ValueError("翻译服务返回的内容不是有效 JSON。") from exc
    try: choice = value["choices"][0]; result = choice.get("text") or choice.get("message", {}).get("content", "")
    except (KeyError, IndexError, TypeError) as exc: raise ValueError("翻译服务响应中缺少 choices 结果。") from exc
    return result


def test_provider(workspace: Path, provider_id: str) -> dict:
    provider = next((item for item in load_providers(workspace, False).get("providers", []) if item.get("id") == provider_id), None)
    if not provider: raise ValueError("翻译服务不存在。")
    result = request(provider, "Reply with OK only.", 30).strip()
    return {"status": "passed" if result else "failed", "response": result[:200]}


def provider_models(workspace: Path, provider_id: str) -> dict:
    provider = next((item for item in load_providers(workspace, False).get("providers", []) if item.get("id") == provider_id), None)
    if not provider: raise ValueError("翻译服务不存在。")
    endpoint = provider["base_url"] + "/models"; http = Request(endpoint, headers={"Authorization": "Bearer " + provider.get("api_key", "")})
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl): raise ValueError("模型接口返回重定向，已拒绝继续发送密钥。")
    with build_opener(NoRedirect()).open(http, timeout=30) as response: value = json.loads(response.read().decode("utf-8"))
    return {"models": sorted({str(item["id"]) for item in value.get("data", []) if item.get("id")})}


def codex_translate(source_locale: str, targets: list[str], text: str, timeout: int = 120, title: str = "", context: str = "") -> dict:
    executable = shutil.which("codex") or shutil.which("codex.cmd")
    if not executable: raise ValueError("当前机器未安装 Codex CLI，请配置 AI 翻译服务或使用网页翻译。")
    expected = {"translations": [{"locale": target, "text": "Translated Markdown"} for target in targets]}
    prompt = "Translate the Markdown selection. Return JSON only in this shape: " + json.dumps(expected, ensure_ascii=False) + "\nPreserve Markdown structure, links, image paths, code, HTML attributes, URLs, placeholders, flags, brand names and technical terms.\nInput: " + json.dumps({"source_locale": source_locale, "targets": targets, "title": title, "context": context, "source": text}, ensure_ascii=False)
    command = [executable, "exec", "--ephemeral", "--sandbox", "read-only", "--", prompt]
    if Path(executable).suffix.casefold() in {".cmd", ".bat"}: command = [os.environ.get("ComSpec", "cmd.exe"), "/d", "/s", "/c", *command]
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if completed.returncode: raise ValueError((completed.stderr or completed.stdout)[-4096:] or "Codex CLI 翻译失败。")
    try: value = json.loads(_strip_fence(completed.stdout))
    except json.JSONDecodeError as exc: raise ValueError("Codex CLI 没有返回约定的 JSON。") from exc
    results=[]
    for target in targets:
        item=next((candidate for candidate in value.get("translations",[]) if candidate.get("locale")==target),None)
        results.append({"locale":target,"status":"ready" if item and not structure_differences(text,str(item.get("text",""))) else "failed","text":item.get("text","") if item else "","differences":structure_differences(text,str(item.get("text",""))) if item else [],"error":"缺少该语言结果。" if not item else ""})
    return {"results":results,"outgoing":{"source":text}}


def _strip_fence(value: str) -> str:
    fence = chr(96) * 3; clean = value.strip()
    if clean.startswith(fence): clean = clean.split("\n", 1)[1] if "\n" in clean else ""
    if clean.rstrip().endswith(fence): clean = clean.rstrip()[:-3].rstrip()
    return clean


def translate(workspace: Path, provider_id: str, source_locale: str, targets: list[str], text: str, title: str = "", context: str = "", timeout: int = 120) -> dict:
    provider = next((item for item in load_providers(workspace, False).get("providers", []) if item.get("id") == provider_id), None)
    if not provider: raise ValueError("翻译服务不存在。")
    terms = load_terms(workspace)
    prompt = "Return JSON only with translations [{locale,text}]. Preserve Markdown structure, links, image paths, code, HTML attributes, URLs, placeholders, flags, brand names and technical terms.\n" + json.dumps({"source_locale": source_locale, "target_locales": targets, "title": title, "context": context, "source": text, "protected_terms": terms["protected"], "terms": terms["terms"]}, ensure_ascii=False)
    try: value = json.loads(_strip_fence(request(provider, prompt, timeout)))
    except json.JSONDecodeError as exc: raise ValueError("翻译服务没有返回约定的 JSON。") from exc
    if isinstance(value, list): translations = value
    elif isinstance(value, dict) and isinstance(value.get("translations"), list): translations = value["translations"]
    else: raise ValueError("翻译服务响应格式无效，应返回 translations 数组。")
    if not all(isinstance(item, dict) for item in translations): raise ValueError("翻译服务响应格式无效，translations 中的项目必须是对象。")
    results = []
    for target in targets:
        item = next((candidate for candidate in translations if candidate.get("locale") == target), None)
        if not item: results.append({"locale": target, "status": "failed", "error": "缺少该语言结果。"}); continue
        differences = structure_differences(text, str(item.get("text", ""))); results.append({"locale": target, "status": "blocked" if differences else "ready", "text": item.get("text", ""), "differences": differences})
    return {"results": results, "outgoing": {"source": text, "title": title, "context": context}}


def load_terms(workspace: Path) -> dict:
    path = workspace / ".mdoc" / "feedback-translation-terms.yaml"
    if not path.is_file(): return {"path": str(path), "exists": False, "protected": [], "terms": [], "warning": "未配置翻译术语表。"}
    try: value = YAML(typ="safe").load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc: raise ValueError("翻译术语表无法解析：" + str(exc)) from exc
    if value.get("schema_version") != 1 or not isinstance(value.get("protected", []), list) or not isinstance(value.get("terms", []), list): raise ValueError("翻译术语表必须使用 schema_version 1。")
    return {"path": str(path), "exists": True, "protected": value.get("protected", []), "terms": value.get("terms", []), "warning": ""}


def structure_differences(source: str, translated: str) -> list[str]:
    differences = []
    if PROTECTED.findall(source) != PROTECTED.findall(translated): differences.append("受保护的链接、代码、URL、占位符或 HTML 结构发生变化。")
    fences = r"(?m)^" + re.escape(chr(96) * 3)
    for label, pattern in (("标题层级", r"(?m)^#{1,6} "), ("列表结构", r"(?m)^\s*(?:[-+*]|\d+\.) "), ("引用层级", r"(?m)^\s*>+ "), ("代码围栏", fences)):
        if re.findall(pattern, source) != re.findall(pattern, translated): differences.append(label + "发生变化。")
    return differences
