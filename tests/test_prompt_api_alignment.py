"""
tests/test_prompt_api_alignment.py

Guard against generation-prompt / TaskGraphBuilder API drift.

The GCJP generation prompts enumerate the allowed builder methods and show the
call signatures the LLM must imitate. If a prompt permits a method but omits
its signature, the model has to guess parameter names by analogy with other
methods; if a prompt references a parameter that does not exist, the model
copies it verbatim. Either way the generated code fails at execution time with
an opaque ``TypeError`` (e.g. ``add_group_sync_constraint() got an unexpected
keyword argument 'sync_tolerance'``). This test surfaces that whole class of
defect here, against the real signatures in ``gcjp/mission_graph.py`` and the
enums in ``gcjp/api_spec.py``, instead of silently at generation time.
"""
import inspect
import re
import unittest
from pathlib import Path

from gcjp.api_spec import (
    ALLOWED_BUILDER_METHODS,
    RELATION_ALIASES,
    VALID_RELATION_TYPES,
    VALID_RESOURCE_TYPES,
)
from gcjp.mission_graph import TaskGraphBuilder

ROOT = Path(__file__).resolve().parents[1]

# The generation prompts that teach the LLM the GCJP builder API. All four share
# the same "allowed methods + signatures" contract, so all four must stay in
# sync with the real API. (Repair prompt is intentionally out of scope.)
PROMPT_FILES = [
    ROOT / "prompts" / "standard_nl_to_gcjp_prompt.md",
    ROOT / "prompts" / "standard_nl_to_gcjp_prompt_fewshot.md",
    ROOT / "prompts" / "gcjp_generation_prompt.md",
    ROOT / "prompts" / "gcjp_generation_prompt_fewshot.md",
]

# Matches the start of a builder call: g.<method>(
_CALL_RE = re.compile(r"\bg\.(\w+)\s*\(")
# Matches a leading "<identifier> =" (but not "==") at the start of an argument.
_KW_RE = re.compile(r"\s*(\w+)\s*=(?!=)")


def _iter_calls(text):
    """Yield ``(method_name, call_body)`` for every ``g.<method>(...)`` call.

    Walks balanced brackets so multi-line calls and nested ``[]``/``{}`` are
    captured as a single body.
    """
    for match in _CALL_RE.finditer(text):
        method = match.group(1)
        i = match.end()
        body_start = i
        depth = 1
        while i < len(text) and depth > 0:
            ch = text[i]
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
            i += 1
        yield method, text[body_start : i - 1]


def _split_top_level(body):
    """Split a call body on top-level commas (ignoring nested brackets)."""
    segments = []
    depth = 0
    start = 0
    for i, ch in enumerate(body):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "," and depth == 0:
            segments.append(body[start:i])
            start = i + 1
    segments.append(body[start:])
    return segments


def _top_level_kwargs(body):
    """Return the keyword-argument names passed at the top level of a call."""
    kwargs = []
    for segment in _split_top_level(body):
        match = _KW_RE.match(segment)
        if match:
            kwargs.append(match.group(1))
    return kwargs


def _real_params(method):
    """Real parameter names of ``TaskGraphBuilder.<method>`` (minus ``self``)."""
    func = getattr(TaskGraphBuilder, method)
    return {
        name
        for name, param in inspect.signature(func).parameters.items()
        if name != "self" and param.kind is not inspect.Parameter.VAR_KEYWORD
    }


class TestPromptApiAlignment(unittest.TestCase):
    def test_allowed_methods_exist_on_builder(self):
        """Every method api_spec permits must actually exist on the builder."""
        for method in sorted(ALLOWED_BUILDER_METHODS):
            with self.subTest(method=method):
                self.assertTrue(
                    hasattr(TaskGraphBuilder, method),
                    f"api_spec lists '{method}' but TaskGraphBuilder has no "
                    f"such method.",
                )

    def test_every_allowed_method_is_documented(self):
        """Each permitted method must appear as a concrete g.<method>(...) form.

        Completeness guard: a method the prompt allows but never demonstrates
        forces the model to invent its parameters. This is exactly the gap that
        let `add_group_sync_constraint(sync_tolerance=...)` through.
        """
        for path in PROMPT_FILES:
            text = path.read_text(encoding="utf-8")
            called = {method for method, _ in _iter_calls(text)}
            for method in sorted(ALLOWED_BUILDER_METHODS):
                with self.subTest(prompt=path.name, method=method):
                    self.assertIn(
                        method,
                        called,
                        f"{path.name} permits '{method}' but never shows a "
                        f"g.{method}(...) signature or example; the model must "
                        f"guess its parameters.",
                    )

    def test_prompt_calls_use_real_parameters(self):
        """Every kwarg shown in a prompt must be a real parameter of the method.

        Correctness guard: catches drift like `sync_tolerance=` on a method
        whose real parameter is `tolerance`.
        """
        for path in PROMPT_FILES:
            text = path.read_text(encoding="utf-8")
            for method, body in _iter_calls(text):
                if not hasattr(TaskGraphBuilder, method):
                    continue  # reported by test_allowed_methods_exist_on_builder
                real = _real_params(method)
                for kwarg in _top_level_kwargs(body):
                    with self.subTest(prompt=path.name, method=method, kwarg=kwarg):
                        self.assertIn(
                            kwarg,
                            real,
                            f"{path.name}: g.{method}(...) passes '{kwarg}=', "
                            f"which is not a parameter of "
                            f"TaskGraphBuilder.{method}. Real parameters: "
                            f"{sorted(real)}",
                        )

    def test_relation_literals_are_valid(self):
        """Any literal relation="..." in a prompt must be a valid relation."""
        valid = VALID_RELATION_TYPES | set(RELATION_ALIASES)
        relation_re = re.compile(r'relation\s*=\s*"([^"]+)"')
        for path in PROMPT_FILES:
            text = path.read_text(encoding="utf-8")
            for value in relation_re.findall(text):
                if "<" in value:
                    continue  # placeholder such as "<relation>"
                with self.subTest(prompt=path.name, relation=value):
                    self.assertIn(
                        value,
                        valid,
                        f'{path.name}: relation="{value}" is not in '
                        f"VALID_RELATION_TYPES/aliases {sorted(valid)}",
                    )

    def test_resource_type_literals_are_valid(self):
        """The resource-type positional in add_resource_constraint must be valid."""
        for path in PROMPT_FILES:
            text = path.read_text(encoding="utf-8")
            for method, body in _iter_calls(text):
                if method != "add_resource_constraint":
                    continue
                segments = _split_top_level(body)
                if len(segments) < 2:
                    continue
                quoted = re.search(r'"([^"]+)"', segments[1])
                if not quoted or "<" in quoted.group(1):
                    continue  # placeholder such as "<resource_type>"
                with self.subTest(prompt=path.name, resource_type=quoted.group(1)):
                    self.assertIn(
                        quoted.group(1),
                        VALID_RESOURCE_TYPES,
                        f'{path.name}: resource_type "{quoted.group(1)}" is not '
                        f"in VALID_RESOURCE_TYPES {sorted(VALID_RESOURCE_TYPES)}",
                    )


if __name__ == "__main__":
    unittest.main()
