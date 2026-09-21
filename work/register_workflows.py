"""Register UI-format workflows (flow graphs) into the E: ComfyUI instance.

Three graphs, per user request:
  1. qwenimage            -- Qwen-Image 2.1 text->image
  2. trellis2             -- TRELLIS.2 image->3D (shape + texture)
  3. qwenimage+trellis2   -- both, WITH explicit VRAM-release notes

Writes via POST /userdata/workflows/<name>.json and also drops a filesystem
copy under ComfyUI/user/default/workflows/ as a fallback.

Usage: python register_workflows.py
"""
import json
import os
import urllib.request

BASE = "http://127.0.0.1:8199"
ROOT = "E:/python/qwenimage"
WF_DIR = os.path.join(ROOT, "ComfyUI", "user", "default", "workflows")


def post_userdata(name, payload):
    url = f"{BASE}/userdata/workflows/{name}"
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
        """inputs_spec/outputs_spec: list of (name, type)."""
        self._id += 1
        nid = self._id
        self._ids[key] = nid
        node = {
            "id": nid, "type": class_type, "pos": list(pos), "size": list(size),
            "flags": {}, "order": len(self.nodes), "mode": 0,
            "inputs": [{"name": n, "type": t, "link": None} for n, t in inputs_spec],
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

    def out(self, name_hint):
        return {
            "id": name_hint, "revision": 0,
            "last_node_id": self._id, "last_link_id": self._link,
            "nodes": self.nodes, "links": self.links, "groups": [],
            "config": {}, "extra": {"ds": {"scale": 0.75, "offset": [0, 0]}},
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


def main():
    os.makedirs(WF_DIR, exist_ok=True)
    jobs = [
        ("qwenimage", build_qwen()),
        ("trellis2", build_trellis2()),
        ("qwenimage_trellis2", build_combined()),
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
