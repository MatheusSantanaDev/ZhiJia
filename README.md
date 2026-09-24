# ZhiJia (智家) - Smart Home

Projeto pessoal de automação residencial utilizando ESP32.

---

## Overview do Projeto

### Estrutura de Arquivos

```
ZhiJia/
├── src/main.cpp          # Código principal ESP32
├── data/
│   ├── index.html        # Interface web
│   ├── scripts.js        # Lógica frontend
│   ├── styles.css        # Estilos responsivos
│   ├── config.json       # Credenciais WiFi, MQTT, DuckDNS
│   └── icons/            # Ícones SVG para UI
├── platformio.ini        # Configuração PlatformIO
└── extra_script.py       # Auto-upload SPIFFS
```

---

### Arquitetura do Sistema

```
┌─────────────────────┐     ┌─────────────────────┐
│   Browser (Web UI)  │◄───►│  MQTT Broker        │
│   - Sliders RGB     │     │  (HiveMQ público)   │
│   - Controle Motor  │     │  wss://8884/mqtt    │
│   - Previsão Tempo  │     └─────────┬───────────┘
└─────────────────────┘               │
                                       ▼
                           ┌───────────────────────┐
                           │       ESP32           │
                           │  - RGB LED (PWM)      │
                           │  - Servo Motor        │
                           │  - Web Server         │
                           │  - Duck DNS           │
                           └───────────────────────┘
```

---

### Funcionalidades Implementadas

| Funcionalidade | Descrição |
|----------------|-----------|
| **RGB LED** | Controle PWM nos GPIOs 25/26/27, valores 0-255 por canal |
| **Servo Motor** | GPIO 15, velocidade -100 a +100|
| **Web Server** | HTTP Basic Auth, serve arquivos do SPIFFS |
| **MQTT** | Pub/Sub para `home/led/color` e `home/servo/angle` |
| **Duck DNS** | DDNS via `central-comando.duckdns.org` |
| **Previsão do Tempo** | APIs BrasilAPI (CEP) + Open-Meteo (6 dias) |

---

### Bibliotecas Utilizadas

- **ArduinoJson** - Parse de JSON (config.json, MQTT payloads)
- **ESP32Servo** - Controle de servo motor
- **mqtt_client** - Cliente MQTT ESP-IDF
- **WiFi/WebServer** - Conectividade e servidor HTTP
- **SPIFFS** - Sistema de arquivos para interface web

---

### Padrões de Código

**Backend (C++):**
- Funções organizadas por funcionalidade
- Event handlers para MQTT
- Configurações carregadas do `config.json`

**Frontend (JavaScript):**
- Async/await para chamadas de API
- MQTT via WebSocket para sincronização entre abas
- Manipulação direta do DOM (sem frameworks)

---

### Estado do Desenvolvimento

**Concluído:**
- Controle de RGB LED (desligar, branco, amarelado)
- Sincronização entre abas via MQTT
- Card de previsão do tempo funcional
- Interface responsiva para mobile
- Abertura e fechamento de janela 

**Pendente:**
- Serial monitor na página web
- Comunicação com PS5
- Comunicação com TV

---

## TODO
- [X] Buttom to turn off, turn white light and turn yellowish.
- [X] Comunication between web pages opened.
- [ ] Put the serial monitor on web page.
- [X] Put some APIs with useful informations, weather forecast.
- [X] Control of window.
- [ ] communication with ps5.
- [ ] communication with TV.
