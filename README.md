# igv in vscode

Integrative Genomics Viewer - desktop genome visualization tool for Mac, Windows, and Linux.
从 [igvteam/igv](https://github.com/igvteam/igv) 而来，新增远程渲染模式。

### Remote：在 VS Code 中使用远程 Java IGV

本仓库新增远程渲染模式：**原版 Java IGV 在服务器运行，track 读取、解析和渲染
全部在服务器完成**。本地 VS Code 内嵌 noVNC，只显示画面并发送键盘、鼠标操作。
BAM、CRAM、VCF、BigWig、索引和参考序列不需要复制到本地；网络仍需传输画面更新。
菜单、右键菜单和文件选择弹窗保留原版 IGV 的交互。

```text
本地 VS Code / noVNC ← SSH 画面与操作 → websockify → TigerVNC → Java IGV
                                                   远程虚拟显示   远程数据文件
```

#### Pixi 隔离环境与依赖安装（无需 sudo）

仓库根目录提供 [pixi.toml](pixi.toml)。远程图形运行环境针对
**Ubuntu 20.04/22.04/24.04 x86_64**（含 20.04.3），manifest 平台为 `linux-64`；不需要完整桌面、
物理显示器、GPU、Docker 或 sudo。

服务器先准备 Pixi，参考[Pixi 官方安装指南](https://pixi.sh/latest/installation/)。

`pixi install` 从 conda-forge 安装 Java 21、Python 3.12、websockify、fontconfig、字体以及 GCC/binutils/make 编译工具到项目的 `.pixi/envs/default`。
`pixi run setup-remote` 再准备 TigerVNC、Openbox、PRoot 和 X11/XKB，图形组件解包到同一环境的 `share/igv-remote/root/`。
图形组件使用私有 Ubuntu 软件源和索引；它们不是由 Conda solver 管理的包。
因此完整安装需要下面的 install 和 setup 两个步骤，单独 `pixi install` 还不能启动远程桌面。

setup 从固定的上游源码构建 PRoot 5.4.0，源码按固定的 SHA256 校验。
编译器由 Pixi 提供，`libtalloc-dev` 头文件和运行库在私有 Ubuntu 图形目录内，
无需系统 GCC、开发包或 sudo。源码缓存、编译日志和构建记录位于
`share/igv-remote/cache/proot-v5.4.0.tar.gz` 和 `share/igv-remote/proot/`。
修复后的程序放到私有 `root/usr/bin/proot`，系统 `/usr/bin` 不变；原 Ubuntu
程序备份为 `share/igv-remote/proot/proot.ubuntu`。已安装 VSIX 的原启动路径仍可使用。

图形准备任务需要服务器已有 `apt-cache`、`apt-get`、`dpkg-deb`、Ubuntu 官方
归档签名密钥及 HTTPS 下载能力，不再要求系统 Python、单独的 JDK 或 pip。
不会修改系统软件源、系统服务或防火墙，也不更改用户 shell 配置。
PRoot 只为虚拟显示进程映射 XKB 的固定路径，避免要求系统安装 `/usr/bin/xkbcomp`；
无需 root 或 mount 权限，但服务器须允许普通用户跟踪自己启动的子进程（ptrace）。

在服务器上的仓库根目录执行：

```sh
pixi install
pixi run setup-remote
pixi run doctor
```

Pixi 依赖在项目 `.pixi/` 中；运行配置、日志和 IGV 自动保存文件默认放在
`~/.local/share/igv-remote`，与可重新创建的环境分开。可自定义运行配置目录：

```sh
pixi run setup-remote --prefix "$HOME/apps/igv-remote"
pixi run doctor --prefix "$HOME/apps/igv-remote"
```

Pixi 首次解析依赖会生成 `pixi.lock`，应保留并提交该文件；后续用 `pixi install --locked`
保持版本一致。图形依赖的版本和 SHA256 单独记录在环境内的 `graphics.json`，
重复 setup 会复用已校验文件，并根据 `extracted.json` 跳过已完成的解包；每个包显示
缓存检查、下载、解包或已安装状态。中途失败可重新运行并继续未完成的包；旧安装
根据已完成的 `graphics.json` 自动迁移，不要求重新下载全部组件。
PRoot 构建同样缓存并校验，重复 setup 不再编译。`runtime.json` 记录真实的 Pixi Python/Java 和图形路径，
插件因此无需先执行 `pixi shell`；不会偷偷切换到系统 Python 或其他 Conda 环境。
移动仓库或重建 Pixi 环境后，应重新执行 `pixi run setup-remote` 更新这些绝对路径。
旧运行目录仍可读取；新 setup 会切换到 Pixi，不自动删除旧依赖和会话文件。
从旧安装方式迁移前先停止旧会话，再重新构建并安装 VSIX，使插件携带新版 Pixi 启动代码。

图形组件不替换系统 glibc，不能跨 Ubuntu 版本复制使用。诊断成功表示组件可加载，
实际图形操作还需要启动会话验收。`remote/install.sh` 保留为 Pixi setup 任务的兼容入口。

#### Ubuntu 20.04 兼容方案与安装指南

| Ubuntu 版本 | 私有 APT 软件源系列 | VNC 密码工具包 |
| --- | --- | --- |
| 20.04（含 20.04.3） | focal / focal-updates / focal-security | tigervnc-common |
| 22.04 | jammy / jammy-updates / jammy-security | tigervnc-tools |
| 24.04 | noble / noble-updates / noble-security | tigervnc-tools |

[Ubuntu Focal 的 TigerVNC 包记录](https://launchpad.net/ubuntu/focal/+source/tigervnc)
提供 TigerVNC 1.10.1 和 `tigervnc-common`，没有独立的 `tigervnc-tools`。
安装器针对这一差异选择工具包，仍使用私有下载、SHA256 校验与解包，不执行系统级安装。

`pixi.toml` 显式声明 glibc 2.31、Linux 内核 4.18 的兼容基线，用于选择可在
20.04 上运行的 Java 21、Python 3.12 等依赖；同一约束也适用于 dev 环境。
[Ubuntu 20.04 的 glibc 为 2.31](https://launchpad.net/ubuntu/focal/+source/glibc)，
[Pixi 的系统要求说明](https://pixi.prefix.dev/v0.66.0/workspace/system_requirements/)
解释了这些约束如何参与依赖解析。这不会安装或替换系统 glibc，
也不应通过 `CONDA_OVERRIDE_GLIBC` 伪造较高系统版本来绕过兼容检查。
图形包仅使用当前 Ubuntu 版本的软件源，不从 22.04/24.04 借用二进制文件。

#### 构建 Java IGV 与 VS Code 插件

先在远程服务器构建本仓库的 IGV：

```sh
pixi run build-igv
```

Pixi 自动设置 `JAVA_HOME`。分发目录为 `build/IGV-dist`，
需要保留其中的 `igv.args` 和整个 `lib/`。默认运行环境不安装 Node.js。

Linux 上可以直接使用 Pixi 的 dev 环境构建插件：

```sh
pixi install -e dev
pixi run -e dev package-extension
```

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
依赖源。Pixi dev 环境管理 Node.js，插件的 npm 依赖仍由 `package.json` 管理；
npm 安装后请保留生成的 `package-lock.json`。

noVNC 固定使用 v1.6.0 上游源码归档，其完整性由 `package-lock.json` 锁定。
1.6.0 的 npm CommonJS 发布包包含无法通过 require 加载的顶层 await，因此
浏览器端改用上游 ESM 源码并打包为 module 脚本；没有降级 noVNC，也不在运行时
下载脚本。首次构建需能访问 GitHub codeload，后续可通过 `npm ci` 重现依赖。

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
- **Java/动态库错误：**运行 `pixi run doctor` 查看失败组件。不要直接使用不同 Ubuntu
  版本的运行目录，也不要把运行脚本的私有 `LD_LIBRARY_PATH` 导出到全局 shell。
- **Pixi 环境不存在或路径过期：**重新运行 `pixi install` 和 `pixi run setup-remote`；
  移动仓库后也需 setup。Java/Python 使用 Pixi 库，Ubuntu 图形进程使用自己的运行库。
- **PRoot/ptrace 被拒绝：**服务器或容器策略限制了子进程跟踪，当前用户目录图形
  运行方式无法启动；需在允许该能力的服务器运行，或调整服务器策略。
- **PRoot 提示 `terminated with signal 31`：**旧版 PRoot 5.1 的 syscall(-2) 可能
  触发外层 seccomp。更新仓库后先运行 `pixi install`，再执行 `pixi run setup-remote`，
  安装器会构建兼容的 PRoot 5.4。`PROOT_NO_SECCOMP=1` 只控制 PRoot 自身的加速，
  不能移除容器的过滤器。本修复不禁用 seccomp，也不赋予额外 capability；
  如果平台连跟踪自己的子进程都不允许，更新 PRoot 仍不能绕过该限制。
- **依赖文件被手动删除或损坏：**执行 `pixi run setup-remote --repair`，从校验后的
  缓存重新解包，再恢复已编译的 PRoot。不要为普通重复安装使用 repair，否则会重复解包。
- **PRoot 编译失败：**先运行 `pixi install` 更新编译工具，查看环境中的
  `share/igv-remote/proot/build.log`。首次 setup 需访问 GitHub codeload 下载固定源码；
  后续复用源码和编译缓存，不依赖系统编译器。
- **黑屏或只显示背景：**检查 `display.log`、`openbox.log`、`igv.log`，确认 Java IGV
  未因内存、字体、配置或外部 genome 下载失败而退出。
- **画面模糊或字符缺失：**关闭“适应窗口”使用原始像素显示，并检查安装器的字体诊断。
- **连接失败：**确认 Remote-SSH 仍连接，查看 VS Code 的 Ports 面板和 WebSocket 日志；
  重新连接命令会重新解析转发地址。不要将原始 VNC 端口设为公开。
- **旧版 TigerVNC 提示 `Unrecognized option: yes`：**更新并重新安装 VSIX。
  会话启动已统一使用 `-localhost=1` 等布尔参数写法，兼容 20.04 的 TigerVNC；
  插件打包了会话脚本，仅更新服务器仓库不会替换旧 VSIX 中的启动脚本。
- **无法启动新会话：**已有会话尚未结束时优先重新连接；停止后等待几秒让进程清理完成。

卸载前先停止所有会话。通过 VS Code 扩展面板卸载插件；依赖目录可先改名保留备份：

```sh
mv "$HOME/.local/share/igv-remote" "$HOME/.local/share/igv-remote.backup"
```

使用自定义目录时替换上面的明确路径。备份包含日志和可能需要恢复的 IGV 自动保存
文件；确认无需保留后再手动删除。停止所有会话后，可在项目根目录执行 `pixi clean`
移除依赖环境；保留 `pixi.toml` 和 `pixi.lock` 以便重建。这些操作不影响服务器上的 track 数据。

#### 开发验证

```sh
pixi run test-remote
pixi run smoke
pixi run -e dev check-extension
```

完整验收还需在 Ubuntu 20.04、22.04、24.04 环境中以普通用户安装并启动，通过 Windows VS Code
验证菜单、弹窗、键鼠、track 显示、断线恢复和超时清理。纯单元测试不能代替该验收。
CI 已加入三个 Ubuntu 版本的 Pixi 安装、无 sudo 图形准备、重复安装、真实 Java IGV 窗口与
WebSocket/VNC 链路检查，以及 VSIX 构建任务；CI 日志需实际运行后确认。
由于 GitHub 已停用 `ubuntu-20.04` 托管 runner，20.04 测试在 `ubuntu:20.04` 容器中进行；
容器初始化仅准备基础系统工具，Pixi 依赖安装、图形准备、构建和 smoke 均由普通用户执行。
容器仅用于 CI，不是服务器部署要求；容器测试也不能代替真实服务器的 ptrace 策略验收。

2026-10-13 在远程 20.04.3 上验证：19 项远程单元测试、3 项插件测试、TypeScript
检查、Java 分发构建、真实 IGV 窗口/WebSocket smoke 和 VSIX 打包通过。
Java 全量测试共 685 项，645 项通过、38 项跳过，`JsonGenomeLoaderTest.loadGenome`
及 `FileFormatUtilsTest.testDetermineFormat` 两项因网络访问超时失败，不能标为全量通过。

### Old igv Building Instructions

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



 
