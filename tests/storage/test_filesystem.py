from __future__ import annotations

from pathlib import Path

import pytest

from bioparser.storage import (
    ArtifactAlreadyExistsError,
    ArtifactMetadata,
    ArtifactNotFoundError,
    ArtifactStorage,
    FileSystemArtifactStorage,
    InvalidArtifactPathError,
    StorageConfigurationError,
    StoragePathTraversalError,
    StoragePayloadError,
)
from bioparser.storage.filesystem import FileBlobMetadata


@pytest.fixture
def storage(tmp_path: Path) -> FileSystemArtifactStorage:
    root = tmp_path / "artifacts"
    return FileSystemArtifactStorage(root)


class TestProtocolConformance:
    def test_implements_protocol(self, storage: FileSystemArtifactStorage) -> None:
        assert isinstance(storage, ArtifactStorage)


class TestBasicStorageOperations:
    def test_store_and_retrieve_pdf(self, storage: FileSystemArtifactStorage) -> None:
        pdf_bytes = b"%PDF-1.4\n%some test pdf content\n%%EOF"

        artifact_path = "pdf/dummy_hash/1"
        metadata = ArtifactMetadata(
            artifact_path=artifact_path,
            media_type="application/pdf",
        )

        assert not storage.exists(artifact_path)

        returned_id = storage.store(pdf_bytes, metadata)
        assert returned_id == artifact_path

        # Verify blob-pointer flat layout on disk
        meta_json = (storage.root_path / f"{artifact_path}.meta.json").read_text(encoding="utf-8")
        blob_record = FileBlobMetadata.model_validate_json(meta_json)
        assert blob_record.blob_id is not None
        assert (storage.root_path / f"{artifact_path}.{blob_record.blob_id}.data").is_file()
        assert (storage.root_path / f"{artifact_path}.meta.json").is_file()

        assert storage.exists(artifact_path)

        retrieved_bytes = storage.retrieve(artifact_path)
        assert retrieved_bytes == pdf_bytes

        retrieved_meta = storage.retrieve_metadata(artifact_path)
        assert retrieved_meta.artifact_path == artifact_path
        assert retrieved_meta.media_type == "application/pdf"

        assert retrieved_meta.size_bytes == len(pdf_bytes)

        stored_artifact = storage.retrieve_artifact(artifact_path)
        assert stored_artifact.content == pdf_bytes
        assert stored_artifact.metadata == retrieved_meta

    def test_store_and_retrieve_json_parser_output(
        self, storage: FileSystemArtifactStorage
    ) -> None:
        json_bytes = b'{"schema_version": "1", "pages": []}'

        artifact_path = "pdf/dummy_hash/1"
        metadata = ArtifactMetadata(
            artifact_path=artifact_path,
            media_type="application/json",
            content_schema_version="1",
        )

        returned_id = storage.store(json_bytes, metadata)
        assert returned_id == artifact_path
        assert storage.exists(artifact_path)

        retrieved = storage.retrieve(artifact_path)
        assert retrieved == json_bytes

        retrieved_meta = storage.retrieve_metadata(artifact_path)
        assert retrieved_meta.content_schema_version == "1"
        assert retrieved_meta.media_type == "application/json"


class TestDuplicateUploadBehavior:
    def test_store_duplicate_raises(self, storage: FileSystemArtifactStorage) -> None:

        artifact_path = "pdf/dummy_hash/1"
        metadata = ArtifactMetadata(
            artifact_path=artifact_path,
            media_type="application/pdf",
        )
        storage.store(b"content", metadata)

        metadata2 = ArtifactMetadata(
            artifact_path=artifact_path,
            media_type="application/pdf",
        )
        with pytest.raises(ArtifactAlreadyExistsError) as exc_info:
            storage.store(b"content", metadata2)
        assert artifact_path in str(exc_info.value)

        # Content should remain unchanged
        assert storage.retrieve(artifact_path) == b"content"

    def test_concurrent_exclusive_stores_race(self, storage: FileSystemArtifactStorage) -> None:
        import concurrent.futures

        content = b"race-content"
        artifact_path = "race_condition_test"

        metadata = ArtifactMetadata(
            artifact_path=artifact_path,
            media_type="application/pdf",
        )

        results = []
        errors = []

        def _worker(worker_id: int) -> None:
            storage.store(content, metadata)
            results.append(worker_id)

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(_worker, i) for i in range(4)]
            for future in concurrent.futures.as_completed(futures):
                try:
                    future.result()
                except ArtifactAlreadyExistsError as exc:
                    errors.append(exc)

        assert len(results) == 1
        assert len(errors) == 3
        stored = storage.retrieve_artifact(artifact_path)
        assert stored.content == content
        assert stored.metadata.size_bytes == len(stored.content)


