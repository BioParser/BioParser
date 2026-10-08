"""Fake network hops"""

import json
import re
from collections.abc import Callable
from typing import Any

import httpx2
from bioparser.jobqueue import JobQueueError, ParseJobMessage
from bioparser.jobstate import JobState, JobStateError
from bioparser.storage import ArtifactMetadata, StorageError
from bioparser.storage import ArtifactNotFoundError

# What httpx2.MockTransport calls for every request a client sends
type Handler = Callable[[httpx2.Request], httpx2.Response]

# stub json one page and one text block for mineru mapper
STUB_JSON: dict[str, Any] = {
    "_backend": "pipeline",
    "_version_name": "3.4.5",
    "pdf_info": [
        {
            "page_idx": 0,
            "page_size": [595.0, 842.0],
            "para_blocks": [
                {
                    "type": "text",
                    "bbox": [10.0, 10.0, 500.0, 40.0],
                    "lines": [
                        {
                            "bbox": [10.0, 10.0, 500.0, 40.0],
                            "spans": [
                                {
                                    "type": "text",
                                    "bbox": [10.0, 10.0, 500.0, 40.0],
                                    "content": "Adult body mass averaged 12.5 g in the sampled population.",
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    ],
}


class StubMinerUClient:
    """HTTP hop to mineru-api"""

    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self.payload = payload if payload is not None else STUB_JSON
        self.calls: list[str] = []

    async def parse(self, *, filename: str, content: bytes) -> dict[str, Any]:
        self.calls.append(filename)
        return self.payload

    async def aclose(self) -> None:
        return None


class StubVLLMService:
    """HTTP hop to vLLM"""

    def __init__(self, observations: list[dict[str, Any]] | None = None) -> None:
        self.observations = observations
        self.prompts: list[str] = []
        self.system_prompts: list[str] = []

    async def generate_json(
        self,
        prompt: str,
        *,
        schema: dict[str, Any],
        system_prompt: str,
        max_tokens: int,
    ) -> str:
        self.prompts.append(prompt)
        self.system_prompts.append(system_prompt)

        if self.observations is not None:
            return json.dumps({"observations": self.observations})

        match = re.search(r"\[([^\]]+)\]", prompt)
        ident = match.group(1) if match else "unknown"

        return json.dumps(
            {
                "observations": [
                    {
                        "taxon": "Mus musculus",  # kotihiiri
                        "trait": "body_mass",
                        "value": 12.5,
                        "unit": "g",
                        "evidence_block_id": ident,
                        "quotation": "12.5 g",
                    }
                ]
            }
        )

    async def aclose(self) -> None:
        return None


class StubArtifactStorage:
    def __init__(self, events: list[str]) -> None:
        self.artifacts: dict[str, tuple[bytes, ArtifactMetadata]] = {}
        self.events = events
        self.store_error: StorageError | None = None
        self.retrieve_error: StorageError | None = None

    def store(
        self,
        content: bytes,
        metadata: ArtifactMetadata,
        *,
        overwrite: bool = False,
    ) -> str:
        self.events.append("artifact.store")
        if self.store_error is not None:
            raise self.store_error
        self.artifacts[metadata.artifact_path] = (content, metadata)
        return metadata.artifact_path

    def retrieve(self, artifact_path: str) -> bytes:
        self.events.append("artifact.retrieve")
        if self.retrieve_error is not None:
            raise self.retrieve_error
        try:
            return self.artifacts[artifact_path][0]
        except KeyError as exc:
            raise ArtifactNotFoundError(f"Artifact not found: {artifact_path}") from exc


class StubJobStateStore:
    def __init__(self, events: list[str]) -> None:
        self.states: dict[object, JobState] = {}
        self.events = events
        self.create_error: JobStateError | None = None
        self.get_error: JobStateError | None = None

    async def create(self, state: JobState) -> None:
        self.events.append("job_state.create")
        if self.create_error is not None:
            raise self.create_error
        self.states[state.job_id] = state

    async def get(self, job_id: object) -> JobState | None:
        if self.get_error is not None:
            raise self.get_error
        self.events.append("job_state.get")
        return self.states.get(job_id)

    async def update(self, state: JobState) -> None:
        self.events.append("job_state.update")
        self.states[state.job_id] = state


class StubParseQueue:
    def __init__(self, events: list[str]) -> None:
        self.messages: list[ParseJobMessage] = []
        self.events = events
        self.submit_error: JobQueueError | None = None

    def submit(self, message: ParseJobMessage) -> None:
        self.events.append("queue.submit")
        if self.submit_error is not None:
            raise self.submit_error
        self.messages.append(message)
