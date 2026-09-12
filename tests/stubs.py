"""Fake network hops"""

import json
import re
from typing import Any

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
