#!/usr/bin/env python3
"""Importa o historico do extrato Dmae para as estatisticas de longo prazo do HA.

Serve para reconstruir o grafico de agua do Energy Dashboard quando o banco de
dados for recriado (o .db nao e versionado) - o extrato traz ~31 meses, mas o
Home Assistant so grava o que observa ao vivo, entao sem este passo o painel
comeca do zero.

    1. docker stop zhijia-homeassistant
    2. python3 homeassistant/config/scripts/dmae_backfill_stats.py
    3. docker start zhijia-homeassistant

O passo 1 nao e opcional: NUNCA abra o banco pelo host com o HA rodando (nem
este script, nem sqlite3, nem um script Python ad-hoc). Pelo VirtioFS o
processo do host nao enxerga os locks do container, acredita ser o ultimo
usuario do banco e, ao fechar, faz checkpoint e APAGA os arquivos -wal e -shm
de baixo do HA. O HA fica com descritores orfaos, dois indices WAL passam a
coexistir e toda conexao nova falha com "disk I/O error" ou "database disk
image is malformed" - que e exatamente o que quebra a pagina de Energia com
"unknown_error". O script recusa a rodar se detectar uma conexao aberta (o
-existe so enquanto houver conexao); --forcar ignora a trava por conta e risco.

Para INSPECIONAR o banco com o HA no ar, va pelo container:

    docker exec -i zhijia-homeassistant python3 -c \
      "import sqlite3;print(sqlite3.connect('file:/config/home-assistant_v2.db?mode=ro',uri=True).execute('PRAGMA integrity_check').fetchone())"

Convencao: zero point = 0 no comeco da serie, ou seja sum == state em todas as
linhas. As linhas de hoje (longo prazo E as de 5 min) tambem sao atualizadas
porque o recorder encadeia o sum a partir delas:

    sensor/recorder.py      _sum = ultimo sum de 5 min
    statistics.py           sum horario = ultimo sum de 5 min da hora

Se as tres pecas discordassem, o proximo lancamento horario jogaria o sum de
volta para 0 e o Energy veria um buraco no meio do grafico.
"""

import datetime
import json
import sqlite3
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parents[1]  # .../homeassistant/config
DB = BASE / "home-assistant_v2.db"
CACHE = BASE / "scripts" / "dmae_consumo_cache.json"
BACKUP = BASE / "home-assistant_v2.backup.db"
TZ = ZoneInfo("America/Sao_Paulo")

ALVOS = {
    "sensor.consumo_acumulado_do_predio": "acumulado",
    "sensor.consumo_de_agua_dmae": "consumo_mes",
    "sensor.consumo_de_agua_faturado_do_predio": "faturado",
}

COLUNAS = (
    "created, created_ts, metadata_id, start, start_ts, mean, mean_weight, "
    "min, max, last_reset, last_reset_ts, state, sum"
)


def quando(linha):
    """Momento da leitura, arredondado para a hora (LTS e horaria)."""
    dia, mes, ano = linha["data_leitura"].split("/")
    hora, minuto = (linha.get("hora_leitura") or "00:00").split(":")
    momento = datetime.datetime(
        int(ano), int(mes), int(dia), int(hora), int(minuto), tzinfo=TZ
    )
    return momento.replace(minute=0, second=0, microsecond=0)


