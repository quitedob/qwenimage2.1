# Devlog: Qwen-Image 2.1 (INT8 ConvRot) 本地部署全记录

**日期**: 2026-09-20 → 2026-09-21
**硬件**: RTX 5060 Ti 16GB (Blackwell, sm_120) / RAM 63.8GB / Windows 11
**运行时**: ComfyUI 0.37.0 + torch 2.13.0+cu130 + Python 3.13.14
**作者**: Claude Code + 用户协作
**范围**: 从"检查 qwenimage2.1"出发 → 发现本地 ComfyUI 版本过旧 → 重建运行时 → 校验三个权重 → 端到端出图 → 精确计时

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
