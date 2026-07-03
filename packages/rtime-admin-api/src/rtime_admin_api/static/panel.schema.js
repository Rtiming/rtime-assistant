// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"use strict";
/*
 * 纯逻辑:schema → 表单字段模型、值强制转换、改动采集。无 DOM、无网络,便于单测
 * (tests/test_panel_schema_logic.py 用 node 跑,或人工审阅)。挂到 window.PanelSchema。
 *
 * 设计要求:表单 100% 由 GET /v1/schema 生成,绝不手写字段。本模块把一个模块的
 * JSON-Schema properties 翻译成一组“字段描述符”,DOM 层据此渲染控件;提交时只回收
 * “相对当前值发生改动”的键(密文字段留空=不改)。
 */
(function (root) {
  // 从一个 property 解出可编辑的标量类型(处理 pydantic 的 anyOf[..., {type:null}])。
  function fieldType(prop) {
    if (!prop) return "string";
    if (prop.enum) return "enum";
    if (prop.type) return prop.type;
    if (Array.isArray(prop.anyOf)) {
      const first = prop.anyOf.find((x) => x && x.type && x.type !== "null");
      if (first) return first.type;
    }
    return "string";
  }

  // 某路径的 rtime 元数据(secret / reload / scope),从已取的整份 schema 读。
  function fieldMeta(schema, path) {
    const dot = path.indexOf(".");
    const mod = path.slice(0, dot);
    const field = path.slice(dot + 1);
    const props = ((((schema || {}).modules || {})[mod] || {}).properties) || {};
    const prop = props[field] || {};
    return {
      secret: !!prop["x-secret"],
      reload: prop["x-reload"] || "",
      scope: prop["x-scope"] || null,
      type: fieldType(prop),
      enum: prop.enum || null,
      description: prop.description || "",
    };
  }

  // 把整份 schema 展开成有序的字段描述符列表,按 module 分组。DOM 层照此渲染。
  function fieldDescriptors(schema, values) {
    values = values || {};
    const mods = (schema && schema.modules) || {};
    const out = [];
    Object.keys(mods).sort().forEach((mod) => {
      const props = (mods[mod].properties) || {};
      const fields = [];
      Object.keys(props).forEach((field) => {
        const path = mod + "." + field;
        const prop = props[field];
        const secret = !!prop["x-secret"];
        const cell = values[path];
        const cur = cell && typeof cell === "object" && "value" in cell ? cell.value : cell;
        fields.push({
          path: path,
          type: fieldType(prop),
          secret: secret,
          reload: prop["x-reload"] || "",
          scope: prop["x-scope"] || null,
          enum: prop.enum || null,
          description: prop.description || "",
          current: cur === undefined ? null : cur,
          // 密文永远不把原值放进 orig(界面只显示 ***),留空即“不改”。
          orig: secret ? null : (cur === undefined ? null : cur),
        });
      });
      out.push({ module: mod, fields: fields });
    });
    return out;
  }

  // 把一个字符串输入按声明类型强制转换;空串→null(=清除/不设)。
  function coerce(raw, type) {
    if (raw === "" || raw == null) return null;
    if (type === "integer") { const n = parseInt(raw, 10); return Number.isNaN(n) ? raw : n; }
    if (type === "number") { const n = parseFloat(raw); return Number.isNaN(n) ? raw : n; }
    if (type === "boolean") return raw === "true" || raw === true;
    if (type === "array" || type === "object") {
      try { return JSON.parse(raw); } catch (e) { return raw; }
    }
    return raw; // string / enum
  }

  // 判断一个输入相对原值是否真的改了(用 JSON 规范化比较,避免 "1" vs 1 误判)。
  function isChanged(parsed, orig) {
    return JSON.stringify(parsed) !== JSON.stringify(orig === undefined ? null : orig);
  }

  /*
   * 从“表单读数数组”回收改动集。每项:
   *   {path, type, secret, raw, orig}
   * 语义:
   *   - 密文字段:raw 为空 → 不提交;非空 → 原样提交字符串(交后端校验)。
   *   - 普通字段:coerce 后与 orig 相同 → 不提交;不同 → 提交。
   * 返回 {path: value}。纯函数,DOM 层负责把控件读成 readings。
   */
  function collectChangesFrom(readings) {
    const changes = {};
    (readings || []).forEach((r) => {
      if (r.secret) {
        if (r.raw === "" || r.raw == null) return;
        changes[r.path] = r.raw;
        return;
      }
      const parsed = coerce(r.raw, r.type);
      if (isChanged(parsed, r.orig)) changes[r.path] = parsed;
    });
    return changes;
  }

  root.PanelSchema = {
    fieldType: fieldType,
    fieldMeta: fieldMeta,
    fieldDescriptors: fieldDescriptors,
    coerce: coerce,
    isChanged: isChanged,
    collectChangesFrom: collectChangesFrom,
  };

  // node/CommonJS 导出(供测试 require)
  if (typeof module !== "undefined" && module.exports) module.exports = root.PanelSchema;
})(typeof window !== "undefined" ? window : globalThis);
