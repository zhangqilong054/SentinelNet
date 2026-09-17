# -*- coding: utf-8 -*-
"""规格比对引擎单测（`tests/contract/specdiff.py`）。

门禁本身也会出错 —— 首版就因为**不展开 `$ref`** 报出 26 条假 BREAKING
（新版 FastAPI 的响应是 Pydantic 模型 → OpenAPI 里是 `$ref`，`properties` 为空，
于是所有旧字段都被判成"消失"）。所以引擎需要自己的测试，否则"门禁全绿"
可能只意味着门禁坏了。

本文件用**内联的小规格**做单元测试，不依赖真实应用的 schema ——
真实 schema 的比对由 `tests/test_contract_replay.py` 覆盖。
"""
from __future__ import annotations

from tests.contract.specdiff import (
    BREAKING,
    COMPATIBLE,
    LEGACY_SPEC_ERRATA,
    RefResolver,
    diff_documents,
    normalize_document,
)


def _levels(findings) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {BREAKING: [], COMPATIBLE: []}
    for level, msg in findings:
        out[level].append(msg)
    return out


# ── RefResolver ────────────────────────────────────────────────────

class TestRefResolver:
    DOC = {
        "components": {"schemas": {
            "A": {"$ref": "#/components/schemas/B"},
            "B": {"type": "object", "properties": {"x": {"type": "string"}}},
            "Cycle": {"$ref": "#/components/schemas/Cycle"},
        }}
    }

    def test_resolves_chained_ref(self):
        r = RefResolver(self.DOC)
        resolved = r.resolve({"$ref": "#/components/schemas/A"})
        assert resolved["type"] == "object"
        assert "x" in resolved["properties"]

    def test_cyclic_ref_is_sentinel_not_infinite(self):
        r = RefResolver(self.DOC)
        assert r.resolve({"$ref": "#/components/schemas/Cycle"})["type"] == "recursive"

    def test_external_ref_is_sentinel(self):
        r = RefResolver(self.DOC)
        assert r.resolve({"$ref": "https://x/schema.json"})["type"] == "external"

    def test_unresolved_ref_is_sentinel(self):
        r = RefResolver(self.DOC)
        assert r.resolve({"$ref": "#/components/schemas/Missing"})["type"] == "unresolved"

    def test_non_dict_passthrough(self):
        assert RefResolver(self.DOC).resolve(None) is None


# ── 类型归一化（通过 normalize_document 的间接断言）────────────────

def _oas3_op(*, params=None, body=None, resp_props=None, statuses=("200",)) -> dict:
    op: dict = {"responses": {}}
    if params:
        op["parameters"] = params
    if body is not None:
        op["requestBody"] = {"content": {"application/json": {"schema": body}}}
    for code in statuses:
        spec: dict = {"description": "ok"}
        if code == "200" and resp_props is not None:
            spec["content"] = {"application/json": {"schema": {
                "type": "object", "properties": resp_props,
            }}}
        op["responses"][code] = spec
    return op


def _doc(op: dict, path: str = "/x", method: str = "get") -> dict:
    return {"openapi": "3.1.0", "paths": {path: {method: op}}}


class TestTypeNormalization:
    def test_anyof_nullable_folds_to_plain_type(self):
        """`anyOf:[string,null]`（Pydantic 可选字段）必须等价于 `type: string`。"""
        doc = _doc(_oas3_op(resp_props={"a": {"anyOf": [{"type": "string"}, {"type": "null"}]}}))
        op = normalize_document(doc)[("/x", "GET")]
        assert op["response"]["a"]["type"] == "string"

    def test_type_array_nullable_folds_to_plain_type(self):
        """`type: ["string","null"]` 必须与 anyOf 写法归一到同一结果。"""
        doc = _doc(_oas3_op(resp_props={"a": {"type": ["string", "null"]}}))
        op = normalize_document(doc)[("/x", "GET")]
        assert op["response"]["a"]["type"] == "string"

    def test_array_of_ref(self):
        doc = {"openapi": "3.1.0", "components": {"schemas": {"Item": {"type": "integer"}}},
               "paths": {"/x": {"get": _oas3_op(resp_props={
                   "items": {"type": "array", "items": {"$ref": "#/components/schemas/Item"}}})}}}
        op = normalize_document(doc)[("/x", "GET")]
        assert op["response"]["items"]["type"] == "array[integer]"

    def test_bare_array_is_marked_undeclared(self):
        """只写 `type: array` 不写 `items` → `array[?]`（"声明不完整"，≠ 元素是 any）。"""
        doc = _doc(_oas3_op(resp_props={"a": {"type": "array"}}))
        op = normalize_document(doc)[("/x", "GET")]
        assert op["response"]["a"]["type"] == "array[?]"