def main():
    dry = "--dry-run" in sys.argv
    if not DB.exists():
        print(f"ERRO: banco nao encontrado: {DB}", file=sys.stderr)
        return 1
    if not CACHE.exists():
        print(
            f"ERRO: cache do extrato nao encontrado: {CACHE}\n"
            "Rode 'python3 dmae_consumo.py' uma vez para gerar o historico.",
            file=sys.stderr,
        )
        return 1

    historico = json.loads(CACHE.read_text(encoding="utf-8"))["historico"]
    historico.sort(key=quando)

    # Trava de seguranca: o -shm so existe enquanto ha alguma conexao aberta
    # no banco. Abrir aqui com o HA rodando e o que quebra o recorder (ver
    # o docstring).
    if (BASE / "home-assistant_v2.db-shm").exists() and "--forcar" not in sys.argv:
        print(
            "ERRO: existe home-assistant_v2.db-shm ao lado do banco, ou seja\n"
            "      alguem (o HA) esta com ele aberto. Pare o Home Assistant:\n"
            "          docker stop zhijia-homeassistant\n"
            "      Se o HA ja parou e o -shm ficou para tras, apague o -shm e o -wal\n"
            "      (o banco em si nao toca) ou use --forcar por conta e risco.\n"
            "      Para SO inspecionar com o HA no ar, use docker exec (ver docstring).",
            file=sys.stderr,
        )
        return 1

    con = sqlite3.connect(DB)
    try:
        metas = dict(con.execute("SELECT statistic_id, id FROM statistics_meta"))
        faltando = [entidade for entidade in ALVOS if entidade not in metas]
        if faltando:
            print(f"ERRO: sem metadata para {faltando}", file=sys.stderr)
            return 1

        if not dry:
            # backup atomico (inclusive o WAL) antes de qualquer escrita
            origem = sqlite3.connect(DB)
            destino = sqlite3.connect(BACKUP)
            with destino:
                origem.backup(destino)
            destino.close()
            origem.close()
            print(f"backup -> {BACKUP} ({BACKUP.stat().st_size} bytes)")

        agora = time.time()
        linhas = []
        acumulado = 0.0
        for item in historico:
            ts = quando(item).timestamp()
            consumo = float(item["consumo_m3"])
            faturado = float(item["consumo_faturado_m3"])
            acumulado += consumo
            # as primeiras competencias do extrato vem com 0 m3 - ali esta o
            # zero point natural da serie
            linhas.append(
                (metas["sensor.consumo_acumulado_do_predio"], ts, acumulado)
            )
            linhas.append((metas["sensor.consumo_de_agua_dmae"], ts, consumo))
            linhas.append(
                (metas["sensor.consumo_de_agua_faturado_do_predio"], ts, faturado)
            )

        conflitos = [
            (mid, ts)
            for mid, ts, _ in linhas
            if con.execute(
                "SELECT 1 FROM statistics WHERE metadata_id=? AND start_ts=?",
                (mid, ts),
            ).fetchone()
        ]

        if dry:
            print(
                f"dry-run: {len(linhas)} linhas seriam inseridas "
                f"({len(historico)} por entidade), de "
                f"{datetime.datetime.fromtimestamp(linhas[0][1], TZ):%d/%m/%Y} ate "
                f"{datetime.datetime.fromtimestamp(max(l[1] for l in linhas), TZ):%d/%m/%Y}"
            )
            print(f"          acumulado final: {acumulado:.1f} m3")
            print(
                f"          conflitos com linhas existentes: {len(conflitos)}"
                + (" (historico ja importado)" if conflitos else "")
            )
            print("          nenhuma gravacao foi feita")
            return 0

        if conflitos:
            print(
                f"ERRO: {len(conflitos)} linhas ja existem (importacao repetida?): "
                f"{conflitos[:5]}",
                file=sys.stderr,
            )
            return 1

        with con:
            con.executemany(
                f"INSERT INTO statistics ({COLUNAS}) VALUES "
                "(NULL, ?, ?, NULL, ?, NULL, NULL, NULL, NULL, NULL, NULL, ?, ?)",
                [(agora, mid, ts, valor, valor) for mid, ts, valor in linhas],
            )
            alvos = tuple(metas[entidade] for entidade in ALVOS)
            placeholders = ",".join("?" * len(alvos))
            lt = con.execute(
                f"UPDATE statistics SET sum = state "
                f"WHERE metadata_id IN ({placeholders}) AND state IS NOT NULL",
                alvos,
            ).rowcount
            st = con.execute(
                f"UPDATE statistics_short_term SET sum = state "
                f"WHERE metadata_id IN ({placeholders}) AND state IS NOT NULL",
                alvos,
            ).rowcount

        print(f"insert de {len(linhas)} linhas + update LT={lt} ST={st}")

        ok = True
        for entidade, nome in ALVOS.items():
            mid = metas[entidade]
            n_lt = con.execute(
                "SELECT count(*) FROM statistics WHERE metadata_id=?", (mid,)
            ).fetchone()[0]
            serie = con.execute(
                "SELECT start_ts, state, sum FROM statistics WHERE metadata_id=? "
                "ORDER BY start_ts",
                (mid,),
            ).fetchall()
            nulos = sum(1 for linha in serie if linha[2] is None)
            ultima_st = con.execute(
                "SELECT sum FROM statistics_short_term WHERE metadata_id=? "
                "ORDER BY start_ts DESC LIMIT 1",
                (mid,),
            ).fetchone()
            coerente = serie[-1][2] == ultima_st[0]
            monotonico = all(
                serie[i][2] <= serie[i + 1][2] + 1e-9
                for i in range(len(serie) - 1)
            )
            ok &= coerente
            print(
                f"  {nome:12s} LT={n_lt:3d} "
                f"inicio={datetime.datetime.fromtimestamp(serie[0][0], TZ):%d/%m/%Y} "
                f"sum={serie[0][2]:.1f} | fim sum={serie[-1][2]:.1f} "
                f"state={serie[-1][1]:.1f} | ultima ST={ultima_st[0]:.1f} | "
                f"monotonico={monotonico} nulos={nulos} coerente={coerente}"
            )
        print(f"acumulado do historico: {acumulado:.1f} m3")
        print("RESULTADO:", "OK" if ok else "VERIFICAR")
        return 0 if ok else 1
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
