import { MQTT_CONFIG, WEATHER_CONFIG } from './config.js';
import { createMqttClient, subscribe, isConnected } from './mqtt/client.js';
import { fetchWeatherData } from './weather/api.js';
import { processApiData, updateWeatherUI } from './weather/ui.js';
import { initLedController, handleMqttMessage as handleLedMessage, turnOffLights, whiteLight, yellowishLight } from './led/controller.js';
import { initServoController, handleMqttMessage as handleServoMessage, openMotor, closeMotor } from './servo/controller.js';

window.turnOffLights = turnOffLights;
window.whiteLight = whiteLight;
window.yellowishLight = yellowishLight;
window.openMotor = openMotor;
window.closeMotor = closeMotor;

let weatherUpdateTimer = null;

async function initApp() {
    initLedController();
    initServoController();
    createMqttClient();

    subscribe(MQTT_CONFIG.topics.led, handleLedMessage);
    subscribe(MQTT_CONFIG.topics.servo, handleServoMessage);

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
    weatherUpdateTimer = setInterval(loadWeather, WEATHER_CONFIG.updateInterval);
}

function stopWeatherUpdates() {
    if (weatherUpdateTimer) {
        clearInterval(weatherUpdateTimer);
        weatherUpdateTimer = null;
    }
}

document.addEventListener('DOMContentLoaded', initApp);

window.spotifyLogin = () => import('./spotify/spotify.js').then(m => m.spotifyLogin());
window.spotifyTogglePlay = () => import('./spotify/spotify.js').then(m => m.spotifyTogglePlay());
window.spotifyPrevious = () => import('./spotify/spotify.js').then(m => m.spotifyPrevious());
window.spotifyNext = () => import('./spotify/spotify.js').then(m => m.spotifyNext());
window.spotifySetVolume = (v) => import('./spotify/spotify.js').then(m => m.spotifySetVolume(v));