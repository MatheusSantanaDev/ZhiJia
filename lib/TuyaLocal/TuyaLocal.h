#pragma once

#include <Arduino.h>
#include <WiFiClient.h>

/**
 * TuyaLocal — Protocolo Tuya Local v3.3 para ESP32
 *
 * Controla dispositivos Tuya diretamente na rede local via TCP (porta 6668),
 * sem depender de cloud ou internet. Usa AES-128-ECB (mbedTLS) e CRC32.
 *
 * Uso básico:
 *   TuyaLocal strip("192.168.0.181", "device-id", "local-key-16b");
 *   strip.setPower(true);
 *   strip.setColor(255, 100, 0);
 */
class TuyaLocal {
public:
    /**
     * @param ip       IP local do dispositivo na rede
     * @param deviceId ID do dispositivo (Tuya device ID)
     * @param localKey Chave local de 16 caracteres
     */
    TuyaLocal(const String& ip, const String& deviceId, const String& localKey);

    /** Liga ou desliga o dispositivo (DP 20) */
    bool setPower(bool on);

    /**
     * Define a cor RGB (converte internamente para HSV no formato Tuya).
     * Também garante que o dispositivo esteja ligado (DP 20 = true).
     * @param r Red   0–255
     * @param g Green 0–255
     * @param b Blue  0–255
     */
    bool setColor(uint8_t r, uint8_t g, uint8_t b);

    /**
     * Envia um DPS (Data Points) arbitrário em JSON.
     * Útil para comandos avançados não cobertos pelos métodos acima.
     * @param dpsJson JSON com os DPs, ex: {"20":true,"21":"colour"}
     */
    bool sendDPS(const char* dpsJson);

private:
    String   _ip;
    String   _deviceId;
    String   _localKey;
    uint32_t _sequence;

    void     _rgbToHSV(uint8_t r, uint8_t g, uint8_t b,
                       uint16_t& h, uint16_t& s, uint16_t& v);
    uint32_t _crc32(const uint8_t* data, size_t len);
    bool     _aesECBEncrypt(const uint8_t* key, const uint8_t* input,
                            uint8_t* output, size_t len);
};
