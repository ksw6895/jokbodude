#!/usr/bin/env python3
"""
Generate tiny sample PDFs for smoke testing.
Creates:
- jokbo/sample.pdf
- lesson/sample.pdf
"""
from pathlib import Path
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


def make_pdf(path: Path, title: str, body: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path), pagesize=A4)
    width, height = A4
    c.setFont("Helvetica-Bold", 16)
    c.drawString(72, height - 96, title)
    c.setFont("Helvetica", 12)
    y = height - 130
    for line in body.split("\n"):
        c.drawString(72, y, line)
        y -= 18
    c.showPage()
    c.save()


def main():
    make_pdf(Path("jokbo/sample.pdf"), "Jokbo Sample", "Q1. Dummy multiple choice question\n(1) A (2) B (3) C (4) D")
    make_pdf(Path("lesson/sample.pdf"), "Lesson Sample", "Slide: Related concept\nKey points:\n- Alpha\n- Beta")
    print("Generated jokbo/sample.pdf and lesson/sample.pdf")


if __name__ == "__main__":
    main()

