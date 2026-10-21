"""A delivery that loses the store must reuse the winner, or fail if it cannot."""

import pytest

from bioparser.storage import ArtifactAlreadyExistsError, ArtifactMetadata, ArtifactNotFoundError
from bioparser.storage.models import StoredArtifact
from bioparser.worker.json_artifact import store_json_artifact
from bioparser.worker.stage import PermanentJobFailure

PATH = "pdf/abc/parsed/default/1/extracted/default/0"
WINNER = b'{"schema_version":"1"}'
OURS = b'{"schema_version":"1","observations":[]}'


class _LostStore:
    """`store` always loses. `retrieve` returns whatever the other delivery left."""

    def __init__(self, existing: bytes | None) -> None:
        self.existing = existing
        self.stores = 0

    def store(self, content: bytes, metadata: ArtifactMetadata) -> str:
        self.stores += 1
        raise ArtifactAlreadyExistsError(metadata.artifact_path)

    def retrieve(self, artifact_path: str) -> bytes:
        if self.existing is None:
            raise ArtifactNotFoundError(artifact_path)
        return self.existing

    def exists(self, artifact_path: str) -> bool:
        return self.existing is not None

    def retrieve_metadata(self, artifact_path: str) -> ArtifactMetadata:
        raise NotImplementedError

    def retrieve_artifact(self, artifact_path: str) -> StoredArtifact:
        raise NotImplementedError


def _is_winner(payload: bytes) -> bool:
    return payload == WINNER


def test_a_lost_store_reuses_a_valid_artifact() -> None:
    storage = _LostStore(WINNER)

    path = store_json_artifact(
        storage,
        schema_version="1",
        payload=OURS,
        artifact_path=PATH,
        created_by="extractor-worker",
        is_valid=_is_winner,
        kind="extraction artifact",
    )

    assert path == PATH
    assert storage.stores == 1
    assert storage.existing == WINNER


@pytest.mark.parametrize("existing", [None, b'{"old":true}'])
def test_a_lost_store_fails_when_the_existing_artifact_is_unusable(
    existing: bytes | None,
) -> None:
    storage = _LostStore(existing)

    with pytest.raises(PermanentJobFailure) as exc_info:
        store_json_artifact(
            storage,
            schema_version="1",
            payload=OURS,
            artifact_path=PATH,
            created_by="extractor-worker",
            is_valid=_is_winner,
            kind="extraction artifact",
        )

    assert exc_info.value.code == "internal_error"
    assert storage.stores == 1
    assert storage.existing == existing
