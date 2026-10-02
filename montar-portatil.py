#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
montar-portatil.py - monta o pacote PORTATIL do insta-dl.

O resultado e uma pasta que o colega descompacta e usa: duplo clique no
insta-dl.exe e a janela abre. Sem instalar nada, sem senha de administrador,
sem mexer no registro do Windows, e sem nenhum arquivo .cmd/.bat/.ps1.

Como isso e possivel sem gerar um .exe suspeito
-----------------------------------------------
O insta-dl.exe NAO e um binario empacotado por nos. E o pythonw.exe do pacote
"embeddable" oficial do python.org, apenas renomeado - e renomear preserva a
assinatura Authenticode, que continua valendo como Python Software Foundation
(medido em 2026-09-15). Ou seja: o unico executavel do pacote e um binario
assinado por editor conhecido, nao um blob sem assinatura.

Um interpretador Python sozinho abriria o REPL, nao a nossa janela. Quem faz a
ligacao e o sitecustomize.py: com a linha "import site" ligada no
python313._pth, o proprio Python carrega esse arquivo no startup, e dali
chamamos a janela. Por isso o executavel funciona sem receber argumento.

Uso:
    python montar-portatil.py                 (usa a versao do versao.json)
    python montar-portatil.py --versao 1.2.0

Exige uma instalacao normal do Python na maquina de quem monta, para copiar o
tkinter: o pacote embeddable nao traz interface grafica.
"""

import hashlib
import json
import os
import shutil
import sys
import urllib.request
import zipfile

AQUI = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(AQUI, "dist")
CACHE = os.path.join(AQUI, "dist", "_cache")

# Runtime oficial, com hash fixo: se o download vier diferente disso, para.
PY_VERSAO = "3.13.15"
PY_URL = (f"https://www.python.org/ftp/python/{PY_VERSAO}/"
          f"python-{PY_VERSAO}-embed-amd64.zip")
PY_SHA256 = "d1f04d990aee1253d8569e8e5104e30fa9f5fa830899f14843448872d936a2cf"

# Pecas do tkinter que faltam no embeddable (copiadas da instalacao normal).
TK_ARQUIVOS = [
    os.path.join("DLLs", "_tkinter.pyd"),
    os.path.join("DLLs", "tcl86t.dll"),
    os.path.join("DLLs", "tk86t.dll"),
    os.path.join("DLLs", "zlib1.dll"),
]
TK_PASTAS = [(os.path.join("Lib", "tkinter"), os.path.join("Lib", "tkinter")),
             ("tcl", "tcl")]

# Arquivos da ferramenta, que vao para a subpasta app\.
APP = ["insta-dl.pyw", "baixar.py", "atualizacao.py", "README.md",
       "versao.json", "atualizacao.json"]
RAIZ_EXTRA = ["COMECE-AQUI.html"]

# Layout final. Ao lado do insta-dl.exe fica SO o que o Windows exige estar la:
# o carregador procura python313.dll/vcruntime na pasta do executavel, e o
# ._pth tambem e lido dali. Todo o resto (pyd, dlls de apoio, stdlib, tcl) vai
# para sistema\, porque 38 arquivos cripticos na raiz assustam tanto quanto um
# .cmd - e o ponto do pacote e nao assustar.
FICA_NA_RAIZ = {
    "insta-dl.exe", "python313.dll", "python3.dll",
    "vcruntime140.dll", "vcruntime140_1.dll",
    "python313._pth", "COMECE-AQUI.html", "LICENCA-python.txt",
}
PTH = "sistema\\python313.zip\nsistema\nsistema\\Lib\napp\nimport site\n"

# Gordura que vem no runtime oficial e que uma janela tkinter nunca toca.
# Medido em 2026-09-25: ~6 MB dos 29 MB extraidos. O corte mora AQUI, nunca na
# mao, senao o proximo rebuild traz tudo de volta. Caminhos relativos a raiz
# montada, ANTES de arrumar_pasta() recolher tudo para sistema\.
PODAR = [
    # material de desenvolvimento em C: nao se compila nada em runtime
    "tcl/nmake", "tcl/tcl86t.lib", "tcl/tk86t.lib",
    "tcl/tclstub86.lib", "tcl/tkstub86.lib",
    "tcl/tclConfig.sh", "tcl/tclooConfig.sh",
    # versoes antigas dos modulos Tcl - a janela roda em 8.6
    "tcl/tcl8/8.4", "tcl/tcl8/8.5",
    # demos do Tk, as imagens delas, e os fusos horarios do comando 'clock'
    "tcl/tk8.6/demos", "tcl/tk8.6/images", "tcl/tcl8.6/tzdata",
    # mensagens traduzidas do proprio Tcl/Tk: a interface e nossa, em PT
    "tcl/tcl8.6/msgs", "tcl/tk8.6/msgs",
    # extensoes Tcl que a janela nao carrega (DDE e registro do Windows)
    "tcl/dde1.4", "tcl/reg1.3",
    # banco embutido: a ferramenta nao guarda nada em sqlite
    "sqlite3.dll", "_sqlite3.pyd",
    "Lib/tkinter/__pycache__",
]
# NAO entram na poda, e o motivo de cada um:
#   libcrypto/libssl + _ssl.pyd -> HTTPS, sem isso nao se fala com o Instagram
#   tcl8.6/encoding             -> o Tcl nao sobe sem as tabelas de encoding
#   tk8.6/ttk                   -> os widgets que a janela usa
#   unicodedata.pyd             -> encodings.idna importa; 700 KB nao pagam o risco
#   python.cat                  -> catalogo de assinatura do runtime oficial

SITECUSTOMIZE = '''# -*- coding: utf-8 -*-
"""
Carregado automaticamente pelo Python no startup (via "import site" no
python313._pth). E o que faz o insta-dl.exe abrir a janela sem receber
argumento nenhum - um interpretador sozinho abriria o REPL.

