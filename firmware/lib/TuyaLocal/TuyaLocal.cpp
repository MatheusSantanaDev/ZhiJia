#include "TuyaLocal.h"
#include <mbedtls/aes.h>
#include <math.h>

// ── Constantes do protocolo ──────────────────────────────────────────────────

static const uint16_t TUYA_PORT        = 6668;
static const uint32_t TUYA_PREFIX      = 0x000055AA;
static const uint32_t TUYA_SUFFIX      = 0x0000AA55;
static const uint32_t TUYA_CMD_CONTROL = 0x00000007;
static const uint8_t  TUYA_VERSION[3]  = {'3', '.', '3'};
static const size_t   TUYA_VER_HEADER  = 15; // 3 bytes versão + 12 bytes nulos

// ── Construtor ───────────────────────────────────────────────────────────────

TuyaLocal::TuyaLocal(const String& ip, const String& deviceId, const String& localKey)
    : _ip(ip), _deviceId(deviceId), _localKey(localKey), _sequence(0) {}

// ── Métodos públicos ─────────────────────────────────────────────────────────

bool TuyaLocal::setPower(bool on) {
    char dps[20];
    snprintf(dps, sizeof(dps), "{\"20\":%s}", on ? "true" : "false");
    return sendDPS(dps);
}

bool TuyaLocal::setColor(uint8_t r, uint8_t g, uint8_t b) {
    uint16_t h, s, v;
    _rgbToHSV(r, g, b, h, s, v);

    char dps[64];
    snprintf(dps, sizeof(dps),
        "{\"20\":true,\"21\":\"colour\",\"24\":\"%04x%04x%04x\"}", h, s, v);

    return sendDPS(dps);
}

bool TuyaLocal::sendDPS(const char* dpsJson) {
    // ── Pré-validações ────────────────────────────────────────────────────────

    if (_localKey.length() < 16) {
        Serial.println("[TuyaLocal] local_key deve ter exatamente 16 bytes");
        return false;
    }

    // ── 1. JSON do payload ────────────────────────────────────────────────────
    //
    // Formato obrigatório do protocolo Tuya v3.3:
    //   {"devId":"<id>","uid":"<id>","t":"<segundos>","dps":<dpsJson>}
    //
    // O campo "t" usa millis()/1000 (segundos desde o boot).
    // Dispositivos Tuya locais não validam o valor absoluto do timestamp,
    // apenas a presença do campo. Para Unix timestamp real, integrar NTP.

    char json[400];
    int jsonLen = snprintf(json, sizeof(json),
        "{\"devId\":\"%s\",\"uid\":\"%s\",\"t\":\"%lu\",\"dps\":%s}",
        _deviceId.c_str(),
        _deviceId.c_str(),
        (unsigned long)(millis() / 1000UL),
        dpsJson);

    if (jsonLen < 0 || jsonLen >= (int)sizeof(json)) {
        Serial.println("[TuyaLocal] JSON do payload excedeu o buffer");
        return false;
    }

    // ── 2. Header de versão: "3.3" (3 bytes) + 12 bytes nulos ────────────────
    //
    // O protocolo v3.3 exige que o payload seja prefixado com esta assinatura
    // antes da encriptação. Total do header: 15 bytes.
    //
    //   raw = [0x33][0x2E][0x33][0x00 x12][...JSON...]

    size_t rawLen = TUYA_VER_HEADER + (size_t)jsonLen;

    if (rawLen > 480) { // margem segura para o buffer de 512
        Serial.println("[TuyaLocal] Payload excede tamanho máximo");
        return false;
    }

    uint8_t raw[512];
    memcpy(raw, TUYA_VERSION, 3);       // "3.3"
    memset(raw + 3, 0x00, 12);          // 12 bytes nulos
    memcpy(raw + TUYA_VER_HEADER, json, (size_t)jsonLen);

    // ── 3. PKCS7 padding para alinhamento em blocos de 16 bytes ──────────────
    //
    // PKCS7 sempre adiciona entre 1 e 16 bytes (nunca zero).
    // Se já alinhado: adiciona um bloco completo de 16 × 0x10.
    // Ex: rawLen=32 → pad=16, paddedLen=48, todos bytes finais = 0x10.
    // Ex: rawLen=33 → pad=15, paddedLen=48, todos bytes finais = 0x0F.

    uint8_t pad        = 16 - (rawLen % 16);
    memset(raw + rawLen, pad, pad);
    size_t  paddedLen  = rawLen + pad;

    // ── 4. AES-128-ECB: encriptação bloco a bloco ─────────────────────────────
    //
    // Key: 16 bytes da local_key (índices 0–15).
    // Modo ECB sem IV — cada bloco de 16 bytes é cifrado independentemente.

    uint8_t encrypted[512];
    uint8_t key[16];
    memcpy(key, _localKey.c_str(), 16);

    if (!_aesECBEncrypt(key, raw, encrypted, paddedLen)) {
        Serial.println("[TuyaLocal] Falha na encriptação AES-128-ECB");
        return false;
    }

    // ── 5. Montagem do frame Tuya v3.3 ────────────────────────────────────────
    //
    // Layout completo (Big Endian):
    //
    //   [PREFIX 4B][SEQ 4B][CMD 4B][LEN 4B][PAYLOAD NB][CRC32 4B][SUFFIX 4B]
    //
    // LEN = len(PAYLOAD) + 4
    //   → conta apenas o payload cifrado + os 4 bytes do CRC.
    //   → o SUFFIX não entra no cálculo (é terminador fixo).
    //
    // CRC32 cobre: PREFIX + SEQ + CMD + LEN + PAYLOAD
    //   → NÃO inclui o próprio CRC nem o SUFFIX.
    //   → Polinômio CRC-32/ISO-HDLC (0xEDB88320), init=0xFFFFFFFF, XOR final=0xFFFFFFFF.
    //   → Inserido em Big Endian imediatamente antes do SUFFIX.

    _sequence++;
    uint32_t length = (uint32_t)paddedLen + 4; // payload + CRC (suffix excluído)

    uint8_t packet[600];
    size_t  pos = 0;

    auto write32BE = [&](uint32_t val) {
        packet[pos++] = (val >> 24) & 0xFF;
        packet[pos++] = (val >> 16) & 0xFF;
        packet[pos++] = (val >>  8) & 0xFF;
        packet[pos++] =  val        & 0xFF;
    };

    write32BE(TUYA_PREFIX);       // 00 00 55 AA
    write32BE(_sequence);         // número de sequência incremental
    write32BE(TUYA_CMD_CONTROL);  // 00 00 00 07
    write32BE(length);            // len(payload) + 4

    memcpy(packet + pos, encrypted, paddedLen);
    pos += paddedLen;

    // CRC calculado sobre tudo que foi escrito até aqui (header + payload)
    uint32_t crc = _crc32(packet, pos);
    write32BE(crc);               // CRC32 em Big Endian
    write32BE(TUYA_SUFFIX);       // 00 00 AA 55

    // ── 6. Envio via TCP ──────────────────────────────────────────────────────

    WiFiClient client;
    client.setTimeout(3000);

    if (!client.connect(_ip.c_str(), TUYA_PORT)) {
        Serial.printf("[TuyaLocal] Falha ao conectar em %s:%d\n",
                      _ip.c_str(), TUYA_PORT);
        return false;
    }

    size_t sent = client.write(packet, pos);
    client.flush();
    delay(200);
    client.stop();

    if (sent != pos) {
        Serial.printf("[TuyaLocal] Enviado %u de %u bytes\n", sent, pos);
        return false;
    }

    return true;
}

