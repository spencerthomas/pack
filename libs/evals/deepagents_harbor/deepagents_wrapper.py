"""A wrapper for Deep Agents to run in Harbor environments."""

from __future__ import annotations

import importlib.metadata
import json
import logging
import os
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from deepagents import create_deep_agent
from deepagents.graph import get_default_model
from deepagents_cli.agent import create_cli_agent
from dotenv import load_dotenv
from harbor.agents.base import BaseAgent
from harbor.models.trajectories import (
    Agent,
    FinalMetrics,
    Observation,
    ObservationResult,
    Step,
    ToolCall,
    Trajectory,
)
from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langsmith import trace
from langsmith.client import Client

if TYPE_CHECKING:
    from pathlib import Path

    from harbor.environments.base import BaseEnvironment
    from harbor.models.agent.context import AgentContext
    from langchain.messages import UsageMetadata
    from langchain_core.runnables import RunnableConfig

from deepagents_harbor.backend import HarborSandbox
from deepagents_harbor.metadata import InfraMetadata, collect_sandbox_metadata

_FAILURE_EXCEPTION_MAX_LEN = 2048  # keep the repr small in trajectory JSON


@dataclass(frozen=True)
class _FailureInfo:
    """Structured record of why an invocation terminated unsuccessfully.

    Serialized into ``trajectory.extra["failure"]`` so post-mortem tooling
    (e.g. ``skills/langsmith-trace-analyzer/``) can cluster failures by
    reason and exception class without re-parsing logs.
    """

    reason: str  # "retry_exhausted" | "non_retryable" | "unknown"
    exception_type: str
    attempts: int
    final_exception_repr: str


def _build_failure_info(exc: BaseException, *, attempts: int, reason: str) -> _FailureInfo:
    repr_str = repr(exc)
    if len(repr_str) > _FAILURE_EXCEPTION_MAX_LEN:
        repr_str = repr_str[: _FAILURE_EXCEPTION_MAX_LEN - 3] + "..."
    return _FailureInfo(
        reason=reason,
        exception_type=type(exc).__name__,
        attempts=attempts,
        final_exception_repr=repr_str,
    )


_RETRY_MAX_ATTEMPTS = 3
_RETRY_BASE_DELAY = 2.0  # seconds
_RETRY_JITTER_MAX = 0.5  # seconds
_RETRY_CHAIN_MAX_DEPTH = 10  # guards against cyclic __cause__ chains

_CORE_RETRYABLE_ERROR_TYPES: tuple[type[BaseException], ...] = (
    ConnectionError,
    TimeoutError,
    OSError,
)


def _sdk_retryable_types() -> tuple[type[BaseException], ...]:
    """Collect known-retryable exception types from installed provider SDKs.

    langchain wraps httpx, httpcore, anthropic, and openai; each raises its
    own connection/timeout/protocol exception classes that do NOT inherit
    from ``ConnectionError`` / ``OSError``. Without this widening, the
    dominant 36% Harbor failure mode (OpenRouter/Anthropic disconnects)
    would not be caught by the type-based check and would fall through to
    the string fallback — where the generic-type gate also excludes them.

    Imports are guarded so the wrapper stays importable even when an SDK
    is absent from the environment.
    """
    extra: list[type[BaseException]] = []
    try:
        import httpx

        extra += [
            httpx.ConnectError,
            httpx.ReadError,
            httpx.RemoteProtocolError,
            httpx.TimeoutException,
        ]
    except ImportError:
        pass
    try:
        import httpcore

        extra += [
            httpcore.ConnectError,
            httpcore.ReadError,
            httpcore.RemoteProtocolError,
        ]
    except ImportError:
        pass
    try:
        import anthropic

        extra += [anthropic.APIConnectionError, anthropic.APITimeoutError]
    except ImportError:
        pass
    try:
        import openai

        extra += [openai.APIConnectionError, openai.APITimeoutError]
    except ImportError:
        pass
    return tuple(extra)


_RETRYABLE_ERROR_TYPES: tuple[type[BaseException], ...] = (
    _CORE_RETRYABLE_ERROR_TYPES + _sdk_retryable_types()
)

_DISCONNECT_MARKERS: tuple[str, ...] = (
    "server disconnected",
    "connection reset",
    "eof",
    "broken pipe",
)

