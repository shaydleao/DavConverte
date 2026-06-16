#!/usr/bin/env python3
"""
DAVConvert — Conversor rápido de câmeras de segurança .DAV → AVI / MP4
Usa FFmpeg com aceleração de hardware (NVENC/VAAPI/QSV) quando disponível.
"""
import os, sys
# Procura ffmpeg na mesma pasta do executável
if getattr(sys, 'frozen', False):
    os.environ["PATH"] = os.path.dirname(sys.executable) + os.pathsep + os.environ["PATH"]
    
import os
import sys
import subprocess
import threading
import time
import queue
import re
import psutil
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import customtkinter as ctk

# ─── Paleta ────────────────────────────────────────────────────────────────────
DARK_BG    = "#0d0f14"
PANEL_BG   = "#141720"
CARD_BG    = "#1c2030"
ACCENT     = "#00d4ff"
ACCENT2    = "#0095b3"
SUCCESS    = "#00e676"
WARN       = "#ffab00"
ERROR      = "#ff5252"
TEXT       = "#e8ecf4"
TEXT_DIM   = "#6b7899"
FONT_MONO  = ("JetBrains Mono", 10) if sys.platform != "darwin" else ("Menlo", 10)

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


# ─── FFmpeg helpers ─────────────────────────────────────────────────────────────

def detect_hw_encoder(codec: str) -> str:
    """Detecta encoder de hardware disponível, cai em software se não houver."""
    hw_map = {
        "h264": ["h264_nvenc", "h264_vaapi", "h264_qsv", "libx264"],
        "hevc": ["hevc_nvenc", "hevc_vaapi", "hevc_qsv", "libx265"],
    }
    for enc in hw_map.get(codec, ["libx264"]):
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True
        )
        if enc in result.stdout:
            # Testa se realmente funciona
            test = subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error",
                 "-f", "lavfi", "-i", "nullsrc=s=64x64:d=0.1",
                 "-c:v", enc, "-f", "null", "-"],
                capture_output=True
            )
            if test.returncode == 0:
                return enc
    return "libx264"


def probe_video(path: str) -> dict:
    """Retorna metadados básicos do vídeo via ffprobe."""
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_streams", "-select_streams", "v:0", path
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        import json
        data = json.loads(r.stdout)
        if data.get("streams"):
            s = data["streams"][0]
            dur = float(s.get("duration", 0))
            nb_frames = int(s.get("nb_frames", 0))
            return {"duration": dur, "nb_frames": nb_frames,
                    "width": s.get("width", 0), "height": s.get("height", 0),
                    "codec": s.get("codec_name", "?")}
    except Exception:
        pass
    return {"duration": 0, "nb_frames": 0}


def build_ffmpeg_cmd(input_path: str, output_path: str,
                     fmt: str, quality: str, hw_enc: str,
                     threads: int) -> list:
    """Monta o comando FFmpeg otimizado."""
    ext = Path(output_path).suffix.lower()

    quality_map = {
        "Alta  (melhor qualidade)": "18",
        "Média (equilibrado)":      "23",
        "Baixa (arquivo menor)":    "28",
    }
    crf = quality_map.get(quality, "23")

    is_hw = hw_enc not in ("libx264", "libx265")

    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-stats",
        "-i", input_path,
    ]

    # Threads CPU
    cmd += ["-threads", str(threads)]

    # Codec de vídeo
    cmd += ["-c:v", hw_enc]

    if is_hw:
        # Hardware: usa qp em vez de crf
        qp = str(int(crf) - 5)  # ajusta escala
        cmd += ["-qp", qp]
    else:
        cmd += ["-crf", crf, "-preset", "fast"]

    # Áudio: copia direto se possível
    cmd += ["-c:a", "aac", "-b:a", "128k"]

    # Container-specific
    if ext == ".mp4":
        cmd += ["-movflags", "+faststart"]

    cmd += ["-y", output_path]
    return cmd


# ─── Parser de progresso FFmpeg ─────────────────────────────────────────────────