class TestArrayDeclarationPrecision:
    """`array`（未声明元素）与 `array[X]` 的差异不是破坏性变更。

    旧 Swagger 2.0 常写成 `type: array` 而不写 `items`；新实现（Pydantic）一定会
    声明元素类型。若按字面不等就报 BREAKING，**每个数组字段都会误报一条**。
    """

    def test_undeclared_element_vs_declared_is_compatible(self):
        old = _doc(_oas3_op(resp_props={"a": {"type": "array"}}))
        new = _doc(_oas3_op(resp_props={"a": {"type": "array", "items": {"type": "string"}}}))
        findings, stats = diff_documents(old, new)
        assert stats["breaking"] == 0, f"不应报破坏性变更：{findings}"
        assert any("精度" in msg for _lv, msg in findings), "应记录为声明精度变化"

    def test_conflicting_element_types_are_breaking(self):
        """元素类型真的变了必须报出来 —— 精度豁免不能顺手放过它。"""
        old = _doc(_oas3_op(resp_props={"a": {"type": "array", "items": {"type": "string"}}}))
        new = _doc(_oas3_op(resp_props={"a": {"type": "array", "items": {"type": "integer"}}}))
        _findings, stats = diff_documents(old, new)
        assert stats["breaking"] == 1


class TestNormalizeDocument:
    def test_swagger2_body_param_and_inline_response(self):
        swagger = {"swagger": "2.0", "paths": {"/x": {"post": {
            "parameters": [
                {"in": "body", "name": "body", "required": True,
                 "schema": {"type": "object",
                            "properties": {"days": {"type": "integer"}},
                            "required": ["days"]}},
                {"in": "query", "name": "q", "type": "string", "required": False},
            ],
            "responses": {"200": {"schema": {"type": "object",
                                             "properties": {"ok": {"type": "boolean"}}}}},
        }}}}
        op = normalize_document(swagger)[("/x", "POST")]
        assert op["params"][("q", "query")] == {"type": "string", "required": False}
        assert op["body"]["days"] == {"type": "integer", "required": True}
        assert op["response"]["ok"]["type"] == "boolean"
        assert op["statuses"] == {"200"}

    def test_oas3_request_body(self):
        doc = _doc(_oas3_op(body={"type": "object",
                                  "properties": {"payload": {"type": "string"}},
                                  "required": ["payload"]}))
        op = normalize_document(doc)[("/x", "GET")]
        assert op["body"]["payload"] == {"type": "string", "required": True}

    def test_only_http_methods_collected(self):
        doc = {"openapi": "3.1.0", "paths": {"/x": {
            "get": _oas3_op(resp_props={}),
            "parameters": [{"name": "shared", "in": "query", "schema": {"type": "string"}}],
        }}}
        assert set(normalize_document(doc)) == {("/x", "GET")}


# ── 破坏性判定 ─────────────────────────────────────────────────────

class TestBreakingDetection:
    def test_removed_query_param_is_breaking(self):
        old = _doc(_oas3_op(params=[{"name": "topic", "in": "query",
                                     "required": False, "schema": {"type": "string"}}],
                            resp_props={}))
        new = _doc(_oas3_op(resp_props={}))
        findings, stats = diff_documents(old, new)
        assert stats["breaking"] == 1
        assert "参数被移除" in _levels(findings)[BREAKING][0]

    def test_optional_to_required_param_is_breaking(self):
        old = _doc(_oas3_op(params=[{"name": "days", "in": "query",
                                     "required": False, "schema": {"type": "integer"}}],
                            resp_props={}))
        new = _doc(_oas3_op(params=[{"name": "days", "in": "query",
                                     "required": True, "schema": {"type": "integer"}}],
                            resp_props={}))
        findings, stats = diff_documents(old, new)
        assert stats["breaking"] == 1
        assert "必填" in _levels(findings)[BREAKING][0]

    def test_param_type_change_is_breaking(self):
        old = _doc(_oas3_op(params=[{"name": "n", "in": "query",
                                     "required": False, "schema": {"type": "integer"}}],
                            resp_props={}))
        new = _doc(_oas3_op(params=[{"name": "n", "in": "query",
                                     "required": False, "schema": {"type": "string"}}],
                            resp_props={}))
        _findings, stats = diff_documents(old, new)
        assert stats["breaking"] == 1

    def test_removed_response_field_is_breaking(self):
        old = _doc(_oas3_op(resp_props={"a": {"type": "string"}, "b": {"type": "integer"}}))
        new = _doc(_oas3_op(resp_props={"a": {"type": "string"}}))
        findings, stats = diff_documents(old, new)
        assert stats["breaking"] == 1
        assert "响应字段消失" in _levels(findings)[BREAKING][0]

    def test_new_required_body_field_is_breaking(self):
        old = _doc(_oas3_op(body={"type": "object",
                                  "properties": {"a": {"type": "string"}},
                                  "required": ["a"]}))
        new = _doc(_oas3_op(body={"type": "object",
                                  "properties": {"a": {"type": "string"}, "b": {"type": "string"}},
                                  "required": ["a", "b"]}))
        findings, stats = diff_documents(old, new)
        assert stats["breaking"] == 1
        assert "必填" in _levels(findings)[BREAKING][0]

    def test_lost_2xx_status_is_breaking(self):
        old = _doc(_oas3_op(resp_props={}, statuses=("200", "202")))
        new = _doc(_oas3_op(resp_props={}, statuses=("200",)))
        _findings, stats = diff_documents(old, new)
        assert stats["breaking"] == 1

    def test_deleted_endpoint_only_breaking_in_snapshot_mode(self):
        old = _doc(_oas3_op(resp_props={}))
        new = {"openapi": "3.1.0", "paths": {}}
        assert diff_documents(old, new, only_common=True)[1]["breaking"] == 0
        assert diff_documents(old, new, only_common=False)[1]["breaking"] == 1


