export const MQTT_CONFIG = {
    host: window.location.hostname || 'localhost',
    wsPort: 9001,
    topics: {
        led: 'home/led/color',
        servo: 'home/servo/angle',
        stripColor: 'home/strip/color',
        stripPower: 'home/strip/power'
    }
};

export const WEATHER_CONFIG = {
    zipCode: "38414-553",
    updateInterval: 3600000
};

export const SERVO_CONFIG = {
    speedMin: 1,
    speedMax: 100,
    defaultSpeed: 50
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