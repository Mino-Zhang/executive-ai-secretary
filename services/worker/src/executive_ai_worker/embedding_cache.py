from __future__ import annotations

import hashlib
import os
import shutil
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class EmbeddingArtifact:
    model_name: str
    url: str
    sha256: str
    archive_root: str
    required_file: str


ARTIFACTS = {
    "BAAI/bge-small-zh-v1.5": EmbeddingArtifact(
        model_name="BAAI/bge-small-zh-v1.5",
        url="https://storage.googleapis.com/qdrant-fastembed/fast-bge-small-zh-v1.5.tar.gz",
        sha256="bf023219b6029148fddf764d248808816c0ca1f107f058231bb1ae0fa526f83f",
        archive_root="fast-bge-small-zh-v1.5",
        required_file="model_optimized.onnx",
    )
}


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download_with_resume(url: str, target: Path) -> None:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise RuntimeError("embedding artifact URL must use HTTPS")
    target.parent.mkdir(parents=True, exist_ok=True)
    existing_size = target.stat().st_size if target.exists() else 0
    headers = {"User-Agent": "executive-ai-embedding-preloader/1"}
    if existing_size:
        headers["Range"] = f"bytes={existing_size}-"
    request = urllib.request.Request(url, headers=headers)  # noqa: S310 - HTTPS only
    with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310 - HTTPS only
        response_status = getattr(response, "status", 200)
        append = existing_size > 0 and response_status == 206
        with target.open("ab" if append else "wb") as destination:
            shutil.copyfileobj(response, destination, length=1024 * 1024)


def _safe_extract(archive: Path, target: Path) -> None:
    target_root = target.resolve()
    with tarfile.open(archive, "r:gz") as bundle:
        for member in bundle.getmembers():
            member_target = (target / member.name).resolve()
            if target_root not in member_target.parents and member_target != target_root:
                raise RuntimeError("embedding archive contains an unsafe path")
            if member.issym() or member.islnk():
                raise RuntimeError("embedding archive must not contain links")
        bundle.extractall(target, filter="data")


def preload_artifact(artifact: EmbeddingArtifact, cache_dir: Path) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    final_dir = cache_dir / artifact.archive_root
    required = final_dir / artifact.required_file
    marker = final_dir / ".artifact-sha256"
    if required.is_file() and marker.is_file() and marker.read_text().strip() == artifact.sha256:
        return final_dir
    if final_dir.exists():
        raise RuntimeError(f"embedding cache is incomplete or unverified: {final_dir}")

    downloads = cache_dir / ".downloads"
    partial = downloads / f"{artifact.archive_root}.tar.gz.part"
    last_error: Exception | None = None
    download_verified = partial.is_file() and _file_sha256(partial) == artifact.sha256
    for attempt in range(1, 4):
        if download_verified:
            break
        try:
            _download_with_resume(artifact.url, partial)
            actual_sha256 = _file_sha256(partial)
            if actual_sha256 != artifact.sha256:
                partial.unlink(missing_ok=True)
                raise RuntimeError(
                    "embedding artifact integrity check failed: "
                    f"expected {artifact.sha256}, received {actual_sha256}"
                )
            download_verified = True
            break
        except (OSError, RuntimeError, urllib.error.URLError) as exc:
            last_error = exc
            if attempt == 3:
                raise RuntimeError("embedding artifact download failed") from exc
            time.sleep(2**attempt)
    else:  # pragma: no cover - defensive, the loop either breaks or raises.
        raise RuntimeError("embedding artifact download failed") from last_error

    staging = Path(tempfile.mkdtemp(prefix="embedding-preload-", dir=cache_dir))
    try:
        _safe_extract(partial, staging)
        extracted = staging / artifact.archive_root
        if not (extracted / artifact.required_file).is_file():
            raise RuntimeError("embedding archive does not contain the expected model file")
        (extracted / ".artifact-sha256").write_text(f"{artifact.sha256}\n")
        os.replace(extracted, final_dir)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        partial.unlink(missing_ok=True)
    return final_dir


def preload_embedding_model(model_name: str, cache_dir: Path) -> Path:
    artifact = ARTIFACTS.get(model_name)
    if artifact is None:
        raise RuntimeError(f"embedding model is not approved for production: {model_name}")
    return preload_artifact(artifact, cache_dir)


def main() -> None:
    model_name = os.environ.get("EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")
    cache_dir = Path(os.environ.get("EMBEDDING_CACHE_DIR", "/opt/models"))
    model_path = preload_embedding_model(model_name, cache_dir)
    # Loading one deterministic sentence proves the ONNX graph and tokenizer,
    # not just the archive, are usable before the file worker starts.
    from fastembed import TextEmbedding

    model = TextEmbedding(model_name=model_name, cache_dir=str(cache_dir), local_files_only=True)
    vector = next(model.embed(["向量模型启动验收"]))
    if len(vector) != 512:
        raise RuntimeError("embedding model dimension does not match the product schema")
    print(f"embedding model ready: {model_path}")


if __name__ == "__main__":
    main()
