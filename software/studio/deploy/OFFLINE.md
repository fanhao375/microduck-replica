# 没有 WiFi 时怎么用（户外 / 离线）

户外、展会、地下室这些没有家用路由器的地方，鸭子的两条控制路径都不需要互联网：

- **手机说话**：鸭子自己开一个 2.4 GHz 热点，手机连上去，录音发给鸭子，由板载 Vosk 识别。不联网。
- **实体手柄**：蓝牙直连鸭子板子，完全本地，跟 WiFi 无关。

两条路互不依赖，可以同时用。下面分开说。

---

## 一、手机说话（鸭子开热点）

手机麦克风只在**安全上下文**（HTTPS）里才给浏览器用，所以这条路的地址是 `https://10.42.0.1:8000/m`。证书由鸭子第一次开机时自己生成（局部 CA + 服务器证书），位置：

```
/etc/microduck/tls/ca.crt        # 让手机信任的证书（可以下载）
/etc/microduck/tls/duck.crt      # 鸭子自己的 HTTPS 证书
/etc/microduck/tls/duck.key      # 私钥，只有板子自己用
```

### 第一次准备

1. **先设网页登录密码**。热点密码 `microduck` 是公开的默认值，同一热点下的人都能打开 Studio。
   在 Studio 的「更多 → 登录密码」里设置；没有密码时不要在外面演示。
2. 手机连上鸭子的热点：
   - WiFi 列表里找到 `Microduck-xxxx`（xxxx 是鸭子的 4 位名字），密码 `microduck`；
   - 手机问「此网络无法上网」时选**保持连接**，并临时**关掉移动数据**（有的安卓手机会绕开没网的 WiFi）。
3. 手机浏览器打开 **https://10.42.0.1:8000/m** 。
   - 第一次会提示证书不受信任，这是正常的：接着做第 4 步。
4. **下载并信任鸭子自己的 CA**：
   - 在「更多 → 手机麦克风证书」点「下载证书」，得到 `Microduck-Local-CA.crt`。
   - 只信任从自己的鸭子热点或可信连接下载的 CA。有疑问时，在鸭子上运行 `openssl x509 -in /etc/microduck/tls/ca.crt -noout -fingerprint -sha256`，与下载的证书核对 SHA-256 指纹。
   - **iPhone**：点下载 → 设置 → 已下载描述文件 → 安装；
     再到 设置 → 通用 → 关于本机 → **证书信任设置**，把 `Microduck Local CA` 打开。
   - **Android**：设置 → 安全 → 加密与凭据 → 安装证书 → **CA 证书**，选刚下载的文件。
   - 装完刷新本页。
5. 回到「更多 → 语音方式」，选「自动」或「手机麦克风」，按住「手机说话」就能控制小鸭子。

以后换一台手机要重新装一次 CA；板载麦克风、实体手柄都不需要证书。

> 已经有鸭子、但证书是旧的（只有一张自签 leaf、没有 `ca.crt`）的，在板子上运行
> `sudo microduck-firstboot --tls-only` 重新生成，旧证书会自动备份成 `*.bak-<时间戳>`，
> 然后 `sudo systemctl restart microduck-studio`。Studio 发现 `duck.crt` / `duck.key` 在就会自动用 HTTPS 起。
> 还没启用 HTTPS 的设备仍可用原来的 HTTP 地址打开控制台。手机麦克风需要先生成并信任证书。

### 从热点回家里 WiFi

在手机「更多 → 户外」卡片点「关热点，回家里 WiFi」。手机会掉线，这是正常的：

- 鸭子关掉热点后，NetworkManager 会按已保存的配置自动连回家里的 WiFi。
- 手机连回同一个 WiFi 后，打开 `https://duck-xxxx.local:8000/m` 或 `https://duck-xxxx:8000/m`。出厂证书覆盖这两个主机名和热点固定地址。
- 尚未启用 HTTPS 的设备继续用 `http://duck-xxxx.local:8000/` 或 `http://duck-xxxx:8000/`。

如果鸭子没连回家里 WiFi，热点一般会自己回来（马上到 2 分钟左右），连回 `Microduck-xxxx` 看卡片上的红字原因。

---

## 二、实体手柄（蓝牙）

实体手柄不经 WiFi，也不经 Studio：

1. 在 Studio 的手柄页按向导配对，或者在板子上用官方工具：`robotctl pad pair`。
2. 配对后 `bluetoothctl trust <手柄地址>`，以后开机会自动回连。
3. 手柄的按键由 `padd` 读 `/dev/input/event*`，再走 `/run/robotd.sock`，全程在板子上。

没有家庭 WiFi 时不用改任何东西，配对过的手柄照常开机回连。

手机上那个虚拟手柄是另一条路径：它走 WiFi / Studio，需要手机和鸭子在同一个网络（热点或家里 WiFi 都行）。

---

## 三、排障

- **浏览器提示证书不受信任**：CA 还没装，或者装完没刷新。按上面第 4 步重装，iPhone 记得再去「证书信任设置」打开。
- **「手机说话」按钮是灰的 / 不给麦克风**：确认地址是 `https://` 不是 `http://`；确认 CA 已信任；iOS 需要在弹窗里允许麦克风。
- **手机总走蜂窝、打不开鸭子页面**：关掉移动数据，或者在该 WiFi 的详情里选「保持连接」/关掉「自动切换网络」。
- **开关热点很慢 / 手机掉线后连不上**：热点起落要几秒到几十秒；先等 10 秒再找 `Microduck-xxxx`。切回家里 WiFi 时鸭子也要重新连，慢的话等 2 分钟。
- **热点和蓝牙一起用时手柄偶尔卡**：热点、蓝牙、电机都在 2.4 GHz，强干扰下需要拉开距离或换个位置；这是实机环境问题，不是配置错。
- **热点起来了但页面打不开**：确认手机连的是 `Microduck-xxxx`，地址是 `https://10.42.0.1:8000/m`（端口 `8000` 不能少）。

---

## 四、相关命令（板子上，radxa 用户加 sudo）

```sh
sudo microduck-firstboot --tls-only              # 只生成 / 迁移 HTTPS 证书
sudo microduck-firstboot --tls-only --force      # 强制重建（旧证书自动备份）
sudo systemctl restart microduck-studio          # 让 Studio 重新读证书
openssl x509 -in /etc/microduck/tls/duck.crt -noout -subject -ext subjectAltName
openssl verify -CAfile /etc/microduck/tls/ca.crt /etc/microduck/tls/duck.crt
```

服务器证书的 SAN 覆盖：`duck-xxxx`、`duck-xxxx.local`、`localhost`、`10.42.0.1`、`127.0.0.1`。
