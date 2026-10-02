# AutoCanvas V2

## 回放阅读稿

回放默认使用独立的长语段流程，直播仍读取 `chunk_seconds` 的短块。设置 `replay_chunk_seconds`
（默认 30）、`replay_max_seconds`（默认 45）和 `replay_silence_seconds`（默认 0.6）。
语段优先在停顿切分，下限为目标长度的 80% 与 24 秒中的较小值；结尾不足一段时保留实际长度。通过 `replay_vad_model`
指定已下载的官方 Silero TorchScript 文件；留空时使用较保守的能量检测。模型只从本地加载。

```sh
python -m autocanvas --config config.toml transcribe audio.wav --output runtime/local/lecture
python -m autocanvas --config config.toml transcribe audio.wav --output runtime/local/legacy --legacy
python -m autocanvas --config config.toml transcribe audio.wav --output runtime/local/lecture --context '已核对的术语'
```

离线 `transcribe --model /local/model/path` 可单独测试较大模型，不修改已保存的模型设置。
按课程使用术语时，在 `runtime/contexts/<course_id>.json` 写入 `{"terms": ["已核对的术语"]}`。
词表是识别提示，不能作为补写未听清内容的依据。

原有三秒稿保留。新回放结果写到 `vod_asr/runs/<参数指纹>/`，包含原始
`segments.jsonl`、`transcript.json`、`transcript.txt`、段落版 `reading.json/md/txt`、
`edits.json` 和处理清单。指纹包括模型、VAD 校验和、切分参数、词表、音源和试跑区间。
中断仅从已经写入的完整语段续跑；空白时间也保存，网络提前结束不会冒充完成。

阅读稿只做段落组织和轻度口语清理，保留“对”“是”等回答；每段带原始语段编号。
编辑后会检查数值、拉丁/希腊符号、常见单位的出现顺序，以及常见否定/条件词的数量。
这是保守的机械预检，不能证明数值关系或语义正确，涉及重排这些内容时需要人工核对。
显式校对可导入每段 `{"segment_ids": [...], "text": "..."}` 的 JSON 数组。
正文只放课堂内容；无法可靠恢复的术语或完整语句可以移到 `issues`，必须引用该段的原文：

```json
{"segment_ids": [0], "text": "本节介绍基本概念。", "issues": [
  {"source_text": "unclearTerm", "reason": "术语识别不确定"}
]}
```

疑点保存为独立的 `issues.json` / `issues.md`，带时间范围和原始语段编号。
识别说明由审校者放入 `issues`，正文不依靠关键词自动删改；原有已审校内容保持不变。
疑点必须引用来源，不允许单独挪走否定词、数字或词的一部分来绕过预检。

```sh
python -m autocanvas reading /path/to/run --review reviewed.json
```

已校对阅读稿不会被同一原始稿的重新格式化覆盖。前端优先打开段落阅读稿，原始稿仍可选择。
时间标记是语段范围，不是逐字对齐。语义润色不会自动调用外部模型或上传音频。

回放最多预读十六个完整语段，让解码、CPU 语音检测与 GPU 推理交叠进行。排队时保留最早
请求，优先选择长度相近、术语上下文相同的语段，减少批内填充和等待。CUDA 模型合批识别
最多八段，填充音频预算不超过 300 秒，共用一份模型；显存不足时自动减小批次并重试。输出仍按时间顺序逐段
保存，失败时不会跨过未成功识别的语段。该优化不缩短语段，也不增加一轮模型润色调用。

直播任务仍优先入队，但正在推理的回放语段不能中途抢占。较大的离线模型建议单独运行；
切勿把语音活动检测分数、文件完成状态或段落更顺当作识别准确率。

新版 Canvas 视频接入、独立本地转写与 Slides 抽取，以及外层自动化控制。

登录鉴权、Canvas 数据查询、视频地址解析、媒体读取、ASR、Slides 各自独立。没有插件加载、Gateway 或 Job 注册机制。ASR 不知道视频 URL，Slides 不知道课程 ID，功能模块不访问执行数据库。

## 环境与启动

Python 3.11+，系统需要 `ffmpeg` 和 `ffprobe`。可使用虚拟环境或 conda；模型从本地 Hugging Face 缓存读取，不自动下载。

