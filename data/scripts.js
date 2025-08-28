const client = mqtt.connect('wss://broker.hivemq.com:8000/mqtt'); // Broker público para teste
const topic = 'home/led/color'; // Tópico usado para sincronização

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

// Função para buscar e atualizar os dados do tempo
function fetchWeatherData() {
    console.log("Buscando dados da previsão do tempo...");

    // ---- IMPORTANTE ----
    // No mundo real, aqui você faria uma chamada para uma API de verdade, como:
    // fetch('https://api.openweathermap.org/data/2.5/weather?q=Uberlandia&appid=SUA_CHAVE_API&units=metric&lang=pt_br')
    //     .then(response => response.json())
    //     .then(data => {
    //         updateWeatherUI(data); // Função que atualiza a interface
    //     });
    //
    // Por enquanto, vamos usar dados de EXEMPLO (mock) para simular a resposta da API.
    
    const mockWeatherData = {
        location: "Uberlândia, MG",
        current: {
            temp: 28,
            feels_like: 29,
            humidity: 45,
            description: "Ensolarado",
            icon: "sunny" // nome do ícone
        },
        forecast: [
            { day: "Segunda", temp: 24, humidity: 60, icon: "sunny" },
            { day: "Terça", temp: 20, humidity: 65, icon: "cloudy" },
            { day: "Quarta", temp: 19, humidity: 70, icon: "rain" },
            { day: "Quinta", temp: 21, humidity: 68, icon: "storm" },
            { day: "Sexta", temp: 18, humidity: 75, icon: "rain" }
        ]
    };

    // Atraso de 1 segundo para simular o carregamento da rede
    setTimeout(() => {
        updateWeatherUI(mockWeatherData);
    }, 1000);
}

// Função para atualizar a interface com os dados recebidos
function updateWeatherUI(data) {
    // Mapeamento de condições para arquivos de ícone
    const iconMap = {
        sunny: './icons/sunny.svg',
        cloudy: './icons/cloudy.svg',
        rain: './icons/rain.svg',
        storm: './icons/storm.svg',
        // adicione outros ícones conforme necessário
    };

    // Atualiza a previsão atual
    document.getElementById('current-location').textContent = data.location;
    document.getElementById('current-icon').src = iconMap[data.current.icon] || './icons/placeholder.svg';
    document.getElementById('current-temp').textContent = `${data.current.temp}°C`;
    document.getElementById('current-description').textContent = data.current.description;
    document.getElementById('current-feels-like').textContent = `${data.current.feels_like}°C`;
    document.getElementById('current-humidity').textContent = `${data.current.humidity}%`;

    // Atualiza a previsão da semana
    const forecastList = document.getElementById('forecast-list');
    forecastList.innerHTML = ''; // Limpa a lista antes de adicionar novos itens

    data.forecast.forEach(dayData => {
        const listItem = document.createElement('li');
        listItem.className = 'forecast-day';
        
        listItem.innerHTML = `
            <span class="day-name">${dayData.day}</span>
            <img src="${iconMap[dayData.icon] || './icons/placeholder.svg'}" alt="${dayData.icon}" class="day-icon">
            <span class="day-temp">${dayData.temp}°C</span>
            <span class="day-humidity">Umidade: ${dayData.humidity}%</span>
        `;
        
        forecastList.appendChild(listItem);
    });
    
    console.log("Interface do tempo atualizada!");
}

// Event Listeners
// Roda a função quando a página carregar
document.addEventListener('DOMContentLoaded', fetchWeatherData);

// Roda a função quando o botão de refresh for clicado
document.getElementById('refresh-weather').addEventListener('click', fetchWeatherData);

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
        document.getElementById('title').style.color = getComplementaryColor(red, green, blue);

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
    document.getElementById('title').style.color = getComplementaryColor(red, green, blue);

    // Publica a nova cor no tópico MQTT
    if (client.connected) {
        client.publish(topic, `${red},${green},${blue}`);
    } else {
        console.error('MQTT não está conectado.');
    }
}

function turnOffLights() {
    setLEDValues(0, 0, 0);
    document.getElementById('title').style.color = getComplementaryColor(255, 255, 255);
}

function whiteLight() {
    setLEDValues(255, 255, 255);
    document.getElementById('title').style.color = getComplementaryColor(0, 0, 0);
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

function getComplementaryColor(r, g, b) {
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