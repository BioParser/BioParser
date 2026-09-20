from __future__ import annotations

from pathlib import Path

import pytest

from bioparser.storage import (
    ArtifactAlreadyExistsError,
    ArtifactMetadata,
    ArtifactNotFoundError,
    ArtifactRef,
    ArtifactStorage,
    FileSystemArtifactStorage,
    InvalidArtifactIdError,
    StorageConfigurationError,
    StoragePathTraversalError,
)


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
        metadata = ArtifactMetadata(
            artifact_id="doc-123-pdf",
            document_id="doc-123",
            media_type="application/pdf",
            checksum="abc123sha",
        )

        assert not storage.exists("doc-123-pdf")

        ref = storage.store(pdf_bytes, metadata)
        assert isinstance(ref, ArtifactRef)
        assert ref.artifact_id == "doc-123-pdf"
        assert ref.schema_version == "1"

        assert storage.exists("doc-123-pdf")
        assert storage.exists(ref)

        retrieved_bytes = storage.retrieve("doc-123-pdf")
        assert retrieved_bytes == pdf_bytes

        retrieved_ref_bytes = storage.retrieve(ref)
        assert retrieved_ref_bytes == pdf_bytes

        retrieved_meta = storage.retrieve_metadata("doc-123-pdf")
        assert retrieved_meta.artifact_id == "doc-123-pdf"
        assert retrieved_meta.document_id == "doc-123"
        assert retrieved_meta.media_type == "application/pdf"
        assert retrieved_meta.checksum == "abc123sha"
        assert retrieved_meta.size_bytes == len(pdf_bytes)

        stored_artifact = storage.retrieve_artifact(ref)
        assert stored_artifact.content == pdf_bytes
        assert stored_artifact.metadata == retrieved_meta

    def test_store_and_retrieve_json_parser_output(
        self, storage: FileSystemArtifactStorage
    ) -> None:
        json_bytes = b'{"schema_version": "1", "pages": []}'
        metadata = ArtifactMetadata(
            artifact_id="parsed-blocks-1",
            document_id="doc-456",
            media_type="application/json",
            content_schema_version="1",
        )

        ref = storage.store(json_bytes, metadata)
        assert storage.exists(ref)

        retrieved = storage.retrieve(ref)
        assert retrieved == json_bytes

        retrieved_meta = storage.retrieve_metadata(ref)
        assert retrieved_meta.content_schema_version == "1"
        assert retrieved_meta.media_type == "application/json"


class TestOverwriteBehavior:
    def test_store_duplicate_without_overwrite_raises(
        self, storage: FileSystemArtifactStorage
    ) -> None:
        metadata = ArtifactMetadata(
            artifact_id="art-dup",
            document_id="doc-1",
            media_type="application/pdf",
        )
        storage.store(b"first content", metadata)

        with pytest.raises(ArtifactAlreadyExistsError) as exc_info:
            storage.store(b"second content", metadata, overwrite=False)
        assert "art-dup" in str(exc_info.value)

        # Content should remain unchanged
        assert storage.retrieve("art-dup") == b"first content"

    def test_store_duplicate_with_overwrite_succeeds(
        self, storage: FileSystemArtifactStorage
    ) -> None:
        metadata_1 = ArtifactMetadata(
            artifact_id="art-upd",
            document_id="doc-1",
            media_type="application/pdf",
        )
        storage.store(b"version 1", metadata_1)

        metadata_2 = ArtifactMetadata(
            artifact_id="art-upd",
            document_id="doc-1",
            media_type="application/pdf",
            checksum="new-checksum",
        )
        storage.store(b"version 2", metadata_2, overwrite=True)

        assert storage.retrieve("art-upd") == b"version 2"
        meta = storage.retrieve_metadata("art-upd")
        assert meta.checksum == "new-checksum"
        assert meta.size_bytes == len(b"version 2")

    def test_concurrent_exclusive_stores_race(self, storage: FileSystemArtifactStorage) -> None:
        import concurrent.futures

        metadata = ArtifactMetadata(
            artifact_id="art-race",
            document_id="doc-race",
            media_type="application/pdf",
        )

        results = []
        errors = []

        def _worker(worker_id: int) -> None:
            try:
                storage.store(f"content-{worker_id}".encode(), metadata, overwrite=False)
                results.append(worker_id)
            except ArtifactAlreadyExistsError as exc:
                errors.append(exc)

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(_worker, i) for i in range(4)]
            concurrent.futures.wait(futures)

        assert len(results) == 1
        assert len(errors) == 3
        stored = storage.retrieve_artifact("art-race")
        assert stored.content in [f"content-{i}".encode() for i in range(4)]
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

    def test_size_bytes_mismatch_raises(self, storage: FileSystemArtifactStorage) -> None:
        metadata = ArtifactMetadata(
            artifact_id="art-mismatch",
            document_id="doc-1",
            media_type="application/pdf",
            size_bytes=999,
        )
        with pytest.raises(ValueError, match="Metadata size_bytes"):
            storage.store(b"short", metadata)

    def test_uninitialized_root_without_create_root_raises(self, tmp_path: Path) -> None:
        non_existent = tmp_path / "does_not_exist"
        with pytest.raises(StorageConfigurationError):
            FileSystemArtifactStorage(non_existent, create_root=False)


class TestPathTraversalDefense:
    @pytest.mark.parametrize(
        "malicious_id",
        [
            "../escaped",
            "../../etc/passwd",
            "something/../../root",
            "/absolute/path",
            "\\windows\\path",
            "C:\\Windows\\System32",
            "D:/escaped",
            "C:file",
            ".",
            "~",
            "subdir/../escaped",
        ],
    )
    def test_path_traversal_attempts_raise(
        self, storage: FileSystemArtifactStorage, malicious_id: str
    ) -> None:
        metadata = ArtifactMetadata(
            artifact_id="temp",
            document_id="doc-1",
            media_type="application/pdf",
        )
        # Construct metadata with malicious artifact_id bypass
        object.__setattr__(metadata, "artifact_id", malicious_id)

        with pytest.raises(StoragePathTraversalError):
            storage.store(b"payload", metadata)

        with pytest.raises(StoragePathTraversalError):
            storage.retrieve(malicious_id)

        with pytest.raises(StoragePathTraversalError):
            storage.retrieve_metadata(malicious_id)

        with pytest.raises(StoragePathTraversalError):
            storage.exists(malicious_id)

    @pytest.mark.parametrize(
        "invalid_id",
        [
            "",
            "   ",
            "foo\0bar",
        ],
    )
    def test_invalid_artifact_ids_raise(
        self, storage: FileSystemArtifactStorage, invalid_id: str
    ) -> None:
        with pytest.raises(InvalidArtifactIdError):
            storage.retrieve(invalid_id)

    def test_files_never_escape_storage_root(
        self, storage: FileSystemArtifactStorage, tmp_path: Path
    ) -> None:
        outside_marker = tmp_path / "outside_marker.txt"
        outside_marker.write_text("safe")

        metadata = ArtifactMetadata(
            artifact_id="temp",
            document_id="doc-1",
            media_type="application/pdf",
        )
        object.__setattr__(metadata, "artifact_id", "../outside_marker.txt")

        with pytest.raises(StoragePathTraversalError):
            storage.store(b"evil overwrite", metadata, overwrite=True)

        assert outside_marker.read_text() == "safe"
