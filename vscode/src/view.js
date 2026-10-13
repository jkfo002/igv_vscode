import RFB from '@novnc/novnc/lib/rfb.js';

const vscode = acquireVsCodeApi();
const status = document.getElementById('status');
let rfb;
let resize = true;
let scale = false;

window.addEventListener('message', ({ data }) => {
  if (data.type === 'connect') {
    if (rfb) { rfb.disconnect(); }
    rfb = new RFB(document.getElementById('screen'), data.url,
      { shared: false, credentials: { password: data.password } });
    rfb.resizeSession = resize;
    rfb.scaleViewport = scale;
    rfb.qualityLevel = 9;
    rfb.compressionLevel = 2;
    rfb.addEventListener('connect', () => {
      status.textContent = '已连接远程 Java IGV';
      vscode.postMessage({ type: 'connected' });
    });
    rfb.addEventListener('disconnect', () => {
      status.textContent = '连接已断开；十分钟内可重新连接';
      vscode.postMessage({ type: 'disconnected' });
    });
    rfb.addEventListener('clipboard', event => {
      vscode.postMessage({ type: 'clipboard', text: event.detail.text });
    });
    rfb.addEventListener('securityfailure', () => { status.textContent = '认证失败，请重新连接或查看日志'; });
  } else if (data.type === 'paste' && rfb) {
    rfb.clipboardPasteFrom(data.text);
    rfb.focus();
  } else if (data.type === 'error') {
    status.textContent = data.text;
  }
});
for (const type of ['reconnect', 'paste', 'copy', 'stop']) {
  document.getElementById(type).addEventListener('click', () => vscode.postMessage({ type }));
}
document.getElementById('resize').addEventListener('click', event => {
  resize = !resize;
  if (rfb) { rfb.resizeSession = resize; }
  event.target.textContent = `调整远程分辨率：${resize ? '开' : '关'}`;
});
document.getElementById('scale').addEventListener('click', event => {
  scale = !scale;
  if (rfb) { rfb.scaleViewport = scale; }
  event.target.textContent = `适应窗口：${scale ? '开' : '关'}`;
});
vscode.postMessage({ type: 'ready' });
