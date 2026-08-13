const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  // Log viewer
  getLogs: () => ipcRenderer.invoke('get-logs'),
  clearLogs: () => ipcRenderer.invoke('clear-logs'),
  onLogLine: (callback) => {
    const handler = (_event, line) => callback(line);
    ipcRenderer.on('log-line', handler);
    return () => ipcRenderer.removeListener('log-line', handler);
  },
  // Check if running in Electron
  isElectron: true,
  // Native directory picker (returns '' on cancel / unsupported)
  pickDirectory: () => ipcRenderer.invoke('dialog:pickDirectory'),
});
