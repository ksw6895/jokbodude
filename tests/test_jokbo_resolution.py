import tempfile
from pathlib import Path
from pdf_creator import PDFCreator


def test_resolve_jokbo_path_unique_sanitized_name():
    creator = PDFCreator()
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        # Only one file that matches after sanitization
        target = base / "exam 1.pdf"
        target.write_bytes(b"")
        resolved = creator._resolve_jokbo_path(str(base), "exam-1.pdf")
        assert resolved == target
        assert resolved.exists()


def test_resolve_jokbo_path_ambiguous_collision():
    creator = PDFCreator()
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        # Create two files that normalize to the same key
        (base / "exam-1.pdf").write_bytes(b"")
        (base / "exam 1.pdf").write_bytes(b"")
        # Request a third variant that sanitizes to the same key but doesn't exist
        resolved = creator._resolve_jokbo_path(str(base), "exam1.pdf")
        assert resolved == base / "exam1.pdf"
        assert not resolved.exists()