// ── Métodos privados ─────────────────────────────────────────────────────────

void TuyaLocal::_rgbToHSV(uint8_t r, uint8_t g, uint8_t b,
                           uint16_t& h, uint16_t& s, uint16_t& v) {
    float rf = r / 255.0f;
    float gf = g / 255.0f;
    float bf = b / 255.0f;

    float maxC  = fmaxf(fmaxf(rf, gf), bf);
    float minC  = fminf(fminf(rf, gf), bf);
    float delta = maxC - minC;

    // Value: 0–1000
    v = (uint16_t)(maxC * 1000.0f);

    // Saturation: 0–1000
    s = (maxC > 0.0f) ? (uint16_t)((delta / maxC) * 1000.0f) : 0;

    // Hue: 0–360
    if (delta == 0.0f) { h = 0; return; }

    float hf;
    if      (maxC == rf) hf = 60.0f * fmodf((gf - bf) / delta, 6.0f);
    else if (maxC == gf) hf = 60.0f * ((bf - rf) / delta + 2.0f);
    else                 hf = 60.0f * ((rf - gf) / delta + 4.0f);

    if (hf < 0.0f) hf += 360.0f;
    h = (uint16_t)hf;
}

uint32_t TuyaLocal::_crc32(const uint8_t* data, size_t len) {
    uint32_t crc = 0xFFFFFFFF;
    for (size_t i = 0; i < len; i++) {
        crc ^= data[i];
        for (int j = 0; j < 8; j++)
            crc = (crc & 1) ? ((crc >> 1) ^ 0xEDB88320) : (crc >> 1);
    }
    return crc ^ 0xFFFFFFFF;
}

bool TuyaLocal::_aesECBEncrypt(const uint8_t* key, const uint8_t* input,
                                uint8_t* output, size_t len) {
    mbedtls_aes_context ctx;
    mbedtls_aes_init(&ctx);

    if (mbedtls_aes_setkey_enc(&ctx, key, 128) != 0) {
        mbedtls_aes_free(&ctx);
        return false;
    }

    for (size_t i = 0; i < len; i += 16)
        mbedtls_aes_crypt_ecb(&ctx, MBEDTLS_AES_ENCRYPT, input + i, output + i);

    mbedtls_aes_free(&ctx);
    return true;
}