项目生成的文字文件统一写为 UTF-8。手写 TOML 配置、审校 JSON 和术语 JSON 也可使用
带 BOM 的 UTF-8；旧 GBK / UTF-16 文件需先转换，程序不猜测编码或忽略解码错误。

Linux、Windows 和 Apple Silicon MacBook 共用 Python / WebUI 流程，不依赖本地 `.sh`
启动脚本。`device = "auto"` 在首次识别时选择 CUDA、MPS 或 CPU，也可以明确指定
`cuda:0`、`mps`、`cpu`。CUDA 使用支持的 BF16，MPS / CPU 使用 FP32 和单段推理。
PyTorch 请按[官方安装页面](https://pytorch.org/get-started/locally/)选择对应系统的版本；
`ffmpeg` 和 `ffprobe` 都需加入 PATH。当前实测平台是 Linux / NVIDIA；Windows 和
Mac 的代码兼容检查不能替代真机验证，Intel Mac 的依赖组合尚未验证。

```sh
cd /path/to/AutoCanvas
python -m venv .venv
source .venv/bin/activate
python -m autocanvas --help
```

Windows PowerShell 激活命令为 `.\.venv\Scripts\Activate.ps1`，其余 `python -m ...`
命令相同；也可直接使用 `.\.venv\Scripts\python.exe`，无需修改 PowerShell 执行策略。

安装到其他环境：`python -m pip install -e '.[asr,slides]'`。仅登录、查询与 HTTP 功能可以只安装基础依赖；ASR 和 Slides 的重依赖延迟加载。

```sh
# 首次创建私有 runtime；已有 runtime 不需要重新初始化。
python -m autocanvas init
# 可在首次 init 时附加 --import-session /path/to/canvas_session.json
python -m autocanvas login
python -m autocanvas sync --assignments
python -m autocanvas serve
```

已有登录会话时无需重复登录。服务默认绑定 `127.0.0.1:8080`。前台运行时 Ctrl+C 清理媒体子进程、保存进度；需要后台驻留可用 `tmux new -s auto-canvas-v2 'conda run --no-capture-output -n auto-canvas python -m autocanvas serve'`。

### 自动登录

将 `credentials.example.yml` 复制到 `runtime/auth/credentials.yml`，直接填写：

```yaml
enabled: true
username: '你的 jAccount 用户名'
password: '你的密码'
```

这是本机明文配置，支持注释；密码中的单引号写成两个单引号。使用 `--root` 时文件位于该运行目录的 `auth/credentials.yml`。文件不进入版本管理，也不会通过 WebUI 文件接口公开；程序写入时设置 POSIX 权限 0600，Windows 使用所在目录的访问权限。配置修改会自动重新读取，无需重启。

认证依次尝试现有 Canvas Cookie、JAAuthCookie 静默刷新、保存的账号密码完整登录。完整登录沿 Canvas OIDC 跳转建立会话 Cookie，不依赖独立 OAuth refresh token。验证码图片发送到用户指定脚本使用的 `https://geek.sjtu.edu.cn/captcha-solver/`，独立请求不携带学校 Cookie 或账号密码。

验证码错误每轮最多重试 3 次；网络或识别服务故障按 60/300/900/3600 秒退避，重启保留退避状态。密码被拒绝或出现额外身份验证时停止自动提交，修改 YAML 后恢复尝试。服务每分钟检查认证并恢复符合条件的 `needs_login` 执行；暂停和取消语义保持不变，视频任务需额外验证课程视频访问。退出登录会同时停用自动登录，避免立即重新登录。

WebUI 登录页提供状态和“验证全新登录”按钮；它不复用现有 Cookie，成功后才替换会话文件，失败保留旧会话。也可在服务运行时调用 `POST /api/auth/automatic-test`，状态通过 `GET /api/auth/automatic` 查看，均不返回密码。

通常无需手动更新会话，但学校登录流程变化、账号锁定或新增短信验证仍可能需要人工处理。认证模块不依赖 macOS 专属能力；Windows/Linux 的 ASR 设备需按实际环境配置（例如 `cpu` 或 `cuda`）。

配置：复制 `config.example.toml` 为本地 `config.toml`，运行 `python -m autocanvas --config config.toml serve`。也可用全局 `--root /private/path` 指定独立运行数据。`config.toml` 与 `runtime/` 都不进入版本管理。

## 独立命令

```sh
# 同步当前课程和视频；没有新版教学班映射的课程会明确显示 video_unavailable。
python -m autocanvas sync
python -m autocanvas sync --course 10001
python -m autocanvas assignments 10001
python -m autocanvas list courses
python -m autocanvas list lectures
python -m autocanvas list executions

# 查询来源编号，默认不输出私人播放地址。
python -m autocanvas sources 10001 20001
# 确实需要地址时显式附加 --show-urls。

# 不依赖 Canvas 登录、数据库或服务的本地处理。
python -m autocanvas transcribe /path/to/audio.wav --output /path/to/transcript
python -m autocanvas slides /path/to/video.mp4 --output /path/to/slides

# 同一课次的 ASR 和 Slides 独立记录结果。
python -m autocanvas process 10001 20001 --kind both
python -m autocanvas process 10001 20001 --kind vod_asr
python -m autocanvas process 10001 20001 --kind vod_slides --view 5
python -m autocanvas process 10001 20001 --kind vod_asr --retry

# 短片段验证写入 samples，不会把完整课次标为已完成。
python -m autocanvas process 10001 20001 --duration 30
```

`--view` 显式覆盖选流。默认 ASR 选择有声来源；Slides 优先分辨率，再使用较低码率作为静态屏幕的启发式。不会硬编码 view 1/5 的含义。

操作同一 runtime 的服务和处理 CLI 使用进程锁，防止双重模型加载与执行。服务运行时使用 HTTP 提交请求。纯本地处理无需常驻服务。

## 自动化与直播

服务启动后同步课程、视频、作业；课表默认每周更新，视频与作业每小时更新，调度每分钟检查。新回放默认自动转写、抽取 Slides，二者独立排队、独立重试。单个课程没有录播映射不影响其作业同步或其他课程。

直播课次到开始前十分钟时创建一个独立监听协程。协程获取最新地址、启动 ffmpeg、持续接收音频，将音频放入有界队列交给 ASR，保存转写并检测关键词。断流后重新获取地址并连接；未开播持续重试至课程结束。队列满时丢弃最老待识别块并记录音频缺口。ASR 单实例串行执行，直播块优先于待处理回放块，正在推理的块不会被强行中断。

到结束时间停止拉流并消化已收音频。手动停止或服务退出时最多额外等待 30 秒消化队列，未处理部分记录为缺口。ffmpeg 始终回收。直播结束安排回放检查，回放生成延迟由后续小时同步补齐。

重启后，中断执行恢复为待处理；回放从已保存音频时间位置继续，Slides 重新生成后替换结果。仍在上课的直播重新连接，已结束的监听记录标为 expired 并触发回放同步。显式取消不会被周期同步重新启动，需显式重试。

自动化暂停只停止新的自动派发，正在执行的任务继续；取消正在执行的任务需使用取消接口。人工请求可在暂停期间执行。

直播目前按官方前端 `liveDay=0` 查询可见课次；真实直播流仍需在有正在直播的课程时验收。不能据此保证尚未被平台返回的未来课次会提前十分钟发现。接口与限制详见 [验证记录](docs/validation.md)。

## HTTP

所有 `/api` 端点都是明确功能入口；耗时请求返回 `202` 和 `execution_id`。

| 方法与路径 | 功能 |
| --- | --- |
| `GET /health` | 服务、鉴权阻塞、后台循环健康状态 |
| `GET /api/courses`、`/api/lectures`、`/api/assignments`、`/api/sync` | 已同步的数据与同步状态，可用 `?course_id=` 过滤 |
| `POST /api/sync` | 同步全部或 `{"course_id":"10001"}` 指定课程，并安排作业同步 |
| `POST /api/process/vod_asr`、`vod_slides`、`live` | 输入 course_id、lecture_id，可选 view、retry |
| `GET /api/executions`、`/api/executions/{id}` | 查看结果、错误类型、产物位置 |
| `POST /api/executions/{id}/cancel`、`retry` | 取消、重试 |
| `GET/POST /api/automation` | 查看或设置 `{"paused":true}` |
| `GET/PATCH /api/courses/{id}/rules` | 按课程覆盖 `asr`、`slides`、`live` 开关 |

```sh
curl http://127.0.0.1:8080/health
curl -X POST http://127.0.0.1:8080/api/process/vod_slides \
  -H 'Content-Type: application/json' \
  -d '{"course_id":"10001","lecture_id":"20001"}'
```

不存在上传代码、动态注册任务或操作模型内部状态的接口。重新登录后会自动恢复符合条件的 `needs_login` 执行，也可手动 retry；常驻进程不会等待交互式密码或验证码。

## 数据与验证

`runtime/state.sqlite3` 是唯一执行状态库；`auth/` 保存私有会话，`assignments/` 保存作业，`outputs/<course>/<lecture>/` 保存转写和 Slides，`cache/` 保存中间画面，`logs/` 保存轮转服务日志。完整媒体地址和令牌不写入数据库、转写头或日志。

转写有断点 JSONL、最终 JSON 和 TXT；Slides 有图片、时间清单与联系表；直播有连接、缺口、关键词事件 JSONL。Slides 完成后自动检测二维码并保存独立 qr.json；WebUI「点名与二维码」按课程展示直播关键词与回放二维码记录。日志与产物含个人课程数据，保留在个人目录。向 Public 发布时仅复制源码、测试、文档和配置模板，另行脱敏。

```sh
python -m unittest discover -s tests -v
```

测试覆盖模块依赖边界、SQLite 领取防重、暂停/取消/重启、鉴权故障隔离、直播断流/满载/清理、HTTP 控制和真实 ffmpeg 本地处理。架构说明见 [模块边界](docs/architecture.md)。

## 独立 WebUI

前端在 `webui/`，使用 Vue 3 + TypeScript + Vite + Element Plus。先启动 `python -m autocanvas serve`，再在另一终端执行：

```sh
cd webui
npm ci
npm run dev
```

访问 http://127.0.0.1:5173 。支持全部配置、登录、待执行课程、历史执行记录、按课程组织的图片墙与转写阅读、作业附件及日志。前端仅通过 REST / SSE 调用后端，关闭页面不影响处理流程。

详见 [WebUI 启动与部署](webui/README.md) 和 [API 说明](docs/web-api.md)。

## 作业列表与 Codex 会话

WebUI「作业」只提供 Canvas 作业列表和独立的 Codex 会话列表。支持新建、查看、续聊、切换模型与停止执行。通过现有密码入口直接调用本机 `codex exec`，无需另外启动 Homework 服务。

会话工作目录由页面中的「工作目录」设置，初始沿用 `homework.yml` 的 `workspace`。新建和续聊都直接使用该目录及其中的 AGENTS.md；不按课程或作业创建子目录，不生成资料快照，不绑定作业。模型留空沿用本机 Codex 默认配置，也可以手动输入模型名称。目录下的本机 Codex 历史会话会出现在列表中，已有包装器会话记录也保留。页面关闭不停止执行；重启密码入口会中断其正在运行的 CLI。

「点名与二维码」页面现支持直播自动签到：设置课程范围后独立观察实时画面，使用最新解码结果提交，并显示耗时与学校响应。直播扫码优先 HTTP-FLV、失败回退 HLS；回放二维码不会触发签到。结果不确定或学校要求定位时需在交我办核对 / 完成，不自动重复提交。

## 访问密码与 cloudflared

当前 WebUI 的 4173 端口由独立密码网关提供。访问密码见本地 `dashboard.yml` 的 `password` 字段，修改后重启网关。未登录只加载密码页，所有业务 API、资源与实时流均受保护。cloudflared 指向 `http://127.0.0.1:4173`；不要将内部 8080 / 8090 或开发端口映射到外网。详见 [部署与会话管理](dashboard_gateway/README.md)。

## 公开源码与本地配置

仓库仅包含源码、测试和示例配置。使用前将 `dashboard.example.yml` 复制为 `dashboard.yml` 并设置自己的访问密码；将 `homework.example.yml` 复制为 `homework.yml` 并填写已有工作目录。凭据、Cookie、模型会话、课程数据、媒体和产物保存在本地，均不应提交。文档中的课程和课次编号均为示例。
