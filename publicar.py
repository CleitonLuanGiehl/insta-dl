#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
publicar.py - publica uma versao nova no GitHub, do inicio ao fim.

    python publicar.py --versao 1.1.8 --notas "o que mudou"
    python publicar.py --versao 1.1.8 --simular      (nao toca em nada)

Por que isto e um script e nao uma sequencia de comandos na mao: em
2026-10-02 uma release foi montada ANTES da ultima correcao e publicada
defasada do fonte. Build verde, teste verde, artefato errado. A ordem aqui
nao e preferencia - e a correcao daquele defeito:

  1. constroi os dois pacotes na versao pedida
  2. CONFERE que o que foi empacotado e igual ao fonte, byte a byte
     (e este passo que pega o artefato defasado)
  3. cria a release e sobe os dois assets
  4. escreve o manifest.json com a URL do asset e o sha256
  5. commita e empurra
  6. confere ANONIMAMENTE que o manifest e o pacote estao no ar e que o
     sha256 bate - ou seja, que uma maquina de colega conseguiria atualizar

Falha em qualquer passo para o resto. Ninguem publica metade.
"""

import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request
import zipfile

AQUI = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(AQUI, "dist")
REPO = "CleitonLuanGiehl/insta-dl"
RAW = f"https://raw.githubusercontent.com/{REPO}/main/manifest.json"
# o que vai dentro do pacote pequeno, para a conferencia do passo 2
NO_PACOTE = ["COMECE-AQUI.html", "insta-dl.pyw", "baixar.py", "atualizacao.py",
             "README.md"]


def log(msg):
    print(msg, flush=True)


def rodar(argv, simular=False):
    if simular:
        log("   [simulacao] " + " ".join(argv))
        return 0, ""
    p = subprocess.run(argv, capture_output=True, text=True, cwd=AQUI)
    if p.returncode != 0:
        log("   ! falhou: " + " ".join(argv))
        log((p.stderr or p.stdout or "").strip()[-800:])
    return p.returncode, (p.stdout or "")


def sha256_de(caminho):
    with open(caminho, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def conferir_pacote(caminho):
    """
    O passo que pega o artefato defasado: cada arquivo de codigo dentro do
    zip tem que ser igual ao que esta no fonte, byte a byte.
    """
    divergentes = []
    with zipfile.ZipFile(caminho) as z:
        for nome in NO_PACOTE:
            no_fonte = os.path.join(AQUI, nome)
            if not os.path.exists(no_fonte):
                divergentes.append(nome + " (ausente no fonte)")
                continue
            with open(no_fonte, "rb") as f:
                if z.read(nome) != f.read():
                    divergentes.append(nome)
    return divergentes


def conferir_no_ar(versao, esperado, tentativas=6):
    """O raw do GitHub tem cache curto; vale insistir algumas vezes."""
    for tentativa in range(1, tentativas + 1):
        try:
            req = urllib.request.Request(RAW, headers={"User-Agent": "insta-dl"})
            with urllib.request.urlopen(req, timeout=30) as r:
                manifesto = json.loads(r.read().decode())
            if manifesto.get("versao") == versao:
                req = urllib.request.Request(
                    manifesto["pacote_url"], headers={"User-Agent": "insta-dl"})
                with urllib.request.urlopen(req, timeout=180) as r:
                    dados = r.read()
                obtido = hashlib.sha256(dados).hexdigest()
                if obtido != esperado:
                    return False, f"sha256 no ar difere: {obtido[:16]}..."
                if not zipfile.is_zipfile(__import__("io").BytesIO(dados)):
                    return False, "o que esta no ar nao e um zip"
                return True, f"{len(dados)} bytes, sha256 confere"
            motivo = f"o raw ainda diz {manifesto.get('versao')}"
        except Exception as e:
            motivo = f"{type(e).__name__}: {e}"
        if tentativa < tentativas:
            log(f"   aguardando o GitHub propagar ({motivo})...")
            time.sleep(5)
    return False, motivo


def main(argv):
    versao = notas = None
    simular = False
    i = 0
    while i < len(argv):
        if argv[i] == "--versao" and i + 1 < len(argv):
            i += 1
            versao = argv[i]
        elif argv[i] == "--notas" and i + 1 < len(argv):
            i += 1
            notas = argv[i]
        elif argv[i] == "--simular":
            simular = True
        elif argv[i] in ("-h", "--help"):
            print(__doc__)
            return 0
        else:
            log(f"opcao desconhecida: {argv[i]}")
            return 2
        i += 1
    if not versao:
        log("ERRO: diga a versao. Ex.: python publicar.py --versao 1.1.8")
        return 2

    tag = "v" + versao
    pequeno = os.path.join(DIST, f"insta-dl-{versao}.zip")
    portatil = os.path.join(DIST, f"insta-dl-portatil-{versao}.zip")
    # Os assets sobem com nome SEM versao: e isso que faz
    # /releases/latest/download/<nome> ser um link permanente, que nunca muda
    # e sempre entrega a versao mais nova.
    nome_portatil = "insta-dl-portatil.zip"
    nome_pequeno = "insta-dl-atualizacao.zip"
    # Ja o manifest aponta para a URL da TAG, nao para 'latest': o pacote que
    # o sha256 descreve tem que ser imutavel, senao o hash deixa de bater na
    # janela entre criar a release e empurrar o manifest.
    url_asset = (f"https://github.com/{REPO}/releases/download/"
                 f"{tag}/{nome_pequeno}")
    link_fixo = (f"https://github.com/{REPO}/releases/latest/download/"
                 f"{nome_portatil}")

    log(f"publicando {versao}" + ("  (SIMULACAO)" if simular else ""))

    log("1. construindo os dois pacotes")
    argumentos = [sys.executable, "empacotar.py", "--versao", versao]
    if notas:
        argumentos += ["--notas", notas]
    if rodar(argumentos, simular)[0] != 0:
        return 1
    if rodar([sys.executable, "montar-portatil.py", "--versao", versao],
             simular)[0] != 0:
        return 1

    log("2. conferindo que o pacote NAO esta defasado do fonte")
    if simular:
        log("   [simulacao] conferencia byte a byte")
    else:
        if not os.path.exists(pequeno):
            log(f"   ! nao achei {pequeno}")
            return 1
        divergentes = conferir_pacote(pequeno)
        if divergentes:
            log("   ! o pacote difere do fonte em: " + ", ".join(divergentes))
            log("     publicar assim entrega codigo velho. Abortado.")
            return 1
        log(f"   {len(NO_PACOTE)} arquivos iguais byte a byte")

    digest = sha256_de(pequeno) if not simular else "0" * 64
    log(f"   sha256 do pacote de atualizacao: {digest[:16]}...")

    log("3. criando a release e subindo os assets")
    corpo = (
        "Baixe o **insta-dl-portatil.zip**, descompacte e de "
        "duplo clique no `insta-dl.exe`.\n\nNao precisa instalar nada: o Python "
        "vem dentro, e o executavel e o `pythonw.exe` oficial do python.org "
        "renomeado, com assinatura valida da Python Software Foundation.\n\n"
        + (notas or ""))
    if rodar(["gh", "release", "create", tag,
              portatil + "#" + nome_portatil,
              pequeno + "#" + nome_pequeno,
              "--title", f"insta-dl {versao}", "--notes", corpo], simular)[0] != 0:
        return 1

    log("4. escrevendo o manifest.json")
    manifesto = {"versao": versao, "pacote_url": url_asset,
                 "sha256": digest, "notas": notas or ""}
    if simular:
        log("   [simulacao] " + json.dumps(manifesto, ensure_ascii=False))
    else:
        for destino in (os.path.join(AQUI, "manifest.json"),
                        os.path.join(DIST, "manifest.json")):
            with open(destino, "w", encoding="utf-8", newline="\n") as f:
                json.dump(manifesto, f, ensure_ascii=False, indent=2)
                f.write("\n")

    log("5. commitando e empurrando")
    rodar(["git", "add", "-u"], simular)          # so arquivos ja versionados
    rodar(["git", "add", "manifest.json"], simular)
    rodar(["git", "commit", "-m", f"Publica {versao}" +
           (f": {notas}" if notas else "")], simular)
    if rodar(["git", "push", "origin", "main"], simular)[0] != 0:
        return 1

    log("6. conferindo no ar, sem credencial, como a maquina do colega faria")
    if simular:
        log("   [simulacao] le o raw e baixa o asset")
        ok, detalhe = True, "simulado"
    else:
        ok, detalhe = conferir_no_ar(versao, digest)
    if not ok:
        log(f"   ! a publicacao NAO esta utilizavel: {detalhe}")
        return 1
    log(f"   atualizacao disponivel e conferida ({detalhe})")

    log("")
    log("publicado.")
    log("")
    log("Link PERMANENTE para mandar ao colega (nao muda a cada release):")
    log(f"  {link_fixo}")
    log("")
    log("Pagina da release (com o passo a passo):")
    log(f"  https://github.com/{REPO}/releases/latest")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