Nao ha console neste executavel: qualquer erro aqui seria invisivel, por isso
tudo que escapar vai para erro.log ao lado do executavel.
"""
import os
import sys
import traceback


def _preparar_tcl(raiz):
    """
    O Tcl procura o init.tcl por caminhos relativos ao executavel. Como aqui a
    biblioteca mora em sistema\\tcl, avisamos onde ela esta - senao o tkinter
    sobe com "Can't find a usable init.tcl".
    """
    tcl = os.path.join(raiz, "sistema", "tcl")
    for variavel, pasta in (("TCL_LIBRARY", "tcl8.6"), ("TK_LIBRARY", "tk8.6")):
        caminho = os.path.join(tcl, pasta)
        if os.path.isdir(caminho):
            os.environ.setdefault(variavel, caminho)


def _iniciar():
    raiz = os.path.dirname(os.path.abspath(sys.executable))
    _preparar_tcl(raiz)
    sys.argv = ["insta-dl"]
    teste = os.environ.get("INSTA_DL_AUTOTESTE")
    if teste:
        sys.argv += ["--autoteste", teste]
    import runpy
    runpy.run_path(os.path.join(raiz, "app", "insta-dl.pyw"), run_name="__main__")


if os.path.basename(sys.executable).lower().startswith("insta-dl"):
    try:
        _iniciar()
    except SystemExit:
        pass
    except BaseException:
        try:
            destino = os.path.join(
                os.path.dirname(os.path.abspath(sys.executable)), "erro.log")
            with open(destino, "w", encoding="utf-8") as saida:
                traceback.print_exc(file=saida)
        except OSError:
            pass
    os._exit(0)
'''


def log(msg):
    print(msg, flush=True)


def sha256_de(dados):
    return hashlib.sha256(dados).hexdigest()


def baixar_runtime():
    os.makedirs(CACHE, exist_ok=True)
    destino = os.path.join(CACHE, os.path.basename(PY_URL))
    if os.path.exists(destino):
        with open(destino, "rb") as f:
            dados = f.read()
        if sha256_de(dados) == PY_SHA256:
            log(f"  runtime ja em cache: {destino}")
            return dados
        log("  cache com hash diferente; baixando de novo.")
    log(f"  baixando o Python embeddable oficial ({PY_VERSAO})...")
    req = urllib.request.Request(PY_URL, headers={"User-Agent": "insta-dl"})
    with urllib.request.urlopen(req, timeout=300) as r:
        dados = r.read()
    obtido = sha256_de(dados)
    if obtido != PY_SHA256:
        log("  ! o runtime baixado NAO bate com o hash esperado. Abortado.")
        log(f"    esperado {PY_SHA256}")
        log(f"    obtido   {obtido}")
        return None
    with open(destino, "wb") as f:
        f.write(dados)
    log(f"  runtime verificado ({len(dados) / 1048576:.1f} MB)")
    return dados


def achar_python_completo():
    """
    Instalacao normal do Python, de onde sai o tkinter.

    Evita de proposito o Python da Microsoft Store (C:\\Program Files\\
    WindowsApps): os arquivos de la tem ACL restritiva, que viaja junto na
    copia, e redistribuir o conteudo de um pacote da Store e mais nebuloso do
    que redistribuir o do instalador do python.org.
    """
    import glob
    candidatos = []
    for padrao in (os.path.join(os.environ.get("LOCALAPPDATA", ""),
                                "Programs", "Python", "Python3*"),
                   os.path.join(os.environ.get("ProgramFiles", ""), "Python3*"),
                   "C:\\Python3*"):
        candidatos.extend(sorted(glob.glob(padrao), reverse=True))
    candidatos.append(getattr(sys, "base_prefix", sys.prefix))
    candidatos.append(os.path.dirname(os.path.abspath(sys.executable)))

    for pasta in candidatos:
        if "windowsapps" in pasta.lower():
            continue
        if all(os.path.exists(os.path.join(pasta, rel)) for rel in TK_ARQUIVOS):
            return pasta
    return None


def enxugar(saida):
    """Remove a gordura do PODAR. Devolve (itens removidos, bytes liberados)."""
    itens, bytes_ = 0, 0
    for rel in PODAR:
        alvo = os.path.join(saida, rel.replace("/", os.sep))
        if not os.path.exists(alvo):
            continue
        if os.path.isdir(alvo):
            bytes_ += sum(os.path.getsize(os.path.join(b, n))
                          for b, _, fs in os.walk(alvo) for n in fs)
            shutil.rmtree(alvo)
        else:
            bytes_ += os.path.getsize(alvo)
            os.remove(alvo)
        itens += 1
    return itens, bytes_


def arrumar_pasta(saida):
    """Recolhe o runtime para sistema\\, deixando a raiz com o essencial."""
    sistema = os.path.join(saida, "sistema")
    os.makedirs(sistema, exist_ok=True)
    movidos = 0
    for nome in sorted(os.listdir(saida)):
        if nome in FICA_NA_RAIZ or nome in ("app", "videos", "sistema"):
            continue
        shutil.move(os.path.join(saida, nome), os.path.join(sistema, nome))
        movidos += 1
    return movidos


def montar(versao):
    saida = os.path.join(DIST, "portatil")
    # NUNCA apagar videos\ aqui. Esta pasta de montagem acaba sendo usada como
    # instalacao de verdade (foi o que aconteceu em 2026-09-25), e um rebuild
    # que faz rmtree na raiz leva junto o que a pessoa baixou.
    if os.path.exists(saida):
        for nome in os.listdir(saida):
            if nome == "videos":
                continue
            caminho = os.path.join(saida, nome)
            if os.path.isdir(caminho):
                shutil.rmtree(caminho)
            else:
                os.remove(caminho)
    os.makedirs(saida, exist_ok=True)

    dados = baixar_runtime()
    if not dados:
        return None
    with zipfile.ZipFile(os.path.join(CACHE, os.path.basename(PY_URL))) as z:
        z.extractall(saida)

    completo = achar_python_completo()
    if not completo:
        log("  ! nao achei uma instalacao normal do Python para copiar o tkinter.")
        log("    O pacote embeddable nao traz interface grafica.")
        return None
    log(f"  copiando o tkinter de {completo}")
    for rel in TK_ARQUIVOS:
        shutil.copy2(os.path.join(completo, rel), saida)
    for origem, destino in TK_PASTAS:
        shutil.copytree(os.path.join(completo, origem),
                        os.path.join(saida, destino), dirs_exist_ok=True)

    # o executavel: pythonw.exe renomeado (mantem a assinatura da PSF)
    shutil.copy2(os.path.join(saida, "pythonw.exe"),
                 os.path.join(saida, "insta-dl.exe"))
    for sobra in ("python.exe", "pythonw.exe"):
        os.remove(os.path.join(saida, sobra))

    with open(os.path.join(saida, "python313._pth"), "w", encoding="ascii",
              newline="\r\n") as f:
        f.write(PTH)
    with open(os.path.join(saida, "sitecustomize.py"), "w", encoding="utf-8",
              newline="\n") as f:
        f.write(SITECUSTOMIZE)

    if os.path.exists(os.path.join(saida, "LICENSE.txt")):
        os.replace(os.path.join(saida, "LICENSE.txt"),
                   os.path.join(saida, "LICENCA-python.txt"))

    pasta_app = os.path.join(saida, "app")
    os.makedirs(pasta_app)
    for nome in APP:
        origem = os.path.join(AQUI, nome)
        if nome == "versao.json":
            with open(os.path.join(pasta_app, nome), "w", encoding="utf-8") as f:
                json.dump({"versao": versao}, f, ensure_ascii=False, indent=2)
            continue
        if not os.path.exists(origem):
            log(f"  ! falta o arquivo {nome}")
            return None
        shutil.copy2(origem, pasta_app)
    for nome in RAIZ_EXTRA:
        shutil.copy2(os.path.join(AQUI, nome), saida)
    os.makedirs(os.path.join(saida, "videos"), exist_ok=True)

    itens, liberados = enxugar(saida)
    log(f"  {itens} itens podados do runtime ({liberados / 1048576:.1f} MB)")
    movidos = arrumar_pasta(saida)
    log(f"  {movidos} arquivos de runtime recolhidos para sistema\\")
    return saida


def compactar(pasta, versao):
    """
    Zipa a pasta montada - menos videos\\.

    O que a pessoa baixou nao entra no pacote que vai para os colegas: seria
    vazamento de conteudo e peso a toa. A pasta e criada no primeiro download.
    """
    caminho = os.path.join(DIST, f"insta-dl-portatil-{versao}.zip")
    with zipfile.ZipFile(caminho, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for base, pastas, arquivos in os.walk(pasta):
            pastas[:] = [p for p in pastas if p != "videos"]
            for nome in arquivos:
                inteiro = os.path.join(base, nome)
                z.write(inteiro, os.path.join(
                    "insta-dl", os.path.relpath(inteiro, pasta)))
    return caminho


def main(argv):
    versao = None
    if argv and argv[0] == "--versao" and len(argv) > 1:
        versao = argv[1]
    elif argv and argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if not versao:
        try:
            with open(os.path.join(AQUI, "versao.json"), encoding="utf-8") as f:
                versao = json.load(f)["versao"]
        except (OSError, ValueError, KeyError):
            versao = "1.0.0"

    log(f"montando o pacote portatil {versao}")
    pasta = montar(versao)
    if not pasta:
        return 1
    caminho = compactar(pasta, versao)
    with open(caminho, "rb") as f:
        digest = sha256_de(f.read())
    cru = sum(os.path.getsize(os.path.join(b, n))
              for b, _, fs in os.walk(pasta) for n in fs)

    log("")
    log(f"pasta:  {pasta}")
    log(f"zip:    {caminho}")
    log(f"        {os.path.getsize(caminho) / 1048576:.1f} MB compactado, "
        f"{cru / 1048576:.1f} MB na maquina do colega")
    log(f"sha256: {digest}")
    log("")
    log("Esse zip e o PRIMEIRO download (traz o Python dentro). As atualizacoes")
    log("seguintes usam o pacote pequeno do empacotar.py, que troca so os")
    log("arquivos de app\\ - o runtime nao precisa ser baixado de novo.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
