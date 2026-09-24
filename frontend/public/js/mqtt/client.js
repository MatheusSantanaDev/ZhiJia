import { MQTT_CONFIG } from '../config.js';
import { updateConnectionStatus } from './status.js';

let client = null;
const subscribers = new Map();

export function createMqttClient() {
    const url = `ws://${MQTT_CONFIG.host}:${MQTT_CONFIG.wsPort}/mqtt`;
    client = mqtt.connect(url);
    setupEventHandlers();
    return client;
}

export function getMqttClient() {
    return client;
}

function setupEventHandlers() {
    client.on('connect', () => {
        console.log('Conectado ao broker MQTT');
        updateConnectionStatus('connected');
        subscribeToTopics();
    });

    client.on('close', () => {
        console.log('Conexão MQTT fechada');
        updateConnectionStatus('disconnected');
    });

    client.on('offline', () => {
        console.log('Cliente MQTT offline');
        updateConnectionStatus('disconnected');
    });

    client.on('reconnect', () => {
        console.log('Reconectando ao broker MQTT...');
        updateConnectionStatus('connecting');
    });

    client.on('error', (error) => {
        console.error('Erro na conexão MQTT:', error);
        updateConnectionStatus('disconnected');
    });

    client.on('message', (topic, message) => {
        const handlers = subscribers.get(topic);
        if (handlers) {
            handlers.forEach(handler => handler(message.toString()));
        }
    });
}

function subscribeToTopics() {
    Object.values(MQTT_CONFIG.topics).forEach(topic => {
        client.subscribe(topic, (err) => {
            if (err) {
                console.error(`Erro ao se inscrever no tópico ${topic}:`, err);
            } else {
                console.log(`Inscrito no tópico: ${topic}`);
            }
        });
    });
}

export function subscribe(topic, handler) {
    if (!subscribers.has(topic)) {
        subscribers.set(topic, []);
    }
    subscribers.get(topic).push(handler);
}

export function publish(topic, payload) {
    if (client && client.connected) {
        client.publish(topic, payload);
    } else {
        console.error('MQTT não está conectado.');
    }
}

export function isConnected() {
    return client && client.connected;
}