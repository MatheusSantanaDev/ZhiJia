import { getConfigValue } from './utils/config.js';

export const MQTT_CONFIG = {
    get host() { return getConfigValue('mqtt_host', window.location.hostname || 'localhost'); },
    get wsPort() { return getConfigValue('mqtt_ws_port', 9001); },
    get topics() {
        return getConfigValue('mqtt_topics', {
            led: 'home/led/color',
            servo: 'home/servo/angle',
            stripColor: 'home/strip/color',
            stripPower: 'home/strip/power'
        });
    }
};

export const WEATHER_CONFIG = {
    get zipCode() { return getConfigValue('weather_zip_code', '38414-553'); },
    get updateInterval() { return getConfigValue('weather_update_interval', 3600000); }
};

export const SERVO_CONFIG = {
    get speedMin() { return getConfigValue('servo_speed_min', 1); },
    get speedMax() { return getConfigValue('servo_speed_max', 100); },
    get defaultSpeed() { return getConfigValue('servo_default_speed', 50); }
};

export const ICON_MAP = {
    sol: './icons/sol.svg',
    nuvem: './icons/nuvem.svg',
    nublado: './icons/nublado.svg',
    chuva_leve: './icons/chuva_leve.svg',
    chuva: './icons/chuva.svg',
    trovao: './icons/trovao.svg',
    neve: './icons/neve.svg',
    nevoa: './icons/nevoa.svg'
};