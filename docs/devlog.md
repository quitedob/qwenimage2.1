# Devlog: Qwen-Image 2.1 (INT8 ConvRot) 本地部署全记录

**日期**: 2026-09-20 → 2026-09-21
**硬件**: RTX 5060 Ti 16GB (Blackwell, sm_120) / RAM 63.8GB / Windows 11
**运行时**: ComfyUI 0.37.0 + torch 2.13.0+cu130 + Python 3.13.14
**作者**: Claude Code + 用户协作
**范围**: 从"检查 qwenimage2.1"出发 → 发现本地 ComfyUI 版本过旧 → 重建运行时 → 校验三个权重 → 端到端出图 → 精确计时 → 2.1 原生参考图编辑工作流(静态)→ 合并 TRELLIS.2 3D 生成

---

## 〇、一句话结论

**单张 1024×1024 出图 = 18.5 秒(热)/ 21.7 秒(冷)**,其中 **采样占 15.9 秒(86%)**,25 步,1.58 it/s。峰值显存 15,806 / 16,311 MiB(97%)。

---

## 一、这条线索是怎么一步步展开的

起点是用户一句指令:**"check qwenimage2.1"**。随后每一步都推翻了上一层假设:

```
1. "Qwen-Image 2.1 是民间的吧?"        ← 初判(疑似不存在)
2. "不,官方 2026-09-20 发布"          ← perplexity 确认,比会话早一天
3. "本地 ComfyUI 0.30.0 不支持它"      ← 发现 Aug 3 构建缺 QwenImage21
4. "那就 clone 最新版"                  ← 0.37.0 (2026-09-20)
5. "venv 是坏的,连 python.exe 都没"    ← 环境故障
6. "F: 盘有可用的 portable 环境"        ← 复用 4.6GB python_embeded
7. "下载的文件到底是不是对的?"          ← SHA256 全绿
8. "出图了,18.5 秒"                    ← 端到端验证
```

---

## 二、时间线(所有动作)

### 阶段 A:确认模型存在性

| # | 动作 | 结果 |
|---|---|---|
| 1 | 探索 `E:/python/qwenimage/` + `nvidia-smi` | 只有 `models/` 和 `venv/`;GPU = RTX 5060 Ti 16GB,sm_120 |
| 2 | 读 `venv/pyvenv.cfg` | **关键线索**:`command = ... -m venv F:\python\h3\ComfyUI_sage3_py312\venv` → venv 是从 F 盘**拷贝**来的 |
| 3 | `/perplexity-research` "Qwen-Image 2.1 官方?" | **确认为官方**:Qwen 团队 2026-09-20 开源。架构 = 7B 单流 DiT(32层) + Qwen3-VL-8B TE + 64通道 RGBA VAE(16× 压缩) |
| 4 | 查 HF `Comfy-Org/Qwen-Image-2.1` 文件清单 + 精确字节数 | 拿到全部 8 个文件的精确 size,用于后续反查 |

### 阶段 B:识别在下载的文件(无文件名)

用户用 Chrome 下载,文件名是 `未确认 66983.crdownload` / `未确认 704422.crdownload`,**无法从名字判断是什么**。

| # | 动作 | 结果 |
|---|---|---|
| 5 | 复制 Chrome `History` 到 `/tmp` 查 downloads 表 | 三个 profile 都损坏/无数据 —— **此路不通** |
| 6 | **改用内容反推**:读取已下载文件的 safetensors header | `qwen_image_2.1_vae_bf16.safetensors` 675,509,688 字节 **精确命中** HF 尺寸,238 张量,meta 含 `qwen_image_2.1_vae` |
| 7 | 持续轮询两个 crdownload 的 size | `66983` 停在 **9,350,798,360** = `qwen3vl_8b_int8_convrot` 精确值;`704422` 停在 **7,256,783,064** = `qwen_image_2.1_int8_convrot` 精确值 |

> **结论**:用户下载的正是官方模板所需的 int8 + int8 组合,**一个字节都没错**。

### 阶段 C:运行时故障排查

| # | 动作 | 结果 |
|---|---|---|
| 8 | `venv/Scripts/python.exe` 不存在 | venv **损坏** —— 只有 `pythonw.exe`,没有 `python.exe` |
| 9 | 从系统 Python 3.12 复制 `python.exe` 进去 | 可以启动了,但 `import torch` 报 `Failed to load PyTorch C extensions` |
| 10 | 检查 `venv/Lib/site-packages/torch/lib/` | 里面是 `_C.lib`/`c10.lib` 等**导入库(.lib),不是运行库(.dll)** —— 这是个构建残留,不是可用运行时 |
| 11 | 检查 F 盘原始 venv | **同样损坏**(`import torch` 返回 `None`,只有 14 个 dist-info vs E 盘 90 个) |
| 12 | **找到可用环境**:`F:/python/h3/ComfyUI_windows_portable/python_embeded/` | ✅ `import torch` → 2.13.0+cu130,**CUDA available True**,sm_120 在 arch list 内 |
| 13 | 检查本地 ComfyUI 版本 | 0.30.0,**构建日期 2026-08-03** —— 比模型发布早 6 周 |
| 14 | grep `supported_models.py` 找 `QwenImage21` | **不存在** —— 0.30.0 完全不支持 2.1 |
| 15 | 拉 master 的 `supported_models.py` 对比 | master **有** `class QwenImage21` + `text_encoders/qwen_image21.py` + `latent_formats.QwenImage21` |

