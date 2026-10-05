#!/usr/bin/env python3
"""Puxa o consumo de agua do Dmae (Uberlandia/MG) e imprime JSON.

O Dmae (autarquia municipal) nao expoe API publica, mas o portal do DCDR/PRODAUB
(sistema de consultas da Prefeitura) tem o relatorio "Extrato de Consumo Imoveis"
que roda SEM login: basta o codigo I.D.A. de 10 digitos que vem no topo de toda
fatura de agua.

  https://dcdr.uberlandia.mg.gov.br/dcdr/f/n/relconsumoimoveisrel

O fluxo tem 3 passos:
  1. GET na pagina do formulario (JSF/Trinidad): captura sessionid + ViewState;
  2. POST com `source` = botao "Gerar Relatorio" -> devolve no campo oculto
     `relatorioGeracao` a URL do relatorio BIRT gerado (plcVis372);
  3. GET nessa URL trocando `frameset`/`__format=pdf` por `output`/`__format=html`,
     que entrega o extrato como HTML tabelado (da la pra puxar o PDF, mas ai
     precisaria de lib de PDF - o HTML e parsed com a stdlib).

O portal fica atras de um Akamai que so deixa passar HTTP/2 com header-set de
browser, por isso as chamadas sao feitas pelo `curl --http2` (requests/urllib
levam 403).

O Home Assistant chama esse script pelo sensor `command_line`
(pacote homeassistant/config/packages/agua.yaml) e le o JSON da saida.

Uso:
  python3 dmae_consumo.py             # consulta, cacheia e imprime JSON (o HA faz isso)
  python3 dmae_consumo.py --dump      # salva o HTML do extrato em dmae_ultimo_extrato.html
  python3 dmae_consumo.py --ida 1234567890   # usa outro I.D.A. (nao grava segredo)

O I.D.A. fica em dmae_secrets.json (gitignorado; copie o .example.json).
Tambem e aceito o ambiente DMAE_IDA.
"""

import html as html_mod
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unicodedata
from datetime import datetime
from html.parser import HTMLParser

URL = "https://dcdr.uberlandia.mg.gov.br/dcdr/f/n/relconsumoimoveisrel"
BASE = "https://dcdr.uberlandia.mg.gov.br"

AQUI = os.path.dirname(os.path.abspath(__file__))
SEGREDO = os.path.join(AQUI, "dmae_secrets.json")
CACHE = os.path.join(AQUI, "dmae_consumo_cache.json")
BRUTO = os.path.join(AQUI, "dmae_ultimo_extrato.html")
CACHE_TTL = 6 * 3600  # 6h
TIMEOUT = 60

# O Akamai do portal recusa clientes padrao (python - 403). O curl com HTTP/2
# e header-set de browser passa; qualquer coisa a menos cai no bloqueio.
CURL = [
    "curl", "-s", "--http2", "--compressed", "-L", "--max-time", str(TIMEOUT),
    "-H", "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "-H", "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,"
          "image/avif,image/webp,*/*;q=0.8",
    "-H", "Accept-Language: pt-BR,pt;q=0.9,en;q=0.8",
    "-H", 'sec-ch-ua: "Chromium";v="125", "Not.A/Brand";v="24"',
    "-H", "sec-ch-ua-mobile: ?0",
    "-H", 'sec-ch-ua-platform: "macOS"',
    "-H", "Sec-Fetch-Dest: document",
    "-H", "Sec-Fetch-Mode: navigate",
    "-H", "Sec-Fetch-Site: none",
    "-H", "Sec-Fetch-User: ?1",
    "-H", "Upgrade-Insecure-Requests: 1",
]


# ---------------------------------------------------------------- http


def _curl(args, jar, referencia=URL):
    """Roda o curl guardando cookies no jar e devolve (http_code, corpo)."""
    cmd = CURL + ["-c", jar, "-b", jar, "-H", "Referer: " + referencia,
                  *args, "-o", "-", "-w", "%{http_code}"]
    proc = subprocess.run(cmd, capture_output=True, timeout=TIMEOUT + 30)
    corpo = proc.stdout
    codigo = corpo[-3:].decode("ascii", "replace")
    return codigo, corpo[:-3].decode("utf-8", "replace")


def _attr(tag, nome):
    m = re.search(r'{0}="([^"]*)"'.format(re.escape(nome)), tag)
    return html_mod.unescape(m.group(1)) if m else ""


