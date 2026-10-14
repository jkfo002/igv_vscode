import * as vscode from 'vscode';
import { execFile } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import * as http from 'node:http';
import * as os from 'node:os';
import * as path from 'node:path';
import { promisify } from 'node:util';
const { websocketUrl, htmlEscape } = require('./protocol.cjs');

const execute = promisify(execFile);
interface Session {
  sessionId: string;
  directory: string;
  token: string;
  vncPassword: string;
  controlPort: number;
  websocketPort: number;
  state: string;
}

let instance: RemoteViewer | undefined;

function expand(input: string): string {
  return path.resolve(input === '~' ? os.homedir() : input.startsWith('~/')
    ? path.join(os.homedir(), input.slice(2)) : input);
}

function api(session: Session, route: string, method = 'GET'): Promise<any> {
  return new Promise((resolve, reject) => {
    const request = http.request({ hostname: '127.0.0.1', port: session.controlPort,
      path: route, method, headers: { Authorization: `Bearer ${session.token}` },
      timeout: 5000 }, response => {
      let body = '';
      response.setEncoding('utf8');
      response.on('data', chunk => { body += chunk; });
      response.on('end', () => {
        if (response.statusCode !== 200) { reject(new Error(`Session API: ${response.statusCode}`)); return; }
        try { resolve(JSON.parse(body)); } catch (error) { reject(error); }
      });
    });
    request.on('timeout', () => request.destroy(new Error('Session API timed out')));
    request.on('error', reject);
    request.end();
  });
}

class RemoteViewer implements vscode.Disposable {
  private panel?: vscode.WebviewPanel;
  private session?: Session;
  private interval?: NodeJS.Timeout;
  private connected = false;
  private busy = false;
  private disposing = false;
  private clipboard = '';
  private lastDirectory?: string;
  private output = vscode.window.createOutputChannel('IGV Remote');

  constructor(private context: vscode.ExtensionContext) { }

  private requireRemote() {
    if (process.platform !== 'linux' || vscode.env.remoteName !== 'ssh-remote') {
      throw new Error('请先使用 Remote-SSH 连接 Linux 服务器，并在远端安装本插件。');
    }
    if (!vscode.workspace.isTrusted) { throw new Error('请先信任当前远程工作区。'); }
  }

  private secretKey(id: string) { return `igvRemote.${os.hostname()}.${id}`; }

  private async remembered(): Promise<Session | undefined> {
    if (this.session) { return this.session; }
    const id = this.context.workspaceState.get<string>('igvRemote.sessionId');
    if (!id) { return; }
    const json = await this.context.secrets.get(this.secretKey(id));
    if (json) { return JSON.parse(json) as Session; }
  }

  private async forget(session: Session) {
    this.lastDirectory = session.directory;
    await this.context.secrets.delete(this.secretKey(session.sessionId));
    await this.context.workspaceState.update('igvRemote.sessionId', undefined);
    this.session = undefined;
  }

  private async ended(session: Session): Promise<boolean> {
    try {
      const state = JSON.parse(await readFile(path.join(session.directory, 'session.json'), 'utf8'));
      return state.sessionId === session.sessionId && ['stopped', 'failed'].includes(state.state);
    } catch (error: any) {
      if (error.code === 'ENOENT') { return true; }
      throw error;
    }
  }

  private closePanel() {
    this.connected = false;
    if (this.interval) { clearInterval(this.interval); this.interval = undefined; }
    this.disposing = true;
    this.panel?.dispose();
    this.disposing = false;
  }