class TestCompatibleChanges:
    def test_added_optional_param_is_compatible(self):
        old = _doc(_oas3_op(resp_props={}))
        new = _doc(_oas3_op(params=[{"name": "n", "in": "query",
                                     "required": False, "schema": {"type": "string"}}],
                            resp_props={}))
        _findings, stats = diff_documents(old, new)
        assert stats["breaking"] == 0
        assert stats["compatible"] == 1

    def test_added_response_field_is_compatible(self):
        old = _doc(_oas3_op(resp_props={"a": {"type": "string"}}))
        new = _doc(_oas3_op(resp_props={"a": {"type": "string"}, "b": {"type": "integer"}}))
        _findings, stats = diff_documents(old, new)
        assert stats["breaking"] == 0

    def test_required_to_optional_is_compatible(self):
        old = _doc(_oas3_op(params=[{"name": "n", "in": "query",
                                     "required": True, "schema": {"type": "string"}}],
                            resp_props={}))
        new = _doc(_oas3_op(params=[{"name": "n", "in": "query",
                                     "required": False, "schema": {"type": "string"}}],
                            resp_props={}))
        _findings, stats = diff_documents(old, new)
        assert stats["breaking"] == 0
        assert stats["compatible"] == 1

    def test_identical_documents_have_no_findings(self):
        doc = _doc(_oas3_op(params=[{"name": "n", "in": "query", "required": True,
                                     "schema": {"type": "string"}}],
                            resp_props={"a": {"type": "string"}}))
        findings, stats = diff_documents(doc, doc)
        assert findings == []
        assert stats["breaking"] == 0 and stats["compatible"] == 0


# ── 豁免机制 ───────────────────────────────────────────────────────

class TestErrata:
    def test_errata_entries_carry_a_reason(self):
        """豁免必须带依据 —— 空说明等于把门禁关掉。"""
        assert LEGACY_SPEC_ERRATA, "豁免清单不应为空（至少应有 /api/check 的两条）"
        for key, reason in LEGACY_SPEC_ERRATA.items():
            assert len(key) == 3, f"key 应为 (路径, 方法, 字段)：{key}"
            assert reason.strip(), f"{key} 的豁免缺少依据说明"

    def test_errata_only_applies_to_response_fields(self):
        """豁免只作用于响应字段 —— 请求体/参数上的差异必须仍然报出来。"""
        # 构造一个"参数名恰好等于豁免字段名"的场景，确认豁免不会误放行
        path, method, field = next(iter(LEGACY_SPEC_ERRATA))
        old = {"/x": {method: _oas3_op(
            params=[{"name": field, "in": "query", "required": False,
                     "schema": {"type": "integer"}}], resp_props={})}}
        new = {"/x": {method: _oas3_op(resp_props={})}}
        old_doc = {"openapi": "3.1.0", "paths": old}
        new_doc = {"openapi": "3.1.0", "paths": new}
        _findings, stats = diff_documents(old_doc, new_doc)
        assert stats["breaking"] == 1, (
            f"字段名 {field!r} 的豁免不应影响**查询参数**的删除判定"
        )
