"""OpenAI Agents SDK adapter with bounded usage and private evidence inputs.

SDK retries are disabled: each explicitly initiated request gets its own durable
budget reservation. Unknown charges conservatively consume the reservation.
"""

import base64
import inspect
import mimetypes
import os
from pathlib import Path
from typing import Callable

from PIL import Image
from pydantic import BaseModel

DEFAULT_MODEL = "gpt-6.1-sol"
COMPLEX_MODEL = "gpt-6-astra"


async def _invoke(callback: Callable | None, *arguments):
    if callback is None:
        return None
    value = callback(*arguments)
    return await value if inspect.isawaitable(value) else value


class AgentsCaller:
    """Callable matching the pipeline contract, without arbitrary agent tools.

    before_call(model, estimated_input_tokens, max_output_tokens) returns a
    reservation. after_call(reservation, input_tokens, output_tokens) settles it.
    release_call is used only before a model request has started. Unknown-usage
    exceptions settle with None/None so the ledger consumes the reservation and
    records usage as unknown instead of presenting estimates as actual tokens.
    """

    def __init__(self, *, before_call: Callable, after_call: Callable,
                 release_call: Callable | None = None, api_key: str | None = None,
                 default_model: str = DEFAULT_MODEL, complex_model: str = COMPLEX_MODEL,
                 max_output_tokens: int = 12000, artifact_root: Path | None = None,
                 timeout: float = 180.0):
        if max_output_tokens < 1 or max_output_tokens > 32000:
            raise ValueError("Output token bound must be between 1 and 32000")
        self.before_call, self.after_call, self.release_call = before_call, after_call, release_call
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self.default_model, self.complex_model = default_model, complex_model
        self.max_output_tokens = max_output_tokens
        self.artifact_root = artifact_root.resolve() if artifact_root else None
        self.timeout = timeout

    def _image(self, path: Path) -> tuple[dict, int]:
        path = Path(path).resolve(strict=True)
        if self.artifact_root is not None and not path.is_relative_to(self.artifact_root):
            raise ValueError("Agent image is outside the authorized private artifact directory")
        if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            raise ValueError("Only rendered evidence images can be supplied to agents")
        if path.stat().st_size > 20 * 1024 * 1024:
            raise ValueError("Evidence image exceeds the request size bound")
        with Image.open(path) as image:
            if image.width * image.height > 25_000_000:
                raise ValueError("Evidence image exceeds the pixel bound")
            # Reserve one token per 16x16 patch plus a large allowance; conservative
            # across documented image tokenization methods, never a billing claim.
            tokens = max(16384, ((image.width + 15) // 16) * ((image.height + 15) // 16) * 4)
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return {"type": "input_image", "image_url": f"data:{mime};base64,{encoded}", "detail": "high"}, tokens

    async def __call__(self, agent_name: str, instructions: str, input_text: str,
                       output_type: type[BaseModel], images: list[Path] | None = None,
                       model: str | None = None) -> BaseModel:
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is required; no paid extraction was executed")
        # Import lazily so structural inspection remains usable without SDK setup.
        from agents import Agent, AgentOutputSchema, ModelSettings, RunConfig, Runner
        from agents.model_settings import ModelRetrySettings
        from agents.models.openai_provider import OpenAIProvider
        from openai import AsyncOpenAI

        selected_model = model or self.default_model
        content = [{"type": "input_text", "text": input_text}]
        # UTF-8 bytes conservatively bound ordinary text tokens and schema overhead.
        estimate = len((instructions + input_text).encode("utf-8")) + len(str(output_type.model_json_schema()).encode("utf-8")) + 2048
        for path in images or []:
            block, tokens = self._image(path)
            content.append(block)
            estimate += tokens
        reservation = await _invoke(self.before_call, selected_model, estimate, self.max_output_tokens)
        started = False
        settled = False
        client = None
        try:
            client = AsyncOpenAI(api_key=self.api_key, max_retries=0, timeout=self.timeout)
            provider = OpenAIProvider(openai_client=client)
            agent = Agent(name=agent_name, instructions=instructions, model=selected_model,
                          tools=[], output_type=AgentOutputSchema(output_type),
                          model_settings=ModelSettings(max_tokens=self.max_output_tokens, retry=ModelRetrySettings(max_retries=0)))
            config = RunConfig(model_provider=provider, tracing_disabled=True,
                               trace_include_sensitive_data=False)
            started = True
            result = await Runner.run(agent, input=[{"role": "user", "content": content}],
                                      max_turns=1, run_config=config)
            usage = result.context_wrapper.usage
            settled = True
            input_tokens, output_tokens = usage.input_tokens, usage.output_tokens
            if input_tokens == 0 and output_tokens == 0:
                input_tokens, output_tokens = None, None
            await _invoke(self.after_call, reservation, input_tokens, output_tokens)
            output = result.final_output
            return output if isinstance(output, output_type) else output_type.model_validate(output)
        except BaseException:
            if not settled:
                if started:
                    await _invoke(self.after_call, reservation, None, None)
                else:
                    await _invoke(self.release_call, reservation)
            raise
        finally:
            if client is not None:
                await client.close()
