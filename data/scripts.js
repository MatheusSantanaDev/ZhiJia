const client = mqtt.connect('wss://broker.hivemq.com:8884/mqtt'); // Broker público para teste
const topic = 'home/led/color'; // Tópico usado para sincronização

const WEATHER_CONFIG = {
    ZIP_CODE: "38414-553",
    UPDATE_INTERVAL: 3600000 
};


// Conectar ao broker MQTT
client.on('connect', function () {
    console.log('Conectado ao broker MQTT');
    client.subscribe(topic, function (err) {
        if (err) {
            console.error('Erro ao se inscrever no tópico:', err);
        } else {
            console.log('Inscrito no tópico:', topic);
        }
    });
});

client.on('error', function (error) {
    console.error('Erro na conexão MQTT:', error);
});

async function fetchWeatherData() {
    console.log("Iniciando busca de dados...");
    try {
        const cep = WEATHER_CONFIG.ZIP_CODE.replace(/\D/g, '');

        // Tenta obter dados de localização do CEP
        const locationInfo = await _getLocationDataFromCep(cep);
        if (!locationInfo) throw new Error("CEP inválido ou não encontrado na BrasilAPI.");

        let coords = { lat: locationInfo.lat, lon: locationInfo.lon };

        // Verifica se as coordenadas vieram. Se não, executa pela cidade
        if (!coords.lat || !coords.lon) {
            console.warn("Coordenadas não encontradas para o CEP. Usando nome da cidade.");
            coords = await _getCoordsFromCity(locationInfo.locationName);
            if (!coords) throw new Error(`Não foi possível encontrar coordenadas para a cidade: ${locationInfo.locationName}`);
        }
        console.log("Coordenadas finais utilizadas:", coords);

        const openMeteoURL = `https://api.open-meteo.com/v1/forecast?latitude=${coords.lat}&longitude=${coords.lon}&current=temperature_2m,relative_humidity_2m,apparent_temperature,weather_code&daily=weather_code,temperature_2m_max&hourly=relative_humidity_2m&timezone=auto&forecast_days=6`;
        const response = await fetch(openMeteoURL);
        if (!response.ok) throw new Error(`Erro na API Open-Meteo: ${response.statusText}`);
        
        const weatherData = await response.json();

        // Processa e atualiza a interface
        const processedData = processApiData(weatherData, locationInfo.locationName);
        updateWeatherUI(processedData);

    } catch (error) {
        console.error("ERRO FINAL:", error.message);
        document.getElementById('current-location').textContent = "Erro ao carregar dados";
    }
}

// Converte os dados brutos da Open-Meteo em um objeto limpo e estruturado.
function processApiData(apiData, locationName) {
    const { current, daily, hourly } = apiData;
    const weekdays = ["Domingo", "Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado"];
    const today = new Date();
    const currentDayName = weekdays[today.getDay()];

    const processed = {
        location: locationName,
        dayOfWeek: currentDayName,
        current: {
            temp: Math.round(current.temperature_2m),
            feels_like: Math.round(current.apparent_temperature),
            humidity: current.relative_humidity_2m,
            description: _getWeatherDescription(current.weather_code),
            icon: _mapWmoIcon(current.weather_code)
        },
        forecast: []
    };

    for (let i = 1; i < daily.time.length; i++) {

        const forecastDate = daily.time[i];
        const targetTimeString = `${forecastDate}T12:00`;
        const hourlyIndex = hourly.time.indexOf(targetTimeString);
        
        let humidityForDay = 'N/D';
        if (hourlyIndex !== -1) {
            humidityForDay = `${hourly.relative_humidity_2m[hourlyIndex]}%`;
        }

        const date = new Date(forecastDate + 'T00:00:00');
        const dayName = weekdays[date.getDay()];

        processed.forecast.push({
            day: dayName,
            temp: Math.round(daily.temperature_2m_max[i]),
            humidity: humidityForDay,
            icon: _mapWmoIcon(daily.weather_code[i])
        });
    }
    return processed;
}