def parse_ffmpeg_time(line: str) -> float | None:
    """Extrai o tempo atual processado de uma linha de stats do FFmpeg (em segundos)."""
    m = re.search(r"time=(\d+):(\d+):(\d+)\.(\d+)", line)
    if m:
        h, mn, s, cs = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
        return h * 3600 + mn * 60 + s + cs / 100
    return None


def fmt_time(seconds: float) -> str:
    """Formata segundos em mm:ss."""
    if seconds <= 0:
        return "--:--"
    seconds = int(seconds)
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


# ─── Worker de conversão ────────────────────────────────────────────────────────

def convert_file(task: dict, log_queue: queue.Queue) -> dict:
    """Converte um arquivo .DAV. Retorna dict com resultado."""
    src      = task["src"]
    dst      = task["dst"]
    cmd      = task["cmd"]
    duration = task.get("duration", 0)
    start    = time.time()

    log_queue.put(("log", f"▶ {Path(src).name}"))

    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True, bufsize=1
        )

        last_pct = 0.0
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue

            t = parse_ffmpeg_time(line)
            if t is not None and duration > 0:
                pct = min(t / duration, 1.0)
                elapsed = time.time() - start
                speed = t / elapsed if elapsed > 0 else 0
                remaining = (duration - t) / speed if speed > 0 else 0

                if abs(pct - last_pct) >= 0.005:   # atualiza a cada 0.5%
                    last_pct = pct
                    log_queue.put(("file_progress", {
                        "src":       src,
                        "pct":       pct,
                        "elapsed":   elapsed,
                        "remaining": remaining,
                        "speed":     speed,
                        "cur_time":  t,
                        "duration":  duration,
                    }))

        proc.wait()
        elapsed = time.time() - start

        if proc.returncode == 0 and Path(dst).exists():
            size_mb = Path(dst).stat().st_size / 1024 / 1024
            log_queue.put(("file_progress", {"src": src, "pct": 1.0,
                                              "elapsed": elapsed, "remaining": 0,
                                              "speed": 0, "cur_time": duration,
                                              "duration": duration}))
            log_queue.put(("log", f"✔ {Path(dst).name}  [{elapsed:.1f}s  {size_mb:.1f} MB]"))
            return {"src": src, "ok": True, "time": elapsed}
        else:
            log_queue.put(("log", f"✘ Falha: {Path(src).name}"))
            return {"src": src, "ok": False, "time": elapsed}

    except Exception as e:
        log_queue.put(("log", f"✘ Erro: {e}"))
        return {"src": src, "ok": False, "time": 0}


# ─── GUI ────────────────────────────────────────────────────────────────────────