  async start() {
    this.requireRemote();
    if (this.busy) { return; }
    this.busy = true;
    try {
      const previous = await this.remembered();
      if (previous) {
        try { await api(previous, '/status'); }
        catch {
          // Read the private state, not just a reusable port, before forgetting.
          if (!await this.ended(previous)) {
            throw new Error('已有会话暂时无法连接。请重新连接或检查日志，避免重复启动。');
          }
          this.closePanel();
          await this.forget(previous);
        }
        if (await this.remembered()) { await this.reconnect(); return; }
      }
      const config = vscode.workspace.getConfiguration('igvRemote');
      const prefix = expand(config.get<string>('runtimePrefix')!);
      const igv = config.get<string>('igvDirectory');
      if (!igv) { throw new Error('请设置 igvRemote.igvDirectory 为远程 build/IGV-dist 目录。'); }
      const runtime = JSON.parse(await readFile(path.join(prefix, 'runtime.json'), 'utf8'));
      const owner = randomBytes(16).toString('hex');
      this.lastDirectory = path.join(prefix, 'run', owner);
      const cwd = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? os.homedir();
      const manager = vscode.Uri.joinPath(this.context.extensionUri, 'dist', 'remote', 'session.py').fsPath;
      this.output.appendLine('Starting remote Java IGV…');
      const result = await vscode.window.withProgress({ location: vscode.ProgressLocation.Notification,
        title: '启动远程 Java IGV', cancellable: false }, () => execute(runtime.python, [manager, 'start',
        '--prefix', prefix, '--igv-dir', expand(igv), '--owner', owner, '--cwd', cwd,
        '--memory', config.get<string>('memory', '4g')], { timeout: 100000, maxBuffer: 1024 * 1024 }));
      this.session = JSON.parse(result.stdout.trim()) as Session;
      await this.context.secrets.store(this.secretKey(owner), JSON.stringify(this.session));
      await this.context.workspaceState.update('igvRemote.sessionId', owner);
      this.output.appendLine(`Session logs: ${this.session.directory}`);
      await this.show(this.session);
    } finally { this.busy = false; }
  }

  async reconnect() {
    this.requireRemote();
    const session = await this.remembered();
    if (!session) { throw new Error('没有可恢复的会话，请先启动远程 IGV。'); }
    let status;
    try { status = await api(session, '/status'); }
    catch (error) {
      if (await this.ended(session)) {
        this.closePanel();
        await this.forget(session);
        throw new Error('远程会话已结束。请执行“启动远程 IGV”，再从服务器打开已保存的会话。');
      }
      throw error;
    }
    if (status.sessionId !== session.sessionId) { throw new Error('会话标识不匹配。'); }
    this.session = session;
    // Forwarding addresses may change; resolve on every connection.
    await this.show(session);
  }

