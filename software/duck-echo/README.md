# 鸭子本地语音与听音乐

新版默认单击手柄右摇杆按钮 **R3** 开启监听，短“嘎”一声；松手说话，再次单击关闭，低“咕”一声。网页开关采用同样提示，只在实际切换时叫一次；Select、断开和服务退出等自动取消保持安静。默认 `ECHO_REPLY_MODE=duck`：已启用声控时执行明确坐站口令，音乐模式回答明确音乐问题；普通或未识别的话语保持安静，不复读人的声音，也不提供自由聊天。关闭会取消待处理的识别并停止录音/复读，再播放关闭提示。

提示声使用安装音效库的 WAV 时长，再加 100 毫秒启动余量和 350 毫秒尾音，期间暂停收音；提示声结束后稍停再说话。每次服务启动仍默认关闭监听和听音乐。

录音回放保留为单独的 `record` 命令，暂不绑定新按钮。明确需要旧复读行为时，可设置 `ECHO_REPLY_MODE=echo` 或传 `--reply-mode echo`；此时默认音调和语速提高到 1.25 倍，设为 1.0 可恢复原声。

本轮语音和网页音乐面板已通过SSH上机，分句问题经真人复测恢复。外放音乐声学和身体动作效果仍待真人验收，软件测试不代替这两项。详见 [本轮验证](../../docs/superpowers/verification/2026-10-25-audio-interaction.md)。下文初版历史验收对应按住说话的版本。

这是独立服务，通过 SSH 安装，沿用主程序的坐立动作接口，不重新烧录或重启 robotd/padd。只读旁听实体手柄，不独占输入设备。声音只放在内存，不保存对话录音文件。

**初版历史验收：2026-10-25 通过 SSH 部署到 duck-0de6（192.168.0.102），设为开机自启动。真实麦克风采集、变调回放、实体手柄 R3 按住/松开跑通。45项软件测试通过；用户随后明确确认真人语音“站起来和坐下都成功”。**

实际手柄为 Nintendo Switch Pro Controller，输入设备 /dev/input/event4。安装前后 robotd PID 1090、padd PID 1128 及启动时间均未变化，控制环保持 50 Hz。已设置服务 enabled，未为此重启整机；已配对手柄开机回连后，复读服务会自动检测。

适用硬件：64 位 Linux、TLV320AIC3104 HAT、已有正常的喇叭播放通路、支持 BTN_THUMBR 的实体手柄。默认使用板载 Mic3R。外接 J2 使用 Line1L，两路可以分别选用或同时混入右 ADC（`--channel 1`）。J2 选项需要声卡提供 `Right PGA Mixer Line1L Switch`。不支持网页模拟手柄的 R3。

## 听音乐与明确提问（已上机，外放与动作效果待验收）

网页可分别控制监听和听音乐；打开听音乐会同时打开麦克风，关闭监听会一起关闭音乐模式。R3 和网页读取同一个实际监听状态。实体手柄关闭时仍可从网页开始收音；真正的手柄断开、Select、输入溢出和服务退出会取消当前输入。

音乐模式复用这一条麦克风流，按短时能量起音及包络重复证据估计节奏和粗略感觉。轻快、舒缓、摇摆只是节奏反馈，不代表准确曲风或情绪识别。音乐不会送去普通复读或坐站口令识别。说“感觉怎么样”“喜欢这种歌吗”等明确问题时，单独的本地流式识别器复用已有 Vosk Model，按近期可靠节奏调用原生 chirp、coo、greet 音效；没有可靠节奏用 inquire。完整问题的逐词置信度至少 0.8；稳定完整 partial 保持 0.5 秒可以在音乐持续时回答，回应间隔至少 4 秒。提示声和尾音期间抑制自身收音。

连续采集请求 20 毫秒 period、80 毫秒 buffer；节拍时间按样本推进，避免驱动成批送达造成节奏失真。重复性估计只保留有限数值包络，每秒最多计算五次，状态读取复用缓存。平滑音乐通常需要数秒重复证据；没有清晰重复、声音停止或采集过期时不生成节拍。稳定读数只是音量包络的周期估计，强弱拍、倍速及混合节奏仍可能存在歧义；不同证据冲突时优先较强支持，不能保证唯一的音乐拍点。