class DAVConverter(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("DAVConvert — Câmeras de Segurança")
        self.geometry("900x680")
        self.minsize(780, 560)
        self.configure(fg_color=DARK_BG)

        self.files: list[str] = []
        self.log_queue: queue.Queue = queue.Queue()
        self.running = False
        self.hw_enc = ""
        self._executor = None
        self._conv_start: float = 0
        self._total_files: int = 0
        self._done_files: int = 0

        self._build_ui()
        self._detect_hw()
        self.after(100, self._poll_queue)
        self.after(1000, self._update_hw_stats)

    # ── Layout ─────────────────────────────────────────────────────────────────

    def _build_ui(self):
        # Título
        hdr = ctk.CTkFrame(self, fg_color=PANEL_BG, corner_radius=0, height=56)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        ctk.CTkLabel(hdr, text="  ◉  DAVConvert",
                     font=ctk.CTkFont("Arial", 18, "bold"),
                     text_color=ACCENT).pack(side="left", padx=16, pady=12)
        self._hw_label = ctk.CTkLabel(hdr, text="Detectando hardware…",
                                       font=ctk.CTkFont("Arial", 11),
                                       text_color=TEXT_DIM)
        self._hw_label.pack(side="right", padx=16)

        # Body
        body = ctk.CTkFrame(self, fg_color=DARK_BG)
        body.pack(fill="both", expand=True, padx=14, pady=10)
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=2)
        body.rowconfigure(1, weight=1)

        # ── Coluna esquerda ──
        left = ctk.CTkFrame(body, fg_color=PANEL_BG, corner_radius=10)
        left.grid(row=0, column=0, rowspan=2, sticky="nsew", padx=(0,6), pady=0)
        left.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=1)

        # Botões de arquivo
        btn_row = ctk.CTkFrame(left, fg_color="transparent")
        btn_row.grid(row=0, column=0, sticky="ew", padx=12, pady=(12,6))
        ctk.CTkButton(btn_row, text="+ Adicionar .DAV",
                      fg_color=ACCENT2, hover_color=ACCENT,
                      text_color=DARK_BG, font=ctk.CTkFont("Arial", 12, "bold"),
                      command=self._add_files).pack(side="left", padx=(0,6))
        ctk.CTkButton(btn_row, text="Pasta inteira",
                      fg_color=CARD_BG, hover_color="#2a3050",
                      command=self._add_folder).pack(side="left", padx=(0,6))
        ctk.CTkButton(btn_row, text="Limpar",
                      fg_color="transparent", border_width=1,
                      border_color="#2a3050",
                      command=self._clear_files).pack(side="right")

        # Lista de arquivos
        list_frame = ctk.CTkFrame(left, fg_color=CARD_BG, corner_radius=8)
        list_frame.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0,12))
        list_frame.rowconfigure(0, weight=1)
        list_frame.columnconfigure(0, weight=1)

        self._file_list = tk.Listbox(
            list_frame, bg=CARD_BG, fg=TEXT, selectbackground=ACCENT2,
            selectforeground=DARK_BG, font=FONT_MONO,
            relief="flat", bd=0, activestyle="none",
            highlightthickness=0
        )
        self._file_list.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        sb = ctk.CTkScrollbar(list_frame, command=self._file_list.yview)
        sb.grid(row=0, column=1, sticky="ns", pady=6)
        self._file_list.configure(yscrollcommand=sb.set)

        self._count_label = ctk.CTkLabel(left, text="0 arquivo(s)",
                                          font=ctk.CTkFont("Arial", 10),
                                          text_color=TEXT_DIM)
        self._count_label.grid(row=2, column=0, sticky="w", padx=14, pady=(0,8))

        # ── Coluna direita ──
        right = ctk.CTkFrame(body, fg_color=PANEL_BG, corner_radius=10)
        right.grid(row=0, column=1, sticky="nsew", padx=(6,0), pady=0)
        right.columnconfigure(0, weight=1)

        def section(parent, label):
            ctk.CTkLabel(parent, text=label,
                         font=ctk.CTkFont("Arial", 10, "bold"),
                         text_color=TEXT_DIM).pack(anchor="w", padx=14, pady=(14,2))

        section(right, "FORMATO DE SAÍDA")
        self._fmt = ctk.CTkSegmentedButton(
            right, values=["MP4", "AVI"],
            fg_color=CARD_BG, selected_color=ACCENT2,
            selected_hover_color=ACCENT,
            unselected_color=CARD_BG,
            text_color=TEXT
        )
        self._fmt.set("MP4")
        self._fmt.pack(fill="x", padx=14)

        section(right, "QUALIDADE")
        self._quality = ctk.CTkComboBox(
            right,
            values=["Alta  (melhor qualidade)",
                    "Média (equilibrado)",
                    "Baixa (arquivo menor)"],
            fg_color=CARD_BG, border_color="#2a3050",
            button_color=ACCENT2, dropdown_fg_color=CARD_BG
        )
        self._quality.set("Média (equilibrado)")
        self._quality.pack(fill="x", padx=14)

        section(right, "THREADS CPU")
        cpu_count = os.cpu_count() or 4
        self._threads = ctk.CTkSlider(right, from_=1, to=cpu_count,
                                       number_of_steps=cpu_count - 1,
                                       button_color=ACCENT, progress_color=ACCENT2)
        self._threads.set(min(cpu_count, 4))
        self._threads.pack(fill="x", padx=14)
        self._threads_label = ctk.CTkLabel(right, text=f"4 threads",
                                            font=ctk.CTkFont("Arial", 10),
                                            text_color=TEXT_DIM)
        self._threads_label.pack(anchor="w", padx=14)
        self._threads.configure(command=self._update_threads)

        section(right, "CONVERSÕES PARALELAS")
        self._parallel = ctk.CTkSlider(right, from_=1, to=4,
                                        number_of_steps=3,
                                        button_color=ACCENT, progress_color=ACCENT2)
        self._parallel.set(2)
        self._parallel.pack(fill="x", padx=14)
        self._parallel_label = ctk.CTkLabel(right, text="2 ao mesmo tempo",
                                             font=ctk.CTkFont("Arial", 10),
                                             text_color=TEXT_DIM)
        self._parallel_label.pack(anchor="w", padx=14)
        self._parallel.configure(command=self._update_parallel)

        section(right, "PASTA DE SAÍDA")
        out_row = ctk.CTkFrame(right, fg_color="transparent")
        out_row.pack(fill="x", padx=14)
        self._out_var = tk.StringVar(value="Mesma pasta dos .DAV")
        self._out_entry = ctk.CTkEntry(out_row, textvariable=self._out_var,
                                        fg_color=CARD_BG, border_color="#2a3050")
        self._out_entry.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(out_row, text="…", width=32,
                      fg_color=CARD_BG, hover_color="#2a3050",
                      command=self._choose_output).pack(side="left", padx=(4,0))

        # Botão converter
        self._run_btn = ctk.CTkButton(
            right, text="⚡  CONVERTER",
            font=ctk.CTkFont("Arial", 14, "bold"),
            fg_color=ACCENT2, hover_color=ACCENT,
            text_color=DARK_BG, height=44,
            command=self._start_conversion
        )
        self._run_btn.pack(fill="x", padx=14, pady=(20,14))

        # ── Log / barra ──
        log_frame = ctk.CTkFrame(body, fg_color=CARD_BG, corner_radius=8, height=150)
        log_frame.grid(row=1, column=1, sticky="nsew", padx=(6,0), pady=(10,0))
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)

        self._log = tk.Text(log_frame, bg=CARD_BG, fg=TEXT,
                             font=FONT_MONO, relief="flat", bd=0,
                             state="disabled", wrap="none",
                             highlightthickness=0)
        self._log.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        lsb = ctk.CTkScrollbar(log_frame, command=self._log.yview)
        lsb.grid(row=0, column=1, sticky="ns", pady=4)
        self._log.configure(yscrollcommand=lsb.set)

        # ── Painel de progresso inferior ──
        prog_panel = ctk.CTkFrame(self, fg_color=PANEL_BG, corner_radius=0)
        prog_panel.pack(fill="x", side="bottom")

        # Linha 1 — arquivo atual + ETA
        row1 = ctk.CTkFrame(prog_panel, fg_color="transparent")
        row1.pack(fill="x", padx=14, pady=(8, 2))
        self._file_lbl = ctk.CTkLabel(row1, text="Pronto.",
                                       font=ctk.CTkFont("Arial", 10, "bold"),
                                       text_color=TEXT)
        self._file_lbl.pack(side="left")
        self._eta_lbl = ctk.CTkLabel(row1, text="",
                                      font=ctk.CTkFont("Arial", 10),
                                      text_color=TEXT_DIM)
        self._eta_lbl.pack(side="right")

        # Barra de progresso do arquivo atual
        self._file_progress = ctk.CTkProgressBar(prog_panel,
                                                   progress_color=ACCENT,
                                                   fg_color=CARD_BG, height=10)
        self._file_progress.set(0)
        self._file_progress.pack(fill="x", padx=14, pady=(0, 4))

        # Linha 2 — progresso geral + stats
        row2 = ctk.CTkFrame(prog_panel, fg_color="transparent")
        row2.pack(fill="x", padx=14, pady=(2, 2))
        self._status_lbl = ctk.CTkLabel(row2, text="",
                                         font=ctk.CTkFont("Arial", 10),
                                         text_color=TEXT_DIM)
        self._status_lbl.pack(side="left")
        self._hw_stats_lbl = ctk.CTkLabel(row2, text="",
                                           font=ctk.CTkFont("Arial", 10),
                                           text_color=TEXT_DIM)
        self._hw_stats_lbl.pack(side="right")

        # Barra de progresso geral (todos os arquivos)
        self._progress = ctk.CTkProgressBar(prog_panel,
                                             progress_color=ACCENT2,
                                             fg_color=CARD_BG, height=6)
        self._progress.set(0)
        self._progress.pack(fill="x", padx=14, pady=(0, 8))

    # ── Hardware detection ──────────────────────────────────────────────────────

    def _detect_hw(self):
        def _run():
            codec = "h264"
            enc = detect_hw_encoder(codec)
            self.hw_enc = enc
            label = f"GPU: {enc}" if enc not in ("libx264","libx265") else f"CPU: {enc}"
            self._hw_label.configure(text=label,
                text_color=SUCCESS if "nvenc" in enc or "vaapi" in enc else TEXT_DIM)
        threading.Thread(target=_run, daemon=True).start()

    def _update_hw_stats(self):
        """Atualiza CPU/RAM a cada segundo no rodapé."""
        try:
            cpu = psutil.cpu_percent(interval=None)
            ram = psutil.virtual_memory()
            ram_used = ram.used / 1024 / 1024 / 1024
            ram_total = ram.total / 1024 / 1024 / 1024
            self._hw_stats_lbl.configure(
                text=f"CPU {cpu:.0f}%  •  RAM {ram_used:.1f}/{ram_total:.1f} GB"
            )
        except Exception:
            pass
        self.after(1000, self._update_hw_stats)

    

    def _add_files(self):
        paths = filedialog.askopenfilenames(
            title="Selecionar arquivos .DAV",
            filetypes=[("DAV files", "*.dav *.DAV"), ("All files", "*.*")]
        )
        for p in paths:
            if p not in self.files:
                self.files.append(p)
        self._refresh_list()

    def _add_folder(self):
        folder = filedialog.askdirectory(title="Selecionar pasta com arquivos .DAV")
        if folder:
            found = list(Path(folder).rglob("*.dav")) + list(Path(folder).rglob("*.DAV"))
            for p in found:
                s = str(p)
                if s not in self.files:
                    self.files.append(s)
            self._refresh_list()

    def _clear_files(self):
        self.files.clear()
        self._refresh_list()

    def _refresh_list(self):
        self._file_list.delete(0, "end")
        for f in self.files:
            self._file_list.insert("end", f"  {Path(f).name}")
        self._count_label.configure(text=f"{len(self.files)} arquivo(s)")

    def _choose_output(self):
        d = filedialog.askdirectory(title="Pasta de saída")
        if d:
            self._out_var.set(d)

    def _update_threads(self, val):
        self._threads_label.configure(text=f"{int(val)} threads")

    def _update_parallel(self, val):
        n = int(val)
        self._parallel_label.configure(text=f"{n} ao mesmo tempo")

    # ── Conversion ──────────────────────────────────────────────────────────────

    def _start_conversion(self):
        if self.running:
            return
        if not self.files:
            messagebox.showwarning("Sem arquivos", "Adicione ao menos um arquivo .DAV.")
            return
        if not self.hw_enc:
            messagebox.showwarning("Aguarde", "Detecção de hardware ainda em curso…")
            return

        fmt         = self._fmt.get().lower()
        quality     = self._quality.get()
        threads     = int(self._threads.get())
        parallel    = int(self._parallel.get())
        out_dir_val = self._out_var.get()

        # Probing rápido para duração de cada arquivo
        self._log_write("Lendo metadados dos arquivos…\n")
        tasks = []
        for src in self.files:
            if out_dir_val == "Mesma pasta dos .DAV":
                out_dir = str(Path(src).parent)
            else:
                out_dir = out_dir_val
                Path(out_dir).mkdir(parents=True, exist_ok=True)

            dst      = str(Path(out_dir) / (Path(src).stem + f".{fmt}"))
            meta     = probe_video(src)
            duration = meta.get("duration", 0)
            cmd      = build_ffmpeg_cmd(src, dst, fmt, quality, self.hw_enc, threads)
            tasks.append({"src": src, "dst": dst, "cmd": cmd, "duration": duration})

        self.running        = True
        self._conv_start    = time.time()
        self._total_files   = len(tasks)
        self._done_files    = 0

        self._run_btn.configure(state="disabled", text="⏳ Convertendo…")
        self._progress.set(0)
        self._file_progress.set(0)
        self._file_lbl.configure(text="Iniciando conversão…", text_color=TEXT)
        self._eta_lbl.configure(text="")
        self._log_write("─" * 40 + "\n")

        def _worker():
            done = 0
            ok   = 0
            total = len(tasks)
            with ThreadPoolExecutor(max_workers=parallel) as exe:
                futures = {exe.submit(convert_file, t, self.log_queue): t for t in tasks}
                for f in as_completed(futures):
                    res = f.result()
                    done += 1
                    if res["ok"]:
                        ok += 1
                    self.log_queue.put(("progress", done / total))
                    self.log_queue.put(("status",
                        f"Arquivos: {done}/{total}  ✔ {ok} concluídos"))

            self.log_queue.put(("done", (ok, total - ok)))

        threading.Thread(target=_worker, daemon=True).start()

    # ── Queue polling ───────────────────────────────────────────────────────────

    def _poll_queue(self):
        try:
            while True:
                msg  = self.log_queue.get_nowait()
                kind = msg[0]
                data = msg[1]

                if kind == "log":
                    self._log_write(data + "\n")

                elif kind == "progress":
                    self._progress.set(data)

                elif kind == "status":
                    self._status_lbl.configure(text=data)

                elif kind == "file_progress":
                    pct       = data["pct"]
                    remaining = data["remaining"]
                    elapsed   = data["elapsed"]
                    speed     = data["speed"]
                    cur_time  = data["cur_time"]
                    duration  = data["duration"]
                    src       = data["src"]

                    self._file_progress.set(pct)

                    name = Path(src).name
                    pct_str = f"{pct*100:.1f}%"
                    self._file_lbl.configure(
                        text=f"{name}  —  {pct_str}  ({fmt_time(cur_time)} / {fmt_time(duration)})",
                        text_color=TEXT
                    )

                    if pct >= 1.0:
                        self._eta_lbl.configure(
                            text=f"✔ concluído em {fmt_time(elapsed)}",
                            text_color=SUCCESS
                        )
                    elif speed > 0:
                        # ETA global baseado no tempo decorrido total
                        total_elapsed = time.time() - self._conv_start if self._conv_start else elapsed
                        eta_str = fmt_time(remaining)
                        speed_str = f"{speed:.1f}x"
                        self._eta_lbl.configure(
                            text=f"ETA {eta_str}  •  {speed_str} velocidade",
                            text_color=TEXT_DIM
                        )

                elif kind == "done":
                    ok, fail = data
                    total_t  = time.time() - self._conv_start if self._conv_start else 0
                    self._log_write(
                        f"\n{'─'*40}\n"
                        f"Concluído: {ok} ✔  {fail} ✘  |  Tempo total: {fmt_time(total_t)}\n"
                    )
                    self._file_lbl.configure(
                        text=f"Pronto — {ok} convertidos, {fail} com erro.  ({fmt_time(total_t)})",
                        text_color=SUCCESS if fail == 0 else WARN
                    )
                    self._eta_lbl.configure(text="")
                    self._status_lbl.configure(text="")
                    self._run_btn.configure(state="normal", text="⚡  CONVERTER")
                    self._progress.set(1.0)
                    self._file_progress.set(1.0 if fail == 0 else 0)
                    self.running = False

        except queue.Empty:
            pass
        self.after(80, self._poll_queue)

    def _log_write(self, text: str):
        self._log.configure(state="normal")
        self._log.insert("end", text)
        self._log.see("end")
        self._log.configure(state="disabled")


# ─── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = DAVConverter()
    app.mainloop()