### 阶段 D:重建运行时

| # | 动作 | 结果 |
|---|---|---|
| 16 | `git clone --depth 1` ComfyUI | **0.37.0**,commit `0f74f7f`,**2026-09-20** —— 与模型同日 |
| 17 | `robocopy /E` 复制 `python_embeded` → E 盘 | 4.6GB。首次因 MSYS 路径转换失败,**加 `MSYS_NO_PATHCONV=1` 后成功** |
| 18 | 对比 `requirements.txt` 与已装包 | 无缺失,但 5 个 comfy 包版本落后 |
| 19 | **首次 import 失败**:`No module named 'comfy_aimdo.storage'` | `comfy-aimdo` 0.4.11 太旧(需 0.5.5) |
| 20 | `pip install --dry-run` 验证升级范围 | 只有 5 个 comfy 包 + 4 个子包,**不碰 torch** → 安全 |
| 21 | 实际升级 5 个包 | 成功 |
| 22 | 重跑 import gate | ✅ `QwenImage21` 加载,64通道 latent,sampling `shift 0.69` |

### 阶段 E:权重校验 + 出图

| # | 动作 | 结果 |
|---|---|---|
| 23 | 建 `extra_model_paths.yaml` 指向 E 盘 models | 三个模型全部被 ComfyUI 识别 |
| 24 | 从 templates 包提取**官方工作流** `image_qwen_image_2_1_t2i.json` | 解开 subgraph 拿到权威参数:`UNETLoader=int8_convrot`, `CLIPLoader=qwen3vl_8b_int8_convrot`, `KSampler=steps25/cfg1/euler/simple` |
| 25 | 提交 smoke test | **21.67s 成功**(冷启动) |
| 26 | 建 ComfyUI 服务 | 端口 **8199**(8188 被占用,见"困难 4") |
| 27 | 校验三个文件 SHA256(对照 HF LFS) | **三个全 `integrity_verified: true`** |
| 28 | 语义验证:64 tile std 分析 | 0 个平坦 tile(min 10.5),103 色桶 → 排除灰图/噪声故障 |
| 29 | 条件敏感性验证:换 prompt 对比 | 100% 像素差异 → 确认 conditioning 真正驱动输出 |
| 30 | 精确计时:从带时间戳日志提取各阶段 | 见下表 |

---

## 三、核心实测结果

### 3.1 单张出图耗时分解 ⭐

从 ComfyUI 服务端日志(`/internal/logs`)的**毫秒时间戳**逐段提取:

| 阶段 | 冷启动 | 热(第3次) | 热(第4次) |
|---|---:|---:|---:|
| 队列→DiT staged | 0.761s | 0.071s | 0.066s |
| TE 加载 | 1.539s | 0.770s | 0.784s |
| TE 二次 pass | 0.367s | 0.277s | 0.272s |
| **采样(25步)** | **17.283s** | **15.698s** | **15.890s** |
| 尾段(卸载/切换) | 0.616s | 0.613s | 0.622s |
| VAE 加载+解码+存盘 | 1.104s | 0.934s | 0.906s |
| **合计** | **21.67s** | **18.36s** | **18.54s** |

**结论**:
- **热态单张 = 18.5 秒**;冷态 = 21.7 秒(多 3.2s 用于首次 staging)
- **采样是绝对瓶颈:15.9s / 86%**
- 单步 = 15.9 / 25 = **0.636 s/step = 1.57 it/s**
- 所有非采样开销(TE+VAE+调度)合计仅 ≈ 2.5s

### 3.2 分辨率/步数的线性推算

采样是纯线性成本,可外推:

| 配置 | 预估耗时 |
|---|---:|
| 1024² / 25步(实测) | 18.5s |
| 1024² / 20步 | ≈ 15.2s |
| 1024² / 50步 | ≈ 34.4s |
| 2048² / 25步 | ≈ 4× 采样 ≈ 66s(未实测,激活显存相应增长) |

### 3.3 显存

| 项 | 值 |
|---|---:|
| 峰值 VRAM | **15,806 / 16,311 MiB (97%)** |
| TE staged | 8,916 MB |
| DiT staged | 6,920 MB |
| VAE staged | 644 MB |

### 3.4 INT8 内核确实生效(非 fallback)

启动日志铁证:
```
Detected mixed precision quantization
Using mixed precision operations
Native ops: mxfp8, float8_e5m2, asym_w4a8_int8, nvfp4, convrot_w4a4, int8_tensorwise, float8_e4m3fn
```
`convrot_w4a4` 在列 → ConvRot 路径激活。

---

## 四、困难与踩坑

### 困难 1:venv 是从别的机器/目录拷来的,已损坏
`pyvenv.cfg` 的 `command` 字段暴露了来源。`python.exe` 缺失 + `torch/lib` 里只有 `.lib` 导入库。
**教训**:拷贝 venv 是不可靠的(venv 记录绝对路径)。发现 `python.exe` 缺失就该直接判定 venv 不可用,不要指望补一个 exe 就能救活。

### 困难 2:F 盘的"原版" venv 也是坏的
本以为能回溯到源 venv,结果 `import torch` 返回 `None`(命名空间包)—— `torch/__init__.py` 不在。
**真正的可用环境是 `python_embeded/`(portable 嵌入式 Python),不是 venv**。

