import { MQTT_CONFIG, SERVO_CONFIG } from '../config.js';
import { publish, isConnected } from '../mqtt/client.js';

let speedValue = SERVO_CONFIG.defaultSpeed;
let isUserDragging = false;

export function initServoController() {
    setupSpeedSlider();
    updateSpeedTrack();
}

function setupSpeedSlider() {
    const container = document.getElementById('speedSliderContainer');
    const track = document.getElementById('speedTrack');
    if (!container || !track) return;

    function updateTrack() {
        const percentage = ((speedValue - SERVO_CONFIG.speedMin) / (SERVO_CONFIG.speedMax - SERVO_CONFIG.speedMin)) * 100;
        track.style.background = `linear-gradient(to right, #4CAF50 ${percentage}%, #ccc ${percentage}%)`;
    }

    function setSpeedFromPosition(clientX) {
        const rect = container.getBoundingClientRect();
        const x = clientX - rect.left;
        const percentage = Math.max(0, Math.min(1, x / rect.width));
        speedValue = Math.round(SERVO_CONFIG.speedMin + percentage * (SERVO_CONFIG.speedMax - SERVO_CONFIG.speedMin));
        updateTrack();
    }

    container.addEventListener('mousedown', (e) => {
        isUserDragging = true;
        setSpeedFromPosition(e.clientX);

        const onMouseMove = (e) => setSpeedFromPosition(e.clientX);
        const onMouseUp = () => {
            isUserDragging = false;
            document.removeEventListener('mousemove', onMouseMove);
            document.removeEventListener('mouseup', onMouseUp);
        };

        document.addEventListener('mousemove', onMouseMove);
        document.addEventListener('mouseup', onMouseUp);
    });

    container.addEventListener('touchstart', (e) => {
        isUserDragging = true;
        setSpeedFromPosition(e.touches[0].clientX);
    });

    container.addEventListener('touchmove', (e) => {
        e.preventDefault();
        setSpeedFromPosition(e.touches[0].clientX);
    });

    container.addEventListener('touchend', () => {
        isUserDragging = false;
    });

    window.updateSpeedTrack = updateTrack;
}

export function handleMqttMessage(payload) {
    if (isUserDragging) return;

    try {
        const data = JSON.parse(payload);
        const speed = data.speed;

        speedValue = Math.abs(speed);
        if (speedValue < SERVO_CONFIG.speedMin) speedValue = SERVO_CONFIG.speedMin;
        if (speedValue > SERVO_CONFIG.speedMax) speedValue = SERVO_CONFIG.speedMax;

        if (window.updateSpeedTrack) window.updateSpeedTrack();
        updateMotorButtons(speed);

        console.log(`Comando do motor recebido: velocidade=${speed}`);
    } catch (e) {
        console.error('Erro ao parsear mensagem do servo:', e);
    }
}

function updateMotorButtons(speed) {
    const openBtn = document.querySelector('.open-btn');
    const closeBtn = document.querySelector('.close-btn');
    if (!openBtn || !closeBtn) return;

    openBtn.classList.remove('active');
    closeBtn.classList.remove('active');

    if (speed > 0) openBtn.classList.add('active');
    else if (speed < 0) closeBtn.classList.add('active');
}

export function openMotor() {
    updateMotorButtons(speedValue);
    sendMotorCommand(speedValue);
}

export function closeMotor() {
    updateMotorButtons(-speedValue);
    sendMotorCommand(-speedValue);
}

function sendMotorCommand(speed) {
    const command = { speed };
    const message = JSON.stringify(command);
    console.log(`Enviando comando para o motor: ${message}`);
    if (isConnected()) {
        publish(MQTT_CONFIG.topics.servo, message);
    } else {
        console.error('MQTT não conectado.');
    }
}