"""
gcjp/safety_checker.py
API 白名单校验器
在执行 LLM 生成的 GCJP 代码前，静态分析其是否只调用了受限 API
"""
from __future__ import annotations

import ast
from dataclasses import dataclass

from gcjp.api_spec import ALLOWED_BUILDER_METHODS, ALLOWED_IMPORTS


# ─────────────────────────────────────────────────────────────────────────────
# 白名单定义
# ─────────────────────────────────────────────────────────────────────────────

# 允许的内建函数（仅以下安全子集）
ALLOWED_BUILTINS = {
    "print", "len", "range", "enumerate", "zip",
    "min", "max", "sum", "abs", "round",
    "str", "int", "float", "bool", "list", "dict", "tuple", "set",
    "isinstance", "hasattr",
}

# 绝对禁止的操作
FORBIDDEN_PATTERNS = [
    ("import os",       "禁止导入 os 模块"),
    ("import sys",      "禁止导入 sys 模块"),
    ("import subprocess","禁止导入 subprocess 模块"),
    ("__import__",      "禁止使用 __import__"),
    ("eval(",           "禁止使用 eval()"),
    ("exec(",           "禁止使用 exec()"),
    ("open(",           "禁止文件操作"),
    ("requests.",       "禁止网络请求"),
    ("socket.",         "禁止 socket 操作"),
]


# ─────────────────────────────────────────────────────────────────────────────
# 检查结果数据类
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SafetyCheckResult:
    passed: bool
    violations: list[str]
    warnings: list[str]

    def summary(self) -> str:
        if self.passed:
            w = f"，{len(self.warnings)} 条警告" if self.warnings else ""
            return f"✅ 安全检查通过{w}"
        else:
            return (f"❌ 安全检查失败，{len(self.violations)} 条违规:\n"
                    + "\n".join(f"  - {v}" for v in self.violations))


# ─────────────────────────────────────────────────────────────────────────────
# 主检查器
# ─────────────────────────────────────────────────────────────────────────────

class SafetyChecker:
    """
    对 LLM 生成的 GCJP 代码做静态安全分析。
    使用 AST 解析，不执行代码。
    """

    def check(self, code: str) -> SafetyCheckResult:
        violations: list[str] = []
        warnings: list[str] = []

        # ── 1. 文本级禁用模式扫描 ──────────────────────────────────────────
        for pattern, reason in FORBIDDEN_PATTERNS:
            if pattern in code:
                violations.append(f"[文本扫描] {reason} (检测到: '{pattern}')")

        # ── 2. AST 解析 ───────────────────────────────────────────────────
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            violations.append(f"[语法错误] 代码无法解析: {e}")
            return SafetyCheckResult(passed=False,
                                     violations=violations, warnings=warnings)

        visitor = _ASTVisitor()
        visitor.visit(tree)

        # ── 3. 检查导入 ───────────────────────────────────────────────────
        for imp in visitor.imports:
            if imp not in ALLOWED_IMPORTS:
                violations.append(f"[非法导入] '{imp}' 不在白名单中")
        for module, name in visitor.import_from_names:
            if module == "gcjp.mission_graph" and name != "TaskGraphBuilder":
                violations.append(f"[非法导入] 不允许从 '{module}' 导入 '{name}'")

        # ── 4. 检查方法调用 ───────────────────────────────────────────────
        for call in visitor.method_calls:
            obj, method = call
            if method not in ALLOWED_BUILDER_METHODS:
                violations.append(
                    f"[非法调用] '{obj}.{method}()' —— 方法 '{method}' 不在白名单中"
                )
            elif obj not in visitor.builder_vars:
                violations.append(
                    f"[非法调用] '{obj}.{method}()' —— 调用对象不是 TaskGraphBuilder 实例"
                )

        # ── 5. 检查内建函数调用 ───────────────────────────────────────────
        for func in visitor.builtin_calls:
            if func in visitor.builder_constructor_names:
                continue
            if func not in ALLOWED_BUILTINS:
                violations.append(f"[非法调用] 使用了未在白名单中的顶层函数: '{func}'")

        # ── 6. 检查危险属性访问 ───────────────────────────────────────────
        for attr in visitor.attribute_accesses:
            if attr.startswith("__") and attr.endswith("__"):
                violations.append(f"[危险属性] 访问了 dunder 属性: '{attr}'")

        for node_type, lineno in visitor.forbidden_nodes:
            violations.append(f"[非法语法] 不允许在 GCJP 代码中定义 {node_type} (line {lineno})")

        passed = len(violations) == 0
        return SafetyCheckResult(passed=passed,
                                 violations=violations, warnings=warnings)


class _ASTVisitor(ast.NodeVisitor):
    """内部 AST 遍历器，收集各类调用信息"""

    def __init__(self):
        self.imports: list[str] = []
        self.import_from_names: list[tuple[str, str]] = []
        self.method_calls: list[tuple[str, str]] = []   # (object_name, method_name)
        self.builtin_calls: list[str] = []
        self.attribute_accesses: list[str] = []
        self.builder_constructor_names: set[str] = {"TaskGraphBuilder"}
        self.builder_vars: set[str] = set()
        self.forbidden_nodes: list[tuple[str, int]] = []

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            self.imports.append(alias.name)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        if node.module:
            self.imports.append(node.module)
            for alias in node.names:
                self.import_from_names.append((node.module, alias.name))
                if node.module == "gcjp.mission_graph" and alias.name == "TaskGraphBuilder":
                    self.builder_constructor_names.add(alias.asname or alias.name)

    def visit_Assign(self, node: ast.Assign):
        if self._is_builder_constructor_call(node.value):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.builder_vars.add(target.id)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        if isinstance(node.func, ast.Attribute):
            # obj.method() 形式
            obj_name = ""
            if isinstance(node.func.value, ast.Name):
                obj_name = node.func.value.id
            self.method_calls.append((obj_name, node.func.attr))
        elif isinstance(node.func, ast.Name):
            # func() 形式（内建或顶层函数）
            self.builtin_calls.append(node.func.id)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute):
        self.attribute_accesses.append(node.attr)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self.forbidden_nodes.append(("function", node.lineno))
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        self.forbidden_nodes.append(("async function", node.lineno))
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef):
        self.forbidden_nodes.append(("class", node.lineno))
        self.generic_visit(node)

    def _is_builder_constructor_call(self, node: ast.AST) -> bool:
        if not isinstance(node, ast.Call):
            return False
        if isinstance(node.func, ast.Name):
            return node.func.id in self.builder_constructor_names
        return False


# ─────────────────────────────────────────────────────────────────────────────
# 便捷函数
# ─────────────────────────────────────────────────────────────────────────────

def check_gcjp_code(code: str) -> SafetyCheckResult:
    """对 GCJP 代码做安全检查，返回 SafetyCheckResult"""
    return SafetyChecker().check(code)