### 困难 3:下载文件无文件名,无法识别
Chrome 的 `History` 数据库在三个 profile 里都取不到 downloads 表。
**解法**:用**内容反推** —— 读 safetensors header 拿 meta + 张量数,再用**精确字节数**匹配 HF API 返回的 size。两个 crdownload 都停在精确命中值,证据链完整。

### 困难 4:端口 8188 被占用
一个游离的 `python.exe` (PID 21040) 占着 8188。改用 **8199**。
排查:`netstat -ano | grep :8188 | grep LISTENING` → `tasklist //FI "PID eq <pid>"`。

### 困难 5:MSYS 路径转换坑了 robocopy
Git Bash 下 `robocopy F:\... E:\...` 被 MSYS 改写成 POSIX 路径导致失败。
**解法**:`MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*'`。另外 robocopy 退出码 **1 = 成功**(拷了文件),别当失败。

### 困难 6:拖了六周的 ComfyUI
0.30.0(2026-08-03) vs 模型发布(2026-09-20)。
**教训**:新模型发布后,先查本地 ComfyUI 构建日期和 `supported_models.py`,再决定是否要升级/重装。

---

## 五、结论

1. **Qwen-Image 2.1 是官方模型**,2026-09-20 发布,7B DiT 是相对早期 20B Qwen-Image 的重大瘦身。
2. **本地 RTX 5060 Ti 16GB 完全可以跑**,int8 扩散 + int8 TE 组合,峰值 15.8GB。
3. **单张 1024² 约 18.5 秒(热)**。
4. **int8_convrot 是必需的,不是可选的** —— 官方模板默认的 `qwen3vl_8b_bf16` TE 峰值约 10.6GB,叠加 6.9GB 的 int8 DiT 会 OOM。用户当初下 int8 的决定是对的。
5. 三个权重 SHA256 全部对齐 HF 发布值,可放心使用。

---

## 六、复现步骤

```bash
# 1. 运行时(本仓库不含二进制,需自备)
#    - ComfyUI 0.37.0+:  git clone --depth 1 https://github.com/comfyanonymous/ComfyUI.git
#    - Python 3.13 + torch 2.13.0+cu130 (sm_120 必须在 arch list)

# 2. 权重(放到 models/ 下,目录结构见 README)
#    Comfy-Org/Qwen-Image-2.1:
#      diffusion_models/qwen_image_2.1_int8_convrot.safetensors
#      text_encoders/qwen3vl_8b_int8_convrot.safetensors
#      vae/qwen_image_2.1_vae_bf16.safetensors

# 3. 启动
start_comfyui.bat          # → http://127.0.0.1:8199

# 4. 出图
#    work/workflow_api.json 通过 /prompt 提交
```

---

## 七、未验证/风险

- **2048² 分辨率未实测** —— 采样约为 4×,但激活显存也会涨,当前已占 97%,2K **极可能 OOM**。需要先降 TE 到 `w4a8` 或开 CPU offload。
- **系统 RAM 峰值未测** —— 63.8GB 总量,unified loader 在 RAM/VRAM 间搬权重,加模型时要盯。
- **`E:/python/qwenimage/venv` 已损坏仍有残留** —— 建议删除。它不在部署路径上,不影响出图。
- SageAttention / Sol-Attn 等注意力优化**未在 2.1 上测试**。注意 H3 的经验(见 `F:/python/h3/docs/`):SageAttention 在 sm_120 上会走 sm89 内核,多步后崩溃。**不要**在 2.1 上贸然开 `--use-sage-attention`。

---

## 八、2.1 原生参考图编辑(新增工作流)

### 8.1 加了什么

2.1 最大的升级是**原生多参考图**——最多 10 张,不需要再单独跑一个编辑专用模型。
两个新工作流:

| 文件 | 作用 |
|---|---|
| `work/workflow_api_edit.json` | 多参考图编辑(拿图 1 的构图 + 图 2 的某物件) |
| `work/workflow_api_edit_masked.json` | 局部编辑,**mask 外逐像素还原** |

UI 图由 `work/register_workflows.py` 注册为 `qwenimage_edit` / `qwenimage_edit_masked`。

**没有跑 GPU。** 本轮只做静态验证,没提交任何任务(用户明确要求不测)。

### 8.2 拓扑来源:直接抄官方 subgraph

官方模板 `image_qwen_image_2_1_image_edit.json` 把真正的图包在 subgraph 里,
得从 `definitions.subgraphs[0]` 里掏。掏出来是这样的(官方节点 id):

```
453 CLIP ──► 474.clip
454 VAE  ──► 474.vae, 457.vae
451 UNET ──► 469(QwenImage21Cache) ──► 458.model
474 ──► 458.positive/.negative, 468.on_false
456 EmptyLatent ──► 468.on_true, 468.switch=False ──► 458.latent_image
```

两个关键结论:

1. **采样参数是 steps 25 / cfg 1 / euler + simple**,不是此仓库 `workflow_api.json` 里
   那套 steps 25 / cfg 1 / euler + **normal**。文生图那张图用的 scheduler 抄错了(见 8.5)。
2. **`QwenImage21Cache` 是官方默认链路的一环**(`device=auto, dtype=default`),
   接在 UNET 和 sampler 之间。之前那张文生图漏了这个节点。

### 8.3 踩坑:fatal——Autogrow 输入名写错会**静默失效**

`TextEncodeQwenImage21` 的 `images` 是 v3 **Autogrow** 输入,16 个槽位。
`/object_info` 里它长这样:

```
"images": ["COMFY_AUTOGROW_V3", {"template": {"names": ["image_1", ..., "image_16"], "min": 0}}]
```

**直觉会写成 `"image_1": ["6", 0]`,这是错的,而且错得没有任何提示。**

从 `comfy_api/latest/_io.py` 看:

- `_expand_schema_for_dynamic` 用 `expected_id = finalize_prefix(curr_prefix, name)`,
  而 `handle_prefix` 会把 autogrow 自己的 id(`images`)拼进去
  → **`expected_id` 是带点的 `images.image_1`**。
- `parse_class_inputs` 只对非动态输入做 `finalize_prefix`,而 autogrow 分支只在
  `expected_id in live_inputs` 时才 `type_dict[name] = ...`。
- `execution.get_input_data` 对不在 `valid_inputs` 里的键是**直接跳过**的。

结果:**键名写错 → 静默丢弃 → 图能出,只是当作纯文生图**。不报错、不警告。

实测确认:

```python
probe = {'clip': ['3',0], ..., 'image_1': ['6',0], 'image_2': ['7',0]}
# -> optional 里 images.image_1..16 全部存在,但 dynamic_paths 是空的
#    这两个键被丢掉了
probe = {..., 'images.image_1': ['6',0], 'images.image_2': ['7',0]}
# -> dynamic_paths: {'images.image_1': 'images.image_1', 'images.image_2': ...}
#    命中
```

**正确写法是带点的 `images.image_1`。**

UI 格式同理:节点 input 的 `name` 必须是 `images.image_N`,标签用
`localized_name: "image_N"` 来显示成好看的样子(官方模板就是这么干的)。
`register_workflows.py` 的 `Builder.add()` 现在支持第三个元素塞 extra 字段。

### 8.4 坑:UI 节点 id 和 API 节点 id 必须对齐

`Builder` 按插入顺序发 id,**MarkdownNote 也占 id**。所以 `qwenimage_edit` 里
TextEncode 是 5、KSampler 是 20,而 mask 图的 KSampler 是 12。

第一版 `workflow_api_edit_masked.json` 我按"API 自己的一套连号"写的(19~23),
和 UI 对不上。跨检时暴露出来,已重写成 5/7/8/9/10/11/12/13/14/15。

**教训:同名工作流的两份文件必须共用一套 id**,否则以后对着改的时候一定会错位。

### 8.5 坑:`ImageCompositeMasked` 会把 mask 糊两道

mask 外"逐像素还原"我一开始接成:

```
grow(扩过的 mask) ──► ImageCompositeMasked.mask
```

**错。** 因为 `VAEDecode` 出来的图本身已经是"mask 外 = 原图"了
(`SetLatentNoiseMask` 保证的),再拿 mask 糊一遍,等于把**已经外扩过的边界**
再糊一次——边界被扩了两倍,还会盖掉一部分新生成的内容。

**正解:`ImageCompositeMasked` 的 mask 要用未扩过的 `LoadImage` mask(槽 1),
而 `SetLatentNoiseMask` 用扩过的。**

### 8.6 坑:API 格式里 `LoadImage` 没有 `upload`

手写 API 文件时习惯性带了 `"upload": "image"`。那是**前端**上传用的字段,
不在 `INPUT_TYPES` 里。留着就是 `unknown input key`。已删。

### 8.7 坑:注册工作流时 `POST /userdata` 的路径必须 URL 编码

`register_workflows.py` 一开始这么发:

```python
url = f"{BASE}/userdata/workflows/{name}"      # -> 405 Method Not Allowed
```

**5 张图全部 405,一张都没注册上。** 但脚本**照样打印了 `filesystem: ...`**,
因为它是先写文件系统副本、再 POST 的——所以看起来像成功了。这是我第一次回复
"已添加"时它其实没进 UI 的原因。

原因是 aiohttp 的路由:`@routes.post("/userdata/{file}")` 里的 `{file}` 只匹配
**单个路径段**,`workflows/qwenimage.json` 里的字面 `/` 压根到不了这个路由。

而 `user_manager.py:90` 明确写了:

```python
if file is not None:
    if "%" in file:            # 只有带 % 才 unquote
        file = parse.unquote(file)
    path = os.path.abspath(os.path.join(user_root, file))
```

**所以正确姿势是把整条相对路径当成一个段来百分号编码:**

```bash
# 405
curl -X POST ".../userdata/workflows/_probe.json" --data-binary @p.json
# 200  -> "workflows\\_probe.json"
curl -X POST ".../userdata/workflows%2F_probe.json" --data-binary @p.json
```

已改成 `urllib.parse.quote(f"workflows/{name}", safe="")`,现在 5/5 返回 200。

**教训:这个脚本的"文件系统兜底"会把注册失败伪装成成功。**
`print` 里那个状态码才是真相,别只看最后一行。顺带一提,`POST` 成功返回的是
**文件路径字符串**(不是 JSON),不要当 JSON 解析。

### 8.8 最终实现:10 条线全连，但 `[no image]` 真正不输入

用户要的不是"需要时手工连一条线",而是:

> **十条线都接上；没有图片时，这条线不向 Qwen 输入任何内容。**

直接给每条线放原生 `LoadImage` 不行。它的 `image` 是必填 combo；空字符串在
`execution.py` 的 combo 校验阶段就触发 `value_not_in_list`，根本到不了下游。

