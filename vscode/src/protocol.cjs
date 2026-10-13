// Shared by the extension and dependency-free tests.
function websocketUrl(external, token) {
  const url = new URL(external);
  if (!['http:', 'https:'].includes(url.protocol)) {
    throw new Error('Unexpected forwarded URI scheme');
  }
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  // Preserve any path/query supplied by the VS Code tunnel resolver.
  url.searchParams.set('token', token);
  return url.toString();
}

function htmlEscape(value) {
  return String(value).replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[char]));
}

module.exports = { websocketUrl, htmlEscape };
