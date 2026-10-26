// 只开放几条固定的通道给渲染端，渲染端拿不到 Node 能力
const { contextBridge, ipcRenderer } = require('electron');

const SEND = new Set(['set-ignore', 'drag-start', 'drag-end', 'pet-event', 'open-chat', 'context-menu', 'smoke-result', 'shot-data']);
const RECEIVE = new Set(['cursor', 'pet-state']);

contextBridge.exposeInMainWorld('dfy', {
  smoke: process.argv.includes('--dfy-smoke'),
  shot: process.argv.includes('--dfy-shot'),
  bg: (process.argv.find((a) => a.startsWith('--dfy-bg=')) || '').slice(9),
  shading: (process.argv.find((a) => a.startsWith('--dfy-shading=')) || '').slice(14),
  send: (channel, data) => { if (SEND.has(channel)) ipcRenderer.send(channel, data); },
  on: (channel, callback) => {
    if (RECEIVE.has(channel)) ipcRenderer.on(channel, (_event, data) => callback(data));
  },
});