解决是新增一个 55 行的本地 custom node：`work/optional_load_image.py`。
它继承原生 `LoadImage`（真实图片仍复用原版的上传/动画/alpha mask/batch/缓存逻辑），
只新增一个 combo 选项:

```python
NO_IMAGE = "[no image]"

def load_image(self, image):
    if image == NO_IMAGE:
        return (None, None)
    return super().load_image(image)
```

这不是黑图、透明图、16×16 占位图，也不是空文件名；是**沿 IMAGE edge 传 Python `None`**。
而本机 `TextEncodeQwenImage21.execute` 的官方实现正好是:

```python
for name in sorted(images, key=lambda n: int(n.rsplit("_", 1)[-1])):
    image = images[name]
    if image is None:
        continue
    # 只有非空图才缩放、进 Qwen3-VL、进 VAE
```

所以 `[no image]` 的效果是严格的:

- **不进入 Qwen3-VL 视觉序列**
- **不做 VAE encode**
- **不进入 `reference_latents`**
- 因而不是"模型看到一张空白图再忽略它"，而是编码器根本没有该图

`qwenimage_edit` 现为 **10 个 `OptionalLoadImage` 永久连接**，默认全部 `[no image]`。
可随意只选第 1/3/7 张，余下槽位保持 `[no image]`；**不用断线、不用静音、不用删节点**。

#### GPU 实测（只跑 Qwen，未跑 TRELLIS）✅

| Case | 10 个连接槽位的值 | 结果 | 服务端/轮询耗时 |
|---|---|---|---:|
| A: 全空 fallback | 10 × `[no image]` | ✅ 1024² RGBA 黑色大理石机械表（纯文生图） | **20.58 s / 25.1 s** |
| B: 非首槽单独启用 | **仅 slot 3** = `viking_wolf_rune_axe.png`，其余 9 × `[no image]` | ✅ 1024² RGBA；狼首、符文、金属斧刃、皮缠木柄均保留，prompt 成功换成雪山场景 | **29.17 s / 30.1 s** |

产物：

```text
output/qwen21_edit_fallback_00001_.png
output/qwen21_edit_slot3_only_00001_.png
```

两个图都是 RGB 动态范围 0–255；全空图 `std=33.40 / 34,856` 种 RGB，slot-3 图
`std=67.67 / 134,362` 种 RGB，排除灰图/单色/噪声失败。后者由我直接视觉检查:
斧的核心视觉身份被明显保留。

**这也实际证明了 `min=0` 的语义**：参考图片全在 autogrow `optional` 里；原本的
required 只有 `clip`、`prompt`、`negative_prompt`、`resolution`。不同的是现在空 slot
即使**已连线**，也通过 `None → continue` 完成真正的 fallback。

#### 部署约束

`ComfyUI/` 在 gitignore 内，不能把 node 直接作为仓库文件提交。所以 canonical source
放在 `work/optional_load_image.py`。`register_workflows.py` 注册前会把它复制到:

```text
E:/python/qwenimage/ComfyUI/custom_nodes/optional_load_image.py
```

若复制内容有变化，脚本会**停止注册**并要求先重启 ComfyUI；重启后第二次运行注册脚本。
这样不会把引用一个尚未被 server import 的节点的图悄悄发布出去。

### 8.9 修正:`example.png` 是低细节涂鸦，不是 64×64 空白图

我前面把它说成了 64×64 空白占位，这是错的。实查：它是 **768×768 RGB** 的简单儿童涂鸦
（蓝天绿地、黄发粉衣人物），只有 **9 种 RGB 颜色**。它能作为合法参考图，
但不适合作为真实人物/物件编辑的质量测试；本轮实际用的是 1024² 的
`viking_wolf_rune_axe.png`。

### 8.10 App 模式(旧名 Linear mode)是怎么存的

查了一圈 1.53.6 的前端包,结论:

```js
function linearModeToAppMode(e){
  return typeof e === `boolean` ? (e ? `app` : `graph`) : null
}
// 读取处:
e.initialMode = linearModeToAppMode(e.initialState?.extra?.linearMode) ?? s
```

**开关是 `extra.linearMode`,一个布尔值**,不是 `initialMode`(那只是运行时字段,
存盘时叫 `linearMode`)。`true` → 图以 App 模式打开。

`Builder.out()` 现在带这个参数,五张图**全部传 `app=False`**——理由写在代码注释里:

> App 模式**隐藏节点图,只显示 widget 输入**,于是 `KSampler` 的
> `Controller After Generate`、`GrowMask.expand`、`custom_size` 开关、
> **以及参考图槽位本身**都够不到。而且它把**所有连上的输入**都暴露成控件,
> 跟"按需连接"的意图正好相反。

**制作阶段求能力完整,用图模式;定稿后再切 App 模式。**

#### 我之前的错:记错了一个不存在的机制

上一轮我说过 `test_node(edge)` 这种按输入动态显隐 App 控件的机制。
**这个 API 在 1.53.6 里根本不存在** —— 我 grep 了整个前端包,`test_node` 零命中。
那是我从别处记混的东西,不该当成事实讲出来。

同样纠正:`ExecutionBlocker` 也**不是**"跳过某一个槽位"的机制。
它读的是(docs 原文):

> Return this from a node and any users will be blocked with the given error message.

**它会 block 掉整个下游节点**,不是单个 input,所以也做不了逐槽位的 fallback。

### 8.11 验证方式:`work/validate_workflows.py`

