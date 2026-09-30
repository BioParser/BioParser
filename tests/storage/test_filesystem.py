from __future__ import annotations

import hashlib
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

        checksum = hashlib.sha256(pdf_bytes).hexdigest()
        metadata = ArtifactMetadata(
            artifact_id="doc-123-pdf",
            document_id="doc-123",
            media_type="application/pdf",
            checksum=checksum,
        )

        assert not storage.exists("doc-123-pdf")

        returned_id = storage.store(pdf_bytes, metadata)
        assert returned_id == "doc-123-pdf"

        # Verify blob-pointer flat layout on disk
        meta_json = (storage.root_path / "doc-123-pdf.meta.json").read_text(encoding="utf-8")
        blob_record = FileBlobMetadata.model_validate_json(meta_json)
        assert blob_record.blob_id is not None
        assert (storage.root_path / f"doc-123-pdf.{blob_record.blob_id}.data").is_file()
        assert (storage.root_path / "doc-123-pdf.meta.json").is_file()

        assert storage.exists("doc-123-pdf")

        retrieved_bytes = storage.retrieve("doc-123-pdf")
        assert retrieved_bytes == pdf_bytes

        retrieved_meta = storage.retrieve_metadata("doc-123-pdf")
        assert retrieved_meta.artifact_id == "doc-123-pdf"
        assert retrieved_meta.document_id == "doc-123"
        assert retrieved_meta.media_type == "application/pdf"
        assert retrieved_meta.checksum == checksum
        assert retrieved_meta.size_bytes == len(pdf_bytes)

        stored_artifact = storage.retrieve_artifact("doc-123-pdf")
        assert stored_artifact.content == pdf_bytes
        assert stored_artifact.metadata == retrieved_meta

    def test_store_and_retrieve_json_parser_output(
        self, storage: FileSystemArtifactStorage
    ) -> None:
        json_bytes = b'{"schema_version": "1", "pages": []}'

        checksum = hashlib.sha256(json_bytes).hexdigest()
        metadata = ArtifactMetadata(
            artifact_id="parsed-blocks-1",
            document_id="doc-456",
            media_type="application/json",
            checksum=checksum,
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

        checksum1 = hashlib.sha256(b"first content").hexdigest()
        metadata = ArtifactMetadata(
            artifact_id="art-dup",
            document_id="doc-1",
            media_type="application/pdf",
            checksum=checksum1,
        )
        storage.store(b"first content", metadata)

        checksum2 = hashlib.sha256(b"second content").hexdigest()
        metadata2 = ArtifactMetadata(
            artifact_id="art-dup",
            document_id="doc-1",
            media_type="application/pdf",
            checksum=checksum2,
        )
        with pytest.raises(ArtifactAlreadyExistsError) as exc_info:
            storage.store(b"second content", metadata2, overwrite=False)
        assert "art-dup" in str(exc_info.value)

        # Content should remain unchanged
        assert storage.retrieve("art-dup") == b"first content"

    def test_store_duplicate_with_overwrite_succeeds(
        self, storage: FileSystemArtifactStorage
    ) -> None:

        checksum1 = hashlib.sha256(b"version 1").hexdigest()
        metadata_1 = ArtifactMetadata(
            artifact_id="art-upd",
            document_id="doc-1",
            media_type="application/pdf",
            checksum=checksum1,
        )
        storage.store(b"version 1", metadata_1)
        blob_files_v1 = list(storage.root_path.glob("art-upd.*.data"))
        assert len(blob_files_v1) == 1

        checksum2 = hashlib.sha256(b"version 2").hexdigest()
        metadata_2 = ArtifactMetadata(
            artifact_id="art-upd",
            document_id="doc-1",
            media_type="application/pdf",
            checksum=checksum2,
        )
        storage.store(b"version 2", metadata_2, overwrite=True)

        assert storage.retrieve("art-upd") == b"version 2"
        meta_2 = storage.retrieve_metadata("art-upd")
        assert meta_2.checksum == checksum2
        assert meta_2.size_bytes == len(b"version 2")

        # Verify old blob is preserved (deletion/retention out of scope) and new blob is active
        blob_files_v2 = list(storage.root_path.glob("art-upd.*.data"))
        assert len(blob_files_v2) == 2
        assert blob_files_v1[0] in blob_files_v2

    def test_concurrent_exclusive_stores_race(self, storage: FileSystemArtifactStorage) -> None:
        import concurrent.futures

        content = b"race-content"
        checksum = hashlib.sha256(content).hexdigest()

        metadata = ArtifactMetadata(
            artifact_id="art-race",
            document_id="doc-race",
            media_type="application/pdf",
            checksum=checksum,
        )

        results = []
        errors = []

        def _worker(worker_id: int) -> None:
            try:
                storage.store(content, metadata, overwrite=False)
                results.append(worker_id)
            except ArtifactAlreadyExistsError as exc:
                errors.append(exc)

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(_worker, i) for i in range(4)]
            concurrent.futures.wait(futures)

        assert len(results) == 1

        # All others should fail with ArtifactAlreadyExistsError
        for exc in errors:
            assert isinstance(exc, ArtifactAlreadyExistsError)
        stored = storage.retrieve_artifact("art-race")
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
            artifact_id="metaonly",
            document_id="doc-meta",
            media_type="application/pdf",
            blob_id="nonexistent-blob",
            checksum="a" * 64,
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
        (storage.root_path / "corrupt.meta.json").write_text(
            "invalid json content", encoding="utf-8"
        )
        # Metadata file exists, so artifact slot is occupied
        assert storage.exists("corrupt")
        with pytest.raises(StoragePayloadError, match="Corrupted metadata"):
            storage.retrieve_metadata("corrupt")

        checksum1 = hashlib.sha256(b"content").hexdigest()
        # Non-overwrite store correctly raises ArtifactAlreadyExistsError
        meta = ArtifactMetadata(
            artifact_id="corrupt",
            document_id="doc-1",
            media_type="application/pdf",
            checksum=checksum1,
        )
        with pytest.raises(ArtifactAlreadyExistsError):
            storage.store(b"content", meta, overwrite=False)

        checksum2 = hashlib.sha256(b"fixed-content").hexdigest()
        meta2 = ArtifactMetadata(
            artifact_id="corrupt",
            document_id="doc-1",
            media_type="application/pdf",
            checksum=checksum2,
        )
        # Overwrite store succeeds and replaces corrupted metadata
        storage.store(b"fixed-content", meta2, overwrite=True)
        assert storage.retrieve("corrupt") == b"fixed-content"

    def test_retrieved_content_size_mismatch_raises_storage_payload_error(
        self, storage: FileSystemArtifactStorage
    ) -> None:

        checksum = hashlib.sha256(b"expected-length-content").hexdigest()
        meta = ArtifactMetadata(
            artifact_id="art-tamper",
            document_id="doc-1",
            media_type="application/pdf",
            checksum=checksum,
        )
        storage.store(b"expected-length-content", meta)
        blob_path = next(storage.root_path.glob("art-tamper.*.data"))

        # Truncate content file on disk
        blob_path.write_bytes(b"short")

        with pytest.raises(StoragePayloadError, match="content size .* does not match metadata"):
            storage.retrieve("art-tamper")

    def test_size_bytes_mismatch_raises_storage_payload_error(
        self, storage: FileSystemArtifactStorage
    ) -> None:

        checksum = hashlib.sha256(b"short").hexdigest()
        metadata = ArtifactMetadata(
            artifact_id="art-mismatch",
            document_id="doc-1",
            media_type="application/pdf",
            checksum=checksum,
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


class TestChecksumResolution:
    def test_lookup_by_checksum_returns_artifact_id(
        self, storage: FileSystemArtifactStorage
    ) -> None:

        content = b"find me by checksum"
        checksum = hashlib.sha256(content).hexdigest()
        metadata = ArtifactMetadata(
            artifact_id="art-chk-1",
            document_id="doc-1",
            media_type="application/pdf",
            checksum=checksum,
        )
        storage.store(content, metadata)

        assert storage.lookup_by_checksum(checksum) == "art-chk-1"

    def test_lookup_by_checksum_returns_none_if_missing(
        self, storage: FileSystemArtifactStorage
    ) -> None:
        assert storage.lookup_by_checksum("a" * 64) is None

    def test_lookup_by_checksum_returns_none_if_invalid_format(
        self, storage: FileSystemArtifactStorage
    ) -> None:
        assert storage.lookup_by_checksum("invalid-format") is None
        assert storage.lookup_by_checksum("a" * 63) is None
        assert storage.lookup_by_checksum("g" * 64) is None

    def test_store_rejects_mismatched_caller_supplied_checksum(
        self, storage: FileSystemArtifactStorage
    ) -> None:
        metadata = ArtifactMetadata(
            artifact_id="art-chk-2",
            document_id="doc-1",
            media_type="application/pdf",
            checksum="b" * 64,  # Wrong checksum for content
        )
        with pytest.raises(StoragePayloadError, match="does not match"):
            storage.store(b"content", metadata)

    def test_store_rejects_checksum_already_mapped_to_different_artifact(
        self, storage: FileSystemArtifactStorage
    ) -> None:

        content = b"shared content"
        checksum = hashlib.sha256(content).hexdigest()

        metadata_1 = ArtifactMetadata(
            artifact_id="art-chk-first",
            document_id="doc-1",
            media_type="application/pdf",
            checksum=checksum,
        )
        storage.store(content, metadata_1)

        metadata_2 = ArtifactMetadata(
            artifact_id="art-chk-second",
            document_id="doc-2",
            media_type="application/pdf",
            checksum=checksum,
        )
        with pytest.raises(ArtifactAlreadyExistsError, match="mapped to artifact: art-chk-first"):
            storage.store(content, metadata_2)

    def test_overwrite_updates_checksum_pointer(self, storage: FileSystemArtifactStorage) -> None:

        content_1 = b"v1 content"
        checksum_1 = hashlib.sha256(content_1).hexdigest()

        metadata = ArtifactMetadata(
            artifact_id="art-chk-upd",
            document_id="doc-1",
            media_type="application/pdf",
            checksum=checksum_1,
        )
        storage.store(content_1, metadata)
        assert storage.lookup_by_checksum(checksum_1) == "art-chk-upd"

        content_2 = b"v2 content"
        checksum_2 = hashlib.sha256(content_2).hexdigest()
        metadata_2 = ArtifactMetadata(
            artifact_id="art-chk-upd",
            document_id="doc-1",
            media_type="application/pdf",
            checksum=checksum_2,
        )
        storage.store(content_2, metadata_2, overwrite=True)

        assert storage.lookup_by_checksum(checksum_2) == "art-chk-upd"
        assert storage.lookup_by_checksum(checksum_1) is None
