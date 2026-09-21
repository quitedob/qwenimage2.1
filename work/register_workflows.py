"""Register UI-format workflows (flow graphs) into the E: ComfyUI instance.

Five graphs, per user request:
  1. qwenimage             -- Qwen-Image 2.1 text->image
  2. trellis2              -- TRELLIS.2 image->3D (shape + texture)
  3. qwenimage+trellis2    -- both, WITH explicit VRAM-release notes
  4. qwenimage_edit        -- 2.1 native multi-reference edit (up to 10 refs)
  5. qwenimage_edit_masked -- 2.1 masked / region edit (paint, mask file, or box)

Graphs 4 and 5 mirror the official subgraph "Image Edit (Qwen Image 2.1)">
shipped in comfyui-workflow-templates, and are API format compatible with
work/workflow_api_edit.json and work/workflow_api_edit_masked.json.

Writes via POST /userdata/workflows/<name>.json and also drops a filesystem
copy under ComfyUI/user/default/workflows/ as a fallback.

Usage: python register_workflows.py
"""
import json
import os
import shutil
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8199"
ROOT = "E:/python/qwenimage"
WORK_DIR = os.path.dirname(os.path.abspath(__file__))
WF_DIR = os.path.join(ROOT, "ComfyUI", "user", "default", "workflows")
OPTIONAL_NODE_SOURCE = os.path.join(WORK_DIR, "optional_load_image.py")
OPTIONAL_NODE_TARGET = os.path.join(ROOT, "ComfyUI", "custom_nodes", "optional_load_image.py")


def install_optional_image_node():
    """Install the tracked OptionalLoadImage implementation into ComfyUI.

    ComfyUI/custom_nodes/ is intentionally gitignored because this repository
    does not vendor ComfyUI. Keeping the source in work/ makes the fallback
    reproducible; registration copies it into the active ComfyUI instance before
    serialising graphs that require OptionalLoadImage.

    Returns True when the on-disk node changed. A ComfyUI restart is then needed
    before registering/running qwenimage_edit; ComfyUI does not hot-reload
    Python custom nodes.
    """
    if not os.path.isfile(OPTIONAL_NODE_SOURCE):
        raise FileNotFoundError(f"missing fallback node source: {OPTIONAL_NODE_SOURCE}")
    os.makedirs(os.path.dirname(OPTIONAL_NODE_TARGET), exist_ok=True)
    with open(OPTIONAL_NODE_SOURCE, "rb") as f:
        source = f.read()
    try:
        with open(OPTIONAL_NODE_TARGET, "rb") as f:
            changed = f.read() != source
    except FileNotFoundError:
        changed = True
    if changed:
        shutil.copyfile(OPTIONAL_NODE_SOURCE, OPTIONAL_NODE_TARGET)
        print("installed custom node:", OPTIONAL_NODE_TARGET)
        print("  Restart ComfyUI, then run register_workflows.py again.")
    else:
        print("custom node current:", OPTIONAL_NODE_TARGET)
    return changed


def optional_image_node_is_live():
    """Whether the currently running ComfyUI has imported the fallback node."""
    try:
        with urllib.request.urlopen(f"{BASE}/object_info/OptionalLoadImage", timeout=10) as r:
            return "OptionalLoadImage" in json.load(r)
    except (OSError, ValueError):
        return False


def post_userdata(name, payload):
    """Upload one workflow through POST /userdata/{file}.

    `{file}` is a SINGLE aiohttp path segment, so the relative path has to be
    percent-encoded -- "workflows%2Fqwenimage.json", not "workflows/...". Sending
    a literal slash does not reach the route at all and returns 405. The handler
    unquotes the segment, so an encoded path is what it expects.
    """
    url = f"{BASE}/userdata/" + urllib.parse.quote(f"workflows/{name}", safe="")
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read()[:200]
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:400]


class Builder:
    """Turns an API-format node dict into a UI-format graph."""

    def __init__(self):
        self.nodes = []
        self.links = []
        self._id = 0
        self._link = 0
        self._ids = {}

    def add(self, key, class_type, widgets, inputs_spec, outputs_spec,
            pos, size=(280, 110), title=None, note=None):
        """inputs_spec/outputs_spec: list of (name, type) or (name, type, extra).

        `extra` is merged into the input dict; use it to set localized_name /
        shape. For v3 autogrow inputs the `name` must be the DOTTED form
        ("images.image_1"): that is what /object_info advertises and what the
        backend expects as the API prompt key. A bare "image_1" is silently
        dropped by the engine, which is how autogrow inputs get "lost".
        """

        def _spec(s):
            d = {"name": s[0], "type": s[1]}
            if len(s) > 2:
                d.update(s[2])
            return d

        self._id += 1
        nid = self._id
        self._ids[key] = nid
        node = {
            "id": nid, "type": class_type, "pos": list(pos), "size": list(size),
            "flags": {}, "order": len(self.nodes), "mode": 0,
            "inputs": [dict(_spec(s), link=None) for s in inputs_spec],
            "outputs": [{"name": n, "type": t, "links": None, "slot_index": i}
                        for i, (n, t) in enumerate(outputs_spec)],
            "properties": {"Node name for S&R": class_type},
            "widgets_values": widgets,
        }
        if title:
            node["title"] = title
        self.nodes.append(node)
        return nid

    def add_note(self, text, pos, size=(520, 300), color="#432", bgcolor="#653"):
        self._id += 1
        self.nodes.append({
            "id": self._id, "type": "MarkdownNote", "pos": list(pos), "size": list(size),
            "flags": {}, "order": len(self.nodes), "mode": 0,
            "inputs": [], "outputs": [], "title": "说明",
            "properties": {}, "widgets_values": [text],
            "color": color, "bgcolor": bgcolor,
        })
        return self._id

    def link(self, src_key, src_slot, dst_key, dst_slot, type_):
        self._link += 1
        lid = self._link
        s, d = self._ids[src_key], self._ids[dst_key]
        self.links.append([lid, s, src_slot, d, dst_slot, type_])
        for n in self.nodes:
            if n["id"] == s:
                if n["outputs"][src_slot]["links"] is None:
                    n["outputs"][src_slot]["links"] = []
                n["outputs"][src_slot]["links"].append(lid)
            if n["id"] == d:
                n["inputs"][dst_slot]["link"] = lid

    def out(self, name_hint, app=False):
        """Serialise the graph.

        `app=True` ships it in App mode ("Linear mode" in older builds). The
        switch is `extra.linearMode`, a BOOLEAN: the frontend's
        `linearModeToAppMode` maps `true -> "app"`, `false -> "graph"`, and any
        missing/other value -> null, in which case the builder's default view
        applies. Verified against comfyui-frontend-package 1.53.6.

        We deliberately do NOT ship the edit graphs as App: App mode hides the
        graph and shows only widget inputs, so node-level controls the edit
        flows depend on -- `Controller After Generate` on the sampler,
        `GrowMask.expand`, the `custom_size` switch, and the reference image
        slots themselves -- become unreachable. It also exposes every connected
        input as a control, including any unused ones, which is the opposite of
        what "connect only what you need" wants. The graph is the honest
        surface; App mode stays one click away in the view dropdown.
        """
        return {
            "id": name_hint, "revision": 0,
            "last_node_id": self._id, "last_link_id": self._link,
            "nodes": self.nodes, "links": self.links, "groups": [],
            "config": {},
            "extra": {"ds": {"scale": 0.75, "offset": [0, 0]}, "linearMode": app},
            "version": 0.4,
        }


