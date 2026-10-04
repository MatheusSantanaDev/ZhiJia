#!/usr/bin/env python3
"""Puxa a tarifa vigente da CEMIG + bandeira tarifaria e imprime JSON.

Fonte: Portal de Dados Abertos da ANEEL (API CKAN publica, sem login).

  - Tarifas de aplicacao das distribuidoras: TE + TUSD por distribuidora
  - Bandeira tarifaria acionada no mes (adicional em R$/MWh)

O Home Assistant chama esse script pelo sensor `command_line`
(pacote homeassistant/config/packages/energia.yaml) e le o JSON da saida.

Saida (exemplo):
  {"tarifa_kwh": 0.90329, "bandeira": "Amarela", "custo_kwh": 0.92214, ...}
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://dadosabertos.aneel.gov.br/api/3/action/datastore_search"

# Tarifas de aplicacao das distribuidoras (TE + TUSD)
RECURSO_TARIFAS = "fcf2906c-7c32-4b9b-a637-054e7a5234f4"
# Acionamento mensal das bandeiras tarifarias
RECURSO_BANDEIRA = "0591b8f6-fe54-437b-b72b-1aa2efd46e42"

DISTRIBUIDORA = "CEMIG-D"  # SigAgente na base da ANEEL
SUBGRUPO = "B1"  # baixa tensao, consumidor residencial

# Cache local: evita bater na API da ANEEL a cada poll e cobre falha de rede
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cemig_tarifa_cache.json")
CACHE_TTL = 6 * 3600  # 6h

TIMEOUT = 20


def _api(params):
    """GET na API CKAN da ANEEL. Levanta excecao em qualquer falha."""
    url = "{API}?{qs}".format(API=API, qs=urllib.parse.urlencode(params))
    req = urllib.request.Request(url, headers={"User-Agent": "ZhiJia/1.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        dados = json.loads(resp.read().decode("utf-8"))
    if not dados.get("success"):
        raise RuntimeError("API da ANEEL retornou success=false")
    return dados["result"]


def _br(valor):
    """'593,08' -> 593.08 (aceita tambem separador de milhar: '1.234,56')."""
    texto = str(valor or "").strip()
    if "," in texto:
        if "." in texto:
            texto = texto.replace(".", "")
        texto = texto.replace(",", ".")
    return float(texto or 0)


def tarifa_cemig():
    """Tarifa de aplicacao vigente da CEMIG para residencial B1 convencional."""
    resultado = _api({
        "resource_id": RECURSO_TARIFAS,
        "filters": json.dumps({
            "SigAgente": DISTRIBUIDORA,
            "DscSubGrupo": SUBGRUPO,
            "DscBaseTarifaria": "Tarifa de Aplicação",
            "DscModalidadeTarifaria": "Convencional",
            "DscSubClasse": "Residencial",
            "DscDetalhe": "Não se aplica",
        }),
        "fields": "DatInicioVigencia,DatFimVigencia,VlrTUSD,VlrTE,DscModalidadeTarifaria",
        "sort": "DatInicioVigencia desc",
        "limit": 1,
    })
    registros = resultado.get("records") or []
    if not registros:
        raise RuntimeError("nenhum registro de tarifa CEMIG encontrado na ANEEL")

    reg = registros[0]
    tusd = _br(reg.get("VlrTUSD"))
    te = _br(reg.get("VlrTE"))
    return {
        "tusd_mwh": round(tusd, 2),          # R$/MWh
        "te_mwh": round(te, 2),              # R$/MWh
        "tarifa_kwh": round((tusd + te) / 1000.0, 5),  # R$/kWh (sem impostos)
        "vigencia_inicio": reg.get("DatInicioVigencia", ""),
        "vigencia_fim": reg.get("DatFimVigencia", ""),
        "modalidade": reg.get("DscModalidadeTarifaria", ""),
    }


def bandeira_atual():
    """Bandeira tarifaria acionada no mes de competencia mais recente."""
    resultado = _api({
        "resource_id": RECURSO_BANDEIRA,
        "fields": "DatCompetencia,NomBandeiraAcionada,VlrAdicionalBandeira",
        "sort": "DatCompetencia desc",
        "limit": 1,
    })
    registros = resultado.get("records") or []
    if not registros:
        raise RuntimeError("nenhum registro de bandeira tarifaria encontrado")

    reg = registros[0]
    adicional = _br(reg.get("VlrAdicionalBandeira"))
    return {
        "bandeira": reg.get("NomBandeiraAcionada", ""),
        "bandeira_competencia": reg.get("DatCompetencia", "")[:7],  # AAAA-MM
        "adicional_bandeira_kwh": round(adicional / 1000.0, 5),     # R$/MWh -> R$/kWh
    }


def montar():
    tarifa = tarifa_cemig()
    bandeira = bandeira_atual()
    saida = {}
    saida.update(tarifa)
    saida.update(bandeira)
    # O que a concessionaria cobra por kWh hoje = tarifa + bandeira
    saida["custo_kwh"] = round(saida["tarifa_kwh"] + saida["adicional_bandeira_kwh"], 5)
    saida["fonte"] = "ANEEL Dados Abertos"
    saida["atualizado_em"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    saida["stale"] = False
    return saida


def ler_cache():
    try:
        with open(CACHE, encoding="utf-8") as arquivo:
            dados = json.load(arquivo)
        if isinstance(dados, dict) and dados.get("custo_kwh") is not None:
            dados["stale"] = True
            return dados
    except (OSError, ValueError):
        pass
    return None


def gravar_cache(dados):
    try:
        with open(CACHE, "w", encoding="utf-8") as arquivo:
            json.dump(dados, arquivo, ensure_ascii=False)
    except OSError:
        pass  # cache e melhor esforco


def main():
    try:
        dados = montar()
        gravar_cache(dados)
    except (urllib.error.URLError, OSError, RuntimeError, ValueError, KeyError) as erro:
        # Sem rede/API: entrega o ultimo valor conhecido para o HA nao perder historico
        dados = ler_cache()
        if dados is None:
            print("falha ao consultar a ANEEL: {0}".format(erro), file=sys.stderr)
            return 1
        print("ANEEL indisponivel ({0}); usando cache".format(erro), file=sys.stderr)

    print(json.dumps(dados, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
