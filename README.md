# igv

![Build Status](https://github.com/igvteam/igv/actions/workflows/gradle.yml/badge.svg)
![GitHub issues](https://img.shields.io/github/issues/igvteam/igv)
![GitHub closed issues](https://img.shields.io/github/issues-closed/igvteam/igv)
![](https://img.shields.io/npm/l/igv.svg)

Integrative Genomics Viewer - desktop genome visualization tool for Mac, Windows, and Linux.

### Remote：在 VS Code 中使用远程 Java IGV

本仓库新增远程渲染模式：**原版 Java IGV 在服务器运行，track 读取、解析和渲染
全部在服务器完成**。本地 VS Code 内嵌 noVNC，只显示画面并发送键盘、鼠标操作。
BAM、CRAM、VCF、BigWig、索引和参考序列不需要复制到本地；网络仍需传输画面更新。
菜单、右键菜单和文件选择弹窗保留原版 IGV 的交互。

```text
本地 VS Code / noVNC ← SSH 画面与操作 → websockify → TigerVNC → Java IGV
                                                   远程虚拟显示   远程数据文件
```

#### 支持环境与依赖安装（无需 sudo）

首版安装器针对 **Ubuntu 22.04/24.04 x86_64**；不需要完整桌面、物理显示器、GPU
或 Docker。服务器需要已有的基础工具 `python3`、`apt-cache`、`apt-get`、`dpkg-deb`，
Ubuntu 官方归档签名密钥及 HTTPS 下载能力。安装器在用户目录创建独立的 APT
软件源和索引，包含 Main/Universe，不修改系统软件源、不执行 `apt install` 或 sudo。

安装器下载并校验 Java 21、TigerVNC、Openbox、PRoot、X11/XKB、字体和 Python/websockify。
Ubuntu 软件包用 `dpkg-deb --extract` 解包到用户目录，Python 包安装到专用环境。
运行路径只对远程子进程生效，不更改 `.bashrc`、系统 PATH、系统服务或防火墙。
PRoot 只为虚拟显示进程映射 XKB 的固定路径，避免要求系统安装 `/usr/bin/xkbcomp`；
无需 root 或 mount 权限，但服务器须允许普通用户跟踪自己启动的子进程（ptrace）。

在服务器上的仓库根目录执行：

```sh
sh remote/install.sh
python3 remote/session.py diagnose
```

默认目录为 `~/.local/share/igv-remote`，也可以选择其他专用、可写目录：

```sh
sh remote/install.sh --prefix "$HOME/apps/igv-remote"
python3 remote/session.py diagnose --prefix "$HOME/apps/igv-remote"
```

`runtime.json` 记录依赖版本和校验信息，`requirements.lock` 锁定 Python 依赖；
重复安装复用已校验的下载文件和已锁定版本。目录下的 `root/`、`jdk/`、`venv/`
和 `python/` 是私有依赖，`run/` 保存各会话日志、IGV 配置和自动保存文件。
安装器不替换系统 glibc；不能把同一运行目录直接复制到不同版本的 Ubuntu。
诊断成功仅表示组件能加载，实际图形操作还需要启动会话验收。

#### 构建 Java IGV 与 VS Code 插件

先在远程服务器构建本仓库的 IGV：

```sh
export JAVA_HOME="$HOME/.local/share/igv-remote/jdk"
export PATH="$JAVA_HOME/bin:$PATH"
sh gradlew createDist
```

使用自定义安装目录时相应调整 `JAVA_HOME`。分发目录为 `build/IGV-dist`，
需要保留其中的 `igv.args` 和整个 `lib/`。服务器运行时不需要 Node.js。

在有 **Node.js 20+** 的开发机器上构建 VSIX（本地或远端均可）：

```sh
cd vscode
npm install
npm run check
npm test
npm run package
```

Windows 使用 `npm.cmd`。生成 `vscode/igv-remote-0.1.0.vsix`；构建会将会话管理程序
和 noVNC 打包进插件，运行时不从 CDN 加载脚本。首次构建需要访问 npm 和 Gradle
依赖源。本项目源代码依赖版本固定，npm 安装后请保留生成的 `package-lock.json`。

#### 安装与连接指南

1. 在本地 VS Code 安装 Microsoft Remote-SSH，配置 SSH 主机并连接服务器。
2. 在连接后的远程窗口打开数据目录或项目目录，通过扩展面板的 **Install from VSIX**
   安装 VSIX，确认 IGV Remote 安装在 **SSH 远端**。
3. 在 **Remote Settings** 配置以下路径，`igvDirectory` 必须是服务器上的绝对路径：

```json
{
  "igvRemote.runtimePrefix": "~/.local/share/igv-remote",
  "igvRemote.igvDirectory": "/home/your-user/igv_vscode/build/IGV-dist",
  "igvRemote.memory": "4g"
}
```

4. 命令面板执行 **IGV Remote: 启动远程 IGV**。插件启动独立虚拟显示、Openbox、
   Java IGV 和 WebSocket 桥接，并复用 Remote-SSH 转发，无需手工开放服务器端口。
5. 在 IGV 中通过 **File > Load from File** 选择服务器上的 track；使用原版 IGV
   的 Genomes 菜单选择参考序列。文件选择弹窗访问的是远程服务器。

#### Remote 使用说明

- **数据与索引：**格式能力沿用原版 Java IGV。BAM 应有对应 BAI/CSI，CRAM 应有
  CRAI 及匹配的参考序列，压缩 VCF 按原版要求提供索引。缺索引、路径权限及
  参考序列错误会在远程 IGV 中显示。首轮验收重点使用 BAM+BAI、BigWig、VCF.gz+TBI。
- **显示与输入：**初始虚拟屏幕为 1600×900，默认随视图调整远程分辨率；工具栏可
  关闭自动调整或启用“适应窗口”。点击画面后输入坐标、缩放、拖动或打开右键菜单。
  VS Code/系统保留的快捷键可能被本地拦截，可使用 IGV 菜单执行同一操作。
- **复制粘贴：**工具栏“粘贴到远程”读取本地剪贴板并发送文本；“复制远程文本”
  将远程选择复制后的文本写到本地剪贴板。不会自动同步剪贴板，不传输文件。
- **保存与关闭：**先通过 **File > Save Session** 保存到服务器，再点击“保存后关闭”
  或执行停止命令。关闭视图会提示结束会话或重新打开保存。
- **断线恢复：**意外断线、关闭整个 VS Code 或重载扩展后，远程会话最多保留十分钟；
  重新连接相同服务器与工作区后执行 **IGV Remote: 重新连接**。十分钟无心跳时
  自动结束进程，未保存的界面状态可能丢失；已有数据文件和保存的会话不会删除。
- **资源和隔离：**每个窗口使用独立会话，默认 Java 最大堆内存 4 GiB；大型数据可
  调整 `igvRemote.memory`。图形、WebSocket 和管理接口只监听回环地址，并使用
  会话凭据。额外 IGV 命令监听端口在本远程模式下也限定为回环地址。
- **日志：**执行 **IGV Remote: 查看日志**，或查看安装目录 `run/<会话ID>/` 中
  `manager.log`、`display.log`、`openbox.log`、`igv.log`、`websocket.log`。
  会话关闭后保留日志和 IGV 自动保存文件，便于恢复与排障。

#### 常见问题与卸载

- **缺少 APT 候选版本：**确认系统是支持的 Ubuntu，能够访问 Ubuntu 官方归档且
  `/usr/share/keyrings/ubuntu-archive-keyring.gpg` 存在；私有 APT 索引在安装目录 `apt/` 中。
- **Java/动态库错误：**运行 `diagnose` 查看失败组件。不要直接使用不同 Ubuntu
  版本的运行目录，也不要把运行脚本的私有 `LD_LIBRARY_PATH` 导出到全局 shell。
- **PRoot/ptrace 被拒绝：**服务器或容器策略限制了子进程跟踪，当前用户目录图形
  运行方式无法启动；需在允许该能力的服务器运行，或调整服务器策略。
- **黑屏或只显示背景：**检查 `display.log`、`openbox.log`、`igv.log`，确认 Java IGV
  未因内存、字体、配置或外部 genome 下载失败而退出。
- **画面模糊或字符缺失：**关闭“适应窗口”使用原始像素显示，并检查安装器的字体诊断。
- **连接失败：**确认 Remote-SSH 仍连接，查看 VS Code 的 Ports 面板和 WebSocket 日志；
  重新连接命令会重新解析转发地址。不要将原始 VNC 端口设为公开。
- **无法启动新会话：**已有会话尚未结束时优先重新连接；停止后等待几秒让进程清理完成。

卸载前先停止所有会话。通过 VS Code 扩展面板卸载插件；依赖目录可先改名保留备份：

```sh
mv "$HOME/.local/share/igv-remote" "$HOME/.local/share/igv-remote.backup"
```

使用自定义目录时替换上面的明确路径。备份包含日志和可能需要恢复的 IGV 自动保存
文件；确认无需保留后再手动删除。此操作不影响系统依赖或服务器上的 track 数据。

#### 开发验证

```sh
python3 -m unittest discover -s remote/test -v
python3 remote/test/smoke.py --igv-dir build/IGV-dist
cd vscode
npm test
npm run check
```

完整验收还需在两种 Ubuntu 环境中以普通用户安装并启动，通过 Windows VS Code
验证菜单、弹窗、键鼠、track 显示、断线恢复和超时清理。纯单元测试不能代替该验收。
CI 已加入两种 Ubuntu 的无 sudo 安装、重复安装、真实 Java IGV 窗口与
WebSocket/VNC 链路检查，以及 VSIX 构建任务；CI 日志需实际运行后确认。

### Building

These instructions are meant for developers interested in working on the IGV code. For normal use,
we recommend the pre-built releases available
at [http://software.broadinstitute.org/software/igv/download](http://software.broadinstitute.org/software/igv/download).

Builds are executed from the IGV project directory. Files will be created in the 'build' subdirectory.

IGV requires **Java 21** to build and run. Later versions of Java should work but we build and test on **Java 21**.

NOTE: If on a Windows platform use ```./gradlew.bat``` in the instructions below

#### Folder structure and build targets

The IGV bundles ship with embedded JREs from AdoptOpenJDK.

* Install Gradle for your platform. See https://gradle.org/ for details.

* Use ```./gradlew createDist``` to build a distribution directory (found in ```build/IGV-dist```) containing
  the igv.jar and its required runtime third-party dependencies as well as helper scripts for launching.

    * Launch IGV with `igv.sh` or `igv_hidpi.sh` on Linux, `igv.command` on Mac, and `igv.bat` on Windows.

    * To run igvtools from the command line use the script `igvtools` on Linux and Mac, or igvtools.bat
      on Windows. See the instructions in igvtools_readme.txt in that directory.

    * The launcher scripts expect this folder structure in order to run IGV.

* Use ```./gradlew test``` to run the test suite. See 'src/test/README.txt' for more information about running
  the tests.

* See this [README](https://raw.githubusercontent.com/igvteam/igv/master/scripts/readme.txt) for tips about using the
  IGV launcher scripts.

* This dashboard describes [project structure and dependencies](https://sourcespy.com/github/igvteamigv/).

Note that Gradle creates a number of other subdirectories in 'build'. These can be safely ignored.

#### Amazon Web Services support

Public data files hosted in Amazon S3 buckets can be loaded into IGV
using [https endpoints](https://docs.aws.amazon.com/AmazonS3/latest/dev/UsingBucket.html).

Authenticated access using s3:// urls is supported by either (1) enabling OAuth access with Cognito using the UMCCR
contributed AWS configuration option, or (2) setting AWS credentials and region information as described
[here]( https://docs.aws.amazon.com/sdk-for-java/v1/developer-guide/credentials.html) and
[here](https://docs.aws.amazon.com/sdk-for-java/v1/developer-guide/java-dg-region-selection.html).

For more details on using Cognito for OAuth access, see
the [UMCCR documentation on the backend](https://umccr.org/blog/igv-amazon-backend-setup/)
and [frontend for a provisioning URL step by step guide](https://umccr.org/blog/igv-amazon-frontend-setup/).

### More info

The `CLAUDE.md` file provides guidance to Claude Code (claude.ai/code) when working with code in this repository and
contains useful general information about the app, including build and run commands, architecture, and configuration.



 
