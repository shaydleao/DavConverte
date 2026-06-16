# DAVConvert 🎥

Conversor rápido de gravações de câmeras de segurança (`.DAV`) para **AVI** e **MP4**, com interface gráfica moderna e suporte a aceleração de hardware.

---

## ✨ Recursos

| Recurso | Detalhe |
|---|---|
| **Aceleração GPU** | Detecta NVENC (NVIDIA), VAAPI (Intel/AMD), QSV automaticamente |
| **Conversão paralela** | Converte múltiplos arquivos simultaneamente |
| **Interface gráfica** | GUI moderna com CustomTkinter |
| **CLI incluída** | Uso em scripts e automações |
| **Qualidade ajustável** | Alta / Média / Baixa com CRF |
| **Pasta recursiva** | Detecta .DAV em subpastas |

---

## 📦 Instalação

```bash
# Dependências Python
pip install customtkinter

# FFmpeg (se não tiver)
# Ubuntu/Debian:
sudo apt install ffmpeg

# Windows: baixar de https://ffmpeg.org/download.html
```

---

## 🚀 Uso

### Interface gráfica
```bash
python converter.py
```

### Linha de comando
```bash
# Converter uma pasta inteira para MP4
python cli.py /caminho/das/gravacoes -f mp4 -q medium -j 2

# Converter com alta qualidade e 4 jobs paralelos
python cli.py gravacoes/ -f mp4 -q high -j 4 -o /saida/

# Converter para AVI
python cli.py camera01.dav camera02.dav -f avi
```

#### Opções CLI
```
-f, --format     mp4 | avi          (padrão: mp4)
-q, --quality    high | medium | low (padrão: medium)
-j, --jobs       Conversões paralelas (padrão: 2)
-t, --threads    Threads CPU por job  (padrão: 4)
-o, --output     Pasta de saída
```

---

## ⚡ Por que é mais rápido que o Format Factory?

1. **Hardware encoding**: usa GPU diretamente (NVENC/VAAPI) quando disponível
2. **Paralelismo real**: converte N arquivos ao mesmo tempo com ThreadPoolExecutor
3. **FFmpeg nativo**: sem camadas de abstração, acesso direto ao codec
4. **Preset `fast`**: balanceia velocidade/qualidade em software
5. **`-movflags +faststart`**: escreve header MP4 no início para streaming

---

## 📁 Estrutura
```
dav_converter/
├── converter.py   # Interface gráfica
├── cli.py         # Linha de comando
└── README.md
```
