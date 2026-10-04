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
├── mosquitto/               # Configuração MQTT Broker
│   ├── config/mosquitto.conf
│   ├── data/
│   └── log/

├── homeassistant/           # Config do HA + Dockerfile do build local
│   ├── config/              # .storage, banco, logs (fora do versionamento)
│   └── Dockerfile.local
│
├── homeassistant-core/      # SUBMÓDULO - fork MatheusSantanaDev/core (branch ZhiJia)
├── homeassistant-frontend/  # SUBMÓDULO - fork MatheusSantanaDev/frontend (branch ZhiJia)
│
├── nginx/                   # Configuração Nginx (Reverse Proxy)
│   └── nginx.conf
│
├── docker-compose.yml       # Orquestração (raiz do projeto)
├── .gitmodules              # Os 2 submódulos (branch ZhiJia)
├── .dockerignore
├── .gitignore
└── README.md
```

---

## Submódulos (forks customizados)

Tudo que é custom do Home Assistant vive nos forks, e o ZhiJia só aponta pra eles:

| Submódulo | Fork | Branch |
|-----------|------|--------|
| `homeassistant-core` | `MatheusSantanaDev/core` | `ZhiJia` |
| `homeassistant-frontend` | `MatheusSantanaDev/frontend` | `ZhiJia` |

**Clone:**
```bash
git clone --recurse-submodules https://github.com/MatheusSantanaDev/ZhiJia.git
# já clonou sem submódulo?
git submodule update --init --recursive
```

**Fluxo de manutenção** (toda alteração custom é commitada 2x):
```bash
# 1. editar no submódulo (ex: homeassistant-core/...)
git -C homeassistant-core add -A
git -C homeassistant-core commit -m "feat: ..."
git -C homeassistant-core push fork ZhiJia

# 2. atualizar o ponteiro no ZhiJia
git add homeassistant-core
git commit -m "feat: ..."
git push

# puxar atualização do upstream
git -C homeassistant-core fetch origin
git -C homeassistant-core merge origin/<tag>
# depois repita o passo 2
```

> `origin` nos submódulos = upstream do HA (pull), `fork` = seu (push).

**Após clonar limpo**, o `hass_frontend/` (bundle do frontend do HA) não existe — buildar antes de subir o container `ha-frontend`:
```bash
cd homeassistant-frontend && yarn install && yarn build && cd ..
```


---

## Arquitetura do Sistema

```
┌──────────────────────┐                        ┌──────────────────────┐
│  Home Assistant UI   │   HTTP/WebSocket:8123  │  Home Assistant      │
│  (Nginx ha-frontend) │◄──────────────────────►│  (backend)           │
└──────────────────────┘                        └──────────┬───────────┘
                                                           │ MQTT (1883)
                                                           ▼
                                                 ┌──────────────────────┐
                                                 │   Mosquitto          │
                                                 │   MQTT Broker        │
                                                 └──────────┬───────────┘
                                                            │ MQTT (1883)
                                                            ▼
                                              ┌───────────────────────┐
                                              │       ESP32           │
                                              │  - RGB LED (PWM)      │
                                              │  - Servo Motor        │
                                              │  - Web Server (80)    │
                                              │  - Tuya Strip (LAN)   │
                                              └───────────────────────┘
```

- **Interface** → apenas a do Home Assistant (porta 8123)
- **ESP32** → Conecta via MQTT TCP na porta 1883
- **Broker local** → Substitui broker público (HiveMQ)

---

## Subindo a Infraestrutura Local

```bash
docker-compose up -d
```

Serviços disponíveis:
- **Home Assistant:** http://localhost:8123 (ou http://<seu-ip>:8123)
- **MQTT Broker:** `mqtt://<seu-ip>:1883` (ESP32 e Home Assistant)
- **MQTT WebSocket:** `ws://<seu-ip>:9001/mqtt` (opcional, clientes de browser)

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

A interface é **apenas a do Home Assistant**, customizada no submódulo `homeassistant-frontend` (fork `MatheusSantanaDev/frontend`, branch `ZhiJia`). Cards, temas e ajustes visuais vivem lá.

Após alterações no submódulo, rebuild e reinício:

```bash
cd homeassistant-frontend && yarn install && yarn build && cd ..
docker-compose up -d --force-recreate ha-frontend
```

---

## Funcionalidades