def _formulario(pagina):
    """Devolve (action, ViewState) do form principal do relatorio."""
    form = re.search(r'<form id="corpo:formulario".*?</form>', pagina, re.S)
    if not form:
        raise RuntimeError("formuluario do relatorio nao encontrado no portal")
    acao = _attr(re.search(r"<form[^>]*>", form.group(0)).group(0), "action")
    vs = re.search(r'<input[^>]*name="javax\.faces\.ViewState"[^>]*>', form.group(0))
    if not acao or not vs:
        raise RuntimeError("action/ViewState ausentes no portal")
    return acao, _attr(vs.group(0), "value")


def _url_relatorio(pagina):
    """URL do relatorio BIRT que o portal devolve depois de gerar."""
    m = re.search(r'name="corpo:formulario:relatorioGeracao"\s+value="([^"]*)"', pagina)
    if not m:
        m = re.search(r'id="corpo:formulario:relatorioGeracao"[^>]*value="([^"]*)"', pagina)
    return html_mod.unescape(m.group(1)) if m else ""


def consultar(ida):
    """Devolve o HTML do extrato de consumo (relatorio gerado)."""
    with tempfile.TemporaryDirectory() as tmp:
        jar = os.path.join(tmp, "cookies.txt")

        codigo, pagina = _curl([URL], jar)
        if codigo != "200":
            raise RuntimeError("portal DCDR respondeu HTTP {0} no GET".format(codigo))
        acao, viewstate = _formulario(pagina)

        campos = [
            ("corpo:formulario:cdIdentificador", ida),
            ("corpo:formulario:relatorioGeracao", ""),
            ("source", "corpo:formulario:botaoAcaoGerarRelatorio"),
            ("javax.faces.ViewState", viewstate),
            ("org.apache.myfaces.trinidad.faces.FORM", "corpo:formulario"),
            ("_noJavaScript", "false"),
            ("detCorrPlc", ""), ("tabCorrPlc", ""), ("detCorrPlcPaginado", ""),
            ("exibeEdDocPlc", ""), ("indExcDetPlc", ""),
        ]
        args = ["-H", "Origin: " + BASE,
                "-H", "Content-Type: application/x-www-form-urlencoded",
                "-X", "POST"]
        for chave, valor in campos:
            args += ["--data-urlencode", "{0}={1}".format(chave, valor)]
        args.append(BASE + acao)
        codigo, resposta = _curl(args, jar, referencia=URL)
        if codigo != "200":
            raise RuntimeError("portal DCDR respondeu HTTP {0} no POST".format(codigo))

        if re.search(r"Nenhum resultado encontrado para este I\.D\.A",
                     texto_limpo(resposta), re.I):
            raise RuntimeError("Nenhum resultado encontrado para o I.D.A. informado")

        relatorio = _url_relatorio(resposta)
        if not relatorio:
            raise RuntimeError("portal nao devolveu a URL do relatorio")

        # frameset/pdf abre num popup; o endpoint `output` em html entrega o
        # extrato pronto (mesmo relatorio, outro formato de saida).
        relatorio = relatorio.replace("http://", "https://")
        relatorio = relatorio.replace("/plcVis372/frameset?", "/plcVis372/output?")
        relatorio = re.sub(r"__format=\w+", "__format=html", relatorio)

        codigo, extrato = _curl([relatorio], jar, referencia=URL)
        if codigo != "200":
            raise RuntimeError("relatorio respondeu HTTP {0}".format(codigo))
        return extrato


# ------------------------------------------------------------ parser


