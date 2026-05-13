"""
gcjp/safety_checker.py
API 白名单校验器
在执行 LLM 生成的 GCJP 代码前，静态分析其是否只调用了受限 API
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field

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


# 从 ALLOWED_BUILDER_METHODS 派生的白名单文案，避免在建议中 hardcode 出现漂移。
_ALLOWED_BUILDER_METHODS_DISPLAY = "、".join(sorted(ALLOWED_BUILDER_METHODS))


# 违规建议文案（按 violation code 分类）
_SUGGESTIONS: dict[str, str] = {
    "FORBIDDEN_PATTERN": "该字符串模式被文本扫描禁止，请删除相关代码。",
    "SYNTAX_ERROR": "请修正 GCJP 代码的语法错误后重试。",
    "DISALLOWED_IMPORT": (
        "GCJP 代码仅允许 `from gcjp.mission_graph import TaskGraphBuilder`，"
        "请删除其他模块的 import 语句。"
    ),
    "DISALLOWED_IMPORT_FROM": (
        "仅允许从 gcjp.mission_graph 导入 TaskGraphBuilder。"
    ),
    "DISALLOWED_BUILDER_METHOD": (
        f"请改用 GCJP v1 受支持的结构化 API：{_ALLOWED_BUILDER_METHODS_DISPLAY}。"
    ),
    "INVALID_METHOD_CALLER": (
        "方法应作用于 TaskGraphBuilder 实例（通常命名为 g），"
        "请确认调用者由 TaskGraphBuilder(...) 构造。"
    ),
    "DISALLOWED_BUILTIN_CALL": (
        "GCJP 仅允许有限的内建函数集合，请避免调用其他顶层函数。"
    ),
    "FORBIDDEN_DUNDER_ATTR": (
        "禁止访问 dunder 属性以避免突破沙箱限制。"
    ),
    "FORBIDDEN_SYNTAX": (
        "GCJP 是线性 DSL，禁止使用 def/class/for/while/with/try/lambda 等控制结构，"
        "请展开为对 TaskGraphBuilder 的顺序调用。"
    ),
}


# ─────────────────────────────────────────────────────────────────────────────
# 检查结果数据类
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SafetyViolation:
    """结构化违规记录：携带错误码、行号、源码片段与修复建议，便于反馈给 LLM。"""
    code: str
    message: str
    lineno: int | None = None
    col_offset: int | None = None
    source_line: str | None = None
    suggestion: str | None = None


@dataclass
class SafetyCheckResult:
    passed: bool
    violations: list[str]
    warnings: list[str]
    structured_violations: list[SafetyViolation] = field(default_factory=list)

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
        structured: list[SafetyViolation] = []
        warnings: list[str] = []
        code_lines = code.splitlines()

        def _source_at(lineno: int | None) -> str | None:
            if lineno is None or not (1 <= lineno <= len(code_lines)):
                return None
            return code_lines[lineno - 1]

        def _record(message: str, vcode: str, *,
                    lineno: int | None = None,
                    col_offset: int | None = None) -> None:
            violations.append(message)
            structured.append(SafetyViolation(
                code=vcode,
                message=message,
                lineno=lineno,
                col_offset=col_offset,
                source_line=_source_at(lineno),
                suggestion=_SUGGESTIONS.get(vcode),
            ))

        # ── 1. 文本级禁用模式扫描 ──────────────────────────────────────────
        for pattern, reason in FORBIDDEN_PATTERNS:
            if pattern not in code:
                continue
            first_lineno: int | None = None
            for ln, line in enumerate(code_lines, start=1):
                if pattern in line:
                    first_lineno = ln
                    break
            _record(
                f"[文本扫描] {reason} (检测到: '{pattern}')",
                "FORBIDDEN_PATTERN",
                lineno=first_lineno,
            )

        # ── 2. AST 解析 ───────────────────────────────────────────────────
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            _record(
                f"[语法错误] 代码无法解析: {e}",
                "SYNTAX_ERROR",
                lineno=e.lineno,
                col_offset=e.offset,
            )
            return SafetyCheckResult(
                passed=False,
                violations=violations,
                warnings=warnings,
                structured_violations=structured,
            )

        visitor = _ASTVisitor()
        visitor.visit(tree)

        # ── 3. 检查导入 ───────────────────────────────────────────────────
        for imp, lineno in visitor.imports:
            if imp not in ALLOWED_IMPORTS:
                _record(
                    f"[非法导入] '{imp}' 不在白名单中",
                    "DISALLOWED_IMPORT",
                    lineno=lineno,
                )
        for module, name, lineno in visitor.import_from_names:
            if module == "gcjp.mission_graph" and name != "TaskGraphBuilder":
                _record(
                    f"[非法导入] 不允许从 '{module}' 导入 '{name}'",
                    "DISALLOWED_IMPORT_FROM",
                    lineno=lineno,
                )

        # ── 4. 检查方法调用 ───────────────────────────────────────────────
        for obj, method, lineno, col_offset in visitor.method_calls:
            if method not in ALLOWED_BUILDER_METHODS:
                _record(
                    f"[非法调用] '{obj}.{method}()' —— 方法 '{method}' 不在白名单中",
                    "DISALLOWED_BUILDER_METHOD",
                    lineno=lineno,
                    col_offset=col_offset,
                )
            elif obj not in visitor.builder_vars:
                _record(
                    f"[非法调用] '{obj}.{method}()' —— 调用对象不是 TaskGraphBuilder 实例",
                    "INVALID_METHOD_CALLER",
                    lineno=lineno,
                    col_offset=col_offset,
                )

        # ── 5. 检查内建函数调用 ───────────────────────────────────────────
        for func, lineno in visitor.builtin_calls:
            if func in visitor.builder_constructor_names:
                continue
            if func not in ALLOWED_BUILTINS:
                _record(
                    f"[非法调用] 使用了未在白名单中的顶层函数: '{func}'",
                    "DISALLOWED_BUILTIN_CALL",
                    lineno=lineno,
                )

        # ── 6. 检查危险属性访问 ───────────────────────────────────────────
        for attr, lineno in visitor.attribute_accesses:
            if attr.startswith("__") and attr.endswith("__"):
                _record(
                    f"[危险属性] 访问了 dunder 属性: '{attr}'",
                    "FORBIDDEN_DUNDER_ATTR",
                    lineno=lineno,
                )

        for node_type, lineno in visitor.forbidden_nodes:
            _record(
                f"[非法语法] 不允许在 GCJP 代码中定义 {node_type} (line {lineno})",
                "FORBIDDEN_SYNTAX",
                lineno=lineno,
            )

        passed = len(violations) == 0
        return SafetyCheckResult(
            passed=passed,
            violations=violations,
            warnings=warnings,
            structured_violations=structured,
        )


class _ASTVisitor(ast.NodeVisitor):
    """内部 AST 遍历器，收集各类调用信息（含行号 / 列号）"""

    def __init__(self):
        self.imports: list[tuple[str, int]] = []                       # (module, lineno)
        self.import_from_names: list[tuple[str, str, int]] = []        # (module, name, lineno)
        self.method_calls: list[tuple[str, str, int, int]] = []        # (obj, method, lineno, col)
        self.builtin_calls: list[tuple[str, int]] = []                 # (func, lineno)
        self.attribute_accesses: list[tuple[str, int]] = []            # (attr, lineno)
        self.builder_constructor_names: set[str] = {"TaskGraphBuilder"}
        self.builder_vars: set[str] = set()
        self.forbidden_nodes: list[tuple[str, int]] = []

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            self.imports.append((alias.name, node.lineno))

    def visit_ImportFrom(self, node: ast.ImportFrom):
        if node.module:
            self.imports.append((node.module, node.lineno))
            for alias in node.names:
                self.import_from_names.append((node.module, alias.name, node.lineno))
                if node.module == "gcjp.mission_graph" and alias.name == "TaskGraphBuilder":
                    self.builder_constructor_names.add(alias.asname or alias.name)

    def visit_Assign(self, node: ast.Assign):
        if self._is_builder_constructor_call(node.value):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.builder_vars.add(target.id)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        lineno = getattr(node, "lineno", -1)
        col_offset = getattr(node, "col_offset", -1)
        if isinstance(node.func, ast.Attribute):
            # obj.method() 形式
            obj_name = ""
            if isinstance(node.func.value, ast.Name):
                obj_name = node.func.value.id
            self.method_calls.append((obj_name, node.func.attr, lineno, col_offset))
        elif isinstance(node.func, ast.Name):
            # func() 形式（内建或顶层函数）
            self.builtin_calls.append((node.func.id, lineno))
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute):
        self.attribute_accesses.append((node.attr, getattr(node, "lineno", -1)))
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

    def visit_For(self, node: ast.For):
        self.forbidden_nodes.append(("for loop", node.lineno))
        self.generic_visit(node)

    def visit_While(self, node: ast.While):
        self.forbidden_nodes.append(("while loop", node.lineno))
        self.generic_visit(node)

    def visit_With(self, node: ast.With):
        self.forbidden_nodes.append(("with block", node.lineno))
        self.generic_visit(node)

    def visit_Try(self, node: ast.Try):
        self.forbidden_nodes.append(("try block", node.lineno))
        self.generic_visit(node)

    def visit_Lambda(self, node: ast.Lambda):
        self.forbidden_nodes.append(("lambda", getattr(node, "lineno", -1)))
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
