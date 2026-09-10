import test from 'node:test';
import assert from 'node:assert/strict';
import { HubConnection } from '../src/transport';

test('normalizes the hub endpoint and starts disconnected', () => {
  const hub = new HubConnection('http://localhost:8790///', 'token');
  assert.equal(hub.endpoint, 'http://localhost:8790');
  assert.equal(hub.connected, false);
  hub.close();
});

test('event subscriptions can be disposed deterministically', () => {
  const hub = new HubConnection('http://localhost:8790', 'token');
  let received = 0;
  const subscription = hub.onEvent(() => received++);
  subscription.dispose();
  hub.close();
  assert.equal(received, 0);
});
