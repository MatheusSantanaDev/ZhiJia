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

## Orçamento de compras (ABC e Atacadão)

A lista padrão do Google Keep ganhou uma aba **Orçamento** dentro do próprio
painel de tarefas. Ela consulta os itens da lista no **ABC** e no **Atacadão**
de Uberlândia e mostra:

- total dos itens com preço;
- total já comprado (preço congelado no momento da conclusão);
- total restante (preços atuais);
- cobertura e itens ainda sem preço.

**Configuração** — `homeassistant/config/packages/compras.yaml` contém o
mercado principal, URLs, loja do ABC, CEP do Atacadão e TTLs. O catálogo de
produtos, marcas, apelidos e gramaturas fica no `Store` da integração e é
editável pela aba Orçamento. A lista usada é a que tem
`default_shopping_list: true` (hoje, `todo.google_keep_lista_de_compras`).

**Como funciona**

- O **ABC** usa a API da loja online; a loja mais próxima é escolhida pela
  localização da casa (`zone.home`). O resultado é casado por marca e embalagem.
- O **Atacadão** usa a API VTEX da loja online. A busca retorna o catálogo e a
  entrega é simulada com o CEP configurado para obter o preço da loja local.
  Quando a simulação não confirma um item, a integração usa o preço online de
  referência se a embalagem for compatível; esse valor fica marcado como
  estimado e avisa que não foi confirmado para o CEP.
- Todo item recebe uma **primeira opção** de produto, com marca e
  gramatura/volume comuns do catálogo (por exemplo, arroz Camil 5 kg, feijão
  Kicaldo 1 kg e leite Porto Alegre 1 L). Itens livres criados direto no Google
  Keep também são vinculados automaticamente; se o catálogo ainda não conhecer
  o produto, a própria busca online completa a marca e a embalagem.
- Ao adicionar, vincular ou atualizar um item, a integração consulta
  automaticamente **ABC e Atacadão** (`default_market: abc` define apenas o
  mercado principal). Resultados de baixa confiança também são preenchidos como
  **estimados**, com o nome da opção encontrada e o aviso para conferir marca e
  tamanho. Um preço manual sempre tem prioridade e nunca é sobrescrito.
- **Assaí** e **Bahamas** ficam desativados porque os encartes não oferecem uma
  API de consulta confiável. O código de OCR continua no projeto e pode ser
  reativado no YAML, mas não aparece na aba enquanto os mercados estiverem
  comentados.
- Sempre há o atalho **Google** e o campo de preço manual por mercado. O campo
  nativo da lista continua aceitando texto livre; a busca de catálogo fica
  apenas na aba Orçamento.
- Marca e gramatura ficam salvas no item até serem editadas. Ao concluir um item,
  os preços daquele momento são congelados para o total de comprados.

**Dependências de sistema** — o `Dockerfile.local` instala `tesseract-ocr`,
`tesseract-ocr-por` e `poppler-utils`. Depois de alterar o Dockerfile ou a
integração:

```bash
docker compose build homeassistant
docker compose up -d homeassistant
```

O frontend precisa ser rebuildado para a aba aparecer:

```bash
cd homeassistant-frontend && yarn build && cd ..
docker compose up -d --force-recreate ha-frontend
```

> Se o Google Keep mostrar `BadAuthentication` nos logs, as credenciais
> expiraram: reautentique a integração em **Configurações → Dispositivos e
> serviços → Google Keep**. A aba Orçamento só aparece quando a lista padrão
> está disponível.

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
| **Consumo Água** | Extrato do Dmae (Uberlândia/MG) via portal público + custo estimado em R$ |
| **Lista de Compras** | Sincronização bidirecional com Google Keep (integração customizada) |
| **Orçamento de Compras** | Preço automático do ABC e Atacadão, Google como fallback e preço congelado ao concluir |
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
- `dmae_backfill_stats.py` (irmão do script) importa o histórico do extrato para as
  estatísticas de longo prazo do HA — ver o passo 6

**2. Home Assistant** — `homeassistant/config/packages/agua.yaml`:

| Entidade | O que é |
|----------|---------|
| `sensor.consumo_de_agua_dmae` | Consumo **medido** do último mês (m³) + atributos |
| `sensor.consumo_acumulado_do_predio` | Soma histórica (m³) — fonte do painel de água |
| `sensor.consumo_de_agua_faturado_do_predio` | O que o Dmae faturou no período (m³) |
| `sensor.custo_da_agua_do_predio` | Custo estimado da conta (R$) + água/esgoto/tarifa |
| `sensor.tarifa_da_agua_dmae` | Preço médio pago por m³ faturado (R$/m³) |

Atributos de `sensor.consumo_de_agua_dmae`: `competencia`, `data_leitura`,
`hora_leitura`, `dias`, `ocorrencia`, `consumo_faturado_m3`, `consumo_medio_m3`,
`custo_agua_brl`, `custo_esgoto_brl`, `custo_total_brl`, `tarifa_media_brl_m3`,
`tarifa` (vigência/% esgoto/calibração), `capacidade`, `imovel`, `hidrometro`,
`economias`, `historico` (~31 meses), `acumulado_m3`, `ida`, `fonte`,
`consultado_em`, `stale`.

**3. Medido × faturado** — o Dmae cobra no mínimo **10 m³ por economia**: com 198
residências o faturado fica em **1980 m³**, mesmo quando o prédio mede ~700–1000 m³.
Por isso existem os dois sensores: o *medido* mostra o consumo real, o *faturado*
explica a conta.

**4. Custo estimado da conta (R$)** — o portal público só devolve m³ (sem reais), e
os decretos tarifários são PDFs escaneados, então o `dmae_consumo.py` calcula a
estimativa com a **tabela vigente desde 21/12/2025** (Resolução Aresan 001/2025),
**calibrada na fatura real do imóvel** (venc. 04/2026):

```
1ª economia  = tarifa individual      1 × R$ 30,89
2ª em diante = medição compartilhada  197 × R$ 20,60   (10 m³/economia)
                            água = R$ 4.089,09   ← bateu com a fatura
esgoto = 80% do valor da água = R$ 3.271,27      ← 3.271,27 / 4.089,09
        total base = R$ 7.360,36                 ← igual ao da fatura
```

O rateio é por economia (`faturado / 198`) e as faixas de excedente (11–20:
R$ 2,37 … acima de 50: R$ 8,06) entram quando o consumo passa do mínimo. Se o Dmae
atualizar a tabela, os valores estão em `TABELAS`/`FAIXAS_*` no topo do script.

> **Não entram no cálculo:** a Taxa de Coleta de Lixo (em condomínio vertical sem
> hidrômetro individual o Dmae cobra em **carnê próprio**) e multa/juros de atraso.
> Por ser uma estimativa, vale comparar com a fatura de vez em quando — se bater
> diferente, ajuste `PERCENTUAL_ESGOTO`/`TABELAS`.

**5. Energy Dashboard** — a fonte já está cadastrada no `.storage/energy` (fonte
`water` → `sensor.consumo_acumulado_do_predio`, nome "Água do prédio (Dmae)"), então
em *Configurações → Dashboards → Energia* ela aparece direto na seção **Água**.
Ficou **sem preço de propósito**: o painel calcularia `consumo × preço_fixo`, mas a
conta tem piso (1980 m³ faturados contra ~700–1000 m³ medidos) e nunca fecharia —
o R$ certo continua em `sensor.custo_da_agua_do_predio`.

**6. Histórico** — o extrato traz ~31 meses, mas o HA só grava o que observa ao vivo,
então as estatísticas de longo prazo precisam ser importadas uma vez (o `.db` não é
versionado; refaça isso se recriar o banco):

```
docker stop zhijia-homeassistant
python3 homeassistant/config/scripts/dmae_backfill_stats.py   # faz backup antes
docker start zhijia-homeassistant
```

Carrega **17/03/2024 → 16/09/2026 (14.314 m³)** nas três entidades de volume e o
gráfico de Água já nasce com o histórico. O script é idempotente (recusa importar
duas vezes), tem `--dry-run` e também renova o `sum` das linhas de hoje, para a
cadeia de soma do recorder (`short term` → horária → longo prazo) continuar coerente.