仅 toggle 模式提供私有 `/run/duck-echo/control.sock`（目录 0700、socket 0600）。每连接一条 UTF-8 JSON 行，最多 8192 字节。`{"method":"status"}` 读取状态，`{"method":"set","listening":true,"music":true}` 调整开关，布尔值必须是 JSON true/false。hold 保留旧的按住说话行为。

`{"method":"microphone","source":"external"}` 切换实际输入，source 可选 `onboard`、`external`、`both`。切换保留监听及音乐开关，丢弃旧输入的待处理语音。状态中的 `microphone_source` 表示已确认的路由，`microphone_level_dbfs` 表示右 ADC 当前窗口去直流后的 RMS 电平，显示下限为 −96 dBFS。无法确认路由、没有新鲜电平时，对应字段为 null。Studio 把用户选择保存在 `/var/lib/microduck-studio/microphone-source.json`。服务启动后按该选择恢复路由，监听初始为关闭。

音乐问答需要已安装本地识别模型。模型未就绪会在状态里显示音乐识别故障；单独的录音回放及显式 echo 模式仍可使用。软件假 ALSA/UNIX socket 测试不等于真实外放音乐、混响和提问识别已通过验收。

## 中文口令模式

本地声控模式：说“坐下”“坐下来”“站起来”“起立”等明确短口令时执行对应动作；普通话语默认不回放。“不要坐下”、多个动作混在一句里、低置信度识别不会触发动作。按 Select、手柄断线或输入溢出会取消未执行的旧口令并关闭持续监听。

已经在鸭子 CPU 上通过 14 条合成测试语音，覆盖正向口令、否定、引用、混合动作和普通话语，识别约 0.26–0.51 秒，峰值内存约 144 MB。使用含未知语音及否定形式的小词表，避免完整听写的 6–7 秒延迟。用户随后使用实际麦克风与 R3 测试，并确认站起和坐下均成功。

验收后曾出现 SSH 与网页均不可达。后续经蓝牙配网恢复连接，将这台鸭子固定到 TP-LINK_5G_6494（5240 MHz），关闭其他已保存网络的自动连接及 Wi-Fi 省电。网页连续 20 次访问均成功。语音关闭约 160 秒、开启约 180 秒的蓝牙记录均无断链，手柄数据最大间隔均不足 49 毫秒；开启期间完成真人复读、坐下与站起，已取得两次动作 accepted=true 的日志。用户确认复读和摇杆均正常；另一次看到灯闪时，动作也正常，记录中没有对应断链。此前日志确有真实掉线，尚未确定其原因，短时复测不能证明长期间歇问题彻底消失。详见 docs/network-20261025.md 与本地 artifacts/duck-network-20261025/final-verification.json。

使用 Vosk small-cn-0.22，模型和依赖放在 /opt/duck-echo 内，不上传语音。完整录音不写入文件，日志只记口令类别、置信度和动作回执，不记普通对话文本。每次会先读当前坐立状态，重复“坐下”不会反向站起；主程序拒绝姿态请求时不绕过。坐着卸力后的恢复仍遵循现有固件要求。

单击模式在说话停顿后处理一句，最长 10 秒；识别结果超过 6 秒不再执行。自己复读和动作提示声期间会抑制收音，尾音结束后继续监听。普通自动分句适合安静环境，持续外放音乐时应开启音乐模式。

需要恢复旧版按住说话时，在 `/etc/default/duck-echo` 中设置 `ECHO_TRIGGER_MODE=hold`，然后只重启 `duck-echo` 服务。hold 模式仍在松开 R3 后处理；声控录满 10 秒时取消这一句。