  private async show(session: Session) {
    const external = await vscode.env.asExternalUri(vscode.Uri.parse(`http://127.0.0.1:${session.websocketPort}/`));
    const url = websocketUrl(external.toString(), session.token);
    this.connected = false;
    if (!this.panel) {
      this.panel = vscode.window.createWebviewPanel('igvRemote', 'IGV Remote — Java', vscode.ViewColumn.Active,
        { enableScripts: true, retainContextWhenHidden: true,
          localResourceRoots: [vscode.Uri.joinPath(this.context.extensionUri, 'dist')] });
      this.panel.onDidDispose(() => {
        this.panel = undefined;
        this.connected = false;
        if (!this.disposing) { void this.onClose().catch(error => this.report(error)); }
      });
      this.panel.webview.onDidReceiveMessage(message => {
        void this.message(message).catch(error => this.report(error));
      });
    }
    const panel = this.panel;
    const nonce = randomBytes(18).toString('base64');
    const script = panel.webview.asWebviewUri(vscode.Uri.joinPath(this.context.extensionUri, 'dist', 'view.js'));
    const css = panel.webview.asWebviewUri(vscode.Uri.joinPath(this.context.extensionUri, 'dist', 'view.css'));
    const origin = new URL(url).origin;
    panel.webview.html = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
      <meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'nonce-${nonce}';
        style-src ${panel.webview.cspSource}; connect-src ${htmlEscape(origin)}; img-src data: blob:;">
      <meta name="viewport" content="width=device-width, initial-scale=1"><link rel="stylesheet" href="${css}">
      </head><body><nav><span id="status">正在连接…</span>
      <button id="reconnect">重新连接</button><button id="resize">调整远程分辨率：开</button>
      <button id="scale">适应窗口：关</button><button id="paste">粘贴到远程</button>
      <button id="copy">复制远程文本</button><button id="stop">保存后关闭</button></nav>
      <div id="screen"></div><script type="module" nonce="${nonce}" src="${script}"></script></body></html>`;
    // Use messaging, not HTML interpolation, to deliver ephemeral credentials.
    this.pending = { url, password: session.vncPassword };
    panel.reveal();
    if (this.interval) { clearInterval(this.interval); }
    this.interval = setInterval(() => {
      if (this.connected && this.session) {
        void api(this.session, '/heartbeat', 'POST').catch(error => {
          this.connected = false;
          void this.panel?.webview.postMessage({ type: 'error', text: '远程连接已中断，请重新连接。' });
          this.report(error);
        });
      }
    }, 15000);
  }

  private pending?: { url: string; password: string };

  private async message(message: any) {
    if (!message || typeof message.type !== 'string') { return; }
    if (message.type === 'ready' && this.pending) {
      await this.panel?.webview.postMessage({ type: 'connect', ...this.pending });
      this.pending = undefined;
    } else if (message.type === 'connected' && this.session) {
      this.connected = true;
      await api(this.session, '/heartbeat', 'POST');
    } else if (message.type === 'disconnected') {
      this.connected = false;
    } else if (message.type === 'reconnect') {
      await this.reconnect();
    } else if (message.type === 'stop') {
      await this.stop();
    } else if (message.type === 'paste') {
      const text = await vscode.env.clipboard.readText();
      if (text.length <= 262144) { await this.panel?.webview.postMessage({ type: 'paste', text }); }
      else { throw new Error('剪贴板文本超过 256 KiB。'); }
    } else if (message.type === 'clipboard' && typeof message.text === 'string' && message.text.length <= 262144) {
      this.clipboard = message.text;
    } else if (message.type === 'copy') {
      await vscode.env.clipboard.writeText(this.clipboard);
    }
  }

  private async onClose() {
    if (!this.session) { return; }
    const action = await vscode.window.showWarningMessage('结束远程 IGV 前，请确认已在 IGV 中保存会话。',
      { modal: true }, '已保存，结束会话', '重新打开并保存');
    if (action === '已保存，结束会话') { await this.stop(false); }
    else { await this.reconnect(); }
  }

  async stop(confirm = true) {
    const session = await this.remembered();
    if (!session) { return; }
    if (confirm) {
      const action = await vscode.window.showWarningMessage('停止会关闭远程 IGV；请先通过 File > Save Session 保存。',
        { modal: true }, '已保存，停止');
      if (!action) { return; }
    }
    try { await api(session, '/stop', 'POST'); }
    catch (error) {
      if (!await this.ended(session)) { throw error; }
    }
    this.closePanel();
    await this.forget(session);
    this.output.appendLine('Remote session stopped. Logs and IGV autosaves remain on the server.');
  }

  async logs() {
    const session = await this.remembered();
    this.output.show();
    const directory = session?.directory ?? this.lastDirectory;
    if (!directory) { this.output.appendLine('No session recorded.'); return; }
    for (const name of ['manager', 'display', 'openbox', 'igv', 'websocket']) {
      const file = path.join(directory, `${name}.log`);
      try {
        const data = await readFile(file, 'utf8');
        // Avoid printing known ephemeral credentials, even from third-party logs.
        let tail = data.slice(-64000).replace(/([?&]token=)[^\s&"']+/g, '$1[redacted]');
        if (session) {
          tail = tail.split(session.token).join('[redacted]').split(session.vncPassword).join('[redacted]');
        }
        this.output.appendLine(`\n${file}\n${tail}`);
      } catch { this.output.appendLine(`${file}: unavailable`); }
    }
  }

  report(error: any) {
    // execFile errors embed stdout (which can contain credentials); only show stderr/message.
    let detail = typeof error?.stderr === 'string' && error.stderr.trim()
      ? error.stderr.trim() : (error instanceof Error ? error.message : String(error));
    detail = detail.replace(/([?&]token=)[^\s&"']+/g, '$1[redacted]');
    if (this.session) {
      detail = detail.split(this.session.token).join('[redacted]').split(this.session.vncPassword).join('[redacted]');
    }
    this.output.appendLine(detail);
    void vscode.window.showErrorMessage(`IGV Remote: ${detail}`);
  }

  dispose() {
    this.disposing = true;
    if (this.interval) { clearInterval(this.interval); }
    this.panel?.dispose();
    // Window shutdown/SSH loss cannot show a save prompt. Let the remote lease expire.
    this.output.dispose();
  }
}

export function activate(context: vscode.ExtensionContext) {
  instance = new RemoteViewer(context);
  context.subscriptions.push(instance);
  for (const action of ['start', 'reconnect', 'stop', 'logs'] as const) {
    context.subscriptions.push(vscode.commands.registerCommand(`igvRemote.${action}`, async () => {
      try { await instance![action](); } catch (error) { instance!.report(error); }
    }));
  }
}

export function deactivate() { instance?.dispose(); }