# Denylist for the string fallback. Programmer/data errors whose message
# may incidentally contain a disconnect marker must not be retried. Any
# type not in this list is eligible for string-based transient detection
# — a deliberate inversion of the previous allowlist, which excluded the
# SDK-specific exception types that are the actual 36% failure mode.
_NON_RETRYABLE_TYPES_FOR_STRING_FALLBACK: tuple[type[BaseException], ...] = (
    ValueError,
    TypeError,
    KeyError,
    AttributeError,
    AssertionError,
    NameError,
    SyntaxError,
    ImportError,
    LookupError,
    ArithmeticError,
)


def _is_transient_error(exc: BaseException) -> bool:
    """Return True if ``exc`` looks like a transient I/O failure worth retrying.

    Checks, in order:
    1. The exception — or any exception in its ``__cause__``/``__context__``
       chain, or any sub-exception of a ``BaseExceptionGroup`` — is an
       instance of a known retryable type (core Python exceptions plus
       SDK-specific connection/timeout classes from httpx, httpcore,
       anthropic, and openai).
    2. The exception's type is NOT in the programmer-error denylist and
       its message (or any chain member's message) matches a disconnect
       marker.

    The chain walk is depth-capped and cycle-guarded.
    """
    if _chain_contains_retryable_type(exc):
        return True

    if type(exc) in _NON_RETRYABLE_TYPES_FOR_STRING_FALLBACK:
        return False

    return _chain_contains_disconnect_marker(exc)


def _walk_exception_chain(exc: BaseException):  # noqa: ANN202
    """Yield ``exc`` plus its ``__cause__`` / ``__context__`` / ExceptionGroup
    descendants, up to ``_RETRY_CHAIN_MAX_DEPTH`` nodes, cycle-guarded."""
    seen: set[int] = set()
    stack: list[BaseException] = [exc]
    while stack and len(seen) < _RETRY_CHAIN_MAX_DEPTH:
        cur = stack.pop()
        if id(cur) in seen:
            continue
        seen.add(id(cur))
        yield cur
        if cur.__cause__ is not None:
            stack.append(cur.__cause__)
        if cur.__context__ is not None:
            stack.append(cur.__context__)
        # BaseExceptionGroup (Python 3.11+): unpack sub-exceptions
        sub = getattr(cur, "exceptions", None)
        if sub is not None:
            for s in sub:
                if isinstance(s, BaseException):
                    stack.append(s)


def _chain_contains_retryable_type(exc: BaseException) -> bool:
    for node in _walk_exception_chain(exc):
        if isinstance(node, _RETRYABLE_ERROR_TYPES):
            return True
    return False


def _chain_contains_disconnect_marker(exc: BaseException) -> bool:
    for node in _walk_exception_chain(exc):
        if type(node) in _NON_RETRYABLE_TYPES_FOR_STRING_FALLBACK:
            continue
        msg = str(node).lower()
        if any(marker in msg for marker in _DISCONNECT_MARKERS):
            return True
    return False


def _compute_backoff_delay(attempt: int) -> float:
    """Exponential backoff with small uniform jitter to avoid thundering herd."""
    import random as _random

    return _RETRY_BASE_DELAY * (2 ** (attempt - 1)) + _random.uniform(0, _RETRY_JITTER_MAX)


