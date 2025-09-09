#!/usr/bin/env python3
import os
import threading
from pathlib import Path
from datetime import datetime
from typing import List

try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    from tkinter.scrolledtext import ScrolledText
    import tkinter.font as tkfont
except ImportError:
    raise SystemExit("Tkinter is not available. Please install Tk support for your Python (e.g., sudo apt-get install python3-tk) and ensure an X server is running.")

from config import create_model, configure_api
from pdf_processor import PDFProcessor
from pdf_creator import PDFCreator
from path_validator import PathValidator


def find_pdf_files(directory: str, pattern: str = "*.pdf") -> List[Path]:
    path = Path(directory)
    pdf_files = list(path.glob(pattern))
    valid_files = []
    for f in pdf_files:
        if not f.name.endswith('.Zone.Identifier') and PathValidator.validate_pdf_filename(f.name):
            valid_files.append(f)
    return valid_files


class JokboDudeGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("JokboDude - PDF Processor")
        self.root.geometry("980x720")

        # Initialize UI font with Korean-capable family if available
        self.ui_font = self._init_fonts()

        self.mode_var = tk.StringVar(value="lesson-centric")
        self.model_var = tk.StringVar(value="flash")
        self.multi_api_var = tk.BooleanVar(value=False)
        self.thinking_budget_var = tk.StringVar(value="")

        # Paths
        self.lesson_file_var = tk.StringVar()
        self.jokbo_file_var = tk.StringVar()
        self.lesson_dir_var = tk.StringVar(value=str(Path("lesson").absolute()))
        self.jokbo_dir_var = tk.StringVar(value=str(Path("jokbo").absolute()))
        self.output_dir_var = tk.StringVar(value=str(Path("output").absolute()))

        # Additional multi-select sources
        self.lc_lesson_dir_var = tk.StringVar(value="")  # optional lesson dir for lesson-centric
        self.lc_lesson_files: List[str] = []
        self.lc_jokbo_files: List[str] = []
        self.jc_jokbo_files: List[str] = []
        self.jc_lesson_files: List[str] = []

        self._build_ui()

    def _build_ui(self):
        pad = {"padx": 8, "pady": 6}

        # Top controls
        top = ttk.Frame(self.root)
        top.pack(fill="x", **pad)

        ttk.Label(top, text="Mode:").pack(side="left")
        ttk.Radiobutton(top, text="Lesson-centric", variable=self.mode_var, value="lesson-centric", command=self._on_mode_change).pack(side="left", padx=4)
        ttk.Radiobutton(top, text="Jokbo-centric", variable=self.mode_var, value="jokbo-centric", command=self._on_mode_change).pack(side="left", padx=4)

        ttk.Label(top, text="Model:").pack(side="left", padx=(20, 4))
        ttk.Combobox(top, textvariable=self.model_var, values=["pro", "flash", "flash-lite"], state="readonly", width=10).pack(side="left")

        ttk.Label(top, text="Thinking budget:").pack(side="left", padx=(20, 4))
        ttk.Entry(top, textvariable=self.thinking_budget_var, width=10).pack(side="left")

        ttk.Checkbutton(top, text="Multi-API", variable=self.multi_api_var).pack(side="left", padx=(20, 4))

        # Paths group
        paths = ttk.LabelFrame(self.root, text="Inputs")
        paths.pack(fill="x", **pad)

        # Lesson-centric inputs
        self.lesson_centric_frame = ttk.Frame(paths)
        self.lesson_centric_frame.pack(fill="x", **pad)

        lc1 = ttk.Frame(self.lesson_centric_frame)
        lc1.pack(fill="x", **pad)
        ttk.Label(lc1, text="Lesson PDF (single, optional):").pack(side="left")
        ttk.Entry(lc1, textvariable=self.lesson_file_var, width=80).pack(side="left", padx=6)
        ttk.Button(lc1, text="Browse", command=self._browse_lesson_file).pack(side="left")

        # Optional multiple lesson files and directory for lesson-centric
        lc1b = ttk.LabelFrame(self.lesson_centric_frame, text="Lesson files (optional)")
        lc1b.pack(fill="x", **pad)
        lc1b_top = ttk.Frame(lc1b)
        lc1b_top.pack(fill="x")
        ttk.Button(lc1b_top, text="Add Files", command=lambda: self._add_files(self.lc_lesson_files, self.lc_lesson_list, many=True)).pack(side="left")
        ttk.Button(lc1b_top, text="Clear", command=lambda: self._clear_files(self.lc_lesson_files, self.lc_lesson_list)).pack(side="left", padx=6)
        self.lc_lesson_list = tk.Listbox(lc1b, height=4)
        self.lc_lesson_list.pack(fill="x", padx=4, pady=4)

        lc1c = ttk.Frame(self.lesson_centric_frame)
        lc1c.pack(fill="x", **pad)
        ttk.Label(lc1c, text="Lesson directory (optional):").pack(side="left")
        ttk.Entry(lc1c, textvariable=self.lc_lesson_dir_var, width=80).pack(side="left", padx=6)
        ttk.Button(lc1c, text="Browse", command=lambda: self._browse_dir(self.lc_lesson_dir_var)).pack(side="left")

        lc2 = ttk.Frame(self.lesson_centric_frame)
        lc2.pack(fill="x", **pad)
        ttk.Label(lc2, text="Jokbo directory (optional):").pack(side="left")
        ttk.Entry(lc2, textvariable=self.jokbo_dir_var, width=80).pack(side="left", padx=6)
        ttk.Button(lc2, text="Browse", command=self._browse_jokbo_dir).pack(side="left")

        lc2b = ttk.LabelFrame(self.lesson_centric_frame, text="Jokbo files (optional)")
        lc2b.pack(fill="x", **pad)
        lc2b_top = ttk.Frame(lc2b)
        lc2b_top.pack(fill="x")
        ttk.Button(lc2b_top, text="Add Files", command=lambda: self._add_files(self.lc_jokbo_files, self.lc_jokbo_list, many=True)).pack(side="left")
        ttk.Button(lc2b_top, text="Clear", command=lambda: self._clear_files(self.lc_jokbo_files, self.lc_jokbo_list)).pack(side="left", padx=6)
        self.lc_jokbo_list = tk.Listbox(lc2b, height=4)
        self.lc_jokbo_list.pack(fill="x", padx=4, pady=4)

        # Jokbo-centric inputs
        self.jokbo_centric_frame = ttk.Frame(paths)
        self.jokbo_centric_frame.pack(fill="x", **pad)

        jc1 = ttk.Frame(self.jokbo_centric_frame)
        jc1.pack(fill="x", **pad)
        ttk.Label(jc1, text="Jokbo PDF (single, optional):").pack(side="left")
        ttk.Entry(jc1, textvariable=self.jokbo_file_var, width=80).pack(side="left", padx=6)
        ttk.Button(jc1, text="Browse", command=self._browse_jokbo_file).pack(side="left")

        jc1b = ttk.LabelFrame(self.jokbo_centric_frame, text="Jokbo files (optional)")
        jc1b.pack(fill="x", **pad)
        jc1b_top = ttk.Frame(jc1b)
        jc1b_top.pack(fill="x")
        ttk.Button(jc1b_top, text="Add Files", command=lambda: self._add_files(self.jc_jokbo_files, self.jc_jokbo_list, many=True)).pack(side="left")
        ttk.Button(jc1b_top, text="Clear", command=lambda: self._clear_files(self.jc_jokbo_files, self.jc_jokbo_list)).pack(side="left", padx=6)
        self.jc_jokbo_list = tk.Listbox(jc1b, height=4)
        self.jc_jokbo_list.pack(fill="x", padx=4, pady=4)

        jc2 = ttk.Frame(self.jokbo_centric_frame)
        jc2.pack(fill="x", **pad)
        ttk.Label(jc2, text="Lesson directory (optional):").pack(side="left")
        ttk.Entry(jc2, textvariable=self.lesson_dir_var, width=80).pack(side="left", padx=6)
        ttk.Button(jc2, text="Browse", command=self._browse_lesson_dir).pack(side="left")

        jc2b = ttk.LabelFrame(self.jokbo_centric_frame, text="Lesson files (optional)")
        jc2b.pack(fill="x", **pad)
        jc2b_top = ttk.Frame(jc2b)
        jc2b_top.pack(fill="x")
        ttk.Button(jc2b_top, text="Add Files", command=lambda: self._add_files(self.jc_lesson_files, self.jc_lesson_list, many=True)).pack(side="left")
        ttk.Button(jc2b_top, text="Clear", command=lambda: self._clear_files(self.jc_lesson_files, self.jc_lesson_list)).pack(side="left", padx=6)
        self.jc_lesson_list = tk.Listbox(jc2b, height=4)
        self.jc_lesson_list.pack(fill="x", padx=4, pady=4)

        # Output
        out = ttk.Frame(paths)
        out.pack(fill="x", **pad)
        ttk.Label(out, text="Output directory:").pack(side="left")
        ttk.Entry(out, textvariable=self.output_dir_var, width=80).pack(side="left", padx=6)
        ttk.Button(out, text="Browse", command=self._browse_output_dir).pack(side="left")

        # Actions
        actions = ttk.Frame(self.root)
        actions.pack(fill="x", **pad)
        ttk.Button(actions, text="Start", command=self._on_start).pack(side="left")
        ttk.Button(actions, text="List sessions", command=self._on_list_sessions).pack(side="left", padx=6)
        ttk.Button(actions, text="Cleanup sessions", command=self._on_cleanup_sessions).pack(side="left")

        # Log area
        self.log_text = ScrolledText(self.root, height=20)
        try:
            self.log_text.configure(font=self.ui_font)
        except Exception:
            pass
        self.log_text.pack(fill="both", expand=True, **pad)

        self._on_mode_change()

    def _init_fonts(self):
        """Pick a font family that supports Korean and apply globally to ttk."""
        # Candidate families commonly available across platforms
        candidates = [
            "Malgun Gothic",  # Windows
            "맑은 고딕",
            "NanumGothic",    # Linux (fonts-nanum)
            "Noto Sans CJK KR",
            "Noto Sans Korean",
            "Apple SD Gothic Neo",  # macOS
            "D2Coding",
            "Pretendard",
            "Spoqa Han Sans Neo",
            "Gulim",
            "굴림",
        ]
        families = set()
        try:
            families = set(tkfont.families())
        except Exception:
            pass
        chosen = None
        for name in candidates:
            if name in families:
                chosen = name
                break
        # Fallback to a sans-serif default if none found
        if chosen is None:
            chosen = "Sans"
        # Create font object
        try:
            font_obj = tkfont.Font(family=chosen, size=10)
        except Exception:
            font_obj = (chosen, 10)

        # Apply to ttk globally
        try:
            style = ttk.Style()
            style.configure(".", font=font_obj)
            # Ensure common widgets get it
            for widget_style in [
                "TLabel", "TButton", "TEntry", "TCheckbutton", "TRadiobutton",
                "TCombobox", "TMenubutton", "TNotebook.Tab", "TFrame", "TLabelframe",
            ]:
                style.configure(widget_style, font=font_obj)
            # Also set Tk option for classic widgets
            self.root.option_add("*Font", font_obj)
        except Exception:
            pass

        # If we likely don't have CJK fonts, log a hint
        if chosen in ("Sans",):
            # Non-blocking info in log area after it's created
            pass
        return font_obj

    def log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert("end", f"[{ts}] {msg}\n")
        self.log_text.see("end")
        self.root.update_idletasks()

    def _browse_lesson_file(self):
        path = filedialog.askopenfilename(title="Select lesson PDF", filetypes=[("PDF files", "*.pdf")])
        if path:
            self.lesson_file_var.set(path)

    def _browse_jokbo_file(self):
        path = filedialog.askopenfilename(title="Select jokbo PDF", filetypes=[("PDF files", "*.pdf")])
        if path:
            self.jokbo_file_var.set(path)

    def _browse_lesson_dir(self):
        path = filedialog.askdirectory(title="Select lesson directory")
        if path:
            self.lesson_dir_var.set(path)

    def _browse_jokbo_dir(self):
        path = filedialog.askdirectory(title="Select jokbo directory")
        if path:
            self.jokbo_dir_var.set(path)

    def _browse_output_dir(self):
        path = filedialog.askdirectory(title="Select output directory")
        if path:
            self.output_dir_var.set(path)

    def _browse_dir(self, var: tk.StringVar):
        path = filedialog.askdirectory(title="Select directory")
        if path:
            var.set(path)

    def _add_files(self, storage: List[str], listbox: tk.Listbox, many: bool = True):
        if many:
            files = filedialog.askopenfilenames(title="Select PDF files", filetypes=[("PDF files", "*.pdf")])
        else:
            f = filedialog.askopenfilename(title="Select PDF file", filetypes=[("PDF files", "*.pdf")])
            files = [f] if f else []
        for f in files:
            if f and f not in storage and Path(f).suffix.lower() == ".pdf":
                storage.append(f)
                listbox.insert("end", f)

    def _clear_files(self, storage: List[str], listbox: tk.Listbox):
        storage.clear()
        listbox.delete(0, "end")

    def _on_mode_change(self):
        if self.mode_var.get() == "lesson-centric":
            self.lesson_centric_frame.tkraise()
            self.lesson_centric_frame.pack(fill="x")
            self.jokbo_centric_frame.forget()
        else:
            self.jokbo_centric_frame.tkraise()
            self.jokbo_centric_frame.pack(fill="x")
            self.lesson_centric_frame.forget()

    def _validate_inputs(self) -> bool:
        mode = self.mode_var.get()
        if mode == "lesson-centric":
            # At least one lesson source (single file, multi files, or directory)
            has_single = bool(self.lesson_file_var.get().strip()) and Path(self.lesson_file_var.get().strip()).exists()
            has_multi = len(self.lc_lesson_files) > 0
            has_dir = bool(self.lc_lesson_dir_var.get().strip()) and Path(self.lc_lesson_dir_var.get().strip()).exists()
            if not (has_single or has_multi or has_dir):
                messagebox.showerror("Error", "Provide a lesson PDF, add lesson files, or select a lesson directory")
                return False
            # At least one jokbo source (files or directory)
            has_jokbo_files = len(self.lc_jokbo_files) > 0
            has_jokbo_dir = bool(self.jokbo_dir_var.get().strip()) and Path(self.jokbo_dir_var.get().strip()).exists()
            if not (has_jokbo_files or has_jokbo_dir):
                messagebox.showerror("Error", "Add jokbo files or select a jokbo directory")
                return False
        else:
            # At least one jokbo source
            has_single = bool(self.jokbo_file_var.get().strip()) and Path(self.jokbo_file_var.get().strip()).exists()
            has_multi = len(self.jc_jokbo_files) > 0
            has_dir = bool(self.jokbo_dir_var.get().strip()) and Path(self.jokbo_dir_var.get().strip()).exists()
            if not (has_single or has_multi or has_dir):
                messagebox.showerror("Error", "Provide a jokbo PDF, add jokbo files, or select a jokbo directory")
                return False
            # At least one lesson source
            has_l_single = False  # not using single lesson var in JC
            has_l_multi = len(self.jc_lesson_files) > 0
            has_l_dir = bool(self.lesson_dir_var.get().strip()) and Path(self.lesson_dir_var.get().strip()).exists()
            if not (has_l_multi or has_l_dir):
                messagebox.showerror("Error", "Add lesson files or select a lesson directory")
                return False

        od = self.output_dir_var.get().strip()
        if not od:
            messagebox.showerror("Error", "Select an output directory")
            return False
        Path(od).mkdir(parents=True, exist_ok=True)
        return True

    def _on_start(self):
        if not self._validate_inputs():
            return
        threading.Thread(target=self._run_pipeline, daemon=True).start()

    def _run_pipeline(self):
        try:
            mode = self.mode_var.get()
            model_name = self.model_var.get()
            thinking_budget = self._parse_thinking_budget(self.thinking_budget_var.get().strip())
            use_multi_api = self.multi_api_var.get()

            # Configure API and create model
            configure_api()
            model = create_model(model_name, thinking_budget)
            self.log(f"Using model: Gemini 2.5 {model_name.upper()}")

            processor = PDFProcessor(model)
            creator = PDFCreator()

            output_dir = Path(self.output_dir_var.get().strip())
            output_dir.mkdir(parents=True, exist_ok=True)

            if mode == "lesson-centric":
                # Collect lessons
                lessons: List[str] = []
                lf = self.lesson_file_var.get().strip()
                if lf and Path(lf).exists():
                    lessons.append(str(Path(lf).absolute()))
                lessons.extend([str(Path(p).absolute()) for p in self.lc_lesson_files if Path(p).exists()])
                if self.lc_lesson_dir_var.get().strip():
                    lessons.extend([str(p) for p in find_pdf_files(self.lc_lesson_dir_var.get().strip())])
                # Deduplicate
                lessons = sorted(set(lessons))
                if not lessons:
                    self.log("No lesson PDFs provided.")
                    messagebox.showerror("Error", "No lesson PDFs provided.")
                    return
                # Collect jokbos
                jokbo_files: List[str] = []
                if self.jokbo_dir_var.get().strip():
                    jokbo_files.extend([str(p) for p in find_pdf_files(self.jokbo_dir_var.get().strip())])
                jokbo_files.extend([str(Path(p).absolute()) for p in self.lc_jokbo_files if Path(p).exists()])
                jokbo_files = sorted(set(jokbo_files))
                if not jokbo_files:
                    self.log("No jokbo PDFs provided.")
                    messagebox.showerror("Error", "No jokbo PDFs provided.")
                    return
                self.log(f"Lesson-centric: {len(jokbo_files)} jokbos vs {len(lessons)} lesson(s)")

                # Process each lesson
                for lesson_path_str in lessons:
                    lesson_path = Path(lesson_path_str)
                    if use_multi_api:
                        try:
                            from config import API_KEYS
                        except Exception:
                            API_KEYS = []
                        if len(API_KEYS) < 2:
                            self.log("Multi-API requested but <2 API keys; running in single-API mode.")
                            analysis_result = processor.analyze_lesson_centric(jokbo_files, str(lesson_path))
                        else:
                            self.log(f"Multi-API active with {len(API_KEYS)} keys")
                            analysis_result = processor.analyze_lesson_centric_multi_api(jokbo_files, str(lesson_path), API_KEYS)
                    else:
                        analysis_result = processor.analyze_lesson_centric(jokbo_files, str(lesson_path))

                    if "error" in analysis_result:
                        self.log(f"Error: {analysis_result['error']}")
                        messagebox.showerror("Error", str(analysis_result['error']))
                        return

                    out_path = output_dir / f"filtered_{lesson_path.stem}_all_jokbos.pdf"
                    self.log(f"Creating lesson-centric PDF for {lesson_path.name}...")
                    # Use jokbo_dir as base for locating jokbo PDFs
                    jokbo_dir = Path(self.jokbo_dir_var.get().strip() or ".")
                    creator.create_filtered_pdf(str(lesson_path), analysis_result, str(out_path), str(jokbo_dir))
                    self.log(f"Done: {out_path}")

            else:  # jokbo-centric
                # Collect jokbos
                jokbos: List[str] = []
                jf = self.jokbo_file_var.get().strip()
                if jf and Path(jf).exists():
                    jokbos.append(str(Path(jf).absolute()))
                jokbos.extend([str(Path(p).absolute()) for p in self.jc_jokbo_files if Path(p).exists()])
                if self.jokbo_dir_var.get().strip():
                    jokbos.extend([str(p) for p in find_pdf_files(self.jokbo_dir_var.get().strip())])
                jokbos = sorted(set(jokbos))
                if not jokbos:
                    self.log("No jokbo PDFs provided.")
                    messagebox.showerror("Error", "No jokbo PDFs provided.")
                    return
                # Collect lessons
                lesson_files: List[str] = []
                lesson_files.extend([str(Path(p).absolute()) for p in self.jc_lesson_files if Path(p).exists()])
                if self.lesson_dir_var.get().strip():
                    lesson_files.extend([str(p) for p in find_pdf_files(self.lesson_dir_var.get().strip())])
                lesson_files = sorted(set(lesson_files))
                if not lesson_files:
                    self.log("No lesson PDFs provided.")
                    messagebox.showerror("Error", "No lesson PDFs provided.")
                    return
                self.log(f"Jokbo-centric: {len(lesson_files)} lessons vs {len(jokbos)} jokbo(s)")

                # Process each jokbo
                for jokbo_path_str in jokbos:
                    jokbo_path = Path(jokbo_path_str)
                    if use_multi_api:
                        try:
                            from config import API_KEYS
                        except Exception:
                            API_KEYS = []
                        if len(API_KEYS) < 2:
                            self.log("Multi-API requested but <2 API keys; running in single-API mode.")
                            analysis_result = processor.analyze_jokbo_centric(lesson_files, str(jokbo_path))
                        else:
                            self.log(f"Multi-API active with {len(API_KEYS)} keys")
                            analysis_result = processor.analyze_jokbo_centric_multi_api(lesson_files, str(jokbo_path), API_KEYS)
                    else:
                        analysis_result = processor.analyze_jokbo_centric(lesson_files, str(jokbo_path))

                    if "error" in analysis_result:
                        self.log(f"Error: {analysis_result['error']}")
                        messagebox.showerror("Error", str(analysis_result['error']))
                        return

                    out_path = output_dir / f"jokbo_centric_{jokbo_path.stem}_all_lessons.pdf"
                    self.log(f"Creating jokbo-centric PDF for {jokbo_path.name}...")
                    creator.create_jokbo_centric_pdf(str(jokbo_path), analysis_result, str(out_path), str(self.lesson_dir_var.get().strip() or "."))
                    self.log(f"Done: {out_path}")

            messagebox.showinfo("Completed", "PDF generation completed successfully.")

        except Exception as e:
            self.log(f"Error: {e}")
            messagebox.showerror("Error", str(e))

    def _parse_thinking_budget(self, s: str):
        if not s:
            return None
        try:
            return int(s)
        except ValueError:
            return None

    def _on_list_sessions(self):
        sessions_dir = Path("output/temp/sessions")
        if not sessions_dir.exists():
            self.log("No sessions directory.")
            return
        rows = []
        for session_dir in sessions_dir.iterdir():
            if session_dir.is_dir():
                state_file = session_dir / "processing_state.json"
                chunk_dir = session_dir / "chunk_results"
                size = sum(f.stat().st_size for f in session_dir.rglob('*') if f.is_file())
                chunk_count = len(list(chunk_dir.glob('*.json'))) if chunk_dir.exists() else 0
                created = datetime.fromtimestamp(session_dir.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                rows.append((session_dir.name, chunk_count, f"{size/(1024*1024):.1f}MB", created))
        if not rows:
            self.log("No sessions found.")
            return
        self.log("Sessions:")
        for name, chunks, size, created in sorted(rows, key=lambda x: x[3], reverse=True):
            self.log(f"  {name} | chunks={chunks} | size={size} | created={created}")

    def _on_cleanup_sessions(self):
        sessions_dir = Path("output/temp/sessions")
        if not sessions_dir.exists():
            self.log("No sessions directory.")
            return
        removed = 0
        for session_dir in sessions_dir.iterdir():
            if session_dir.is_dir():
                try:
                    for p in session_dir.rglob('*'):
                        if p.is_file():
                            p.unlink(missing_ok=True)
                    session_dir.rmdir()
                    removed += 1
                except Exception:
                    # Fallback: ignore errors
                    pass
        self.log(f"Removed {removed} session(s).")


def main():
    root = tk.Tk()
    app = JokboDudeGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()