// Atualiza o HTML com os dados já processados.
function updateWeatherUI(data) {
    const iconMap = {
        sol: './icons/sol.svg',
        nuvem: './icons/nuvem.svg',
        nublado: './icons/nublado.svg',
        chuva_leve: './icons/chuva_leve.svg',
        chuva: './icons/chuva.svg',
        trovao: './icons/trovao.svg',
        neve: './icons/neve.svg',
        nevoa: './icons/nevoa.svg'
    };
    
    document.getElementById('current-location').textContent = data.location;
    document.getElementById('current-icon').src = iconMap[data.current.icon] || './icons/local.svg';
    document.getElementById('current-day-of-week').textContent = data.dayOfWeek;
    document.getElementById('current-temp-value').textContent = `${data.current.temp}°C`;
    document.getElementById('current-description').textContent = data.current.description;
    document.getElementById('current-feels-like').textContent = `${data.current.feels_like}°C`;
    document.getElementById('current-humidity').textContent = `${data.current.humidity}%`;

    const forecastList = document.getElementById('forecast-list');
    forecastList.innerHTML = ''; 

    data.forecast.forEach(dayData => {
        const listItem = document.createElement('li');
        listItem.className = 'forecast-day';
        listItem.innerHTML = `
            <span class="day-name">${dayData.day}</span>
            <img src="${iconMap[dayData.icon] || './icons/local.svg'}" alt="${dayData.icon}" class="day-icon">
            <span class="day-temp"><img src="./icons/termometro.svg" alt="Termômetro" class="inline-icon"> ${dayData.temp}°C</span>
            <span class="day-humidity"><img src="./icons/gota.svg" alt="Gota de umidade" class="inline-icon"> ${dayData.humidity}</span>
        `;
        forecastList.appendChild(listItem);
    });
    
    console.log("Interface do tempo atualizada com dados da Open-Meteo!");
}

document.addEventListener('DOMContentLoaded', () => {
    fetchWeatherData(); 
    document.getElementById('refresh-weather').addEventListener('click', fetchWeatherData);
    setInterval(fetchWeatherData, WEATHER_CONFIG.UPDATE_INTERVAL);
});

// Atualizar sliders, fundo e cor do título ao receber mensagens MQTT
client.on('message', function (receivedTopic, message) {
    if (receivedTopic === topic) {
        const [red, green, blue] = message.toString().split(',').map(Number);

        // Atualiza os sliders
        document.getElementById('red').value = red;
        document.getElementById('green').value = green;
        document.getElementById('blue').value = blue;

        // Atualiza a cor de fundo e do título
        const bgColor = `rgb(${red}, ${green}, ${blue})`;
        document.body.style.backgroundColor = bgColor;
        document.getElementById('title').style.color = _getComplementaryColor(red, green, blue);

        console.log(`Cor recebida: R=${red}, G=${green}, B=${blue}`);
    }
});

function updateLED() {
    const red = document.getElementById('red').value;
    const green = document.getElementById('green').value;
    const blue = document.getElementById('blue').value;

    // Atualiza a cor de fundo e do título
    const bgColor = `rgb(${red}, ${green}, ${blue})`;
    document.body.style.backgroundColor = bgColor;
    document.getElementById('title').style.color = _getComplementaryColor(red, green, blue);

    // Publica a nova cor no tópico MQTT
    if (client.connected) {
        client.publish(topic, `${red},${green},${blue}`);
    } else {
        console.error('MQTT não está conectado.');
    }
}

// Botoes
function turnOffLights() {
    setLEDValues(0, 0, 0);
    document.getElementById('title').style.color = _getComplementaryColor(0, 0, 0);
}
function whiteLight() {
    setLEDValues(255, 255, 255);
    document.getElementById('title').style.color = _getComplementaryColor(255, 255, 255);
}
function yellowishLight() {
    setLEDValues(255, 80, 0);
}

function setLEDValues(r, g, b) {
    document.getElementById('red').value = r;
    document.getElementById('green').value = g;
    document.getElementById('blue').value = b;
    updateLED();
}

function _getComplementaryColor(r, g, b) {
    // Verificar se a cor é preta ou branca
    if (r === 0 && g === 0 && b === 0) {
        return 'rgb(255, 255, 255)';
    }
    if (r === 255 && g === 255 && b === 255) {
        return 'rgb(0, 0, 0)';
    }

    // Converte RGB para HSL
    const hsl = _rgbToHsl(r, g, b);
    
    // A cor complementar é a cor com a matiz (hue) deslocada em 180 graus
    const compH = (hsl[0] + 0.5) % 1;
    
    const [compR, compG, compB] = _hslToRgb(compH, hsl[1], hsl[2]);
    return `rgb(${Math.round(compR)}, ${Math.round(compG)}, ${Math.round(compB)})`;
}

