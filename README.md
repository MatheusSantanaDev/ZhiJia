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
| **Consumo Energia** | Simulação por circuito no ESP32 (W/kWh) + tarifa CEMIG via ANEEL |
| **Consumo Água** | Extrato de consumo do Dmae (Uberlândia/MG) via portal público da Prefeitura |
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
| `home/energy/power` | ESP32 → Home Assistant | Total do ESP32 em W (soma dos circuitos) |
| `home/energy/energy_total` | ESP32 → Home Assistant | kWh acumulado, `state_class: total_increasing` |
| `home/energy/<c>/power` | ESP32 → Home Assistant | Potência de um circuito em W (ex: `home/energy/fogao/power`) |
| `home/energy/<c>/energy_total` | ESP32 → Home Assistant | kWh acumulado do circuito |

---

## Consumo de Energia

Fluxo da feature:

```
ESP32 (simula o consumo)  --MQTT-->  Mosquitto  -->  Home Assistant
                                                      │
ANEEL Dados Abertos  <--script Python (CEMIG)---------┘
```

**1. ESP32** — publica a cada 10 s, com MQTT Discovery (`homeassistant/sensor/.../config`),
um circuito por par de tópicos:

| Tópico | Entidade no HA | Unidade |
|--------|----------------|---------|
| `home/energy/power` | `sensor.zhijia_esp32_power` — total do ESP32 | W |
| `home/energy/energy_total` | `sensor.zhijia_esp32_energy_total` — total do ESP32 | kWh |
| `home/energy/<c>/power` | `sensor.zhijia_esp32_<c>_power` — potência do circuito | W |
| `home/energy/<c>/energy_total` | `sensor.zhijia_esp32_<c>_energy_total` — kWh do circuito | kWh |

Circuitos simulados: **`fogao`** (pico no café, almoço e jantar), **`tomadas`**
(standby + TV/notebook à noite) e as luzes **`luz_sala`**, **`luz_quarto`**,
**`luz_cozinha`** (cada uma com seu horário). Cada um tem curva própria de 24 h
interpolada + ruído + picos, e a hora vem do NTP (`UTC-3`); sem NTP ainda, vale
um "dia sintético" a partir do boot. O LED aceso soma 8 W nas tomadas.

**2. Home Assistant** — `homeassistant/config/packages/energia.yaml`:

| Entidade | O que é |
|----------|---------|
| `sensor.energia_importada_total` | **Total da casa** = ESP32 + dispositivos reais |
| `sensor.potencia_total_da_casa` | Potência instantânea da casa (ESP32 + reais) |
| `sensor.cemig_tarifa_kwh` | Tarifa vigente da CEMIG + bandeira (R$/kWh) |
| `sensor.consumo_energia_diario` / `_mensal` | `utility_meter` do total da casa |
| `sensor.consumo_de_energia_hoje` / `_mes` | kWh no período |
| `sensor.custo_da_energia_hoje` / `_mes` | kWh × tarifa da CEMIG (R$) |
| `sensor.tarifa_cemig_sem_bandeira` | Só a tarifa, sem o adicional da bandeira |

O total soma o ESP32 com os aparelhos que medem o próprio consumo. Hoje só a
**lavadora Hisense** reporta (SmartThings, `sensor.lavanderia_lavadora_energia`);
**TV Samsung** e **geladeira LG** entram na soma e no painel assim que ligarem
nas integrações deles — é só acrescentar o sensor no template.

**3. Tarifa da CEMIG** — `homeassistant/config/scripts/cemig_tarifa.py` consulta a
API pública da ANEEL (sem login):

- *Tarifas de aplicação das distribuidoras* → `SigAgente=CEMIG-D`, B1 Residencial
  Convencional → TUSD + TE, convertidos de R$/MWh para R$/kWh
- *Bandeiras tarifárias* → bandeira do mês + adicional (R$/MWh)
- Cache em `scripts/cemig_tarifa_cache.json` (6 h) para a API cair sem o sensor sumir