def grid(i, per_row=5):
    return (-60 + (i % per_row) * 330, 60 + (i // per_row) * 190)


def build_trellis2(with_note=True):
    b = Builder()
    if with_note:
        b.add_note(
            "## TRELLIS.2 图生 3D\n\n"
            "**流程**：输入图 → DINOv3 视觉编码 → 结构/形状/高清 三级采样 → 双 VAE 解码 → GLB\n\n"
            "**权重**（均在 `E:/python/qwenimage/models/`）：\n"
            "- `trellis_2_int8_convrot.safetensors` (UNET, 5.25 GB)\n"
            "- `trellis_2_shape_vae_bf16` / `trellis_2_texture_vae_bf16` (VAE)\n"
            "- `dino_v3_vit_l.safetensors` (clip_vision)\n\n"
            "**显存**：本图约需 10 GB。运行前请先释放上一任务的模型：\n"
            "`POST /free {\"unload_models\":true,\"free_memory\":true}`\n\n"
            "**产物**：`output/3d/*.glb`（形状 ~264 MB / 1470 万面；贴图 ~352 MB，逐顶点色 COLOR_0）",
            (-640, 40), (560, 430))

    n = {}
    n['load'] = b.add('load', 'LoadImage', ['viking_wolf_rune_axe.png', 'image'],
                      [], [('IMAGE', 'IMAGE'), ('MASK', 'MASK')], grid(0), title='输入图')
    n['crop'] = b.add('crop', 'ImageCropToMask', [1024, 1024, 1.1, 0, '#000000'],
                      [('images', 'IMAGE'), ('masks', 'MASK')], [('IMAGE', 'IMAGE')], grid(1), title='裁剪到 mask')
    n['clip'] = b.add('clip', 'CLIPVisionLoader', ['dino_v3_vit_l.safetensors'],
                      [], [('CLIP_VISION', 'CLIP_VISION')], grid(2), title='DINOv3 ViT-L')
    n['cond'] = b.add('cond', 'Trellis2Conditioning', None,
                      [('clip_vision_model', 'CLIP_VISION'), ('image', 'IMAGE')],
                      [('positive', 'CONDITIONING'), ('negative', 'CONDITIONING')], grid(3), title='TRELLIS 条件')
    n['unet'] = b.add('unet', 'UNETLoader', ['trellis_2_int8_convrot.safetensors', 'default'],
                      [], [('MODEL', 'MODEL')], grid(4), title='TRELLIS UNET int8')
    n['vaeS'] = b.add('vaeS', 'VAELoader', ['trellis_2_shape_vae_bf16.safetensors'],
                      [], [('VAE', 'VAE')], grid(5), title='Shape VAE')
    n['vaeT'] = b.add('vaeT', 'VAELoader', ['trellis_2_texture_vae_bf16.safetensors'],
                      [], [('VAE', 'VAE')], grid(6), title='Texture VAE')
    n['empty'] = b.add('empty', 'EmptyTrellis2LatentStructure', [1],
                       [], [('LATENT', 'LATENT')], grid(7), title='空潜变量')

    n['cfgA'] = b.add('cfgA', 'CFGOverride', [1.0, 0.667, 1.0], [('model', 'MODEL')],
                      [('MODEL', 'MODEL')], grid(8, 4), title='CFG (结构)')
    n['rsA'] = b.add('rsA', 'RescaleCFG', [0.7], [('model', 'MODEL')],
                     [('MODEL', 'MODEL')], grid(9, 4), title='Rescale (结构)')
    n['msA'] = b.add('msA', 'ModelSamplingSD3', [5.0], [('model', 'MODEL')],
                     [('MODEL', 'MODEL')], grid(10, 4), title='Sampling shift 5')
    n['ks1'] = b.add('ks1', 'KSampler', [56, 'fixed', 12, 7.5, 'euler', 'normal', 1.0],
                     [('model', 'MODEL'), ('positive', 'CONDITIONING'),
                      ('negative', 'CONDITIONING'), ('latent_image', 'LATENT')],
                     [('LATENT', 'LATENT')], grid(11, 4), title='结构 12 步')

    n['strdec'] = b.add('strdec', 'VaeDecodeStructureTrellis2', ['32'],
                        [('samples', 'LATENT'), ('vae', 'VAE')], [('VOXEL', 'VOXEL')],
                        grid(12, 4), title='结构解码')
    n['stage1'] = b.add('stage1', 'Trellis2ShapeStage', None,
                        [('positive', 'CONDITIONING'), ('negative', 'CONDITIONING'),
                         ('voxel', 'VOXEL')],
                        [('positive', 'CONDITIONING'), ('negative', 'CONDITIONING'),
                         ('latent', 'LATENT')], grid(13, 4), title='形状阶段')

    n['cfgB'] = b.add('cfgB', 'CFGOverride', [1.0, 0.769, 1.0], [('model', 'MODEL')],
                      [('MODEL', 'MODEL')], grid(14, 4), title='CFG (形状)')
    n['rsB'] = b.add('rsB', 'RescaleCFG', [0.5], [('model', 'MODEL')],
                     [('MODEL', 'MODEL')], grid(15, 4), title='Rescale (形状)')
    n['ks2'] = b.add('ks2', 'KSampler', [42, 'fixed', 20, 7.5, 'euler', 'normal', 1.0],
                     [('model', 'MODEL'), ('positive', 'CONDITIONING'),
                      ('negative', 'CONDITIONING'), ('latent_image', 'LATENT')],
                     [('LATENT', 'LATENT')], grid(16, 4), title='形状 20 步')
    n['up'] = b.add('up', 'Trellis2UpsampleStage', [1536],
                    [('positive', 'CONDITIONING'), ('negative', 'CONDITIONING'),
                     ('shape_latent', 'LATENT'), ('vae', 'VAE')],
                    [('positive', 'CONDITIONING'), ('negative', 'CONDITIONING'),
                     ('latent', 'LATENT')], grid(17, 4), title='上采样 1536')
    n['ks3'] = b.add('ks3', 'KSampler', [42, 'fixed', 12, 7.5, 'euler', 'simple', 1.0],
                     [('model', 'MODEL'), ('positive', 'CONDITIONING'),
                      ('negative', 'CONDITIONING'), ('latent_image', 'LATENT')],
                     [('LATENT', 'LATENT')], grid(18, 4), title='高清 12 步')
    n['meshdec'] = b.add('meshdec', 'VaeDecodeShapeTrellis', None,
                         [('samples', 'LATENT'), ('vae', 'VAE')],
                         [('MESH', 'MESH'), ('SHAPE_SUBDIVIDES', 'SHAPE_SUBDIVIDES')],
                         grid(19, 4), title='网格解码')
    n['stage2'] = b.add('stage2', 'Trellis2TextureStage', None,
                        [('positive', 'CONDITIONING'), ('negative', 'CONDITIONING'),
                         ('shape_latent', 'LATENT')],
                        [('positive', 'CONDITIONING'), ('negative', 'CONDITIONING'),
                         ('latent', 'LATENT')], grid(20, 4), title='贴图阶段')
    n['ksT'] = b.add('ksT', 'KSampler', [43, 'fixed', 12, 1.0, 'euler', 'normal', 1.0],
                     [('model', 'MODEL'), ('positive', 'CONDITIONING'),
                      ('negative', 'CONDITIONING'), ('latent_image', 'LATENT')],
                     [('LATENT', 'LATENT')], grid(21, 4), title='贴图 12 步 (cfg 1.0)')
    n['texdec'] = b.add('texdec', 'VaeDecodeTextureTrellis', None,
                        [('samples', 'LATENT'), ('vae', 'VAE'),
                         ('shape_subdivides', 'SHAPE_SUBDIVIDES')],
                        [('VOXEL', 'VOXEL')], grid(22, 4), title='贴图解码')
    n['paint'] = b.add('paint', 'PaintMesh', None,
                       [('mesh', 'MESH'), ('voxel_colors', 'VOXEL')],
                       [('MESH', 'MESH')], grid(23, 4), title='上色')
    n['saveShape'] = b.add('saveShape', 'SaveGLB', ['3d/trellis2_shape'],
                           [('mesh', 'MESH')], [], grid(24, 4), title='保存形状 GLB')
    n['saveTex'] = b.add('saveTex', 'SaveGLB', ['3d/trellis2_textured'],
                         [('mesh', 'MESH')], [], (-60 + 4 * 330, 60 + 4 * 190),
                         title='保存贴图 GLB')

    L = [
        ('load', 0, 'crop', 0, 'IMAGE'), ('load', 1, 'crop', 1, 'MASK'),
        ('clip', 0, 'cond', 0, 'CLIP_VISION'), ('crop', 0, 'cond', 1, 'IMAGE'),
        ('unet', 0, 'cfgA', 0, 'MODEL'), ('cfgA', 0, 'rsA', 0, 'MODEL'),
        ('rsA', 0, 'msA', 0, 'MODEL'), ('msA', 0, 'ks1', 0, 'MODEL'),
        ('cond', 0, 'ks1', 1, 'CONDITIONING'), ('cond', 1, 'ks1', 2, 'CONDITIONING'),
        ('empty', 0, 'ks1', 3, 'LATENT'),
        ('ks1', 0, 'strdec', 0, 'LATENT'), ('vaeS', 0, 'strdec', 1, 'VAE'),
        ('strdec', 0, 'stage1', 2, 'VOXEL'),
        ('cond', 0, 'stage1', 0, 'CONDITIONING'), ('cond', 1, 'stage1', 1, 'CONDITIONING'),
        ('unet', 0, 'cfgB', 0, 'MODEL'), ('cfgB', 0, 'rsB', 0, 'MODEL'),
        ('rsB', 0, 'ks2', 0, 'MODEL'),
        ('stage1', 0, 'ks2', 1, 'CONDITIONING'), ('stage1', 1, 'ks2', 2, 'CONDITIONING'),
        ('stage1', 2, 'ks2', 3, 'LATENT'),
        ('stage1', 0, 'up', 0, 'CONDITIONING'), ('stage1', 1, 'up', 1, 'CONDITIONING'),
        ('ks2', 0, 'up', 2, 'LATENT'), ('vaeS', 0, 'up', 3, 'VAE'),
        ('rsB', 0, 'ks3', 0, 'MODEL'),
        ('up', 0, 'ks3', 1, 'CONDITIONING'), ('up', 1, 'ks3', 2, 'CONDITIONING'),
        ('up', 2, 'ks3', 3, 'LATENT'),
        ('ks3', 0, 'meshdec', 0, 'LATENT'), ('vaeS', 0, 'meshdec', 1, 'VAE'),
        ('meshdec', 0, 'saveShape', 0, 'MESH'),
        ('up', 0, 'stage2', 0, 'CONDITIONING'), ('up', 1, 'stage2', 1, 'CONDITIONING'),
        ('ks3', 0, 'stage2', 2, 'LATENT'),
        ('unet', 0, 'ksT', 0, 'MODEL'),
        ('stage2', 0, 'ksT', 1, 'CONDITIONING'), ('stage2', 1, 'ksT', 2, 'CONDITIONING'),
        ('stage2', 2, 'ksT', 3, 'LATENT'),
        ('ksT', 0, 'texdec', 0, 'LATENT'), ('vaeT', 0, 'texdec', 1, 'VAE'),
        ('meshdec', 1, 'texdec', 2, 'SHAPE_SUBDIVIDES'),
        ('meshdec', 0, 'paint', 0, 'MESH'), ('texdec', 0, 'paint', 1, 'VOXEL'),
        ('paint', 0, 'saveTex', 0, 'MESH'),
    ]
    for s, ss, d, ds, t in L:
        b.link(s, ss, d, ds, t)
    return b.out("trellis2")


def build_qwen():
    b = Builder()
    b.add_note(
        "## Qwen-Image 2.1 文生图\n\n"
        "**权重**（`E:/python/qwenimage/models/`）：\n"
        "- `qwen_image_2.1_int8_convrot.safetensors` (UNET, 7.26 GB)\n"
        "- `qwen3vl_8b_int8_convrot.safetensors` (TE, 9.35 GB)\n"
        "- `qwen_image_2.1_vae_bf16.safetensors` (VAE)\n\n"
        "**显存**：单独跑峰值约 **15.8 / 16.3 GB (97%)**——本卡几乎占满。\n"
        "与 TRELLIS.2 切换前必须释放模型：\n"
        "`POST /free {\"unload_models\":true,\"free_memory\":true}`",
        (-640, 40), (560, 330))
    b.add('unet', 'UNETLoader', ['qwen_image_2.1_int8_convrot.safetensors', 'default'],
          [], [('MODEL', 'MODEL')], grid(0), title='Qwen UNET int8')
    b.add('clip', 'CLIPLoader', ['qwen3vl_8b_int8_convrot.safetensors', 'qwen_image'],
          [], [('CLIP', 'CLIP')], grid(1), title='Qwen TE')
    b.add('vae', 'VAELoader', ['qwen_image_2.1_vae_bf16.safetensors'],
          [], [('VAE', 'VAE')], grid(2), title='Qwen VAE')
    b.add('pos', 'CLIPTextEncode', ['a photo of a cat'], [('clip', 'CLIP')],
          [('CONDITIONING', 'CONDITIONING')], grid(3), title='正向提示词')
    b.add('neg', 'CLIPTextEncode', [''], [('clip', 'CLIP')],
          [('CONDITIONING', 'CONDITIONING')], grid(4), title='负向提示词')
    b.add('lat', 'EmptyLatentImage', [1024, 1024, 1], [], [('LATENT', 'LATENT')],
          grid(5), title='空潜变量')
    b.add('ks', 'KSampler', [0, 'fixed', 20, 2.5, 'euler', 'normal', 1.0],
          [('model', 'MODEL'), ('positive', 'CONDITIONING'), ('negative', 'CONDITIONING'),
           ('latent_image', 'LATENT')], [('LATENT', 'LATENT')], grid(6), title='采样')
    b.add('dec', 'VAEDecode', None, [('samples', 'LATENT'), ('vae', 'VAE')],
          [('IMAGE', 'IMAGE')], grid(7), title='VAE 解码')
    b.add('save', 'SaveImage', ['qwen21'], [('images', 'IMAGE')], [], grid(8), title='保存图像')
    for s, ss, d, ds, t in [
        ('unet', 0, 'ks', 0, 'MODEL'), ('clip', 0, 'pos', 0, 'CLIP'),
        ('clip', 0, 'neg', 0, 'CLIP'),
        ('pos', 0, 'ks', 1, 'CONDITIONING'), ('neg', 0, 'ks', 2, 'CONDITIONING'),
        ('lat', 0, 'ks', 3, 'LATENT'), ('ks', 0, 'dec', 0, 'LATENT'),
        ('vae', 0, 'dec', 1, 'VAE'), ('dec', 0, 'save', 0, 'IMAGE'),
    ]:
        b.link(s, ss, d, ds, t)
    return b.out("qwenimage")


def build_combined():
    b = Builder()
    b.add_note(
        "## Qwen-Image 2.1 + TRELLIS.2 组合流程\n\n"
        "### ⚠️ 显存卸载（本图最关键的一步）\n"
        "**本卡 16 GB，两个模型的显存需求互斥，无法同时常驻：**\n"
        "- Qwen-Image 2.1 单独跑峰值 **15,806 / 16,311 MiB（97%）**\n"
        "- TRELLIS.2 约需 **10 GB**\n\n"
        "**实测结论**：ComfyUI **不会**在两次任务之间自动卸载模型\n"
        "（连跑 4 次后日志仍是 `0 models unloaded`，12.5 GB 一直驻留）。\n"
        "所以**每次切换流程前必须显式释放**：\n\n"
        "```\n"
        "POST http://127.0.0.1:8199/free\n"
        "Content-Type: application/json\n\n"
        '{"unload_models":true,"free_memory":true}\n'
        "```\n\n"
        "实测效果：**14,283 MiB → 2,013 MiB**（释放 12.3 GB）。\n\n"
        "**注意**：`/system_stats` 报的可用显存会**高报约 1.2 GB**，\n"
        "判断是否够用请以 `nvidia-smi` 为准。\n\n"
        "### 使用顺序\n"
        "1. 本图上半部分（Qwen 分支）出图\n"
        "2. `POST /free` 释放\n"
        "3. 本图下半部分（TRELLIS 分支）出 3D\n"
        "4. 要再出图 → 再 `POST /free`\n\n"
        "两组分支**不要同时执行**（用 ComfyUI 的节点静音 `Ctrl+M` 切换）。",
        (-660, 40), (600, 620), color="#432", bgcolor="#653")

    # --- Qwen 分支 (左侧) ---
    b.add('q_unet', 'UNETLoader', ['qwen_image_2.1_int8_convrot.safetensors', 'default'],
          [], [('MODEL', 'MODEL')], (-40, 720), title='[Qwen] UNET')
    b.add('q_clip', 'CLIPLoader', ['qwen3vl_8b_int8_convrot.safetensors', 'qwen_image'],
          [], [('CLIP', 'CLIP')], (290, 720), title='[Qwen] TE')
    b.add('q_vae', 'VAELoader', ['qwen_image_2.1_vae_bf16.safetensors'],
          [], [('VAE', 'VAE')], (620, 720), title='[Qwen] VAE')
    b.add('q_pos', 'CLIPTextEncode', ['a photo of a cat'], [('clip', 'CLIP')],
          [('CONDITIONING', 'CONDITIONING')], (-40, 930), title='[Qwen] 正向')
    b.add('q_neg', 'CLIPTextEncode', [''], [('clip', 'CLIP')],
          [('CONDITIONING', 'CONDITIONING')], (290, 930), title='[Qwen] 负向')
    b.add('q_lat', 'EmptyLatentImage', [1024, 1024, 1], [], [('LATENT', 'LATENT')],
          (620, 930), title='[Qwen] 空潜变量')
    b.add('q_ks', 'KSampler', [0, 'fixed', 20, 2.5, 'euler', 'normal', 1.0],
          [('model', 'MODEL'), ('positive', 'CONDITIONING'), ('negative', 'CONDITIONING'),
           ('latent_image', 'LATENT')], [('LATENT', 'LATENT')], (-40, 1140), title='[Qwen] 采样')
    b.add('q_dec', 'VAEDecode', None, [('samples', 'LATENT'), ('vae', 'VAE')],
          [('IMAGE', 'IMAGE')], (290, 1140), title='[Qwen] 解码')
    b.add('q_save', 'SaveImage', ['qwen21'], [('images', 'IMAGE')], [], (620, 1140),
          title='[Qwen] 保存')
    for s, ss, d, ds, t in [
        ('q_unet', 0, 'q_ks', 0, 'MODEL'), ('q_clip', 0, 'q_pos', 0, 'CLIP'),
        ('q_clip', 0, 'q_neg', 0, 'CLIP'),
        ('q_pos', 0, 'q_ks', 1, 'CONDITIONING'), ('q_neg', 0, 'q_ks', 2, 'CONDITIONING'),
        ('q_lat', 0, 'q_ks', 3, 'LATENT'), ('q_ks', 0, 'q_dec', 0, 'LATENT'),
        ('q_vae', 0, 'q_dec', 1, 'VAE'), ('q_dec', 0, 'q_save', 0, 'IMAGE'),
    ]:
        b.link(s, ss, d, ds, t)

    # --- TRELLIS 分支 (右侧) ---
    b.add('t_load', 'LoadImage', ['viking_wolf_rune_axe.png', 'image'],
          [], [('IMAGE', 'IMAGE'), ('MASK', 'MASK')], (990, 720), title='[TRELLIS] 输入图')
    b.add('t_crop', 'ImageCropToMask', [1024, 1024, 1.1, 0, '#000000'],
          [('images', 'IMAGE'), ('masks', 'MASK')], [('IMAGE', 'IMAGE')], (1320, 720),
          title='[TRELLIS] 裁剪')
    b.add('t_clipv', 'CLIPVisionLoader', ['dino_v3_vit_l.safetensors'],
          [], [('CLIP_VISION', 'CLIP_VISION')], (1650, 720), title='[TRELLIS] DINOv3')
    b.add('t_cond', 'Trellis2Conditioning', None,
          [('clip_vision_model', 'CLIP_VISION'), ('image', 'IMAGE')],
          [('positive', 'CONDITIONING'), ('negative', 'CONDITIONING')], (990, 930),
          title='[TRELLIS] 条件')
    b.add('t_unet', 'UNETLoader', ['trellis_2_int8_convrot.safetensors', 'default'],
          [], [('MODEL', 'MODEL')], (1320, 930), title='[TRELLIS] UNET')
    b.add('t_vaeS', 'VAELoader', ['trellis_2_shape_vae_bf16.safetensors'],
          [], [('VAE', 'VAE')], (1650, 930), title='[TRELLIS] Shape VAE')
    b.add('t_vaeT', 'VAELoader', ['trellis_2_texture_vae_bf16.safetensors'],
          [], [('VAE', 'VAE')], (1980, 930), title='[TRELLIS] Texture VAE')
    b.add('t_empty', 'EmptyTrellis2LatentStructure', [1], [], [('LATENT', 'LATENT')],
          (990, 1140), title='[TRELLIS] 空潜变量')
    b.add('t_msA', 'ModelSamplingSD3', [5.0], [('model', 'MODEL')], [('MODEL', 'MODEL')],
          (1320, 1140), title='[TRELLIS] shift 5')
    b.add('t_ks1', 'KSampler', [56, 'fixed', 12, 7.5, 'euler', 'normal', 1.0],
          [('model', 'MODEL'), ('positive', 'CONDITIONING'), ('negative', 'CONDITIONING'),
           ('latent_image', 'LATENT')], [('LATENT', 'LATENT')], (1650, 1140),
          title='[TRELLIS] 结构 12 步')
    b.add('t_strdec', 'VaeDecodeStructureTrellis2', ['32'],
          [('samples', 'LATENT'), ('vae', 'VAE')], [('VOXEL', 'VOXEL')], (1980, 1140),
          title='[TRELLIS] 结构解码')
    b.add('t_stage1', 'Trellis2ShapeStage', None,
          [('positive', 'CONDITIONING'), ('negative', 'CONDITIONING'), ('voxel', 'VOXEL')],
          [('positive', 'CONDITIONING'), ('negative', 'CONDITIONING'), ('latent', 'LATENT')],
          (990, 1350), title='[TRELLIS] 形状阶段')
    b.add('t_ks2', 'KSampler', [42, 'fixed', 20, 7.5, 'euler', 'normal', 1.0],
          [('model', 'MODEL'), ('positive', 'CONDITIONING'), ('negative', 'CONDITIONING'),
           ('latent_image', 'LATENT')], [('LATENT', 'LATENT')], (1320, 1350),
          title='[TRELLIS] 形状 20 步')
    b.add('t_up', 'Trellis2UpsampleStage', [1536],
          [('positive', 'CONDITIONING'), ('negative', 'CONDITIONING'),
           ('shape_latent', 'LATENT'), ('vae', 'VAE')],
          [('positive', 'CONDITIONING'), ('negative', 'CONDITIONING'), ('latent', 'LATENT')],
          (1650, 1350), title='[TRELLIS] 上采样')
    b.add('t_ks3', 'KSampler', [42, 'fixed', 12, 7.5, 'euler', 'simple', 1.0],
          [('model', 'MODEL'), ('positive', 'CONDITIONING'), ('negative', 'CONDITIONING'),
           ('latent_image', 'LATENT')], [('LATENT', 'LATENT')], (1980, 1350),
          title='[TRELLIS] 高清 12 步')
    b.add('t_meshdec', 'VaeDecodeShapeTrellis', None,
          [('samples', 'LATENT'), ('vae', 'VAE')],
          [('MESH', 'MESH'), ('SHAPE_SUBDIVIDES', 'SHAPE_SUBDIVIDES')], (990, 1560),
          title='[TRELLIS] 网格解码')
    b.add('t_saveShape', 'SaveGLB', ['3d/trellis2_shape'], [('mesh', 'MESH')], [],
          (1320, 1560), title='[TRELLIS] 存 GLB')
    b.add('t_stage2', 'Trellis2TextureStage', None,
          [('positive', 'CONDITIONING'), ('negative', 'CONDITIONING'),
           ('shape_latent', 'LATENT')],
          [('positive', 'CONDITIONING'), ('negative', 'CONDITIONING'), ('latent', 'LATENT')],
          (1650, 1560), title='[TRELLIS] 贴图阶段')
    b.add('t_ksT', 'KSampler', [43, 'fixed', 12, 1.0, 'euler', 'normal', 1.0],
          [('model', 'MODEL'), ('positive', 'CONDITIONING'), ('negative', 'CONDITIONING'),
           ('latent_image', 'LATENT')], [('LATENT', 'LATENT')], (1980, 1560),
          title='[TRELLIS] 贴图 12 步')
    b.add('t_texdec', 'VaeDecodeTextureTrellis', None,
          [('samples', 'LATENT'), ('vae', 'VAE'), ('shape_subdivides', 'SHAPE_SUBDIVIDES')],
          [('VOXEL', 'VOXEL')], (990, 1770), title='[TRELLIS] 贴图解码')
    b.add('t_paint', 'PaintMesh', None,
          [('mesh', 'MESH'), ('voxel_colors', 'VOXEL')], [('MESH', 'MESH')], (1320, 1770),
          title='[TRELLIS] 上色')
    b.add('t_saveTex', 'SaveGLB', ['3d/trellis2_textured'], [('mesh', 'MESH')], [],
          (1650, 1770), title='[TRELLIS] 存贴图 GLB')
    for s, ss, d, ds, t in [
        ('t_load', 0, 't_crop', 0, 'IMAGE'), ('t_load', 1, 't_crop', 1, 'MASK'),
        ('t_clipv', 0, 't_cond', 0, 'CLIP_VISION'), ('t_crop', 0, 't_cond', 1, 'IMAGE'),
        ('t_unet', 0, 't_msA', 0, 'MODEL'),
        ('t_msA', 0, 't_ks1', 0, 'MODEL'),
        ('t_cond', 0, 't_ks1', 1, 'CONDITIONING'), ('t_cond', 1, 't_ks1', 2, 'CONDITIONING'),
        ('t_empty', 0, 't_ks1', 3, 'LATENT'),
        ('t_ks1', 0, 't_strdec', 0, 'LATENT'), ('t_vaeS', 0, 't_strdec', 1, 'VAE'),
        ('t_strdec', 0, 't_stage1', 2, 'VOXEL'),
        ('t_cond', 0, 't_stage1', 0, 'CONDITIONING'),
        ('t_cond', 1, 't_stage1', 1, 'CONDITIONING'),
        ('t_unet', 0, 't_ks2', 0, 'MODEL'),
        ('t_stage1', 0, 't_ks2', 1, 'CONDITIONING'),
        ('t_stage1', 1, 't_ks2', 2, 'CONDITIONING'),
        ('t_stage1', 2, 't_ks2', 3, 'LATENT'),
        ('t_stage1', 0, 't_up', 0, 'CONDITIONING'),
        ('t_stage1', 1, 't_up', 1, 'CONDITIONING'),
        ('t_ks2', 0, 't_up', 2, 'LATENT'), ('t_vaeS', 0, 't_up', 3, 'VAE'),
        ('t_unet', 0, 't_ks3', 0, 'MODEL'),
        ('t_up', 0, 't_ks3', 1, 'CONDITIONING'), ('t_up', 1, 't_ks3', 2, 'CONDITIONING'),
        ('t_up', 2, 't_ks3', 3, 'LATENT'),
        ('t_ks3', 0, 't_meshdec', 0, 'LATENT'), ('t_vaeS', 0, 't_meshdec', 1, 'VAE'),
        ('t_meshdec', 0, 't_saveShape', 0, 'MESH'),
        ('t_up', 0, 't_stage2', 0, 'CONDITIONING'),
        ('t_up', 1, 't_stage2', 1, 'CONDITIONING'),
        ('t_ks3', 0, 't_stage2', 2, 'LATENT'),
        ('t_unet', 0, 't_ksT', 0, 'MODEL'),
        ('t_stage2', 0, 't_ksT', 1, 'CONDITIONING'),
        ('t_stage2', 1, 't_ksT', 2, 'CONDITIONING'),
        ('t_stage2', 2, 't_ksT', 3, 'LATENT'),
        ('t_ksT', 0, 't_texdec', 0, 'LATENT'), ('t_vaeT', 0, 't_texdec', 1, 'VAE'),
        ('t_meshdec', 1, 't_texdec', 2, 'SHAPE_SUBDIVIDES'),
        ('t_meshdec', 0, 't_paint', 0, 'MESH'), ('t_texdec', 0, 't_paint', 1, 'VOXEL'),
        ('t_paint', 0, 't_saveTex', 0, 'MESH'),
    ]:
        b.link(s, ss, d, ds, t)
    return b.out("qwenimage_trellis2")


def build_edit():
    """Native multi-reference edit -- mirrors the official 2.1 edit subgraph.

    The official subgraph is (node ids from the shipped template):
      453 CLIP -> 474.clip
      454 VAE  -> 474.vae, 457.vae
      451 UNET -> 469 (QwenImage21Cache) -> 458.model
      474 -> 458.positive/.negative, 468.on_false(latent)
      456 EmptyLatent -> 468.on_true, 468.switch=False -> 458.latent_image
    i.e. width/height only apply when custom_size is switched on; by default the
    latent comes from TextEncodeQwenImage21 so the output size matches ref #1.
    Hosting 'switch' on ComfySwitchNode requires connecting on_true, so we keep
    the official shape. The switch is exposed via PrimitiveBoolean so it can be
    toggled from the UI; note the API workflow has no such switch and sizes the
    latent from ref #1 instead (identical default behaviour).

    image_1..image_10 are all OPTIONAL (the autogrow template has min=0), so a
    graph may wire as few as zero of them -- zero refs is plain text-to-image.

    This graph deliberately wires EVERY slot, but uses OptionalLoadImage rather
    than stock LoadImage. Its default `[no image]` returns None, and the Qwen
    encoder's own loop does `if image is None: continue`, so an empty connected
    slot contributes no vision tokens and no VAE reference latent. This is a
    genuine fallback, not a black/transparent placeholder image.
    """
    b = Builder()
    b.add_note(
        "## Qwen-Image 2.1 参考图编辑（原生多图）\n\n"
        "**这是 2.1 相对旧版最大的升级**：最多 **10 张参考图**，不需要另外跑编辑专用模型。\n"
        "中文 / 日文 / 英文参考图可以混着用，模型直接读。\n\n"
        "### 10 个槽位都连好了，空着也真的不输入\n"
        "每个 `参考图 N` 都是 **Load Image (Optional / Fallback)**，默认值为 **`[no image]`**。\n"
        "它不是黑图、透明图，也不是空文件名：它输出 Python `None`。\n"
        "Qwen 2.1 编码器源码会执行 `if image is None: continue`，因此空槽位：\n"
        "- **不进入 Qwen3-VL 视觉序列**\n"
        "- **不经 VAE 编码**\n"
        "- **不产生 reference latent**\n\n"
        "所以你只要在要用的任意槽位选择/上传真图，其余保持 `[no image]` 就行。\n"
        "可以只填 1 张、2 张、3 张，或者 10 张；**所有 10 条线不必手工断开。**\n\n"
        "⚠️ **参考图 #1 建议优先填。** 有参考图时输出尺寸跟随第一个实际非空参考图；\n"
        "全空时走纯文生图，`custom_size` 开关控制输出尺寸。\n\n"
        "⚠️ **别拿 `example.png` 当测试图。** 它是 ComfyUI 自带的测试资源；\n"
        "请上传实际要编辑的真图。\n\n"
        "### 视图\n"
        "默认保留图模式，便于看清十个槽位和 `custom_size` 等控制；\n"
        "右上角视图下拉可自行切到 **App 模式**（旧名 Linear mode）。\n\n"
        "### ⚠️ 参考图不是越多越好\n"
        "官方 PE-I2I 结果和社区实测都指向同一件事——**图给多了、文字描述一长，参考图的细节就丢**：\n"
        "- 从**第 3 张**参考图开始，一致性明显下滑\n"
        "- **侧视角的发型角度**最容易崩\n\n"
        "**实用建议：有效参考图控制在 2~3 张以内。**\n"
        "边角信息（配饰、材质、背景细节）写成**文字**塞进 prompt，\n"
        "比硬凑满 10 张图效果好。\n\n"
        "### 尺寸（重要）\n"
        "**输出尺寸 = 第 1 张参考图的尺寸**（resolution=1024 时按面积缩放到 1024²，保持长宽比）。\n"
        "`resolution` 是**面积预算**，不是边长；`0` = 保持参考图原始尺寸。\n\n"
        "换尺寸的正确做法：**把参考图本身换成目标尺寸**，不要指望通过改 latent 来换。\n\n"
        "### 参数\n"
        "对齐官方 `image_qwen_image_2_1_image_edit` 模板：steps **25**、cfg **1**、euler + simple。\n"
        "cfg=1 = 负向提示词**不生效**（负向分支仍是空串，照官方保持）。\n\n"
        "### 显存\n"
        "与文生图同为 **~15.8 / 16.3 GB（97%）**。每张参考图都要过 VAE 编码并拼进序列，\n"
        "**参考图越多越吃显存**——这也是控制在 2~3 张的另一个理由。\n"
        "切流程前记得 `POST /free {\"unload_models\":true,\"free_memory\":true}`。\n\n"
        "### <image1> <image2> 编号（施工中，未实测）\n"
        "官方模板的 prompt 里用 `<image1>`、`<image2>` 指代参考图，所以照抄了这个写法。\n"
        "但从本地 `qwen_image21.py` 看，**模型自己拼的引用块是 0-based**——\n"
        "灌进 prompt 的是 `<image{}>` 按 `range(len(images))` 循环 + 一个末尾 `Picture 1:`，\n"
        "也就是说 `<image1>` 对应的是**第 2 张**参考图。\n\n"
        "**所以别依赖这个编号。** 稳妥的用法：参考图控制在 2~3 张，\n"
        "prompt 里直接按顺序描述（\"第 1 张的人物 / 第 2 张的衣服\"），\n"
        "或者显式写 `Picture 1:`、`Picture 2:` 再跟描述。\n"
        "**编号这块我还没实测，第一次用请先拿 2 张图验证一下再上生产。**",
        (-720, 40), (620, 620))
    b.add('unet', 'UNETLoader', ['qwen_image_2.1_int8_convrot.safetensors', 'default'],
          [], [('MODEL', 'MODEL')], grid(0), title='Qwen UNET int8')
    b.add('clip', 'CLIPLoader', ['qwen3vl_8b_int8_convrot.safetensors', 'qwen_image', 'default'],
          [], [('CLIP', 'CLIP')], grid(1), title='Qwen TE')
    b.add('vae', 'VAELoader', ['qwen_image_2.1_vae_bf16.safetensors'],
          [], [('VAE', 'VAE')], grid(2), title='Qwen VAE')
    b.add('te', 'TextEncodeQwenImage21',
          ['Keep the character and pose in <image1> unchanged, put the light blue denim '
           'shirt from <image2> on the character, keep the background and lighting of <image1>',
           '', 1024],
          [('clip', 'CLIP'), ('vae', 'VAE')] +
          [(f'images.image_{i}', 'IMAGE',
            {'localized_name': f'image_{i}', 'shape': 7}) for i in range(1, 11)],
          [('positive', 'CONDITIONING'), ('negative', 'CONDITIONING'), ('latent', 'LATENT')],
          (990, 40), size=(320, 300), title='Qwen3VL 多参考图编码')
    # All ten slots are permanently wired. `[no image]` from OptionalLoadImage
    # yields None, which TextEncodeQwenImage21 explicitly skips. Therefore an
    # empty slot is absent from conditioning rather than an invalid LoadImage.
    for i in range(10):
        b.add(f'ref{i + 1}', 'OptionalLoadImage', ['[no image]', 'image'],
              [], [('IMAGE', 'IMAGE'), ('MASK', 'MASK')],
              (990 + (i % 2) * 330, 400 + (i // 2) * 180),
              title=f'参考图 {i + 1}（可空）')
    b.add('empty', 'EmptyLatentImage', [1024, 1024, 1], [], [('LATENT', 'LATENT')],
          (990, 300), title='空潜变量 (custom_size 用)')
    b.add('sw', 'ComfySwitchNode', [False],
          [('on_false', 'LATENT'), ('on_true', 'LATENT'), ('switch', 'BOOLEAN')],
          [('LATENT', 'LATENT')], (1320, 300), title='尺寸开关 (custom_size)')
    b.add('swv', 'PrimitiveBoolean', [False], [], [('BOOLEAN', 'BOOLEAN')],
          (990, 450), title='custom_size 值')
    b.add('cache', 'QwenImage21Cache', ['auto', 'default'], [('model', 'MODEL')],
          [('MODEL', 'MODEL')], (1650, 300), title='KV cache')
    b.add('ks', 'KSampler', [42, 'fixed', 25, 1.0, 'euler', 'simple', 1.0],
          [('model', 'MODEL'), ('positive', 'CONDITIONING'), ('negative', 'CONDITIONING'),
           ('latent_image', 'LATENT')], [('LATENT', 'LATENT')], (1980, 300), title='采样 25 步')
    b.add('dec', 'VAEDecode', None, [('samples', 'LATENT'), ('vae', 'VAE')],
          [('IMAGE', 'IMAGE')], (1980, 550), title='VAE 解码')
    b.add('save', 'SaveImage', ['qwen21_edit'], [('images', 'IMAGE')], [], (1980, 700),
          title='保存图像')

    L = [
        ('clip', 0, 'te', 0, 'CLIP'), ('vae', 0, 'te', 1, 'VAE'),
        ('unet', 0, 'cache', 0, 'MODEL'), ('cache', 0, 'ks', 0, 'MODEL'),
        ('te', 0, 'ks', 1, 'CONDITIONING'), ('te', 1, 'ks', 2, 'CONDITIONING'),
        ('te', 2, 'sw', 0, 'LATENT'), ('empty', 0, 'sw', 1, 'LATENT'),
        ('swv', 0, 'sw', 2, 'BOOLEAN'), ('sw', 0, 'ks', 3, 'LATENT'),
        ('ks', 0, 'dec', 0, 'LATENT'), ('vae', 0, 'dec', 1, 'VAE'),
        ('dec', 0, 'save', 0, 'IMAGE'),
    ]
    # All ten reference slots are connected. `[no image]` becomes None on the
    # edge; TextEncodeQwenImage21 skips it before VLM/VAE processing.
    for i in range(10):
        L.append((f'ref{i + 1}', 0, 'te', 2 + i, 'IMAGE'))
    for s, ss, d, ds, t in L:
        b.link(s, ss, d, ds, t)
    return b.out("qwenimage_edit")


def build_edit_masked():
    """Masked / region edit -- paint-to-edit, explicit mask file, or a box mask.

    Mirrors the inpainting blueprint's mask path (LoadImage mask -> GrowMask ->
    VAEEncode -> SetLatentNoiseMask) but keeps 2.1's native reference encoder.
    Only the masked region is re-diffused; the rest comes back from the source
    pixels, so untouched areas are pixel-exact rather than merely similar.
    """
    b = Builder()
    b.add_note(
        "## Qwen-Image 2.1 局部编辑（涂鸦 / 蒙版 / 圈选）\n\n"
        "**改哪里由 mask 决定，不靠 prompt 猜。** 只有 mask 内的区域会重新扩散，\n"
        "mask 外**逐像素还原**（不是\"尽量保持一致\"，是直接贴回去）。\n\n"
        "### 三种给 mask 的方式（按需选一个）\n"
        "1. **涂鸦** — 直接在 LoadImage 的图上画（右键 → MaskEditor）。**默认走这条**。\n"
        "   API 提交时把绘制的 mask 写进节点输入的\n"
        "   `inputs[\"12\"][\"mask\"] = {\"points\": [...], \"image\": \"<src>.png\"}`。\n"
        "2. **蒙版文件** — 把 `LoadImageMask`(节点 30) 的 mask 接到后面 3 个节点，\n"
        "   再把 `channel` 设成 `alpha` / `red` / `green` / `blue`。\n"
        "3. **圈选边角信息** — 想精确框住某个局部，用 `ImageCrop` 切出那块单独精修，\n"
        "   比在整图上画框容易对准。\n\n"
        "⚠️ **方式 1 和 2 是二选一**，同时接会打架——建议先静音（`Ctrl+M`）没用的那路。\n\n"
        "### 参数\n"
        "`denoise` 控制改动力度：**1.0** = 完全重画 mask 内（换物体）；\n"
        "**0.6~0.8** = 保结构、改质感（推荐起点）。\n\n"
        "### 限制（必读）\n"
        "- **只支持硬边二值 mask**。所谓\"蒙版\"就是 0/1，没有软边羽化，\n"
        "  也不会自动融合背景——这是它做不到的部分，别期待它像 PS 那样无缝。\n"
        "- 想减少边界反差可以把 **GrowMask.expand 调大**（默认 12），或对 mask 做模糊。\n"
        "- **尺寸必须 = 参考图尺寸**，所以 latent 由 `VAEEncode(参考图)` 给出，\n"
        "  这里**没有** EmptyLatentImage 那条路可选。换尺寸先换参考图。\n\n"
        "### 显存\n"
        "同样 **~15.8 / 16.3 GB（97%）**。切流程前 `POST /free`。",
        (-720, 40), (620, 560))
    b.add('unet', 'UNETLoader', ['qwen_image_2.1_int8_convrot.safetensors', 'default'],
          [], [('MODEL', 'MODEL')], grid(0), title='Qwen UNET int8')
    b.add('clip', 'CLIPLoader', ['qwen3vl_8b_int8_convrot.safetensors', 'qwen_image', 'default'],
          [], [('CLIP', 'CLIP')], grid(1), title='Qwen TE')
    b.add('vae', 'VAELoader', ['qwen_image_2.1_vae_bf16.safetensors'],
          [], [('VAE', 'VAE')], grid(2), title='Qwen VAE')
    b.add('src', 'LoadImage', ['example.png', 'image'],
          [], [('IMAGE', 'IMAGE'), ('MASK', 'MASK')], grid(3), title='参考图 / 涂抹')
    b.add('maskimg', 'LoadImageMask', ['example.png', 'alpha'],
          [], [('MASK', 'MASK')], grid(4), title='蒙版文件 (换成方式 2)')
    b.add('grow', 'GrowMask', [12, True], [('mask', 'MASK')], [('MASK', 'MASK')],
          (990, 240), title='蒙版外扩')
    b.add('enc', 'VAEEncode', None, [('pixels', 'IMAGE'), ('vae', 'VAE')],
          [('LATENT', 'LATENT')], (1320, 240), title='参考图编码')
    b.add('snm', 'SetLatentNoiseMask', None, [('samples', 'LATENT'), ('mask', 'MASK')],
          [('LATENT', 'LATENT')], (1650, 240), title='只给蒙版区加噪')
    b.add('te', 'TextEncodeQwenImage21',
          ['Replace the masked area with a matte black ceramic vase, keep the lighting '
           'and reflections of the surroundings', '', 1024],
          [('clip', 'CLIP'), ('vae', 'VAE'),
           ('images.image_1', 'IMAGE', {'localized_name': 'image_1', 'shape': 7})],
          [('positive', 'CONDITIONING'), ('negative', 'CONDITIONING'), ('latent', 'LATENT')],
          (990, 430), title='编辑提示词')
    b.add('cache', 'QwenImage21Cache', ['auto', 'default'], [('model', 'MODEL')],
          [('MODEL', 'MODEL')], (1980, 240), title='KV cache')
    b.add('ks', 'KSampler', [42, 'fixed', 25, 1.0, 'euler', 'simple', 0.8],
          [('model', 'MODEL'), ('positive', 'CONDITIONING'), ('negative', 'CONDITIONING'),
           ('latent_image', 'LATENT')], [('LATENT', 'LATENT')], (1980, 430), title='采样 25 步')
    b.add('dec', 'VAEDecode', None, [('samples', 'LATENT'), ('vae', 'VAE')],
          [('IMAGE', 'IMAGE')], (1980, 700), title='VAE 解码')
    b.add('final', 'ImageCompositeMasked', [0, 0, False],
          [('destination', 'IMAGE'), ('source', 'IMAGE'), ('mask', 'MASK')],
          [('IMAGE', 'IMAGE')], (990, 700), title='Mask 外还原原图')
    b.add('save', 'SaveImage', ['qwen21_edit_masked'], [('images', 'IMAGE')], [],
          (990, 950), title='保存图像')

    # 'maskimg' (the explicit-mask-file branch) is deliberately NOT linked:
    # it is the alternative to the painted mask, toggled by the user.
    for s, ss, d, ds, t in [
        ('clip', 0, 'te', 0, 'CLIP'), ('vae', 0, 'te', 1, 'VAE'),
        ('src', 0, 'te', 2, 'IMAGE'),
        ('src', 1, 'grow', 0, 'MASK'),
        ('src', 0, 'enc', 0, 'IMAGE'), ('vae', 0, 'enc', 1, 'VAE'),
        ('enc', 0, 'snm', 0, 'LATENT'), ('grow', 0, 'snm', 1, 'MASK'),
        ('unet', 0, 'cache', 0, 'MODEL'), ('cache', 0, 'ks', 0, 'MODEL'),
        ('te', 0, 'ks', 1, 'CONDITIONING'), ('te', 1, 'ks', 2, 'CONDITIONING'),
        ('snm', 0, 'ks', 3, 'LATENT'),
        ('ks', 0, 'dec', 0, 'LATENT'), ('vae', 0, 'dec', 1, 'VAE'),
        ('src', 0, 'final', 0, 'IMAGE'), ('dec', 0, 'final', 1, 'IMAGE'),
        ('grow', 0, 'final', 2, 'MASK'),
        ('final', 0, 'save', 0, 'IMAGE'),
    ]:
        b.link(s, ss, d, ds, t)
    return b.out("qwenimage_edit_masked")


def main():
    node_changed = install_optional_image_node()
    if node_changed:
        # Do not publish a graph that names a node the current server cannot
        # deserialize. The disk copy is intentionally delayed as well, so a
        # restart plus a second registration is unambiguous.
        raise SystemExit(
            "OptionalLoadImage was installed/updated. Restart ComfyUI, then run "
            "register_workflows.py again to publish qwenimage_edit."
        )
    if not optional_image_node_is_live():
        raise SystemExit(
            "The server at 8199 has not loaded OptionalLoadImage. Restart ComfyUI "
            "and run register_workflows.py again."
        )
    os.makedirs(WF_DIR, exist_ok=True)
    jobs = [
        ("qwenimage", build_qwen()),
        ("trellis2", build_trellis2()),
        ("qwenimage_trellis2", build_combined()),
        ("qwenimage_edit", build_edit()),
        ("qwenimage_edit_masked", build_edit_masked()),
    ]
    for name, payload in jobs:
        # filesystem copy (fallback / for git)
        fp = os.path.join(WF_DIR, name + ".json")
        with open(fp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        code, body = post_userdata(name + ".json", payload)
        print(f"{name:24s} nodes={len(payload['nodes']):3d} links={len(payload['links']):3d} "
              f"-> POST /userdata {code}  ({len(json.dumps(payload))//1024} KB)")
        if code != 200:
            print("   body:", body)
    print("\nfilesystem:", WF_DIR)


if __name__ == "__main__":
    main()
