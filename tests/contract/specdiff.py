# -*- coding: utf-8 -*-
"""规格比对引擎（参数级 / 类型级）—— `oasdiff` 的 Python 等价物。

## 为什么需要它

`replay.py` 原本只有**路径级**三分类账（保留 / 已解释移除 / 未解释丢失）。
这留下一个能力缺口：**同一条路径上的参数被删掉、必填性被收紧、字段类型被改**
—— 路径还在，账面上一切正常，但调用方会立刻炸。R2 收口时如实标注过这个缺口
（"参数级 / 类型级破坏性变更的判定仍是能力缺口"），本模块补齐它。

`oasdiff` 是 Go 二进制、本机未安装，且即便装上也只能做跨版本比对；本模块额外
提供**快照门禁**，覆盖全部路径（跨版本比对只能覆盖新旧同名的那几条）。

## 两种用途

1. `diff_documents(old, new)` —— 跨格式比对。旧 `apispec.json` 是 **Swagger 2.0**，
   新 `app.openapi()` 是 **OpenAPI 3.1**，两边结构不同，先各自规范化成同一个模型。
2. **快照门禁** —— `dump_snapshot(app)` 落盘当前 `app.openapi()`，
   门禁比对"当前 schema vs 快照"。这样**任何**契约收紧都会被拦住，
   而不是只有那几条新旧同名路径。契约该变时必须显式刷快照并说明原因。

## 分级

- `BREAKING` —— 已有调用方可能立刻失败（参数被删、必填收紧、类型变化、响应字段消失）
- `COMPATIBLE` —— 调用方不受影响，或只是放宽（新增可选参数、字段变可选、新增响应字段）

只有 `BREAKING` 计入失败数。`COMPATIBLE` 会打印出来供人工确认，但不阻断。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

BREAKING = "BREAKING"
COMPATIBLE = "COMPATIBLE"

# ── 旧规格基线里的**已知声明错误**（豁免清单）─────────────────────
#
# 这些差异的成因在**旧基线自己**（Swagger docstring 与实现不符），不是新实现的问题。
# 每条豁免**必须带依据**（文件:行号），否则就成了"把门禁关掉"的后门。
# key = (路径, 方法, 字段名)，仅作用于**响应字段**比对。
#
# 怎么判断该不该加到这里：**去看旧实现的源码**，而不是相信 apispec.json 的声明。
LEGACY_SPEC_ERRATA: dict[tuple[str, str, str], str] = {
    ("/api/check", "GET", "data_files"):
        "旧 docstring 声明 {type: object}，但实现返回 list "
        "（web/bp_admin.py:310-315：`data_results = []` + append）",
    ("/api/check", "GET", "model_files"):
        "同上：声明 object，实现是 list（web/bp_admin.py:302-307）",
}

# 规范化后的一次操作
Operation = dict[str, Any]


# ── `$ref` 解析 ────────────────────────────────────────────────────


class RefResolver:
    """展开文档内部的 `$ref`（`#/components/schemas/X` / `#/definitions/X`）。

    ⚠️ **不展开就只能得到误报**：新版 FastAPI 的响应模型是 Pydantic 模型 →
    OpenAPI 里写的是 `{"$ref": "#/components/schemas/HealthResponse"}`，
    `properties` 为空。2026-09-17 首版运行时报出 26 条
    "响应字段消失" —— 全部是这一个原因。

    只展开顶层 `$ref` 链（`while` 循环 + `seen` 去重），不做深度展开：
    字段的**类型标识**用 `ref:Name` / 联合分支描述即可满足破坏性判定，
    深度展开会把递归模型（如树形结构）炸掉。循环引用返回 `recursive` 哨兵。
    """

    def __init__(self, doc: dict) -> None:
        self._doc = doc

    def resolve(self, schema: Any) -> Any:
        if not isinstance(schema, dict):
            return schema
        seen: set[str] = set()
        while isinstance(schema, dict) and "$ref" in schema:
            ref = schema["$ref"]
            if ref in seen:
                return {"type": "recursive"}
            seen.add(ref)
            schema = self._lookup(ref)
        return schema

    def _lookup(self, ref: str) -> Any:
        if not str(ref).startswith("#/"):
            return {"type": "external"}
        node: Any = self._doc
        for part in str(ref)[2:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            if not isinstance(node, dict) or part not in node:
                return {"type": "unresolved"}
            node = node[part]
        return node


# ── 规范化：把 Swagger 2.0 / OpenAPI 3.1 统一成一个模型 ─────────────


def _is_oas3(doc: dict) -> bool:
    return str(doc.get("openapi", "")).startswith("3")


def _norm_type(value: Any) -> str:
    """把 `type` 归一化成可比较的字符串。

    OpenAPI 3.1 允许 `type: ["string", "null"]`（联合类型语法），Swagger 2.0 只有单值。
    **`null` 分支一律剔除** —— "可为空"这件事由 `required` 表达，两种写法必须归一到
    同一个结果，否则 `type: ["string","null"]` 与 `anyOf:[{type:string},{type:null}]`
    会得出不同标识，把同一个契约误报成类型变化。
    """
    if isinstance(value, list):
        parts = {str(v) for v in value if str(v) != "null"}
        return "|".join(sorted(parts)) if parts else "null"
    if value is None:
        return "any"
    return str(value)


def _schema_type(schema: Any, resolver: RefResolver | None = None) -> str:
    """从 JSON Schema 片段里提取"可比较的类型标识"。

    关键归一化：把 `anyOf: [{type: string}, {type: null}]` 折叠成 `string`。
    OpenAPI 3.1（Pydantic v2）用前者表达"可选字段"，Swagger 2.0 用后者 + `required`，
    不做这一步会把**每一个可选字段**都误报成"类型变化"。
    """
    if resolver is not None:
        schema = resolver.resolve(schema)
    if not isinstance(schema, dict):
        return "any"
    if "type" in schema:
        base = _norm_type(schema["type"])
        # ⚠️ 数组必须带元素类型：只返回 "array" 会让 `array[string]` 与
        # `array[integer]` 显示为同一标识 → **元素类型变化漏报**。
        # 由 tests/test_specdiff.py::test_array_of_ref 抓出。
        # 未声明 items 时用 `array[?]` 标记"声明不完整"，与"元素是 any"区分开。
        if base == "array":
            if "items" in schema:
                return f"array[{_schema_type(schema['items'], resolver)}]"
            return "array[?]"
        return base
    for keyword in ("anyOf", "oneOf"):
        if keyword in schema:
            branches = {
                _schema_type(sub, resolver) for sub in (schema[keyword] or [])
            }
            non_null = {b for b in branches if b != "null"}
            if not non_null:
                return "null"
            return "|".join(sorted(non_null))
    if "allOf" in schema:
        # 合并式组合（Pydantic 继承常用）：类型标识只报"复合"，
        # 字段比对由 _properties/_required_set 递归取并集完成
        return "allOf"
    if "enum" in schema:
        return "enum"
    if "items" in schema:
        return f"array[{_schema_type(schema['items'], resolver)}]"
    return "any"


def _types_equivalent(old_type: str, new_type: str) -> bool:
    """类型标识是否"等价"（不算破坏性变更）。

    `array[?]` 表示**未声明元素类型**（旧 Swagger 的常见写法：
    只写 `type: array` 不写 `items`）。它与 `array[string]` 描述的是同一件事，
    只差声明精度 —— 如果按字面不等就报 BREAKING，每个数组字段都会误报一条。
    """
    if old_type == new_type:
        return True
    if old_type.startswith("array[") and new_type.startswith("array["):
        return "array[?]" in (old_type, new_type)
    return False


def _properties(schema: Any, resolver: RefResolver) -> dict[str, Any]:
    schema = resolver.resolve(schema)
    if not isinstance(schema, dict):
        return {}
    merged: dict[str, Any] = {}
    # allOf → 合并各分支的属性（Pydantic 继承）
    for sub in schema.get("allOf") or []:
        merged.update(_properties(sub, resolver))
    merged.update(schema.get("properties") or {})
    return merged


def _required_set(schema: Any, resolver: RefResolver) -> set[str]:
    schema = resolver.resolve(schema)
    if not isinstance(schema, dict):
        return set()
    required = set(schema.get("required") or [])
    for sub in schema.get("allOf") or []:
        required |= _required_set(sub, resolver)
    return required


def _fields_of(schema: Any, resolver: RefResolver) -> dict[str, dict[str, Any]]:
    """把 object schema 的顶层字段规范化成 {字段名: {type, required}}。"""
    required = _required_set(schema, resolver)
    out: dict[str, dict[str, Any]] = {}
    for name, sub in _properties(schema, resolver).items():
        out[name] = {
            "type": _schema_type(sub, resolver),
            "required": name in required,
        }
    return out


def _json_schema_of(container: Any, resolver: RefResolver) -> Any:
    """从 OAS3 的 content 包装或 Swagger2 的裸 schema 里取出 schema。"""
    container = resolver.resolve(container)
    if not isinstance(container, dict):
        return None
    if "content" in container:
        content = container["content"] or {}
        for media in ("application/json", *content.keys()):
            if media in content:
                return content[media].get("schema")
        return None
    return container.get("schema")


def normalize_operation(op: dict, *, oas3: bool, resolver: RefResolver) -> Operation:
    """把一次操作（某个 path+method）规范化成统一模型。

    返回：
        {
          "params": {(name, location): {"type": str, "required": bool}},
          "body":   {字段名: {"type": str, "required": bool}},
          "response": {字段名: {"type": str}},
          "statuses": set[str],
        }
    """
    params: dict[tuple[str, str], dict[str, Any]] = {}
    body_schema: Any = None

    for raw in op.get("parameters") or []:
        if not isinstance(raw, dict):
            continue
        location = raw.get("in", "?")
        name = raw.get("name", "?")
        if location == "body":  # Swagger 2.0 的请求体写法
            body_schema = raw.get("schema")
            continue
        schema = raw.get("schema") if oas3 else raw
        params[(name, location)] = {
            "type": _schema_type(schema, resolver),
            "required": bool(raw.get("required", False)),
        }

    if oas3 and op.get("requestBody") is not None:
        body_schema = _json_schema_of(op["requestBody"], resolver)

    statuses: set[str] = set()
    response_fields: dict[str, dict[str, Any]] = {}
    for code, spec in (op.get("responses") or {}).items():
        statuses.add(str(code))
        # 只关心成功响应的字段结构；错误响应各版本差异大，纳入会制造噪声
        if not str(code).startswith("2"):
            continue
        fields = _fields_of(_json_schema_of(spec, resolver), resolver)
        if fields and not response_fields:
            response_fields = fields

    return {
        "params": params,
        "body": _fields_of(body_schema, resolver),
        "response": response_fields,
        "statuses": statuses,
    }


def normalize_document(doc: dict) -> dict[tuple[str, str], Operation]:
    """把整份规格规范化成 {(路径, 方法): Operation}。"""
    oas3 = _is_oas3(doc)
    resolver = RefResolver(doc)
    out: dict[tuple[str, str], Operation] = {}
    for path, methods in (doc.get("paths") or {}).items():
        if not isinstance(methods, dict):
            continue
        for method, op in methods.items():
            if method.lower() not in ("get", "post", "put", "patch", "delete", "head"):
                continue
            if not isinstance(op, dict):
                continue
            out[(path, method.upper())] = normalize_operation(
                op, oas3=oas3, resolver=resolver
            )
    return out


# ── 比对 ──────────────────────────────────────────────────────────


def _diff_params(
    where: str, old: Operation, new: Operation
) -> list[tuple[str, str]]:
    findings: list[tuple[str, str]] = []
    old_params, new_params = old["params"], new["params"]

    for key, old_spec in sorted(old_params.items()):
        name, location = key
        if key not in new_params:
            findings.append((
                BREAKING,
                f"{where}: 参数被移除 `{name}`（in={location}）—— 调用方传它会被拒或失效",
            ))
            continue
        new_spec = new_params[key]
        if not _types_equivalent(old_spec["type"], new_spec["type"]):
            findings.append((
                BREAKING,
                f"{where}: 参数 `{name}` 类型变化 {old_spec['type']} → {new_spec['type']}",
            ))
        elif old_spec["type"] != new_spec["type"]:
            findings.append((
                COMPATIBLE,
                f"{where}: 参数 `{name}` 类型声明精度变化 "
                f"{old_spec['type']} → {new_spec['type']}（实际可接受值未变）",
            ))
        if not old_spec["required"] and new_spec["required"]:
            findings.append((
                BREAKING,
                f"{where}: 参数 `{name}` 由可选变**必填** —— 老调用方会被拒",
            ))
        elif old_spec["required"] and not new_spec["required"]:
            findings.append((COMPATIBLE, f"{where}: 参数 `{name}` 由必填变可选（放宽）"))

    for key, new_spec in sorted(new_params.items()):
        if key in old_params:
            continue
        name, location = key
        level = COMPATIBLE
        findings.append((level, f"{where}: 新增参数 `{name}`（in={location}，required={new_spec['required']}）"))
    return findings


def _diff_fields(
    where: str,
    old_fields: dict[str, dict[str, Any]],
    new_fields: dict[str, dict[str, Any]],
    *,
    field_kind: str,
    key: tuple[str, str] | None = None,
) -> list[tuple[str, str]]:
    findings: list[tuple[str, str]] = []

    def _errata(name: str) -> str | None:
        if key is None or field_kind != "响应":
            return None
        return LEGACY_SPEC_ERRATA.get((key[0], key[1], name))

    for name, old_spec in sorted(old_fields.items()):
        if name not in new_fields:
            reason = _errata(name)
            if reason:
                findings.append((COMPATIBLE, f"{where}: 字段 `{name}` 差异已豁免 —— {reason}"))
                continue
            if field_kind == "响应":
                findings.append((
                    BREAKING,
                    f"{where}: 响应字段消失 `{name}` —— 读该字段的客户端会拿到 undefined",
                ))
            else:
                findings.append((
                    COMPATIBLE,
                    f"{where}: 请求体字段不再声明 `{name}`（多余字段被忽略，客户端无需改）",
                ))
            continue
        new_spec = new_fields[name]
        if not _types_equivalent(old_spec["type"], new_spec["type"]):
            reason = _errata(name)
            if reason:
                findings.append((COMPATIBLE, f"{where}: 字段 `{name}` 类型差异已豁免 —— {reason}"))
                continue
            findings.append((
                BREAKING,
                f"{where}: {field_kind}字段 `{name}` 类型变化 "
                f"{old_spec['type']} → {new_spec['type']}",
            ))
        elif old_spec["type"] != new_spec["type"]:
            findings.append((
                COMPATIBLE,
                f"{where}: {field_kind}字段 `{name}` 类型声明精度变化 "
                f"{old_spec['type']} → {new_spec['type']}（实际可接受值未变）",
            ))
        if field_kind == "请求体":
            if not old_spec["required"] and new_spec["required"]:
                findings.append((
                    BREAKING,
                    f"{where}: 请求体字段 `{name}` 新增为**必填** —— 老调用方不传会 422",
                ))
            elif old_spec["required"] and not new_spec["required"]:
                findings.append((COMPATIBLE, f"{where}: 请求体字段 `{name}` 变为可选（放宽）"))

    for name, new_spec in sorted(new_fields.items()):
        if name in old_fields:
            continue
        if field_kind == "请求体" and new_spec["required"]:
            findings.append((
                BREAKING,
                f"{where}: 新增**必填**请求体字段 `{name}` —— 老调用方不传会 422",
            ))
        else:
            findings.append((
                COMPATIBLE,
                f"{where}: 新增{field_kind}字段 `{name}`（{new_spec['type']}，"
                f"required={new_spec['required']}）",
            ))
    return findings


def diff_operation(
    key: tuple[str, str], old: Operation, new: Operation
) -> list[tuple[str, str]]:
    where = f"{key[1]} {key[0]}"
    findings = _diff_params(where, old, new)
    findings += _diff_fields(where, old["body"], new["body"], field_kind="请求体", key=key)
    findings += _diff_fields(where, old["response"], new["response"], field_kind="响应", key=key)

    lost_2xx = sorted(
        c for c in old["statuses"] - new["statuses"] if c.startswith("2")
    )
    for code in lost_2xx:
        findings.append((BREAKING, f"{where}: 成功状态码 {code} 不再声明"))
    return findings


def diff_documents(
    old_doc: dict, new_doc: dict, *, only_common: bool = True
) -> tuple[list[tuple[str, str]], dict[str, int]]:
    """比对两份规格文档。

    Args:
        only_common: True 时只比对**两边都存在**的操作 —— 用于"跨版本比对"，
            因为旧路径被移除/更名已由 `mapping.py` 的账目显式解释过，
            在这里重复报会淹没真正的参数级变化。
            False 时把"操作消失"也算 BREAKING —— 用于**快照门禁**。

    Returns:
        (findings, stats)
    """
    old_ops = normalize_document(old_doc)
    new_ops = normalize_document(new_doc)

    findings: list[tuple[str, str]] = []
    for key in sorted(set(old_ops) | set(new_ops)):
        if key not in old_ops:
            if not only_common:
                findings.append((COMPATIBLE, f"{key[1]} {key[0]}: 新增端点"))
            continue
        if key not in new_ops:
            if not only_common:
                findings.append((BREAKING, f"{key[1]} {key[0]}: **端点被删除**"))
            continue
        findings.extend(diff_operation(key, old_ops[key], new_ops[key]))

    stats = {
        "old_operations": len(old_ops),
        "new_operations": len(new_ops),
        "common_operations": len(set(old_ops) & set(new_ops)),
        "breaking": sum(1 for level, _ in findings if level == BREAKING),
        "compatible": sum(1 for level, _ in findings if level == COMPATIBLE),
    }
    return findings, stats


# ── 快照门禁 ───────────────────────────────────────────────────────

SNAPSHOT_PATH = Path(__file__).resolve().parent / "schema_snapshot.json"


def dump_snapshot(app) -> dict:
    """把当前 OpenAPI 规格落盘为快照（供防回归门禁使用）。"""
    spec = app.openapi()
    SNAPSHOT_PATH.write_text(
        json.dumps(spec, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return spec


def load_snapshot() -> dict | None:
    if not SNAPSHOT_PATH.exists():
        return None
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


def summarize_snapshot(spec: dict) -> int:
    """统计规格里的操作数（路径 × 方法）。"""
    return len(normalize_document(spec))
