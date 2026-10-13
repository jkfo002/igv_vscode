const { test } = require('node:test');
const assert = require('node:assert/strict');
const { websocketUrl, htmlEscape } = require('../src/protocol.cjs');

test('forwarded HTTPS path and provider query survive conversion to WebSocket', () => {
  const result = new URL(websocketUrl('https://tunnel.example/proxy/123/?provider=abc', 'a+b/&'));
  assert.equal(result.protocol, 'wss:');
  assert.equal(result.pathname, '/proxy/123/');
  assert.equal(result.searchParams.get('provider'), 'abc');
  assert.equal(result.searchParams.get('token'), 'a+b/&');
});

test('loopback HTTP uses ws and replaces a previous session token', () => {
  assert.equal(websocketUrl('http://127.0.0.1:6080/?token=old', 'new'), 'ws://127.0.0.1:6080/?token=new');
  assert.throws(() => websocketUrl('file:///tmp/session', 'secret'));
});

test('HTML attributes cannot escape their quoted value', () => {
  assert.equal(htmlEscape('"<script>&'), '&quot;&lt;script&gt;&amp;');
});
