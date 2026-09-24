# ZhiJia - Smart Home

Projeto pessoal de automação residencial utilizando ESP32.

---

## Estrutura do Repositório

```
ZhiJia/
├── firmware/                 # Código do ESP32 (hardware)
│   ├── src/main.cpp         # Código principal
│   ├── lib/                 # Bibliotecas locais (TuyaLocal)
│   ├── include/             # Headers
│   ├── data/                # Arquivos SPIFFS (config.json sobe para o ESP)
│   ├── platformio.ini       # Configuração PlatformIO
│   ├── extra_script.py      # Auto-upload SPIFFS
│   └── test/                # Testes unitários
│
├── frontend/                # Interface Web (software)
│   └── public/              # Arquivos estáticos servidos pelo Nginx
│       ├── index.html
│       ├── scripts.js       # Lógica MQTT, weather, UI
│       ├── spotify.js       # Integração Spotify
│       ├── styles.css
│       ├── config.example.json
│       └── icons/
│
├── mosquitto/               # Configuração MQTT Broker
│   ├── config/mosquitto.conf
│   ├── data/
│   └── log/
│
├── nginx/                   # Configuração Nginx (Reverse Proxy)
│   └── nginx.conf
│
├── docker-compose.yml       # Orquestração (raiz do projeto)
├── .dockerignore
├── .gitignore
└── README.md
```

---

## Arquitetura do Sistema

```
┌──────────────────┐     WebSocket (9001)      ┌──────────────────┐
│   Frontend       │◄─────────────────────────►│   Mosquitto      │
│   (Nginx:8080)   │                           │   MQTT Broker    │
└──────────────────┘                           └────────┬─────────┘
                                                        │
                                              MQTT (1883)│
                                                        ▼
                                          ┌───────────────────────┐
                                          │       ESP32           │
                                          │  - RGB LED (PWM)      │
                                          │  - Servo Motor        │
                                          │  - Web Server (80)    │
                                          │  - Tuya Strip (LAN)   │
                                          └───────────────────────┘
```

- **Frontend** → Conecta via WebSocket na porta 9001
- **ESP32** → Conecta via MQTT TCP na porta 1883
- **Broker local** → Substitui broker público (HiveMQ)

---

## Subindo a Infraestrutura Local

```bash
docker-compose up -d
```

Serviços disponíveis:
- **Frontend:** http://localhost:8080 (ou http://<seu-ip>:8080)
- **MQTT Broker:** `mqtt://<seu-ip>:1883` (ESP32)
- **MQTT WebSocket:** `ws://<seu-ip>:9001/mqtt` (Frontend)

---

## Configurando o ESP32

1. Copie `firmware/data/config.example.json` → `firmware/data/config.json`
2. Edite `config.json` com suas credenciais:
   ```json
   {
     "wifi_ssid": "SEU_SSID",
     "wifi_password": "SUA_SENHA",
     "admin_user": "admin",
     "admin_password": "admin",
     "mqtt_server": "mqtt://192.168.0.XXX",  // IP da máquina rodando docker
     "mqtt_port": 1883,
     "duckdns_token": "SEU_TOKEN",
     "duckdns_domain": "seu-dominio.duckdns.org",
     "server_port": 1420,
     "strip_ip": "192.168.0.XXX",
     "strip_device_id": "DEVICE_ID",
     "strip_local_key": "LOCAL_KEY"
   }
   ```
3. Build e upload via PlatformIO:
   ```bash
   cd firmware
   pio run -t upload
   pio run -t uploadfs  # Upload SPIFFS (config.json, index.html, etc)
   ```

---

## Desenvolvimento Frontend

Os arquivos em `frontend/public/` são servidos diretamente pelo Nginx. Edite e recarregue o browser.

Para desenvolvimento com hot-reload, use um servidor local apontando para `frontend/public/`.

---

## Funcionalidades

| Funcionalidade | Descrição |
|----------------|-----------|
| **RGB LED** | PWM nos GPIOs 25/26/27, controle via sliders |
| **Servo Motor** | GPIO 15, velocidade -100 a +100 com fim de curso |
| **Fita Tuya** | Controle local via protocolo Tuya (IP/LAN) |
| **Web Server** | HTTP Basic Auth, arquivos do SPIFFS |
| **MQTT** | Pub/Sub: `home/led/color`, `home/servo/angle`, `home/strip/*` |
| **Duck DNS** | DDNS automático |
| **Previsão Tempo** | BrasilAPI (CEP) + Open-Meteo (6 dias) |
| **Spotify** | OAuth + Web Playback SDK |

---

## Tópicos MQTT

| Tópico | Direção | Payload |
|--------|---------|---------|
| `home/led/color` | ESP32 ← Frontend | `R,G,B` (ex: `255,128,0`) |
| `home/servo/angle` | ESP32 ← Frontend | `{"speed": -50}` |
| `home/strip/color` | Tuya ← Frontend | `R,G,B` |
| `home/strip/power` | Tuya ← Frontend | `"on"` / `"off"` |

---

## Comandos Úteis

```bash
# Subir infraestrutura
docker-compose up -d

# Logs
docker-compose logs -f mosquitto
docker-compose logs -f frontend

# Parar tudo
docker-compose down

# Rebuild firmware
cd firmware && pio run -t upload

# Monitor serial
cd firmware && pio device monitor
```