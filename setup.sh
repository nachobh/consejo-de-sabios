#!/usr/bin/env bash
# Descarga los modelos GGUF de sabios2. Variables: SABIOS2_MODEL_DIR, HF_BIN.
set -euo pipefail
BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
MODEL_DIR="${SABIOS2_MODEL_DIR:-$HOME/models/sabios2}"
[[ "${1:-}" == "--skip-download" ]] && SKIP=1 || SKIP=0
HF_BIN="${HF_BIN:-hf}"
download(){ local repo="$1" file="$2" dest="$3"; mkdir -p "$dest"; [[ "$SKIP" == 1 ]] && { echo "[skip] $repo/$file"; return; }; command -v "$HF_BIN" >/dev/null || { echo "Instala huggingface_hub: pip install -U huggingface_hub" >&2; exit 1; }; "$HF_BIN" download "$repo" "$file" --local-dir "$dest"; }
download "${ORNITH35_REPO:-PocketAiHub/Ornith-1.5-35B-A3B-Abliterated-GGUF}" "${ORNITH35_FILE:-Ornith-1.5-35B-A3B-Abliterated-Q8_0.gguf}" "$MODEL_DIR/ornith-35b"
download "${ORNITH35_REPO:-PocketAiHub/Ornith-1.5-35B-A3B-Abliterated-GGUF}" "${ORNITH35_MMPROJ:-mmproj-Ornith-1.5-35B-Abliterated-F16.gguf}" "$MODEL_DIR/ornith-35b"
download "${ORNITH9_REPO:-mradermacher/Ornith-1.5-9B-uncensored-i1-GGUF}" "${ORNITH9_FILE:-Ornith-1.5-9B-uncensored.i1-Q4_K_M.gguf}" "$MODEL_DIR/ornith-9b"
download "${ORNITH9_REPO:-mradermacher/Ornith-1.5-9B-uncensored-i1-GGUF}" "${ORNITH9_MMPROJ:-mmproj-Ornith-1.5-9B-uncensored.mmproj-f16.gguf}" "$MODEL_DIR/ornith-9b"
download "${QWEN9_REPO:-DavidAU/Qwen3.5-9B-The-Defiant-Fable-Uncensored-Heretic-NEO-IMATRIX-MAX-MTP-GGUF}" "${QWEN9_FILE:-Qwen3.5-9B-The-Defiant-Fable-Uncnr-Heretic-NEO-MAX-MTP-Q4_K_M.gguf}" "$MODEL_DIR/qwen3.5-9b"
download "${QWEN9_REPO:-DavidAU/Qwen3.5-9B-The-Defiant-Fable-Uncensored-Heretic-NEO-IMATRIX-MAX-MTP-GGUF}" "${QWEN9_MMPROJ:-mmproj-BF16.gguf}" "$MODEL_DIR/qwen3.5-9b"
if [[ "$SKIP" == 0 ]]; then
  MODEL_ROOT="$MODEL_DIR" PYTHONPATH="$BASE_DIR" python3 -c 'import os; from proxy.configurations import load_runtime,save_runtime; b=os.environ["PYTHONPATH"]; root=os.environ["MODEL_ROOT"]; r=load_runtime(b); p={"35":(root+"/ornith-35b/Ornith-1.5-35B-A3B-Abliterated-Q8_0.gguf",root+"/ornith-35b/mmproj-Ornith-1.5-35B-A3B-Abliterated-F16.gguf"),"ornith":(root+"/ornith-9b/Ornith-1.5-9B-uncensored.i1-Q4_K_M.gguf",root+"/ornith-9b/mmproj-Ornith-1.5-9B-uncensored.mmproj-f16.gguf"),"qwen":(root+"/qwen3.5-9b/Qwen3.5-9B-The-Defiant-Fable-Uncnr-Heretic-NEO-MAX-MTP-Q4_K_M.gguf",root+"/qwen3.5-9b/mmproj-BF16.gguf")}; [r["services"][x].update(model="ornith-35b",model_path=p["35"][0],mmproj=p["35"][1]) for x in ("arbitro","cerebro","orquestador")]; [r["services"]["sabi"+str(i)].update(model="ornith-9b" if i%2==0 else "qwen3.5-9b",model_path=p["ornith" if i%2==0 else "qwen"][0],mmproj=p["ornith" if i%2==0 else "qwen"][1]) for i in range(12)]; save_runtime(b,r)'
  echo "config.json actualizado con las rutas descargadas"
fi
echo "Modelos preparados en $MODEL_DIR. Usa model_path en config.json para apuntarlos."