class TestErrorHandling:
    def test_retrieve_non_existent_raises(self, storage: FileSystemArtifactStorage) -> None:
        with pytest.raises(ArtifactNotFoundError):
            storage.retrieve("missing-id")

        with pytest.raises(ArtifactNotFoundError):
            storage.retrieve_metadata("missing-id")

        with pytest.raises(ArtifactNotFoundError):
            storage.retrieve_artifact("missing-id")

        assert not storage.exists("missing-id")

    def test_retrieve_requires_metadata_and_content_agreement(
        self, storage: FileSystemArtifactStorage
    ) -> None:
        # Orphan content file without metadata
        (storage.root_path / "orphan.data").write_bytes(b"content only")
        assert not storage.exists("orphan")
        with pytest.raises(ArtifactNotFoundError):
            storage.retrieve("orphan")
        with pytest.raises(ArtifactNotFoundError):
            storage.retrieve_metadata("orphan")

        # Metadata file exists without content
        meta = FileBlobMetadata(
            artifact_path="a" * 64,
            media_type="application/pdf",
            blob_id="nonexistent-blob",
        )
        (storage.root_path / "metaonly.meta.json").write_text(
            meta.model_dump_json(), encoding="utf-8"
        )
        assert storage.exists("metaonly")
        with pytest.raises(ArtifactNotFoundError, match="content not found"):
            storage.retrieve("metaonly")
        with pytest.raises(ArtifactNotFoundError, match="content not found"):
            storage.retrieve_metadata("metaonly")

    def test_corrupted_metadata_json_raises_storage_payload_error(
        self, storage: FileSystemArtifactStorage
    ) -> None:
        artifact_path = "corrupted_metadata_test"
        (storage.root_path / f"{artifact_path}.meta.json").write_text(
            "invalid json content", encoding="utf-8"
        )
        # Metadata file exists, so artifact slot is occupied
        assert storage.exists(artifact_path)
        with pytest.raises(StoragePayloadError, match="Corrupted metadata"):
            storage.retrieve_metadata(artifact_path)

        meta = ArtifactMetadata(
            artifact_path=artifact_path,
            media_type="application/pdf",
        )
        with pytest.raises(ArtifactAlreadyExistsError):
            storage.store(b"content", meta)

    def test_retrieved_content_size_mismatch_raises_storage_payload_error(
        self, storage: FileSystemArtifactStorage
    ) -> None:

        artifact_path = "pdf/dummy_hash/1"
        meta = ArtifactMetadata(
            artifact_path=artifact_path,
            media_type="application/pdf",
        )
        storage.store(b"expected-length-content", meta)
        blob_path = next(storage.root_path.glob(f"{artifact_path}.*.data"))

        # Truncate content file on disk
        blob_path.write_bytes(b"short")

        with pytest.raises(StoragePayloadError, match="content size .* does not match metadata"):
            storage.retrieve(artifact_path)

    def test_size_bytes_mismatch_raises_storage_payload_error(
        self, storage: FileSystemArtifactStorage
    ) -> None:

        artifact_path = "pdf/dummy_hash/1"
        metadata = ArtifactMetadata(
            artifact_path=artifact_path,
            media_type="application/pdf",
            size_bytes=999,
        )
        with pytest.raises(StoragePayloadError, match="Metadata size_bytes"):
            storage.store(b"short", metadata)

    def test_uninitialized_root_without_create_root_raises(self, tmp_path: Path) -> None:
        non_existent = tmp_path / "does_not_exist"
        with pytest.raises(StorageConfigurationError):
            FileSystemArtifactStorage(non_existent, create_root=False)


class TestPathTraversalDefense:
    @pytest.mark.parametrize(
        "malicious_id",
        [
            ".",
            "..",
        ],
    )
    def test_path_traversal_tokens_raise(
        self, storage: FileSystemArtifactStorage, malicious_id: str
    ) -> None:
        with pytest.raises(StoragePathTraversalError):
            storage.retrieve(malicious_id)

    @pytest.mark.parametrize(
        "invalid_id",
        [
            "",
            "   ",
            "\\windows\\path",
            "C:\\Windows\\System32",
            "D:/escaped",
            "C:file",
            "foo\0bar",
            "has space",
            "has@symbol",
            "has\\backslash",
            "has:colon",
            "a" * 513,  # exceeds 512 chars limit
            "/absolute/path",
            "a//b",  # empty segment
            "a/b/",  # trailing empty segment
            "a" * 201,  # exceeds max segment length (200)
        ],
    )
    def test_invalid_artifact_paths_rejected_by_allowlist(
        self, storage: FileSystemArtifactStorage, invalid_id: str
    ) -> None:
        with pytest.raises(InvalidArtifactPathError):
            storage.retrieve(invalid_id)

    @pytest.mark.parametrize(
        "invalid_id",
        [
            "../escaped",
            "../../etc/passwd",
            "something/../../root",
            "a/./b",
            "./x",
        ],
    )
    def test_path_traversal_paths_rejected(
        self, storage: FileSystemArtifactStorage, invalid_id: str
    ) -> None:
        with pytest.raises(StoragePathTraversalError):
            storage.retrieve(invalid_id)

    def test_valid_ids_allowed(self, storage: FileSystemArtifactStorage) -> None:
        valid_ids = [
            "3fa85f64-5717-4562-b3fc-2c963f66afa6",
            "doc_123.v1-final",
            "SimpleArtifactID",
            "a" * 150 + "/" + "b" * 150 + "/" + "c" * 150,  # long path, but short segments
            "pdf/f2ca1bb6c7e907d06dafe4687e579fce76b37e4e93b7605022da52e6ccc26fd2/parsed/mineru/1.0",
            "a/b/c/d/e/f/g",
        ]
        for aid in valid_ids:
            storage._validate_artifact_path(aid)

    def test_store_and_retrieve_with_nested_path(self, storage: FileSystemArtifactStorage) -> None:
        artifact_path = "pdf/checksum123/parsed/backend_v1"
        meta = ArtifactMetadata(
            artifact_path=artifact_path,
            media_type="application/json",
        )
        storage.store(b"nested", meta)

        assert storage.exists(artifact_path)
        assert storage.retrieve(artifact_path) == b"nested"
