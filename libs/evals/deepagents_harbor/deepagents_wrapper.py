"""A wrapper for Deep Agents to run in Harbor environments."""

from __future__ import annotations

import importlib.metadata
import json
import logging
import os
import uuid
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

_RETRY_MAX_ATTEMPTS = 3
_RETRY_BASE_DELAY = 2.0  # seconds
_RETRYABLE_ERRORS = (
    ConnectionError,
    TimeoutError,
    OSError,
)


async def _invoke_with_retry(
    agent: Any,
    input_data: dict,
    config: Any,
    *,
    max_attempts: int = _RETRY_MAX_ATTEMPTS,
) -> dict:
    """Invoke an agent with retry on transient errors.

    Retries on connection errors, timeouts, and server disconnects with
    exponential backoff. Non-transient errors (validation, auth) are
    raised immediately.
    """
    import asyncio as _asyncio

    last_exc: BaseException | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return await agent.ainvoke(input_data, config=config)
        except _RETRYABLE_ERRORS as exc:
            last_exc = exc
            if attempt < max_attempts:
                delay = _RETRY_BASE_DELAY * (2 ** (attempt - 1))
                logger.warning(
                    "Agent invocation failed (attempt %d/%d): %s. Retrying in %.1fs",
                    attempt,
                    max_attempts,
                    exc,
                    delay,
                )
                await _asyncio.sleep(delay)
            else:
                logger.error(
                    "Agent invocation failed after %d attempts: %s",
                    max_attempts,
                    exc,
                )
        except Exception as exc:
            # Check for server disconnect messages in the error string
            err_str = str(exc).lower()
            if any(
                s in err_str
                for s in ("server disconnected", "connection reset", "eof", "broken pipe")
            ):
                last_exc = exc
                if attempt < max_attempts:
                    delay = _RETRY_BASE_DELAY * (2 ** (attempt - 1))
                    logger.warning(
                        "Server disconnect (attempt %d/%d): %s. Retrying in %.1fs",
                        attempt,
                        max_attempts,
                        exc,
                        delay,
                    )
                    await _asyncio.sleep(delay)
                    continue
            raise
    raise last_exc  # type: ignore[misc]

logger = logging.getLogger(__name__)

# Load .env file if present
load_dotenv()

_MAX_FILE_LISTING = 10  # maximum files shown in the system prompt directory context

HARBOR_PREAMBLE = """\
You are running inside a sandboxed benchmark environment. Complete the task fully and autonomously.

## Benchmark-Specific Rules

- Read the task name carefully — the name often contains the key action (e.g., "break-filter" means bypass/defeat the filter, not build one).
- Use exact identifiers, class names, file paths, and field names specified in the task. Do not rename them.
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
            if hasattr(model, "temperature"):
                model = model.model_copy(update={"temperature": temperature})
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
            self._model = init_chat_model(model_name, temperature=temperature, **model_kwargs)

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

        if langsmith_experiment_name:
            with trace(
                name=environment.session_id,
                reference_example_id=example_id,
                inputs={"instruction": instruction},
                project_name=langsmith_experiment_name,
                metadata=metadata,
            ) as run_tree:
                result = await _invoke_with_retry(
                    deep_agent,
                    {"messages": [{"role": "user", "content": instruction}]},
                    config,
                )
                last_message = result["messages"][-1]
                if isinstance(last_message, AIMessage):
                    run_tree.end(outputs={"last_message": last_message.text})
        else:
            config["metadata"] = metadata
            result = await _invoke_with_retry(
                deep_agent,
                {"messages": [{"role": "user", "content": instruction}]},
                config,
            )

        self._save_trajectory(environment, instruction, result, infra_meta)

    def _save_trajectory(
        self,
        environment: BaseEnvironment,
        instruction: str,
        result: dict,
        infra_meta: InfraMetadata | None = None,
    ) -> None:
        """Save current trajectory to logs directory.

        Args:
            environment: Harbor environment with trial paths.
            instruction: The task instruction given to the agent.
            result: Agent invocation result containing messages.
            infra_meta: Infrastructure metadata collected at trial start,
                if available.
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

        for msg in result["messages"]:
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
        )
        trajectory_path = self.logs_dir / "trajectory.json"
        trajectory_path.write_text(json.dumps(trajectory.to_json_dict(), indent=2))