> ⚠️ **Nunca abra o `home-assistant_v2.db` pelo host com o HA rodando** — nem com
> `sqlite3`, nem com um script Python ad-hoc. Pelo VirtioFS o processo do host não
> enxerga os locks do container, acredita ser o último usuário do banco e, ao fechar,
> faz checkpoint e **apaga os `-wal`/`-shm`** de baixo do HA. Ele fica com descritores
> órfãos, dois índices WAL concorrentes passam a coexistir e toda conexão nova falha
> com `disk I/O error` / `database disk image is malformed` — que é o que estoura
> `unknown_error` na página de Energia. (aconteceu de verdade em 06/10/2026; o
> conserto foi parar o HA, limpar os resíduos e subir de novo). O
> `dmae_backfill_stats.py` tem trava contra isso (`--forcar` ignora). Para inspecionar
> com o HA no ar, vá pelo container:
>
> ```
> docker exec -i zhijia-homeassistant python3 -c \
>   "import sqlite3; print(sqlite3.connect('file:/config/home-assistant_v2.db?mode=ro', uri=True).execute('PRAGMA integrity_check').fetchone())"
> ```

**7. Mês a mês** — o `command_line` executa o script **a cada 6 h**, para sempre. O
Dmae lê o hidrômetro **uma vez por mês**, por volta do dia 15 (o extrato mostra
16/09, 15/08, 15/07, 16/06, 16/05…) e só publica a linha nova quando a leitura sai —
até lá os lançamentos devolvem os mesmos números. Quando a leitura nova aparece, na
próxima execução de 6 h:

- `sensor.consumo_de_agua_dmae` assume o novo m³ medido
- `sensor.custo_da_agua_do_predio` recalcula a estimativa da nova fatura
- o acumulado sobe e o painel de Água registra o consumo daquele período

Se o portal cair, o script cai no cache (6 h) e marca `stale: true` — `consultado_em`
parar de avançar é o sinal. Sem cache nenhum, o sensor mantém o último estado.

---

## Lista de Compras (Google Keep)