```bash
# 先更新运行文件，再准备本地识别依赖和模型。离线安装可提供 ARM64/cp313 wheels。
sudo python3 /home/radxa/duck-echo/install.py
sudo python3 /opt/duck-echo/install_speech.py --mode actions \
  --archive /home/radxa/duck-echo/vosk-model-small-cn-0.22.zip \
  --wheels /home/radxa/duck-echo/arm64-wheels

# 观察识别，暂不执行动作：
sudo python3 /opt/duck-echo/install_speech.py --mode shadow

# 关闭口令识别（需要复读时另设 ECHO_REPLY_MODE=echo）：
sudo python3 /opt/duck-echo/install_speech.py --mode off
```

来源：[Vosk 官方模型](https://alphacephei.com/vosk/models)、[ARM64 安装支持](https://alphacephei.com/vosk/install)。

## SSH 上机

将本文件夹上传到鸭子的 `/home/radxa/duck-echo`。在鸭子上执行：

```bash
cd /home/radxa/duck-echo
arecord -l
python3 duck_echo.py devices
sudo python3 install.py prepare-mic
python3 duck_echo.py probe --seconds 3
python3 duck_echo.py record --seconds 4
sudo python3 install.py
journalctl -u duck-echo -n 30 --no-pager
```

`probe` 的 3 秒内在麦克风旁说话，输出包含 peak、rms、dbfs；静音/过短/采集失败时退出码为 2。`record` 命令在 4 秒内录音，然后播放；播放进程退出成功才会输出 `playback_ok: true`。进程成功仍需耳朵确认实际音质和喇叭响度。

安装需要 sudo；服务以 radxa 用户运行，并加入 audio/input/robot 附加组。依赖系统 Python 3、alsa-utils 和 systemd，无需下载 Python 包。

## 原声与设备设置

```bash
sudo nano /etc/default/duck-echo
# ECHO_PITCH=1.0 为原声，1.25 为较高较快的声音。
# ECHO_CHANNEL=1 为 Mic3R 右声道；仅硬件确实接左路时改成 0。
sudo systemctl restart duck-echo
```

仅重启复读服务。声卡名不同则修改 ECHO_CAPTURE/ECHO_PLAYBACK；本安装脚本的麦克风设置专用于 aic3104，其他声卡须另按其硬件确认采集通路。

## 诊断与撤回

- 配套新版 padd 存在时，服务读取 `/run/padd/mapping-state.json`，选择 padd 的实际 evdev 节点，即使该手柄没有 Linux 默认 BTN_THUMBR（318）也支持。自定义 R3／Select 按原始按钮或帽轴绑定处理；自定义配置省略 R3 时不回退 318。旧安装从未提供该状态文件时，才沿用第一个支持默认 R3 的手柄。
- 校准暂停会取消现有监听、识别、待执行动作和回放，期间按键只更新基线；配置、设备、进程或暂停状态变化都会重新建立按键基线。启动或恢复时已按住 R3，需要先释放再按。已经读过新版状态后，状态消失／损坏／进程退出时保守停用，直到有效状态恢复。
- 录不到声音：查看 `arecord -l`、probe 电平和 Mic3R 焊接/线路；尚未验证本机硬件，不把静音归因为软件或焊接。
- 播放提示 Device busy：松开原有发声扳机后再试。原生音效与复读可能竞争硬件播放通路；本版会报错，不会停止 robotd 或篡改全局 ALSA 播放配置。
- 变调同时改变语速；本版支持按键后识别坐立短口令，不含连续唤醒、自由对话和保持语速的变声。

```bash
sudo systemctl stop duck-echo
sudo python3 /opt/duck-echo/install.py uninstall
```

卸载会关闭复读服务并恢复安装前的麦克风采集设置。程序和备份留在 /opt/duck-echo、/var/lib/duck-echo；喇叭音量不受该安装脚本修改。安装或升级失败时会尝试恢复本次修改的文件、采集设置和此前的复读服务状态；恢复失败会明确输出错误。

## 离线验证

```bash
python3 -m unittest discover -v
python3 duck_echo.py --help
```

Linux 集成测试用临时假 arecord/aplay/amixer 程序运行真实子进程，覆盖录放、限时、断线、声卡忙碌、麦克风设置恢复；不代表真机声音已经验收。
