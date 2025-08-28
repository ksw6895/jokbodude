import tempfile
from pathlib import Path
from pdf_creator import PDFCreator

def test_resolve_lesson_path_ambiguous_collision():
    creator = PDFCreator()
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        # Create two files that normalize to the same key
        (base / "lecture-1.pdf").write_bytes(b"")
        (base / "lecture 1.pdf").write_bytes(b"")
        # Request a third variant that sanitizes to same key but doesn't exist
        resolved = creator._resolve_lesson_path(str(base), "lecture1.pdf")
        assert resolved == base / "lecture1.pdf"
        assert not resolved.exists()
