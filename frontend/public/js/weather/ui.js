import { ICON_MAP } from '../config.js';

export function processApiData(apiData, locationName) {
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
            description: getWeatherDescription(current.weather_code),
            icon: mapWmoIcon(current.weather_code)
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
            icon: mapWmoIcon(daily.weather_code[i])
        });
    }
    return processed;
}

export function updateWeatherUI(data) {
    document.getElementById('current-location').textContent = data.location;
    document.getElementById('current-icon').src = ICON_MAP[data.current.icon] || './icons/local.svg';
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
            <img src="${ICON_MAP[dayData.icon] || './icons/local.svg'}" alt="${dayData.icon}" class="day-icon">
            <span class="day-temp"><img src="./icons/termometro.svg" alt="Termômetro" class="inline-icon"> ${dayData.temp}°C</span>
            <span class="day-humidity"><img src="./icons/gota.svg" alt="Gota de umidade" class="inline-icon"> ${dayData.humidity}</span>
        `;
        forecastList.appendChild(listItem);
    });

    console.log("Interface do tempo atualizada com dados da Open-Meteo!");
}

function isNightTime() {
    const hour = new Date().getHours();
    return hour >= 18 || hour < 6;
}

function mapWmoIcon(code) {
    const isNight = isNightTime();
    if (code <= 1) return isNight ? "lua" : "sol";
    if (code === 2) return "nuvem";
    if (code === 3) return "nublado";
    if (code >= 45 && code <= 48) return "nevoa";
    if (code >= 51 && code <= 67) return "chuva_leve";
    if (code >= 71 && code <= 77) return "neve";
    if (code >= 80 && code <= 82) return "chuva";
    if (code === 95 || code === 96 || code === 99) return "trovao";
    return "nuvem";
}

function getWeatherDescription(code) {
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