结构校验依赖运行中的 ComfyUI 的 `/object_info` 做只读 GET（不提交任务、不加载权重）。
**它和 §8.8 的两次 GPU fallback 推理是两层不同证据**：前者防 schema/link 漂移，
后者证明全空和任意单槽真实图在实际 Qwen 路径都能出图。结构校验查三件事:

1. **API 格式**:class_type 是否存在、输入键是否合法、必填是否齐全、link 的源节点和槽位是否越界。
2. **UI 格式**:link id 是否都指向存在的 link、槽位下标是否对得上。
3. **UI ↔ API 一致**:节点 id→类型、边的集合是否一致。**故意的不一致要在 `ALLOWED` 里显式声明**,没声明的就报错。

为什么必须走 `/object_info` 而不是 `nodes.NODE_CLASS_MAPPINGS`:
**`TextEncodeQwenImage21` 是 v3 节点,根本不在 `NODE_CLASS_MAPPINGS` 里**
(第一版验证脚本就是这么 KeyError 的)。`/object_info` 是引擎自己用的注册表,也是唯一的地方。

当前结果:

```
workflow_api.json / _edit / _edit_masked            -> OK
5 张 UI 图                                          -> OK
qwenimage_edit.ui vs workflow_api_edit.json         -> OK (4 UI-only / 1 API-only, 均已声明)
qwenimage_edit_masked.ui vs ..._masked.json         -> OK (19/19 全等)
```

两张图之间**故意**的差异(已在 `ALLOWED` 里声明):

- `qwenimage_edit`:UI 与 API 都有 10 条 `OptionalLoadImage → images.image_N` 边；
  UI 额外保留官方 custom_size 开关(`EmptyLatentImage` + `ComfySwitchNode` + boolean)，
  API 直接把 `TextEncodeQwenImage21` 自己的 latent 喂给 sampler——**默认效果相同**，
  因为 UI 的 `ComfySwitchNode` 默认走 `on_false`。
- `qwenimage_edit_masked`:UI 多一个 `LoadImageMask` 作为蒙版文件的备选路径,故意不连线。

### 8.12 已验证与仍未验证

**已验证（Qwen-only）**：10 个连接槽位均 `[no image]` 能走完整的 Qwen TE → DiT → VAE →
SaveImage；仅第 3 槽放真实图、其它 9 槽为空也能完成，且参考斧明显保留（§8.8）。

仍未验证:

- **多张真实参考图**（2/3/10 张）的峰值 VRAM、延时、细节一致性还没测。单 slot 3 的服务端总时
  29.17 s；文生图 peak 仍是 15,806/16,311 MiB，更多图可能 OOM，运行时请盯 `nvidia-smi`。
- **`<imageN>` 编号没实测。** 官方模板的 prompt 里用 `<image1>`/`<image2>`，但本地
  `qwen_image21.py` 自己拼的引用块是 0-based (`range(len(images))`) 加一个末尾 `Picture 1:`。
  所以正式 prompt 建议按图的内容描述，先别依赖 `<imageN>` 的号码语义。
- **API 格式提交时 mask 怎么传没实测。** UI 里涂鸦后,提交到 `/prompt` 时节点 inputs 里会多出
  `mask: {points, image}`；本轮没有造这一实例。
- **`ImageCropToMask`** 存在于注册表但本机**没有 `input/3d`** 目录；未验证。
- mask 走涂鸦还是蒙版文件,**只能二选一**，同时接会打架；`Ctrl+M` 静音切换尚未实测。

### 8.13 参考图数量的实用建议

官方 PE-I2I 结果 + 社区实测指向同一件事:**图给多了、文字描述一长,参考图细节就丢**。

- 从**第 3 张**参考图开始一致性明显下滑
- **侧视角的发型角度**最容易崩

**有效参考图控制在 2~3 张。** 边角信息(配饰、材质、背景)写成文字塞进 prompt,
比硬凑满 10 张强。图注里也这么写了——这条是**建议**,不是硬限制,节点本身支持到 16。

---

## 九、合并 TRELLIS.2 3D 生成(2026-09-21)

把 F 盘那个独立的 TRELLIS.2 测试实例**合并进本实例**,让 8199 一个端口同时服务
Qwen-Image 2.1 文生图/编辑 与 TRELLIS.2 图生 3D。事后删除 F 盘那套。

**本轮没跑目标模型的生成**——只做静态验证(结构校验 + 注册),TRELLIS 的**实际出片**
是在 F 盘环境验证过的(见 `F:/python/h3/docs/devlog.md` §24)。

### 9.1 结论先行:不需要装任何东西

最意外的发现:**本实例的 ComfyUI 0.37.0 自带 TRELLIS.2 支持**,连 kernel 都比 F 盘的新。

| 项 | 本实例 | F 盘 TRELLIS 环境 | 冲突? |
|---|---|---|---|
| Python | **3.13.14** | 3.13.14 | ✅ 无 |
| torch | **2.13.0+cu130** | 2.11.0+cu130 | 版本差(同在 cu130) |
| ComfyUI | **0.37.0** | 0.36.0 | ✅ 更高 |
| `nodes_trellis2.py` | **已存在**(42,371 B) | 41,057 B | ✅ |
| `comfy/ldm/trellis2/` | **已存在**,`flexgemm.py` 还多了 `sparse_pool3d_mean` | 有 | ✅ 更新 |
| `dino3_large.json` | **已存在** | 有 | ✅ |
| flash_attn | **未装** | 2.9.0 | ✅ **不需要** |
| comfy-kitchen / aimdo | 0.2.35 / 0.5.5 | 0.2.34 / 0.5.3 | ✅ 更新 |