class _Tabelas(HTMLParser):
    """Coleta <table> como listas de celulas ja convertidas em texto."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.atual = None
        self.linha = None
        self.celula = None
        self.tabelas = []

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.atual = []
        elif tag == "tr" and self.atual is not None:
            self.linha = []
        elif tag in ("td", "th") and self.linha is not None:
            self.celula = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.celula is not None:
            self.linha.append(" ".join("".join(self.celula).split()))
            self.celula = None
        elif tag == "tr" and self.linha is not None:
            if any(self.linha):
                self.atual.append(self.linha)
            self.linha = None
        elif tag == "table" and self.atual is not None:
            if self.atual:
                self.tabelas.append(self.atual)
            self.atual = None

    def handle_data(self, dado):
        if self.celula is not None:
            self.celula.append(dado)


def texto_limpo(pagina):
    """HTML -> texto unico (sem scripts/menus) para casar rotulos."""
    pagina = re.sub(r"<script.*?</script>", " ", pagina, flags=re.S | re.I)
    pagina = re.sub(r"<style.*?</style>", " ", pagina, flags=re.S | re.I)
    return " ".join(html_mod.unescape(re.sub(r"<[^>]+>", " ", pagina)).split())


def numero(valor):
    """'1.234,56' -> 1234.56 (aceita tambem ponto como decimal)."""
    texto = str(valor or "").strip().replace("R$", "").strip()
    if not texto:
        return None
    if "," in texto:
        if "." in texto:
            texto = texto.replace(".", "")
        texto = texto.replace(",", ".")
    try:
        return float(texto)
    except ValueError:
        return None


def _ascii(texto):
    """'Hidrômetro:' -> 'Hidrometro:' (o extrato sai com acento)."""
    texto = unicodedata.normalize("NFKD", str(texto or ""))
    return texto.encode("ascii", "ignore").decode("ascii")


def _chave(texto):
    """Rotulo sem acento/pontuacao: 'Imóvel:' -> 'imovel'."""
    return re.sub(r"\W+", "", _ascii(texto)).lower()


def _cabecalho(tabela):
    """Le os dados do imovel no bloco de cabecalho do extrato."""
    info = {}
    economias = {}
    for linha in tabela:
        for idx, celula in enumerate(linha):
            rotulo, _, valor = celula.partition(":")
            valor = valor.strip()
            chave = _chave(rotulo)
            if not valor and idx + 1 < len(linha):
                valor = linha[idx + 1].strip()
            if chave in ("imovel", "endereco", "proprietario",
                         "hidrometro", "capacidade", "consumomedio"):
                info[chave] = valor
            eco = re.match(r"^(Residencia|Comercio|Industria|Publica)\s+([0-9.,]+)$",
                           _ascii(celula).strip(), re.I)
            if eco:
                economias[eco.group(1).lower()] = numero(eco.group(2))
    if economias:
        info["economias"] = economias
    return info


def _linhas_extrato(tabelas):
    """Acha a tabela do extrato e devolve (cabecalho, linhas)."""
    for tabela in tabelas:
        for pos, linha in enumerate(tabela):
            if any("Consumo Medido" in c for c in linha):
                return linha, tabela[pos + 1:]
    return None, []


def parsear(pagina, ida):
    """Extrai o consumo do HTML do extrato gerado pelo BIRT."""
    if re.search(r"Nenhum resultado encontrado para este I\.D\.A", texto_limpo(pagina), re.I):
        raise RuntimeError("Nenhum resultado encontrado para o I.D.A. informado")

    parser = _Tabelas()
    parser.feed(pagina)

    cab, linhas = _linhas_extrato(parser.tabelas)
    if not cab:
        raise RuntimeError("tabela de consumo nao encontrada no extrato")

    def col(rotulo, padrao):
        for idx, titulo in enumerate(cab):
            if rotulo.lower() in titulo.lower():
                return idx
        return padrao

    i_data = col("Data Leitura", 1)
    i_medido = col("Consumo Medido", 2)
    i_faturado = col("Consumo Faturado", 3)
    i_ocorr = col("Ocorrência", 4)
    i_dias = col("Dias", 5)

    historico = []
    for linha in linhas:
        if max(i_data, i_medido, i_faturado) >= len(linha):
            continue
        data_bruta = linha[i_data].strip()
        m_data = re.match(r"(\d{2}/\d{2}/\d{4})(?:\s+(\d{2}:\d{2}))?", data_bruta)
        if not m_data:
            continue
        data = m_data.group(1)
        consumo = numero(linha[i_medido])
        if consumo is None:
            continue
        try:
            dia, mes, ano = data.split("/")
            competencia = "{0}/{1}".format(mes, ano)
        except ValueError:
            competencia = ""
        historico.append({
            "competencia": competencia,
            "data_leitura": data,
            "hora_leitura": m_data.group(2) or "",
            "consumo_m3": consumo,
            "consumo_faturado_m3": numero(linha[i_faturado]),
            "ocorrencia": linha[i_ocorr].strip() if i_ocorr < len(linha) else "",
            "dias": numero(linha[i_dias]) if i_dias < len(linha) else None,
        })

    if not historico:
        raise RuntimeError("extrato veio sem linhas de consumo")

    # Extrato vem do mes mais recente para o mais antigo
    ultimo = historico[0]
    info = {}
    for tabela in parser.tabelas:
        achado = _cabecalho(tabela)
        if achado.get("imovel"):
            info = achado
            break

    return {
        "consumo_m3": ultimo["consumo_m3"],
        "competencia": ultimo["competencia"],
        "data_leitura": ultimo["data_leitura"],
        "hora_leitura": ultimo["hora_leitura"],
        "dias": ultimo["dias"],
        "ocorrencia": ultimo["ocorrencia"],
        "consumo_faturado_m3": ultimo["consumo_faturado_m3"],
        "consumo_medio_m3": numero(info.get("consumomedio")),
        "capacidade": numero(info.get("capacidade")),
        "imovel": info.get("imovel", ""),
        "hidrometro": info.get("hidrometro", ""),
        "economias": info.get("economias", {}),
        "historico": historico,
        "ida": "****" + str(ida)[-4:],
    }


# ----------------------------------------------------------- cache


def ler_cache():
    try:
        with open(CACHE, encoding="utf-8") as arquivo:
            dados = json.load(arquivo)
        if isinstance(dados, dict) and dados.get("consumo_m3") is not None:
            dados["stale"] = True
            return dados
    except (OSError, ValueError):
        pass
    return None


def gravar_cache(dados):
    try:
        with open(CACHE, "w", encoding="utf-8") as arquivo:
            json.dump(dados, arquivo, ensure_ascii=False, indent=2)
    except OSError:
        pass  # cache e melhor esforco


def _acumular(dados, cache):
    """Soma os m3 ja vistos por competencia (acumulado monotonic pro HA).

    O extrato traz ~2 anos de historico; mantemos o acumulado no cache para o
    sensor nao cair quando o portal deixar de devolver uma competencia antiga.
    """
    vistos = cache.get("vistos") if isinstance(cache, dict) else None
    if not isinstance(vistos, dict):
        vistos = {}
    for item in dados.get("historico") or []:
        chave = str(item.get("competencia") or "").strip()
        consumo = item.get("consumo_m3")
        if chave and consumo is not None:
            vistos[chave] = consumo
    dados["vistos"] = vistos
    dados["acumulado_m3"] = round(sum(float(v) for v in vistos.values()), 3)
    return dados


# ------------------------------------------------------------- main


def carregar_ida(explicito=None):
    if explicito:
        bruto = explicito
    elif os.environ.get("DMAE_IDA"):
        bruto = os.environ["DMAE_IDA"]
    else:
        try:
            with open(SEGREDO, encoding="utf-8") as arquivo:
                bruto = str(json.load(arquivo).get("ida") or "")
        except (OSError, ValueError):
            bruto = ""
    # O I.D.A. vem formatado como "000280463-8"; o campo do portal aceita
    # so os 10 digitos (maxlength=10, validador de digito).
    ida = re.sub(r"\D", "", bruto)
    if len(ida) != 10:
        raise RuntimeError(
            "I.D.A. nao configurado ou invalido: copie dmae_secrets.example.json "
            "para dmae_secrets.json (gitignorado) e preencha o campo 'ida' com "
            "os 10 digitos do topo da fatura do Dmae (ou exporte DMAE_IDA)"
        )
    return ida


def montar(ida, dump=False):
    pagina = consultar(ida)
    if dump:
        with open(BRUTO, "w", encoding="utf-8") as arquivo:
            arquivo.write(pagina)
        print("HTML salvo em {0}".format(BRUTO), file=sys.stderr)
    dados = parsear(pagina, ida)
    dados = _acumular(dados, ler_cache() or {})
    dados["fonte"] = "DCDR/PRODAUB - Extrato de Consumo Imoveis (Dmae Uberlandia)"
    dados["consultado_em"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    dados["stale"] = False
    return dados


def main():
    dump = "--dump" in sys.argv
    ida = None
    if "--ida" in sys.argv:
        ida = sys.argv[sys.argv.index("--ida") + 1]
    ida = carregar_ida(ida)

    try:
        dados = montar(ida, dump=dump)
        gravar_cache(dados)
    except (RuntimeError, OSError, subprocess.SubprocessError, ValueError) as erro:
        dados = ler_cache()
        if dados is None:
            print("falha ao consultar o Dmae: {0}".format(erro), file=sys.stderr)
            return 1
        print("Dmae indisponivel ({0}); usando cache".format(erro), file=sys.stderr)

    print(json.dumps(dados, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
