#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
empacotar.py - monta o pacote de distribuicao e o manifest da auto atualizacao.

Uso normal (duas etapas, porque o Drive so da o ID depois do upload):

  1) python empacotar.py --versao 1.1.0
        -> gera dist\\insta-dl-1.1.0.zip e dist\\manifest.json

  2) sobe o .zip no Drive, compartilha como "qualquer pessoa com o link",
     copia o ID do link e roda:

     python empacotar.py --pacote-id <ID_DO_ZIP>
        -> preenche o pacote_url no dist\\manifest.json (sem refazer o zip)

  3) sobe o manifest.json no Drive pela opcao "Gerenciar versoes ->
     Enviar nova versao" do arquivo que JA existe. Isso mantem o mesmo ID,
     que e o link que todas as maquinas consultam.

Primeira vez (antes de existir qualquer coisa no Drive):
  - sobe um manifest.json qualquer no Drive, compartilha pelo link, copia o ID
  - python empacotar.py --manifest-id <ID_DO_MANIFEST> --versao 1.0.0
    (o ID vai gravado dentro do pacote, e e assim que as maquinas dos colegas
     descobrem para onde olhar)
"""

import json
import os
import re
import sys
import zipfile

AQUI = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(AQUI, "dist")

# O que vai no pacote.
#
# NAO entram arquivos .cmd/.bat/.ps1: script solto e o formato que as pessoas
# (com razao) aprenderam a nao clicar, e num parque com antivirus corporativo
# ele chama atencao a toa. O colega ve duas coisas: uma pagina HTML que abre no
# navegador e o .pyw que abre a janela. Os .cmd continuam existindo aqui na
# pasta de quem mantem - so nao sao distribuidos.
ARQUIVOS = [
    "COMECE-AQUI.html",
    "insta-dl.pyw",
    "baixar.py",
    "atualizacao.py",
    "README.md",
]
GERADOS = ["versao.json", "atualizacao.json"]
# Data fixa no zip: dois pacotes com o mesmo conteudo dao o mesmo sha256.
DATA_FIXA = (2026, 1, 1, 0, 0, 0)


def sha256_de(dados):
    import hashlib
    return hashlib.sha256(dados).hexdigest()


def ler_json(caminho, padrao=None):
    try:
        with open(caminho, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {} if padrao is None else padrao


def proxima_versao(atual):
    partes = [int(x) if x.isdigit() else 0 for x in re.split(r"[.\-+]", atual)]
    while len(partes) < 3:
        partes.append(0)
    partes[2] += 1
    return ".".join(str(x) for x in partes[:3])


def montar_zip(versao, config):
    os.makedirs(DIST, exist_ok=True)
    caminho = os.path.join(DIST, f"insta-dl-{versao}.zip")
    faltando = [a for a in ARQUIVOS if not os.path.exists(os.path.join(AQUI, a))]
    if faltando:
        print("ERRO: faltam arquivos no pacote: " + ", ".join(faltando))
        return None, None

    conteudo = {
        "versao.json": json.dumps({"versao": versao}, ensure_ascii=False, indent=2),
        "atualizacao.json": json.dumps(config, ensure_ascii=False, indent=2),
    }
    with zipfile.ZipFile(caminho, "w", zipfile.ZIP_DEFLATED) as z:
        for nome in ARQUIVOS:
            info = zipfile.ZipInfo(nome, date_time=DATA_FIXA)
            info.compress_type = zipfile.ZIP_DEFLATED
            with open(os.path.join(AQUI, nome), "rb") as f:
                z.writestr(info, f.read())
        for nome in GERADOS:
            info = zipfile.ZipInfo(nome, date_time=DATA_FIXA)
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, conteudo[nome].encode("utf-8"))

    with open(caminho, "rb") as f:
        digest = sha256_de(f.read())
    return caminho, digest


def main(argv):
    versao = None
    manifest_id = None
    pacote_id = None
    notas = None

    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("--versao", "--pacote-id", "--manifest-id", "--notas"):
            i += 1
            if i >= len(argv):
                print(f"ERRO: {arg} precisa de um valor.")
                return 2
            valor = argv[i]
            if arg == "--versao":
                versao = valor
            elif arg == "--pacote-id":
                pacote_id = valor
            elif arg == "--manifest-id":
                manifest_id = valor
            else:
                notas = valor
        elif arg in ("-h", "--help"):
            print(__doc__)
            return 0
        else:
            print(f"ERRO: opcao desconhecida: {arg}")
            return 2
        i += 1

    caminho_manifest = os.path.join(DIST, "manifest.json")

    # ---- etapa 2: so preenche o link do pacote no manifest ja gerado
    if pacote_id and not versao:
        manifesto = ler_json(caminho_manifest)
        if not manifesto:
            print("ERRO: rode primeiro com --versao para gerar o dist\\manifest.json.")
            return 1
        manifesto["pacote_url"] = (
            "https://drive.google.com/uc?export=download&id=" + pacote_id.strip()
        )
        with open(caminho_manifest, "w", encoding="utf-8") as f:
            json.dump(manifesto, f, ensure_ascii=False, indent=2)
        print(f"pacote_url preenchido em {caminho_manifest}")
        print("Agora suba esse manifest.json no Drive por 'Gerenciar versoes ->")
        print("Enviar nova versao' do arquivo que JA existe (mantem o mesmo ID).")
        return 0

    # ---- etapa 1: monta o zip e o manifest
    caminho_config = os.path.join(AQUI, "atualizacao.json")
    config = ler_json(caminho_config)
    if manifest_id:
        config["manifest_id"] = manifest_id.strip()
        config.pop("manifest_url", None)
        # Grava tambem AQUI, nao so dentro do zip. Sem isto o proximo release
        # feito sem repetir a flag sai com o id vazio e desliga a atualizacao
        # automatica de todas as maquinas - em silencio. Medido em 2026-10-02.
        with open(caminho_config, "w", encoding="utf-8", newline="\n") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        print(f"manifest_id gravado tambem em {caminho_config}")
    if not (config.get("manifest_id") or config.get("manifest_url")):
        print("AVISO: atualizacao.json sem manifest_id/manifest_url.")
        print("       O pacote vai funcionar, mas SEM auto atualizacao.")
        print("       Use --manifest-id <ID> para gravar o link no pacote.")

    atual = str(ler_json(os.path.join(AQUI, "versao.json")).get("versao") or "0.0.0")
    versao = versao or proxima_versao(atual)

    caminho, digest = montar_zip(versao, config)
    if not caminho:
        return 1

    manifesto = {
        "versao": versao,
        "pacote_url": ("https://drive.google.com/uc?export=download&id=" + pacote_id.strip())
                      if pacote_id else "COLE_AQUI_O_LINK_DO_ZIP",
        "sha256": digest,
        "notas": notas or "",
    }
    os.makedirs(DIST, exist_ok=True)
    with open(caminho_manifest, "w", encoding="utf-8") as f:
        json.dump(manifesto, f, ensure_ascii=False, indent=2)

    # a pasta local passa a ser a versao empacotada
    with open(os.path.join(AQUI, "versao.json"), "w", encoding="utf-8") as f:
        json.dump({"versao": versao}, f, ensure_ascii=False, indent=2)

    tamanho = os.path.getsize(caminho)
    print(f"pacote:   {caminho}  ({tamanho / 1024:.0f} KB)")
    print(f"sha256:   {digest}")
    print(f"manifest: {caminho_manifest}")
    print()
    print("No Google Drive:")
    print(f"  1. suba o {os.path.basename(caminho)} (arquivo novo) e compartilhe")
    print("     como 'qualquer pessoa com o link'; copie o ID do link")
    print(f"  2. python empacotar.py --pacote-id <ID>")
    print("  3. no manifest.json que JA existe no Drive: botao direito ->")
    print("     'Gerenciar versoes' -> 'Enviar nova versao' -> escolha o")
    print(f"     {caminho_manifest}")
    print()
    print("O ID do manifest.json NAO pode mudar: e o unico link que as maquinas")
    print("dos colegas conhecem. Por isso 'nova versao', nunca 'upload novo'.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
