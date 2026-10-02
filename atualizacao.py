#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
atualizacao.py - auto atualizacao do insta-dl a partir de um link publico
(Google Drive ou qualquer URL direta). So biblioteca padrao do Python.

Como funciona
-------------
Na nuvem ficam DOIS arquivos publicos ("qualquer pessoa com o link"):

  manifest.json   - pequeno, diz qual e a versao atual e onde esta o pacote
  insta-dl-X.Y.Z.zip - o pacote com os arquivos da ferramenta

A ferramenta le o manifest (no maximo 1x por dia), compara a versao, e se
houver uma mais nova baixa o zip, CONFERE O SHA-256 e troca os arquivos.

Regra importante do Google Drive
--------------------------------
O ID de um arquivo do Drive muda quando voce sobe um arquivo novo. Entao o
manifest.json tem que ser atualizado por "Gerenciar versoes -> Enviar nova
versao" (mantem o mesmo ID/link). O .zip pode ser upload novo a cada release,
porque o link dele vem escrito dentro do manifest.
"""

import io
import json
import os
import re
import shutil
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

AQUI = os.path.dirname(os.path.abspath(__file__))
ARQ_VERSAO = os.path.join(AQUI, "versao.json")
ARQ_CONFIG = os.path.join(AQUI, "atualizacao.json")
ARQ_SELO = os.path.join(AQUI, ".ultima-verificacao")

# No pacote portatil os fontes ficam em app\ e estes arquivos moram na pasta
# de cima, ao lado do insta-dl.exe. Sem esse desvio a atualizacao gravaria uma
# copia dentro de app\, e a pessoa continuaria abrindo a versao velha na raiz.
NA_RAIZ = {"COMECE-AQUI.html"}
_ACIMA = os.path.dirname(AQUI)
RAIZ = _ACIMA if os.path.exists(os.path.join(_ACIMA, "insta-dl.exe")) else AQUI
PASTAS_NOSSAS = {"videos", "bin", ".venv", "dist", "__pycache__"}
HORAS_ENTRE_CHECAGENS = 20


def log(msg):
    print(msg, flush=True)


# ------------------------------------------------------------ versao e config


def ler_versao():
    try:
        with open(ARQ_VERSAO, encoding="utf-8") as f:
            return str(json.load(f).get("versao") or "0.0.0")
    except (OSError, ValueError):
        return "0.0.0"


def gravar_versao(versao):
    # newline="\n" de proposito: o empacotador grava LF dentro do zip, e sem
    # isto o arquivo instalado passa a diferir do empacotado so no fim de
    # linha - divergencia inofensiva que custa um alarme falso em qualquer
    # comparacao byte a byte. Medido em 2026-10-02.
    with open(ARQ_VERSAO, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"versao": versao}, f, ensure_ascii=False, indent=2)


def ler_config():
    try:
        with open(ARQ_CONFIG, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def url_do_manifest(config=None):
    """Aceita URL completa ou so o ID do arquivo no Drive."""
    config = config if config is not None else ler_config()
    url = (config.get("manifest_url") or "").strip()
    if url:
        return url
    ident = (config.get("manifest_id") or "").strip()
    if ident:
        return f"https://drive.google.com/uc?export=download&id={ident}"
    return ""


def comparar(a, b):
    """-1, 0 ou 1 comparando '1.2.10' com '1.3.0' (numerico por segmento)."""
    def partes(v):
        return [int(x) if x.isdigit() else 0 for x in re.split(r"[.\-+]", str(v))]
    pa, pb = partes(a), partes(b)
    pa += [0] * (len(pb) - len(pa))
    pb += [0] * (len(pa) - len(pb))
    return (pa > pb) - (pa < pb)


# ------------------------------------------------------------ download (Drive)


def _campos_do_form(html):
    """Le action + inputs escondidos da pagina de confirmacao do Drive."""
    acao = re.search(r'<form\b[^>]*\baction="([^"]+)"', html)
    campos = {}
    for tag in re.findall(r"<input\b[^>]*>", html):
        nome = re.search(r'\bname="([^"]+)"', tag)
        valor = re.search(r'\bvalue="([^"]*)"', tag)
        if nome:
            campos[nome.group(1)] = valor.group(1) if valor else ""
    return (acao.group(1).replace("&amp;", "&") if acao else None), campos


def baixar_url(url, timeout=60, tentativas_form=1):
    """
    Baixa uma URL e devolve bytes.

    Trata as tres formas que o Google Drive usa: arquivo direto, pagina de
    confirmacao com formulario, e a pagina de cota estourada.
    """
    import http.cookiejar
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def pegar(alvo):
        req = urllib.request.Request(alvo, headers={"User-Agent": "insta-dl"})
        with opener.open(req, timeout=timeout) as r:
            dados = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                import gzip
                dados = gzip.decompress(dados)
            return dados, (r.headers.get("Content-Type") or "")

    try:
        dados, tipo = pegar(url)
    except (OSError, ValueError) as e:
        # Arquivo que exige login do Google redireciona para a tela de entrada,
        # e o urllib morre nisso com um erro ilegivel. Medido em 2026-10-02: um
        # arquivo restrito ao dominio devolve /v3/signin/identifier.
        if any(m in str(e) for m in ("signin", "ServiceLogin", "accounts.google.com")):
            raise RuntimeError(
                "o arquivo exige login do Google. Para a atualizacao automatica "
                "funcionar ele precisa estar compartilhado como 'qualquer pessoa "
                "com o link' - restrito ao dominio nao serve, porque a ferramenta "
                "baixa sem credencial nenhuma."
            ) from e
        raise
    for _ in range(tentativas_form + 1):
        if "text/html" not in tipo.lower():
            return dados
        html = dados.decode("utf-8", "replace")
        if re.search(r"Too many users|cota|quota exceeded", html, re.I):
            raise RuntimeError(
                "o Google Drive bloqueou o download por excesso de acessos "
                "(cota). Tente mais tarde ou hospede o pacote em outro lugar."
            )
        # Arquivo que exige login devolve a TELA DE ENTRADA, que tambem tem um
        # <form> - seguir esse formulario levava a um erro ilegivel de urllib.
        # Medido em 2026-10-02: restrito ao dominio cai em /v3/signin.
        if re.search(r"ServiceLogin|/v3/signin|accounts\.google\.com", html):
            raise RuntimeError(
                "o arquivo exige login do Google. Para a atualizacao automatica "
                "funcionar ele precisa estar compartilhado como 'qualquer pessoa "
                "com o link' - restrito ao dominio nao serve, porque a ferramenta "
                "baixa sem credencial nenhuma."
            )
        acao, campos = _campos_do_form(html)
        if acao and not acao.lower().startswith(("http://", "https://")):
            # action relativa: resolve contra a pagina, nunca usa cru
            acao = urllib.parse.urljoin(url, acao)
        if not acao:
            raise RuntimeError(
                "a resposta veio como pagina HTML, nao como arquivo. "
                "Confirme que o arquivo esta compartilhado como "
                "'qualquer pessoa com o link'."
            )
        dados, tipo = pegar(acao + ("&" if "?" in acao else "?")
                            + urllib.parse.urlencode(campos))
    raise RuntimeError("o Drive ficou pedindo confirmacao em loop.")


def sha256(dados):
    import hashlib
    return hashlib.sha256(dados).hexdigest()


# ------------------------------------------------------------ aplicar pacote


def conferir_zip(dados):
    """
    Valida o zip antes de extrair: sem caminho absoluto, sem '..', sem pasta.

    O pacote e plano de proposito - so arquivos na raiz. Isso impede que um
    pacote adulterado escreva fora da pasta da ferramenta.
    """
    with zipfile.ZipFile(io.BytesIO(dados)) as z:
        ruim = z.testzip()
        if ruim:
            raise RuntimeError(f"o zip esta corrompido (entrada {ruim}).")
        nomes = z.namelist()
    for nome in nomes:
        normalizado = nome.replace("\\", "/")
        # Entrada dentro de pasta nao e furo de seguranca - aplicar_pacote so
        # copia o que esta na raiz -, mas seria DESCARTADA EM SILENCIO, e a
        # atualizacao se diria bem-sucedida faltando um arquivo. Recusar aqui
        # troca uma falha muda por uma falha visivel. Achado por teste em
        # 2026-10-02.
        if "/" in normalizado:
            raise RuntimeError(
                f"o pacote e plano: entrada em pasta nao entra ({nome})")
        if os.path.isabs(nome) or ".." in normalizado.split("/") or ":" in nome:
            raise RuntimeError(f"caminho suspeito no pacote: {nome}")
    return nomes


def aplicar_pacote(dados):
    """Extrai o pacote sobre a pasta da ferramenta. Devolve os nomes trocados."""
    conferir_zip(dados)
    trocados = []
    temp = tempfile.mkdtemp(prefix="insta-dl-upd-")
    try:
        with zipfile.ZipFile(io.BytesIO(dados)) as z:
            z.extractall(temp)
        for nome in sorted(os.listdir(temp)):
            origem = os.path.join(temp, nome)
            if not os.path.isfile(origem):
                continue
            shutil.copy2(origem, os.path.join(
                RAIZ if nome in NA_RAIZ else AQUI, nome))
            trocados.append(nome)
    finally:
        shutil.rmtree(temp, ignore_errors=True)
    return trocados


# ------------------------------------------------------------ fluxo principal


def deve_checar(forcar=False):
    if forcar:
        return True
    try:
        with open(ARQ_SELO, encoding="utf-8") as f:
            ultima = float(f.read().strip())
    except (OSError, ValueError):
        return True
    return (time.time() - ultima) > HORAS_ENTRE_CHECAGENS * 3600


def marcar_checagem():
    try:
        with open(ARQ_SELO, "w", encoding="utf-8") as f:
            f.write(str(time.time()))
    except OSError:
        pass


def verificar(forcar=False, falar=False):
    """
    Checa e aplica atualizacao. Devolve a versao nova se trocou, senao None.

    Nunca levanta excecao: sem internet, sem link configurado ou com erro no
    caminho da atualizacao, a ferramenta segue funcionando normalmente.
    """
    config = ler_config()
    url = url_do_manifest(config)
    if not url:
        if falar:
            log("  atualizacao automatica nao configurada (atualizacao.json vazio).")
        return None
    if not deve_checar(forcar):
        return None

    local = ler_versao()
    try:
        manifesto = json.loads(baixar_url(url, timeout=20).decode("utf-8", "replace"))
    except (OSError, ValueError, RuntimeError) as e:
        if falar:
            log(f"  nao deu para checar atualizacao agora: {e}")
        return None
    marcar_checagem()

    remota = str(manifesto.get("versao") or "0.0.0")
    if comparar(remota, local) <= 0:
        if falar:
            log(f"  ja esta na versao mais recente ({local}).")
        return None

    pacote = (manifesto.get("pacote_url") or "").strip()
    if not pacote and manifesto.get("pacote_id"):
        pacote = ("https://drive.google.com/uc?export=download&id="
                  + str(manifesto["pacote_id"]).strip())
    if not pacote:
        if falar:
            log("  o manifest nao diz onde esta o pacote (pacote_url/pacote_id).")
        return None

    log(f"  atualizacao disponivel: {local} -> {remota}. Baixando...")
    try:
        dados = baixar_url(pacote, timeout=180)
    except (OSError, RuntimeError) as e:
        log(f"  ! falhou o download do pacote: {e}")
        return None

    esperado = str(manifesto.get("sha256") or "").strip().lower()
    if not esperado:
        log("  ! o manifest nao traz sha256; nao vou aplicar pacote sem conferir.")
        return None
    obtido = sha256(dados)
    if obtido != esperado:
        log("  ! o pacote nao corresponde ao sha256 do manifest; descartado.")
        log(f"    esperado {esperado[:16]}... obtido {obtido[:16]}...")
        return None

    try:
        trocados = aplicar_pacote(dados)
    except (OSError, RuntimeError, zipfile.BadZipFile) as e:
        log(f"  ! nao consegui aplicar o pacote: {e}")
        return None

    gravar_versao(remota)
    log(f"  atualizado para {remota} ({len(trocados)} arquivos).")
    if manifesto.get("notas"):
        log(f"    {manifesto['notas']}")
    return remota
