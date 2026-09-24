import { loadConfig, getConfigValue } from './utils/config.js';
import { createMqttClient, subscribe, isConnected } from './mqtt/client.js';
import { fetchWeatherData } from './weather/api.js';
import { processApiData, updateWeatherUI } from './weather/ui.js';
import { initLedController, handleMqttMessage as handleLedMessage, turnOffLights, whiteLight, yellowishLight } from './led/controller.js';
import { initServoController, handleMqttMessage as handleServoMessage, openMotor, closeMotor } from './servo/controller.js';
import './spotify/spotify.js';

window.turnOffLights = turnOffLights;
window.whiteLight = whiteLight;
window.yellowishLight = yellowishLight;
window.openMotor = openMotor;
window.closeMotor = closeMotor;

let weatherUpdateTimer = null;

async function initApp() {
    try {
        await loadConfig();
        console.log('[App] Config carregado');
    } catch (error) {
        console.error('[App] Falha ao carregar config:', error);
    }

    initLedController();
    initServoController();
    createMqttClient();

    subscribe(getConfigValue('mqtt_topics', {}).led || 'home/led/color', handleLedMessage);
    subscribe(getConfigValue('mqtt_topics', {}).servo || 'home/servo/angle', handleServoMessage);

    await loadWeather();
    startWeatherUpdates();
}

async function loadWeather() {
    try {
        const { weatherData, locationName } = await fetchWeatherData();
        const processedData = processApiData(weatherData, locationName);
        updateWeatherUI(processedData);
    } catch (error) {
        document.getElementById('current-location').textContent = "Erro ao carregar dados";
    }
}

function startWeatherUpdates() {
    document.getElementById('refresh-weather').addEventListener('click', loadWeather);
    const interval = getConfigValue('weather_update_interval', 3600000);
    weatherUpdateTimer = setInterval(loadWeather, interval);
}

function stopWeatherUpdates() {
    if (weatherUpdateTimer) {
        clearInterval(weatherUpdateTimer);
        weatherUpdateTimer = null;
    }
}

document.addEventListener('DOMContentLoaded', initApp);

import('./spotify/spotify.js').then(m => {
    window.spotifyLogin = m.spotifyLogin;
    window.spotifyTogglePlay = m.spotifyTogglePlay;
    window.spotifyPrevious = m.spotifyPrevious;
    window.spotifyNext = m.spotifyNext;
    window.spotifySeekForward = m.spotifySeekForward;
    window.spotifySeekBackward = m.spotifySeekBackward;
    window.spotifySetVolume = m.spotifySetVolumeDebounced;
});