| Funcionalidade | Descrição |
|----------------|-----------|
| **RGB LED** | PWM nos GPIOs 25/26/27, controle via sliders |
| **Servo Motor** | GPIO 15, velocidade -100 a +100 com fim de curso |
| **Fita Tuya** | Controle local via protocolo Tuya (IP/LAN) |
| **Web Server** | HTTP Basic Auth, arquivos do SPIFFS |
| **MQTT** | Pub/Sub: `home/led/color`, `home/servo/angle`, `home/strip/*` |
| **Consumo Energia** | Simulação de consumo no ESP32 (W/kWh) + tarifa CEMIG via ANEEL |
| **Duck DNS** | DDNS automático |
| **Previsão Tempo** | BrasilAPI (CEP) + Open-Meteo (6 dias) |
| **Spotify** | OAuth + Web Playback SDK |

---

## Tópicos MQTT

| Tópico | Direção | Payload |
|--------|---------|---------|
| `home/led/color` | ESP32 ← Home Assistant | `R,G,B` (ex: `255,128,0`) |
| `home/servo/angle` | ESP32 ← Home Assistant | `{"speed": -50}` |
| `home/strip/color` | Tuya ← Home Assistant | `R,G,B` |
| `home/strip/power` | Tuya ← Home Assistant | `"on"` / `"off"` |
| `home/energy/power` | ESP32 → Home Assistant | Potência instantânea em W (ex: `342.5`) |
| `home/energy/energy_total` | ESP32 → Home Assistant | kWh acumulado, `state_class: total_increasing` |

---

## Consumo de Energia

Fluxo da feature:

```
ESP32 (simula o consumo)  --MQTT-->  Mosquitto  -->  Home Assistant
                                                      │
ANEEL Dados Abertos  <--script Python (CEMIG)---------┘
```

**1. ESP32** — publica a cada 10 s, com MQTT Discovery (`homeassistant/sensor/.../config`):

| Tópico | Entidade no HA | Unidade |
|--------|----------------|---------|
| `home/energy/power` | `sensor.zhijia_esp32_power` | W |
| `home/energy/energy_total` | `sensor.zhijia_esp32_energy_total` | kWh |

A curva de consumo é uma simulação (perfil residencial 24 h interpolado + ruído +
picos de chuveiro/forno), e o LED aceso soma 8 W. A hora vem do NTP (`UTC-3`);
sem NTP ainda, vale um "dia sintético" a partir do boot.

**2. Home Assistant** — `homeassistant/config/packages/energia.yaml`:

| Entidade | O que é |
|----------|---------|
| `sensor.cemig_tarifa_kwh` | Tarifa vigente da CEMIG + bandeira (R$/kWh) |
| `sensor.consumo_energia_diario` / `_mensal` | `utility_meter` do contador do ESP32 |
| `sensor.consumo_de_energia_hoje` / `_mes` | kWh no período |
| `sensor.custo_da_energia_hoje` / `_mes` | kWh × tarifa da CEMIG (R$) |
| `sensor.tarifa_cemig_sem_bandeira` | Só a tarifa, sem o adicional da bandeira |

**3. Tarifa da CEMIG** — `homeassistant/config/scripts/cemig_tarifa.py` consulta a
API pública da ANEEL (sem login):

- *Tarifas de aplicação das distribuidoras* → `SigAgente=CEMIG-D`, B1 Residencial
  Convencional → TUSD + TE, convertidos de R$/MWh para R$/kWh
- *Bandeiras tarifárias* → bandeira do mês + adicional (R$/MWh)
- Cache em `scripts/cemig_tarifa_cache.json` (6 h) para a API cair sem o sensor sumir

Para ver no **Energy Dashboard** do HA: *Configurações → Energia → Fontes →
Consumo da rede* e escolha `sensor.zhijia_esp32_energy_total`, com custo
`sensor.custo_da_energia_mes`.

---

## Comandos Úteis

```bash
# Subir infraestrutura
docker-compose up -d

# Logs
docker-compose logs -f mosquitto
docker-compose logs -f ha-frontend

# Parar tudo
docker-compose down

# Rebuild firmware
cd firmware && pio run -t upload

# Monitor serial
cd firmware && pio device monitor
```

---

## Traduções do Home Assistant

**Nota para o futuro:** para atualizar traduções, repita:

```bash
gh run download <run-id> -R home-assistant/frontend -n translations
# + atualizar translations/artifact.json
yarn build
```