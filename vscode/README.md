# IGV Remote — Java Desktop

本插件在 **Remote-SSH 的远端扩展宿主**运行，将远程原版 Java IGV 的画面显示在
本地 VS Code 中。track 读取、解析与渲染都在服务器；本地仅接收画面并发送操作。

完整的无 sudo 依赖安装、构建、配置和排障步骤见仓库根目录 README 的
“Remote：在 VS Code 中使用远程 Java IGV”章节。

支持 Ubuntu 22.04/24.04 x86_64。在服务器仓库根目录使用 Pixi 准备依赖：

```sh
pixi install
pixi run setup-remote
pixi run build-igv
pixi run doctor
```

`pixi install` 管理 Java/Python/websockify；`setup-remote` 在 Pixi 环境内准备
TigerVNC/Openbox 等私有图形组件，无需 sudo。随后设置：

```json
{
  "igvRemote.runtimePrefix": "~/.local/share/igv-remote",
  "igvRemote.igvDirectory": "/home/your-user/igv_vscode/build/IGV-dist",
  "igvRemote.memory": "4g"
}
```

运行 `IGV Remote: 启动远程 IGV`。通过原版 IGV 的菜单打开服务器上的文件。
主动停止前使用 **File > Save Session** 保存；意外断线后可在十分钟内重新连接。
关闭整个 VS Code 窗口时无法显示保存提示，会话保留至十分钟心跳超时。

画面工具栏支持远程分辨率调整、适应窗口、显式复制粘贴及保存后关闭。
浏览器/VS Code 保留的快捷键可能不会传到服务器，请使用 IGV 菜单执行相应操作。

Linux 上可在仓库根目录使用 Pixi 的 dev 环境构建插件：

```sh
pixi install -e dev
pixi run -e dev package-extension
```

或在本地 `vscode` 目录使用 Node.js 20+：

```sh
npm install
npm run check
npm test
npm run package
```

noVNC 的许可证随构建产物附带；插件本身遵循仓库 MIT 许可证。
