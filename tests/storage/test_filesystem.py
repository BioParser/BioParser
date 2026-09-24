from __future__ import annotations

from pathlib import Path

import pytest

from bioparser.storage import (
    ArtifactAlreadyExistsError,
    ArtifactMetadata,
    ArtifactNotFoundError,
    ArtifactStorage,
    FileSystemArtifactStorage,
    InvalidArtifactIdError,
    StorageConfigurationError,
    StoragePathTraversalError,
    StoragePayloadError,
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

        returned_id = storage.store(pdf_bytes, metadata)
        assert returned_id == "doc-123-pdf"

        # Verify blob-pointer flat layout
        retrieved_meta = storage.retrieve_metadata("doc-123-pdf")
        assert retrieved_meta.artifact_id == "doc-123-pdf"
        assert retrieved_meta.document_id == "doc-123"
        assert retrieved_meta.media_type == "application/pdf"
        assert retrieved_meta.checksum == "abc123sha"
        assert retrieved_meta.size_bytes == len(pdf_bytes)
        assert retrieved_meta.blob_id is not None

        assert (storage.root_path / f"doc-123-pdf.{retrieved_meta.blob_id}.data").is_file()
        assert (storage.root_path / "doc-123-pdf.meta.json").is_file()

        assert storage.exists("doc-123-pdf")

        retrieved_bytes = storage.retrieve("doc-123-pdf")
        assert retrieved_bytes == pdf_bytes

        stored_artifact = storage.retrieve_artifact("doc-123-pdf")
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

        returned_id = storage.store(json_bytes, metadata)
        assert returned_id == "parsed-blocks-1"
        assert storage.exists("parsed-blocks-1")

        retrieved = storage.retrieve("parsed-blocks-1")
        assert retrieved == json_bytes

        retrieved_meta = storage.retrieve_metadata("parsed-blocks-1")
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
        meta_1 = storage.retrieve_metadata("art-upd")
        old_blob_path = storage.root_path / f"art-upd.{meta_1.blob_id}.data"
        assert old_blob_path.is_file()

        metadata_2 = ArtifactMetadata(
            artifact_id="art-upd",
            document_id="doc-1",
            media_type="application/pdf",
            checksum="new-checksum",
        )
        storage.store(b"version 2", metadata_2, overwrite=True)

        assert storage.retrieve("art-upd") == b"version 2"
        meta_2 = storage.retrieve_metadata("art-upd")
        assert meta_2.checksum == "new-checksum"
        assert meta_2.size_bytes == len(b"version 2")
        assert meta_2.blob_id != meta_1.blob_id

        # Verify old blob was cleaned up and new blob exists
        assert not old_blob_path.exists()
        assert (storage.root_path / f"art-upd.{meta_2.blob_id}.data").is_file()

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

        # Orphan metadata file without content
        meta = ArtifactMetadata(
            artifact_id="metaonly",
            document_id="doc-meta",
            media_type="application/pdf",
            blob_id="nonexistent-blob",
        )
        (storage.root_path / "metaonly.meta.json").write_text(
            meta.model_dump_json(), encoding="utf-8"
        )
        assert not storage.exists("metaonly")
        with pytest.raises(ArtifactNotFoundError):
            storage.retrieve("metaonly")
        with pytest.raises(ArtifactNotFoundError):
            storage.retrieve_metadata("metaonly")

    def test_corrupted_metadata_json_raises_storage_payload_error(
        self, storage: FileSystemArtifactStorage
    ) -> None:
        (storage.root_path / "corrupt.meta.json").write_text(
            "invalid json content", encoding="utf-8"
        )
        assert not storage.exists("corrupt")
        with pytest.raises(StoragePayloadError, match="Corrupted metadata"):
            storage.retrieve_metadata("corrupt")

    def test_retrieved_content_size_mismatch_raises_storage_payload_error(
        self, storage: FileSystemArtifactStorage
    ) -> None:
        meta = ArtifactMetadata(
            artifact_id="art-tamper",
            document_id="doc-1",
            media_type="application/pdf",
        )
        storage.store(b"expected-length-content", meta)
        retrieved_meta = storage.retrieve_metadata("art-tamper")
        blob_path = storage.root_path / f"art-tamper.{retrieved_meta.blob_id}.data"

        # Truncate content file on disk
        blob_path.write_bytes(b"short")

        with pytest.raises(StoragePayloadError, match="content size .* does not match metadata"):
            storage.retrieve("art-tamper")

    def test_size_bytes_mismatch_raises_storage_payload_error(
        self, storage: FileSystemArtifactStorage
    ) -> None:
        metadata = ArtifactMetadata(
            artifact_id="art-mismatch",
            document_id="doc-1",
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
            "../escaped",
            "../../etc/passwd",
            "something/../../root",
            "/absolute/path",
            "\\windows\\path",
            "C:\\Windows\\System32",
            "D:/escaped",
            "C:file",
            "~",
            "subdir/../escaped",
            "foo\0bar",
            "has space",
            "has@symbol",
            "has/slash",
            "has\\backslash",
            "has:colon",
            "a" * 129,  # exceeds 128 chars limit
        ],
    )
    def test_invalid_artifact_ids_rejected_by_allowlist(
        self, storage: FileSystemArtifactStorage, invalid_id: str
    ) -> None:
        with pytest.raises(InvalidArtifactIdError):
            storage.retrieve(invalid_id)

    def test_valid_ids_allowed(self, storage: FileSystemArtifactStorage) -> None:
        valid_ids = [
            "3fa85f64-5717-4562-b3fc-2c963f66afa6",
            "doc_123.v1-final",
            "SimpleArtifactID",
            "a" * 128,  # exactly 128 chars
        ]
        for aid in valid_ids:
            storage._validate_artifact_id(aid)