**TRELLIS.2 的原生路径对 `flash_attn / spconv / natten / flex_gemm / o_voxel / cumesh / nvdiffrast` 零引用**
(`comfy/ldm/trellis2/flexgemm.py` 名字唬人,实为纯 torch:`searchsorted` / `matmul` / `index_add_`)。
所以 F 盘那套的 `flash_attn 2.9.0` 不是迁移的一部分。

### 9.2 关于"TRELLIS.2 限制 cu12.4"——已核实为**不成立**

| 变体 | CUDA 要求 | 依据 |
|---|---|---|
| microsoft 原版 | cu124 只是 `setup.sh:73` 的**默认值**,README:77 明说可换 | 原仓库 |
| **ComfyUI 原生(本路径)** | **无任何要求**,只依赖 `torch` | `nodes_trellis2.py` / `model.py` / `vae.py` / `flexgemm.py` 逐文件核对 |
| visualbruno 封装 | cu128 / cu130,**无 cu124 路径** | 上游 README:92 |
| Comfy-Org 权重 | 无版本门(safetensors header `__metadata__=None`) | HF |

ComfyUI 官方博客原话:原版"targets PyTorch 2.6.0 with CUDA 12.4 … **These dependencies have been removed with the native integration**"。
**真正的 CUDA 约束是反方向的**:int8 convrot 走 `comfy_kitchen`,其 METADATA 要求 **CUDA Runtime ≥13.0 + 驱动 r580+**(二进制里是 `-arch sm_120f`)。本机驱动 591.86 / cu130 满足;退到 cu124 反而会因为**不支持 sm_120** 而崩。

**本实例已经在跑 `qwen_image_2.1_int8_convrot`**,用的是**同一套 convrot 量化机制** —— 也就是说 TRELLIS 那个 int8 权重依赖的量化路径,在这里已被 Qwen 验证过。实测 `comfy_kitchen` CUDA 后端 capability 列表里明确含 `dequantize_int8_convrot_weight` / `quantize_int8_convrot_weight` / `rotate_int8_convrot_weight`。

### 9.3 权重迁移:复用现有 `models/` 树,只加一行配置

4 个 TRELLIS 权重(共 **8,509,913,356 B**)从 F 盘复制到 E 盘,按官方结构摆放:

```
models/diffusion_models/trellis_2_int8_convrot.safetensors     5,253,048,192
models/vae/trellis_2_shape_vae_bf16.safetensors                1,095,844,024
models/vae/trellis_2_texture_vae_bf16.safetensors                948,461,364
models/clip_vision/dino_v3_vit_l.safetensors                   1,212,559,776
```

**SHA256 四个全部命中**预期值。跨盘(HDD→SSD)复制,`cp` 只用 5.975 s 返回 —— 那是 Windows 写回缓存,`sync` 后重新哈希确认内容正确(18.57 s 读完 8.5 GB ≈ 458 MB/s,正是 NVMe 速度)。

**配置只加一行**:`extra_model_paths.yaml` 里 `diffusion_models` / `vae` 两项**本来就指向 `models/`**,只差 `clip_vision`。补上即可,`diff` 确认只有一行新增。Qwen 三个权重字节数未变。

### 9.4 五个工作流注册(UI 格式)

当前 8199 的 `/userdata?dir=workflows` 已实际列出 5 张图：

| 文件 | 节点 / 连线 | 内容 |
|---|---:|---|
| `qwenimage.json` | 10 / 9 | Qwen 文生图 |
| `qwenimage_edit.json` | 22 / 23 | Qwen 2.1 原生多参考图编辑：10 个永久连接的 `[no image]` fallback 槽（§8） |
| `qwenimage_edit_masked.json` | 15 / 19 | Qwen 2.1 局部 / 蒙版编辑（§8） |
| `trellis2.json` | 27 / 46 | 完整 TRELLIS 图生 3D（形状 + 贴图） |
| `qwenimage_trellis2.json` | 32 / 51 | Qwen 与 TRELLIS 两组分支 + **显存卸载说明便签** |

TRELLIS 图的**连线拓扑与 widget 取值全部取自官方模板** `3d_pixal3d_trellis2_image_to_model.json`（随 pip 包落在 `comfyui_workflow_templates_json/templates/`），但删去 Pixal3D / MoGe / BiRefNet 三组本机没有的模型分支以及两个 `ComfySwitchNode`。

**静态校验**（用 `/object_info` 做只读 GET）全部通过：每个 link 的源/目标槽位存在、类型匹配、节点类型在实例中注册、输入槽位的 `link` 反指一致。widget 值与已跑通的 API 图**逐字段对照一致**。

> **坑**：UI 格式的 `KSampler` 有 7 个 widget 位 `[seed, control_after_generate, steps, cfg, sampler, scheduler, denoise]`。
> 我写的一次性转换脚本没丢掉第 2 格，导致提交报 `could not convert string to float: 'normal'`。
> **那是转换脚本的 bug，不是注册图的错** —— 注册图本身与 API 图一致。

另一个注册坑：`POST /userdata/{file}` 的 `{file}` 只能匹配**一个 URL path segment**。`workflows/trellis2.json` 若带字面 `/` 发出会是 405；必须将整段相对路径百分号编码成 `workflows%2Ftrellis2.json`。`register_workflows.py` 已用 `urllib.parse.quote(..., safe="")` 修复；状态码 200 才算真正注册成功，文件系统兜底不能当成功判据。

