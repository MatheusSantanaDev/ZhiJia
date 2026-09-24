export function updateConnectionStatus(status) {
    const statusElement = document.getElementById('connectionStatus');
    if (!statusElement) return;
    statusElement.classList.remove('connecting', 'connected', 'disconnected');
    statusElement.classList.add(status);
}