async def _invoke_with_retry(
    agent: Any,
    input_data: dict,
    config: Any,
    *,
    max_attempts: int = _RETRY_MAX_ATTEMPTS,
) -> dict:
    """Invoke an agent with retry on transient errors.

    Retries on connection errors, timeouts, and server disconnects with
    exponential backoff plus jitter. Non-transient errors (validation,
    auth, programmer errors) are raised immediately.

    Mutates ``config["metadata"]`` (if present) to record the attempt
    count actually used, so callers that pass a RunnableConfig see the
    retry count surface in LangSmith without needing a separate return
    channel.
    """
    import asyncio as _asyncio

    last_exc: BaseException | None = None
    attempts_used = 0
    for attempt in range(1, max_attempts + 1):
        attempts_used = attempt
        try:
            result = await agent.ainvoke(input_data, config=config)
        except Exception as exc:  # noqa: BLE001  # classifier decides retry vs re-raise
            if not _is_transient_error(exc):
                _annotate_config_metadata(
                    config,
                    attempts=attempt,
                    terminated=True,
                    final_exception_type=type(exc).__name__,
                )
                raise
            last_exc = exc
            if attempt >= max_attempts:
                logger.error(
                    "Agent invocation failed after %d attempts: %s",
                    max_attempts,
                    exc,
                )
                break
            delay = _compute_backoff_delay(attempt)
            logger.warning(
                "Transient error on attempt %d/%d (%s): %s. Retrying in %.1fs",
                attempt,
                max_attempts,
                type(exc).__name__,
                exc,
                delay,
            )
            await _asyncio.sleep(delay)
        else:
            _annotate_config_metadata(
                config, attempts=attempts_used, terminated=False
            )
            return result
    assert last_exc is not None  # loop only exits via break after exhausting retries
    _annotate_config_metadata(
        config,
        attempts=attempts_used,
        terminated=True,
        final_exception_type=type(last_exc).__name__,
    )
    raise last_exc


def _annotate_config_metadata(
    config: Any,
    *,
    attempts: int,
    terminated: bool,
    final_exception_type: str | None = None,
) -> None:
    """Stamp retry outcome on ``config["metadata"]`` when possible.

    No-op when ``config`` is None or lacks a metadata dict — callers that
    care about retry annotations pass a RunnableConfig; callers that don't
    (tests, ad-hoc invocations) are unaffected.
    """
    if not isinstance(config, dict):
        return
    meta = config.get("metadata")
    if not isinstance(meta, dict):
        return
    meta["retry_attempts"] = attempts
    meta["retry_terminated"] = terminated
    if final_exception_type is not None:
        meta["retry_final_exception_type"] = final_exception_type


_RETRY_METADATA_KEYS = (
    "retry_attempts",
    "retry_terminated",
    "retry_final_exception_type",
)


def _mirror_retry_metadata_to_trace(run_tree: Any, metadata: dict[str, Any]) -> None:
    """Copy retry annotations onto the LangSmith RunTree explicitly.

    Removes the assumption that ``trace(metadata=metadata)`` holds the same
    dict object we later mutate via ``_annotate_config_metadata``. Some
    LangSmith versions snapshot the metadata dict at trace creation, which
    would otherwise leave retry annotations invisible in the UI.
    """
    if run_tree is None:
        return
    tree_meta = getattr(run_tree, "metadata", None)
    if not isinstance(tree_meta, dict):
        return
    for key in _RETRY_METADATA_KEYS:
        if key in metadata:
            tree_meta[key] = metadata[key]

logger = logging.getLogger(__name__)

# Load .env file if present
load_dotenv()

_MAX_FILE_LISTING = 10  # maximum files shown in the system prompt directory context
_OPENROUTER_TIMEOUT_SEC = 300  # per-request timeout for the OpenRouter httpx client


def _patch_openrouter_timeout(model: Any) -> None:
    """Replace the OpenRouter SDK client with one that has a proper timeout.

    The ``langchain_openrouter.ChatOpenRouter`` model delegates HTTP calls
    to the OpenRouter SDK which uses the default 5-second httpx timeout.
    Large-context agent calls routinely exceed this.  This function creates
    a new ``openrouter.OpenRouter`` client with ``timeout_ms`` set, which
    the SDK uses to override per-request timeouts.
    """
    try:
        import openrouter as _openrouter

        old_client = getattr(model, "client", None)
        if old_client is None or not isinstance(old_client, _openrouter.OpenRouter):
            return
        api_key = getattr(old_client.sdk_configuration.security, "api_key", None)
        if not api_key:
            return
        model.client = _openrouter.OpenRouter(
            api_key=api_key,
            timeout_ms=_OPENROUTER_TIMEOUT_SEC * 1000,
        )
        logger.info("Patched OpenRouter client timeout to %ss", _OPENROUTER_TIMEOUT_SEC)
    except Exception:
        logger.debug("Could not patch OpenRouter timeout", exc_info=True)