async function _getLocationDataFromCep(cep) {
    const brasilApiURL = `https://brasilapi.com.br/api/cep/v2/${cep}`;
    try {
        const response = await fetch(brasilApiURL);
        if (!response.ok) return null;
        const data = await response.json();

        if (data.location && data.location.coordinates && data.location.coordinates.latitude) {
            return { 
                lat: data.location.coordinates.latitude, 
                lon: data.location.coordinates.longitude,
                locationName: `${data.city}, ${data.state}`
            };
        } else if (data.city) {
            return {
                lat: null,
                lon: null,
                locationName: `${data.city}, ${data.state}`
            };
        }
        return null;
    } catch (error) {
        console.error("[_getLocationDataFromCep] Falhou:", error);
        return null;
    }
}

async function _getCoordsFromCity(cityNameWithState) {
    const cityNameOnly = cityNameWithState.split(',')[0].trim();

    const geocodingURL = `https://geocoding-api.open-meteo.com/v1/search?name=${encodeURIComponent(cityNameOnly)}&count=1&language=pt&format=json`;
    try {
        const response = await fetch(geocodingURL);
        if (!response.ok) return null;
        const data = await response.json();
        
        if (data.results && data.results[0]) {
            return {
                lat: data.results[0].latitude,
                lon: data.results[0].longitude
            };
        }
        return null;
    } catch (error) {
        console.error("[_getCoordsFromCity] Falhou:", error);
        return null;
    }
}
function _mapWmoIcon(code) {
    if (code <= 1) return "sol";
    if (code === 2) return "nuvem";
    if (code === 3) return "nublado";
    if (code >= 45 && code <= 48) return "nevoa";
    if (code >= 51 && code <= 67) return "chuva_leve";
    if (code >= 71 && code <= 77) return "neve";
    if (code >= 80 && code <= 82) return "chuva";
    if (code === 95 || code === 96 || code === 99) return "trovao";

    return "nuvem";
}

function _getWeatherDescription(code) {
    const descriptions = {
        0: 'Céu limpo', 1: 'Quase limpo', 2: 'Parcialmente nublado', 3: 'Nublado',
        45: 'Nevoeiro', 48: 'Nevoeiro com gelo',
        51: 'Garoa leve', 53: 'Garoa moderada', 55: 'Garoa forte',
        61: 'Chuva leve', 63: 'Chuva moderada', 65: 'Chuva forte',
        80: 'Pancadas de chuva leves', 81: 'Pancadas de chuva moderadas', 82: 'Pancadas de chuva violentas',
        95: 'Trovoada', 96: 'Trovoada com granizo', 99: 'Trovoada com granizo forte'
    };
    return descriptions[code] || 'Não disponível';
}

// Função para converter de RGB para HSL
function _rgbToHsl(r, g, b) {
    r /= 255;
    g /= 255;
    b /= 255;
    
    const max = Math.max(r, g, b);
    const min = Math.min(r, g, b);
    let h = (max + min) / 2;
    let s = h;
    let l = h;
    
    if (max === min) {
        h = s = 0; // sem saturação
    } else {
        const d = max - min;
        s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
        if (max === r) {
            h = (g - b) / d + (g < b ? 6 : 0);
        } else if (max === g) {
            h = (b - r) / d + 2;
        } else {
            h = (r - g) / d + 4;
        }
        h /= 6;
    }
    return [h, s, l];
}

// Função para converter de HSL para RGB
function _hslToRgb(h, s, l) {
    let r, g, b;
    
    if (s === 0) {
        r = g = b = l; // cinza
    } else {
        const hue2rgb = (p, q, t) => {
            if (t < 0) t += 1;
            if (t > 1) t -= 1;
            if (t < 1 / 6) return p + (q - p) * 6 * t;
            if (t < 1 / 2) return q;
            if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
            return p;
        };
        
        const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
        const p = 2 * l - q;
        r = hue2rgb(p, q, h + 1 / 3);
        g = hue2rgb(p, q, h);
        b = hue2rgb(p, q, h - 1 / 3);
    }
    
    return [r * 255, g * 255, b * 255];
}