O Shopping List nativo do Home Assistant **não** se conecta ao Google Keep. Para unificar as
listas, o projeto usa a integração customizada
[**Google Keep Sync**](https://github.com/watkins-matt/home-assistant-google-keep-sync)
(v1.2.0), instalada manualmente em `homeassistant/config/custom_components/google_keep_sync`
porque o HACS não está instalado neste projeto.

- **Bidirecional:** cada lista selecionada do Keep vira uma entidade `todo.google_keep_*` no HA.
- **HA → Keep:** imediato.
- **Keep → HA:** polling, 15 min por padrão (configurável nas opções da integração).
- **Conflito:** se o mesmo item for editado nos dois lados entre polls, a edição do HA vence.
- **API não oficial** (`gkeepapi`): pode quebrar se o Google mudar o serviço.

### Configuração inicial

1. Gere um **OAuth token** da conta Google (recomendado: conta dedicada, com as listas do Keep
   compartilhadas com ela). O fluxo antigo de master token por senha foi bloqueado pelo Google.

   1. Abra `https://accounts.google.com/EmbeddedSetup` em uma janela anônima.
   2. Entre com a conta e clique em **I Agree**. A página vai ficar carregando; isso é normal.
   3. Com a página travada, abra o DevTools (`F12` ou `Cmd+Option+I`).
   4. Vá em **Application → Storage → Cookies → https://accounts.google.com**.
   5. Copie o valor do cookie **`oauth_token`** (começa com `oauth2_4/`).

2. No Home Assistant: **Configurações → Dispositivos e Serviços → Adicionar Integração →
   Google Keep Sync**.

3. Informe o e-mail da conta e o token `oauth2_4/...`; selecione as listas que deseja
   sincronizar. A integração troca o token OAuth por um master token internamente.

4. As listas aparecem como `todo.google_keep_...`. Use o card **Lista** no dashboard ou os
   serviços `todo.add_item`, `todo.update_item` e `todo.remove_item` em automações.

### Lista de compras padrão (Keep)

A integração pode tratar uma lista do Keep como a **Lista de compras padrão** do Home
Assistant. Por padrão, ela procura uma lista chamada `Lista de Compras` ou `Shopping List`
entre as listas sincronizadas. Você pode escolher outra lista (ou desativar) em
**Configurações → Dispositivos e Serviços → Google Keep Sync → Configurar → Lista de compras
padrão**.

Com a lista padrão definida:

- Os serviços `shopping_list.add_item`, `remove_item`, `complete_item`, `incomplete_item`,
  `complete_all`, `incomplete_all`, `clear_completed_items` e `sort` passam a operar na lista
  do Keep.
- Os comandos de voz de lista de compras (`HassShoppingListAddItem`,
  `HassShoppingListCompleteItem` e `HassShoppingListLastItems`) usam a lista do Keep.
- O painel **Listas** aplica essa lista como seleção inicial uma vez por navegador; depois disso,
  respeita a lista escolhida manualmente.
- O card **Lista de compras** do dashboard prefere essa lista.
- A entidade recebe o atributo `default_shopping_list: true`, usado pelo painel e pelo card
  para identificar a lista padrão.

A lista escolhida é adicionada automaticamente às listas sincronizadas. Se você selecionar
**Nenhum** (**None**), os serviços e comandos de voz voltam para a Lista de compras nativa do
Home Assistant.

> O `shopping_list.sort` só reordena a lista do Keep quando a opção **Ordenar listas
> automaticamente** (**Automatically Sort Lists**) está desativada. Com a ordenação automática
> ativa, o próprio Keep mantém a ordem.

> `todo.shopping_list` continua sendo a entidade nativa, que fica oculta do painel. Para
> automações que chamam `todo.*` diretamente, use a entidade
> `todo.google_keep_lista_de_compras` ou os serviços `shopping_list.*` acima.

### No painel Listas

- **Sincronizar agora:** o painel Listas mostra um ícone de sincronizar no topo (ao lado do
  menu de três pontos). Ele chama `google_keep_sync.request_sync` com `force: true`.
- **Reordenar:** no computador, arraste pelo ícone de grade à esquerda de cada lista. A ordem
  fica salva no navegador (localStorage) e vale também para o menu suspenso do celular.
- **Remover lista / Remover do painel:** menu de três pontos. O comportamento depende da
  integração:
  - **Google Keep:** move a lista para a lixeira do Keep, remove da sincronização e remove a
    entidade do Home Assistant.
  - **Lista local:** exclui a lista definitivamente.
  - **Lista de compras nativa:** oculta a lista do Home Assistant. Para removê-la completamente,
    exclua a integração **Lista de compras** nas configurações.
  - **iCloud/Lembretes e outras integrações sem exclusão via API:** a lista é apenas ocultada
    do Home Assistant e continua existindo no serviço original.

O serviço também pode ser chamado em automações/scripts:

```yaml
action:
  - service: google_keep_sync.delete_list
    target:
      entity_id: todo.google_keep_lista_de_compras
```

Exemplo de automação para adicionar um item à lista padrão (funciona mesmo que o nome da
lista mude):

```yaml
action:
  - service: shopping_list.add_item
    data:
      name: "Leite"
```

Também é possível mirar diretamente a entidade do Keep:

```yaml
action:
  - service: todo.add_item
    target:
      entity_id: todo.google_keep_lista_de_compras
    data:
      item: "Leite"
```

Para forçar uma sincronização imediata com o Keep (respeitando o cooldown do serviço):

```yaml
action:
  - service: google_keep_sync.request_sync
```

> **Segurança:** o master token dá acesso total à conta Google, não apenas ao Keep. Por isso,
> use uma conta dedicada e compartilhe somente as listas necessárias. Não use a conta principal.

### Atualização

Substitua a pasta `custom_components/google_keep_sync` pela versão nova do repositório e reinicie:

```bash
docker restart zhijia-homeassistant
```

> **Atenção:** a cópia local tem modificações próprias: o serviço
> `google_keep_sync.delete_list`, a ponte da Lista de compras padrão
> (`shopping_list_bridge.py`), a opção de lista padrão no config flow e o
> botão/ordenação/seleção padrão no painel Listas. Ao atualizar pelo upstream, essas mudanças
> precisam ser reaplicadas.

Se alterar o painel/frontend, recompile e recrie o container do frontend:

```bash
cd homeassistant-frontend
SKIP_FETCH_NIGHTLY_TRANSLATIONS=1 yarn build --modern
cd ..
docker compose up -d --force-recreate ha-frontend
```

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