### 9.5 显存：唯一的真冲突，且是**调度**冲突

| 事实 | 证据 |
|---|---|
| Qwen-Image 2.1 单独跑就峰值 **15,806 / 16,311 MiB (97%)** | 本文档 §3.3 |
| **ComfyUI 不会在任务间自动卸载模型** | 连跑 4 次,日志均 `0 models unloaded`,12.5 GB 一直驻留 |
| `POST /free` 有效 | 实测 **14,283 → 2,013 MiB**(释放 12.3 GB) |
| `/system_stats` 的 `vram_free` **高报约 1.2 GB** | 15,067(接口)vs 13,909(nvidia-smi 物理) |

**所以 `/free` 是必需步骤,不是可选优化**:

```
POST http://127.0.0.1:8199/free
{"unload_models":true,"free_memory":true}
```

判断显存够不够**只信 `nvidia-smi`**。非 ComfyUI 基线约 2,100 MiB(最大占用者 `GameViewerServer.exe` 3,135 MB,且不出现在 nvidia-smi 的进程表里)。

`qwenimage_trellis2.json` 的说明便签里写清了这条,并明确**两组分支不要同时执行**(用 `Ctrl+M` 静音切换)。

### 9.6 端到端实测：TRELLIS 与 Qwen 在同一 8199 实例交替运行 ✅

先前写的「未跑 TRELLIS」已经被本轮实测**推翻**。这不是只看 `status_str`，而是有日志、磁盘产物、GLB 结构与交替 Qwen 出图四层证据。

| 运行 | 服务端耗时 | 磁盘产物 | 结构核验 |
|---|---:|---|---|
| TRELLIS 仅形状（冷） | **101.99 s** | `output/3d/trellis2_shape_00001_.glb`，264,130,396 B | glTF v2 / 1 mesh / 14,728,506 三角面 / `POSITION` |
| TRELLIS 完整贴图（热） | **154.63 s** | `output/3d/trellis2_textured_00001_.glb`，351,515,668 B | glTF v2 / 14,728,506 三角面 / `POSITION + COLOR_0` |
| 再切回 Qwen | **22.87 s** | `output/qwen21_smoke_00002_.png`（1,432,361 B） | Qwen 的 DiT / TE / VAE 完整加载与 25 步完成 |

**形状/贴图的实证细节**：

- 日志确认 `Requested to load Trellis2`，`Model Trellis2 prepared for dynamic VRAM loading. 5004MB Staged`；DINOv3、ShapeVae、TextureVae 均完成动态加载。
- INT8 路径铁证：`Found quantization metadata version 1` → `Detected mixed precision quantization` → `Using mixed precision operations` → `Native ops: ... int8_tensorwise ... convrot_w4a4 ...`。
- `trellis2_shape_00001_.glb` 的 bbox 为 `[-0.49992,-0.49663,-0.06516] → [0.49857,0.49199,0.07416]`，与 F 盘已验证基线逐位一致。
- 输入图是 RGB、没有 alpha，所以日志有 `Trellis2 preprocess: mask bbox empty, using inverted mask.`。这是已知的居中裁剪回退，不是 CUDA / 模型错误。

**交替共存的关键判据**：完整 TRELLIS 运行后，显存仍驻留 **9,643 MiB**；显式调用 `/free` 后降至 **2,027 MiB / 14,024 MiB free**，再提交 Qwen 才成功。日志里的 `0 models unloaded.` 进一步证明 ComfyUI **不会自动**在任务间卸载，`/free` 是两个流程切换前的强制步骤。

### 9.7 仍未验证 / 风险

- 已验证的是官方示例 `viking_wolf_rune_axe.png` 的一形状、一完整贴图及一次切回 Qwen；**未做多图、多 seed 的质量回归**。
- 14,728,506 面对普通预览、游戏资产和打印都过大；**未做减面、闭合性、壁厚或可打印性验收**。
- `qwenimage_edit` 已做过两次 Qwen-only fallback GPU 出图（全空 / 仅 slot 3，§8.8）；`qwenimage_edit_masked` 仍只做结构验证。
- Qwen 2.1 仍会峰值占满 97% VRAM；2K、更多参考图和 Qwen/TRELLIS 并发仍可能 OOM。

### 9.8 教训

1. **合并前先查目标环境已有的能力**,别默认"要装一遍"。本实例的 ComfyUI 0.37.0 本来就带 TRELLIS.2,而且 kernel 比源环境新。
2. **"有 flash-attn"不构成路线依据。** TRELLIS.2 原生路径零引用 flash-attn;真正的依赖是 `comfy_kitchen` 的 convrot 算子,而它本实例已在用。
3. **CUDA 版本要求要查一手来源。** "限制 cu12.4"来自原版仓库的便捷脚本默认值 + 官方博客对**原版**的描述,而博客紧接着声明原生集成已移除这些依赖;我们的实际约束方向相反(需 ≥13.0)。
4. **UI 格式的隐藏 widget 位会咬人。** `KSampler` 的 `control_after_generate` 占一格,API↔UI 转换时漏掉它会造成"字段错位"型的 400。
5. **显存不足往往是调度问题,不是容量问题。** 两个模型各自都跑得动,合在一个进程里就必须显式卸载。
