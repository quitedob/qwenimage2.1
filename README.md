# Qwen-Image 2.1 — Local deployment on RTX 5060 Ti 16GB

ComfyUI deployment of **Qwen-Image 2.1** (Alibaba Qwen, released 2026-09-20) using INT8 ConvRot
quantization, running locally on a 16 GB consumer GPU.

**Measured: ~18.5 s per 1024×1024 image (25 steps, warm).** Sampling alone is 15.9 s of that.

Full build narrative, per-stage timings, and every pitfall hit along the way:
**[docs/devlog.md](docs/devlog.md)**

---

## Result

| | |
|---|---|
| Model | Qwen-Image 2.1 — 7B single-stream DiT (32 layers) + Qwen3-VL-8B text encoder + 64ch RGBA VAE |
| Quantization | INT8 ConvRot (diffusion + text encoder), BF16 VAE |
| Runtime | ComfyUI 0.37.0 + torch 2.13.0+cu130 + Python 3.13 |
| GPU | RTX 5060 Ti 16 GB (Blackwell, sm_120) |
| **Warm time** | **18.5 s** (cold 21.7 s) |
| Sampling | 15.9 s — 25 steps @ 1.57 it/s (0.636 s/step) |
| Peak VRAM | 15,806 / 16,311 MiB (97%) |
| Output | 1024×1024 RGBA (native alpha channel via the RGBA VAE) |

---

## Requirements

- **GPU**: Blackwell / Ada, ≥16 GB VRAM. INT8 ConvRot needs Turing or newer.
- **Python**: 3.13 with `torch 2.13.0+cu130` — `sm_120` must be in `torch.cuda.get_arch_list()`
- **Disk**: ~40 GB (4.6 GB runtime + ~17.3 GB weights + working space)

> VRAM headroom is thin. The official template's default text encoder (`qwen3vl_8b_bf16`,
> ~10.6 GiB peak) will **OOM** here when stacked with the INT8 diffusion model. Use the INT8
> or W4A8 encoder. See [Risks](#risks).

---

## Setup

This repo holds **code and docs only** — no binaries or weights.
[`.gitignore`](.gitignore) excludes `python_embeded/`, `venv/`, `models/`, `ComfyUI/`, and `output/`.

### 1. ComfyUI

```bash
cd E:/python/qwenimage
git clone --depth 1 https://github.com/comfyanonymous/ComfyUI.git ComfyUI
```

Requires **0.37.0 or newer** — `QwenImage21` support was added 2026-09-20. Verify:

```bash
grep -c "class QwenImage21" ComfyUI/comfy/supported_models.py   # must be >= 1
```

### 2. Python runtime

Needs Python 3.13 + torch 2.13.0+cu130. Easiest path is a ComfyUI portable build's
`python_embeded/`, or a fresh venv:

```bash
python -m venv python_embeded
python_embeded/Scripts/pip install -r ComfyUI/requirements.txt
# torch must be the cu130 build with sm_120 in its arch list
python_embeded/Scripts/python -c "import torch; print(torch.__version__, torch.cuda.get_arch_list())"
```

Then the 0.37-era packages ComfyUI needs (avoid upgrading torch):

```bash
python_embeded/Scripts/pip install --upgrade --dry-run \
  comfyui-frontend-package==1.53.6 comfyui-workflow-templates==0.11.66 \
  comfyui-embedded-docs==0.5.12 comfy-kitchen==0.2.35 comfy-aimdo==0.5.5
```

### 3. Weights

From [`Comfy-Org/Qwen-Image-2.1`](https://huggingface.co/Comfy-Org/Qwen-Image-2.1):

| Path under `models/` | File | Size |
|---|---|---|
| `diffusion_models/` | `qwen_image_2.1_int8_convrot.safetensors` | 7,256,783,064 |
| `text_encoders/` | `qwen3vl_8b_int8_convrot.safetensors` | 9,350,798,360 |
| `vae/` | `qwen_image_2.1_vae_bf16.safetensors` | 675,509,688 |

Download with the official client, **not** segmented HTTP (bare-HTTP downloads of these
files have produced full-sized artifacts with multi-GB zeroed regions):

```bash
python_embeded/Scripts/hf download Comfy-Org/Qwen-Image-2.1 \
  diffusion_models/qwen_image_2.1_int8_convrot.safetensors \
  text_encoders/qwen3vl_8b_int8_convrot.safetensors \
  vae/qwen_image_2.1_vae_bf16.safetensors \
  --local-dir models
```

Verify every file against HF's published SHA256 before first use.

### 4. Model paths

[`ComfyUI/extra_model_paths.yaml`](ComfyUI/extra_model_paths.yaml) points ComfyUI at this
repo's `models/`:

```yaml
qwenimage:
    base_path: E:/python/qwenimage/models
    diffusion_models: diffusion_models
    text_encoders: text_encoders
    vae: vae
```

---

## Run

```bash
start_comfyui.bat
```

Serves on **port 8199**, not the default 8188 — change it in the batch file if 8188 is free.

Submit [`work/workflow_api.json`](work/workflow_api.json) to `/prompt`:

```bash
curl -X POST http://127.0.0.1:8199/prompt \
  -H "Content-Type: application/json" \
  -d "{\"prompt\": $(cat work/workflow_api.json)}"
```

This matches the official template (`image_qwen_image_2_1_t2i`): steps 25, cfg 1,
euler/simple, negative prompt empty.

---

## Verified

- **SHA256** — all three artifacts `integrity_verified: true` against HF LFS hashes
- **Import** — `QwenImage21` loads; 64-channel latent format
- **Kernels** — `Native ops: ..., convrot_w4a4, int8_tensorwise, ...` (INT8 path active, not a fallback)
- **Output** — 1024×1024 RGBA, full dynamic range; 0/64 tiles flat (rules out the gray/noise failure mode); 100% pixel divergence across prompts (conditioning genuinely drives output)

---

## Risks

- **2K output (2048×2048) is untested and likely to OOM.** Sampling cost roughly quadruples;
  activation VRAM grows too. Current peak is already 97%. Drop the text encoder to
  `qwen3vl_8b_w4a8` or enable CPU offload first.
- **Peak system RAM not measured** (63.8 GB total). The loader shuttles weights between RAM
  and VRAM — watch this when adding models.
- **Do not enable `--use-sage-attention`.** In this GPU family SageAttention silently routes to
  an sm89 kernel via PTX forward-compat and crashes the GPU after several steps. See the H3
  devlog at `F:/python/h3/docs/devlog.md`. Attention optimizations are untested on 2.1.
- The `venv/` in this directory is **broken** (`python.exe` missing; `torch/lib` holds `.lib`
  import libraries rather than runtime DLLs). It is not on the deployment path. Safe to delete.