HARBOR_PREAMBLE = """\
You are running inside a sandboxed benchmark environment. Complete the task fully and autonomously.

## Workflow

**Phase 1 — EXAMINE (fast, 2-3 tool calls):** Read the task fully. List files, read key files, check for existing tests. Then write two internal artifacts before acting:

1. **Requirements checklist** — every concrete requirement with exact details (file paths, field names, CLI flags, output formats, services/ports, edge cases mentioned).
2. **Test plan** — how you'll verify each requirement. If test files exist (`/tests/`, `check.py`), note the exact command. If the task gives a test command, use it verbatim. Otherwise describe a minimal smoke check you'll write.

**Phase 2 — BUILD:** Write a first draft, get it running even if incomplete. A partial solution that exists beats a perfect one never written. After your first draft, immediately run the tests — `bash /tests/test.sh` if it exists, or whatever test command the task specifies.

**Phase 3 — TEST & FIX (spend most of your time here):** Run `bash /tests/test.sh` (or the task's test command). Read the FULL output — every error, every assertion. Fix one issue at a time and re-run. Walk your checklist item-by-item and verify each one. **Do NOT declare the task complete without running the tests at least once.**

## Pivot Rules

- Same error twice → try a **different** approach, not a variation of the same approach.
- 3 failed attempts on the same sub-problem → stop, step back, rethink the whole approach.
- One failed command is NOT a reason to give up — try at least 3 different approaches before concluding something is impossible.

## Discipline

- **Commit to stated actions.** If you say you will use a tool, call it as your next action. Never say "I will search for..." without immediately searching.
- **Multi-search is mandatory.** First-pass search results often miss key details. Try different wording, synonyms, and related terms before concluding something doesn't exist.
- **Re-read after a failed edit.** If an `edit_file` call fails, read the file again before retrying — sandbox state may have changed since your last read.

## Code Output Rules

- **Never write entire programs in a single response.** Use the write_file or edit_file tool to create files, then execute and verify them. Short tool-call responses beat long code blocks.
- When a task involves computation, data transforms, or arithmetic — write and execute a script, never attempt computation in text.

## Benchmark-Specific Rules

- Read the task name carefully — the name often contains the key action (e.g., "break-filter" means bypass/defeat the filter, not build one).
- Use exact identifiers, class names, file paths, and field names specified in the task. `value` ≠ `val`. `/app/result.txt` ≠ `/app/results.txt`.
- For tasks involving "all" or "every": count the total items first, process each one, then verify the count matches.
- For server/service tasks: start in background (`&` or `nohup`), wait for startup, verify it responds. **Do NOT stop or kill the server when finishing** — external verification needs it running.
- When the task mentions "faster", "speed", "performance", or "benchmark": write your solution, run the benchmark, and iterate until the target is met.
- Your context is automatically managed through summarization — you always have room. Keep working until the objective is fully complete. Do not stop early.
- All file paths must be absolute. Work in /app unless instructed otherwise.
- Always use non-interactive command variants: `apt-get install -y`, `npm init -y`, etc.
- If a shell command fails with a network error, retry it once before giving up.

## Environment

Your current working directory is:
{current_directory}

{file_listing_header}
{file_listing}
"""