**Energy Dashboard** já vem preenchido pelo pacote (`.storage/energy`):

| Seção do painel | Entidade |
|-----------------|----------|
| Consumo da rede (Energia importada) | `sensor.energia_importada_total` |
| Potência da rede | `sensor.potencia_total_da_casa` |
| Preço da energia | `sensor.cemig_tarifa_kwh` (custo calculado pelo HA) |
| Dispositivos | `sensor.zhijia_esp32_fogao_*`, `_tomadas_*`, `_luz_*` e `sensor.lavanderia_lavadora_energia` |

---

## Consumo de Água (Dmae)

Uberlândia/MG é atendida pelo **Dmae** (Departamento Municipal de Água e Esgoto),
que não expõe API pública. Os dados ficam no portal do **DCDR/PRODAUB** (sistema
de consultas da Prefeitura), no relatório **Extrato de Consumo Imóveis**, que roda
**sem login** — só precisa do **código I.D.A.** de 10 dígitos que vem no topo de
toda fatura (`000280463-8`: o hífen é só formatação).

No condomínio existe **um único hidrômetro** (o Dmae fatura o prédio inteiro), entao
o que dá pra medir é o **consumo do prédio** — não existe consumo individual por
apartamento nessa fonte. Para a unidade seria preciso um hidrômetro secundário com
saída de pulso (Shelly/ESP32 → MQTT).

Fluxo da feature:

```
DCDR/PRODAUB (portal público)  --script Python-->  Home Assistant
  1. GET no formulário JSF (sessionid + ViewState)
  2. POST no botão "Gerar Relatório" → devolve a URL do relatório BIRT
  3. GET nessa URL como output?__format=html → extrato tabelado
```

> **Akamai**: o portal só responde a HTTP/2 com header-set de browser — `requests`
> e `urllib` levam 403. Por isso o script chama `curl --http2` (já existe no
> container, com `nghttp2`).

**1. Script** — `homeassistant/config/scripts/dmae_consumo.py`:

- lê o I.D.A. de `dmae_secrets.json` (gitignorado — copie o `dmae_secrets.example.json`)
- cacheia em `dmae_consumo_cache.json` (6 h) e mantém o **acumulado por competência**,
  para o total não cair quando o extrato parar de devolver um mês antigo
- `--dump` grava o HTML bruto em `dmae_ultimo_extrato.html` (conferir o parser)
- `--ida 0002804638` consulta sem tocar no segredo

**2. Home Assistant** — `homeassistant/config/packages/agua.yaml`:

| Entidade | O que é |
|----------|---------|
| `sensor.consumo_de_agua_dmae` | Consumo **medido** do último mês (m³) + atributos |
| `sensor.consumo_acumulado_do_predio` | Soma histórica (m³) — fonte do painel de água |
| `sensor.consumo_de_agua_faturado_do_predio` | O que o Dmae faturou no período (m³) |

Atributos de `sensor.consumo_de_agua_dmae`: `competencia`, `data_leitura`,
`hora_leitura`, `dias`, `ocorrencia`, `consumo_faturado_m3`, `consumo_medio_m3`,
`capacidade`, `imovel`, `hidrometro`, `economias`, `historico` (~31 meses),
`acumulado_m3`, `ida`, `fonte`, `consultado_em`, `stale`.

**3. Medido × faturado** — o Dmae cobra no mínimo **10 m³ por economia**: com 198
residências o faturado fica em **1980 m³**, mesmo quando o prédio mede ~700–1000 m³.
Por isso existem os dois sensores: o *medido* mostra o consumo real, o *faturado*
explica a conta.

**4. Energy Dashboard** — em *Configurações → Dashboards → Energia → Água*, adicione
`sensor.consumo_acumulado_do_predio` como fonte (o painel exige `total_increasing`).

> O relatório traz só m³, sem valores em R$. Para o custo dá pra usar o relatório
> público "Cadastros Faturas Imóveis" ou a tabela de tarifas do Dmae.

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