import { MQTT_CONFIG } from '../config.js';
import { applyColor } from '../utils/color.js';
import { publish, isConnected } from '../mqtt/client.js';

let isUserDragging = false;

export function initLedController() {
    setupDragEvents();
}

function setupDragEvents() {
    ['red', 'green', 'blue'].forEach(id => {
        const slider = document.getElementById(id);
        if (!slider) return;
        slider.addEventListener('mousedown', () => { isUserDragging = true; });
        slider.addEventListener('mouseup', () => { isUserDragging = false; });
        slider.addEventListener('touchstart', () => { isUserDragging = true; });
        slider.addEventListener('touchend', () => { isUserDragging = false; });
    });
}

export function handleMqttMessage(payload) {
    if (isUserDragging) return;

    const [red, green, blue] = payload.split(',').map(Number);
    updateSliders(red, green, blue);
    applyColor(red, green, blue);
    console.log(`Cor recebida: R=${red}, G=${green}, B=${blue}`);
}

function updateSliders(red, green, blue) {
    document.getElementById('red').value = red;
    document.getElementById('green').value = green;
    document.getElementById('blue').value = blue;
}

export function updateLED() {
    const red = document.getElementById('red').value;
    const green = document.getElementById('green').value;
    const blue = document.getElementById('blue').value;

    applyColor(red, green, blue);

    if (isConnected()) {
        publish(MQTT_CONFIG.topics.led, `${red},${green},${blue}`);
        publish(MQTT_CONFIG.topics.stripColor, `${red},${green},${blue}`);
    } else {
        console.error('MQTT não está conectado.');
    }
}

export function setLEDValues(r, g, b) {
    document.getElementById('red').value = r;
    document.getElementById('green').value = g;
    document.getElementById('blue').value = b;
    updateLED();
}

export function turnOffLights() {
    setLEDValues(0, 0, 0);
    document.getElementById('title').style.color = 'rgb(255, 255, 255)';
    if (isConnected()) publish(MQTT_CONFIG.topics.stripPower, 'off');
}

export function whiteLight() {
    setLEDValues(255, 255, 255);
    document.getElementById('title').style.color = 'rgb(0, 0, 0)';
}

export function yellowishLight() {
    setLEDValues(255, 80, 0);
}