class DeepAgentsWrapper(BaseAgent):
    """Harbor agent implementation using LangChain Deep Agents.

    Wraps Deep Agents to execute tasks in Harbor environments.
    """

    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        temperature: float = 0.0,
        verbose: bool = True,
        use_cli_agent: bool = True,
        openrouter_provider: str | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """Initialize Deep AgentsWrapper.

        Args:
            logs_dir: Directory for storing logs
            model_name: Name of the LLM model to use
            temperature: Temperature setting for the model
            verbose: Enable verbose output
            use_cli_agent: If True, use create_cli_agent from deepagents-cli (default).
                If False, use create_deep_agent from SDK.
            openrouter_provider: Pin OpenRouter routing to a single provider
                (e.g. `"MiniMax"`).

                Requires an `openrouter:` model prefix.
        """
        super().__init__(logs_dir, model_name, *args, **kwargs)

        if openrouter_provider and (model_name is None or not model_name.startswith("openrouter:")):
            msg = "openrouter_provider requires an openrouter: model prefix"
            raise ValueError(msg)

        if model_name is None:
            # Keep Harbor default aligned with the SDK default model.
            model = get_default_model()
            # Apply Harbor's runtime temperature knob to the SDK default when supported.
            updates: dict[str, Any] = {"temperature": temperature}
            if hasattr(model, "max_tokens"):
                updates["max_tokens"] = 16384
            if hasattr(model, "timeout"):
                updates["timeout"] = _OPENROUTER_TIMEOUT_SEC
            if hasattr(model, "max_retries"):
                updates["max_retries"] = 0
            model = model.model_copy(update=updates)
            self._model = model
            self._model_name = model.model
        else:
            self._model_name = model_name
            model_kwargs: dict[str, Any] = {}
            if openrouter_provider:
                model_kwargs["openrouter_provider"] = {
                    "only": [openrouter_provider],
                    "allow_fallbacks": False,
                }
            self._model = init_chat_model(
                model_name,
                temperature=temperature,
                max_tokens=16384,
                timeout=300,
                max_retries=0,
                **model_kwargs,
            )

        _patch_openrouter_timeout(self._model)

        self._temperature = temperature
        self._verbose = verbose
        self._use_cli_agent = use_cli_agent

        # LangSmith run tracking for feedback
        self._langsmith_run_id: str | None = None
        self._task_name: str | None = None

        # Build instruction->example_id mapping if LANGSMITH_EXPERIMENT is set
        self._instruction_to_example_id: dict[str, str] = {}
        langsmith_experiment_name = os.environ.get("LANGSMITH_EXPERIMENT", "").strip() or None
        if langsmith_experiment_name:
            try:
                client = Client()
                experiment = client.read_project(project_name=langsmith_experiment_name)
                examples = list(client.list_examples(dataset_id=experiment.reference_dataset_id))

                # Build mapping from instruction to example ID
                for example in examples:
                    instruction = example.inputs.get("instruction") if example.inputs else None
                    if instruction:
                        self._instruction_to_example_id[instruction] = str(example.id)
            except Exception:  # noqa: BLE001  # gracefully degrade when LangSmith is unavailable
                logger.warning("Failed to build instruction->example_id mapping", exc_info=True)

    @staticmethod
    def name() -> str:
        """Return the agent name identifier."""
        return "deepagent-harbor"

    async def setup(self, environment: BaseEnvironment) -> None:
        """Setup the agent with the given environment.

        Args:
            environment: Harbor environment (Docker, Modal, etc.)
        """

    def version(self) -> str | None:
        """The version of the agent."""
        return "0.0.1"

    async def _get_formatted_system_prompt(self, backend: HarborSandbox) -> str:
        """Format the system prompt with current directory and file listing context.

        Args:
            backend: Harbor sandbox backend to query for directory information

        Returns:
            Formatted system prompt with directory context
        """
        # Get directory information from backend
        ls_result = await backend.als(".")
        current_dir = (await backend.aexecute("pwd")).output

        if ls_result.error:
            logger.warning("Failed to list working directory: %s", ls_result.error)

        entries = ls_result.entries or []
        total_files = len(entries)
        first_files = entries[:_MAX_FILE_LISTING]

        # Build file listing header based on actual count
        if total_files == 0:
            file_listing_header = "Current directory is empty."
            file_listing = ""
        elif total_files <= _MAX_FILE_LISTING:
            # Show actual count when 10 or fewer
            file_count_text = "1 file" if total_files == 1 else f"{total_files} files"
            file_listing_header = f"Files in current directory ({file_count_text}):"
            file_listing = "\n".join(f"{i + 1}. {file}" for i, file in enumerate(first_files))
        else:
            file_listing_header = (
                f"Files in current directory (showing first {_MAX_FILE_LISTING} of {total_files}):"
            )
            file_listing = "\n".join(f"{i + 1}. {file}" for i, file in enumerate(first_files))

        # Format the Harbor preamble with environment context
        return HARBOR_PREAMBLE.format(
            current_directory=current_dir.strip() if current_dir else "/app",
            file_listing_header=file_listing_header,
            file_listing=file_listing,
        )

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,  # noqa: ARG002  # required by BaseAgent interface
    ) -> None:
        """Execute the Deep Agent on the given instruction.

        Args:
            instruction: The task to complete
            environment: Harbor environment (Docker, Modal, etc.)
            context: Context to populate with metrics
        """
        configuration = json.loads(environment.trial_paths.config_path.read_text())
        if not isinstance(configuration, dict):
            msg = f"Unexpected configuration format. Expected a dict got {type(configuration)}."
            raise TypeError(msg)

        backend = HarborSandbox(environment)

        # Infrastructure metadata for noise analysis
        try:
            infra_meta = await collect_sandbox_metadata(backend)
        except Exception:  # noqa: BLE001  # metadata is supplementary; never abort a trial
            logger.warning("Failed to collect infrastructure metadata", exc_info=True)
            infra_meta = None

        # Create agent based on mode (CLI vs SDK)
        if self._use_cli_agent:
            # Activate Pack's full middleware stack (compaction, memory, hooks)
            os.environ["PACK_ENABLED"] = "1"

            # Build prompt: Pack's native prompt (via get_system_prompt) handles
            # core behavioral rules. We append a Harbor-specific preamble with
            # benchmark context and environment details.
            harbor_context = await self._get_formatted_system_prompt(backend)

            deep_agent, _ = create_cli_agent(
                model=self._model,
                assistant_id=environment.session_id,
                sandbox=backend,
                sandbox_type=None,
                system_prompt=harbor_context,  # Pack builds its own base prompt; this adds Harbor context
                interactive=False,  # Activates middleware: edit verification, syntax check, leak detection, doom loop
                auto_approve=True,
                enable_memory=True,  # Activate Pack's memory system
                enable_skills=True,  # Activate Pack's skills system
                enable_shell=False,  # Sandbox provides execution
            )
        else:
            # Use SDK agent
            # Get formatted system prompt with directory context
            system_prompt = await self._get_formatted_system_prompt(backend)

            deep_agent = create_deep_agent(
                model=self._model, backend=backend, system_prompt=system_prompt
            )

        # Build metadata with experiment tracking info
        try:
            sdk_version = importlib.metadata.version("deepagents")
        except importlib.metadata.PackageNotFoundError:
            sdk_version = "unknown"

        metadata = {
            "task_instruction": instruction,
            # "model" is the legacy key; "model_name" is the canonical key
            # used for LangSmith experiment filtering.
            "model": self._model_name,
            "model_name": self._model_name,
            "sdk_version": sdk_version,
            # Harbor's per-task session ID, distinct from the LangSmith
            # TracerSession UUID also called "session_id" in the API.
            "harbor_session_id": environment.session_id,
            # Tag to indicate which agent implementation is being used
            "agent_mode": "cli" if self._use_cli_agent else "sdk",
        }
        metadata.update(configuration)

        # Look up example_id from instruction using the mapping built at initialization
        example_id = self._instruction_to_example_id.get(instruction)

        config: RunnableConfig = {
            "run_name": f"{environment.session_id}",
            "tags": [
                self._model_name,
                environment.session_id,
                "cli-agent" if self._use_cli_agent else "sdk-agent",
            ],
            "configurable": {
                "thread_id": str(uuid.uuid4()),
            },
        }

        # If LANGSMITH_EXPERIMENT is set, wrap in trace context.
        # This will link runs to the given experiment in LangSmith.
        langsmith_experiment_name = os.environ.get("LANGSMITH_EXPERIMENT", "").strip() or None

        # Share the metadata dict with config so _invoke_with_retry's
        # in-place annotations (retry_attempts, retry_terminated, ...) flow
        # through both the RunnableConfig path and the LangSmith trace.
        config["metadata"] = metadata

        result: dict | None = None
        failure: _FailureInfo | None = None
        invoke_input = {"messages": [{"role": "user", "content": instruction}]}
        try:
            if langsmith_experiment_name:
                with trace(
                    name=environment.session_id,
                    reference_example_id=example_id,
                    inputs={"instruction": instruction},
                    project_name=langsmith_experiment_name,
                    metadata=metadata,
                ) as run_tree:
                    try:
                        result = await _invoke_with_retry(deep_agent, invoke_input, config)
                        result = await self._auto_verify_and_fix(
                            deep_agent, result, config, backend,
                        )
                    except Exception as exc:
                        failure = _build_failure_info(
                            exc,
                            attempts=metadata.get("retry_attempts", 1),
                            reason="retry_exhausted" if _is_transient_error(exc) else "non_retryable",
                        )
                        _mirror_retry_metadata_to_trace(run_tree, metadata)
                        run_tree.end(error=str(exc))
                        raise
                    _mirror_retry_metadata_to_trace(run_tree, metadata)
                    messages = result.get("messages") or []
                    last_message = messages[-1] if messages else None
                    if isinstance(last_message, AIMessage):
                        run_tree.end(outputs={"last_message": last_message.text})
                    else:
                        run_tree.end(outputs={})
            else:
                try:
                    result = await _invoke_with_retry(deep_agent, invoke_input, config)
                    result = await self._auto_verify_and_fix(
                        deep_agent, result, config, backend,
                    )
                except Exception as exc:
                    failure = _build_failure_info(
                        exc,
                        attempts=metadata.get("retry_attempts", 1),
                        reason="retry_exhausted" if _is_transient_error(exc) else "non_retryable",
                    )
                    raise
        finally:
            # Persist whatever trajectory state we have, even on terminal
            # failure. A secondary exception here must never mask the
            # original — log and swallow so the root cause propagates.
            try:
                self._save_trajectory(
                    environment, instruction, result, infra_meta, failure=failure
                )
            except Exception:  # noqa: BLE001  # defensive: persistence must not mask root cause
                logger.exception("Failed to persist trajectory after invocation")

    async def _auto_verify_and_fix(
        self,
        agent: Any,
        result: dict,
        config: Any,
        backend: Any,
        *,
        max_cycles: int = 3,
    ) -> dict:
        """Run verification tests and let the agent fix failures.

        After the agent completes, executes ``/tests/test.sh`` in the
        sandbox.  If tests fail, feeds the truncated output back as a
        user message and re-invokes the agent for a fix attempt.

        Returns the final agent result (original or post-fix).
        """
        import asyncio as _asyncio

        test_cmd = "bash /tests/test.sh"

        if not hasattr(backend, "aexecute"):
            logger.debug("Backend does not support aexecute — skipping auto-verification")
            return result

        for cycle in range(1, max_cycles + 1):
            try:
                test_output = await _asyncio.wait_for(
                    backend.aexecute(test_cmd),
                    timeout=120,
                )
                output_text = test_output if isinstance(test_output, str) else str(test_output)
            except Exception as exc:
                exc_str = str(exc)
                if any(s in exc_str for s in ("No such file", "not found", "command not found", "exit code")):
                    logger.info("No /tests/test.sh found — skipping auto-verification")
                    return result
                logger.warning("Verification command failed (cycle %d): %s", cycle, exc)
                output_text = exc_str

            # Check for pass signal
            if "1" in (output_text or "") and "PASSED" in (output_text or "").upper():
                logger.info("Auto-verification PASSED on cycle %d", cycle)
                return result
            if "reward" in (output_text or "").lower() and "1" in (output_text or ""):
                logger.info("Auto-verification PASSED on cycle %d", cycle)
                return result

            # Tests failed — truncate output and feed back to agent
            truncated = output_text[:2000] if output_text else "Tests failed with no output"
            logger.info(
                "Auto-verification FAILED on cycle %d/%d. Feeding errors back to agent.",
                cycle,
                max_cycles,
            )

            if cycle >= max_cycles:
                logger.warning("Auto-verification exhausted %d cycles", max_cycles)
                return result

            fix_message = (
                f"VERIFICATION FAILED. The task's test suite produced errors. "
                f"Fix the issues and try again.\n\n"
                f"Test output (truncated):\n```\n{truncated}\n```"
            )
            try:
                result = await _invoke_with_retry(
                    agent,
                    {"messages": [{"role": "user", "content": fix_message}]},
                    config,
                )
            except Exception:
                logger.warning("Fix attempt failed on cycle %d", cycle, exc_info=True)
                return result

        return result

    def _save_trajectory(
        self,
        environment: BaseEnvironment,
        instruction: str,
        result: dict | None,
        infra_meta: InfraMetadata | None = None,
        *,
        failure: _FailureInfo | None = None,
    ) -> None:
        """Save current trajectory to logs directory.

        Called on both success and failure. When the agent invocation raised,
        ``result`` may be ``None`` — in that case a minimal trajectory is
        persisted containing only the user instruction plus a
        ``extra["failure"]`` block describing the terminal error. This keeps
        the job directory self-describing for post-mortem tooling even when
        the underlying LLM stream died before producing output.

        Args:
            environment: Harbor environment with trial paths.
            instruction: The task instruction given to the agent.
            result: Agent invocation result containing messages. ``None``
                when the invocation failed before returning.
            infra_meta: Infrastructure metadata collected at trial start,
                if available.
            failure: Structured failure record when the invocation did not
                complete successfully.
        """
        # Track token usage and cost for this run
        total_prompt_tokens = 0
        total_completion_tokens = 0

        # Create trajectory
        steps = [
            Step(
                step_id=1,
                timestamp=datetime.now(UTC).isoformat(),
                source="user",
                message=instruction,
            ),
        ]

        observations = []
        pending_step: Step | None = None

        messages = (result or {}).get("messages", [])
        for msg in messages:
            if isinstance(msg, AIMessage):
                # Extract usage metadata from AIMessage
                usage: UsageMetadata = msg.usage_metadata
                if usage:
                    total_prompt_tokens += usage["input_tokens"]
                    total_completion_tokens += usage["output_tokens"]
                # If there's a pending step with tool calls, add it now with observations
                if pending_step is not None:
                    if pending_step.tool_calls and observations:
                        # Add observations to the pending step
                        pending_step.observation = Observation(results=observations)
                        observations = []
                    steps.append(pending_step)
                    pending_step = None

                # Extract content and tool calls from current AIMessage
                atf_tool_calls = []
                message = ""
                for cb in msg.content_blocks:
                    if cb["type"] == "text":
                        message += cb["text"]
                    elif cb["type"] == "reasoning":
                        message += cb["reasoning"]
                    elif cb["type"] == "tool_call":
                        atf_tool_calls.append(
                            ToolCall(
                                tool_call_id=cb["id"],
                                function_name=cb["name"],
                                arguments=cb["args"],
                            )
                        )
                    else:
                        # TODO: Add server side tool call results.
                        continue

                # Create new step
                new_step = Step(
                    step_id=steps[-1].step_id + 1 if steps else 0,
                    timestamp=datetime.now(UTC).isoformat(),
                    source="agent",
                    message=message,
                    tool_calls=atf_tool_calls or None,
                )

                # If this AIMessage has tool calls, make it pending (wait for observations)
                # Otherwise, add it immediately
                if atf_tool_calls:
                    pending_step = new_step
                else:
                    steps.append(new_step)

            elif isinstance(msg, ToolMessage):
                # Collect observations for the pending step
                observations.append(
                    ObservationResult(
                        source_call_id=msg.tool_call_id,
                        content=str(msg.content),
                    )
                )
            elif isinstance(msg, HumanMessage):
                pass
            else:
                err_msg = f"Message type {type(msg)} not supported for step conversion"
                raise NotImplementedError(err_msg)

        # Add any remaining pending step
        if pending_step is not None:
            if pending_step.tool_calls and observations:
                pending_step.observation = Observation(results=observations)
            steps.append(pending_step)

        # Build and save trajectory
        metrics = FinalMetrics(
            total_prompt_tokens=total_prompt_tokens or None,
            total_completion_tokens=total_completion_tokens or None,
            total_steps=len(steps),
        )
        trajectory_extra: dict[str, Any] = {}
        if failure is not None:
            trajectory_extra["failure"] = asdict(failure)
            trajectory_extra["status"] = "failed"

        trajectory = Trajectory(
            schema_version="ATIF-v1.2",
            session_id=environment.session_id,
            agent=Agent(
                name=self.name(),
                version=self.version() or "unknown",
                model_name=self._model_name,
                extra={
                    "framework": "deepagents",
                    "langchain_version": importlib.metadata.version("langchain"),
                    "langchain_core_version": importlib.metadata.version("langchain-core"),
                    **({"infrastructure": infra_meta.to_dict()} if infra_meta else {}),
                },
            ),
            steps=steps,
            final_metrics=metrics,
            extra=trajectory_extra or None,
        )
        trajectory_path = self.logs_dir / "trajectory.json"
        trajectory_path.write_text(json.dumps(trajectory.to_json_dict(), indent=2))
