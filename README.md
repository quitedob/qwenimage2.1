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

Submit [`work/workflow_api.json`](work/workflow_api.json) through the helper:

```bash
python_embeded/python.exe work/submit_8199.py work/workflow_api.json
```

`submit_8199.py` wraps the graph as `{"prompt": <graph>}`. That wrapper is
required by ComfyUI 0.37: submitting the bare graph returns HTTP 400
`no_prompt`. The helper was exercised by all five Qwen edit tests below.

This matches the official template (`image_qwen_image_2_1_t2i`): steps 25, cfg 1,
euler/simple, negative prompt empty.

### Multi-reference edit

2.1's headline feature: up to **10 reference images** in one pass, no separate
edit model. `TextEncodeQwenImage21` feeds the refs to the Qwen3-VL encoder *and*
splices them into the sequence as VAE latents.

```bash
python_embeded/Scripts/python work/submit_8199.py work/workflow_api_edit.json
python_embeded/Scripts/python work/submit_8199.py work/workflow_api_edit_masked.json
```

| File | What it does |
|---|---|
| `work/workflow_api_edit.json` | 10 permanently-connected **optional** reference slots; each defaults to `[no image]` |
| `work/workflow_api_edit_masked.json` | repaint only a masked region; everything outside is **pixel-exact** |

Both mirror the official `image_qwen_image_2_1_image_edit` template: steps 25,
cfg 1, euler/simple, and the `QwenImage21Cache` node in front of the sampler.

#### Reference-slot fallback: connect all 10, fill only what you use

`work/optional_load_image.py` supplies **Load Image (Optional / Fallback)**.
`register_workflows.py` installs it into the active ComfyUI `custom_nodes/`
directory before registering the workflows; **restart ComfyUI once** if that
copy changed.

Every one of the 10 reference wires stays connected to the Qwen encoder. Its
loader defaults to **`[no image]`**, which returns Python `None` — it is not a
black image, a transparent image, or an empty filename. The native
`TextEncodeQwenImage21` encoder explicitly executes `if image is None:
continue`; consequently that slot adds **no Qwen3-VL vision tokens, no VAE
encode, and no reference latent**. Select/upload an actual image only in the
slots you want; leave every other slot at `[no image]`.

This exact configuration was run successfully on this instance:

| Test | Result |
|---|---|
| 10 connected slots, all `[no image]` | Qwen text-to-image completed in **25.1 s**; 1024² RGBA product-watch output |
| Only **slot 3** = `viking_wolf_rune_axe.png`, other 9 `[no image]` | completed in **30.1 s**; wolf-head rune axe was retained while the prompt changed its setting to snowy mountains |

So `[no image]` is a real per-slot fallback: it is safe to leave all ten wires
connected and choose any subset of images. The current canonical API workflow
has those exact 10 defaults.

**Output size follows the first *actual non-empty* reference image.** If every
slot is `[no image]`, it is pure text-to-image and uses the graph's
`custom_size`/empty-latent route. `resolution` is a *pixel-area* budget (0 =
keep each reference's native size). To change an image-edit output's size,
change the reference image.

> **Keep effective refs to 2–3.** Official PE-I2I results and community testing
> agree that detail starts drifting from the **3rd** reference onward, and
> profile-view hairstyle angles are the first thing to break. Write edge details
> into the prompt rather than padding the ref list.

For the masked workflow: the `LoadImage` mask is painted in the UI. Submitting
over the API, put the painted mask on the node as
`inputs["5"]["mask"] = {"points": [...], "image": "<source>.png"}` — the mask
travels in the node input, not in a file. Only a hard binary mask is supported
(no feathering, no automatic background blending). Drag `GrowMask.expand` up
from its default of 12 if the seam is too visible.

Both UI graphs (multi-reference and masked) are registered by
`work/register_workflows.py` as `qwenimage_edit` / `qwenimage_edit_masked`.

### Validating workflow changes

```bash
python_embeded/python.exe work/validate_workflows.py     # needs the server up
```

Checks every node/input/link against the running server's `/object_info`, then
cross-checks each UI graph against its API file. Worth running after editing a
workflow: a misspelt v3 autogrow key (`image_1` instead of `images.image_1`) is
**silently dropped** by the engine — the run succeeds and quietly ignores the
reference image. Only a GET against `/object_info` catches that, and
`/object_info` is also the only place V3 nodes like `TextEncodeQwenImage21`
appear at all (`nodes.NODE_CLASS_MAPPINGS` does not contain them).

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
