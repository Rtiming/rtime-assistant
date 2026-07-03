# brain-visualmd 部署(本地视觉模型,开箱即用)

后端与算力解耦:`vision` 后端只认一个 `base_url`。在哪台设备跑模型,就在那台跑这两个脚本,**换设备=换机器跑 bootstrap**,模块代码不动。

## 在一台新设备上(Mac / Linux / orangepi)

```bash
# 1) 一键起本地模型服务(装 ollama→持久服务带大 context→拉模型→验证)
bash deploy/bootstrap.sh
#   默认 qwen3-vl:8b + ctx 16384;换模型:VISUALMD_MODEL=qwen2.5vl:7b bash deploy/bootstrap.sh

# 2) 跑量(对一个资料目录增量转写,可后台/夜跑,可续)
nohup bash deploy/run-scan.sh /path/to/course ~/visualmd-out >run.log 2>&1 &
tail -f run.log
```

成品落 `~/visualmd-out/<slug>/<slug>.md`(暂存,不写 brain)。再跑一次自动跳过已完成的。

## 关键配置(bootstrap 已处理)

- **持久服务**:Mac=launchd(`com.rtime.visualmd-ollama`)、Linux=systemd --user(`visualmd-ollama.service`)、否则 nohup。都带 `OLLAMA_CONTEXT_LENGTH`(默认 16384)—— 整页幻灯片图≈3700 视觉 token,默认 4096 会截断。
- **模型**:默认 `qwen3-vl:8b`(思考型,会判断图表/存疑,质量优先);要更快用 `qwen2.5vl:7b`(只誊写)。
- **env**:`VISUALMD_VISION_BASE_URL`、`VISUALMD_VISION_MODEL`(run-scan 自动设)、`VISUALMD_VISION_MAX_TOKENS`(默认 8192,思考型需要)、`VISUALMD_VISION_MAX_IMAGE_PX`(可选下采样提速,需 Pillow)。

## 设备差异

| 设备 | 加速 | 说明 |
|---|---|---|
| Mac(Apple Silicon) | Metal GPU | ollama 自动用 Metal;~min/页 |
| 带 NVIDIA 的 Linux | CUDA | ollama 自动用 CUDA,最快 |
| orangepi(RK3588) | CPU(ollama) | 无 CUDA;慢,夜跑;`nice/ionice` 已让路给网关。NPU(RKLLM)是另一条更快但模型受限的路,见工具文档 |

## 瘦客户端架构(orangepi → Mac/GPU)

调研定论:orangepi(RK3588)**跑不了文档 VLM**(NPU 锁 ~400px 缩略图、CPU 分钟/页)。
所以让**算力机当模型主机、orangepi 当瘦客户端**:orangepi 只渲染+编排+存盘,OCR 调远端。

```bash
# 1) 在算力机(Mac/GPU)对外起服务:
VISUALMD_OLLAMA_HOST=0.0.0.0 bash deploy/bootstrap.sh
#    监听 0.0.0.0:11434;用 Tailscale/LAN IP 让 orangepi 连得到(注意只在可信网内开放)。

# 2) 在 orangepi(瘦客户端)指向算力机、跑增量:
export VISUALMD_VISION_BASE_URL=http://<算力机-tailscale-ip>:11434/v1
export VISUALMD_VISION_MODEL=hf.co/ggml-org/GLM-OCR-GGUF:Q8_0
bash deploy/run-scan.sh <brain-root>/knowledge/courses/<id>
```

数据在 orangepi(brain),渲染/编排/checkpoint 也在 orangepi;每页图 POST 到算力机的
`/v1`、收回 Markdown。算力机离线时 orangepi 的 `scan` 会失败该页但不丢已完成页(可续)。
`analyze`(版面)与 `formula`(公式专家)是纯 CPU/ONNX,小到能在 orangepi 本地跑;
唯独文档 VLM 必须走远端。

## 速度旋钮

- `VISUALMD_VISION_MAX_IMAGE_PX=1280`(需 `pip install Pillow`)下采样大图 → 砍掉大部分图像编码时间。
- 或渲染时降 `--dpi`(质量优先则别降)。
- 弱盒优先 `qwen2.5vl:7b` 跑量 + 强后端/人复核被标存疑的页(escalation)。
