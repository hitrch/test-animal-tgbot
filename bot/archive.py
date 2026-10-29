"""Load the standard documents of "Лісовий архів" into the database."""
import asyncio
import csv
import hashlib
import logging
from pathlib import Path

import asyncpg

from bot.chunks import Chunk, split_text
from bot.db import add_document, document_exists
from bot.extract import extract_text
from bot.places import ALL_PLACES
from bot.topics import TOPICS

logger = logging.getLogger(__name__)

ARCHIVE_DIR = Path(__file__).parent.parent / "data" / "archive"
MANIFEST_NAME = "manifest.csv"
# Proofread text of scanned PDFs: OCR alone made mistakes in numbers
OCR_DIR_NAME = "ocr"


def read_manifest(path: Path) -> dict[str, tuple[str, list[str]]]:
    """Return {file name: (place, topics)} and check every value against the fixed lists."""
    manifest = {}
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f, delimiter=";"):
            place = row["place"]
            topics = row["topics"].split(",")
            if place not in ALL_PLACES:
                raise ValueError(f"{row['file']}: unknown place {place!r}")
            unknown = [topic for topic in topics if topic not in TOPICS]
            if unknown:
                raise ValueError(f"{row['file']}: unknown topics {unknown}")
            manifest[row["file"]] = (place, topics)
    return manifest


async def load_archive(pool: asyncpg.Pool, archive_dir: Path = ARCHIVE_DIR) -> None:
    manifest = read_manifest(archive_dir / MANIFEST_NAME)

    files_on_disk = {p.name for p in archive_dir.iterdir() if p.is_file() and p.name != MANIFEST_NAME}
    not_in_manifest = files_on_disk - manifest.keys()
    if not_in_manifest:
        raise ValueError(f"Archive files missing from {MANIFEST_NAME}: {sorted(not_in_manifest)}")

    loaded = skipped = 0
    for name, (place, topics) in manifest.items():
        data = (archive_dir / name).read_bytes()
        file_hash = hashlib.sha256(data).hexdigest()

        # Already loaded earlier (maybe even replaced by a newer user version): never load it again
        if await document_exists(pool, file_hash):
            skipped += 1
            continue

        # Text extraction (OCR for scans) is slow and blocking, so run it in a separate thread
        content = await asyncio.to_thread(_read_archive_text, archive_dir, name, data)
        # The manifest tags the whole document, so every part gets the same topics
        chunks = [Chunk(content=part, topics=topics) for part in split_text(content)]
        await add_document(
            pool,
            title=Path(name).stem,
            place=place,
            chunks=chunks,
            file_hash=file_hash,
            archive_path=name,
        )
        loaded += 1
        logger.info("Archive: loaded %s", name)

    logger.info("Archive: %d loaded, %d already in the database", loaded, skipped)


def _read_archive_text(archive_dir: Path, name: str, data: bytes) -> str:
    """Use the proofread text if there is one, otherwise extract the text from the file."""
    proofread = archive_dir / OCR_DIR_NAME / f"{Path(name).stem}.txt"
    if proofread.exists():
        return proofread.read_text(encoding="utf-8").strip()
    return extract_text(name, data)