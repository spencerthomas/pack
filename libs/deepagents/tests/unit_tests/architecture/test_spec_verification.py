"""Verify that spec files (ghost libraries) match actual source code.

Uses ast parsing to confirm that public functions, classes, and methods
declared in docs/specs/*.spec.md actually exist in the source modules.
When a spec drifts from code, assertion messages point to the spec file
that needs updating.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# Root paths
_REPO = Path(__file__).resolve().parents[5]  # /Users/.../pack
_DEEPAGENTS = _REPO / "libs" / "deepagents" / "deepagents"
_SPECS = _REPO / "docs" / "specs"


def _parse_module(path: Path) -> ast.Module:
    """Parse a Python file and return the AST module node."""
    return ast.parse(path.read_text(), filename=str(path))


def _top_level_names(tree: ast.Module) -> set[str]:
    """Return all top-level function and class names from an AST."""
    names: set[str] = set()
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
    return names


def _class_method_names(tree: ast.Module, class_name: str) -> set[str]:
    """Return all method names for a class in an AST."""
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            methods: set[str] = set()
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods.add(item.name)
            return methods
    return set()


# ---------------------------------------------------------------------------
# Graph Assembly Spec Verification
# ---------------------------------------------------------------------------


class TestGraphAssemblySpec:
    """Verify docs/specs/graph-assembly.spec.md matches graph.py."""

    SPEC = "graph-assembly.spec.md"
    SOURCE = _DEEPAGENTS / "graph.py"

    # Public functions declared in the spec
    EXPECTED_PUBLIC_FUNCTIONS = [
        "create_deep_agent",
        "get_default_model",
    ]

    # Internal functions declared in the spec
    EXPECTED_INTERNAL_FUNCTIONS = [
        "_add_pack_middleware",
        "_resolve_extra_middleware",
        "_harness_profile_for_model",
        "_tool_name",
        "_apply_tool_description_overrides",
    ]

    @pytest.fixture(autouse=True)
    def _parse(self) -> None:
        self.tree = _parse_module(self.SOURCE)
        self.names = _top_level_names(self.tree)

    @pytest.mark.parametrize("func", EXPECTED_PUBLIC_FUNCTIONS)
    def test_public_function_exists(self, func: str) -> None:
        assert func in self.names, (
            f"Function '{func}' declared in docs/specs/{self.SPEC} "
            f"not found in {self.SOURCE.relative_to(_REPO)}. "
            f"Update docs/specs/{self.SPEC} if the function was renamed or removed."
        )

    @pytest.mark.parametrize("func", EXPECTED_INTERNAL_FUNCTIONS)
    def test_internal_function_exists(self, func: str) -> None:
        assert func in self.names, (
            f"Function '{func}' declared in docs/specs/{self.SPEC} "
            f"not found in {self.SOURCE.relative_to(_REPO)}. "
            f"Update docs/specs/{self.SPEC} if the function was renamed or removed."
        )

    def test_create_deep_agent_parameters(self) -> None:
        """Verify create_deep_agent has the parameters declared in the spec."""
        expected_params = [
            "model", "tools", "system_prompt", "middleware", "subagents",
            "skills", "memory", "permissions", "response_format",
            "context_schema", "checkpointer", "store", "backend",
            "interrupt_on", "debug", "name", "cache",
        ]
        for node in ast.iter_child_nodes(self.tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "create_deep_agent":
                actual_params = [
                    arg.arg for arg in node.args.args + node.args.kwonlyargs
                ]
                for param in expected_params:
                    assert param in actual_params, (
                        f"Parameter '{param}' declared in docs/specs/{self.SPEC} "
                        f"not found in create_deep_agent(). "
                        f"Update docs/specs/{self.SPEC} if the parameter was renamed or removed."
                    )
                return
        pytest.fail(f"create_deep_agent not found in {self.SOURCE}")

    def test_spec_file_exists(self) -> None:
        assert (_SPECS / self.SPEC).exists(), (
            f"Spec file docs/specs/{self.SPEC} is missing. "
            f"Regenerate it from the source."
        )


# ---------------------------------------------------------------------------
# Middleware Contract Spec Verification
# ---------------------------------------------------------------------------


class TestMiddlewareContractSpec:
    """Verify docs/specs/middleware-contract.spec.md matches middleware/pack/."""

    SPEC = "middleware-contract.spec.md"
    PACK_DIR = _DEEPAGENTS / "middleware" / "pack"

    # Middleware classes declared in the spec and their source files
    EXPECTED_MIDDLEWARE = {
        "CostMiddleware": "cost_middleware.py",
        "PermissionMiddleware": "permission_middleware.py",
        "CompactionMiddleware": "compaction_middleware.py",
        "PackMemoryMiddleware": "memory_middleware.py",
        "HooksMiddleware": "hooks_middleware.py",
    }

    # Support modules declared in the spec
    EXPECTED_SUPPORT_MODULES = [
        "state.py",
        "agent_dispatch.py",
        "parallel_middleware.py",
    ]

    # Classes/functions declared in agent_dispatch.py
    EXPECTED_DISPATCH_FUNCTIONS = [
        "resolve_agent_profile",
        "build_subagent_spec",
        "create_teammate_config",
    ]

    # PackState declared fields
    EXPECTED_PACK_STATE_FIELDS = [
        "cost_tracker", "permission_pipeline", "collapser",
        "compaction_monitor", "hook_engine", "memory_index",
        "parallel_executor", "data_dir",
    ]

    def test_middleware_files_exist(self) -> None:
        for cls_name, filename in self.EXPECTED_MIDDLEWARE.items():
            path = self.PACK_DIR / filename
            assert path.exists(), (
                f"File '{filename}' for {cls_name} declared in docs/specs/{self.SPEC} "
                f"does not exist. Update docs/specs/{self.SPEC}."
            )

    @pytest.mark.parametrize(
        "cls_name,filename",
        list(EXPECTED_MIDDLEWARE.items()),
    )
    def test_middleware_class_exists_in_file(self, cls_name: str, filename: str) -> None:
        path = self.PACK_DIR / filename
        tree = _parse_module(path)
        names = _top_level_names(tree)
        assert cls_name in names, (
            f"Class '{cls_name}' declared in docs/specs/{self.SPEC} "
            f"not found in {filename}. Update docs/specs/{self.SPEC}."
        )

    @pytest.mark.parametrize("module", EXPECTED_SUPPORT_MODULES)
    def test_support_module_exists(self, module: str) -> None:
        path = self.PACK_DIR / module
        assert path.exists(), (
            f"Support module '{module}' declared in docs/specs/{self.SPEC} "
            f"does not exist. Update docs/specs/{self.SPEC}."
        )

    @pytest.mark.parametrize("func", EXPECTED_DISPATCH_FUNCTIONS)
    def test_dispatch_function_exists(self, func: str) -> None:
        tree = _parse_module(self.PACK_DIR / "agent_dispatch.py")
        names = _top_level_names(tree)
        assert func in names, (
            f"Function '{func}' declared in docs/specs/{self.SPEC} "
            f"not found in agent_dispatch.py. Update docs/specs/{self.SPEC}."
        )

    def test_pack_state_class_exists(self) -> None:
        tree = _parse_module(self.PACK_DIR / "state.py")
        names = _top_level_names(tree)
        assert "PackState" in names, (
            f"Class 'PackState' declared in docs/specs/{self.SPEC} "
            f"not found in state.py. Update docs/specs/{self.SPEC}."
        )

    def test_pack_state_singleton_functions(self) -> None:
        tree = _parse_module(self.PACK_DIR / "state.py")
        names = _top_level_names(tree)
        for func in ["get_state", "set_state", "clear_state"]:
            assert func in names, (
                f"Function '{func}' declared in docs/specs/{self.SPEC} "
                f"not found in state.py. Update docs/specs/{self.SPEC}."
            )

    def test_init_exports_match_spec(self) -> None:
        """Verify __all__ in __init__.py contains all spec-declared exports."""
        tree = _parse_module(self.PACK_DIR / "__init__.py")
        # Extract __all__ list
        all_names: set[str] = set()
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "__all__":
                        if isinstance(node.value, ast.List):
                            for elt in node.value.elts:
                                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                    all_names.add(elt.value)

        expected_exports = {
            "CompactionMiddleware", "CostMiddleware", "HooksMiddleware",
            "PackMemoryMiddleware", "PermissionMiddleware", "PackState",
            "build_subagent_spec", "clear_state", "create_teammate_config",
            "get_state", "resolve_agent_profile", "set_state",
        }
        for name in expected_exports:
            assert name in all_names, (
                f"Export '{name}' declared in docs/specs/{self.SPEC} "
                f"not found in __init__.py __all__. Update docs/specs/{self.SPEC}."
            )

    def test_spec_file_exists(self) -> None:
        assert (_SPECS / self.SPEC).exists()


# ---------------------------------------------------------------------------
# Prompt Assembly Spec Verification
# ---------------------------------------------------------------------------


class TestPromptAssemblySpec:
    """Verify docs/specs/prompt-assembly.spec.md matches prompt modules."""

    SPEC = "prompt-assembly.spec.md"
    BUILDER = _DEEPAGENTS / "prompt" / "builder.py"
    SECTIONS = _DEEPAGENTS / "prompt" / "sections.py"
    CACHE_STRATEGY = _DEEPAGENTS / "prompt" / "cache_strategy.py"

    # Section factory functions declared in the spec
    EXPECTED_SECTION_FACTORIES = [
        "identity_section",
        "safety_section",
        "tool_rules_section",
        "style_section",
        "environment_section",
        "git_section",
    ]

    # Builder methods declared in the spec
    EXPECTED_BUILDER_METHODS = [
        "__init__",
        "add_static_section",
        "add_dynamic_section",
        "build",
        "build_text",
        "_collect_sections",
    ]

    # Cache strategy classes declared in the spec
    EXPECTED_CACHE_STRATEGIES = [
        "AnthropicCacheStrategy",
        "OpenAICacheStrategy",
        "DefaultCacheStrategy",
    ]

    @pytest.mark.parametrize("func", EXPECTED_SECTION_FACTORIES)
    def test_section_factory_exists(self, func: str) -> None:
        tree = _parse_module(self.SECTIONS)
        names = _top_level_names(tree)
        assert func in names, (
            f"Section factory '{func}' declared in docs/specs/{self.SPEC} "
            f"not found in sections.py. Update docs/specs/{self.SPEC}."
        )

    def test_prompt_section_dataclass_exists(self) -> None:
        tree = _parse_module(self.SECTIONS)
        names = _top_level_names(tree)
        assert "PromptSection" in names, (
            f"PromptSection declared in docs/specs/{self.SPEC} "
            f"not found in sections.py. Update docs/specs/{self.SPEC}."
        )

    def test_system_prompt_builder_exists(self) -> None:
        tree = _parse_module(self.BUILDER)
        names = _top_level_names(tree)
        assert "SystemPromptBuilder" in names, (
            f"SystemPromptBuilder declared in docs/specs/{self.SPEC} "
            f"not found in builder.py. Update docs/specs/{self.SPEC}."
        )

    @pytest.mark.parametrize("method", EXPECTED_BUILDER_METHODS)
    def test_builder_method_exists(self, method: str) -> None:
        tree = _parse_module(self.BUILDER)
        methods = _class_method_names(tree, "SystemPromptBuilder")
        # Properties show up as functions in AST, check for decorated ones too
        assert method in methods, (
            f"Method '{method}' declared in docs/specs/{self.SPEC} "
            f"not found in SystemPromptBuilder. Update docs/specs/{self.SPEC}."
        )

    @pytest.mark.parametrize("cls", EXPECTED_CACHE_STRATEGIES)
    def test_cache_strategy_class_exists(self, cls: str) -> None:
        tree = _parse_module(self.CACHE_STRATEGY)
        names = _top_level_names(tree)
        assert cls in names, (
            f"Cache strategy '{cls}' declared in docs/specs/{self.SPEC} "
            f"not found in cache_strategy.py. Update docs/specs/{self.SPEC}."
        )

    def test_cache_strategy_protocol_exists(self) -> None:
        tree = _parse_module(self.CACHE_STRATEGY)
        names = _top_level_names(tree)
        assert "CacheStrategy" in names, (
            f"CacheStrategy protocol declared in docs/specs/{self.SPEC} "
            f"not found in cache_strategy.py. Update docs/specs/{self.SPEC}."
        )

    def test_detect_strategy_factory_exists(self) -> None:
        tree = _parse_module(self.CACHE_STRATEGY)
        names = _top_level_names(tree)
        assert "detect_strategy" in names, (
            f"detect_strategy declared in docs/specs/{self.SPEC} "
            f"not found in cache_strategy.py. Update docs/specs/{self.SPEC}."
        )

    def test_spec_file_exists(self) -> None:
        assert (_SPECS / self.SPEC).exists()


# ---------------------------------------------------------------------------
# Permission Pipeline Spec Verification
# ---------------------------------------------------------------------------


class TestPermissionPipelineSpec:
    """Verify docs/specs/permission-pipeline.spec.md matches permissions/."""

    SPEC = "permission-pipeline.spec.md"
    PIPELINE = _DEEPAGENTS / "permissions" / "pipeline.py"
    CIRCUIT_BREAKER = _DEEPAGENTS / "permissions" / "circuit_breaker.py"
    RULES = _DEEPAGENTS / "permissions" / "rules.py"
    CLASSIFIER = _DEEPAGENTS / "permissions" / "classifier.py"
    SDK_PERMISSIONS = _DEEPAGENTS / "middleware" / "permissions.py"

    # Pipeline methods declared in the spec
    EXPECTED_PIPELINE_METHODS = [
        "__init__",
        "evaluate",
        "learn_from_user",
        "format_denial_feedback",
    ]

    # Enums declared in the spec
    EXPECTED_ENUMS = [
        "Decision",
        "RiskLevel",
    ]

    # Pipeline properties declared in the spec
    EXPECTED_PIPELINE_PROPERTIES = [
        "rule_store",
        "circuit_breaker",
    ]

    def test_pipeline_class_exists(self) -> None:
        tree = _parse_module(self.PIPELINE)
        names = _top_level_names(tree)
        assert "PermissionPipeline" in names, (
            f"PermissionPipeline declared in docs/specs/{self.SPEC} "
            f"not found in pipeline.py. Update docs/specs/{self.SPEC}."
        )

    @pytest.mark.parametrize("method", EXPECTED_PIPELINE_METHODS)
    def test_pipeline_method_exists(self, method: str) -> None:
        tree = _parse_module(self.PIPELINE)
        methods = _class_method_names(tree, "PermissionPipeline")
        assert method in methods, (
            f"Method '{method}' declared in docs/specs/{self.SPEC} "
            f"not found in PermissionPipeline. Update docs/specs/{self.SPEC}."
        )

    @pytest.mark.parametrize("prop", EXPECTED_PIPELINE_PROPERTIES)
    def test_pipeline_property_exists(self, prop: str) -> None:
        tree = _parse_module(self.PIPELINE)
        # Properties are functions with @property decorator in AST
        methods = _class_method_names(tree, "PermissionPipeline")
        assert prop in methods, (
            f"Property '{prop}' declared in docs/specs/{self.SPEC} "
            f"not found in PermissionPipeline. Update docs/specs/{self.SPEC}."
        )

    @pytest.mark.parametrize("enum_name", EXPECTED_ENUMS)
    def test_enum_exists(self, enum_name: str) -> None:
        tree = _parse_module(self.PIPELINE)
        names = _top_level_names(tree)
        assert enum_name in names, (
            f"Enum '{enum_name}' declared in docs/specs/{self.SPEC} "
            f"not found in pipeline.py. Update docs/specs/{self.SPEC}."
        )

    def test_pipeline_result_exists(self) -> None:
        tree = _parse_module(self.PIPELINE)
        names = _top_level_names(tree)
        assert "PipelineResult" in names, (
            f"PipelineResult declared in docs/specs/{self.SPEC} "
            f"not found in pipeline.py. Update docs/specs/{self.SPEC}."
        )

    def test_circuit_breaker_class_exists(self) -> None:
        tree = _parse_module(self.CIRCUIT_BREAKER)
        names = _top_level_names(tree)
        assert "CircuitBreaker" in names, (
            f"CircuitBreaker declared in docs/specs/{self.SPEC} "
            f"not found in circuit_breaker.py. Update docs/specs/{self.SPEC}."
        )

    def test_circuit_breaker_methods(self) -> None:
        tree = _parse_module(self.CIRCUIT_BREAKER)
        methods = _class_method_names(tree, "CircuitBreaker")
        for method in ["record_denial", "record_approval", "reset"]:
            assert method in methods, (
                f"CircuitBreaker method '{method}' declared in docs/specs/{self.SPEC} "
                f"not found. Update docs/specs/{self.SPEC}."
            )

    def test_rule_store_class_exists(self) -> None:
        tree = _parse_module(self.RULES)
        names = _top_level_names(tree)
        assert "RuleStore" in names, (
            f"RuleStore declared in docs/specs/{self.SPEC} "
            f"not found in rules.py. Update docs/specs/{self.SPEC}."
        )

    def test_rule_store_methods(self) -> None:
        tree = _parse_module(self.RULES)
        methods = _class_method_names(tree, "RuleStore")
        for method in ["match", "add", "remove", "clear"]:
            assert method in methods, (
                f"RuleStore method '{method}' declared in docs/specs/{self.SPEC} "
                f"not found. Update docs/specs/{self.SPEC}."
            )

    def test_permission_rule_class_exists(self) -> None:
        tree = _parse_module(self.RULES)
        names = _top_level_names(tree)
        assert "PermissionRule" in names, (
            f"PermissionRule declared in docs/specs/{self.SPEC} "
            f"not found in rules.py. Update docs/specs/{self.SPEC}."
        )

    def test_classifier_class_exists(self) -> None:
        tree = _parse_module(self.CLASSIFIER)
        names = _top_level_names(tree)
        assert "PermissionClassifier" in names, (
            f"PermissionClassifier declared in docs/specs/{self.SPEC} "
            f"not found in classifier.py. Update docs/specs/{self.SPEC}."
        )

    def test_classifier_decision_enum_exists(self) -> None:
        tree = _parse_module(self.CLASSIFIER)
        names = _top_level_names(tree)
        assert "ClassifierDecision" in names, (
            f"ClassifierDecision declared in docs/specs/{self.SPEC} "
            f"not found in classifier.py. Update docs/specs/{self.SPEC}."
        )

    def test_dual_permission_system(self) -> None:
        """Verify both SDK and Pack permission middlewares exist."""
        # SDK _PermissionMiddleware
        tree = _parse_module(self.SDK_PERMISSIONS)
        names = _top_level_names(tree)
        assert "_PermissionMiddleware" in names, (
            f"SDK _PermissionMiddleware declared in docs/specs/{self.SPEC} "
            f"not found in middleware/permissions.py. Update docs/specs/{self.SPEC}."
        )

        # Pack PermissionMiddleware
        pack_path = _DEEPAGENTS / "middleware" / "pack" / "permission_middleware.py"
        tree2 = _parse_module(pack_path)
        names2 = _top_level_names(tree2)
        assert "PermissionMiddleware" in names2, (
            f"Pack PermissionMiddleware declared in docs/specs/{self.SPEC} "
            f"not found in middleware/pack/permission_middleware.py. Update docs/specs/{self.SPEC}."
        )

    def test_spec_file_exists(self) -> None:
        assert (_SPECS / self.SPEC).exists()
